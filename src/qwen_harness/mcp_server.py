from __future__ import annotations

import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from .config import settings, token


def main():
    root = str(Path(os.environ.get("QWEN_PROJECT_ROOT", os.getcwd())).resolve())
    client = httpx.AsyncClient(base_url=settings().url, headers={"Authorization": "Bearer " + token()}, timeout=60, trust_env=False)
    mcp = FastMCP("qwen-harness")

    async def request(method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def owned(identifier):
        run = await request("GET", f"/runs/{identifier}")
        if run["root"] != root:
            raise ValueError("This MCP connection is bound to a different project")
        return run

    @mcp.tool()
    async def start_coding(goal: str) -> dict:
        """Start an isolated coding workflow for this project. It explores, plans up to four parallel workers, tests, repairs and reviews. Returns a run id. Poll wait_coding; a ready result must be applied using apply_coding."""
        return await request("POST", "/runs", json={"root": root, "goal": goal})

    @mcp.tool()
    async def wait_coding(run_id: str) -> dict:
        """Wait up to 25 seconds for a workflow. Repeat while status is running. ready means tests and review passed; failed/needs_attention is not success."""
        await owned(run_id)
        return await request("GET", f"/runs/{run_id}", params={"wait": 25})

    @mcp.tool()
    async def list_coding_runs() -> list[dict]:
        """List recent workflows for the currently selected project, including resumable runs."""
        return await request("GET", "/runs", params={"root": root})

    @mcp.tool()
    async def coding_diff(run_id: str) -> str:
        """Show the proposed patch for a workflow before applying it."""
        await owned(run_id)
        response = await client.get(f"/runs/{run_id}/diff")
        response.raise_for_status()
        return response.text[:48000]

    @mcp.tool()
    async def apply_coding(run_id: str) -> dict:
        """Apply a verified, reviewed workflow to this project. Refuses to overwrite source edited since the snapshot. Use after ready when the user requested code implementation."""
        await owned(run_id)
        return await request("POST", f"/runs/{run_id}/apply")

    @mcp.tool()
    async def cancel_coding(run_id: str) -> dict:
        """Stop a workflow while keeping its files and checkpoints."""
        await owned(run_id)
        return await request("POST", f"/runs/{run_id}/cancel")

    @mcp.tool()
    async def resume_coding(run_id: str) -> dict:
        """Resume failed/cancelled/interrupted work from completed tools and agents. Original budgets still apply."""
        await owned(run_id)
        return await request("POST", f"/runs/{run_id}/resume")

    mcp.run(transport="stdio")
