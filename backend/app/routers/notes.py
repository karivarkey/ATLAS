"""Notes (vault) API: CRUD, tree, search, links, versions, sync."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..services import vault as v

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def _conflict(exc: v.NoteConflict) -> HTTPException:
    return HTTPException(409, {"error": "note_conflict", "detail": str(exc),
                               "current_version": exc.current_version,
                               "current_hash": exc.current_hash})


@router.get("/tree")
def tree():
    return {"tree": v.list_tree()}


@router.get("/search")
def search(q: str = "", limit: int = 50):
    return {"results": v.search_notes(q, min(limit, 100))}


@router.get("/suggest")
def suggest(prefix: str = "", limit: int = 20):
    return {"suggestions": v.complete(prefix, min(limit, 50))}


@router.get("/broken")
def broken():
    return {"broken": v.broken_links()}


@router.post("/sync")
def sync():
    return v.sync_external()


@router.post("")
def create(body: dict):
    try:
        return v.create_note(body.get("path", ""), body.get("content", ""),
                             actor=str(body.get("actor", "user")))
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.get("")
def read(path: str, record_exec: str = ""):
    try:
        return v.get_note(path, record_exec=record_exec)
    except KeyError:
        raise HTTPException(404, {"error": "not_found", "detail": f"note not found: {path}"})
    except FileNotFoundError as exc:
        raise HTTPException(410, {"error": "file_missing", "detail": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.put("")
def update(path: str, body: dict):
    try:
        return v.update_note(path, body.get("content", ""),
                             actor=str(body.get("actor", "user")),
                             expected_version=body.get("expected_version"),
                             expected_hash=body.get("expected_hash"))
    except KeyError:
        raise HTTPException(404, {"error": "not_found", "detail": f"note not found: {path}"})
    except v.NoteConflict as exc:
        raise _conflict(exc)
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.delete("")
def delete(path: str, actor: str = "user"):
    try:
        return v.delete_note(path, actor=actor)
    except KeyError:
        raise HTTPException(404, {"error": "not_found", "detail": f"note not found: {path}"})
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.post("/move")
def move(body: dict):
    try:
        return v.rename_note(body.get("old_path", ""), body.get("new_path", ""),
                             actor=str(body.get("actor", "user")))
    except KeyError as exc:
        raise HTTPException(404, {"error": "not_found", "detail": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.post("/folder")
def folder(body: dict):
    try:
        return v.make_folder(body.get("path", ""))
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.get("/versions")
def versions(path: str):
    try:
        return {"versions": v.list_versions(path)}
    except KeyError:
        raise HTTPException(404, {"error": "not_found", "detail": f"note not found: {path}"})


@router.get("/versions/content")
def version_content(path: str, version: int):
    try:
        return {"path": path, "version": version,
                "content": v.get_version_content(path, version)}
    except KeyError as exc:
        raise HTTPException(404, {"error": "not_found", "detail": str(exc)})


@router.post("/rollback")
def rollback(body: dict):
    try:
        return v.rollback(body.get("path", ""), int(body.get("version", 0)),
                          actor=str(body.get("actor", "user")))
    except KeyError as exc:
        raise HTTPException(404, {"error": "not_found", "detail": str(exc)})
    except v.NoteConflict as exc:
        raise _conflict(exc)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.post("/read-record")
def read_record(body: dict):
    """Record that an execution read a note at its current version."""
    try:
        return v.get_note(body.get("path", ""),
                          record_exec=str(body.get("execution_id", "") or ""))
    except KeyError:
        raise HTTPException(404, {"error": "not_found", "detail": "note not found"})
