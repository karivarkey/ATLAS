"""Vault acceptance: CRUD, folders, rename/link-rewrite, external sync,
traversal guard, atomicity, versions/rollback, frontmatter preservation."""
import os

import pytest
from fastapi.testclient import TestClient

from app import db
from app.services import vault as v


@pytest.fixture()
def tmp_vault(tmp_path, monkeypatch):
    db.reset_for_tests(tmp_path)
    monkeypatch.setenv("ATLAS_VAULT_DIR", str(tmp_path / "vault"))
    yield tmp_path / "vault"


@pytest.fixture()
def client(tmp_vault):
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_crud_nested_folders(client):
    r = client.post("/api/v1/notes", json={"path": "proj/a/b/note.md",
                                           "content": "# Hello\nWorld here"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Hello"
    assert body["frontmatter"]["id"].startswith("note_")
    assert body["version"] == 1
    assert body["word_count"] >= 2
    nid = body["id"]

    r = client.get("/api/v1/notes", params={"path": "proj/a/b/note.md"})
    assert r.status_code == 200
    assert r.json()["id"] == nid

    t = client.get("/api/v1/notes/tree")
    assert t.status_code == 200
    names = str(t.json())
    assert "proj" in names and "note.md" in names


def test_rename_preserves_id_and_rewrites_links(client):
    a = client.post("/api/v1/notes", json={"path": "A.md", "content": "# Alpha\nbody"}).json()
    client.post("/api/v1/notes", json={"path": "B.md",
                                       "content": "see [[A]] and [[A#sec|alias]]"}).json()
    r = client.post("/api/v1/notes/move",
                    json={"old_path": "A.md", "new_path": "dir/C.md"})
    assert r.status_code == 200, r.text
    assert r.json()["references_updated"] == ["B.md"]
    c = client.get("/api/v1/notes", params={"path": "dir/C.md"}).json()
    assert c["id"] == a["id"], "stable note id must survive rename"
    b = client.get("/api/v1/notes", params={"path": "B.md"}).json()
    assert "[[dir/C]]" in b["content"]
    assert "[[dir/C#sec|alias]]" in b["content"]
    assert "[[A" not in b["content"]
    vers = client.get("/api/v1/notes/versions", params={"path": "B.md"}).json()["versions"]
    assert len(vers) == 2, "reference rewrite must be a new version"


def test_backlinks_outlinks_broken(client):
    client.post("/api/v1/notes", json={"path": "Hub.md", "content": "# Hub"})
    client.post("/api/v1/notes", json={"path": "Leaf.md",
                                       "content": "up [[Hub]] down [[Ghost]]"})
    leaf = client.get("/api/v1/notes", params={"path": "Leaf.md"}).json()
    assert any(o["target"] == "Hub" and not o["broken"] for o in leaf["outlinks"])
    assert any(o["target"] == "Ghost" and o["broken"] for o in leaf["outlinks"])
    assert leaf["broken_links"] and leaf["broken_links"][0]["target"] == "Ghost"
    hub = client.get("/api/v1/notes", params={"path": "Hub.md"}).json()
    assert any(b["path"] == "Leaf.md" for b in hub["backlinks"])
    br = client.get("/api/v1/notes/broken").json()["broken"]
    assert any(x["target"] == "Ghost" for x in br)


def test_traversal_blocked(client):
    for bad in ["../evil.md", "..\\evil.md", "/abs.md", "C:/x.md", "a/../../b.md", ""]:
        r = client.post("/api/v1/notes", json={"path": bad, "content": "x"})
        assert r.status_code == 400, (bad, r.text)
    r = client.get("/api/v1/notes", params={"path": "../evil.md"})
    assert r.status_code in (400, 404)


def test_stale_write_conflict(client):
    n = client.post("/api/v1/notes", json={"path": "S.md", "content": "v1"}).json()
    ok = client.put("/api/v1/notes", params={"path": "S.md"},
                    json={"content": "v2", "expected_version": 1,
                          "expected_hash": n["content_hash"]})
    assert ok.status_code == 200
    stale = client.put("/api/v1/notes", params={"path": "S.md"},
                       json={"content": "v3-stale", "expected_version": 1,
                             "expected_hash": n["content_hash"]})
    assert stale.status_code == 409, stale.text
    body = stale.json()
    assert body["detail"]["current_version"] == 2
    cur = client.get("/api/v1/notes", params={"path": "S.md"}).json()
    assert "v2" in cur["content"] and "v3-stale" not in cur["content"]


def test_external_edit_detected_and_synced(client, tmp_vault):
    n = client.post("/api/v1/notes", json={"path": "E.md", "content": "# T\none"}).json()
    assert n["version"] == 1
    (tmp_vault / "E.md").write_text("---\nid: " + n["id"] + "\n---\n\n# T\none TWO",
                                    encoding="utf-8")
    got = client.get("/api/v1/notes", params={"path": "E.md"}).json()
    assert got["version"] == 2, "external edit must be adopted on read"
    assert "TWO" in got["content"]
    vers = client.get("/api/v1/notes/versions", params={"path": "E.md"}).json()["versions"]
    assert any(x["actor"] == "external" for x in vers)


def test_interrupted_save_atomicity_and_cleanup(client, tmp_vault, monkeypatch):
    n = client.post("/api/v1/notes", json={"path": "W.md", "content": "original"}).json()
    import app.services.vault as vv

    real_replace = os.replace

    def _boom(*a, **k):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        vv.atomic_write(tmp_vault / "W.md", "partial-new-content")
    monkeypatch.setattr(os, "replace", real_replace)
    assert (tmp_vault / "W.md").read_text(encoding="utf-8") == \
        client.get("/api/v1/notes/versions/content",
                   params={"path": "W.md", "version": 1}).json()["content"]
    assert client.get("/api/v1/notes", params={"path": "W.md"}).json()["version"] == 1
    # leftover tmp from a crashed write is cleaned by sync
    (tmp_vault / ".W.md.tmp-deadbeef").write_text("junk", encoding="utf-8")
    rep = client.post("/api/v1/notes/sync").json()
    assert rep["tmp_cleaned"], rep
    assert not (tmp_vault / ".W.md.tmp-deadbeef").exists()


def test_version_rollback_as_new_and_frontmatter_keys(client):
    client.post("/api/v1/notes",
                json={"path": "F.md",
                      "content": "---\ncustom: keepme\ntags: [a]\n---\n\nv1 body"})
    d1 = client.get("/api/v1/notes", params={"path": "F.md"}).json()
    assert d1["frontmatter"]["custom"] == "keepme"
    client.put("/api/v1/notes", params={"path": "F.md"},
               json={"content": "v2 body changed"})
    d2 = client.get("/api/v1/notes", params={"path": "F.md"}).json()
    assert d2["version"] == 2
    assert d2["frontmatter"]["custom"] == "keepme", "unknown keys must survive edits"
    assert d2["frontmatter"]["id"] == d1["frontmatter"]["id"]
    rb = client.post("/api/v1/notes/rollback",
                     json={"path": "F.md", "version": 1}).json()
    assert rb["version"] == 3, "rollback lands as a NEW version"
    assert "v1 body" in rb["content"]
    assert rb["frontmatter"]["custom"] == "keepme"


def test_autocomplete_and_search(client):
    client.post("/api/v1/notes", json={"path": "recipes/Pancakes.md",
                                       "content": "# Fluffy Pancakes\nflour eggs"})
    client.post("/api/v1/notes", json={"path": "recipes/Pasta.md", "content": "# Pasta\n Yum"})
    s = client.get("/api/v1/notes/suggest", params={"prefix": "panc"}).json()["suggestions"]
    assert s and s[0]["path"] == "recipes/Pancakes.md"
    q = client.get("/api/v1/notes/search", params={"q": "flour"}).json()["results"]
    assert any(r["path"] == "recipes/Pancakes.md" for r in q)
