"""Read-only web tools: web_search (DuckDuckGo, keyless) and web_fetch.

Results are untrusted third-party data: the agent prompt already instructs
the model to treat them as such. No secrets are ever sent (query text only).
Controlled by ATLAS_ALLOW_NETWORK (default 1).
"""
from __future__ import annotations

import html as _html
import logging
import re
import urllib.parse
from html.parser import HTMLParser

import httpx

from .. import config

log = logging.getLogger("atlas.web")

WEB_TOOL_SCHEMAS = [
    {
        "name": "web_search",
        "description": "Search the web (DuckDuckGo, no key needed). Returns titles, URLs, snippets.",
        "parameters": {"query": "search query", "count": "max results, default 5"},
    },
    {
        "name": "web_fetch",
        "description": "Fetch a page URL and return its readable text (truncated).",
        "parameters": {"url": "https page url"},
    },
]

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Atlas/1.0"}


class _DDGParser(HTMLParser):
    """Collect (title, url, snippet) triples from DDG html endpoint."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._in_a = False
        self._in_snippet = False
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._cur = {"title": "", "url": _clean_ddg_url(a.get("href", ""))}
            self._in_a = True
            self._buf = ""
        elif tag == "td" and "result-snippet" in cls:
            self._in_snippet = True
            self._buf = ""

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self._in_a = False
            if self._cur is not None:
                self._cur["title"] = _html.unescape(self._buf.strip())
        elif tag == "td" and self._in_snippet:
            self._in_snippet = False
            if self._cur is not None:
                self._cur["snippet"] = _html.unescape(self._buf.strip())
                if self._cur.get("url"):
                    self.results.append(self._cur)
                self._cur = None
        elif tag in ("div", "table") and self._cur is not None and not self._in_snippet:
            pass

    def handle_data(self, data):
        if self._in_a or self._in_snippet:
            self._buf += data


def _clean_ddg_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//duckduckgo.com/l/"):
        q = urllib.parse.urlparse("https:" + href).query
        real = urllib.parse.parse_qs(q).get("uddg", [""])[0]
        return real
    return href if href.startswith("http") else ""


async def web_search(query: str, count: int = 5) -> dict:
    if not config.ALLOW_NETWORK:
        return {"ok": False, "output": "network access is disabled (ATLAS_ALLOW_NETWORK=0)"}
    query = (query or "").strip()[:300]
    if not query:
        return {"ok": False, "output": "empty query"}
    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA, follow_redirects=True) as client:
            resp = await client.get("https://html.duckduckgo.com/html/",
                                    params={"q": query})
            resp.raise_for_status()
        parser = _DDGParser()
        parser.feed(resp.text)
        items = [r for r in parser.results if r.get("url")][: max(1, min(count, 10))]
        # Fallback: regex scrape if markup changed.
        if not items:
            items = _regex_fallback(resp.text)[: max(1, min(count, 10))]
        if not items:
            return {"ok": True, "output": "no results"}
        lines = [f"{i + 1}. {r['title']}\n   {r['url']}\n   {r.get('snippet', '')[:300]}"
                 for i, r in enumerate(items)]
        return {"ok": True, "output": "\n".join(lines)}
    except Exception as exc:
        return {"ok": False, "output": f"search failed: {type(exc).__name__}: {exc}"[:1000]}


def _regex_fallback(html_text: str) -> list[dict]:
    out = []
    for m in re.finditer(r'result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.DOTALL):
        url = _clean_ddg_url(_html.unescape(m.group(1)))
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url and title:
            out.append({"title": _html.unescape(title)[:200], "url": url, "snippet": ""})
    return out


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "nav", "footer"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "nav", "footer"):
            self._skip = False
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "tr"):
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self._chunks.append(data.strip() + " ")

    def text(self) -> str:
        collapsed = re.sub(r"[ \t]+", " ", "".join(self._chunks))
        return re.sub(r"\n\s*\n+", "\n\n", collapsed).strip()


async def web_fetch(url: str) -> dict:
    if not config.ALLOW_NETWORK:
        return {"ok": False, "output": "network access is disabled (ATLAS_ALLOW_NETWORK=0)"}
    url = (url or "").strip()[:2000]
    if not re.match(r"https?://", url):
        return {"ok": False, "output": "only http(s) URLs are allowed"}
    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA, follow_redirects=True,
                                     max_redirects=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = await resp.aread()
        if len(body) > 1_000_000:
            return {"ok": False, "output": "page too large (>1MB), refusing to fetch"}
        ctype = resp.headers.get("content-type", "")
        if "html" in ctype:
            parser = _TextParser()
            parser.feed(body.decode(resp.charset_encoding or "utf-8", errors="replace"))
            text = parser.text()
        else:
            text = body.decode("utf-8", errors="replace")
        if len(text) > config.MAX_TOOL_OUTPUT_CHARS:
            text = text[:config.MAX_TOOL_OUTPUT_CHARS] + "\n…[truncated]"
        return {"ok": True, "output": f"[{resp.status_code} {url}]\n{text or '(empty page)'}"}
    except Exception as exc:
        return {"ok": False, "output": f"fetch failed: {type(exc).__name__}: {exc}"[:1000]}


async def run_web_tool(name: str, args: dict) -> dict:
    if name == "web_search":
        try:
            count = int(args.get("count", 5))
        except Exception:
            count = 5
        return await web_search(str(args.get("query", "")), count)
    if name == "web_fetch":
        return await web_fetch(str(args.get("url", "")))
    return {"ok": False, "output": f"unknown web tool: {name}"}
