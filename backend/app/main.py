from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import conversations, executions
from .routers import memory as memory_router
from .routers import jira as jira_router
from .routers import notes as notes_router
from .routers import tasks as tasks_router
from .routers import onboarding as onboarding_router
from .routers import stt as stt_router
from .routers.workspaces import router as workspaces_router, settings_router
from .services import executions as exec_manager

from contextlib import asynccontextmanager
import asyncio as _asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    recovered = exec_manager.recover_on_startup()
    if recovered:
        print(f"[atlas] marked {recovered} orphaned execution(s) as interrupted")
    # Memory engine: seed files, recover indexer, start background tasks.
    try:
        from .services import dream as _dream
        from .services import indexer as _indexer
        from .services import maintenance as _maint
        from .services import memory_files as _mf
        _mf.read_file("SOUL.md")
        _mf.read_file("USER.md")
        rec = _indexer.recover()
        if rec["reset_processing"] or rec["reconciled_messages"]:
            print(f"[atlas] memory recover: {rec}")
        app.state.indexer_task = _asyncio.create_task(_indexer.worker())
        app.state.dream_task = _asyncio.create_task(_dream.scheduler())
        app.state.maint_task = _asyncio.create_task(_maint.loop())
    except Exception as exc:
        print(f"[atlas] memory startup note: {exc}")
    yield
    for attr in ("indexer_task", "dream_task", "maint_task"):
        task = getattr(app.state, attr, None)
        if task:
            task.cancel()


app = FastAPI(title="Atlas API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS + ["http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversations.router)
app.include_router(executions.router)
app.include_router(jira_router.router)
app.include_router(notes_router.router)
app.include_router(tasks_router.router)
app.include_router(stt_router.router)
app.include_router(memory_router.router)
app.include_router(onboarding_router.router)
app.include_router(workspaces_router)
app.include_router(settings_router)


@app.get("/api/v1/health")
def health():
    return {"ok": True, "service": "atlas", "version": "1.0.0",
            "key_configured": bool(config.OPENCODE_ZEN_API_KEY)}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Never leak secrets or tracebacks to the client.
    msg = str(exc)[:300]
    for secret in (config.OPENCODE_ZEN_API_KEY,):
        if secret:
            msg = msg.replace(secret, "[redacted]")
    return JSONResponse({"error": "internal error", "detail": msg}, status_code=500)


FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    dist = FRONTEND_DIST
    if dist.exists():
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        index = dist / "index.html"
        if index.exists():
            return FileResponse(str(index))
    return {"service": "atlas", "hint": "frontend not built; use the API under /api/v1"}
