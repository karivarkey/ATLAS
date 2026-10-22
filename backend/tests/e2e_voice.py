"""True voice E2E: Windows SAPI synthesizes speech offline -> POST to
Atlas /api/v1/stt/transcribe -> assert AssemblyAI nano returns the words.
Costs a few seconds of nano transcription.
"""
import subprocess
import urllib.request

WAV = r"C:\Users\Karivarkey\AppData\Local\Temp\atlas_voice_test.wav"
TEXT = "hello atlas this is a voice test"

ps = (
    "Add-Type -AssemblyName System.Speech; "
    f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    f"$s.SetOutputToWaveFile('{WAV}'); "
    f"$s.Speak('{TEXT}'); $s.Dispose()"
)
subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=120)

with open(WAV, "rb") as f:
    audio = f.read()
print("wav bytes:", len(audio))

boundary = "BOUNDARY12345"
body = (
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="file"; filename="voice.wav"\r\n'
    "Content-Type: audio/wav\r\n\r\n"
).encode() + audio + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request(
    "http://127.0.0.1:8765/api/v1/stt/transcribe",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)
import json

resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
print("transcript:", resp.get("text"))
print("model:", resp.get("model"), "| lang:", resp.get("language"))
got = (resp.get("text") or "").lower()
ok = "hello" in got and "atlas" in got
print("VOICE E2E:", "PASS" if ok else f"CHECK (got: {resp.get('text')!r})")
