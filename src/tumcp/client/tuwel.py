"""Read-only TUWEL (Moodle) queries.

Each query is a fetch (`async def x(tu, ...)`, needs a logged-in TuClient) plus a
pure parser (`parse_x(...)`) that takes the AJAX result or the page HTML, so the
parsers can be tested against fixtures without network access.

AJAX (/lib/ajax/service.php with the page sesskey) works for
core_calendar_get_action_events_by_timesort (limitnum <= 50),
core_calendar_get_calendar_monthly_view,
core_course_get_enrolled_courses_by_timeline_classification and
message_popup_get_popup_notifications (needs useridto). core_course_get_contents
is NOT enabled for AJAX: course contents and files come from the web service with
the Moodle-app token (TuClient.ws). Grade reports and single pages are parsed from
HTML.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx2

from . import dom, files
from .files import rel_path, safe_name
from .session import TUWEL, TuClient, TuError

TZ = ZoneInfo("Europe/Vienna")
ACTION_EVENTS_MAX = 50          # Moodle rejects a larger limitnum
GRACE = 6 * 3600                # also show what was due in the last few hours
DAY = 86400
FILE_MODULES = ("resource", "folder")
MODULE_URL = re.compile(r"/mod/\w+/view\.php\?(?:[^#]*&)?id=(\d+)")


def _days(days: int) -> int:
    return max(1, min(int(days), 365))


def iso(ts: int | float | None) -> str | None:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M") if ts else None


def action_status(action: dict | None) -> str:
    """Rough to-do state from the action button Moodle shows for an event."""
    if not action:
        return "info"
    name = (action.get("name") or "").lower()
    if not action.get("actionable"):
        return "not open yet"
    # "Abgabe bearbeiten" = submitted; "Abgabe hinzufügen", "Test jetzt durchführen" = open
    if any(k in name for k in ("bearbeiten", "edit", "ansehen", "view")):
        return "submitted (editable)"
    return "open"


# ---------------------------------------------------------------- deadlines
async def upcoming(tu: TuClient, days: int = 14) -> list[dict]:
    """Deadlines/actions in the next `days` days, with submission state."""
    now = int(time.time())
    data = await tu.ajax("core_calendar_get_action_events_by_timesort", {
        "timesortfrom": now - GRACE, "timesortto": now + _days(days) * DAY,
        "limitnum": ACTION_EVENTS_MAX})
    return parse_action_events(data)


def parse_action_events(data: dict) -> list[dict]:
    out = []
    for e in data.get("events") or []:
        course = e.get("course") or {}
        action = e.get("action")
        out.append({
            "course": course.get("fullname") or course.get("shortname"),
            "course_id": course.get("id"),
            "name": e.get("name"),
            "due": iso(e.get("timesort")),
            "action": (action or {}).get("name"),
            "status": action_status(action),
            "overdue": bool(e.get("overdue")),
            "url": e.get("url"),
        })
    return out


# ----------------------------------------------------------------- calendar
def months_between(start: float, end: float) -> list[tuple[int, int]]:
    """(year, month) of every month touched by [start, end], local time."""
    a, b = datetime.fromtimestamp(start, TZ), datetime.fromtimestamp(end, TZ)
    year, month, out = a.year, a.month, []
    while (year, month) <= (b.year, b.month):
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


async def calendar(tu: TuClient, days: int = 14) -> list[dict]:
    """All calendar events (incl. lectures, quiz windows) in the next `days` days."""
    now = time.time()
    start, end = now - GRACE, now + _days(days) * DAY
    views = [await tu.ajax("core_calendar_get_calendar_monthly_view",
                           {"year": year, "month": month, "courseid": 1, "categoryid": 0})
             for year, month in months_between(start, end)]
    return parse_monthly_views(views, start, end)


def parse_monthly_views(views: list[dict], start: float, end: float) -> list[dict]:
    """Events of several monthly views between `start` and `end`, deduplicated, sorted."""
    seen, out = set(), []
    for view in views:
        for week in view.get("weeks") or []:
            for day in week.get("days") or []:
                for e in day.get("events") or []:
                    ts = e.get("timestart") or e.get("timesort")
                    if not ts or not start <= ts <= end or e.get("id") in seen:
                        continue
                    seen.add(e.get("id"))
                    course = e.get("course") or {}
                    out.append({
                        "course": course.get("fullname") or "TU Wien",
                        "name": e.get("name"),
                        "start": iso(ts),
                        "duration_min": int((e.get("timeduration") or 0) / 60) or None,
                        "type": e.get("eventtype"),
                        "location": e.get("location") or None,
                        "description": dom.strip_html(e.get("description"), 300) or None,
                        "url": e.get("url"),
                    })
    return sorted(out, key=lambda x: x["start"] or "")


# ------------------------------------------------------------------ courses
async def courses(tu: TuClient) -> list[dict]:
    """Current (in-progress) courses."""
    data = await tu.ajax("core_course_get_enrolled_courses_by_timeline_classification", {
        "classification": "inprogress", "limit": 0, "offset": 0, "sort": "fullname"})
    return parse_courses(data)


def parse_courses(data: dict) -> list[dict]:
    return [{"id": c.get("id"), "name": c.get("fullname"), "short": c.get("shortname"),
             "progress": c.get("progress"), "url": c.get("viewurl")}
            for c in data.get("courses") or []]


# ------------------------------------------------------------ notifications
async def notifications(tu: TuClient, limit: int = 20) -> dict:
    """Latest popup notifications, newest first."""
    data = await tu.ajax("message_popup_get_popup_notifications", {
        "useridto": await tu.userid(), "limit": max(1, min(int(limit), 50)),
        "offset": 0, "newestfirst": True})
    return parse_notifications(data)


def parse_notifications(data: dict) -> dict:
    return {
        "unread": data.get("unreadcount"),
        "notifications": [{
            "time": iso(n.get("timecreated")),
            "read": bool(n.get("read")),
            "subject": n.get("subject"),
            "text": dom.strip_html(n.get("fullmessagehtml") or n.get("smallmessage"), 600),
            "url": n.get("contexturl"),
        } for n in data.get("notifications") or []],
    }


# ---------------------------------------------------------- course contents
def _plain_names(sections: list[dict]) -> list[dict]:
    """The web service sends names HTML-escaped ("TCP &amp; Threads")."""
    for sec in sections:
        sec["name"] = html.unescape(sec.get("name") or "")
        for m in sec.get("modules") or []:
            m["name"] = html.unescape(m.get("name") or "")
    return sections


async def _course(tu: TuClient, cid: int) -> tuple[str | None, list[dict]]:
    sections = _plain_names(await tu.ws("core_course_get_contents", courseid=cid))
    found = await tu.ws("core_course_get_courses_by_field", field="id", value=cid)
    title = (found.get("courses") or [{}])[0].get("fullname")
    return (html.unescape(title) if title else None, sections)


async def course_contents(tu: TuClient, course_id: int) -> dict:
    """Sections and activities of a course, with every file (size, date, link)."""
    cid = int(course_id)
    title, sections = await _course(tu, cid)
    return parse_course_contents(sections, title, cid)


def module_url(m: dict) -> str | None:
    """Link of an activity; folders shown inline on the course page come without one."""
    if m.get("url"):
        return m["url"]
    if m.get("noviewlink") or not m.get("id"):          # labels
        return None
    return f"{TUWEL}/mod/{m.get('modname')}/view.php?id={m['id']}"


def module_files(m: dict) -> list[dict]:
    """Files of a resource or folder: path inside it, size, TUWEL mtime, link."""
    if m.get("modname") not in FILE_MODULES:
        return []
    return [{"path": rel_path(*(c.get("filepath") or "").split("/"), c.get("filename")),
             "size": c.get("filesize"), "modified": c.get("timemodified"),
             "url": c.get("fileurl")}
            for c in m.get("contents") or [] if c.get("type") == "file"]


def _activity(m: dict) -> dict:
    act = {"name": m.get("name"), "type": m.get("modname"), "url": module_url(m)}
    dates = "; ".join(f"{d.get('label')} {iso(d.get('timestamp'))}"
                      for d in m.get("dates") or [])
    if dates:
        act["dates"] = dates
    info = dom.strip_html(m.get("description"), 400)
    if info:
        act["info"] = info
    if not m.get("uservisible", True):
        act["restricted"] = dom.strip_html(m.get("availabilityinfo"), 200) or True
    found = [{"name": f["path"], "size": f["size"], "modified": iso(f["modified"]),
              "url": f["url"]} for f in module_files(m)]
    found += [{"name": f["name"], "url": f["url"]} for f in files.file_links(m.get("description"))]
    if found:
        act["files"] = found
    if m.get("modname") == "url":
        link = next((c.get("fileurl") for c in m.get("contents") or []), None)
        if link:
            act["link"] = link
    return act


def parse_course_contents(sections: list[dict], title: str | None, course_id: int) -> dict:
    out = []
    for sec in sections:
        acts = [_activity(m) for m in sec.get("modules") or []]
        linked = [{"name": f["name"], "url": f["url"]}
                  for f in files.file_links(sec.get("summary"))]
        if sec.get("name") or acts or linked:
            out.append({"section": sec.get("name"),
                        "summary": dom.strip_html(sec.get("summary"), 600) or None,
                        "activities": acts, **({"files": linked} if linked else {})})
    return {"course": title, "course_id": course_id, "sections": out}


# ------------------------------------------------------------------- grades
async def grades(tu: TuClient, course_id: int) -> dict:
    """The user's grade report for one course."""
    cid = int(course_id)
    return parse_grades(await tu.tuwel_get(f"/grade/report/user/index.php?id={cid}"), cid)


