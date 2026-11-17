"""Thin MCP layer over `tumcp.client`, served over stdio.

Every tool only READS from TU Wien. Nothing here submits, registers, posts or
changes state there. `tuwel_download` and `tuwel_sync_course` write files to the
local disk only.
"""
from __future__ import annotations

import logging
import os
import sys

from mcp.server.mcpserver import MCPServer

from . import __version__, config
from .client import tiss, tuwel
from .client.session import TuClient

log = logging.getLogger("tumcp")

INSTRUCTIONS = """Read-only access to the user's TU Wien accounts (TUWEL = Moodle, TISS =
course system). Use it to check deadlines and whether something is already submitted
(tuwel_upcoming), calendar events (tuwel_calendar), course material (tuwel_course_contents;
files to disk with tuwel_download or a whole course with tuwel_sync_course), grades
(tuwel_grades, tiss_certificates), announcements (tiss_messages, tuwel_notifications) and
course/exam/registration details (tiss_favorites, tiss_course). Times are Europe/Vienna.
These tools cannot submit, register or change anything at TU Wien."""

mcp = MCPServer("tu", instructions=INSTRUCTIONS, version=__version__)
_client: TuClient | None = None
# one INFO line per HTTP request is noise on stderr
for _name in ("httpx", "httpx2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def client() -> TuClient:
    global _client
    if _client is None:
        _client = TuClient()
    return _client


async def _safe(coro):
    try:
        return await coro
    except Exception as exc:  # report, never crash the tool call
        log.warning("tool failed: %s: %s", type(exc).__name__, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
async def tu_status() -> dict:
    """Check that the TISS and TUWEL logins work. Returns the course count."""
    async def run():
        await client().tiss_get("https://tiss.tuwien.ac.at/education/favorites.xhtml")
        cs = await tuwel.courses(client())
        return {"tiss": "ok", "tuwel": "ok", "tuwel_courses": len(cs)}
    return await _safe(run())


@mcp.tool()
async def tuwel_upcoming(days: int = 14) -> list[dict] | dict:
    """TUWEL deadlines/actions in the next `days` days with state: 'open' (still to do),
    'submitted (editable)' (already handed in), 'not open yet', 'info'."""
    return await _safe(tuwel.upcoming(client(), days))


@mcp.tool()
async def tuwel_calendar(days: int = 14) -> list[dict] | dict:
    """All TUWEL calendar events in the next `days` days (deadlines, quiz windows,
    lectures, attendance), sorted by start time."""
    return await _safe(tuwel.calendar(client(), days))


@mcp.tool()
async def tuwel_courses() -> list[dict] | dict:
    """Current (in-progress) TUWEL courses with their course ids."""
    return await _safe(tuwel.courses(client()))


@mcp.tool()
async def tuwel_notifications(limit: int = 20) -> dict:
    """Latest TUWEL notifications (forum posts, grading, quiz openings), newest first."""
    return await _safe(tuwel.notifications(client(), limit))


@mcp.tool()
async def tuwel_course_contents(course_id: int) -> dict:
    """Sections and activities (files, folders, assignments, quizzes, labels) of one TUWEL
    course, with every file's size and upload date and files linked in texts. Get ids from
    tuwel_courses; save files with tuwel_download or tuwel_sync_course."""
    return await _safe(tuwel.course_contents(client(), course_id))


@mcp.tool()
async def tuwel_grades(course_id: int) -> dict:
    """The grade report (points per item) of one TUWEL course."""
    return await _safe(tuwel.grades(client(), course_id))


@mcp.tool()
async def tuwel_page(url: str) -> dict:
    """Plain text of one TUWEL page, e.g. an assignment/checkmark/quiz page to see its
    submission state or a forum discussion. Only tuwel.tuwien.ac.at URLs."""
    return await _safe(tuwel.page(client(), url))


@mcp.tool()
async def tuwel_download(url: str, dest: str = "", overwrite: bool = False) -> dict:
    """Save TUWEL files to a local directory and return their paths. `url` is an activity
    link (mod/resource; mod/folder saves every file into a subfolder) or a file link
    (pluginfile.php) from tuwel_course_contents. `dest` is a local directory (default: the
    download folder from `tumcp setup`). Files already there are not fetched again; files
    changed locally are kept unless `overwrite` is true."""
    target = dest or str(config.download_dir())
    return await _safe(tuwel.download(client(), url, target, overwrite))


@mcp.tool()
async def tuwel_sync_course(course_id: int, dest: str = "", overwrite: bool = False) -> dict:
    """Mirror all files of one TUWEL course into <dest>/<course>/ (one folder per section)
    and report what is new or updated. Re-running only downloads new or changed files;
    files changed locally (e.g. annotated PDFs) are kept unless `overwrite` is true.
    `dest` defaults to the download folder from `tumcp setup`."""
    target = dest or str(config.download_dir())
    return await _safe(tuwel.sync_course(client(), course_id, target, overwrite))


@mcp.tool()
async def tiss_messages(days: int = 14, limit: int = 30) -> list[dict] | dict:
    """TISS messages from the last `days` days: course announcements, exam reminders,
    certificate notifications. Newest first."""
    return await _safe(tiss.messages(client(), days, limit))


@mcp.tool()
async def tiss_favorites() -> list[dict] | dict:
    """TISS favourite courses grouped by section (the '!' section is the current
    semester), with registration/group state markers per course."""
    return await _safe(tiss.favorites(client()))


@mcp.tool()
async def tiss_course(course_nr: str, semester: str = "") -> dict:
    """TISS course details as text: dates, exams with registration windows, course and
    group registration info. course_nr like '194.023'; semester like '2026W' (default:
    the current one)."""
    return await _safe(tiss.course(client(), course_nr, semester or None))


@mcp.tool()
async def tiss_certificates() -> list[dict] | dict:
    """Issued certificates (Zeugnisse): course, date, grade."""
    return await _safe(tiss.certificates(client()))


def run() -> None:
    # stdout belongs to the MCP protocol: log to stderr only
    logging.basicConfig(level=os.environ.get("TUMCP_LOG", "WARNING"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mcp.run("stdio")
