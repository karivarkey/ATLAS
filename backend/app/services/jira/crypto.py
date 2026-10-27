"""Server-side credential encryption (stdlib only, no new dependencies).

Construction: PBKDF2-HMAC-SHA256(master_key, salt) -> 64 bytes, split into
enc_key | mac_key. Plaintext is encrypted with a hash-counter stream cipher
(keystream = HMAC-SHA256(enc_key, nonce || counter)), then authenticated with
Encrypt-then-MAC (tag = HMAC-SHA256(mac_key, nonce || ciphertext)).

Master key resolution (never in DB or source):
  1. ATLAS_CREDENTIAL_KEY env var, else
  2. <DATA_DIR>/.jira_master_key (created once, mode 0o600 on POSIX).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets


def master_key() -> bytes:
    from ... import config
    env = os.environ.get("ATLAS_CREDENTIAL_KEY", "").strip()
    if env:
        return env.encode("utf-8")
    config.ensure_dirs()
    key_file = config.DATA_DIR / ".jira_master_key"
    try:
        raw = key_file.read_bytes()
        if len(raw) >= 32:
            return raw
    except OSError:
        pass
    raw = secrets.token_bytes(32)
    try:
        key_file.write_bytes(raw)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return raw


def _derive(master: bytes, salt: bytes) -> tuple[bytes, bytes]:
    okm = hashlib.pbkdf2_hmac("sha256", master, salt, 200_000, dklen=64)
    return okm[:32], okm[32:]


def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out.extend(hmac.new(enc_key, nonce + counter.to_bytes(8, "big"),
                            hashlib.sha256).digest())
        counter += 1
    return bytes(out[:n])


def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("empty token")
    master = master_key()
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    enc_key, mac_key = _derive(master, salt)
    data = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    blob = salt + nonce + tag + ct
    return "JENC1." + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_token(payload: str) -> str:
    if not payload or not payload.startswith("JENC1."):
        raise ValueError("unknown credential envelope")
    blob = base64.urlsafe_b64decode(payload[6:].encode("ascii"))
    if len(blob) < 65:  # 16 salt + 16 nonce + 32 tag + >=1 byte ciphertext
        raise ValueError("credential blob too short")
    salt, nonce, tag, ct = blob[:16], blob[16:32], blob[32:64], blob[64:]
    enc_key, mac_key = _derive(master_key(), salt)
    expect = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, tag):
        raise ValueError("credential integrity check failed")
    data = bytes(a ^ b for a, b in zip(ct, _keystream(enc_key, nonce, len(ct))))
    return data.decode("utf-8")


def is_encrypted_ref(value: str) -> bool:
    return bool(value) and value.startswith("JENC1.")
