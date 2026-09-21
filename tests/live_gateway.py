"""Explicit, optional live test: five requests must share four slots."""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qwen_harness.config import settings, token, atomic_json, state_dir


async def main():
    config = settings()
    async with httpx.AsyncClient(base_url=config.url, headers={"Authorization": "Bearer " + token()},
                                 timeout=120, trust_env=False) as client:
        async def generate(index):
            response = await client.post("/v1/chat/completions", json={"model": config.model,
                "messages": [{"role": "user", "content": f"List integers from {index} to 100, separated by spaces. No explanation."}],
                "max_tokens": 64, "reasoning_effort": "none", "temperature": 0})
            response.raise_for_status()
            result = response.json()
            return {"request": index, "finish_reason": result["choices"][0]["finish_reason"], "usage": result.get("usage")}
        tasks = [asyncio.create_task(generate(i)) for i in range(5)]
        peak = waiting = 0
        while not all(task.done() for task in tasks):
            health = (await client.get("/health")).json()
            peak = max(peak, health["active"])
            waiting = max(waiting, health["waiting"])
            await asyncio.sleep(0.15)
        results = await asyncio.gather(*tasks)
        health = (await client.get("/health")).json()
        assert peak == 4 and waiting >= 1 and health["active"] == health["waiting"] == 0
        report = {"peak_observed": peak, "max_queued": waiting, "requests": results, "final": health}
        atomic_json(state_dir() / "live-concurrency-test.json", report)
        print(json.dumps(report, indent=2))


asyncio.run(main())
