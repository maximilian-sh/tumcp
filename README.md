# tumcp

A local [MCP](https://modelcontextprotocol.io) server that lets an AI assistant **read** your
TU Wien accounts: **TUWEL** (Moodle) and **TISS**. Ask your MCP client things like "what is
due this week and have I already submitted it?", "any new TISS messages?" or "when is the
exam for 194.023 and when does registration open?".

> **Status: work in progress.** The client layer (login, session, TUWEL/TISS parsers) exists;
> the MCP server and the `tumcp` command are not finished, and nothing is on PyPI yet. The
> setup below describes how it will work.

> **Unofficial.** Not affiliated with or endorsed by TU Wien. It reads the same pages your
> browser does and can break whenever TISS or TUWEL change. Use at your own risk and within
> TU Wien's IT terms of use.

## Setup

You need [uv](https://docs.astral.sh/uv/); it fetches a suitable Python (3.12+) on its own.

```bash
uvx tumcp login
```

`login` asks for your TU Wien username, password and TOTP secret, shows the current TOTP code
so you can compare it with your authenticator app, test-logs in, and only then stores the
credentials in your OS keychain. Do this once per device.

Then register the server with your MCP client:

**Claude Code**

```bash
claude mcp add --scope user tu -- uvx tumcp
```

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.tu]
command = "uvx"
args = ["tumcp"]
```

**Cursor** (`~/.cursor/mcp.json`) and **Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "tu": { "command": "uvx", "args": ["tumcp"] }
  }
}
```

Desktop apps on macOS often don't see your shell's `PATH`; if the server doesn't start, use the
absolute path from `which uvx` as `command`.

Other commands: `tumcp status` (are the logins working?), `tumcp logout` (remove the stored
credentials and session).

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
| `tuwel_course_contents` | sections and activities of a course, with upload dates |
| `tuwel_grades` | the grade report of a course |
| `tuwel_page` | plain text of one TUWEL page (assignment, quiz, forum thread) |
| `tiss_messages` | TISS messages: course announcements, exam reminders, certificates |
| `tiss_favorites` | favourite courses by section, with registration state |
| `tiss_course` | course details: dates, exams, registration windows, groups |
| `tiss_certificates` | issued certificates with grade and date |

## Security and privacy

- **Local only.** The server runs on your machine over stdio and talks to
  `tiss.tuwien.ac.at`, `tuwel.tuwien.ac.at` and `idp.zid.tuwien.ac.at`, nothing else. There is
  no hosted version, on purpose: it would have to hold other people's passwords and TOTP secrets.
- **Credentials** live in the OS keychain (macOS Keychain, Windows Credential Locker, Secret
  Service on Linux), never in a config file or log. For headless use, the environment
  variables `TUMCP_USERNAME`, `TUMCP_PASSWORD` and `TUMCP_TOTP_SECRET` take precedence.
- **Both factors on one device.** Storing the TOTP secret next to the password stops remote
  attackers, not someone with access to your unlocked user account.
- **Session.** One cookie jar per device in the user data directory (file mode 0600, override
  with `TUMCP_DATA_DIR`), shared by all MCP clients behind a file lock, so several clients don't
  each burn a single-use TOTP code.
- **Read-only.** No tool submits, registers, posts or changes anything. `tuwel_page` refuses
  URLs that could change state (anything carrying a `sesskey`, logout).
- **Your data goes to your model.** Whatever a tool returns (grades, messages, course pages) is
  sent to the model provider your MCP client uses.

## Be gentle with TU servers

TISS and TUWEL are shared infrastructure. tumcp only makes requests when a tool is called, caches
results briefly and never polls in the background. Please don't wrap it in a loop.

## Development

```
src/tumcp/client/   login, session, TUWEL and TISS read functions (no MCP imports)
src/tumcp/server.py thin MCP layer (planned)
src/tumcp/cli.py    tumcp / login / logout / status (planned)
```

`tumcp.client` is usable on its own:

```python
from tumcp.client import tuwel
from tumcp.client.session import TuClient

async with TuClient() as tu:
    print(await tuwel.upcoming(tu, days=7))
```

Every query is split into a fetch and a pure `parse_*` function, so parsers can be tested
against HTML fixtures. Fixtures must be synthetic — never commit real pages with personal data.

## License

[MIT](LICENSE)
