"""Saving TUWEL files to disk.

Every download folder keeps a small manifest (`.tumcp.json`: path -> size and the
TUWEL modification time, which also becomes the file's mtime). With it a re-sync
fetches only new or changed files, and a file the user has changed locally (e.g.
annotated a PDF) is never overwritten.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import dom

MANIFEST = ".tumcp.json"
MAX_BYTES = 500 * 2**20
UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name: str | None) -> str:
    """One path component: no separators, no leading dots, not too long."""
    name = unquote(name or "").replace(": ", " - ")
    name = UNSAFE.sub("_", name).strip(". ")
    return name[:180] or "_"


def rel_path(*parts: str | None) -> str:
    """Safe relative path ("a/b/c.pdf") from components; empty components are dropped."""
    return "/".join(safe_name(p) for p in parts if p and p.strip("/ "))


def file_key(url: str) -> str:
    """Identity of a TUWEL file across link forms: pluginfile vs webservice/pluginfile, and
    the revision number Moodle puts into folder links (…/mod_folder/content/<rev>/x.pdf)."""
    path = urlparse(url).path.split("pluginfile.php/", 1)[-1]
    parts = path.split("/")
    if len(parts) > 4 and parts[3].isdigit():
        del parts[3]
    return unquote("/".join(parts))


def file_links(html: str | None) -> list[dict]:
    """TUWEL files linked in an HTML fragment (labels, section texts, descriptions)."""
    out, seen = [], set()
    for a in dom.parse(html).css("a[href]"):
        href = a.attributes.get("href") or ""
        if not href.startswith("https://tuwel.tuwien.ac.at/") or "/pluginfile.php/" not in href:
            continue
        if file_key(href) in seen:
            continue
        seen.add(file_key(href))
        out.append({"name": unquote(urlparse(href).path.rsplit("/", 1)[-1]),
                    "label": dom.text(a, 120) or None, "url": href})
    return out


class Folder:
    """A download folder with its manifest."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._path = root / MANIFEST
        try:
            self.entries: dict[str, dict] = json.loads(self._path.read_text())
        except (OSError, ValueError):
            self.entries = {}

    def plan(self, rel: str, size: int | None, modified: int | None,
             overwrite: bool = False) -> str | None:
        """What to do with one remote file: "new", "updated", "kept" (local changes win)
        or None (up to date)."""
        out = self.root / rel
        if not out.exists():
            return "new"
        st = out.stat()
        if modified and int(st.st_mtime) == modified and size in (None, st.st_size):
            return None
        if overwrite:
            return "updated"
        known = self.entries.get(rel)
        untouched = (known is not None and int(st.st_mtime) == known.get("modified")
                     and st.st_size == known.get("size"))
        if modified is None:                  # linked file without date: present is enough
            return "kept" if known is not None and not untouched else None
        if untouched:
            return "updated" if modified > (known.get("modified") or 0) else None
        return "kept"

    def record(self, rel: str, modified: int | None) -> None:
        """Remember a file that was just written (and give it TUWEL's mtime)."""
        out = self.root / rel
        if modified:
            os.utime(out, (modified, modified))
        st = out.stat()
        self.entries[rel] = {"size": st.st_size, "modified": int(st.st_mtime)}

    def save(self) -> None:
        if not self.entries:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1, sort_keys=True))
        tmp.replace(self._path)
