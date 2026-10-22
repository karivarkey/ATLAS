"""Integration tests: full agent loop against fake OpenAI-compatible SSE
servers (stdlib http.server in a thread). Proves protocol parsing, real tool
execution, loop guards and completion — only the model weights are fake.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import db, events
from app.services import agent, executions, model_catalog


class FakeOpenAI(BaseHTTPRequestHandler):
    STATE = {"calls": 0}

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        FakeOpenAI.STATE["calls"] += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if FakeOpenAI.STATE["calls"] == 1:
            chunks = [
                {"choices": [{"delta": {"content": "Creating file now. "}}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "call_1",
                     "function": {"name": "fs_write",
                                 "arguments": json.dumps({"path": "hello.txt",
                                                          "content": "hello atlas"})}}]}}]},
            ]
        else:
            chunks = [{"choices": [{"delta": {"content": "File written and verified."}}]}]
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class LoopyServer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c9",
             "function": {"name": "fs_write",
                         "arguments": json.dumps({"path": "loop.txt",
                                                  "content": "same"})}}]}}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class ReadOnlyServer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "r1",
             "function": {"name": "fs_list",
                         "arguments": {"path": "."}}}]}}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _ok_for(port):
    async def _ok(m):
        return True, {"id": m, "protocol": "chat_completions",
                      "endpoint": f"http://127.0.0.1:{port}/chat",
                      "supports_tools": True}
    return _ok


def _submit_and_wait(cid, title, text, model, timeout_s=30):
    """Submit inside a single event loop and wait — one asyncio.run total."""
    async def main():
        now = db.now_ms()
        db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
                   " VALUES (?, 't', ?, ?, ?, ?)", (cid, model, cid, now, now))
        res = await executions.submit(cid, text, model)
        eid = res["execution_id"]
        for _ in range(int(timeout_s * 10)):
            if executions.get_snapshot(eid)["status"] in events.TERMINAL:
                break
            await asyncio.sleep(0.1)
        return eid
    return asyncio.run(main())


def test_agent_loop_real_tools_fake_model(tmp_path, monkeypatch):
    db.reset_for_tests(tmp_path)
    FakeOpenAI.STATE = {"calls": 0}
    server = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    monkeypatch.setattr(model_catalog, "validate_model", _ok_for(port))
    eid = _submit_and_wait("ce2e", "t", "create hello.txt", "fake/m")
    snap = executions.get_snapshot(eid)
    assert snap["status"] == "completed", snap
    evs = events.replay(eid, 0)
    types = [e["type"] for e in evs]
    assert "turn_started" in types and "tool_started" in types
    assert "tool_completed" in types and "turn_completed" in types
    from app.services.workspace import workspace_root
    assert (workspace_root("ce2e") / "hello.txt").read_text() == "hello atlas"
    msgs = db.query("SELECT content FROM messages WHERE conversation_id='ce2e' AND role='assistant'")
    assert any("verified" in (m["content"] or "") for m in msgs)
    server.shutdown()


def test_unproductive_loop_breaker(tmp_path, monkeypatch):
    """Identical tool results 3x must stop the loop early and still complete."""
    db.reset_for_tests(tmp_path)
    server = HTTPServer(("127.0.0.1", 0), LoopyServer)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(model_catalog, "validate_model", _ok_for(port))
    eid = _submit_and_wait("cloop", "t", "write loop.txt", "fake/m")
    snap = executions.get_snapshot(eid)
    assert snap["status"] == "completed", snap
    assert snap["current_step"] < 10, snap  # stopped early, not at budget
    failures = [e for e in events.replay(eid, 0) if e["type"] == "tool_failed"]
    assert any("same result 3x" in json.dumps(e) for e in failures)
    server.shutdown()


def test_stagnation_guard_wraps_up(tmp_path, monkeypatch):
    """20 steps with no file changes must wrap up instead of running to budget."""
    db.reset_for_tests(tmp_path)
    server = HTTPServer(("127.0.0.1", 0), ReadOnlyServer)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(model_catalog, "validate_model", _ok_for(port))
    eid = _submit_and_wait("cslow", "t", "list files", "fake/m")
    snap = executions.get_snapshot(eid)
    assert snap["status"] == "completed", snap
    assert snap["current_step"] <= 25, snap
    # Either the identical-output breaker or the stagnation guard must
    # have stopped the loop far below the 60-step budget.
    failures = [e for e in events.replay(eid, 0) if e["type"] == "tool_failed"]
    assert any("same result 3x" in json.dumps(e) or "wrapping up" in json.dumps(e)
               for e in failures)
    server.shutdown()


class TickingServer(BaseHTTPRequestHandler):
    N = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        TickingServer.N += 1
        n = TickingServer.N
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": f"t{n}",
             "function": {"name": "shell_exec",
                         "arguments": json.dumps({"command": f"echo tick-{n}"})}}]}}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def test_stagnation_guard_wraps_up_varied_output(tmp_path, monkeypatch):
    """Varied but modification-free tool results must still wrap up at ~20 steps."""
    db.reset_for_tests(tmp_path)
    TickingServer.N = 0
    server = HTTPServer(("127.0.0.1", 0), TickingServer)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(model_catalog, "validate_model", _ok_for(port))
    eid = _submit_and_wait("ctick", "t", "tick", "fake/m")
    snap = executions.get_snapshot(eid)
    assert snap["status"] == "completed", snap
    assert snap["current_step"] <= 25, snap
    failures = [e for e in events.replay(eid, 0) if e["type"] == "tool_failed"]
    assert any("wrapping up" in json.dumps(e) for e in failures)
    server.shutdown()
