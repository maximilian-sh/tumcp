"""Read-only TISS queries.

Each query is a fetch (`async def x(tu, ...)`, needs a logged-in TuClient) plus a
pure parser (`parse_x(html)`), so the parsers can be tested against fixtures.

Course search, exam dates and the staff directory come from TISS's public REST API
(/api/..., documented at /api/dokumentation), the rest from the pages a student sees.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import dom
from .session import TISS, TuClient, TuError

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


# ------------------------------------------------------------------- search
SEARCH_TITLE_RE = re.compile(r"^(?P<nr>[0-9A-Z]{3}\.[0-9A-Z]{3})\s+(?P<type>[A-Z]{2,3})\s+"
                             r"(?P<title>.*?),\s*(?P<semester>\d{4}[WS])$")
# the stock opening of nearly every objectives text
OBJECTIVES_INTRO = re.compile(r"^(Nach positiver Absolvierung der Lehrveranstaltung"
                              r"( sind Studierende in der Lage,?| können Studierende)?|After "
                              r"successful completion of the course,? students are able to)"
                              r"[\s:,]*", re.I)


async def search(tu: TuClient, query: str, limit: int = 25, offset: int = 0) -> dict:
    """Search the course catalogue (public TISS API, full text: titles and descriptions)."""
    query = query.strip()
    if not query:
        raise ValueError("search for something: a course title, keyword or number like 194.023")
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    data = await tu.tiss_api("search/course/v1.0/quickSearch", searchterm=query,
                             count=limit, offset=offset)
    return parse_search(data, offset, query)


def parse_search(data: dict, offset: int = 0, query: str = "") -> dict:
    """quickSearch results: title "194.023 VU Programmierparadigmen, 2026W" plus a
    detail URL and the course objectives as HTML. TISS sorts by course number; courses
    whose title contains the query move to the front of the page."""
    courses = []
    for r in data.get("results") or []:
        m = SEARCH_TITLE_RE.match((r.get("title") or "").strip())
        key = _course_key(r.get("detail_url"))
        courses.append({
            "nr": m["nr"] if m else (key[0] if key else None),
            "type": m["type"] if m else None,
            "title": m["title"] if m else r.get("title"),
            "semester": m["semester"] if m else (key[1] if key else None),
            "summary": dom.clip(OBJECTIVES_INTRO.sub("", dom.strip_html(r.get("short"))), 300)
                       or None,
            "url": r.get("detail_url"),
        })
    q = query.strip().lower()
    courses.sort(key=lambda c: q not in (c["title"] or "").lower())    # stable: number order
    total = data.get("total_results") or 0
    out = {"total": total, "offset": offset, "courses": courses}
    if offset + len(courses) < total:
        out["next_offset"] = offset + len(courses)
    return out


# ------------------------------------------------------------------- course
async def course(tu: TuClient, course_nr: str, semester: str | None = None) -> dict:
    """Course details: the planning-relevant sections (dates, exams, registration, groups)
    plus the upcoming exam dates with their registration deadlines."""
    nr, sem = normalize_course_nr(course_nr), normalize_semester(semester)
    page = await tu.tiss_get(f"{TISS}/course/educationDetails.xhtml?semester={sem}&courseNr={nr}")
    result = parse_course(page, nr, sem)
    try:
        result["upcoming_exams"] = await exam_dates(tu, nr)
    except TuError as exc:                    # the page above is the main answer
        result["upcoming_exams_error"] = str(exc)
    return result


async def exam_dates(tu: TuClient, course_nr: str) -> list[dict]:
    """Future exams of a course (any semester) from the public API, with deadlines."""
    found = await tu.tiss_api(f"course/{normalize_course_nr(course_nr)}/examDates", xml=True)
    return parse_exam_dates(found) if found else []


def _local(stamp: str | None) -> str | None:
    """'2027-01-29T15:00:00.000+01:00' -> '2027-01-29 15:00' (Vienna time)."""
    if not stamp:
        return None
    return datetime.fromisoformat(stamp).astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def parse_exam_dates(text: str) -> list[dict]:
    """<exam id><title>Test 1  (2026-11-23)</title><examinationBegin/><applicationEnd/>
    <deregistrationEnd/></exam>, namespaced; the title repeats the date in brackets."""
    out = []
    for exam in ET.fromstring(text):
        if not exam.tag.endswith("exam"):
            continue
        field = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in exam}
        out.append({
            "title": re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", field.get("title", "")) or None,
            "date": _local(field.get("examinationBegin")),
            "registration_until": _local(field.get("applicationEnd")),
            "deregistration_until": _local(field.get("deregistrationEnd")),
        })
    return sorted(out, key=lambda e: e["date"] or "")


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


# ------------------------------------------------------------------- people
async def people(tu: TuClient, query: str, limit: int = 5) -> list[dict]:
    """Staff directory search: contact data, rooms and consultation hours."""
    query = query.strip()
    if len(query) < 2:
        raise ValueError("search for a name, at least 2 characters")
    found = await tu.tiss_api("person/v23/psuche", q=query, max_treffer=max(1, min(int(limit), 20)))
    return parse_people(found or {}, query)


def _units(person: dict) -> list[dict]:
    out = []
    for job in person.get("employee") or []:
        org = job.get("org_ref") or {}
        room = job.get("room") or {}
        addr = room.get("address") or {}
        place = ", ".join(p for p in (addr.get("street"),
                                      " ".join(filter(None, (addr.get("zip_code"),
                                                             addr.get("city"))))) if p)
        out.append({"unit": org.get("name_en") or org.get("name_de"), "code": org.get("code"),
                    "function": job.get("display_function"),
                    "room": " ".join(filter(None, (room.get("room_code"),
                                                   f"({place})" if place else None))) or None})
    return out


def parse_people(data: dict, query: str = "") -> list[dict]:
    """Results of /api/person/v23/psuche. Only staff (people with a position at TU Wien):
    tumcp is not a tool for looking up students."""
    out = []
    for p in data.get("results") or []:
        if not p.get("employee"):
            continue
        hours = [dom.strip_html(html.unescape(h)) for h in p.get("consultation_hours") or []]
        if p.get("consultation_hour_info"):
            hours.append(dom.strip_html(html.unescape(p["consultation_hour_info"])))
        out.append({
            "name": " ".join(filter(None, (p.get("preceding_titles"), p.get("first_name"),
                                           p.get("last_name"), p.get("postpositioned_titles")))),
            "email": p.get("main_email"),
            "other_emails": p.get("other_emails") or None,
            "phone": p.get("main_phone_number"),
            "consultation_hours": [h for h in hours if h] or None,
            "positions": _units(p),
            "url": f"{TISS}{p['card_uri']}" if p.get("card_uri") else None,
            "_last": (p.get("last_name") or "").lower(),
        })
    q = query.strip().lower()
    out.sort(key=lambda x: not (q and (x["_last"] == q or q in x["name"].lower())))
    return [{k: v for k, v in x.items() if k != "_last" and v is not None} for x in out]
