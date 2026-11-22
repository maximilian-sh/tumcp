"""TuClient: one logged-in TU Wien session (TISS + TUWEL).

Several MCP clients on one machine (Claude Code, Codex, Cursor …) each start their
own tumcp process. They share ONE cookie jar in the user data directory, guarded
by a file lock: before logging in, a process re-reads the jar, because another
process may have just logged in. Every fresh login burns a single-use TOTP code,
so avoiding duplicate logins matters.

Course contents and files come from TUWEL's web service with the token of the
Moodle app (see `_launch_token`): it lists every file with size and date, in every
course format, which the HTML pages do not.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import http.cookiejar
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
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
CDN = "https://cdn.video.tuwien.ac.at"           # LectureTube recordings and captions
UA = "tumcp (+https://github.com/maximilian-sh/tumcp)"
WS = f"{TUWEL}/webservice/rest/server.php"
# the only web-service functions tumcp calls: all of them just read
WS_FUNCTIONS = frozenset({
    "core_webservice_get_site_info",
    "core_course_get_contents",
    "core_course_get_courses_by_field",
    "core_course_get_course_module",
})
TOKEN_ERRORS = ("invalidtoken", "accessexception")


class TuError(RuntimeError):
    pass


def ws_file_url(url: str) -> str:
    """The web-service form of a TUWEL file link (the plain one needs a browser session)."""
    if url.startswith(f"{TUWEL}/pluginfile.php/"):
        return url.replace("/pluginfile.php/", "/webservice/pluginfile.php/", 1)
    if url.startswith(f"{TUWEL}/webservice/pluginfile.php/"):
        return url
    raise TuError("not a TUWEL file link (pluginfile.php)")


ByteProgress = Callable[[int, int | None], Awaitable[None]]


async def _save_stream(r: httpx2.Response, out: Path, max_bytes: int,
                       progress: ByteProgress | None = None) -> int:
    """Write a streamed response to `out` through a .part file; returns the size.
    `progress(bytes_done, bytes_total)` is awaited about every 8 MB."""
    if r.status_code != 200:
        raise TuError(f"HTTP {r.status_code}")
    total = int(r.headers.get("content-length") or 0) or None
    if total and total > max_bytes:
        raise TuError(f"larger than {max_bytes // 2**20} MB")
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    size, reported = 0, 0
    try:
        with part.open("wb") as fh:
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise TuError(f"larger than {max_bytes // 2**20} MB")
                fh.write(chunk)
                if progress is not None and size - reported >= 8 * 2**20:
                    reported = size
                    await progress(size, total)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(out)
    return size


def _form(args: dict, prefix: str = "") -> dict[str, str]:
    """Nested arguments as PHP form fields: {"options": [{"name": "x"}]} -> options[0][name]."""
    out: dict[str, str] = {}
    for key, value in args.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_form(value, name))
        elif isinstance(value, (list, tuple)):
            out.update(_form(dict(enumerate(value)), name))
        elif isinstance(value, bool):
            out[name] = "1" if value else "0"
        else:
            out[name] = str(value)
    return out


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
        self._token: str | None = None
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

    async def tiss_api(self, path: str, *, xml: bool = False, **params):
        """GET from TISS's public REST API (/api/...), which needs no login. Returns the
        parsed JSON, or with `xml` the XML text (some endpoints only speak XML); None if
        TISS answers 404 (nothing found)."""
        r = await self.http.get(f"{TISS}/api/{path}", params=params,
                                headers={"Accept": "application/xml" if xml
                                         else "application/json"})
        if r.status_code == 404:
            return None
        if xml:
            if r.status_code != 200:
                raise TuError(f"TISS API answered with HTTP {r.status_code}")
            return r.text
        try:
            data = r.json()
        except ValueError:
            raise TuError(f"TISS API answered with HTTP {r.status_code}, not JSON") from None
        error = isinstance(data, dict) and (data.get("error") or data.get("error_message"))
        if error:
            raise TuError(f"TISS API: {error.get('message') if isinstance(error, dict) else error}")
        return data

    # ------------------------------------------------------------------ TUWEL
    async def _tuwel_login(self) -> None:
        await self._ensure_tiss()          # IdP session first: the TUWEL hop then needs no TOTP
        r = await self.http.get(f"{TUWEL}/auth/saml2/login.php?wants={TUWEL}/my/")
        for _ in range(8):
            if str(r.url).startswith(TUWEL) and '"sesskey":"' in r.text:
                break
            try:
                nxt = await auth.post_saml(self.http, r)
            except httpx2.UnsupportedProtocol:
                # Moodle went on to a remembered launch.php, which redirects to
                # moodlemobile://: the login itself has worked
                nxt = await self.http.get(f"{TUWEL}/my/")
            if nxt is None:
                raise TuError(f"TUWEL login stuck at {str(r.url).split('?')[0]}")
            r = nxt
        self._remember(r.text)
        if not self._sesskey:
            raise TuError("TUWEL login: no sesskey")
        await asyncio.to_thread(self._save_locked)

    async def _ensure_tuwel(self) -> None:
        """Log in to TUWEL unless the session is still valid (held with self._lock)."""
        r = await self.http.get(f"{TUWEL}/my/")
        if "/login/" in str(r.url) or auth.IDP_HOST in str(r.url):
            await self._tuwel_login()
        else:
            self._remember(r.text)

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

    # ------------------------------------------------------------ web service
    async def _launch_token(self) -> str:
        """Get a web-service token the way the Moodle app does: with the browser session,
        launch.php redirects to moodlemobile://token=<base64 "md5(wwwroot+passport):::token">.
        TUWEL reuses the token it already issued for the app; the user can revoke it under
        Preferences > Security keys.

        Only ever open launch.php with a valid TUWEL session: without one, Moodle remembers
        it as the page to return to after the login, which then ends at moodlemobile://."""
        await self._ensure_tuwel()
        passport = secrets.token_hex(8)
        r = await self.http.get(f"{TUWEL}/admin/tool/mobile/launch.php", params={
            "service": "moodle_mobile_app", "passport": passport, "urlscheme": "moodlemobile"},
            follow_redirects=False)
        location = r.headers.get("location", "")
        if not location.startswith("moodlemobile://token="):
            raise TuError("TUWEL handed out no app token (is the Moodle app access disabled?)")
        payload = base64.b64decode(location.split("token=", 1)[1]).decode()
        signature, token, *_ = payload.split(":::")
        if signature != hashlib.md5(f"{TUWEL}{passport}".encode()).hexdigest():
            raise TuError("TUWEL app token: signature mismatch")
        return token

    async def _ws_token(self) -> str:
        if self._token is None:
            self._token = credentials.load_token()
        if self._token is None:
            self._token = await self._launch_token()
            credentials.save_token(self._token)
        return self._token

    def _drop_token(self) -> None:
        self._token = None
        credentials.delete_token()

    async def ws(self, function: str, **args):
        """Call a TUWEL web-service function (one of WS_FUNCTIONS) with the app token."""
        if function not in WS_FUNCTIONS:
            raise TuError(f"{function} is not one of the read functions tumcp uses")
        async with self._lock:
            for attempt in (1, 2):
                body = {"wstoken": await self._ws_token(), "wsfunction": function,
                        "moodlewsrestformat": "json", **_form(args)}
                r = await self.http.post(WS, data=body)
                try:
                    data = r.json()
                except ValueError:
                    raise TuError(f"{function}: TUWEL answered with HTTP {r.status_code}, "
                                  "not JSON") from None
                if not (isinstance(data, dict) and "exception" in data):
                    return data
                if attempt == 1 and data.get("errorcode") in TOKEN_ERRORS:
                    self._drop_token()
                    continue
                raise TuError(f"{function}: {data.get('message') or data.get('errorcode')}")

    async def download(self, url: str, out: Path, max_bytes: int) -> int:
        """Stream a TUWEL file to `out` (through a .part file) and return its size.

        The token goes in the POST body, so it never shows up in a URL or a log line."""
        url = ws_file_url(url)
        async with self._lock:
            for attempt in (1, 2):
                body = {"token": await self._ws_token()}
                async with self.http.stream("POST", url, data=body) as r:
                    if "application/json" in r.headers.get("content-type", ""):
                        await r.aread()
                        try:
                            err = r.json()
                        except ValueError:
                            err = None
                        if isinstance(err, dict) and err.get("errorcode"):
                            if attempt == 1 and err["errorcode"] in TOKEN_ERRORS:
                                self._drop_token()
                                continue
                            raise TuError(err.get("error") or err["errorcode"])
                    return await _save_stream(r, out, max_bytes)
        raise TuError("TUWEL rejected the app token twice")

    async def cdn_text(self, url: str) -> str:
        """A small public LectureTube file (captions) as text."""
        if not url.startswith(CDN + "/"):
            raise TuError("only cdn.video.tuwien.ac.at URLs are allowed")
        r = await self.http.get(url)
        if r.status_code != 200:
            raise TuError(f"LectureTube CDN: HTTP {r.status_code}")
        return r.content.decode("utf-8", errors="replace")

    async def cdn_download(self, url: str, out: Path, max_bytes: int,
                           progress: ByteProgress | None = None) -> int:
        """Stream a public LectureTube file (video, captions) from the TU video CDN.

        Needs no session, so it does not hold the session lock: other tools keep working
        while a long video downloads."""
        if not url.startswith(CDN + "/"):
            raise TuError("only cdn.video.tuwien.ac.at URLs are allowed")
        async with self.http.stream("GET", url) as r:
            return await _save_stream(r, out, max_bytes, progress)

    async def ajax(self, method: str, args: dict):
        """Call a TUWEL (Moodle) web-service function that is enabled for AJAX."""
        async with self._lock:
            for attempt in (1, 2):
                if self._sesskey is None:
                    await self._ensure_tuwel()
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
