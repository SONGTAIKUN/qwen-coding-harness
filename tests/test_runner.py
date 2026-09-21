import pytest

from qwen_harness.runner import run_check
from qwen_harness.workspace import Check, Project


@pytest.mark.sandbox
async def test_runner_success(tmp_path):
    result = await run_check(Check(name="python", argv=["{python}", "-c", "print('verified')"]), tmp_path, Project())
    assert result["passed"], result
    assert "verified" in result["output"]


@pytest.mark.sandbox
async def test_runner_network_write_read_restrictions(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    secret = tmp_path / "private"
    secret.write_text("secret")
    for program in [
        f"open({str(secret)!r}).read()",
        f"open({str(secret)!r}, 'w').write('bad')",
        "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=1)",
    ]:
        result = await run_check(Check(name="deny", argv=["{python}", "-c", program]), workspace, Project())
        assert not result["passed"], result
        assert "PermissionError" in result["output"], result
    assert secret.read_text() == "secret"


@pytest.mark.sandbox
async def test_runner_timeout(tmp_path):
    result = await run_check(Check(name="timeout", argv=["{python}", "-c", "import time; time.sleep(60)"], timeout_seconds=1), tmp_path, Project())
    assert not result["passed"] and "timed out" in result["error"]
