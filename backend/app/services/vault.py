"""Obsidian-style Markdown vault: real files on disk; DB is an operational index.

- Vault root defaults to ``./vault`` (override with ``ATLAS_VAULT_DIR``).
- Files are the source of truth for content; the DB holds an index
  (stable id, path, title, hash, version, mtime) plus full version history.
- All writes are atomic (tmp file + os.replace) and optimistic-concurrency
  guarded (expected_version / expected_hash). No AI write may bypass this
  service — agent tool dispatch must go through here.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from pathlib import Path

from .. import config, db

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#([^\]|]+?))?(?:\|([^\]]+?))?\]\]")


class NoteConflict(Exception):
    """Optimistic-concurrency rejection: the note changed since it was read."""

    def __init__(self, msg: str, current_version: int, current_hash: str):
        super().__init__(msg)
        self.current_version = current_version
        self.current_hash = current_hash


def vault_root() -> Path:
    root = Path(os.environ.get("ATLAS_VAULT_DIR", str(config.BASE_DIR / "vault")))
    root.mkdir(parents=True, exist_ok=True)
    return root


def normalize(rel: str) -> str:
    """Validate + normalize a vault-relative path. Raises ValueError on escape."""
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("path is required")
    s = rel.strip().replace("\\", "/")
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s) or "\x00" in s:
        raise ValueError(f"path escapes vault: {rel}")
    parts: list[str] = []
    for p in s.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            raise ValueError(f"path escapes vault: {rel}")
        parts.append(p)
    if not parts:
        raise ValueError(f"path escapes vault: {rel}")
    return "/".join(parts)


def resolve(rel: str) -> Path:
    full = (vault_root() / normalize(rel)).resolve()
    root = vault_root().resolve()
    if full != root and root not in full.parents:
        raise ValueError(f"path escapes vault: {rel}")
    return full


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------- frontmatter ----------------

def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML-lite frontmatter. Returns (fields, body).

    Only simple ``key: value`` lines are parsed; unknown keys are preserved
    verbatim on write by merging (see _merge_frontmatter).

    Handles both canonical multi-line form::

        ---
        id: note_abc
        title: Demo
        ---

        body…

    and legacy compact single-line forms like ``--- id: note_abc ---`` or
    ``--- id: note_abc`` (old clients wrote these; preview used to leak them).
    """
    text = text or ""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    # Compact single-line: --- k: v ---  (or without trailing ---)
    if text.startswith("---") and not text.startswith("---\n") and not text.startswith("---\r"):
        first_nl = text.find("\n")
        first_line = text if first_nl < 0 else text[:first_nl]
        inner = first_line.strip()[3:].strip()
        if inner.endswith("---"):
            inner = inner[:-3].strip()
        # Only treat as frontmatter when it looks like key: value pairs.
        fm: dict[str, str] = {}
        if re.match(r"^[A-Za-z0-9_\-]+\s*:", inner):
            for chunk in re.split(r"\s{2,}|\t|,(?=[A-Za-z0-9_\-]+\s*:)", inner):
                m = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", chunk.strip())
                if m:
                    fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
            if fm:
                rest = "" if first_nl < 0 else text[first_nl + 1:]
                return fm, rest.lstrip("\n")
        return {}, text
    if not (text.startswith("---\n") or text.startswith("---\r")):
        return {}, text
    lines = text.split("\n")
    end = -1
    for i in range(1, min(len(lines), 60)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end < 0:
        return {}, text
    fm = {}
    for line in lines[1:end]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return fm, "\n".join(lines[end + 1:])


def strip_frontmatter(text: str) -> str:
    """Return the body with any frontmatter removed (for preview/word-count)."""
    _, body = split_frontmatter(text or "")
    return body


def _dump_frontmatter(fm: dict, body: str) -> str:
    if not fm:
        return body
    head = "---\n" + "".join(
        f"{k}: {v}\n" if "\n" not in str(v) else f'{k}: "{v}"\n'
        for k, v in fm.items()
    ) + "---\n"
    return head + (body if body.startswith("\n") else "\n" + body)


def _title_of(fm: dict, body: str, path: str) -> str:
    if fm.get("title"):
        return str(fm["title"])[:200]
    m = re.search(r"^#{1,3}\s+(.+)$", body or "", re.MULTILINE)
    if m:
        return m.group(1).strip()[:200]
    return Path(path).stem


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


# ---------------- index helpers ----------------

def note_by_path(path: str):
    return db.query_one("SELECT * FROM notes WHERE path = ?", (normalize(path),))


def note_by_id(note_id: str):
    return db.query_one("SELECT * FROM notes WHERE id = ?", (note_id,))


def _insert_version(note_id: str, version: int, content: str, h: str, actor: str) -> None:
    db.execute(
        "INSERT INTO note_versions (id, note_id, version, content, content_hash, actor, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (db.new_id("nv"), note_id, version, content, h, actor, db.now_ms()),
    )


# ---------------- CRUD ----------------

def create_note(path: str, content: str, actor: str = "user") -> dict:
    rel = normalize(path)
    if not rel.lower().endswith(".md"):
        raise ValueError("notes must end with .md")
    if note_by_path(rel) or resolve(rel).exists():
        raise ValueError(f"note already exists: {rel}")
    content = content or ""
    fm, body = split_frontmatter(content)
    fm.setdefault("id", f"note_{uuid.uuid4().hex[:12]}")
    content = _dump_frontmatter(fm, body)
    h = _hash(content)
    now = db.now_ms()
    atomic_write(resolve(rel), content)
    mtime = int(resolve(rel).stat().st_mtime * 1000)
    db.execute(
        "INSERT INTO notes (id, path, title, content_hash, version, file_mtime, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
        (fm["id"], rel, _title_of(fm, body, rel), h, mtime, now, now),
    )
    _insert_version(fm["id"], 1, content, h, actor)
    db.audit("vault.create", {"path": rel, "id": fm["id"]}, actor=actor)
    return get_note(rel)


def _adopt_external(row, disk: str, mtime: int) -> None:
    """Adopt an on-disk change (human edit from another app) as a new version."""
    fm, body = split_frontmatter(disk)
    fm.setdefault("id", row["id"])
    fm["id"] = row["id"]  # id is stable; never adopt a foreign id
    disk = _dump_frontmatter(fm, body)
    h = _hash(disk)
    if h == row["content_hash"]:
        db.execute("UPDATE notes SET file_mtime = ? WHERE id = ?", (mtime, row["id"]))
        return
    new_v = int(row["version"]) + 1
    if disk != resolve(row["path"]).read_text(encoding="utf-8", errors="replace"):
        atomic_write(resolve(row["path"]), disk)
        mtime = int(resolve(row["path"]).stat().st_mtime * 1000)
    db.execute("UPDATE notes SET content_hash = ?, version = ?, file_mtime = ?,"
               " title = ?, updated_at = ? WHERE id = ?",
               (h, new_v, mtime, _title_of(fm, body, row["path"]), db.now_ms(), row["id"]))
    _insert_version(row["id"], new_v, disk, h, "external")
    db.audit("vault.external_sync", {"path": row["path"], "version": new_v})


def get_note(path: str, record_exec: str = "") -> dict:
    row = note_by_path(path)
    if not row:
        raise KeyError(f"note not found: {path}")
    fp = resolve(row["path"])
    if not fp.exists():
        raise FileNotFoundError(f"note file missing on disk: {row['path']} (run sync)")
    disk = fp.read_text(encoding="utf-8", errors="replace")
    mtime = int(fp.stat().st_mtime * 1000)
    if _hash(disk) != row["content_hash"] or mtime != int(row["file_mtime"] or 0):
        _adopt_external(row, disk, mtime)
        row = note_by_path(row["path"])
        assert row is not None
    if record_exec:
        db.execute(
            "INSERT INTO note_reads (execution_id, note_id, version, content_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(execution_id, note_id) DO UPDATE SET version=excluded.version,"
            " content_hash=excluded.content_hash, created_at=excluded.created_at",
            (record_exec, row["id"], row["version"], row["content_hash"], db.now_ms()),
        )
    return note_detail(row)


def note_detail(row) -> dict:
    fp = resolve(row["path"])
    content = fp.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(content)
    out = parse_wikilinks(content)
    back = backlinks(row["path"])
    broken = [o for o in out if o["broken"]]
    stale = stale_readers(row["id"])
    linked_tasks = db.query(
        "SELECT t.id, t.title, t.status FROM task_notes tn JOIN tasks t ON t.id = tn.task_id"
        " WHERE tn.note_id = ?", (row["id"],))
    return {
        "id": row["id"], "path": row["path"], "title": row["title"],
        "version": row["version"], "content_hash": row["content_hash"],
        "content": content, "frontmatter": fm,
        "word_count": len(re.findall(r"\S+", body)),
        "outlinks": out, "backlinks": back, "broken_links": broken,
        "stale_readers": stale,
        "linked_tasks": [dict(t) for t in linked_tasks],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def update_note(path: str, content: str, actor: str = "user",
                expected_version: int | None = None,
                expected_hash: str | None = None) -> dict:
    row = note_by_path(path)
    if not row:
        raise KeyError(f"note not found: {path}")
    # Reconcile external edits first so conflicts name the true current version.
    fp = resolve(row["path"])
    if fp.exists():
        disk = fp.read_text(encoding="utf-8", errors="replace")
        if _hash(disk) != row["content_hash"]:
            _adopt_external(row, disk, int(fp.stat().st_mtime * 1000))
            row = note_by_path(row["path"])
            assert row is not None
    if expected_version is not None and int(row["version"]) != int(expected_version):
        raise NoteConflict(
            f"stale write: note '{row['path']}' is at version {row['version']},"
            f" you based your edit on version {expected_version}. Re-read and retry.",
            int(row["version"]), row["content_hash"])
    if expected_hash and row["content_hash"] != expected_hash:
        raise NoteConflict(
            f"stale write: note '{row['path']}' hash {row['content_hash'][:12]}…"
            f" != expected {expected_hash[:12]}… (version {row['version']}). Re-read and retry.",
            int(row["version"]), row["content_hash"])
    old_fm, _ = split_frontmatter(
        db.query_one("SELECT content FROM note_versions WHERE note_id = ? AND version = ?",
                      (row["id"], row["version"]))["content"])
    new_fm, new_body = split_frontmatter(content or "")
    merged = dict(old_fm)
    merged.update(new_fm)
    merged["id"] = row["id"]  # stable id survives renames and edits
    content = _dump_frontmatter(merged, new_body)
    h = _hash(content)
    new_v = int(row["version"]) + 1
    atomic_write(fp, content)
    mtime = int(fp.stat().st_mtime * 1000)
    db.execute("UPDATE notes SET content_hash = ?, version = ?, file_mtime = ?,"
               " title = ?, updated_at = ? WHERE id = ?",
               (h, new_v, mtime, _title_of(merged, new_body, row["path"]), db.now_ms(), row["id"]))
    _insert_version(row["id"], new_v, content, h, actor)
    db.audit("vault.update", {"path": row["path"], "version": new_v}, actor=actor)
    return note_detail(note_by_path(row["path"]))


def rename_note(old_path: str, new_path: str, actor: str = "user") -> dict:
    old_rel = normalize(old_path)
    new_rel = normalize(new_path)
    if not new_rel.lower().endswith(".md"):
        raise ValueError("notes must end with .md")
    row = note_by_path(old_rel)
    if not row:
        raise KeyError(f"note not found: {old_rel}")
    if note_by_path(new_rel) or resolve(new_rel).exists():
        raise ValueError(f"note already exists: {new_rel}")
    src, dst = resolve(old_rel), resolve(new_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    mtime = int(dst.stat().st_mtime * 1000)
    db.execute("UPDATE notes SET path = ?, file_mtime = ?, updated_at = ? WHERE id = ?",
               (new_rel, mtime, db.now_ms(), row["id"]))
    db.audit("vault.rename", {"from": old_rel, "to": new_rel, "id": row["id"]}, actor=actor)
    # Rewrite [[old]] references in linking notes as new versions of the rename.
    updated_refs = rewrite_references(old_rel, new_rel, actor=f"rename:{actor}")
    return {"id": row["id"], "from": old_rel, "to": new_rel,
            "references_updated": updated_refs}


def rewrite_references(old_rel: str, new_rel: str, actor: str) -> list[str]:
    """Rewrite wikilinks pointing at old_rel so they point at new_rel."""
    old_targets = _target_aliases(old_rel)
    touched: list[str] = []
    for r in db.query("SELECT * FROM notes WHERE path != ?", (new_rel,)):
        try:
            content = resolve(r["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        def _sub(m: re.Match) -> str:
            tgt, head, alias = m.group(1).strip(), m.group(2), m.group(3)
            if _resolve_target(tgt, r["path"]) in old_targets:
                new_t = new_rel[:-3] if new_rel.lower().endswith(".md") else new_rel
                s = f"[[{new_t}"
                if head:
                    s += f"#{head}"
                if alias:
                    s += f"|{alias}"
                return s + "]]"
            return m.group(0)
        new_content, n = WIKILINK_RE.subn(_sub, content)
        if n and new_content != content:
            update_note(r["path"], new_content, actor=actor)
            touched.append(r["path"])
    return touched


def _target_aliases(rel: str) -> set[str]:
    rel = normalize(rel)
    out = {rel, rel.lower()}
    if rel.lower().endswith(".md"):
        out |= {rel[:-3], rel[:-3].lower()}
    return out


def _resolve_target(target: str, from_path: str) -> str:
    """Resolve a wikilink target to a normalized vault path (best effort)."""
    t = (target or "").strip().replace("\\", "/")
    candidates = [t, t + ".md"]
    sib = str(Path(from_path).parent / t).replace("\\", "/")
    if str(Path(from_path).parent) != ".":
        candidates += [sib, sib + ".md"]
    for c in candidates:
        try:
            n = normalize(c)
        except ValueError:
            continue
        if note_by_path(n):
            return n
    try:
        return normalize(candidates[0])
    except ValueError:
        return candidates[0]


def delete_note(path: str, actor: str = "user") -> dict:
    row = note_by_path(path)
    if not row:
        raise KeyError(f"note not found: {path}")
    fp = resolve(row["path"])
    try:
        if fp.exists():
            fp.unlink()
    except OSError as exc:
        raise ValueError(f"cannot delete: {exc}")
    db.execute("DELETE FROM notes WHERE id = ?", (row["id"],))
    db.execute("DELETE FROM note_reads WHERE note_id = ?", (row["id"],))
    db.audit("vault.delete", {"path": row["path"], "id": row["id"]}, actor=actor)
    return {"ok": True, "id": row["id"], "path": row["path"]}


def make_folder(path: str) -> dict:
    rel = normalize(path)
    d = resolve(rel)
    d.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": rel}


def _is_note_rel(rel: str) -> bool:
    return rel.lower().endswith(".md")


def copy_note(src: str, dst: str, actor: str = "user") -> dict:
    """Duplicate a note to a new path with a FRESH stable id."""
    src_rel, dst_rel = normalize(src), normalize(dst)
    if not _is_note_rel(dst_rel):
        raise ValueError("notes must end with .md")
    row = note_by_path(src_rel)
    if not row:
        raise KeyError(f"note not found: {src_rel}")
    if note_by_path(dst_rel) or resolve(dst_rel).exists():
        raise ValueError(f"note already exists: {dst_rel}")
    content = resolve(src_rel).read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(content)
    fm["id"] = f"note_{uuid.uuid4().hex[:12]}"
    if "title" not in fm:
        fm["title"] = _title_of({}, body, src_rel)
    content = _dump_frontmatter(fm, body)
    h = _hash(content)
    now = db.now_ms()
    atomic_write(resolve(dst_rel), content)
    mtime = int(resolve(dst_rel).stat().st_mtime * 1000)
    db.execute(
        "INSERT INTO notes (id, path, title, content_hash, version, file_mtime, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
        (fm["id"], dst_rel, _title_of(fm, body, dst_rel), h, mtime, now, now),
    )
    _insert_version(fm["id"], 1, content, h, actor)
    db.audit("vault.copy", {"from": src_rel, "to": dst_rel, "id": fm["id"]}, actor=actor)
    return get_note(dst_rel)


def delete_folder(path: str, actor: str = "user") -> dict:
    """Recursively delete a vault folder (notes + subfolders). No trash."""
    import shutil

    rel = normalize(path)
    d = resolve(rel)
    if not d.exists() or not d.is_dir():
        raise KeyError(f"folder not found: {rel}")
    if rel == "" or rel == ".":
        raise ValueError("refusing to delete vault root")
    removed: list[str] = []
    for r in db.query("SELECT * FROM notes WHERE path = ? OR path LIKE ?", (rel, rel + "/%")):
        removed.append(r["path"])
    shutil.rmtree(d, ignore_errors=False)
    for p in removed:
        row = note_by_path(p)
        if row:
            db.execute("DELETE FROM notes WHERE id = ?", (row["id"],))
            db.execute("DELETE FROM note_reads WHERE note_id = ?", (row["id"],))
    db.audit("vault.folder_delete", {"path": rel, "removed": len(removed)}, actor=actor)
    return {"ok": True, "path": rel, "removed": removed}


def move_folder(src: str, dst: str, actor: str = "user") -> dict:
    """Rename / move a whole folder, updating the index + wikilinks."""
    rel_src, rel_dst = normalize(src), normalize(dst)
    s, d = resolve(rel_src), resolve(rel_dst)
    if not s.exists() or not s.is_dir():
        raise KeyError(f"folder not found: {rel_src}")
    if d.exists():
        raise ValueError(f"folder already exists: {rel_dst}")
    if rel_dst == rel_src or rel_dst.startswith(rel_src + "/"):
        raise ValueError("cannot move a folder inside itself")
    d.parent.mkdir(parents=True, exist_ok=True)
    os.replace(s, d)
    moved: list[dict] = []
    for r in db.query("SELECT * FROM notes WHERE path = ? OR path LIKE ?", (rel_src, rel_src + "/%")):
        old_p = r["path"]
        new_p = rel_dst + old_p[len(rel_src):]
        mtime = int(resolve(new_p).stat().st_mtime * 1000) if resolve(new_p).exists() else db.now_ms()
        db.execute("UPDATE notes SET path = ?, file_mtime = ?, updated_at = ? WHERE id = ?",
                   (new_p, mtime, db.now_ms(), r["id"]))
        moved.append({"from": old_p, "to": new_p})
    db.audit("vault.folder_move", {"from": rel_src, "to": rel_dst, "moved": len(moved)}, actor=actor)
    for m in moved:
        try:
            rewrite_references(m["from"], m["to"], actor=f"rename:{actor}")
        except Exception:
            pass
    return {"ok": True, "from": rel_src, "to": rel_dst, "moved": moved}


def copy_folder(src: str, dst: str, actor: str = "user") -> dict:
    """Deep-copy a folder; every copied note gets a fresh stable id."""
    import shutil

    rel_src, rel_dst = normalize(src), normalize(dst)
    s, d = resolve(rel_src), resolve(rel_dst)
    if not s.exists() or not s.is_dir():
        raise KeyError(f"folder not found: {rel_src}")
    if d.exists():
        raise ValueError(f"folder already exists: {rel_dst}")
    if rel_dst == rel_src or rel_dst.startswith(rel_src + "/"):
        raise ValueError("cannot copy a folder inside itself")
    shutil.copytree(s, d)
    copied: list[str] = []
    for f in sorted(d.rglob("*.md")):
        if ".tmp-" in f.name:
            continue
        # d is already resolved; relativize against it (not a fresh
        # vault_root() call — on Windows resolve() may return 8.3 short
        # names that break relative_to against the unresolved root).
        rel = (rel_dst + "/" + f.relative_to(d).as_posix()).strip("/")
        content = f.read_text(encoding="utf-8", errors="replace")
        fm, body = split_frontmatter(content)
        fm["id"] = f"note_{uuid.uuid4().hex[:12]}"
        content2 = _dump_frontmatter(fm, body)
        if content2 != content:
            atomic_write(f, content2)
            content = content2
        h = _hash(content)
        now = db.now_ms()
        mtime = int(f.stat().st_mtime * 1000)
        if note_by_path(rel):
            continue
        db.execute(
            "INSERT INTO notes (id, path, title, content_hash, version, file_mtime, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (fm["id"], rel, _title_of(fm, body, rel), h, mtime, now, now))
        _insert_version(fm["id"], 1, content, h, actor)
        copied.append(rel)
    db.audit("vault.folder_copy", {"from": rel_src, "to": rel_dst, "copied": len(copied)}, actor=actor)
    return {"ok": True, "from": rel_src, "to": rel_dst, "copied": copied}


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def hybrid_note_search(q: str, limit: int = 25, query_vec: list[float] | None = None,
                       doc_vecs: dict[str, list[float]] | None = None) -> list[dict]:
    """Lexical-first semantic-ish search over notes.

    Scores title/path matches higher than body matches, adds token-overlap
    (poor-man's semantics that catches e.g. 'upselling tasks' → notes about
    'upsell playbook' even without shared bigrams), then fuses optional
    embedding cosine scores via reciprocal-rank fusion. Always works
    lexically; ``query_vec``/``doc_vecs`` only boost ranking when present.
    """
    q = (q or "").strip()
    if not q:
        return []
    qtok = _tokens(q)
    qset = set(qtok)
    lex_scored: list[tuple[float, dict]] = []
    for r in _all_note_paths():
        try:
            content = resolve(r["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _, body = split_frontmatter(content)
        title_l = (r["title"] or "").lower()
        path_l = r["path"].lower()
        body_l = body.lower()
        score = 0.0
        ql = q.lower()
        if ql in title_l:
            score += 5.0
        if ql in path_l:
            score += 3.0
        if ql in body_l:
            # earlier + more frequent = better; cap frequency contribution
            score += 2.0 + min(2.0, body_l.count(ql) * 0.4)
        bt = _tokens(f"{r['title']} {body[:4000]}")
        if bt and qset:
            overlap = len(qset & set(bt)) / max(1, len(qset))
            score += overlap * 4.0
        if score <= 0:
            continue
        idx = body_l.find(ql)
        if idx < 0:
            # snippet around first overlapping token
            idx = 0
            for t in qtok:
                j = body_l.find(t)
                if j >= 0:
                    idx = j
                    break
        start = max(0, idx - 80)
        snippet = re.sub(r"\s+", " ", body[start:start + 220]).strip()
        lex_scored.append((score, {"path": r["path"], "title": r["title"], "id": r["id"],
                                  "score": round(score, 3), "snippet": snippet,
                                  "via": ["lexical"]}))
    lex_scored.sort(key=lambda x: (-x[0], x[1]["path"]))
    ranked = [h for _, h in lex_scored[: max(limit * 2, limit)]]
    # Fuse embedding ranking when vectors are available.
    if query_vec and doc_vecs:
        import math

        def cos(a: list[float], b: list[float]) -> float:
            n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
            if not n:
                return 0.0
            return sum(x * y for x, y in zip(a, b)) / n

        vec_ranked = sorted(
            ((cos(query_vec, v), p) for p, v in doc_vecs.items() if v),
            key=lambda x: -x[0])
        rrf: dict[str, dict] = {}
        for rank, h in enumerate(ranked):
            f = rrf.setdefault(h["path"], {**h, "rrf": 0.0, "via": []})
            f["rrf"] += 1.0 / (60 + rank)
            f["via"].append("lexical")
        for rank, (s, p) in enumerate(vec_ranked):
            if s <= 0.05:
                continue
            base = next((h for h in ranked if h["path"] == p), None)
            if base is None:
                try:
                    row = note_by_path(p)
                    if not row:
                        continue
                    content = resolve(p).read_text(encoding="utf-8", errors="replace")
                    _, body = split_frontmatter(content)
                    base = {"path": p, "title": row["title"], "id": row["id"],
                            "score": 0.0, "snippet": re.sub(r"\s+", " ", body[:220]),
                            "via": []}
                except OSError:
                    continue
            f = rrf.setdefault(p, {**base, "rrf": 0.0, "via": []})
            f["rrf"] += 1.0 / (60 + rank)
            if "vector" not in f["via"]:
                f["via"].append("vector")
        fused = sorted(rrf.values(), key=lambda x: -x["rrf"])
        out = []
        for h in fused[:limit]:
            out.append({"path": h["path"], "title": h.get("title", ""),
                        "id": h.get("id"), "score": round(float(h["rrf"]), 4),
                        "snippet": h.get("snippet", ""), "via": h.get("via", [])})
        return out
    return [{**h, "via": h.get("via", ["lexical"])} for h in ranked[:limit]]


# ---------------- links ----------------

def parse_wikilinks(content: str) -> list[dict]:
    out = []
    for m in WIKILINK_RE.finditer(content or ""):
        tgt = m.group(1).strip()
        out.append({"target": tgt, "heading": m.group(2), "alias": m.group(3),
                    "broken": _resolve_target(tgt, "") is not None and
                    note_by_path(_resolve_target(tgt, "")) is None})
    return out


def _all_note_paths() -> list:
    return db.query("SELECT * FROM notes")


def backlinks(path: str) -> list[dict]:
    """Notes linking TO path (with alias/heading context)."""
    rel = normalize(path)
    aliases = _target_aliases(rel)
    out = []
    for r in _all_note_paths():
        if r["path"] == rel:
            continue
        try:
            content = resolve(r["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = []
        for m in WIKILINK_RE.finditer(content):
            if _resolve_target(m.group(1).strip(), r["path"]) in aliases:
                hits.append({"heading": m.group(2), "alias": m.group(3)})
        if hits:
            out.append({"path": r["path"], "title": r["title"], "matches": hits})
    return out


def broken_links() -> list[dict]:
    out = []
    for r in _all_note_paths():
        try:
            content = resolve(r["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in WIKILINK_RE.finditer(content):
            tgt = m.group(1).strip()
            if note_by_path(_resolve_target(tgt, r["path"])) is None:
                out.append({"from": r["path"], "target": tgt})
    return out


def complete(prefix: str, limit: int = 20) -> list[dict]:
    """Wikilink autocomplete over titles and paths."""
    p = (prefix or "").strip().lower().lstrip("[")
    rows = _all_note_paths()
    scored = []
    for r in rows:
        hay = f"{r['title']}\n{r['path']}".lower()
        stem = Path(r["path"]).stem.lower()
        if not p:
            score = 0
        elif stem.startswith(p) or r["title"].lower().startswith(p) or r["path"].lower().startswith(p):
            score = 3
        elif p in stem or p in hay:
            score = 1
        else:
            continue
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["path"]))
    return [{"path": r["path"], "title": r["title"],
             "insert": (r["path"][:-3] if r["path"].lower().endswith(".md") else r["path"])}
            for _, r in scored[:limit]]


def search_notes(q: str, limit: int = 50) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    ql = q.lower()
    out = []
    for r in _all_note_paths():
        try:
            content = resolve(r["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _, body = split_frontmatter(content)
        score = 0
        if ql in r["title"].lower():
            score = 3
        if ql in r["path"].lower():
            score = max(score, 2)
        idx = body.lower().find(ql)
        if idx >= 0:
            score = max(score, 1)
            start = max(0, idx - 60)
            snippet = body[start:start + 160].replace("\n", " ")
        else:
            snippet = "" if score else ""
            if not score:
                continue
        out.append({"path": r["path"], "title": r["title"], "score": score, "snippet": snippet})
    out.sort(key=lambda x: (-x["score"], x["path"]))
    return out[:limit]


def list_tree() -> list[dict]:
    """Nested folder tree of the vault."""
    root = vault_root()
    tmp_mark = ".tmp-"

    def _walk(d: Path) -> list[dict]:
        entries = []
        try:
            kids = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return []
        for k in kids:
            if tmp_mark in k.name and k.is_file():
                continue
            rel = k.relative_to(root).as_posix()
            if k.is_dir():
                entries.append({"name": k.name, "path": rel, "type": "folder",
                                "children": _walk(k)})
            elif k.suffix.lower() == ".md":
                row = note_by_path(rel)
                entries.append({"name": k.name, "path": rel, "type": "note",
                                "title": row["title"] if row else k.stem,
                                "id": row["id"] if row else None})
        return entries

    return _walk(root)


# ---------------- versions ----------------

def list_versions(path: str) -> list[dict]:
    row = note_by_path(path)
    if not row:
        raise KeyError(f"note not found: {path}")
    rows = db.query("SELECT version, content_hash, actor, created_at, LENGTH(content) AS size"
                    " FROM note_versions WHERE note_id = ? ORDER BY version DESC LIMIT 50",
                    (row["id"],))
    return [dict(r) for r in rows]


def get_version_content(path: str, version: int) -> str:
    row = note_by_path(path)
    if not row:
        raise KeyError(f"note not found: {path}")
    v = db.query_one("SELECT content FROM note_versions WHERE note_id = ? AND version = ?",
                     (row["id"], version))
    if not v:
        raise KeyError(f"version {version} not found")
    return v["content"]


def rollback(path: str, version: int, actor: str = "user") -> dict:
    content = get_version_content(path, version)
    row = note_by_path(path)
    assert row is not None
    return update_note(path, content, actor=f"rollback:{actor}",
                       expected_version=int(row["version"]),
                       expected_hash=row["content_hash"])


# ---------------- stale reads ----------------

def stale_readers(note_id: str) -> list[dict]:
    row = note_by_id(note_id)
    if not row:
        return []
    rows = db.query("SELECT execution_id, version, content_hash, created_at FROM note_reads"
                    " WHERE note_id = ?", (note_id,))
    return [{"execution_id": r["execution_id"], "read_version": r["version"],
             "current_version": row["version"],
             "stale": int(r["version"]) != int(row["version"])
             or r["content_hash"] != row["content_hash"]}
            for r in rows]


# ---------------- external sync ----------------

def sync_external() -> dict:
    """Reconcile disk vs index: adopt external edits, import new files,
    report missing files and clean interrupted-save tmp leftovers."""
    root = vault_root()
    report: dict = {"adopted": [], "imported": [], "missing": [], "tmp_cleaned": []}
    # Clean interrupted-save leftovers (crash during atomic write).
    for tmp in root.rglob("*.tmp-*"):
        try:
            if tmp.is_file():
                tmp.unlink()
                report["tmp_cleaned"].append(tmp.relative_to(root).as_posix())
        except OSError:
            pass
    for dot in root.rglob(".tmp-*"):
        try:
            if dot.is_file():
                dot.unlink()
                report["tmp_cleaned"].append(dot.relative_to(root).as_posix())
        except OSError:
            pass
    on_disk: set[str] = set()
    for f in root.rglob("*.md"):
        if ".tmp-" in f.name:
            continue
        rel = f.relative_to(root).as_posix()
        on_disk.add(rel)
    for rel in sorted(on_disk):
        row = note_by_path(rel)
        content = (root / rel).read_text(encoding="utf-8", errors="replace")
        mtime = int((root / rel).stat().st_mtime * 1000)
        if row is None:
            fm, body = split_frontmatter(content)
            fm.setdefault("id", f"note_{uuid.uuid4().hex[:12]}")
            content2 = _dump_frontmatter(fm, body)
            if content2 != content:
                atomic_write(root / rel, content2)
                mtime = int((root / rel).stat().st_mtime * 1000)
            h = _hash(content2)
            now = db.now_ms()
            db.execute(
                "INSERT INTO notes (id, path, title, content_hash, version, file_mtime,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                (fm["id"], rel, _title_of(fm, body, rel), h, mtime, now, now))
            _insert_version(fm["id"], 1, content2, h, "external-sync")
            report["imported"].append(rel)
        elif _hash(content) != row["content_hash"]:
            _adopt_external(row, content, mtime)
            report["adopted"].append(rel)
    for r in _all_note_paths():
        if r["path"] not in on_disk:
            report["missing"].append(r["path"])
    db.audit("vault.sync", {k: len(v) for k, v in report.items()})
    return report


def word_count(text: str) -> int:
    _, body = split_frontmatter(text or "")
    return len(re.findall(r"\S+", body))


def _now_ms() -> int:
    return int(time.time() * 1000)
