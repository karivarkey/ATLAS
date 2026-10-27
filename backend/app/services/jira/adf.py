"""Atlassian Document Format (ADF) <-> Markdown conversion.

ADF docs: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
Only safe node types are rendered; unknown nodes degrade to their text content.
Outgoing Markdown supports paragraphs, headings, lists, code, quotes, links,
bold/italic/code marks. HTML is never emitted.
"""
from __future__ import annotations

import re


def adf_to_markdown(doc: object) -> str:
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc
    if not isinstance(doc, dict):
        return ""
    blocks: list[str] = []
    for node in doc.get("content", []) or []:
        blocks.append(_block(node, 0))
    return "\n\n".join(b for b in blocks if b).strip()


def _text(node: dict) -> str:
    out: list[str] = []
    for ch in node.get("content", []) or []:
        if not isinstance(ch, dict):
            continue
        if ch.get("type") == "text":
            t = ch.get("text", "")
            marks = {m.get("type") for m in ch.get("marks", []) or []}
            if "code" in marks:
                t = f"`{t}`"
            else:
                if "strong" in marks:
                    t = f"**{t}**"
                if "em" in marks:
                    t = f"*{t}*"
                for m in ch.get("marks", []) or []:
                    if m.get("type") == "link":
                        href = (m.get("attrs") or {}).get("href", "")
                        t = f"[{t}]({href})"
            out.append(t)
        elif ch.get("type") in ("hardBreak",):
            out.append("\n")
        elif ch.get("type") == "mention":
            attrs = ch.get("attrs") or {}
            out.append("@" + str(attrs.get("text") or attrs.get("id", "user")))
        elif ch.get("type") == "emoji":
            out.append(str((ch.get("attrs") or {}).get("text", "")))
        else:
            out.append(_inline_fallback(ch))
    return "".join(out)


def _inline_fallback(node: dict) -> str:
    parts: list[str] = []
    for ch in node.get("content", []) or []:
        if isinstance(ch, dict) and ch.get("type") == "text":
            parts.append(ch.get("text", ""))
        elif isinstance(ch, dict):
            parts.append(_inline_fallback(ch))
    return "".join(parts)


def _block(node: dict, depth: int) -> str:
    t = node.get("type", "")
    if t == "paragraph":
        return _text(node)
    if t == "heading":
        level = min(max(int((node.get("attrs") or {}).get("level", 1)), 1), 6)
        return "#" * level + " " + _text(node)
    if t == "bulletList":
        lines = [f"{'  ' * depth}- {_block(i, depth + 1)}" for i in node.get("content", []) or []]
        return "\n".join(lines)
    if t == "orderedList":
        lines = [f"{'  ' * depth}{n}. {_block(i, depth + 1)}"
                 for n, i in enumerate(node.get("content", []) or [], 1)]
        return "\n".join(lines)
    if t == "listItem":
        parts = [_block(c, depth) for c in node.get("content", []) or []]
        return "\n".join(p for p in parts if p)
    if t == "codeBlock":
        lang = (node.get("attrs") or {}).get("language", "")
        return f"```{lang}\n{_text(node)}\n```"
    if t == "blockquote":
        inner = "\n".join(_block(c, depth) for c in node.get("content", []) or [])
        return "\n".join("> " + ln for ln in inner.splitlines())
    if t == "rule":
        return "---"
    if t == "table":
        rows = []
        for tr in node.get("content", []) or []:
            cells = []
            for cell in tr.get("content", []) or []:
                cells.append(" ".join(_block(p, depth) for p in cell.get("content", []) or []))
            rows.append("| " + " | ".join(cells) + " |")
        return "\n".join(rows)
    if t == "mediaGroup" or t == "mediaSingle":
        names = []
        for m in node.get("content", []) or []:
            attrs = m.get("attrs") or {}
            if attrs.get("alt") or attrs.get("id"):
                names.append(f"[attachment: {attrs.get('alt') or attrs.get('id')}]")
        return "\n".join(names)
    if t in ("panel", "expand", "blockquote"):
        return "\n".join(_block(c, depth) for c in node.get("content", []) or [])
    if t in ("media", "inlineCard", "blockCard"):
        attrs = node.get("attrs") or {}
        return f"[{t}: {attrs.get('url') or attrs.get('id') or ''}]".strip()
    # Fallback: recurse into children so customField content is not lost.
    kids = [_block(c, depth) for c in node.get("content", []) or []]
    kids = [k for k in kids if k]
    if kids:
        return "\n".join(kids)
    return _text(node) if node.get("content") else ""


def markdown_to_adf(md: str) -> dict:
    """Convert a Markdown subset to an ADF doc (used for create/update/description)."""
    blocks: list[dict] = []
    lines = (md or "").splitlines()
    i = 0
    list_buf: list[dict] = []
    list_ordered = False

    def flush_list() -> None:
        nonlocal list_buf, list_ordered
        if list_buf:
            blocks.append({"type": "orderedList" if list_ordered else "bulletList",
                           "content": list_buf})
            list_buf = []

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush_list()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_list()
            blocks.append({"type": "heading",
                           "attrs": {"level": len(m.group(1))},
                           "content": _para(m.group(2))})
            i += 1
            continue
        if s.startswith("```"):
            flush_list()
            lang = s[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            node: dict = {"type": "codeBlock", "content": [{"type": "text",
                          "text": "\n".join(code_lines)}]}
            if lang:
                node["attrs"] = {"language": lang}
            blocks.append(node)
            continue
        if s.startswith("> "):
            flush_list()
            q: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append({"type": "blockquote", "content": [
                {"type": "paragraph", "content": _para(" ".join(q))}]})
            continue
        bm = re.match(r"^[-*]\s+(.*)$", s)
        om = re.match(r"^\d+[.)]\s+(.*)$", s)
        if bm or om:
            ordered = bool(om)
            if list_buf and ordered != list_ordered:
                flush_list()
            list_ordered = ordered
            list_buf.append({"type": "listItem", "content": [
                {"type": "paragraph",
                 "content": _para((om.group(1) if om else bm.group(1))) or [{"type": "text", "text": ""}]}]})
            i += 1
            continue
        if s in ("---", "***"):
            flush_list()
            blocks.append({"type": "rule"})
            i += 1
            continue
        flush_list()
        para: list[str] = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|```|>\s|[-*]\s+|\d+[.)]\s+|---$|\*\*\*$)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        blocks.append({"type": "paragraph",
                       "content": _para(" ".join(para)) or [{"type": "text", "text": ""}]})
    flush_list()
    if not blocks:
        blocks = [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]
    return {"type": "doc", "version": 1, "content": blocks}


def _para(text: str) -> list[dict]:
    """Inline markdown -> ADF text nodes with marks (bold/italic/code/links)."""
    out: list[dict] = []
    pattern = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))")
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            out.append({"type": "text", "text": text[pos:m.start()]})
        tok = m.group(0)
        if tok.startswith("`"):
            out.append({"type": "text", "text": tok[1:-1], "marks": [{"type": "code"}]})
        elif tok.startswith("**"):
            out.append({"type": "text", "text": tok[2:-2], "marks": [{"type": "strong"}]})
        elif tok.startswith("*"):
            out.append({"type": "text", "text": tok[1:-1], "marks": [{"type": "em"}]})
        else:
            lm = re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok)
            if lm:
                out.append({"type": "text", "text": lm.group(1),
                            "marks": [{"type": "link", "attrs": {"href": lm.group(2)}}]})
        pos = m.end()
    if pos < len(text):
        out.append({"type": "text", "text": text[pos:]})
    return [n for n in out if n.get("text")]
