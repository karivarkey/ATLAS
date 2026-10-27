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


@router.post("/copy")
def copy(body: dict):
    try:
        return v.copy_note(body.get("src", "") or body.get("old_path", ""),
                           body.get("dst", "") or body.get("new_path", ""),
                           actor=str(body.get("actor", "user")))
    except KeyError as exc:
        raise HTTPException(404, {"error": "not_found", "detail": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.delete("/folder")
def folder_delete(path: str, actor: str = "user"):
    try:
        return v.delete_folder(path, actor=actor)
    except KeyError as exc:
        raise HTTPException(404, {"error": "not_found", "detail": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.post("/folder/move")
def folder_move(body: dict):
    try:
        return v.move_folder(body.get("old_path", "") or body.get("src", ""),
                             body.get("new_path", "") or body.get("dst", ""),
                             actor=str(body.get("actor", "user")))
    except KeyError as exc:
        raise HTTPException(404, {"error": "not_found", "detail": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.post("/folder/copy")
def folder_copy(body: dict):
    try:
        return v.copy_folder(body.get("src", "") or body.get("old_path", ""),
                             body.get("dst", "") or body.get("new_path", ""),
                             actor=str(body.get("actor", "user")))
    except KeyError as exc:
        raise HTTPException(404, {"error": "not_found", "detail": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"error": "bad_request", "detail": str(exc)})


@router.get("/semantic-search")
async def semantic_search(q: str = "", limit: int = 10):
    """Lexical + (optional) embedding hybrid search over notes.

    Never 500s when embeddings are down — degrades to lexical ranking and
    reports ``semantic: false``.
    """
    q = (q or "").strip()
    if not q:
        return {"results": [], "semantic": False}
    limit = max(1, min(int(limit or 10), 25))
    query_vec = None
    doc_vecs: dict[str, list[float]] = {}
    semantic = False
    try:
        from ..services import embeddings as _emb
        import asyncio as _aio

        async def _embed_all():
            notes = v.search_notes(q, limit=200)  # candidate paths via lexical
            paths = [h["path"] for h in notes]
            # also include title-only hits from full tree so vector search
            # can surface notes lexical missed
            try:
                seen = set(paths)
                def walk(ns):
                    for n in ns or []:
                        if n.get("type") == "note" and n.get("path") not in seen:
                            paths.append(n["path"])
                        elif n.get("children"):
                            walk(n["children"])
                walk(v.list_tree())
            except Exception:
                pass
            texts = [q[:2000]]
            use_paths: list[str] = []
            for p in paths[:60]:
                try:
                    row = v.note_by_path(p)
                    if not row:
                        continue
                    content = v.resolve(p).read_text(encoding="utf-8", errors="replace")
                    _, body = v.split_frontmatter(content)
                    texts.append(f"{row['title']}\n{body[:1500]}")
                    use_paths.append(p)
                except Exception:
                    continue
            vecs = await _aio.wait_for(_emb.embed_batch(texts), timeout=30)
            return vecs[0], {p: vec for p, vec in zip(use_paths, vecs[1:])}

        try:
            _aio.get_running_loop()
        except RuntimeError:
            query_vec, doc_vecs = _aio.run(_embed_all())
        else:
            # inside a running loop we can await directly (this handler is async)
            query_vec, doc_vecs = await _embed_all()
        semantic = query_vec is not None
    except Exception:
        query_vec, doc_vecs = None, {}
    results = v.hybrid_note_search(q, limit=limit, query_vec=query_vec,
                                   doc_vecs=doc_vecs or None)
    return {"results": results, "semantic": semantic}


@router.post("/ask")
async def ask(body: dict):
    """Notes-sidebar agent turn: full CRUD + Q&A over the vault.

    Returns ``{answer, citations, touched, opened, semantic}``. ``touched``
    lists vault paths the turn created/edited/moved/deleted so the UI can
    refresh; ``opened`` is the note to show. When no chat model is reachable,
    falls back to an extractive summary so the panel still lists clickable
    notes.
    """
    question = str((body or {}).get("question", "") or (body or {}).get("q", "")).strip()
    limit = max(1, min(int((body or {}).get("limit", 6) or 6), 10))
    if not question:
        raise HTTPException(400, {"error": "bad_request", "detail": "question is required"})
    history = (body or {}).get("history", []) or []
    # 1) Agentic loop: the model can search, read, create, edit, move and
    #    delete notes (plus tasks/folders) via tools.
    try:
        from ..services import noteagent as _na
        return await _na.run_notes_chat(
            question, history=history if isinstance(history, list) else [], limit=limit)
    except Exception:
        pass  # no models / provider down → extractive fallback below
    # Multi-turn history so follow-ups ("and what about pricing?") stay grounded.
    hist_lines: list[str] = []
    if isinstance(history, list):
        for m in history[-8:]:
            if isinstance(m, dict) and str(m.get("content", "")).strip():
                who = "User" if str(m.get("role", "")) == "user" else "Assistant"
                hist_lines.append(f"{who}: {str(m['content'])[:1000]}")
    sem = await semantic_search(question, limit)
    hits: list[dict] = sem["results"]
    citations = [{"path": h["path"], "title": h.get("title", ""),
                  "snippet": h.get("snippet", "")} for h in hits]
    if not hits:
        return {"answer": "I couldn't find any notes matching that. Try different keywords, or create a note for it.",
                "citations": [], "touched": [], "opened": None, "trace": [], "semantic": sem["semantic"]}
    context = "\n\n".join(
        f"[note {h['path']} — {h.get('title', '')}]\n{(h.get('snippet') or '')[:800]}"
        for h in hits)
    # Try a real LLM synthesis; fall back to extractive on any failure.
    try:
        from ..services import model_catalog as _cat
        from ..services import providers as _prov
        from .. import config as _cfg

        catalog = await _cat.get_catalog()
        models = catalog.get("models", [])
        wanted = str((body or {}).get("model", "") or _cfg.DEFAULT_MODEL)
        model = next((m for m in models if m["id"] == wanted), None) or (models[0] if models else None)
        if model is None:
            raise RuntimeError("no chat models available")
        prompt = (
            "You answer questions using ONLY the provided vault notes. "
            "Be concise (<=150 words). Name the note paths you used like [path]. "
            "If the notes don't answer it, say so plainly.\n\n"
            + (f"Conversation so far:\n{chr(10).join(hist_lines)}\n\n" if hist_lines else "")
            + f"Question: {question}\n\nNotes:\n{context[:6000]}")
        text_parts: list[str] = []
        async for ev in _prov.complete(model, [{"role": "user", "content": prompt}]):
            if isinstance(ev, dict) and ev.get("text"):
                text_parts.append(ev["text"])
        answer = "".join(text_parts).strip()
        if not answer:
            raise RuntimeError("empty model reply")
        return {"answer": answer, "citations": citations, "touched": [], "opened": None, "trace": [],
                "semantic": sem["semantic"], "model": model["id"]}
    except Exception as exc:
        lines = [f"**Top matches for “{question}”** (AI synthesis unavailable — showing extracts):", ""]
        for h in hits:
            lines.append(f"- **{h.get('title') or h['path']}** `[{h['path']}]` — {(h.get('snippet') or '')[:220]}")
        return {"answer": "\n".join(lines), "citations": citations,
                "touched": [], "opened": None, "trace": [],
                "semantic": sem["semantic"], "fallback": True,
                "detail": str(exc)[:200]}


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