def parse_grades(html: str, course_id: int) -> dict:
    tree = dom.parse(html)
    # action menus ("Aktionen", "Bewertungsanalyse") and the item-type label sit inside the cells
    dom.drop(tree, ".action-menu, .dropdown-menu, .sr-only, .visually-hidden, .accesshide, "
                   "th.column-itemname span.small.text-uppercase")
    rows = []
    for tr in tree.css("table.user-grade tr"):
        item = tr.css_first("th.column-itemname, td.column-itemname")
        if item is None:
            continue

        def cell(column: str, tr=tr) -> str | None:
            value = dom.text(tr.css_first(f"td.column-{column}"))
            return None if value in ("", "-", "–") else value

        rows.append({"item": dom.text(item.css_first(".rowtitle")) or dom.text(item),
                     "grade": cell("grade"), "range": cell("range"),
                     "percentage": cell("percentage"), "feedback": cell("feedback")})
    return {"course_id": course_id, "items": rows}


# --------------------------------------------------------------- any page
async def page(tu: TuClient, url: str, limit: int = 6000) -> dict:
    """Plain text of one TUWEL page (activity, assignment status, forum post …)."""
    low = url.lower()
    # every state-changing Moodle GET needs the sesskey; never send one, never log out
    if "sesskey" in low or "/login/logout.php" in low:
        raise TuError("this URL could change state in TUWEL; tumcp is read-only")
    return parse_page(await tu.tuwel_get(url), url, limit)


