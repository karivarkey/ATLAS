"""Web tools against local fake servers (+ live SearXNG when up)."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import config
from app.services import webtools, workspace as ws

DDG_HTML = """
<html><body>
<table><tr><td>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fqwen&rut=x">Qwen Guide</a>
</td><td class="result-snippet">A guide to Qwen models</td></tr>
<tr><td>
<a class="result__a" href="https://example.com/direct">Direct Hit</a>
</td><td class="result-snippet">Direct result</td></tr>
</table></body></html>
"""

PAGE_HTML = """<html><head><title>T</title></head><body>
<script>var evil = 1;</script>
<h1>Hello World</h1><p>This is a test page.</p>
</body></html>
"""


class FakeWeb(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGE_HTML if self.path.startswith("/page") else DDG_HTML
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _srv():
    server = HTTPServer(("127.0.0.1", 0), FakeWeb)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


class FakeSearx(BaseHTTPRequestHandler):
    PORT = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/search"):
            payload = {"results": [
                {"title": "Page One", "url": f"http://127.0.0.1:{self.PORT}/p1",
                 "content": "snippet one"},
                {"title": "Page Two", "url": f"http://127.0.0.1:{self.PORT}/p2",
                 "content": "snippet two"},
            ]}
            raw = json.dumps(payload).encode()
        elif self.path.startswith("/p1"):
            raw = b"<html><body><h1>Alpha Document</h1><p>deep content alpha</p></body></html>"
        else:
            raw = b"<html><body><h1>Beta Document</h1><p>deep content beta</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/json" if self.path.startswith("/search") else "text/html")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_web_research_combines_sources(tmp_path, monkeypatch):
    import json as _json

    async def main():
        server = HTTPServer(("127.0.0.1", 0), FakeSearx)
        port = server.server_address[1]
        FakeSearx.PORT = port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        monkeypatch.setattr(config, "SEARXNG_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(config, "ALLOW_NETWORK", True)
        r = await webtools.web_research("test topic", 2)
        server.shutdown()
        assert r["ok"], r
        assert "SOURCE: http://127.0.0.1" in r["output"]
        assert "deep content alpha" in r["output"]
        assert "deep content beta" in r["output"]

    asyncio.run(main())


def test_ddg_parse_and_fetch(tmp_path, monkeypatch):
    async def main():
        server, port = _srv()
        # Point the search at the fake DDG by monkeypatching httpx target:
        # web_search hardcodes the DDG URL, so test the parser + fetch directly.
        from app.services.webtools import _DDGParser, web_fetch

        p = _DDGParser()
        p.feed(DDG_HTML)
        assert len(p.results) == 2
        assert p.results[0]["url"] == "https://example.com/qwen"
        assert "Qwen" in p.results[0]["title"]
        monkeypatch.setattr(config, "ALLOW_NETWORK", True)
        r = await web_fetch(f"http://127.0.0.1:{port}/page")
        server.shutdown()
        assert r["ok"], r
        assert "Hello World" in r["output"]
        assert "evil" not in r["output"]

    asyncio.run(main())


def test_web_tools_in_schema_and_policy(tmp_path):
    names = [t["name"] for t in ws.TOOL_SCHEMAS]
    assert "web_search" in names and "web_fetch" in names
    assert ws.summarize_tool("web_search", {"query": "qwen"}) .startswith("Search")
    assert ws.summarize_tool("web_fetch", {"url": "https://x"}).startswith("Fetch")
    need, _ = ws.needs_approval("web_search", {"query": "x"}, auto_approve=True)
    assert need is False


def test_web_search_live_smoke():
    """Real DuckDuckGo query. Network-dependent: fails open with a clear
    message instead of failing the suite when offline/blocked."""
    async def main():
        if not config.ALLOW_NETWORK:
            return
        r = await webtools.web_search("qwen3 model", 3)
        if r["ok"] and "no results" not in r["output"]:
            assert "http" in r["output"], r["output"][:300]
            print("LIVE SEARCH OK:", r["output"][:300])
        else:
            print("LIVE SEARCH unavailable (offline/blocked):", r["output"][:200])

    asyncio.run(main())
