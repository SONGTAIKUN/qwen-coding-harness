from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings, token
from .context import ContextCounter
from .gateway import Gateway
from .jobs import Manager, TERMINAL


class Start(BaseModel):
    root: str
    goal: str = Field(min_length=1, max_length=24000)


def create_app(gateway=None, manager=None):
    @asynccontextmanager
    async def lifespan(app):
        nonlocal gateway, manager
        if gateway is None:
            config = settings()
            gateway = Gateway(config, ContextCounter(config.resolve(config.model_directory)))
        if manager is None:
            manager = Manager(gateway)
        yield
        await manager.shutdown()
        await gateway.client.aclose()

    app = FastAPI(title="Qwen Coding Harness", lifespan=lifespan)

    async def authorize(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + token()):
            raise HTTPException(401, "Invalid local harness token")

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request, exc):
        return JSONResponse({"error": "oMLX request failed: " + str(exc)}, status_code=502)

    @app.get("/health")
    async def health():
        return {"service": "qwen-coding-harness", "version": "0.1.0", "pid": os.getpid(),
                "status": "healthy", **gateway.metrics()}

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    async def models():
        return {"object": "list", "data": [{"id": gateway.config.model, "object": "model", "owned_by": "local"}]}

    @app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
    async def complete(request: Request):
        raw = await request.body()
        if len(raw) > 8_000_000:
            raise HTTPException(413, "Model request exceeds 8 MB")
        body = gateway.prepare(json.loads(raw))
        if not body.get("stream"):
            return await gateway.complete(body)
        # The permit is held through the entire upstream stream, including client cancellation.
        lease = gateway.slot()
        await lease.__aenter__()
        response = None
        try:
            upstream = gateway.client.build_request("POST", gateway.config.upstream_url + "/chat/completions",
                                                    json=body, headers=gateway.headers())
            response = await gateway.client.send(upstream, stream=True)
            if response.status_code >= 400:
                error = await response.aread()
                await response.aclose()
                await lease.__aexit__(None, None, None)
                return JSONResponse({"error": error.decode(errors="replace")[:3000]}, status_code=response.status_code)
        except BaseException as exc:
            if response:
                await response.aclose()
            await lease.__aexit__(type(exc), exc, exc.__traceback__)
            raise

        async def stream():
            try:
                async for data in response.aiter_raw():
                    yield data
            finally:
                await response.aclose()
                await lease.__aexit__(None, None, None)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/runs", dependencies=[Depends(authorize)])
    async def start(body: Start):
        return manager.start(body.root, body.goal)

    @app.get("/runs", dependencies=[Depends(authorize)])
    async def runs(root: str | None = None):
        return [manager.status(identifier) for identifier, job in manager.jobs.items()
                if root is None or job.data["root"] == root][-30:]

    @app.get("/runs/{identifier}", dependencies=[Depends(authorize)])
    async def status(identifier: str, wait: int = 0):
        task = manager.tasks.get(identifier)
        if task and not task.done() and wait:
            await asyncio.wait([task], timeout=min(max(wait, 0), 30))
        return manager.status(identifier)

    @app.post("/runs/{identifier}/{action}", dependencies=[Depends(authorize)])
    async def control(identifier: str, action: str):
        if action == "cancel":
            return await manager.cancel(identifier)
        if action == "resume":
            return manager.resume(identifier)
        if action == "apply":
            return manager.apply(identifier)
        raise HTTPException(404, "Unknown action")

    @app.get("/runs/{identifier}/diff", dependencies=[Depends(authorize)], response_class=PlainTextResponse)
    async def read_diff(identifier: str):
        return manager.get(identifier).current_diff()

    return app