def parse_page(html: str, url: str, limit: int = 6000) -> dict:
    tree = dom.parse(html)
    main = tree.css_first("#region-main, [role=main], main") or tree.body
    return {"url": url, "title": dom.text(tree.css_first("title")), "text": dom.text(main, limit)}


# ----------------------------------------------------------------- download
def course_folder(title: str | None, course_id: int) -> str:
    """'194.023 Programmierparadigmen (VU 4,0) 2026W' -> '194.023 Programmierparadigmen 2026W'."""
    return safe_name(re.sub(r"\s*\([^)]*\)", "", title or "").strip() or f"TUWEL {course_id}")


def sync_plan(sections: list[dict]) -> list[dict]:
    """Every file of a course once, with its place below the course folder.

    Layout: "<nn> <section>/<file>"; folders and resources with several files get a
    subfolder named after the activity. Files that are only linked in a text (labels,
    section summaries) go next to the section's files. Module files are placed first,
    so a label that links to a folder's file does not produce a second copy.
    """
    plan: list[dict] = []
    seen: set[str] = set()
    taken: set[str] = set()

    def add(where: list, fallback: list, path: str, f: dict) -> None:
        key = files.file_key(f["url"])
        if key in seen:
            return
        seen.add(key)
        rel = "/".join(p for p in (rel_path(*where), path) if p)
        if rel in taken:
            rel = "/".join(p for p in (rel_path(*fallback), path) if p)
        base, n = rel, 2
        while rel in taken:
            stem, dot, ext = base.rpartition(".")
            rel = f"{stem} ({n}).{ext}" if dot and "/" not in ext else f"{base} ({n})"
            n += 1
        taken.add(rel)
        plan.append({"path": rel, "url": f["url"], "size": f.get("size"),
                     "modified": f.get("modified")})

    dirs = [f"{i:02d} {sec.get('name') or 'Abschnitt'}" for i, sec in enumerate(sections)]
    for sdir, sec in zip(dirs, sections, strict=True):
        for m in sec.get("modules") or []:
            found, own = module_files(m), [sdir, m.get("name")]
            for f in found:
                if m.get("modname") == "folder" or len(found) > 1:
                    add(own, own, f["path"], f)
                else:
                    add([sdir], own, f["path"], f)
    for sdir, sec in zip(dirs, sections, strict=True):
        texts = [(m.get("description"), m.get("name")) for m in sec.get("modules") or []]
        for html, owner in [*texts, (sec.get("summary"), None)]:
            for f in files.file_links(html):
                add([sdir], [sdir, owner], safe_name(f["name"]), f)
    return plan


