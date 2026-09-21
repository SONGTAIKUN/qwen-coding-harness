from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path, PurePosixPath

from .agent import run_agent
from .config import atomic_json, state_dir
from .runner import verify
from .workspace import Project, Workspace, apply_changes, changes, digest, diff, files, in_scope, snapshot

TERMINAL = {"ready", "applied", "failed", "cancelled", "interrupted", "needs_attention"}


class Job:
    def __init__(self, manager, directory: Path):
        self.manager = manager
        self.directory = directory
        self.data = json.loads((directory / "run.json").read_text())
        self.project = Project.model_validate(self.data["project"])
        self.workspace = directory / "worktree"
        self.reserved = 0

    def save(self):
        self.data["updated_at"] = time.time()
        atomic_json(self.directory / "run.json", self.data)

    def event(self, kind: str, **fields):
        record = {"time": time.time(), "type": kind, **fields}
        with (self.directory / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.data["last_event"] = record
        self.save()

    def check_budget(self):
        if self.data["usage"]["completion_tokens"] + self.reserved >= self.manager.gateway.config.max_job_output_tokens:
            raise RuntimeError("Job output token budget exhausted")

    def reserve(self, maximum: int) -> int:
        if self.data["usage"]["completion_tokens"] + self.reserved + maximum > self.manager.gateway.config.max_job_output_tokens:
            raise RuntimeError("Remaining job budget cannot reserve another model response")
        self.reserved += maximum
        self.data["outstanding_tokens"] = self.reserved
        self.save()
        return maximum

    def account(self, reserved: int, actual: int, prompt: int):
        self.reserved -= reserved
        self.data["outstanding_tokens"] = self.reserved
        self.data["usage"]["completion_tokens"] += actual
        self.data["usage"]["prompt_tokens"] += prompt
        self.data["usage"]["model_calls"] += 1
        self.save()

    def phase(self, value):
        self.data["phase"] = value
        self.event("phase", phase=value)

    def current_diff(self):
        names = changes(self.workspace, self.data["manifest"])
        return diff(self.directory / "baseline", self.workspace, names)

    async def checks(self, label: str):
        before = {name: digest(path) for name, path in files(self.workspace)}
        self.event("verification_start", label=label)
        result = await verify(self.workspace, self.project)
        after = {name: digest(path) for name, path in files(self.workspace)}
        if before != after:
            raise RuntimeError("Verification modified source files; configure read-only checks")
        atomic_json(self.directory / f"checks-{label}.json", result)
        self.data["checks"] = result
        self.event("verification_done", label=label, passed=all(x["passed"] for x in result))
        return result

    def validate_plan(self, tasks: list[dict]) -> list[dict]:
        workspace = Workspace(self.workspace, self.project, self.project.writable_paths)
        for task in tasks:
            task["write_paths"] = [PurePosixPath(scope).as_posix() for scope in task["write_paths"]]
            for scope in task["write_paths"]:
                if scope != ".":
                    name = PurePosixPath(scope)
                    if name.is_absolute() or ".." in name.parts or any(c in scope for c in "*?[\\"):
                        raise ValueError(f"Invalid write scope: {scope}")
                    workspace.path(scope.rstrip("/"), write=True)
                elif self.project.writable_paths != ["."]:
                    raise ValueError("Planner may not widen project write permissions")
        for index, task in enumerate(tasks):
            for other in tasks[index + 1:]:
                if any(in_scope(a, [b]) or in_scope(b, [a]) for a in task["write_paths"] for b in other["write_paths"]):
                    self.event("plan_serialized", reason="overlapping write ownership")
                    return [{"task": "\n".join(t["task"] for t in tasks),
                             "write_paths": sorted({p for t in tasks for p in t["write_paths"]})}]
        return tasks

    async def workflow(self):
        goal = self.data["goal"]
        listing = "\n".join(Workspace(self.workspace, self.project).list())[:18000]
        context = f"User task:\n{goal}\n\nProject files:\n{listing}\n\nProject permissions/checks:\n{self.project.model_dump_json()}"
        if not self.data.get("research"):
            self.phase("research")
            async with asyncio.TaskGroup() as group:
                researchers = {role: group.create_task(run_agent(self, role, role, context))
                               for role in ["explorer", "test_designer", "specialist"]}
            self.data["research"] = {role: task.result() for role, task in researchers.items()}
            self.save()
        if not self.data.get("plan"):
            self.phase("planning")
            result = await run_agent(self, "planner", "planner", context + "\nResearch:\n" + json.dumps(self.data["research"], ensure_ascii=False))
            self.data["plan"] = self.validate_plan(result["tasks"])
            self.save()
        self.phase("implementation")
        async with asyncio.TaskGroup() as group:
            for index, task in enumerate(self.data["plan"]):
                group.create_task(run_agent(self, f"implementer-{index}", "implementer",
                    context + "\nYour assignment:\n" + task["task"] + "\nResearch:\n" + json.dumps(self.data["research"], ensure_ascii=False), task["write_paths"]))
        config = self.manager.gateway.config
        for round_number in range(self.data.get("round", 0), config.max_repair_rounds + 1):
            self.data["round"] = round_number
            self.phase("verification")
            results = await self.checks(str(round_number))
            review = None
            if all(item["passed"] for item in results):
                self.phase("review")
                review = await run_agent(self, f"reviewer-{round_number}", "reviewer",
                    context + "\nDiff (may be truncated; read complete files):\n" + self.current_diff()[:28000]
                    + "\nVerification:\n" + json.dumps(results, ensure_ascii=False)[-20000:])
                self.data["review"] = review
                if review["verdict"] == "approve":
                    self.data["verified_manifest"] = {name: digest(path) for name, path in files(self.workspace)}
                    self.data["changed_files"] = changes(self.workspace, self.data["manifest"])
                    (self.directory / "changes.diff").write_text(self.current_diff())
                    self.data["status"] = "ready"
                    self.phase("ready_to_apply")
                    return
            if round_number >= config.max_repair_rounds:
                self.data["status"] = "needs_attention"
                self.event("budget_stop", reason="repair round limit reached")
                return
            self.phase("repair")
            feedback = json.dumps({"checks": results, "review": review}, ensure_ascii=False)
            # A repair may touch multiple module interfaces, so it holds all project write ownership.
            await run_agent(self, f"debugger-{round_number}", "debugger",
                            context + "\nVerification/review feedback:\n" + feedback[-36000:], self.project.writable_paths)
            self.data["round"] = round_number + 1
            self.save()


class Manager:
    def __init__(self, gateway, root: Path | None = None):
        self.gateway = gateway
        self.root = root or state_dir() / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        for directory in self.root.iterdir():
            if not (directory / "run.json").exists():
                continue
            job = Job(self, directory)
            if job.data["status"] == "running":
                job.data["usage"]["completion_tokens"] += job.data.pop("outstanding_tokens", 0)
                if job.data.get("started_at"):
                    job.data["active_seconds"] = job.data.get("active_seconds", 0) + max(0, time.time() - job.data["started_at"])
                job.data["status"] = "interrupted"
                job.event("interrupted", reason="Harness service restarted; use resume")
            self.jobs[job.data["id"]] = job

    async def execute(self, job: Job):
        started = time.monotonic()
        config = self.gateway.config
        remaining = config.max_job_seconds - job.data.get("active_seconds", 0)
        job.data["started_at"] = time.time()
        job.save()
        try:
            if remaining <= 0:
                raise RuntimeError("Job time budget exhausted")
            async with asyncio.timeout(remaining):
                await job.workflow()
        except asyncio.CancelledError:
            job.data["status"] = "cancelled"
            job.event("cancelled")
            raise
        except Exception as exc:
            job.data["status"] = "failed"
            job.data["error"] = repr(exc)
            job.event("failed", error=repr(exc))
        finally:
            job.data["active_seconds"] = job.data.get("active_seconds", 0) + time.monotonic() - started
            job.data.pop("started_at", None)
            job.save()

    def start(self, root: str, goal: str) -> dict:
        source = Path(root).expanduser().resolve(strict=True)
        if not source.is_dir() or not goal.strip() or len(goal) > 24000:
            raise ValueError("Provide a project directory and task of 1-24000 characters")
        if source == Path.home() or source == Path("/") or self.root.is_relative_to(source):
            raise ValueError("Choose a source project, not the installation/state directory or home")
        if sum(not t.done() for t in self.tasks.values()) >= 4:
            raise ValueError("At most four workflows can be active")
        project = Project.load(source)
        if not project.checks:
            raise ValueError("Configure checks in .agent-project.json before starting")
        identifier = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        directory = self.root / identifier
        directory.mkdir(mode=0o700)
        try:
            manifest = snapshot(source, directory / "worktree", project)
            shutil.copytree(directory / "worktree", directory / "baseline")
        except Exception:
            shutil.rmtree(directory)
            raise
        atomic_json(directory / "run.json", {"id": identifier, "root": str(source), "goal": goal,
                    "project": project.model_dump(), "manifest": manifest, "status": "running", "phase": "queued",
                    "created_at": time.time(), "usage": {"completion_tokens": 0, "prompt_tokens": 0, "model_calls": 0}})
        job = Job(self, directory)
        self.jobs[identifier] = job
        self.tasks[identifier] = asyncio.create_task(self.execute(job))
        return self.status(identifier)

    def get(self, identifier: str) -> Job:
        if identifier not in self.jobs:
            raise ValueError("Unknown run id")
        return self.jobs[identifier]

    def status(self, identifier: str) -> dict:
        job = self.get(identifier)
        return {**{key: value for key, value in job.data.items() if key not in {"manifest", "verified_manifest", "project", "research"}},
                "artifacts": str(job.directory), "gateway": self.gateway.metrics()}

    async def cancel(self, identifier: str) -> dict:
        task = self.tasks.get(identifier)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.status(identifier)

    def resume(self, identifier: str) -> dict:
        job = self.get(identifier)
        if job.data["status"] not in {"interrupted", "cancelled", "failed"}:
            raise ValueError("Only failed, cancelled, or interrupted runs can resume")
        if sum(not t.done() for t in self.tasks.values()) >= 4:
            raise ValueError("At most four workflows can be active")
        job.data["status"] = "running"
        job.data.pop("error", None)
        job.event("resumed")
        self.tasks[identifier] = asyncio.create_task(self.execute(job))
        return self.status(identifier)

    def apply(self, identifier: str) -> dict:
        job = self.get(identifier)
        if job.data["status"] != "ready":
            raise ValueError("Only verified and reviewed runs can be applied")
        if {name: digest(path) for name, path in files(job.workspace)} != job.data["verified_manifest"]:
            raise ValueError("Candidate changed since verification; refusing to apply")
        applied = apply_changes(Path(job.data["root"]), job.workspace, job.data["manifest"], job.project, job.directory)
        job.data["status"] = "applied"
        job.data["phase"] = "applied"
        job.event("applied", files=applied)
        return self.status(identifier)

    async def shutdown(self):
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
