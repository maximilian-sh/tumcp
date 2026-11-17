"""LectureTube parsing, from synthetic pages."""
import json

import pytest

from tumcp.client.lecturetube import _ids, parse_episode, parse_episode_list, parse_vtt, pick_video
from tumcp.client.session import TuError

T = "https://tuwel.tuwien.ac.at/mod/opencast/view.php"
CDN = "https://cdn.video.tuwien.ac.at/static/lecture_tube/engage-player/ep1"

LIST = f"""<table><tbody>
<tr id="mod-opencast-series-list-view_r0">
  <td class="cell c0"><a href="{T}?id=7&amp;e=ep2">Lecture 2</a></td>
  <td class="cell c1">1:45:00</td><td class="cell c2">16.11.26</td><td class="cell c3"></td></tr>
<tr id="mod-opencast-series-list-view_r1">
  <td class="cell c0"><a href="{T}?id=7&amp;e=ep1">Lecture 1</a></td>
  <td class="cell c1">2:00:00</td><td class="cell c2">09.11.26</td><td class="cell c3"></td></tr>
</tbody></table>"""

EPISODE = {
    "metadata": {"id": "ep1", "title": "Lecture 2", "duration": 0},
    "streams": [
        {"content": "presentation", "role": "mainAudio", "sources": {
            "mp4": [{"src": f"{CDN}/a/s-720p.mp4", "res": {"w": 1280, "h": 720}},
                    {"src": f"{CDN}/b/s-1080p.mp4", "res": {"w": 1920, "h": 1080}}],
            "hls": [{"src": f"{CDN}/c/master.m3u8", "master": True}]}},
        {"content": "presenter", "sources": {
            "mp4": [{"src": f"{CDN}/d/p-720p.mp4", "res": {"w": 1280, "h": 720}}]}},
    ],
    "frameList": [],
    "captions": [{"lang": "de", "text": "de Waas (Automatisch generiert)", "format": "vtt",
                  "url": f"{CDN}/e/captions.vtt"}],
}
PAGE = f"<script>\n//<![CDATA[\nwindow.episode = {json.dumps(EPISODE)}\n//]]>\n</script>"


def test_parse_episode_list():
    eps = parse_episode_list(LIST)
    assert [(e["title"], e["date"], e["duration"]) for e in eps] == [
        ("Lecture 2", "2026-11-16", "1:45:00"), ("Lecture 1", "2026-11-09", "2:00:00")]
    assert eps[0]["url"].endswith("&e=ep2")


def test_parse_episode():
    ep = parse_episode(PAGE)
    assert ep["title"] == "Lecture 2"
    assert ep["streams"]["presentation"] == {720: f"{CDN}/a/s-720p.mp4",
                                             1080: f"{CDN}/b/s-1080p.mp4"}
    assert list(ep["streams"]["presenter"]) == [720]
    assert ep["captions"] == [{"lang": "de", "url": f"{CDN}/e/captions.vtt",
                               "auto_generated": True}]
    assert parse_episode("<p>no player here</p>") is None


def test_pick_video():
    sources = {720: "a", 1080: "b"}
    assert pick_video(sources, "1080p") == "b"
    assert pick_video(sources, "720p") == "a"
    assert pick_video({1080: "b"}, "720p") == "b"           # nothing lower: take what exists
    assert pick_video({}, "720p") is None


def test_parse_vtt_groups_by_minute():
    vtt = """WEBVTT

00:00:05.000 --> 00:00:08.000
 Good morning.

1
00:00:30.500 --> 00:00:33.000
<v Speaker>Today: types.</v>

00:01:10.000 --> 00:01:12.000
Next topic.
"""
    assert parse_vtt(vtt) == "[0:00:05] Good morning. Today: types.\n[0:01:10] Next topic."


def test_ids():
    assert _ids(f"{T}?id=7&e=abc") == (7, "abc")
    assert _ids(f"{T}?id=7") == (7, None)
    with pytest.raises(TuError):
        _ids("https://tuwel.tuwien.ac.at/mod/resource/view.php?id=7")
