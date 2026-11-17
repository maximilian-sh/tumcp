"""LectureTube recordings in TUWEL (activity type `opencast`).

The activity page lists the episodes (title, duration, date) with links
`view.php?id=<cmid>&e=<episode uuid>`. Each episode page embeds the player data as
`window.episode = {...}`: the streams (`presentation` = slides with the main audio,
`presenter` = camera), each as MP4 in 720p and 1080p plus HLS, and captions (WebVTT,
mostly generated automatically). The files live on cdn.video.tuwien.ac.at and need no
login.

The LectureTube terms of use (§ 12) allow copies only for personal use in the respective
course; tumcp saves them locally and never shares them.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import dom, files
from .session import TUWEL, TuClient, TuError
from .tuwel import _course, course_folder, module_url

VIDEO_MAX_BYTES = 4 * 2**30
EPISODE_JSON = re.compile(r"window\.episode\s*=\s*(\{.*?\})\s*;?\s*(?://\]\]>|</script>)", re.S)
STREAMS = ("presentation", "presenter")
# caption labels look like "de Waas (Automatisch generiert)": Waas is the speech recogniser
AUTO_CAPTIONS = re.compile(r"automatisch|automatic|auto-generated|waas", re.I)
Progress = Callable[[int, int | None, str], Awaitable[None]]


# ------------------------------------------------------------------ parsing
def parse_episode_list(html: str) -> list[dict]:
    """Episodes of an activity page: title, date (YYYY-MM-DD), duration, link."""
    out = []
    for row in dom.select(dom.parse(html), "tr[id*=series-list-view_r]"):
        link = row.css_first("td.c0 a[href]")
        if link is None:
            continue
        date = dom.text(row.css_first("td.c2"))
        m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2})", date)
        out.append({"title": dom.text(link),
                    "date": f"20{m[3]}-{m[2]}-{m[1]}" if m else date or None,
                    "duration": dom.text(row.css_first("td.c1")) or None,
                    "url": link.attributes.get("href")})
    return out


def parse_episode(html: str) -> dict | None:
    """The player data of an episode page, or None if the page shows no recording."""
    m = EPISODE_JSON.search(html)
    if not m:
        return None
    data = json.loads(m.group(1))
    streams: dict[str, dict[int, str]] = {}
    for s in data.get("streams") or []:
        mp4 = (s.get("sources") or {}).get("mp4") or []
        found = {int((x.get("res") or {}).get("h") or 0): x["src"] for x in mp4 if x.get("src")}
        if found:
            streams[s.get("content") or f"stream{len(streams) + 1}"] = found
    meta = data.get("metadata") or {}
    return {"id": meta.get("id"), "title": meta.get("title"), "streams": streams,
            "captions": [{"lang": c.get("lang"), "url": c.get("url"),
                          "auto_generated": bool(AUTO_CAPTIONS.search(c.get("text") or ""))}
                         for c in data.get("captions") or [] if c.get("url")]}


def pick_video(sources: dict[int, str], quality: str) -> str | None:
    """The MP4 in the wanted height, else the next lower one, else the lowest."""
    if not sources:
        return None
    want = int(quality.rstrip("p"))
    lower = [h for h in sources if h <= want]
    return sources[max(lower)] if lower else sources[min(sources)]


def _seconds(stamp: str) -> float:
    parts = [float(p) for p in stamp.replace(",", ".").split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_vtt(text: str, every: int = 60) -> str:
    """WebVTT as readable text: one paragraph per `every` seconds, each starting with
    its time ("[0:03:09] ...")."""
    paragraphs: list[tuple[float, list[str]]] = []
    start, lines = None, []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        rows = [r.strip() for r in block.strip().split("\n") if r.strip()]
        timing = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if timing is None:
            continue
        at = _seconds(rows[timing].split("-->")[0].strip())
        words = " ".join(re.sub(r"<[^>]+>", "", r) for r in rows[timing + 1:]).strip()
        if not words:
            continue
        if start is None or at - start >= every:
            if lines:
                paragraphs.append((start, lines))
            start, lines = at, []
        lines.append(words)
    if lines:
        paragraphs.append((start, lines))
    return "\n".join(f"[{_stamp(at)}] {' '.join(ls)}" for at, ls in paragraphs)


# ------------------------------------------------------------------ fetching
def _ids(url: str) -> tuple[int, str | None]:
    """(activity id, episode id or None) of an opencast link."""
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    if not url.startswith(TUWEL + "/") or parsed.path != "/mod/opencast/view.php" or "id" not in q:
        raise TuError("pass a LectureTube link from tuwel_recordings "
                      "(…/mod/opencast/view.php?id=…&e=…)")
    return int(q["id"][0]), (q.get("e") or [None])[0]


async def recordings(tu: TuClient, course_id: int) -> dict:
    """Every LectureTube activity of a course with its episodes."""
    cid = int(course_id)
    title, sections = await _course(tu, cid)
    out = []
    for sec in sections:
        for m in sec.get("modules") or []:
            if m.get("modname") != "opencast" or not m.get("uservisible", True):
                continue
            url = module_url(m)
            html = await tu.tuwel_get(url)
            episodes = parse_episode_list(html)
            single = parse_episode(html) if not episodes else None
            if single:                                   # activity shows one episode directly
                episodes = [{"title": single["title"], "date": None, "duration": None,
                             "url": url}]
            out.append({"activity": m.get("name"), "section": sec.get("name"), "url": url,
                        "episodes": episodes})
    return {"course": title, "course_id": cid, "recordings": out}


async def _episode(tu: TuClient, url: str) -> dict:
    _ids(url)
    ep = parse_episode(await tu.tuwel_get(url))
    if ep is None:
        raise TuError("no recording on this page; for an activity with several recordings "
                      "pick one from tuwel_recordings")
    return ep


async def transcript(tu: TuClient, url: str, language: str = "") -> dict:
    """The captions of one recording as text with time marks."""
    ep = await _episode(tu, url)
    if not ep["captions"]:
        raise TuError(f"{ep['title']} has no captions")
    lang = language.strip().lower()
    cap = next((c for c in ep["captions"] if lang and (c["lang"] or "").lower() == lang),
               ep["captions"][0])
    text = parse_vtt(await tu.cdn_text(cap["url"]))
    return {"title": ep["title"], "language": cap["lang"],
            "auto_generated": cap["auto_generated"],
            "available_languages": [c["lang"] for c in ep["captions"]], "text": text}


async def download(tu: TuClient, url: str, dest: str, quality: str = "720p",
                   stream: str = "presentation", captions: bool = True,
                   progress: Progress | None = None) -> dict:
    """Save one recording, or all recordings of an activity, as MP4 (+ captions as .vtt)
    into <dest>/<course>/LectureTube/. Files already there are skipped."""
    cmid, episode = _ids(url)
    cm = (await tu.ws("core_course_get_course_module", cmid=cmid)).get("cm") or {}
    found = await tu.ws("core_course_get_courses_by_field", field="id", value=cm.get("course"))
    title = (found.get("courses") or [{}])[0].get("fullname")
    root = Path(dest).expanduser() / course_folder(title, cm.get("course") or 0) / "LectureTube"
    if episode:
        urls = [url]
    else:
        listed = parse_episode_list(await tu.tuwel_get(url))
        urls = [e["url"] for e in listed] or [url]
    wanted = STREAMS if stream == "both" else (stream,)

    plan = []
    for u in urls:
        ep = await _episode(tu, u)
        base = files.safe_name(ep["title"] or ep["id"])
        for name in wanted:
            src = pick_video(ep["streams"].get(name) or {}, quality)
            if src:
                suffix = "" if name == "presentation" else f" - {name}"
                plan.append((f"{base}{suffix}.mp4", src))
        if captions:
            for c in ep["captions"]:
                plan.append((f"{base}.{files.safe_name(c['lang'] or 'captions')}.vtt", c["url"]))

    folder = files.Folder(root)
    result: dict = {"folder": str(root), "new": [], "unchanged": 0, "failed": []}
    try:
        for rel, src in plan:
            if folder.plan(rel, None, None) is None:
                result["unchanged"] += 1
                continue

            async def report(done: int, total: int | None, rel: str = rel) -> None:
                if progress is not None:
                    await progress(done, total, rel)

            try:
                await tu.cdn_download(src, root / rel, VIDEO_MAX_BYTES, report)
            except Exception as exc:                     # keep going with the next file
                result["failed"].append(f"{rel}: {exc}")
                continue
            folder.record(rel, None)
            result["new"].append(rel)
    finally:
        folder.save()
    return {k: v for k, v in result.items() if v or k in ("folder", "unchanged")}
