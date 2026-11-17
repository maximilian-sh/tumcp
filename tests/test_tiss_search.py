"""TISS course search, from a synthetic quickSearch response."""
from tumcp.client.tiss import parse_search

DATA = {
    "total_results": 3,
    "url": "https://tiss.tuwien.ac.at/course/courseList.xhtml?searchTerm=Systeme",
    "results": [
        {"title": "194.024 VU Verteilte Systeme, 2026W",
         "detail_url": "https://tiss.tuwien.ac.at/course/courseDetails.xhtml"
                       "?courseNr=194024&semester=2026W",
         "short": "<p>Nach positiver Absolvierung k&ouml;nnen Studierende</p><ol><li>A</li></ol>"},
        {"title": "184.194 SE Seminar in Computer Science (Distributed Systems), 2027S",
         "detail_url": "https://tiss.tuwien.ac.at/course/courseDetails.xhtml"
                       "?courseNr=184194&semester=2027S",
         "short": ""},
    ],
}


def test_parse_search():
    out = parse_search(DATA)
    first, second = out["courses"]
    assert (first["nr"], first["type"], first["title"], first["semester"]) == \
        ("194.024", "VU", "Verteilte Systeme", "2026W")
    assert first["summary"] == "Nach positiver Absolvierung können Studierende A"
    assert second["title"] == "Seminar in Computer Science (Distributed Systems)"
    assert second["summary"] is None
    assert out["total"] == 3 and out["next_offset"] == 2


def test_title_matches_first_and_stock_intro_dropped():
    data = {"total_results": 2, "results": [
        {"title": "184.792 PR Bachelorarbeit, 2026W",
         "short": "<p>Nach positiver Absolvierung der Lehrveranstaltung sind Studierende in "
                  "der Lage,</p><p>verteilte Systeme zu bauen</p>"},
        DATA["results"][0]]}
    titles = [c["title"] for c in parse_search(data, query="verteilte systeme")["courses"]]
    assert titles == ["Verteilte Systeme", "Bachelorarbeit"]
    assert parse_search(data)["courses"][0]["summary"] == "verteilte Systeme zu bauen"


def test_parse_search_last_page_and_odd_title():
    url = "https://tiss.tuwien.ac.at/course/courseDetails.xhtml?courseNr=185A12&semester=2026W"
    out = parse_search({"total_results": 1,
                        "results": [{"title": "Something else", "detail_url": url}]})
    course = out["courses"][0]
    assert (course["nr"], course["semester"], course["title"]) == \
        ("185A12", "2026W", "Something else")
    assert "next_offset" not in out
