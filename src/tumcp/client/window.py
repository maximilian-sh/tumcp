"""DeltaSpike client-window handling for TISS.

TISS is a Jakarta Faces app behind Apache DeltaSpike. A plain request returns an
~8 KB "Loading..." shell containing `newWindowId = 'uninitializedWindowId'`; the
real page only renders once a client window exists. In a browser,
windowhandler.js generates a window id (1000-9999) and a request token (0-999),
stores a `dsrwid-<token>` cookie holding the window id and reloads with
`?dswid=<id>&dsrid=<token>`. Without a browser we do the same three things.

Getting this wrong is silent: every page comes back 200 OK, just empty.
"""
from __future__ import annotations

import random

DOMAIN = "tiss.tuwien.ac.at"
SHELL_MARKER = "uninitializedWindowId"


class ClientWindow:
    def __init__(self) -> None:
        self.dswid = str(random.randint(1000, 9999))
        self.dsrid = str(random.randint(0, 999))

    def install(self, cookies) -> None:
        cookies.set(f"dsrwid-{self.dsrid}", self.dswid, domain=DOMAIN, path="/")

    def url(self, url: str) -> str:
        if DOMAIN not in url or "dswid=" in url:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}dswid={self.dswid}&dsrid={self.dsrid}"
