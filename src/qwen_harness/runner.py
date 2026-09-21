from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

from .config import ROOT
from .workspace import Check, Project


def sandbox_profile(workspace: Path, temporary: Path, read_roots: list[str]) -> str:
    allowed = [workspace, temporary, Path(ROOT / ".venv"), Path(sys.base_prefix),
               Path("/System"), Path("/Library"), Path("/usr"), Path("/bin"),
               Path("/sbin"), Path("/opt/homebrew"), Path("/dev"),
               Path("/private/var/db/dyld"), Path("/private/etc"), *map(Path, read_roots)]
    filters = " ".join(f"(subpath {json.dumps(str(p.resolve()))})" for p in allowed)
    parents = {str(parent) for path in allowed for parent in path.resolve().parents}
    filters += " " + " ".join(f"(literal {json.dumps(parent)})" for parent in sorted(parents))
    writes = " ".join(f"(subpath {json.dumps(str(p.resolve()))})" for p in [workspace, temporary])
    return ("(version 1) (allow default) (deny network*) (deny file-write*) "
            f"(allow file-write* {writes}) "
            f"(deny file-read-data (require-not (require-any {filters})))")


async def run_check(check: Check, workspace: Path, project: Project, stdin: bytes | None = None, merge_stderr: bool = True) -> dict:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists():
        raise RuntimeError("This installation requires the macOS sandbox runner; no unsandboxed fallback")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="qwen-check-") as tmp:
        temporary = Path(tmp).resolve()
        argv = [str(ROOT / ".venv/bin/python") if arg == "{python}" else arg for arg in check.argv]
        env = {"PATH": f"{ROOT / '.venv/bin'}:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               "HOME": str(temporary), "TMPDIR": str(temporary), "LANG": "en_US.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "CI": "1"}
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec", "-p", sandbox_profile(workspace, temporary, project.read_roots), *argv,
            cwd=workspace, env=env, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE, start_new_session=True,
        )
        chunks = bytearray()
        stderr_chunks = bytearray()
        total = 0
        stdout_total = 0

        async def drain(reader, target, stdout=False):
            nonlocal total, stdout_total
            while data := await reader.read(8192):
                total += len(data)
                if stdout:
                    stdout_total += len(data)
                target.extend(data)
                if len(target) > 24000:
                    del target[:-24000]
                if total > 5_000_000:
                    raise RuntimeError("Test output exceeded 5 MB")

        async def collect():
            if stdin is not None:
                try:
                    process.stdin.write(stdin)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()
            readers = [asyncio.create_task(drain(process.stdout, chunks, True))]
            if not merge_stderr:
                readers.append(asyncio.create_task(drain(process.stderr, stderr_chunks)))
            try:
                await asyncio.gather(*readers)
            finally:
                for reader in readers:
                    if not reader.done():
                        reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
            await process.wait()

        error = None
        try:
            await asyncio.wait_for(collect(), timeout=check.timeout_seconds)
        except (TimeoutError, RuntimeError) as exc:
            error = str(exc) or "Test timed out"
        finally:
            # Also terminate descendants left behind by a test that already exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        return {"name": check.name, "argv": check.argv, "exit_code": process.returncode,
                "passed": process.returncode == 0 and not error, "error": error,
                "seconds": round(time.monotonic() - started, 3),
                "output": chunks.decode(errors="replace"), "output_truncated": stdout_total > len(chunks),
                "stderr": stderr_chunks.decode(errors="replace")}


async def verify(workspace: Path, project: Project) -> list[dict]:
    if not project.checks:
        raise ValueError("No verification commands configured; cannot mark work as verified")
    return [await run_check(check, workspace, project) for check in project.checks]
