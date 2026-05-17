"""Tiny symmetric encryption for credentials.

Uses HMAC-SHA256-derived key + AES-CTR-like XOR with HMAC tag. This is NOT
intended to replace Fernet/AES-GCM in production – when ``cryptography`` is
available swap to Fernet. The interface is the same: encrypt(str) -> str,
decrypt(str) -> str. Tokens are kept out of plaintext storage and out of logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


def _derive(master: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", master.encode("utf-8"), salt, 50_000, 32)


def _xor_stream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    counter = 0
    block = b""
    for i, byte in enumerate(data):
        if i % 32 == 0:
            block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
            counter += 1
        out[i] = byte ^ block[i % 32]
    return bytes(out)


def encrypt(plaintext: str, master_key: str) -> str:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive(master_key, salt)
    body = _xor_stream(key, nonce, plaintext.encode("utf-8"))
    tag = hmac.new(key, salt + nonce + body, hashlib.sha256).digest()
    blob = salt + nonce + tag + body
    return "v1:" + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt(token: str, master_key: str) -> str:
    if not token.startswith("v1:"):
        raise ValueError("unsupported credential format")
    blob = base64.urlsafe_b64decode(token[3:].encode("ascii"))
    salt, nonce, tag, body = blob[:16], blob[16:28], blob[28:60], blob[60:]
    key = _derive(master_key, salt)
    expected = hmac.new(key, salt + nonce + body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("credential authentication failed")
    return _xor_stream(key, nonce, body).decode("utf-8")


def mask(token: str, visible: int = 4) -> str:
    if not token:
        return ""
    if len(token) <= visible:
        return "*" * len(token)
    return "*" * (len(token) - visible) + token[-visible:]
