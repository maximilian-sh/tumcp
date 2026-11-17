"""Register tumcp as MCP server "tu" with the MCP clients found on this device.

Clients with a CLI (Claude Code, Codex) are configured through it; Claude Desktop
and Cursor through their JSON file. The command is the absolute path of the tumcp
executable, because desktop apps on macOS do not see the shell's PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

NAME = "tu"


@dataclass(frozen=True)
class Client:
    name: str
    register: Callable[[str], str]


def command() -> str:
    found = shutil.which("tumcp")
    return found or str(Path(sys.argv[0]).absolute())


def _claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Claude" / "claude_desktop_config.json"


def _json(path: Path) -> Callable[[str], str]:
    def register(cmd: str) -> str:
        text = path.read_text() if path.exists() else ""
        data = json.loads(text) if text.strip() else {}
        servers = data.setdefault("mcpServers", {})
        entry = {"command": cmd, "args": []}
        if servers.get(NAME) == entry:
            return "already set up"
        state = "updated" if NAME in servers else "added"
        servers[NAME] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return f"{state} in {path}"
    return register


def _cli(exe: str, remove: list[str], add: list[str]) -> Callable[[str], str]:
    def register(cmd: str) -> str:
        subprocess.run([exe, *remove], capture_output=True)      # replace an older entry
        done = subprocess.run([exe, *add, "--", cmd], capture_output=True, text=True)
        if done.returncode:
            return f"failed: {(done.stderr or done.stdout).strip()[:300]}"
        return "added"
    return register


def detect() -> list[Client]:
    found = []
    if exe := shutil.which("claude"):
        found.append(Client("Claude Code", _cli(
            exe, ["mcp", "remove", "--scope", "user", NAME],
            ["mcp", "add", "--scope", "user", NAME])))
    if exe := shutil.which("codex"):
        found.append(Client("Codex", _cli(exe, ["mcp", "remove", NAME], ["mcp", "add", NAME])))
    desktop = _claude_desktop_config()
    if desktop.parent.is_dir():
        found.append(Client("Claude Desktop", _json(desktop)))
    cursor = Path.home() / ".cursor"
    if cursor.is_dir():
        found.append(Client("Cursor", _json(cursor / "mcp.json")))
    return found
