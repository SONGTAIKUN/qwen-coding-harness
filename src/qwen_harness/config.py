from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class Role(BaseModel):
    max_tokens: int = Field(ge=128, le=16384)
    temperature: float = Field(ge=0, le=2)
    reasoning_effort: str = "medium"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upstream_url: str
    omlx_settings: str | None = None
    model: str
    model_directory: str
    host: str = "127.0.0.1"
    port: int = 8001
    max_concurrency: int = Field(default=4, ge=1, le=4)
    context_window: int = 65536
    max_output_tokens: int = 16384
    context_reserve: int = 2048
    request_timeout_seconds: int = 1800
    max_job_seconds: int = 7200
    max_job_output_tokens: int = 196608
    max_agent_steps: int = 24
    max_repair_rounds: int = 3
    roles: dict[str, Role]

    @model_validator(mode="after")
    def local_only(self):
        from urllib.parse import urlsplit
        if urlsplit(self.upstream_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The local harness requires a loopback model endpoint")
        if self.host != "127.0.0.1":
            raise ValueError("Harness must bind to loopback")
        return self

    def resolve(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def upstream_key(self) -> str:
        override = os.environ.get("LOCAL_LLM_API_KEY") or os.environ.get("OMLX_API_KEY")
        if override:
            return override
        if self.omlx_settings:
            path = self.resolve(self.omlx_settings)
            if path.is_file():
                return json.loads(path.read_text())["auth"]["api_key"]
        # Many loopback-only OpenAI-compatible servers accept any non-empty
        # bearer value. Authenticated servers should use LOCAL_LLM_API_KEY.
        return "local"


def settings() -> Settings:
    path = ROOT / "config.json"
    if not path.is_file():
        raise RuntimeError(
            "Missing config.json; copy config.example.json and set your local model paths"
        )
    return Settings.model_validate_json(path.read_text())


def state_dir() -> Path:
    path = ROOT / ".state"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def token() -> str:
    path = state_dir() / "gateway.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    value = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as handle:
        handle.write(value)
    return value
