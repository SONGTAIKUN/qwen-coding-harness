from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .config import atomic_json

IGNORED = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
           ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", "dist", "build",
           ".state", ".omlx", ".downloads", "models", "cache", ".codex", ".agents",
           ".opencode", ".ssh", ".aws", ".idea", ".DS_Store"}
PRIVATE = (".env", ".env.*", "*.pem", "*.key", "*.safetensors", "*.gguf")


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    argv: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=120, ge=1, le=1800)


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[Check] = Field(default_factory=list)
    writable_paths: list[str] = Field(default_factory=lambda: ["."])
    protected_paths: list[str] = Field(default_factory=list)
    read_roots: list[str] = Field(default_factory=list)
    max_file_bytes: int = 2_000_000
    max_snapshot_bytes: int = 250_000_000

    @classmethod
    def load(cls, root: Path):
        path = root / ".agent-project.json"
        if not path.exists():
            raise ValueError("Run qwen-harness init in this project first; configure at least one verification command")
        return cls.model_validate_json(path.read_text())


def permitted_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(parts) and not any(p in IGNORED or p.startswith(".venv") for p in parts) and not any(
        fnmatch.fnmatch(p, pattern) for p in parts for pattern in PRIVATE
    )


def contained(root: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise ValueError("Expected a relative path without '..'")
    path = root.joinpath(*name.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes workspace")
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("Symlinks are not supported in agent file tools")
        current = current.parent
    return path


def in_scope(name: str, scopes: list[str]) -> bool:
    return any(scope == "." or name == scope.rstrip("/") or name.startswith(scope.rstrip("/") + "/") for scope in scopes)


def files(root: Path):
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink() and permitted_name(d))
        for name in sorted(names):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if not path.is_symlink() and permitted_name(relative):
                yield relative, path


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class Workspace:
    def __init__(self, root: Path, project: Project, scopes: list[str] | None = None):
        self.root = root.resolve()
        self.project = project
        self.scopes = scopes or []

    def path(self, name: str, write=False) -> Path:
        if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
            raise ValueError("Expected a relative path without '..'")
        name = PurePosixPath(name).as_posix()
        if not permitted_name(name) or name in {".agent-project.json", "opencode.json", "opencode.jsonc"}:
            raise ValueError("This path is reserved or excluded")
        path = contained(self.root, name)
        if write and (not in_scope(name, self.scopes) or not in_scope(name, self.project.writable_paths)
                      or in_scope(name, self.project.protected_paths)):
            raise ValueError(f"No write ownership for {name}")
        return path

    def list(self) -> list[str]:
        return [name for name, _ in files(self.root) if name != ".agent-project.json"][:10000]

    def read(self, name: str, start: int = 1, limit: int = 200) -> str:
        path = self.path(name)
        if path.stat().st_size > self.project.max_file_bytes:
            raise ValueError("File exceeds readable size limit")
        lines = path.read_text().splitlines()
        start, limit = max(1, start), min(max(1, limit), 400)
        return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines) if start - 1 <= i < start - 1 + limit)[:24000]

    def search(self, query: str) -> list[dict]:
        results = []
        for name, path in files(self.root):
            if path.stat().st_size > self.project.max_file_bytes or name == ".agent-project.json":
                continue
            try:
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    if query in line:
                        results.append({"path": name, "line": number, "text": line[:400]})
                        if len(results) >= 60:
                            return results
            except UnicodeError:
                continue
        return results

    def write(self, name: str, content: str) -> str:
        path = self.path(name, write=True)
        if len(content.encode()) > self.project.max_file_bytes:
            raise ValueError("File exceeds write size limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".agent-write-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return f"Wrote {name} ({len(content)} characters)"

    def edit(self, name: str, old: str, new: str) -> str:
        path = self.path(name, write=True)
        text = path.read_text()
        if not old or text.count(old) != 1:
            if new and old not in text and text.count(new) == 1:
                return f"Edit already present in {name}"
            raise ValueError("old must match exactly once; read the current file before editing")
        return self.write(name, text.replace(old, new, 1))


def snapshot(source: Path, destination: Path, project: Project) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {}
    total = 0
    for name, path in files(source):
        size = path.stat().st_size
        if size > project.max_file_bytes:
            raise ValueError(f"Snapshot file too large: {name}; exclude generated files before running")
        total += size
        if total > project.max_snapshot_bytes:
            raise ValueError("Project snapshot exceeds configured limit")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        manifest[name] = digest(target)
    return manifest


def changes(workspace: Path, original: dict) -> list[str]:
    current = {name: digest(path) for name, path in files(workspace)}
    return sorted(name for name in current.keys() | original.keys() if current.get(name) != original.get(name))


def diff(source: Path, workspace: Path, changed: list[str]) -> str:
    result = []
    for name in changed:
        before, after = contained(source, name), contained(workspace, name)
        old = before.read_text(errors="replace").splitlines(keepends=True) if before.is_file() else []
        new = after.read_text(errors="replace").splitlines(keepends=True) if after.is_file() else []
        result.extend(difflib.unified_diff(old, new, fromfile=f"a/{name}", tofile=f"b/{name}"))
    return "".join(result)


def apply_changes(source: Path, workspace: Path, original: dict, project: Project, run_dir: Path) -> list[str]:
    changed = changes(workspace, original)
    target_ws = Workspace(source, project, project.writable_paths)
    for name in changed:
        target = target_ws.path(name, write=True)
        if digest(target) != original.get(name):
            raise ValueError(f"Source changed since snapshot: {name}; refusing to overwrite")
        if not contained(workspace, name).is_file():
            raise ValueError("Automatic file deletion is not supported; inspect the diff")
    backup = run_dir / "apply-backup"
    backup.mkdir(exist_ok=True)
    applied = []
    try:
        for name in changed:
            target = target_ws.path(name, write=True)
            if target.exists():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            target_ws.write(name, contained(workspace, name).read_text())
            applied.append(name)
    except Exception:
        for name in reversed(applied):
            saved, target = backup / name, contained(source, name)
            if saved.exists():
                shutil.copy2(saved, target)
            else:
                target.unlink()
        raise
    atomic_json(run_dir / "applied.json", {"files": changed})
    return changed
