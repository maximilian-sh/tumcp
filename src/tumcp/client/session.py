"""TuClient: one logged-in TU Wien session (TISS + TUWEL).

Several MCP clients on one machine (Claude Code, Codex, Cursor …) each start their
own tumcp process. They share ONE cookie jar in the user data directory, guarded
by a file lock: before logging in, a process re-reads the jar, because another
process may have just logged in. Every fresh login burns a single-use TOTP code,
so avoiding duplicate logins matters.
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import logging
import os
import re
import time
from pathlib import Path

import httpx2
from filelock import FileLock
from platformdirs import user_data_dir

from . import auth, credentials
from .totp import seconds_into_window
from .window import SHELL_MARKER, ClientWindow

log = logging.getLogger(__name__)

TUWEL = "https://tuwel.tuwien.ac.at"
TISS = "https://tiss.tuwien.ac.at"
UA = "tumcp (+https://github.com/maximilian-sh/tumcp)"


class TuError(RuntimeError):
    pass


def data_dir() -> Path:
    path = Path(os.environ.get("TUMCP_DATA_DIR") or user_data_dir("tumcp", appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


class TuClient:
    """Async client. Use as `async with TuClient() as tu: ...`."""

    def __init__(self, creds: credentials.Credentials | None = None,
                 data: Path | None = None) -> None:
        self._creds = creds
        self._dir = data or data_dir()
        self._jar_path = self._dir / "cookies.txt"
        self._flock = FileLock(str(self._dir / "session.lock"), timeout=120)
        self._lock = asyncio.Lock()          # one request at a time per process
        self._window = ClientWindow()
        self._sesskey: str | None = None
        self._userid: int | None = None
        self._tiss_checked = 0.0
        self.http = httpx2.AsyncClient(
            follow_redirects=True, http2=False,
            timeout=httpx2.Timeout(25.0, connect=10.0),
            headers={"User-Agent": UA, "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"})
        self._load_jar()
        self._window.install(self.http.cookies)

    async def __aenter__(self) -> "TuClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------- cookie jar
    def _load_jar(self) -> None:
        jar = http.cookiejar.MozillaCookieJar(str(self._jar_path))
        if self._jar_path.exists():
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
            except (OSError, http.cookiejar.LoadError):
                return
        for c in jar:
            self.http.cookies.jar.set_cookie(c)

    def _save_jar(self) -> None:
        jar = http.cookiejar.MozillaCookieJar(str(self._jar_path))
        for c in self.http.cookies.jar:
            if not c.name.startswith("dsrwid-"):      # per-process TISS window cookie
                jar.set_cookie(c)
        tmp = self._jar_path.with_suffix(".tmp")
        jar.filename = str(tmp)
        jar.save(ignore_discard=True, ignore_expires=True)
        tmp.chmod(0o600)
        tmp.replace(self._jar_path)

    def _creds_or_fail(self) -> credentials.Credentials:
        creds = self._creds or credentials.load()
        if not creds.complete:
            raise TuError("no TU Wien credentials configured — run `tumcp login`")
        return creds

    # ------------------------------------------------------------------- TISS
    def wurl(self, url: str) -> str:
        return self._window.url(url)

    async def _tiss_ok(self) -> bool:
        try:
            r = await self.http.get(self.wurl(auth.TISS_PROBE))
            return str(r.url).startswith(TISS) and auth.looks_logged_in(r.text)
        except httpx2.HTTPError:
            return False

    async def _ensure_tiss(self) -> None:
        if time.time() - self._tiss_checked < 120:
            return
        if not await self._tiss_ok():
            creds = self._creds_or_fail()
            # another tumcp process may have logged in meanwhile: reload, re-check, then login
            await asyncio.to_thread(self._flock.acquire)
            try:
                self._load_jar()
                if not await self._tiss_ok():
                    try:
                        await auth.login(self.http, self.wurl, *self._fields(creds))
                    except auth.AuthError as exc:
                        if not creds.totp_secret:
                            raise
                        wait = 31 - seconds_into_window()
                        log.warning("login failed (%s); retrying in the next TOTP window", exc)
                        await asyncio.sleep(max(wait, 1))
                        await auth.login(self.http, self.wurl, *self._fields(creds))
                    self._save_jar()
            finally:
                self._flock.release()
        self._tiss_checked = time.time()

    @staticmethod
    def _fields(creds: credentials.Credentials):
        return creds.username, creds.password, creds.totp_secret

    async def tiss_get(self, url: str) -> str:
        if not url.startswith(TISS + "/"):
            raise TuError("only tiss.tuwien.ac.at URLs are allowed")
        async with self._lock:
            await self._ensure_tiss()
            r = await self.http.get(self.wurl(url))
            if SHELL_MARKER in r.text and len(r.text) < 20000:
                raise TuError("TISS returned an empty client-window shell")
            return r.text

    # ------------------------------------------------------------------ TUWEL
    async def _tuwel_login(self) -> None:
        await self._ensure_tiss()          # IdP session first: the TUWEL hop then needs no TOTP
        r = await self.http.get(f"{TUWEL}/auth/saml2/login.php?wants={TUWEL}/my/")
        for _ in range(8):
            if str(r.url).startswith(TUWEL) and '"sesskey":"' in r.text:
                break
            nxt = await auth.post_saml(self.http, r)
            if nxt is None:
                raise TuError(f"TUWEL login stuck at {str(r.url).split('?')[0]}")
            r = nxt
        self._remember(r.text)
        if not self._sesskey:
            raise TuError("TUWEL login: no sesskey")
        await asyncio.to_thread(self._save_locked)

    def _save_locked(self) -> None:
        with self._flock:
            self._save_jar()

    def _remember(self, html: str) -> None:
        m = re.search(r'"sesskey":"([^"]+)"', html)
        if m:
            self._sesskey = m.group(1)
        uid = re.search(r'data-userid="(\d+)"', html) or re.search(r'"userid":(\d+)', html)
        if uid:
            self._userid = int(uid.group(1))

    async def tuwel_get(self, path_or_url: str) -> str:
        url = path_or_url if path_or_url.startswith("http") else f"{TUWEL}{path_or_url}"
        if not url.startswith(TUWEL + "/"):
            raise TuError("only tuwel.tuwien.ac.at URLs are allowed")
        async with self._lock:
            r = await self.http.get(url)
            if "/login/" in str(r.url) or auth.IDP_HOST in str(r.url):
                await self._tuwel_login()
                r = await self.http.get(url)
            self._remember(r.text)
            return r.text

    async def ajax(self, method: str, args: dict):
        """Call a TUWEL (Moodle) web-service function that is enabled for AJAX."""
        async with self._lock:
            for attempt in (1, 2):
                if self._sesskey is None:
                    r = await self.http.get(f"{TUWEL}/my/")
                    if "/login/" in str(r.url) or auth.IDP_HOST in str(r.url):
                        await self._tuwel_login()
                    else:
                        self._remember(r.text)
                r = await self.http.post(
                    f"{TUWEL}/lib/ajax/service.php?sesskey={self._sesskey}&info={method}",
                    json=[{"index": 0, "methodname": method, "args": args}])
                try:
                    data = r.json()
                except ValueError:
                    data = None
                err = None
                if isinstance(data, dict) and data.get("error"):
                    err = data.get("errorcode") or str(data.get("error"))
                elif isinstance(data, list) and data and data[0].get("error"):
                    err = (data[0].get("exception") or {}).get("errorcode") or "error"
                elif not isinstance(data, list) or not data:
                    err = "invalid response"
                if err is None:
                    return data[0]["data"]
                if attempt == 1 and err in ("servicerequireslogin", "invalidsesskey",
                                            "requireloginerror", "invalid response"):
                    self._sesskey = None
                    continue
                raise TuError(f"{method}: {err}")

    async def userid(self) -> int:
        if self._userid is None:
            await self.tuwel_get("/my/")
        if self._userid is None:
            raise TuError("could not determine the TUWEL user id")
        return self._userid
