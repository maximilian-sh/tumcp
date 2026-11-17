"""`tumcp` (serve over stdio), `tumcp setup` (login, download folder, MCP clients),
`tumcp login`, `tumcp logout`, `tumcp status`, `tumcp update`, `tumcp tools` (list
tools), `tumcp call <tool> key=value ...` (run one tool, print JSON)."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, clients, config
from .client import credentials, tuwel
from .client.session import TuClient, data_dir
from .client.totp import totp, valid_secret

REPO = "https://github.com/maximilian-sh/tumcp"


def _ask(prompt: str, default: str = "") -> str:
    try:
        return input(prompt).strip() or default
    except EOFError:
        return default


def _yes(prompt: str, default: bool) -> bool:
    answer = _ask(f"{prompt} [{'Y/n' if default else 'y/N'}] ").lower()
    return default if not answer else answer.startswith(("y", "j"))


def _login() -> int:
    print("TU Wien login. The credentials go into the OS keychain, nowhere else.")
    username = _ask("Username: ")
    password = getpass.getpass("Password: ")
    secret = getpass.getpass("TOTP secret (base32, empty if MFA is off): ").replace(" ", "")
    if secret and not valid_secret(secret):
        print("That is not a valid base32 TOTP secret.", file=sys.stderr)
        return 1
    if secret:
        print(f"Current code: {totp(secret)}  (compare with your authenticator app)")
    creds = credentials.Credentials(username, password, secret or None)
    (data_dir() / "cookies.txt").unlink(missing_ok=True)
    credentials.delete_token()            # the app token belongs to the previous account
    try:
        asyncio.run(_check(creds))
    except Exception as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    credentials.save(creds)
    print("Logged in. Credentials saved.")
    return 0


async def _check(creds=None) -> int:
    async with TuClient(creds) as tu:
        await tu.tiss_get("https://tiss.tuwien.ac.at/education/favorites.xhtml")
        n = len(await tuwel.courses(tu))
        await tu.ws("core_webservice_get_site_info")
    print(f"TISS ok, TUWEL ok ({n} current courses), TUWEL files ok")
    return n


def _status() -> int:
    print(f"tumcp {__version__}")
    try:
        asyncio.run(_check())
    except Exception as exc:
        print(f"Not working: {exc}", file=sys.stderr)
        return 1
    print(f"Download folder: {config.download_dir()}")
    return 0


def _logout() -> int:
    credentials.delete()
    shutil.rmtree(data_dir(), ignore_errors=True)
    print("Credentials, app token and session removed. To revoke the TUWEL app token too: "
          "TUWEL > Einstellungen > Sicherheitsschlüssel.")
    return 0


def _setup() -> int:
    print(f"tumcp {__version__} setup\n\n1. TU Wien login")
    creds = credentials.load()
    ok = False
    if creds.complete:
        try:
            asyncio.run(_check())
            ok = True
        except Exception as exc:
            print(f"   The stored login does not work: {exc}")
    if not ok or _yes(f"   Logged in as {creds.username}. Log in again?", False):
        if _login():
            return 1

    print("\n2. Download folder for TUWEL files")
    current = config.download_dir()
    answer = _ask(f"   Folder [{current}]: ")
    folder = Path(answer).expanduser().absolute() if answer else current
    config.save(download_dir=str(folder))
    print(f"   Files go to {folder}/<course>/")

    print("\n3. MCP clients")
    cmd = clients.command()
    found = clients.detect()
    for client in found:
        if _yes(f"   Add tumcp to {client.name}?", True):
            print(f"   {client.name}: {client.register(cmd)}")
    if not found:
        print("   No known MCP client found.")
    print(f"   Other clients: add a stdio server named \"tu\" with the command\n   {cmd}")
    print("\nDone. Restart your MCP clients so they pick up tumcp.")
    return 0


def _update() -> int:
    if (Path(sys.prefix) / "uv-receipt.toml").exists():     # installed with `uv tool install`
        uv = shutil.which("uv")
        if not uv:
            print("uv not found on PATH; run `uv tool upgrade tumcp` yourself.", file=sys.stderr)
            return 1
        code = subprocess.call([uv, "tool", "upgrade", "tumcp"])
        if code == 0:
            print("Restart your MCP clients to use the new version.")
        return code
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / ".git").exists():
        print(f"tumcp runs from the checkout {checkout}; update it with "
              f"`git -C {checkout} pull`.")
        return 0
    print(f"Install with `uv tool install git+{REPO}`, then `tumcp update` works.")
    return 1


def _tool_funcs() -> dict:
    from . import server
    names = [t.name for t in asyncio.run(server.mcp.list_tools())]
    return {n: getattr(server, n) for n in names}


def _tools() -> int:
    for name, fn in _tool_funcs().items():
        doc = " ".join((fn.__doc__ or "").split())
        print(f"{name}{inspect.signature(fn)}\n    {doc}")
    return 0


def _call(name: str, pairs: list[str]) -> int:
    funcs = _tool_funcs()
    if name not in funcs:
        print(f"Unknown tool {name!r}. See `tumcp tools`.", file=sys.stderr)
        return 2
    fn = funcs[name]
    params = inspect.signature(fn).parameters
    kwargs = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or key not in params:
            print(f"Bad argument {pair!r}, expected one of: {', '.join(params)}",
                  file=sys.stderr)
            return 2
        kind = params[key].annotation
        if kind in ("int", int):
            kwargs[key] = int(raw)
        elif kind in ("bool", bool):
            kwargs[key] = raw.lower() in ("1", "true", "yes")
        else:
            kwargs[key] = raw

    async def run():
        from . import server
        try:
            return await fn(**kwargs)
        finally:
            if server._client is not None:
                await server._client.aclose()

    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 1 if isinstance(result, dict) and "error" in result else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tumcp", description=__doc__)
    parser.add_argument("--version", action="version", version=f"tumcp {__version__}")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["serve", "setup", "login", "logout", "status", "update",
                                 "tools", "call"])
    parser.add_argument("tool", nargs="?", help="tool name for `call`")
    parser.add_argument("args", nargs="*", help="key=value arguments for `call`")
    ns = parser.parse_args()
    cmd = ns.command
    if cmd == "serve":
        from .server import run
        run()
        return
    if cmd == "call":
        if not ns.tool:
            parser.error("call needs a tool name, see `tumcp tools`")
        sys.exit(_call(ns.tool, ns.args))
    sys.exit({"setup": _setup, "login": _login, "logout": _logout, "status": _status,
              "update": _update, "tools": _tools}[cmd]())


if __name__ == "__main__":
    main()
