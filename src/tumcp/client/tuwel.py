"""Read-only TUWEL (Moodle) queries.

Each query is a fetch (`async def x(tu, ...)`, needs a logged-in TuClient) plus a
pure parser (`parse_x(...)`) that takes the AJAX result or the page HTML, so the
parsers can be tested against fixtures without network access.

AJAX (/lib/ajax/service.php with the page sesskey) works for
core_calendar_get_action_events_by_timesort (limitnum <= 50),
core_calendar_get_calendar_monthly_view,
core_course_get_enrolled_courses_by_timeline_classification and
message_popup_get_popup_notifications (needs useridto). core_course_get_contents
and gradereport_* are NOT enabled for AJAX, so course and grade pages are parsed
from HTML.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import dom
from .session import TuClient, TuError

TZ = ZoneInfo("Europe/Vienna")
ACTION_EVENTS_MAX = 50          # Moodle rejects a larger limitnum
GRACE = 6 * 3600                # also show what was due in the last few hours
DAY = 86400
# German type label Moodle appends to an activity name ("Skript Datei")
TYPE_SUFFIX = re.compile(r"\s+(Datei|Verzeichnis|Textseite|Link/URL|Aufgabe|Test|Forum|Buch)$")


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


# ----------------------------------------------------------- course page
async def course_contents(tu: TuClient, course_id: int) -> dict:
    """Sections and activities of a course page (names, types, links, dates)."""
    cid = int(course_id)
    return parse_course_contents(await tu.tuwel_get(f"/course/view.php?id={cid}"), cid)


def _is_section(node) -> bool:
    classes = (node.attributes.get("class") or "").split()
    return node.tag == "li" and ("section" in classes or "course-section" in classes)


def _owning_section(node):
    node = node.parent
    while node is not None and not _is_section(node):
        node = node.parent
    return node


def parse_course_contents(html: str, course_id: int) -> dict:
    tree = dom.parse(html)
    title = dom.text(tree.css_first("h1"))
    sections = []
    for sec in dom.select(tree, "li.section, li.course-section"):
        name = dom.text(sec.css_first(".sectionname, h3"))
        summary = dom.text(sec.css_first(".summarytext, .summary"), 600)
        acts = []
        for act in sec.css("li.activity"):
            if _owning_section(act) != sec:      # belongs to a nested subsection
                continue
            mod = re.search(r"modtype_(\w+)", act.attributes.get("class") or "")
            link = act.css_first("a.aalink, .activityname a, a")
            name_node = act.css_first(".instancename, .activityname")
            acts.append({
                "name": TYPE_SUFFIX.sub("", dom.text(name_node)),
                "type": mod.group(1) if mod else None,
                "url": link.attributes.get("href") if link else None,
                "dates": dom.text(act.css_first(
                    ".activity-dates, [data-region=activity-dates]"), 200) or None,
                "info": dom.text(act.css_first(
                    ".activity-altcontent, .contentafterlink, .description"), 400) or None,
            })
        if name or acts:
            sections.append({"section": name, "summary": summary or None, "activities": acts})
    return {"course": title, "course_id": course_id, "sections": sections}


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
