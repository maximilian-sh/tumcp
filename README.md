# tumcp

A local [MCP](https://modelcontextprotocol.io) server that lets your AI assistant **read** your
TU Wien accounts: **TUWEL** (Moodle) and **TISS**. Ask things like

- "What is due this week, and have I already submitted it?"
- "Any new TISS messages or TUWEL notifications?"
- "When is the exam for 194.023 and when does registration open?"
- "Which courses about security are offered next semester?"
- "Download all slides of Verteilte Systeme." / "What's new in my courses since last week?"

tumcp runs on your own computer: your MCP client (Claude Code, Claude Desktop, Codex, Cursor,
…) starts it when it needs it. There is no hosted service, no account to create and nothing
to run on a server. Your password never leaves your machine except to log in at TU Wien.

> **Unofficial.** Not affiliated with or endorsed by TU Wien. It reads the same pages and the
> same app interface your browser and the Moodle app use, and can break whenever TISS or TUWEL
> change. Use it at your own risk and within TU Wien's IT terms of use.

> **Status: early.** It works and is used daily, but it is young. Used on macOS; the tests
> also run on Linux and Windows, but nobody has used it there yet. Reports welcome.

**Kurz auf Deutsch:** tumcp gibt deinem KI-Assistenten Lesezugriff auf TUWEL und TISS
(Abgaben, Termine, Noten, Nachrichten, Kursunterlagen herunterladen). Es läuft lokal auf deinem
Rechner, die Zugangsdaten liegen im Schlüsselbund deines Betriebssystems. Installation: die zwei
Befehle unter *Quick start*, `tumcp setup` führt durch den Rest. Es kann nichts abgeben, anmelden
oder posten.

## What it can do

- **Deadlines and to-dos:** what is due in TUWEL, whether it is already submitted, quiz
  windows, the full TUWEL calendar.
- **Course material:** every section, activity and file of a course (also tile and tab
  layouts), download single files or folders, or mirror a whole course to your disk and later
  fetch only what is new.
- **Grades:** the grade report of a TUWEL course, the certificates (Zeugnisse) in TISS.
- **News:** TUWEL notifications (forum posts, grading) and TISS messages (announcements, exam
  reminders).
- **TISS course info:** search the whole course catalogue; dates, exams and their
  registration windows, course and group registration, cancelled courses; your favourites
  with registration state.
- **Any TUWEL page as text,** e.g. an assignment or quiz page to check its state.

It can **not** submit, register, tick, post or change anything at TU Wien; see the
[roadmap](#roadmap) for that.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (it brings its own
Python 3.12+):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then install tumcp and run the setup:

```bash
uv tool install git+https://github.com/maximilian-sh/tumcp
tumcp setup
```

`setup` walks you through three steps:

1. **Login.** Your TU Wien username, password and TOTP secret (see
   [below](#where-do-i-get-the-totp-secret)). It shows the current TOTP code so you can compare
   it with your authenticator app, test-logs in, and only then stores everything in your OS
   keychain.
2. **Download folder** for TUWEL files (default `~/Downloads/TUWEL`).
3. **MCP clients.** It offers to register tumcp as server `tu` with the clients it finds
   (Claude Code, Codex, Claude Desktop, Cursor) and prints the command for any other client.

Restart your MCP client and ask it something. That's it.

### Where do I get the TOTP secret?

tumcp logs in without a browser, so it needs the secret behind your authenticator codes, not a
single code. The TU Wien IdP ([idp.zid.tuwien.ac.at](https://idp.zid.tuwien.ac.at)) shows it as
a **base32 key** next to the QR code when you set up an authenticator app. If your
authenticator is already set up and you did not note the key, set up the authenticator again in
the IdP. Scan the new QR code into your normal authenticator app as well, so you keep a working
second factor for yourself.

## Updating

```bash
tumcp update            # runs `uv tool upgrade tumcp`
```

Then restart your MCP clients. `tumcp --version` shows the installed version. A specific
release installs with `uv tool install git+https://github.com/maximilian-sh/tumcp@v0.1.1`.

## Tools

All tools only **read** from TU Wien. Times are Europe/Vienna.

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
| `tuwel_download` | saves a TUWEL file or folder into a local directory |
| `tuwel_sync_course` | mirrors all files of a course into `<download folder>/<course>/`, one folder per section |
| `tiss_messages` | TISS messages: course announcements, exam reminders, certificates |
| `tiss_search` | search the whole course catalogue by title, keyword or number |
| `tiss_favorites` | favourite courses by section, with registration state |
| `tiss_course` | course details: dates, exams, registration windows, groups |
| `tiss_certificates` | issued certificates with grade and date |

### Downloading course files

`tuwel_sync_course` keeps a local copy of a course:

```
~/Downloads/TUWEL/194.023 Programmierparadigmen 2026W/
├── 01 Vorlesungen/Vorlesungsfolien/pp26v01.pdf …
├── 02 Übungsaufgaben/Programmieraufgaben/aufgabe1.pdf …
└── 03 Unterlagen/paradigmen.pdf
```

Running it again downloads only new or changed files. A file you changed locally (for example
an annotated PDF) is never overwritten unless you ask for it (`overwrite`). Each download folder
has a small `.tumcp.json` manifest for this.

## Other commands

| Command | |
|---|---|
| `tumcp status` | version, whether the logins work, download folder |
| `tumcp login` | change the stored credentials |
| `tumcp logout` | remove credentials, app token and session from this device |
| `tumcp tools` | list the tools |
| `tumcp call <tool> key=value ...` | run one tool without an MCP client, JSON on stdout, e.g. `tumcp call tuwel_upcoming days=7` |

### Registering by hand

`tumcp setup` does this for the clients it knows. For others, add a local (stdio) server named
`tu` whose command is the absolute path of `tumcp` (`which tumcp`, on Windows `where tumcp`).
Desktop apps on macOS often don't see your shell's `PATH`, hence the absolute path.

```bash
claude mcp add --scope user tu -- "$(which tumcp)"     # Claude Code
codex mcp add tu -- "$(which tumcp)"                   # Codex
```

Claude Desktop (`claude_desktop_config.json`) and Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "tu": { "command": "/absolute/path/to/tumcp", "args": [] }
  }
}
```

tumcp needs a client that can start local servers. Chat apps that only accept a remote server
URL can't use it, and that is on purpose (see below).

## Troubleshooting

| Problem | What to do |
|---|---|
| `Login failed: the IdP rejected the login` | Wrong password or TOTP secret, or the computer clock is off (TOTP needs the right time). Run `tumcp login` again and compare the shown code with your authenticator app. |
| `no TU Wien credentials configured` | Run `tumcp login` (or `tumcp setup`). |
| The client says the server failed to start | Use the absolute path of `tumcp` as command, then restart the client. `tumcp status` in a terminal shows the actual error. |
| macOS asks whether Python may use the keychain | Choose *Always Allow*. It asks again after an update changed the Python binary. |
| Linux: no keychain backend | Install a Secret Service (GNOME Keyring, KWallet), or set `TUMCP_USERNAME`, `TUMCP_PASSWORD` and `TUMCP_TOTP_SECRET` in the server's environment. |
| A tool returns empty results after a TU update | TISS/TUWEL changed their pages. Please open an issue with the tool name and error, but no screenshots or page dumps with personal data. |

To remove tumcp completely: `tumcp logout`, `uv tool uninstall tumcp`, remove the `tu` entry
from your MCP clients, and if you like revoke the app token in TUWEL under *Einstellungen >
Sicherheitsschlüssel*.

## Security and privacy

- **Local only.** tumcp runs on your machine and talks to `tiss.tuwien.ac.at`,
  `tuwel.tuwien.ac.at` and `idp.zid.tuwien.ac.at`, nothing else (`tumcp update` talks to GitHub
  through uv). There is no hosted version, on purpose: it would have to hold other people's
  passwords and TOTP secrets.
- **Credentials** live in the OS keychain (macOS Keychain, Windows Credential Locker, Secret
  Service on Linux), never in a config file or log. For headless use, the environment
  variables `TUMCP_USERNAME`, `TUMCP_PASSWORD` and `TUMCP_TOTP_SECRET` take precedence.
- **Both factors on one device.** Storing the TOTP secret next to the password protects against
  remote attackers, not against someone with access to your unlocked user account.
- **Session.** One cookie jar per device in the user data directory (file mode 0600, override
  with `TUMCP_DATA_DIR`), shared by all MCP clients behind a file lock, so several clients
  don't each use up a single-use TOTP code.
- **TUWEL app token.** Course contents and files come from TUWEL's web service, the one the
  official Moodle app uses. On first use tumcp gets the app token exactly like the app does
  (your logged-in session opens TUWEL's app launch page) and keeps it in the keychain. TUWEL
  lists it under *Einstellungen > Sicherheitsschlüssel*, where you can revoke it. tumcp calls
  only an explicit list of read functions with it, and sends it in the request body, never in a
  URL.
- **Read-only towards TU Wien.** No tool submits, registers, posts or changes anything there.
  `tuwel_page` refuses URLs that could change state (anything carrying a `sesskey`, logout).
  The download tools write only into your download folder or the folder a tool call names.
  Every tool carries MCP annotations: the reading tools are marked read-only, so your client
  may run them without asking; the two download tools are marked as writing (local files).
- **Your data goes to your model.** Whatever a tool returns (grades, messages, course pages) is
  sent to the model provider your MCP client uses.
- **Course content is written by others.** Forum posts, messages and pages are text from other
  people and can contain instructions aimed at your assistant. tumcp only reads, so such text
  cannot make it change anything at TU Wien, but your client may have other tools; keep its
  confirmation prompts on.

Found a security problem? Please report it privately through GitHub (*Security > Report a
vulnerability*) instead of opening a public issue.

## Be gentle with TU servers

TISS and TUWEL are shared infrastructure. tumcp only makes requests when a tool is called and
never polls in the background; `tuwel_sync_course` downloads only what changed. Please don't
wrap it in a loop or a cron job that hammers TU servers.

## Roadmap

Ideas, roughly in order. Pull requests welcome; open an issue first for bigger ones.

**Reading (fits the current design)**

- [ ] Study progress: curriculum, modules, ECTS done and missing (TISS)
- [ ] Your registrations: courses, groups and exams you are registered for, with status
- [ ] Submission state per assignment and the Kreuzerl (checkmark) you ticked, via the
      TUWEL app interface instead of reading pages
- [ ] Quiz attempts and results
- [ ] Read forum discussions of a course
- [ ] "What's new since last time" across all courses (new files, posts, grades)
- [ ] LectureTube/Opencast recordings of a course
- [ ] Ready-made prompts, e.g. a weekly overview

**Changing things (needs a new, opt-in design)**

- [ ] Tick Kreuzerl (checkmark exercises)
- [ ] Upload a solution to an assignment (as a draft; submitting for grading stays a separate,
      explicit step)
- [ ] Add or remove TISS favourites
- [ ] Register for courses, groups and exams

These would break the "read-only" promise, so they will only come with: separate tools that
are off unless you turn them on in `tumcp setup`, marked as changing data so your client always
asks before running them, a preview of exactly what will happen, and never automatic retries
or timing tricks around registration windows.

**Project**

- [x] Install from GitHub, `tumcp setup`, `tumcp update`
- [x] Tool annotations (read-only, titles), real tool errors, parameter limits, progress
- [x] CI on Linux, macOS and Windows
- [x] Search the TISS course catalogue (`tiss_search`)
- [ ] Publish on PyPI (`uvx tumcp`) and in the official MCP Registry
- [ ] One-click bundle for Claude Desktop (`.mcpb`)
- [ ] Typed output schemas for all tools
- [ ] Parser tests for TISS pages and the grade report

## Contributing

Issues and pull requests are welcome, especially parser fixes when TISS or TUWEL change, and
reports from Linux and Windows.

```
src/tumcp/client/    login, session, TUWEL and TISS read functions, downloads (no MCP imports)
src/tumcp/server.py  thin MCP layer (stdio)
src/tumcp/cli.py     tumcp / setup / login / logout / status / update / tools / call
src/tumcp/clients.py registering with MCP clients (setup)
tests/               pytest, synthetic data only (CI runs them on Linux, macOS, Windows)
```

```bash
git clone https://github.com/maximilian-sh/tumcp && cd tumcp
uv run pytest
uv run tumcp call tuwel_courses          # try a tool against your own account
```

Every query is split into a fetch and a pure `parse_*` function, so parsers can be tested
against HTML or JSON fixtures. **Fixtures must be synthetic: never commit real pages, real
names, matriculation numbers, grades or tokens.** New tools must only read from TU Wien.

`tumcp.client` is usable on its own:

```python
from tumcp.client import tuwel
from tumcp.client.session import TuClient

async with TuClient() as tu:
    print(await tuwel.upcoming(tu, days=7))
```

## License

[MIT](LICENSE)
