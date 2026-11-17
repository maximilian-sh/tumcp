"""Settings that are not secrets, in `<data dir>/config.json` (written by `tumcp setup`).

Currently only the download folder: TUMCP_DOWNLOAD_DIR, else the configured one,
else ~/Downloads/TUWEL.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .client.session import data_dir


def _path() -> Path:
    return data_dir() / "config.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def save(**values) -> None:
    cfg = {**load(), **values}
    _path().write_text(json.dumps(cfg, indent=2) + "\n")


def download_dir() -> Path:
    value = os.environ.get("TUMCP_DOWNLOAD_DIR") or load().get("download_dir")
    return Path(value).expanduser() if value else Path.home() / "Downloads" / "TUWEL"
