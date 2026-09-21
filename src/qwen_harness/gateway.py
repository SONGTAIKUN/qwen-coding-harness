from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import httpx

from .config import Settings
from .context import ContextCounter


class Gateway:
    def __init__(self, config: Settings, counter: ContextCounter):
        self.config = config
        self.counter = counter
        self.semaphore = asyncio.Semaphore(config.max_concurrency)
        self.active = 0
        self.peak = 0
        self.waiting = 0
        self.completed = 0
        self.failed = 0
        self.last_request = None
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds, connect=10),
            trust_env=False, limits=httpx.Limits(max_connections=12),
        )

    def prepare(self, request: dict) -> dict:
        body = dict(request)
        if body.get("model") not in {self.config.model, self.config.resolve(self.config.model_directory).name}:
            raise ValueError("Only the configured local Qwen model is enabled")
        body["model"] = self.config.model
        if body.get("n", 1) != 1:
            raise ValueError("Use separate requests; n must be 1")
        requested = body.pop("max_completion_tokens", body.get("max_tokens", self.config.max_output_tokens))
        body["max_tokens"] = min(max(1, int(requested)), self.config.max_output_tokens)
        template = dict(body.get("chat_template_kwargs") or {})
        effort = body.pop("reasoning_effort", template.get("reasoning_effort", "medium"))
        if effort == "none":
            template["enable_thinking"] = False
            effort = "low"
        if effort not in {"low", "medium", "xhigh"}:
            raise ValueError("Qwen supports low, medium, xhigh, or none in this harness")
        template.setdefault("enable_thinking", True)
        template["reasoning_effort"] = effort
        body["chat_template_kwargs"] = template
        length = self.counter.count(body)
        if length + body["max_tokens"] + self.config.context_reserve > self.config.context_window:
            raise ValueError(f"Context budget exceeded: {length} input + {body['max_tokens']} output + "
                             f"{self.config.context_reserve} reserve > {self.config.context_window}; compact context")
        self.last_request = {"input_tokens": length, "max_output_tokens": body["max_tokens"],
                             "enable_thinking": template["enable_thinking"], "reasoning_effort": effort}
        return body

    @asynccontextmanager
    async def slot(self):
        self.waiting += 1
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.config.request_timeout_seconds)
        finally:
            self.waiting -= 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield
        except BaseException:
            self.failed += 1
            raise
        else:
            self.completed += 1
        finally:
            self.active -= 1
            self.semaphore.release()

    def headers(self) -> dict:
        return {"Authorization": "Bearer " + self.config.upstream_key()}

    async def complete(self, request: dict) -> dict:
        body = self.prepare(request)
        body["stream"] = False
        async with self.slot():
            # A failed inference is not silently replayed: resume is explicit and logged.
            response = await self.client.post(self.config.upstream_url + "/chat/completions", json=body, headers=self.headers())
            response.raise_for_status()
            return response.json()

    def metrics(self) -> dict:
        return {"max_concurrency": self.config.max_concurrency, "active": self.active,
                "waiting": self.waiting, "peak_active": self.peak, "completed": self.completed,
                "failed_or_cancelled": self.failed, "last_request": self.last_request}
