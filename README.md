# tumcp

A local [MCP](https://modelcontextprotocol.io) server that lets an AI assistant **read** your
TU Wien accounts: **TUWEL** (Moodle) and **TISS**. Ask your MCP client things like "what is
due this week and have I already submitted it?", "any new TISS messages?" or "when is the
exam for 194.023 and when does registration open?", or let it download a course's slides.

tumcp runs on your own device: your MCP client starts it when it needs it. There is no
hosted service and nothing to run on a server.

> **Status: early.** Works, installs from GitHub; nothing is on PyPI yet.

> **Unofficial.** Not affiliated with or endorsed by TU Wien. It reads the same pages your
> browser does and can break whenever TISS or TUWEL change. Use at your own risk and within
> TU Wien's IT terms of use.

## Setup

You need [uv](https://docs.astral.sh/uv/); it fetches a suitable Python (3.12+) on its own.

```bash
uv tool install git+https://github.com/maximilian-sh/tumcp
tumcp setup
```

`setup` walks you through three steps:

1. **Login.** It asks for your TU Wien username, password and TOTP secret, shows the current
   TOTP code so you can compare it with your authenticator app, test-logs in, and only then
   stores the credentials in your OS keychain.
2. **Download folder** for TUWEL files (default `~/Downloads/TUWEL`).
3. **MCP clients.** It offers to register tumcp as server `tu` with the clients it finds
   (Claude Code, Codex, Claude Desktop, Cursor) and prints the command for any other client.

Do this once per device, then restart your MCP clients.

**Updates:** `tumcp update` (runs `uv tool upgrade tumcp`), then restart your MCP clients.
`tumcp --version` shows the installed version; a specific release installs with
`uv tool install git+https://github.com/maximilian-sh/tumcp@v0.1.0`.

### Registering by hand

The command is the `tumcp` executable (`which tumcp`); use the absolute path, desktop apps on
macOS often don't see your shell's `PATH`.

```bash
claude mcp add --scope user tu -- "$(which tumcp)"                  # Claude Code
codex mcp add tu -- "$(which tumcp)"                                # Codex
```

Claude Desktop (`claude_desktop_config.json`) and Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "tu": { "command": "/absolute/path/to/tumcp", "args": [] }
  }
}
```

### Other commands

| Command | |
|---|---|
| `tumcp status` | version, are the logins working, download folder |
| `tumcp login` / `tumcp logout` | change or remove the stored credentials, token and session |
| `tumcp update` | update to the latest version from GitHub |
| `tumcp tools` | list the tools |
| `tumcp call <tool> key=value ...` | run one tool without an MCP client, JSON on stdout, e.g. `tumcp call tuwel_upcoming days=7` |

### Where do I get the TOTP secret?

The TU Wien IdP ([idp.zid.tuwien.ac.at](https://idp.zid.tuwien.ac.at)) shows the secret as a
**base32 key** next to the QR code when you set up an authenticator. Note it down and also scan
the QR code into your normal authenticator app, so you keep a working second factor for
yourself.

## Tools

All tools are read-only. Times are Europe/Vienna.

| Tool | What it returns |
|---|---|
| `tu_status` | whether the TISS and TUWEL logins work |
| `tuwel_upcoming` | deadlines in the next days, with state: open, submitted, not open yet |
| `tuwel_calendar` | all calendar events (deadlines, quiz windows, lectures) |
| `tuwel_courses` | current courses with their ids |
| `tuwel_notifications` | latest notifications (forum posts, grading, quiz openings) |
| `tuwel_course_contents` | sections and activities of a course, every file with size and date |
| `tuwel_grades` | the grade report of a course |
| `tuwel_page` | plain text of one TUWEL page (assignment, quiz, forum thread) |
| `tuwel_download` | saves a TUWEL file or folder to a local directory |
| `tuwel_sync_course` | mirrors all files of a course into `<download folder>/<course>/`, one folder per section; re-runs fetch only new or changed files |
| `tiss_messages` | TISS messages: course announcements, exam reminders, certificates |
| `tiss_favorites` | favourite courses by section, with registration state |
| `tiss_course` | course details: dates, exams, registration windows, groups |
| `tiss_certificates` | issued certificates with grade and date |

## Security and privacy

- **Local only.** The server runs on your machine over stdio and talks to
  `tiss.tuwien.ac.at`, `tuwel.tuwien.ac.at` and `idp.zid.tuwien.ac.at`, nothing else (only
  `tumcp update` talks to GitHub, through uv). There is no hosted version, on purpose: it would
  have to hold other people's passwords and TOTP secrets.
- **Credentials** live in the OS keychain (macOS Keychain, Windows Credential Locker, Secret
  Service on Linux), never in a config file or log. For headless use, the environment
  variables `TUMCP_USERNAME`, `TUMCP_PASSWORD` and `TUMCP_TOTP_SECRET` take precedence.
- **Both factors on one device.** Storing the TOTP secret next to the password stops remote
  attackers, not someone with access to your unlocked user account.
- **Session.** One cookie jar per device in the user data directory (file mode 0600, override
  with `TUMCP_DATA_DIR`), shared by all MCP clients behind a file lock, so several clients don't
  each burn a single-use TOTP code.
- **TUWEL app token.** Course contents and files come from TUWEL's web service, the one the
  official Moodle app uses. On first use tumcp gets the app token exactly like the app does
  (your logged-in session opens TUWEL's app launch page) and keeps it in the keychain. TUWEL
  lists it under *Einstellungen > Sicherheitsschlüssel*, where you can revoke it; tumcp then
  fetches a new one. It only calls an explicit list of read functions with it, and sends it
  in the request body, never in a URL.
- **Read-only.** No tool submits, registers, posts or changes anything. `tuwel_page` refuses
  URLs that could change state (anything carrying a `sesskey`, logout).
- **Downloads** go only into the folder you chose (or the `dest` a tool call names). Each
  download folder has a `.tumcp.json` manifest; a re-sync fetches only new or changed files
  and never overwrites a file you changed locally (e.g. an annotated PDF) unless asked to.
- **Your data goes to your model.** Whatever a tool returns (grades, messages, course pages) is
  sent to the model provider your MCP client uses.

## Be gentle with TU servers

TISS and TUWEL are shared infrastructure. tumcp only makes requests when a tool is called and
never polls in the background; `tuwel_sync_course` downloads only what changed. Please don't
wrap it in a loop.

## Development

```
src/tumcp/client/   login, session, TUWEL and TISS read functions, downloads (no MCP imports)
src/tumcp/server.py thin MCP layer (stdio)
src/tumcp/cli.py    tumcp / setup / login / logout / status / update / tools / call
src/tumcp/clients.py registering with MCP clients (setup)
tests/              pytest, synthetic data only: uv run pytest
```

`tumcp.client` is usable on its own:

```python
from tumcp.client import tuwel
from tumcp.client.session import TuClient

async with TuClient() as tu:
    print(await tuwel.upcoming(tu, days=7))
```

Every query is split into a fetch and a pure `parse_*` function, so parsers can be tested
against HTML or JSON fixtures. Fixtures must be synthetic — never commit real pages with personal data.

## License

[MIT](LICENSE)
