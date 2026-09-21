import json

import pytest

from qwen_harness.config import atomic_json
from qwen_harness.evaluation import PublicProblem, judge, prepare
from qwen_harness.workspace import digest
from qwen_harness.workspace import Project
from qwen_harness.runner import verify


def test_public_rejects_hidden_fields():
    with pytest.raises(ValueError):
        PublicProblem(id="a", prompt="sum", public_tests=[{"input": "1 2", "output": "3"}], hidden_tests=[])


async def test_eval_isolation_and_once_only(tmp_path, monkeypatch):
    public = tmp_path / "public.jsonl"
    public.write_text(json.dumps({"id": "a", "prompt": "sum two integers", "public_tests": [{"input": "1 2", "output": "3"}]}))
    hidden = tmp_path / "hidden.jsonl"
    hidden.write_text(json.dumps({"id": "a", "hidden_tests": [{"input": "9 8", "output": "17"}]}))
    directory = tmp_path / "experiment"
    prepare(public, directory)
    assert "9 8" not in (directory / "projects/a/public_tests.json").read_text()
    candidate = directory / "submissions/a/candidate.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("print(sum(map(int, input().split())))")
    atomic_json(candidate.parent / "submission.json", {"candidate_sha256": digest(candidate)})
    async def fake_check(check, work, project, stdin, merge_stderr):
        assert stdin == b"9 8" and not (work / "hidden.jsonl").exists()
        assert not merge_stderr
        return {"passed": True, "output_truncated": False, "output": "17\n"}
    monkeypatch.setattr("qwen_harness.evaluation.run_check", fake_check)
    assert (await judge(hidden, directory))["accuracy"] == 1
    with pytest.raises(ValueError, match="already started"):
        await judge(hidden, directory)


@pytest.mark.sandbox
async def test_public_and_final_judge_execute_in_sandbox(tmp_path):
    public = tmp_path / "public.jsonl"
    public.write_text(json.dumps({"id": "a", "prompt": "sum two integers", "public_tests": [{"input": "1 2", "output": "3"}]}))
    hidden = tmp_path / "hidden.jsonl"
    hidden.write_text(json.dumps({"id": "a", "hidden_tests": [{"input": "9 8", "output": "17"}]}))
    directory = tmp_path / "experiment"
    prepare(public, directory)
    project = directory / "projects/a"
    assert not (await verify(project, Project.load(project)))[0]["passed"]
    solution = "import sys\nprint('debug', file=sys.stderr)\nprint(sum(map(int, input().split())))\n"
    (project / "solution.py").write_text(solution)
    result = await verify(project, Project.load(project))
    assert result[0]["passed"], result
    candidate = directory / "submissions/a/candidate.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(solution)
    atomic_json(candidate.parent / "submission.json", {"candidate_sha256": digest(candidate)})
    assert (await judge(hidden, directory))["accuracy"] == 1
