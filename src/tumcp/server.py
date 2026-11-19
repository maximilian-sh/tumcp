"""Thin MCP layer over `tumcp.client`, served over stdio.

Every tool only READS from TU Wien. Nothing here submits, registers, posts or
changes state there. `tuwel_download` and `tuwel_sync_course` write files to the
local disk only.

Failures are raised as `ToolError`, so the client gets `isError: true` and a message
that says what to do next. Each tool carries annotations (read-only, idempotent, talks
to TU Wien), which lets clients approve the read tools without asking every time.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Literal

import httpx2
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, config
from .client import lecturetube, tiss, tuwel
from .client.auth import AuthError
from .client.session import TuClient, TuError

INSTRUCTIONS = """Read-only access to the user's TU Wien accounts (TUWEL = Moodle, TISS =
course system). Use it to check deadlines and whether something is already submitted
(tuwel_upcoming), calendar events (tuwel_calendar), course material (tuwel_course_contents;
files to disk with tuwel_download or a whole course with tuwel_sync_course), recorded
lectures (tuwel_recordings, tuwel_transcript, tuwel_download_recording), grades
(tuwel_grades, tiss_certificates), announcements (tiss_messages, tuwel_notifications) and
course/exam/registration details (tiss_favorites, tiss_course; any course: tiss_search) and
lecturers' contact data (tiss_person).
Times are Europe/Vienna.
These tools cannot submit, register or change anything at TU Wien."""

mcp = MCPServer("tu", instructions=INSTRUCTIONS, version=__version__)
_client: TuClient | None = None
# one INFO line per HTTP request is noise on stderr
for _name in ("httpx", "httpx2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def _read(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=True, destructive_hint=False,
                           idempotent_hint=True, open_world_hint=True)


def _save(title: str) -> ToolAnnotations:
    """Reads TU Wien, writes local files; without `overwrite` it never replaces local edits."""
    return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=False,
                           idempotent_hint=True, open_world_hint=True)


Days = Annotated[int, Field(ge=1, le=365, description="How many days ahead to look.")]
CourseId = Annotated[int, Field(ge=1, description="TUWEL course id, from tuwel_courses.")]
Dest = Annotated[str, Field(description="Local directory. Empty: the download folder the "
                                        "user chose in `tumcp setup`.")]
Overwrite = Annotated[bool, Field(description="Also replace files the user changed locally. "
                                              "Only when the user asks for it.")]


def client() -> TuClient:
    global _client
    if _client is None:
        _client = TuClient()
    return _client


def _explain(exc: Exception) -> str:
    text = str(exc)
    if "tumcp login" in text:
        return f"{text}. The user has to run `tumcp login` (or `tumcp setup`) in a terminal."
    if isinstance(exc, AuthError):
        return (f"TU Wien login failed: {text}. If it keeps failing, the user should check "
                "the stored credentials with `tumcp login`.")
    if isinstance(exc, httpx2.HTTPError):
        return f"TU Wien did not answer ({type(exc).__name__}: {text}). Network down? Try later."
    if isinstance(exc, (TuError, ValueError)):
        return text
    return f"{type(exc).__name__}: {text}"


async def _safe(coro):
    try:
        return await coro
    except Exception as exc:                     # the MCP library logs the failure
        raise ToolError(_explain(exc)) from exc


@mcp.tool(annotations=_read("Check TU Wien login"))
async def tu_status() -> dict:
    """Check that the TISS and TUWEL logins work. Returns the number of current courses."""
    async def run():
        await client().tiss_get("https://tiss.tuwien.ac.at/education/favorites.xhtml")
        cs = await tuwel.courses(client())
        return {"tiss": "ok", "tuwel": "ok", "tuwel_courses": len(cs)}
    return await _safe(run())


@mcp.tool(annotations=_read("Upcoming TUWEL deadlines"))
async def tuwel_upcoming(days: Days = 14) -> list[dict]:
    """TUWEL deadlines and to-dos in the next `days` days, with state: 'open' (still to do),
    'submitted (editable)' (already handed in), 'not open yet', 'info'. Shows at most 50.
    A missing entry does not prove something was done: check it with tuwel_page."""
    return await _safe(tuwel.upcoming(client(), days))


@mcp.tool(annotations=_read("TUWEL calendar"))
async def tuwel_calendar(days: Days = 14) -> list[dict]:
    """All TUWEL calendar events in the next `days` days (deadlines, quiz windows,
    lectures, attendance), sorted by start time. For submission state use tuwel_upcoming."""
    return await _safe(tuwel.calendar(client(), days))


@mcp.tool(annotations=_read("Current TUWEL courses"))
async def tuwel_courses() -> list[dict]:
    """Current (in-progress) TUWEL courses with their course ids, which the other tuwel_*
    tools take as `course_id`."""
    return await _safe(tuwel.courses(client()))


@mcp.tool(annotations=_read("TUWEL notifications"))
async def tuwel_notifications(
        limit: Annotated[int, Field(ge=1, le=50, description="How many, newest first.")] = 20,
) -> dict:
    """Latest TUWEL notifications (forum posts, grading, quiz openings), newest first, with
    the unread count. TISS has its own messages: tiss_messages."""
    return await _safe(tuwel.notifications(client(), limit))


@mcp.tool(annotations=_read("TUWEL course contents"))
async def tuwel_course_contents(course_id: CourseId) -> dict:
    """Sections and activities (files, folders, assignments, quizzes, labels) of one TUWEL
    course, with every file's size and upload date and files linked in texts. Save files
    with tuwel_download or the whole course with tuwel_sync_course."""
    return await _safe(tuwel.course_contents(client(), course_id))


@mcp.tool(annotations=_read("TUWEL grades"))
async def tuwel_grades(course_id: CourseId) -> dict:
    """The grade report (points per item) of one TUWEL course. Final course grades are in
    tiss_certificates."""
    return await _safe(tuwel.grades(client(), course_id))


@mcp.tool(annotations=_read("Read a TUWEL page"))
async def tuwel_page(
        url: Annotated[str, Field(pattern=r"^https://tuwel\.tuwien\.ac\.at/",
                                  description="A tuwel.tuwien.ac.at link, e.g. from "
                                              "tuwel_course_contents or tuwel_upcoming.")],
) -> dict:
    """Plain text of one TUWEL page, e.g. an assignment, checkmark or quiz page to see its
    submission state, or a forum discussion. Refuses links that could change something."""
    return await _safe(tuwel.page(client(), url))


@mcp.tool(annotations=_save("Download TUWEL files"))
async def tuwel_download(
        url: Annotated[str, Field(pattern=r"^https://tuwel\.tuwien\.ac\.at/",
                                  description="Activity link (mod/resource, mod/folder) or "
                                              "file link (pluginfile.php) from "
                                              "tuwel_course_contents.")],
        dest: Dest = "", overwrite: Overwrite = False,
) -> dict:
    """Save one TUWEL file, or every file of a resource or folder (into a subfolder), to a
    local directory and return the paths. Files already there are not fetched again. For a
    whole course use tuwel_sync_course."""
    target = dest or str(config.download_dir())
    return await _safe(tuwel.download(client(), url, target, overwrite))


@mcp.tool(annotations=_save("Sync a TUWEL course to disk"))
async def tuwel_sync_course(course_id: CourseId, dest: Dest = "", overwrite: Overwrite = False,
                            ctx: Context | None = None) -> dict:
    """Mirror all files of one TUWEL course into <dest>/<course>/ (one folder per section)
    and report what is new or updated. Re-running only downloads new or changed files;
    files the user changed locally (e.g. annotated PDFs) are kept."""
    async def progress(done: int, total: int, path: str) -> None:
        if ctx is not None:
            await ctx.report_progress(done, total, path)

    target = dest or str(config.download_dir())
    return await _safe(tuwel.sync_course(client(), course_id, target, overwrite, progress))


RecordingUrl = Annotated[str, Field(pattern=r"^https://tuwel\.tuwien\.ac\.at/mod/opencast/",
                                    description="A recording link (…/mod/opencast/view.php"
                                                "?id=…&e=…) from tuwel_recordings.")]


@mcp.tool(annotations=_read("LectureTube recordings"))
async def tuwel_recordings(course_id: CourseId) -> dict:
    """The LectureTube lecture recordings of one TUWEL course: each recording activity with
    its episodes (title, date, duration, link). Read one with tuwel_transcript, save it
    with tuwel_download_recording."""
    return await _safe(lecturetube.recordings(client(), course_id))


@mcp.tool(annotations=_read("Lecture transcript"))
async def tuwel_transcript(
        url: RecordingUrl,
        language: Annotated[str, Field(description="Caption language like 'de' or 'en'. "
                                                   "Empty: the first available.")] = "",
) -> dict:
    """The spoken text of one recorded lecture, from its captions, as paragraphs with time
    marks ("[0:12:30] ..."). Captions are usually generated automatically
    (`auto_generated`), so names and formulas can be misheard. A 2-hour lecture is about
    15–20k tokens."""
    return await _safe(lecturetube.transcript(client(), url, language))


@mcp.tool(annotations=_save("Download a lecture recording"))
async def tuwel_download_recording(
        url: RecordingUrl,
        quality: Annotated[Literal["720p", "1080p"], Field(
            description="Video height. 720p slides: about 170 MB per lecture hour; "
                        "1080p and the camera stream are larger.")] = "720p",
        stream: Annotated[Literal["presentation", "presenter", "both"], Field(
            description="'presentation' = slides with the audio, 'presenter' = camera.")]
            = "presentation",
        captions: Annotated[bool, Field(description="Also save the captions as .vtt.")] = True,
        dest: Dest = "",
        ctx: Context | None = None,
) -> dict:
    """Save a recorded lecture as MP4 (and its captions as .vtt) into
    <dest>/<course>/LectureTube/. A link without `&e=` saves every recording of that
    activity, which can be several GB. Files already there are skipped. The LectureTube
    terms allow such copies only for personal use in the course: never share them."""
    async def progress(done: int, total: int | None, name: str) -> None:
        if ctx is not None:
            await ctx.report_progress(done, total, name)

    target = dest or str(config.download_dir())
    return await _safe(lecturetube.download(client(), url, target, quality, stream, captions,
                                            progress))


@mcp.tool(annotations=_read("TISS messages"))
async def tiss_messages(
        days: Annotated[int, Field(ge=1, le=365, description="How many days back.")] = 14,
        limit: Annotated[int, Field(ge=1, le=100, description="At most this many.")] = 30,
) -> list[dict]:
    """TISS messages from the last `days` days: course announcements, exam reminders,
    certificate notifications. Newest first. TUWEL has its own: tuwel_notifications."""
    return await _safe(tiss.messages(client(), days, limit))


@mcp.tool(annotations=_read("TISS favourite courses"))
async def tiss_favorites() -> list[dict]:
    """TISS favourite courses grouped by section (the '!' section is the current
    semester), with registration/group state markers per course."""
    return await _safe(tiss.favorites(client()))


@mcp.tool(annotations=_read("Search TISS courses"))
async def tiss_search(
        query: Annotated[str, Field(min_length=2, description="Course title, keyword or "
                                                              "number, e.g. 'Verteilte "
                                                              "Systeme' or '194.024'.")],
        limit: Annotated[int, Field(ge=1, le=100, description="Results per page.")] = 25,
        offset: Annotated[int, Field(ge=0, description="Skip this many results; use "
                                                       "next_offset to page.")] = 0,
) -> dict:
    """Search the TU Wien course catalogue (all courses, not only the user's). Full-text
    over titles and descriptions, sorted by course number, so broad words match many
    courses: prefer the exact title or the number. Returns number, type, title, semester
    and objectives; details with tiss_course."""
    return await _safe(tiss.search(client(), query, limit, offset))


@mcp.tool(annotations=_read("TISS course details"))
async def tiss_course(
        course_nr: Annotated[str, Field(pattern=r"^[0-9A-Za-z]{3}\.?[0-9A-Za-z]{3}$",
                                        description="Course number like '194.023'.")],
        semester: Annotated[str, Field(pattern=r"^(\d{4}[WSws])?$",
                                       description="Like '2026W' or '2027S'. Empty: the "
                                                   "current semester.")] = "",
) -> dict:
    """TISS course details: dates, exam mode, course and group registration, whether it is
    cancelled, and `upcoming_exams` (every future exam date with registration and
    deregistration deadlines)."""
    return await _safe(tiss.course(client(), course_nr, semester or None))


@mcp.tool(annotations=_read("TU Wien staff directory"))
async def tiss_person(
        name: Annotated[str, Field(min_length=2, description="Name of a lecturer or other "
                                                             "TU Wien staff member.")],
        limit: Annotated[int, Field(ge=1, le=20, description="At most this many.")] = 5,
) -> list[dict]:
    """Find TU Wien staff (lecturers, tutors, institute staff) in the TISS address book:
    email, phone, institute, room and consultation hours. Students are not listed."""
    return await _safe(tiss.people(client(), name, limit))


@mcp.tool(annotations=_read("TISS certificates"))
async def tiss_certificates() -> list[dict]:
    """Issued certificates: course, ECTS, date, grade, examiner."""
    return await _safe(tiss.certificates(client()))


def run() -> None:
    # stdout belongs to the MCP protocol: log to stderr only
    logging.basicConfig(level=os.environ.get("TUMCP_LOG", "WARNING"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mcp.run("stdio")
