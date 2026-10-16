"""Read-only TISS queries.

Each query is a fetch (`async def x(tu, ...)`, needs a logged-in TuClient) plus a
pure parser (`parse_x(html)`), so the parsers can be tested against fixtures.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import dom
from .session import TISS, TuClient

TZ = ZoneInfo("Europe/Vienna")
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})")
COURSE_KEY_RE = re.compile(r"courseNr=([0-9A-Za-z]+).*?semester=(\d{4}[WS])"
                           r"|semester=(\d{4}[WS]).*?courseNr=([0-9A-Za-z]+)")
# favourites row icons worth reporting (not "Zum LVA-Forum", "TUWEL", "Verschieben")
STATE_RE = re.compile(r"angemeld|anmeld|warteliste|bestätigung|voranmeld|abgeschlossen|zeugnis|"
                      r"prüfung|registered|waiting|pending|certificate", re.I)
# sections of the course page that matter for planning
COURSE_SECTIONS = ("Merkmale", "Prüfungsmodus", "Terminübersicht", "LVA Termine",
                   "Leistungsnachweis", "Prüfungen", "LVA-Anmeldung", "Gruppen-Anmeldung",
                   "Weitere Informationen")
SECTION_LIMIT = 2500
# group tables list hundreds of "ppA 1 01.10.2026 10:00 31.10.2026 23:59 " rows: keep six
GROUP_ROW = r"\S+(?: \S+)? \d{2}\.\d{2}\.\d{4} \d{2}:\d{2} \d{2}\.\d{2}\.\d{4} \d{2}:\d{2} "
GROUP_ROWS_RE = re.compile(rf"((?:{GROUP_ROW}){{6}})(?:{GROUP_ROW})+")


# ---------------------------------------------------------------- semesters
def current_semester(today: date | None = None) -> str:
    """TU Wien semester code: winter semester Oct–Feb ("2026W"), summer Mar–Sep ("2027S")."""
    today = today or datetime.now(TZ).date()
    if today.month >= 10:
        return f"{today.year}W"
    if today.month <= 2:
        return f"{today.year - 1}W"
    return f"{today.year}S"


def normalize_semester(value: str | None) -> str:
    if not value:
        return current_semester()
    sem = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"\d{4}[WS]", sem):
        raise ValueError(f"semester must look like 2026W or 2027S, not {value!r}")
    return sem


def normalize_course_nr(value: str) -> str:
    """'194.023' -> '194023'."""
    nr = re.sub(r"[^0-9A-Za-z]", "", value or "").upper()
    if not nr:
        raise ValueError(f"not a TISS course number: {value!r}")
    return nr


def _course_key(href: str | None) -> tuple[str, str] | None:
    m = COURSE_KEY_RE.search(href or "")
    if not m:
        return None
    return (m[1], m[2]) if m[1] else (m[4], m[3])


# ----------------------------------------------------------------- messages
async def messages(tu: TuClient, days: int = 14, limit: int = 30) -> list[dict]:
    """TISS messages (LVA announcements, exam reminders, certificates), newest first."""
    html = await tu.tiss_get(f"{TISS}/education/messages.xhtml")
    since = datetime.now(TZ) - timedelta(days=max(1, int(days)))
    return parse_messages(html, since=since, limit=max(1, int(limit)))


def parse_messages(html: str, since: datetime | None = None, limit: int = 0) -> list[dict]:
    """Messages from `form#messagesForm`: h2 "Sender: <a>Subject</a>", div.encode body,
    div.light with the date (dd.mm.yyyy HH:MM, Vienna time) and tag links."""
    out = []
    for li in dom.parse(html).css("form#messagesForm ol > li"):
        h2 = li.css_first("h2")
        if h2 is None:
            continue
        subject = dom.text(h2.css_first("a"))
        head = dom.text(h2)
        sender = head[:head.find(subject)].rstrip(": ").strip() if subject in head else ""
        light = li.css_first("div.light")
        m = DATE_RE.search(dom.text(light))
        when = (datetime(int(m[3]), int(m[2]), int(m[1]), int(m[4]), int(m[5]), tzinfo=TZ)
                if m else None)
        if since and when and when < since:
            continue
        out.append({
            "date": when.strftime("%Y-%m-%d %H:%M") if when else None,
            "from": sender or None,
            "subject": subject or head,
            "text": dom.text(li.css_first("div.encode"), 1500),
            "tags": [dom.text(a) for a in light.css("a")] if light else [],
        })
        if limit and len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- favorites
async def favorites(tu: TuClient) -> list[dict]:
    """Favourite courses grouped by section, with type and registration-state markers."""
    return parse_favorites(await tu.tiss_get(f"{TISS}/education/favorites.xhtml"))


def _row_type(row) -> str | None:
    for span in row.css("span[title]"):
        if span.attributes.get("title") == "Typ":
            return (span.text() or "").strip().strip(",").strip() or None
    return None


def _row_state(row) -> list[str]:
    marks: list[str] = []
    for node in dom.select(row, "img, span[title], i[title], a[title]"):
        mark = (node.attributes.get("title") or node.attributes.get("alt") or "").strip()
        if mark and mark not in marks and STATE_RE.search(mark):
            marks.append(mark)
    return marks


def parse_favorites(html: str) -> list[dict]:
    """Favourites grouped under their `h3.subscriptionGroupTitle` ("5. Semester", "geplant" …).

    The headings are not siblings of the course tables, so sections come from
    document order: every course link belongs to the heading above it. The tree is
    walked node by node, because comma selectors do not give a usable order.
    """
    sections: list[tuple[str, dict[tuple[str, str], dict]]] = []
    current: dict[tuple[str, str], dict] | None = None
    for node in dom.parse(html).root.traverse():
        if node.tag == "h3":
            if "subscriptionGroupTitle" in (node.attributes.get("class") or ""):
                current = {}
                sections.append((dom.text(node), current))
            continue
        if node.tag != "a" or current is None:
            continue
        key = _course_key(node.attributes.get("href"))
        if key is None:
            continue
        entry = current.get(key)
        if entry is None:
            row = dom.ancestor(node, "tr")
            entry = current[key] = {
                "nr": key[0], "semester": key[1], "title": "",
                "type": _row_type(row) if row is not None else None,
                "state": _row_state(row) if row is not None else [],
            }
        if not entry["title"]:
            entry["title"] = dom.text(node)
    return [{"section": head, "courses": list(courses.values())}
            for head, courses in sections if courses]


# ------------------------------------------------------------------- course
async def course(tu: TuClient, course_nr: str, semester: str | None = None) -> dict:
    """Course details: the planning-relevant sections (dates, exams, registration, groups)."""
    nr, sem = normalize_course_nr(course_nr), normalize_semester(semester)
    html = await tu.tiss_get(f"{TISS}/course/educationDetails.xhtml?semester={sem}&courseNr={nr}")
    return parse_course(html, nr, sem)


def page_cancelled(html: str) -> bool:
    """Is the course marked "Abgesagt"?

    The registration block of a cancelled course looks perfectly normal; the only
    marker is a red <span>Abgesagt</span> in the page heading.
    """
    tree = dom.parse(html)
    head = tree.css_first("#contentInner h1") or tree.css_first("h1")
    return head is not None and any(
        (span.text() or "").strip().lower() == "abgesagt" for span in head.css("span"))


def parse_course(html: str, nr: str, sem: str) -> dict:
    tree = dom.parse(html)
    main = tree.css_first("#contentInner") or tree.body
    title = re.split(r"\s\d{4}[WS]\b", dom.text(main.css_first("h1")))[0]   # drop semester switcher
    sections = {}
    # section bodies are not wrapped in an element: cut the markup at every <h2>
    for part in re.split(r"(?=<h2[\s>])", main.html or ""):
        node = dom.parse(part)
        h2 = node.css_first("h2")
        if h2 is None:
            continue
        head = dom.text(h2)
        if head not in COURSE_SECTIONS:
            continue
        h2.decompose()
        body = GROUP_ROWS_RE.sub(r"\1… ", dom.text(node.body))
        sections[head] = dom.clip(body, SECTION_LIMIT)
    return {"course": title or f"{nr} {sem}", "course_nr": nr, "semester": sem,
            "cancelled": page_cancelled(html), "sections": sections}


# ------------------------------------------------------------- certificates
async def certificates(tu: TuClient) -> list[dict]:
    """Issued certificates (Zeugnisse): one entry per course with grade and date."""
    return parse_certificates(await tu.tiss_get(f"{TISS}/graduation/certificates.xhtml"))


def parse_certificates(html: str) -> list[dict]:
    """Rows of every table whose headers include "Titel" and "Note"."""
    out = []
    for table in dom.parse(html).css("table"):
        heads = [dom.text(th) for th in table.css("th")]
        if "Note" not in heads or "Titel" not in heads:
            continue
        for tr in table.css("tr"):
            cells = [dom.text(td) for td in tr.css("td")]
            if len(cells) != len(heads):
                continue
            row = dict(zip(heads, cells, strict=True))
            out.append({"nr": row.get("Nummer"), "type": row.get("Typ"), "title": row.get("Titel"),
                        "ects": row.get("ECTS"), "date": row.get("Prüfungsdatum"),
                        "grade": row.get("Note"), "examiner": row.get("Prüfer_in")})
    return out
