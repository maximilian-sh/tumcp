"""RFC 6238 TOTP without third-party dependencies."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time


def _b32_decode(secret: str) -> bytes:
    clean = secret.upper().replace(" ", "").replace("-", "").rstrip("=")
    pad = (-len(clean)) % 8
    return base64.b32decode(clean + "=" * pad)


def valid_secret(secret: str) -> bool:
    """True if `secret` is usable base32 (what authenticator apps show)."""
    try:
        return len(_b32_decode(secret)) >= 10
    except (binascii.Error, ValueError):
        return False


def totp(secret: str, at: float | None = None, period: int = 30, digits: int = 6,
         algorithm: str = "sha1") -> str:
    """Current TOTP code. Defaults match the TU Wien IdP authenticator setup."""
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(_b32_decode(secret), struct.pack(">Q", counter),
                   getattr(hashlib, algorithm)).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def seconds_into_window(period: int = 30) -> float:
    return time.time() % period