async def _fetch(tu: TuClient, plan: list[dict], root: Path, overwrite: bool) -> dict:
    """Download what the plan lists into `root`, skipping what is already up to date."""
    folder = files.Folder(root)
    result: dict = {"folder": str(root), "new": [], "updated": [], "unchanged": 0,
                    "kept_local_changes": [], "failed": []}
    try:
        for item in plan:
            state = folder.plan(item["path"], item["size"], item["modified"], overwrite)
            if state is None:
                result["unchanged"] += 1
                continue
            if state == "kept":
                result["kept_local_changes"].append(item["path"])
                continue
            if (item["size"] or 0) > files.MAX_BYTES:
                result["failed"].append(f"{item['path']}: larger than "
                                        f"{files.MAX_BYTES // 2**20} MB")
                continue
            try:
                await tu.download(item["url"], root / item["path"], files.MAX_BYTES)
            except (TuError, httpx2.HTTPError, OSError) as exc:
                result["failed"].append(f"{item['path']}: {exc}")
                continue
            folder.record(item["path"], item["modified"])
            result[state].append(item["path"])
    finally:
        folder.save()
    return {k: v for k, v in result.items() if v or k in ("folder", "unchanged")}


async def sync_course(tu: TuClient, course_id: int, dest: str, overwrite: bool = False) -> dict:
    """Mirror every file of a course into `dest`/<course>/. Only new or changed files are
    downloaded; files changed locally are kept unless `overwrite` is set."""
    cid = int(course_id)
    title, sections = await _course(tu, cid)
    root = Path(dest).expanduser() / course_folder(title, cid)
    return {"course": title, **await _fetch(tu, sync_plan(sections), root, overwrite)}


async def download(tu: TuClient, url: str, dest: str, overwrite: bool = False) -> dict:
    """Save one TUWEL file, or every file of a resource or folder, into the directory `dest`.

    `url` is an activity link (mod/resource, mod/folder) or a file link (pluginfile.php).
    A folder gets its own subdirectory. Existing files are kept unless `overwrite` is set.
    """
    root = Path(dest).expanduser()
    path = urlparse(url).path
    if url.startswith(TUWEL + "/") and "/pluginfile.php/" in path:
        item = {"path": safe_name(path.rsplit("/", 1)[-1]), "url": url, "size": None,
                "modified": None}
        return await _fetch(tu, [item], root, overwrite)
    m = MODULE_URL.search(url)
    if not url.startswith(TUWEL + "/") or not m:
        raise TuError("pass a file, folder or pluginfile link from tuwel_course_contents")
    cmid = int(m[1])
    cm = (await tu.ws("core_course_get_course_module", cmid=cmid)).get("cm") or {}
    sections = _plain_names(await tu.ws("core_course_get_contents", courseid=cm.get("course"),
                                        options=[{"name": "cmid", "value": cmid}]))
    mod = next((x for s in sections for x in s.get("modules") or [] if x.get("id") == cmid), {})
    found = module_files(mod)
    if not found:
        raise TuError(f"{cm.get('name') or 'this activity'} has no files to download")
    sub = safe_name(mod.get("name")) if mod.get("modname") == "folder" or len(found) > 1 else ""
    plan = [{**f, "path": "/".join(p for p in (sub, f["path"]) if p)} for f in found]
    return await _fetch(tu, plan, root, overwrite)
