"""STT: fake AssemblyAI server test + live key validation (no transcription cost)."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.services import stt


class FakeAAI(BaseHTTPRequestHandler):
    polls = 0

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        assert self.headers.get("authorization"), "missing auth header"
        if self.path == "/v2/upload":
            assert len(body) > 0
            self._send({"upload_url": "https://fake/audio"})
        elif self.path == "/v2/transcript":
            data = json.loads(body or b"{}")
            assert data.get("speech_models") == ["universal-2"]
            assert data.get("language_code") == "en"
            self._send({"id": "tr_fake", "status": "queued"})
        else:
            self._send({}, 404)

    def do_GET(self):
        FakeAAI.polls += 1
        if self.path == "/v2/transcript/tr_fake":
            if FakeAAI.polls < 2:
                self._send({"id": "tr_fake", "status": "processing"})
            else:
                self._send({"id": "tr_fake", "status": "completed",
                            "text": "hello atlas", "audio_duration": 1.2})
        else:
            self._send({}, 404)


def test_stt_fake_server(tmp_path, monkeypatch):
    async def main():
        server = HTTPServer(("127.0.0.1", 0), FakeAAI)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        monkeypatch.setattr(stt, "BASE", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(stt.config, "ASSEMBLYAI_API_KEY", "test-key")
        r = await stt.transcribe(b"fake-audio-bytes", "voice.webm")
        server.shutdown()
        return r

    FakeAAI.polls = 0
    r = asyncio.run(main())
    assert r["text"] == "hello atlas"
    assert r["model"] == "universal-2" and r["language"] == "en"


def test_stt_key_valid_live():
    """Zero-cost auth check: an invalid transcript id returns 404 with a
    valid key, 401 without one."""
    import httpx
    from app import config

    if not config.ASSEMBLYAI_API_KEY:
        raise ValueError("ASSEMBLYAI_API_KEY not configured")
    r = httpx.get("https://api.assemblyai.com/v2/transcript/nope",
                  headers={"authorization": config.ASSEMBLYAI_API_KEY}, timeout=20)
    # 400 "transcript id not found" proves the key authenticates
    # (a bad key returns 401).
    assert r.status_code == 400 and "not found" in r.text, \
        f"unexpected: {r.status_code}: {r.text[:200]}"
