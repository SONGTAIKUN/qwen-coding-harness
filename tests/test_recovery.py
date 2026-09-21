import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from qwen_harness.config import atomic_json, settings
from qwen_harness.gateway import Gateway
from qwen_harness.jobs import Manager
from qwen_harness.server import create_app
from qwen_harness.workspace import Project
from test_core import FakeGateway, Counter


async def test_cancel_and_resume_reuses_checkpoints(tmp_path, monkeypatch):
    source = tmp_path / "project"
    source.mkdir()
    (source / "app.py").write_text("value = 0\n")
    atomic_json(source / ".agent-project.json", Project(checks=[{"name": "unit", "argv": ["true"]}]).model_dump())
    checks_called = asyncio.Event()
    release = asyncio.Event()
    async def check(work, config):
        checks_called.set()
        await release.wait()
        return [{"name": "unit", "passed": True}]
    monkeypatch.setattr("qwen_harness.jobs.verify", check)
    gateway = FakeGateway()
    manager = Manager(gateway, tmp_path / "runs")
    identifier = manager.start(str(source), "fix app")["id"]
    await asyncio.wait_for(checks_called.wait(), 5)
    checkpoint = (manager.get(identifier).directory / "agents/implementer-0.json").read_bytes()
    assert (await manager.cancel(identifier))["status"] == "cancelled"
    release.set()
    manager.resume(identifier)
    await manager.tasks[identifier]
    assert manager.status(identifier)["status"] == "ready"
    assert (manager.get(identifier).directory / "agents/implementer-0.json").read_bytes() == checkpoint
    await manager.shutdown()
    await gateway.client.aclose()


async def test_cancel_waiter_releases_queue_count():
    gateway = Gateway(settings(), Counter())
    async with gateway.slot(), gateway.slot(), gateway.slot(), gateway.slot():
        async def waiting():
            async with gateway.slot():
                pytest.fail("Should not acquire a slot")
        task = asyncio.create_task(waiting())
        await asyncio.sleep(0.01)
        assert gateway.waiting == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert gateway.waiting == 0 and gateway.active == 4
    assert gateway.active == 0 and gateway.semaphore._value == 4
    await gateway.client.aclose()


def test_stream_holds_slot_until_body_done(tmp_path, monkeypatch):
    monkeypatch.setattr("qwen_harness.server.token", lambda: "secret")
    monkeypatch.setenv("OMLX_API_KEY", "dummy")
    gateway = Gateway(settings(), Counter())
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            assert gateway.active == 1
            yield b'data: {"choices": []}\n\n'
            assert gateway.active == 1
            yield b'data: [DONE]\n\n'
    gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream())))
    manager = Manager(gateway, tmp_path / "runs")
    with TestClient(create_app(gateway, manager)) as client:
        result = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                             json={"model": settings().model, "messages": [{"role": "user", "content": "hi"}], "stream": True})
        assert result.status_code == 200 and "[DONE]" in result.text
        assert gateway.active == 0 and gateway.semaphore._value == 4
