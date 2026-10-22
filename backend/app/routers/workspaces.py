from __future__ import annotations

import json
import subprocess

from fastapi import APIRouter, HTTPException

from .. import config, db
from ..services import workspace as ws

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


@router.get("")
def list_workspaces():
    rows = db.query("SELECT id, workspace_id, updated_at FROM conversations ORDER BY updated_at DESC LIMIT 100")
    out = []
    for r in rows:
        wid = r["workspace_id"] or r["id"]
        try:
            root = ws.workspace_root(r["id"])
            files = sum(1 for _ in root.rglob("*") if _.is_file()) if root.exists() else 0
        except Exception:
            files = 0
        out.append({"conversation_id": r["id"], "workspace_id": wid, "files": files})
    return out


@router.get("/{cid}/files")
def workspace_files(cid: str, path: str = "."):
    if not db.query_one("SELECT id FROM conversations WHERE id = ?", (cid,)):
        raise HTTPException(404, "conversation not found")
    try:
        root = ws.workspace_root(cid)
        target = ws.resolve(root, path)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc))
    if not target.exists():
        raise HTTPException(404, "path not found")
    if target.is_file():
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"type": "file", "path": path, "content": text[:100_000]}
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:500]
    return {"type": "dir", "path": path,
            "entries": [{"name": e.name, "dir": e.is_dir()} for e in entries]}


@router.get("/{cid}/diff")
def workspace_diff(cid: str):
    """Git diff of the workspace (or its cloned subdir)."""
    if not db.query_one("SELECT id FROM conversations WHERE id = ?", (cid,)):
        raise HTTPException(404, "conversation not found")
    root = ws.workspace_root(cid)
    gitdir = root
    if not (root / ".git").exists():
        subs = [d for d in root.iterdir() if d.is_dir() and (d / ".git").exists()] if root.exists() else []
        if not subs:
            return {"diff": "", "note": "not a git repository"}
        gitdir = subs[0]
    try:
        out = subprocess.run(["git", "diff", "--stat", "HEAD", "--", "."],
                             cwd=str(gitdir), capture_output=True, text=True, timeout=30)
        stat = out.stdout[:8000]
        out2 = subprocess.run(["git", "diff", "HEAD", "--", "."],
                              cwd=str(gitdir), capture_output=True, text=True, timeout=30)
        return {"diff": out2.stdout[:60_000], "stat": stat}
    except FileNotFoundError:
        return {"diff": "", "note": "git binary not available"}
    except Exception as exc:
        raise HTTPException(500, str(exc)[:500])


@router.get("/../settings", include_in_schema=False)
def _unused():
    raise HTTPException(404, "")


settings_router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@settings_router.get("")
def get_settings():
    rows = db.query("SELECT key, value_json FROM settings")
    data = {r["key"]: json.loads(r["value_json"]) for r in rows}
    data.setdefault("default_model", config.DEFAULT_MODEL)
    data.setdefault("auto_approve", config.AUTO_APPROVE_WORKSPACE_WRITES)
    data.setdefault("catalog_ttl_seconds", config.CATALOG_TTL_SECONDS)
    data["key_configured"] = bool(config.OPENCODE_ZEN_API_KEY)
    return data


@settings_router.put("")
def put_settings(body: dict):
    allowed = {"default_model", "auto_approve", "catalog_ttl_seconds"}
    for key, value in body.items():
        if key not in allowed:
            raise HTTPException(400, f"unknown setting: {key}")
        db.execute("INSERT INTO settings (key, value_json) VALUES (?, ?)"
                   " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                   (key, json.dumps(value)))
        db.audit("setting.update", {"key": key})
    return get_settings()
