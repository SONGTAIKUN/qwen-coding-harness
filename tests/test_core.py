import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from tokenizers import Tokenizer
from tokenizers.models import WordLevel

from qwen_harness.config import atomic_json, settings
from qwen_harness.cli import opencode_binary
from qwen_harness.context import ContextCounter, compact
from qwen_harness.gateway import Gateway
from qwen_harness.jobs import Manager
from qwen_harness.server import create_app
from qwen_harness.workspace import Project, Workspace, apply_changes, snapshot


class Counter:
    def count(self, body):
        return len(json.dumps(body.get("messages", [])))


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    config = Project(checks=[{"name": "unit", "argv": ["{python}", "-m", "unittest"]}],
                     writable_paths=["app.py", "tests"], protected_paths=["tests/frozen.py"])
    atomic_json(source / ".agent-project.json", config.model_dump())
    (source / "app.py").write_text("value = 0\n")
    (source / "tests").mkdir()
    (source / "tests/frozen.py").write_text("assert True\n")
    return source, config


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", ".env", "tests/frozen.py", "./tests/frozen.py", ".agent-project.json", "elsewhere.py"])
def test_write_permissions(project, path):
    source, config = project
    with pytest.raises(ValueError):
        Workspace(source, config, ["."]).write(path, "bad")


def test_role_ownership_and_symlinks(project, tmp_path):
    source, config = project
    with pytest.raises(ValueError):
        Workspace(source, config).write("app.py", "bad")
    (source / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        Workspace(source, Project(), ["."]).read("link/outside")
    ws = Workspace(source, config, ["app.py"])
    ws.edit("app.py", "value = 0", "value = 1")
    assert "already present" in ws.edit("app.py", "value = 0", "value = 1")


def test_snapshot_excludes_secrets_and_apply_detects_conflicts(project, tmp_path):
    source, config = project
    (source / ".env").write_text("SECRET=hidden")
    work = tmp_path / "work"
    manifest = snapshot(source, work, config)
    assert ".env" not in manifest
    Workspace(work, config, ["app.py"]).write("app.py", "value = 2\n")
    (source / "app.py").write_text("user edit\n")
    with pytest.raises(ValueError, match="Source changed"):
        apply_changes(source, work, manifest, config, tmp_path)
    assert (source / "app.py").read_text() == "user edit\n"
    (source / "app.py").write_text("value = 0\n")
    assert apply_changes(source, work, manifest, config, tmp_path) == ["app.py"]
    assert (source / "app.py").read_text() == "value = 2\n"


async def test_shared_gate_concurrency_and_cancellation():
    gateway = Gateway(settings(), Counter())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def worker():
        async with gateway.slot():
            if gateway.active == 4:
                entered.set()
            await release.wait()

    tasks = [asyncio.create_task(worker()) for _ in range(9)]
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.sleep(0.01)
    assert gateway.active == 4 and gateway.waiting == 5
    tasks[0].cancel()
    await asyncio.gather(tasks[0], return_exceptions=True)
    await asyncio.sleep(0.01)
    assert gateway.active == 4 and gateway.peak == 4
    release.set()
    await asyncio.gather(*tasks[1:])
    assert gateway.active == 0 and gateway.waiting == 0 and gateway.semaphore._value == 4
    await gateway.client.aclose()


async def test_request_limits():
    gateway = Gateway(settings(), Counter())
    body = {"model": gateway.config.model, "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 99999, "reasoning_effort": "none"}
    prepared = gateway.prepare(body)
    assert prepared["max_tokens"] == 16384
    assert prepared["chat_template_kwargs"]["enable_thinking"] is False
    with pytest.raises(ValueError, match="Context budget"):
        gateway.prepare({**body, "messages": [{"role": "user", "content": "x" * 65000}]})
    with pytest.raises(ValueError, match="Only"):
        gateway.prepare({**body, "model": "cloud"})
    await gateway.client.aclose()


def test_real_template_and_compaction(tmp_path):
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.save(str(tmp_path / "tokenizer.json"))
    (tmp_path / "chat_template.jinja").write_text(
        "{% for message in messages %}{{ message.role }}:{{ message.content }}{% endfor %}"
    )
    counter = ContextCounter(tmp_path)
    messages = [{"role": "system", "content": "Assist."}, {"role": "user", "content": "hello"}]
    assert counter.count({"messages": messages}) > 0
    result = compact(messages + [{"role": "assistant", "content": "old"}], ["edited app.py"])
    assert result[:2] == messages and "edited app.py" in result[2]["content"]


def test_opencode_binary_override(tmp_path, monkeypatch):
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("OPENCODE_BIN", str(binary))
    assert opencode_binary() == binary.resolve()


def test_generic_local_api_key_override(monkeypatch):
    config = settings().model_copy(update={"omlx_settings": None})
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local-test-key")
    assert config.upstream_key() == "local-test-key"


class FakeGateway:
    def __init__(self):
        self.config = settings()
        self.counter = Counter()
        self.client = httpx.AsyncClient()
        self.active = 0
        self.peak = 0

    def metrics(self):
        return {"max_concurrency": 4, "active": self.active, "peak_active": self.peak}

    async def complete(self, body):
        system = body["messages"][0]["content"]
        already_wrote = any(m.get("role") == "tool" for m in body["messages"])
        if "Plan the requested" in system:
            args = {"summary": "plan", "tasks": [{"task": "fix app", "write_paths": ["app.py"]}]}
            name = "finish"
        elif "Implement your" in system and not already_wrote:
            name, args = "write_file", {"path": "app.py", "content": "value = 1\n"}
        elif "Diagnose the" in system and not already_wrote:
            name, args = "write_file", {"path": "app.py", "content": "value = 2\n"}
        elif "Independently review" in system:
            name, args = "finish", {"summary": "review", "verdict": "approve", "findings": []}
        else:
            name, args = "finish", {"summary": "done"}
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "call1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
                "usage": {"completion_tokens": 10, "prompt_tokens": 100}}


async def test_full_feedback_loop_and_apply(project, tmp_path, monkeypatch):
    source, config = project
    async def check(work, config):
        return [{"name": "unit", "passed": (work / "app.py").read_text() == "value = 2\n", "output": "Expected value 2"}]
    monkeypatch.setattr("qwen_harness.jobs.verify", check)
    gateway = FakeGateway()
    manager = Manager(gateway, tmp_path / "runs")
    run = manager.start(str(source), "Set value to 2")
    await manager.tasks[run["id"]]
    status = manager.status(run["id"])
    assert status["status"] == "ready", status
    assert status["round"] == 1 and gateway.peak == 3
    assert (source / "app.py").read_text() == "value = 0\n"
    applied = manager.apply(run["id"])
    assert applied["status"] == "applied"
    assert (source / "app.py").read_text() == "value = 2\n"
    await manager.shutdown()
    await gateway.client.aclose()


def test_api_auth_and_schema(tmp_path, monkeypatch):
    monkeypatch.setattr("qwen_harness.server.token", lambda: "test-secret")
    gateway = FakeGateway()
    manager = Manager(gateway, tmp_path / "runs")
    with TestClient(create_app(gateway, manager)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/runs").status_code == 401
        assert client.get("/runs", headers={"Authorization": "Bearer test-secret"}).json() == []
        assert client.post("/runs", json={}, headers={"Authorization": "Bearer test-secret"}).status_code == 422
