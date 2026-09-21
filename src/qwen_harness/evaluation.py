"""Public-only coding runs; hidden judging is a separate, model-free operation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ROOT, atomic_json, settings, token
from .runner import run_check
from .workspace import Check, Project, digest


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str
    output: str


class PublicProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    prompt: str = Field(min_length=1, max_length=18000)
    public_tests: list[Case] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", value):
            raise ValueError("Use a filename-safe, unique problem id")
        return value


class HiddenProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    hidden_tests: list[Case] = Field(min_length=1)


def load_rows(path, schema):
    rows = [schema.model_validate_json(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if not rows or len({row.id for row in rows}) != len(rows):
        raise ValueError("Dataset must contain nonempty, unique problem ids")
    return rows


PUBLIC_RUNNER = '''import json
import resource
import subprocess
import sys
import tempfile

cases = json.load(open("public_tests.json"))
for index, case in enumerate(cases):
    with tempfile.TemporaryFile(mode="w+") as out, tempfile.TemporaryFile(mode="w+") as err:
        def limits():
            resource.setrlimit(resource.RLIMIT_FSIZE, (5000000, 5000000))
            resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        result = subprocess.run([sys.executable, "solution.py"], input=case["input"],
                                stdout=out, stderr=err, text=True, timeout=5, preexec_fn=limits)
        out.seek(0)
        err.seek(0)
        actual, error = out.read(5000000), err.read(5000000)
        if result.returncode != 0 or actual.split() != case["output"].split():
            print(json.dumps({"case": index, "input": case["input"], "expected": case["output"],
                              "actual": actual[-3000:], "stderr": error[-3000:]}))
            sys.exit(1)
print(f"Passed {len(cases)} public cases")
'''


def prepare(public_file: Path, directory: Path):
    problems = load_rows(public_file, PublicProblem)
    if directory.exists():
        raise ValueError("Use a new evaluation directory; existing experiments are never overwritten")
    directory.mkdir(parents=True, mode=0o700)
    for problem in problems:
        work = directory / "projects" / problem.id
        work.mkdir(parents=True)
        (work / "solution.py").write_text("raise NotImplementedError('Implement the solution')\n")
        (work / "public_runner.py").write_text(PUBLIC_RUNNER)
        (work / "problem.txt").write_text(problem.prompt)
        atomic_json(work / "public_tests.json", [case.model_dump() for case in problem.public_tests])
        config = Project(checks=[Check(name="public-cases", argv=["{python}", "public_runner.py"], timeout_seconds=120)],
                         writable_paths=["solution.py"], protected_paths=["public_tests.json", "public_runner.py", "problem.txt"])
        atomic_json(work / ".agent-project.json", config.model_dump())
    atomic_json(directory / "experiment.json", {"version": 1, "format": "python-stdin-stdout",
                "problem_ids": [p.id for p in problems], "public_dataset_sha256": digest(public_file),
                "created_at": time.time(), "output_comparison": "whitespace-separated tokens"})
    return {"directory": str(directory), "problems": len(problems)}


async def run(directory: Path, workers: int):
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    experiment = json.loads((directory / "experiment.json").read_text())
    gate = asyncio.Semaphore(workers)
    async with httpx.AsyncClient(base_url=settings().url, headers={"Authorization": "Bearer " + token()},
                                 timeout=60, trust_env=False) as client:
        async def call(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        async def solve(identifier):
            async with gate:
                target = directory / "submissions" / identifier
                final = target / "submission.json"
                if final.exists():
                    return json.loads(final.read_text())
                target.mkdir(parents=True, exist_ok=True)
                checkpoint = target / "run.json"
                if checkpoint.exists():
                    status = await call("GET", "/runs/" + json.loads(checkpoint.read_text())["id"])
                    if status["status"] in {"interrupted", "cancelled", "failed"}:
                        status = await call("POST", f"/runs/{status['id']}/resume")
                else:
                    project = directory / "projects" / identifier
                    goal = ((project / "problem.txt").read_text() + "\nImplement a standalone Python stdin/stdout solution in solution.py. "
                            "Only public tests are available. Do not change the public tests or runner. Optimize for all valid inputs, not just samples.")
                    status = await call("POST", "/runs", json={"root": str(project), "goal": goal})
                    atomic_json(checkpoint, {"id": status["id"]})
                while status["status"] == "running":
                    status = await call("GET", f"/runs/{status['id']}", params={"wait": 25})
                candidate = Path(status["artifacts"]) / "worktree/solution.py"
                if status["status"] not in {"ready", "needs_attention"}:
                    return {"id": identifier, "status": status["status"], "run_id": status["id"], "error": status.get("error")}
                shutil.copyfile(candidate, target / "candidate.py")
                record = {"id": identifier, "run_id": status["id"], "status": status["status"],
                          "candidate_sha256": digest(target / "candidate.py"), "usage": status["usage"],
                          "active_seconds": status.get("active_seconds"), "repair_round": status.get("round", 0)}
                atomic_json(final, record)
                return record

        results = await asyncio.gather(*(solve(i) for i in experiment["problem_ids"]))
        atomic_json(directory / "public-results.json", results)
        return results


async def judge(hidden_file: Path, directory: Path):
    # The hidden file is read only here, after frozen submissions; never by the gateway/agents.
    if hidden_file.is_relative_to(directory):
        raise ValueError("Keep hidden cases outside the experiment directory")
    hidden = {row.id: row for row in load_rows(hidden_file, HiddenProblem)}
    experiment = json.loads((directory / "experiment.json").read_text())
    if set(hidden) != set(experiment["problem_ids"]):
        raise ValueError("Hidden and public problem ids must match exactly")
    submissions = {}
    for identifier in experiment["problem_ids"]:
        target = directory / "submissions" / identifier
        record = json.loads((target / "submission.json").read_text())
        if digest(target / "candidate.py") != record["candidate_sha256"]:
            raise ValueError(f"Frozen candidate was changed: {identifier}")
        submissions[identifier] = record
    lock = directory / "judge-started.json"
    try:
        with lock.open("x") as handle:
            json.dump({"time": time.time(), "hidden_sha256": digest(hidden_file)}, handle)
    except FileExistsError:
        raise ValueError("Hidden judging was already started for this experiment; no repeated hidden feedback") from None
    scores = []
    for identifier in experiment["problem_ids"]:
        passed = True
        with tempfile.TemporaryDirectory(prefix="qwen-final-judge-") as temporary:
            work = Path(temporary).resolve()
            shutil.copyfile(directory / "submissions" / identifier / "candidate.py", work / "solution.py")
            for case in hidden[identifier].hidden_tests:
                outcome = await run_check(Check(name="hidden", argv=["{python}", "solution.py"], timeout_seconds=5),
                                          work, Project(), case.input.encode(), merge_stderr=False)
                if not outcome["passed"] or outcome["output_truncated"] or outcome["output"].split() != case.output.split():
                    passed = False
                    break
        scores.append({"id": identifier, "passed": passed, "candidate_sha256": submissions[identifier]["candidate_sha256"]})
    report = {"problems": len(scores), "passed": sum(row["passed"] for row in scores), "results": scores,
              "metric": "final agentic stdin/stdout accuracy; not official LiveCodeBench pass@1",
              "hidden_dataset_sha256": digest(hidden_file), "model": settings().model, "time": time.time()}
    report["accuracy"] = report["passed"] / report["problems"]
    atomic_json(directory / "hidden-score.json", report)
    return report
