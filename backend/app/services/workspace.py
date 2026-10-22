"""Workspace manager: sandboxed per-conversation project directories + tools.

Every conversation gets ``workspaces/<conversation_id>/`` as its cwd.
All relative paths resolve inside it; absolute paths and ``..`` escapes
are rejected. Destructive operations outside the sandbox always require
explicit approval.
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

from .. import config
from . import webtools

TOOL_SCHEMAS = [
    {
        "name": "fs_list",
        "description": "List files and directories under a relative path.",
        "parameters": {"path": "relative directory to list (default '.')"},
    },
    {
        "name": "fs_read",
        "description": "Read a file's content (truncated at 20000 chars).",
        "parameters": {"path": "relative file path"},
    },
    {
        "name": "fs_write",
        "description": "Create or overwrite a file with content.",
        "parameters": {"path": "relative file path", "content": "full file content"},
    },
    {
        "name": "fs_edit",
        "description": "Replace old_string with new_string in a file (must match exactly once).",
        "parameters": {"path": "relative file path", "old_string": "exact text",
                       "new_string": "replacement text"},
    },
    {
        "name": "fs_glob",
        "description": "Find files by glob pattern, e.g. '**/*.py'.",
        "parameters": {"pattern": "glob pattern"},
    },
    {
        "name": "fs_grep",
        "description": "Search file contents with regex/literal.",
        "parameters": {"pattern": "regex pattern", "include": "glob filter e.g. '*.py'"},
    },
    {
        "name": "shell_exec",
        "description": "Run a shell command in the workspace. Returns exit code + output.",
        "parameters": {"command": "shell command"},
    },
    {
        "name": "git_clone",
        "description": "Clone a repository URL into the workspace.",
        "parameters": {"url": "https repository url", "dir": "destination dir (optional)"},
    },
    {
        "name": "git_status",
        "description": "Show git status and diff stat for the workspace.",
        "parameters": {},
    },
    {
        "name": "ask_user",
        "description": "Ask the user a question when blocked. Execution pauses until answered.",
        "parameters": {"question": "the question for the user"},
    },
    {
        "name": "search_memory",
        "description": "Hybrid search across all authorized chats + consolidated memory. Returns evidence with sources.",
        "parameters": {"query": "what to look for", "limit": "max results, default 8"},
    },
    {
        "name": "search_chat_history",
        "description": "Exact/lexical search over raw chat history.",
        "parameters": {"query": "literal text", "limit": "max results, default 10", "conversation_id": "optional conversation id"},
    },
    {
        "name": "get_source_message",
        "description": "Fetch one original message by id with its conversation.",
        "parameters": {"message_id": "message id from search results"},
    },
    {
        "name": "get_conversation_context",
        "description": "Read recent messages of another authorized conversation.",
        "parameters": {"conversation_id": "conversation id"},
    },
    {
        "name": "get_memory_record",
        "description": "Read one consolidated memory record by id.",
        "parameters": {"memory_id": "record id"},
    },
] + webtools.WEB_TOOL_SCHEMAS

DESTRUCTIVE_PATTERNS = re.compile(
    r"(rm\s+-rf|rmdir\s+/s|del\s+/[fs]|format\s+[a-z]:|mkfs|shutdown|reboot|:?\(\)\s*\{)",
    re.IGNORECASE,
)


def workspace_root(conversation_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", conversation_id):
        raise ValueError("invalid conversation id")
    root = config.WORKSPACES_DIR / conversation_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve(root: Path, rel: str) -> Path:
    p = (root / (rel or ".")).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise PermissionError(f"path escapes workspace: {rel}")
    return p


def summarize_tool(name: str, args: dict) -> str:
    if name == "shell_exec":
        return f"Run `{str(args.get('command', ''))[:120]}`"
    if name in ("fs_read", "fs_write", "fs_edit"):
        return f"{name} `{args.get('path', '')}`"
    if name == "fs_list":
        return f"List `{args.get('path', '.')}`"
    if name == "fs_glob":
        return f"Find `{args.get('pattern', '')}`"
    if name == "fs_grep":
        return f"Search `{args.get('pattern', '')}`"
    if name == "git_clone":
        return f"Clone {args.get('url', '')}"
    if name == "git_status":
        return "Git status"
    if name == "ask_user":
        return "Question for you"
    if name == "web_search":
        return f"Search `{str(args.get('query', ''))[:100]}`"
    if name == "web_fetch":
        return f"Fetch `{str(args.get('url', ''))[:100]}`"
    if name == "web_research":
        return f"Deep-research `{str(args.get('query', ''))[:100]}`"
    if name == "search_memory":
        return f"Memory search `{str(args.get('query', ''))[:100]}`"
    if name == "search_chat_history":
        return f"History search `{str(args.get('query', ''))[:100]}`"
    if name in ("get_source_message", "get_conversation_context", "get_memory_record"):
        return f"{name}"
    return name


def needs_approval(name: str, args: dict, auto_approve: bool) -> tuple[bool, str]:
    if name in ("fs_read", "fs_list", "fs_glob", "fs_grep", "git_status"):
        return False, ""
    if name == "ask_user":
        return False, ""
    if name in ("search_memory", "search_chat_history", "get_source_message",
                "get_conversation_context", "get_memory_record"):
        return False, ""  # read-only memory retrieval
    if name in ("web_search", "web_fetch", "web_research"):
        # Read-only network retrieval. Results are untrusted third-party
        # data; the agent prompt requires treating them as such.
        if not config.ALLOW_NETWORK:
            return True, "Network access is disabled (ATLAS_ALLOW_NETWORK=0)."
        return False, ""
    if name == "shell_exec":
        cmd = str(args.get("command", ""))
        if DESTRUCTIVE_PATTERNS.search(cmd):
            return True, "Command looks destructive and requires approval."
        return (not auto_approve), ("" if auto_approve else "Shell execution requires approval.")
    if name in ("fs_write", "fs_edit", "git_clone"):
        return (not auto_approve), ("" if auto_approve else "Workspace write requires approval.")
    return True, f"Unknown tool '{name}' requires approval."


async def run_tool(name: str, args: dict, root: Path) -> dict:
    """Execute a tool. Returns {'ok': bool, 'output': str, 'diff': str?}."""
    if name in ("web_search", "web_fetch", "web_research"):
        return await webtools.run_web_tool(name, args)
    if name in ("search_memory", "search_chat_history", "get_source_message",
                "get_conversation_context", "get_memory_report", "get_memory_record"):
        from . import memory as _memtools
        try:
            scope = _memtools.scope_of(root.name)
        except Exception:
            scope = _memtools.DEFAULT_SCOPE
        if name == "get_memory_report":
            name = "get_memory_record"
        return await _memtools.run_memory_tool(name, args, scope)
    try:
        if name == "fs_list":
            target = resolve(root, str(args.get("path", ".")))
            if not target.exists():
                return {"ok": False, "output": f"not found: {args.get('path')}"}
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            lines = [("dir  " if e.is_dir() else "file ") + e.name for e in entries[:500]]
            return {"ok": True, "output": "\n".join(lines) or "(empty)"}
        if name == "fs_read":
            target = resolve(root, str(args.get("path", "")))
            text = target.read_text(encoding="utf-8", errors="replace")
            if len(text) > 20000:
                text = text[:20000] + "\n…[truncated]"
            return {"ok": True, "output": text}
        if name == "fs_write":
            target = resolve(root, str(args.get("path", "")))
            target.parent.mkdir(parents=True, exist_ok=True)
            before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            target.write_text(str(args.get("content", "")), encoding="utf-8")
            after = str(args.get("content", ""))
            diff = "".join(difflib.unified_diff(
                before.splitlines(True), after.splitlines(True),
                fromfile="before", tofile="after"))[:8000]
            return {"ok": True, "output": f"wrote {target.stat().st_size} bytes to {args.get('path')}",
                    "diff": diff}
        if name == "fs_edit":
            target = resolve(root, str(args.get("path", "")))
            old = str(args.get("old_string", ""))
            new = str(args.get("new_string", ""))
            text = target.read_text(encoding="utf-8", errors="replace")
            if text.count(old) != 1:
                return {"ok": False,
                        "output": f"old_string matched {text.count(old)} times; need exactly 1"}
            target.write_text(text.replace(old, new), encoding="utf-8")
            diff = "".join(difflib.unified_diff(
                text.splitlines(True), text.replace(old, new).splitlines(True),
                fromfile="before", tofile="after"))[:8000]
            return {"ok": True, "output": "edit applied", "diff": diff}
        if name == "fs_glob":
            pattern = str(args.get("pattern", "**/*"))
            matches = []
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    rel = str(Path(dirpath, f).relative_to(root)).replace(os.sep, "/")
                    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch("/" + rel, pattern):
                        matches.append(rel)
                        if len(matches) >= 300:
                            break
            return {"ok": True, "output": "\n".join(sorted(matches)[:300]) or "(no matches)"}
        if name == "fs_grep":
            pattern = str(args.get("pattern", ""))
            include = str(args.get("include") or "*")
            rx = re.compile(pattern)
            out = []
            for dirpath, _dirs, files in os.walk(root):
                if ".git" in dirpath:
                    continue
                for f in files:
                    if not fnmatch.fnmatch(f, include):
                        continue
                    fp = Path(dirpath, f)
                    try:
                        if fp.stat().st_size > 1_000_000:
                            continue
                        for i, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                            if rx.search(line):
                                out.append(f"{fp.relative_to(root)}:{i}: {line.strip()[:200]}")
                                if len(out) >= 200:
                                    break
                    except Exception:
                        continue
            return {"ok": True, "output": "\n".join(out[:200]) or "(no matches)"}
        if name == "shell_exec":
            cmd = str(args.get("command", ""))
            proc = await asyncio.create_subprocess_shell(
                cmd, cwd=str(root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(),
                                                timeout=config.SHELL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                return {"ok": False, "output": f"timed out after {config.SHELL_TIMEOUT_SECONDS}s"}
            text = out.decode("utf-8", errors="replace")
            if len(text) > config.MAX_TOOL_OUTPUT_CHARS:
                text = text[:config.MAX_TOOL_OUTPUT_CHARS] + "\n…[truncated]"
            code = proc.returncode or 0
            return {"ok": code == 0, "output": f"exit={code}\n{text}", "exit_code": code}
        if name == "git_clone":
            url = str(args.get("url", "")).strip()
            if not re.match(r"https?://", url):
                return {"ok": False, "output": "only https:// repository URLs are allowed"}
            dest = str(args.get("dir") or "").strip() or Path(url.rstrip("/").removesuffix(".git")).name
            target = resolve(root, dest)
            if target.exists() and any(target.iterdir()):
                return {"ok": False, "output": f"destination '{dest}' already exists and is not empty"}
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", url, str(target),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            text = out.decode("utf-8", errors="replace")[-4000:]
            if proc.returncode != 0:
                return {"ok": False, "output": f"git clone failed (exit={proc.returncode})\n{text}"}
            return {"ok": True, "output": f"cloned into {dest}\n{text}"}
        if name == "git_status":
            if not (root / ".git").exists():
                # workspace root may contain a cloned subdir; report it
                subs = [d for d in root.iterdir() if (d / ".git").exists()] if root.exists() else []
                if not subs:
                    return {"ok": True, "output": "not a git repository"}
                root = subs[0]
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--short", "--branch", cwd=str(root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            return {"ok": True, "output": out.decode("utf-8", errors="replace")[:4000]}
        return {"ok": False, "output": f"unknown tool: {name}"}
    except PermissionError as exc:
        return {"ok": False, "output": f"permission denied: {exc}"}
    except Exception as exc:
        return {"ok": False, "output": f"{type(exc).__name__}: {exc}"}
