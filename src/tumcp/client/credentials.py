"""Where TU credentials live: the OS keychain, or environment variables.

Order per field: environment variable (TUMCP_USERNAME / TUMCP_PASSWORD /
TUMCP_TOTP_SECRET) first, then the OS keychain entry written by `tumcp login`.
Credentials are never written to a config file or log.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE = "tumcp"
FIELDS = ("username", "password", "totp_secret")


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    totp_secret: str | None

    @property
    def complete(self) -> bool:
        return bool(self.username and self.password)


def _keyring():
    import keyring  # imported lazily: env-only setups need no keychain backend
    return keyring


def _read(field: str) -> str | None:
    value = os.environ.get(f"TUMCP_{field.upper()}")
    if value:
        return value.strip()
    try:
        return (_keyring().get_password(SERVICE, field) or "").strip() or None
    except Exception:
        return None


def load() -> Credentials:
    return Credentials(username=_read("username") or "",
                       password=_read("password") or "",
                       totp_secret=_read("totp_secret"))


def save(creds: Credentials) -> None:
    kr = _keyring()
    kr.set_password(SERVICE, "username", creds.username)
    kr.set_password(SERVICE, "password", creds.password)
    if creds.totp_secret:
        kr.set_password(SERVICE, "totp_secret", creds.totp_secret)


def delete() -> None:
    kr = _keyring()
    for field in FIELDS:
        try:
            kr.delete_password(SERVICE, field)
        except Exception:
            pass
