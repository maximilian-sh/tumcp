"""Course contents and the sync layout, from synthetic core_course_get_contents data."""
from tumcp.client.tuwel import (
    _plain_names,
    course_folder,
    module_url,
    parse_course_contents,
    sync_plan,
)

T = "https://tuwel.tuwien.ac.at"


def _file(ctx, area, name, path="/", size=10, modified=1_800_000_000, rev=1):
    return {"type": "file", "filename": name, "filepath": path, "filesize": size,
            "timemodified": modified,
            "fileurl": f"{T}/webservice/pluginfile.php/{ctx}/{area}/content/{rev}{path}{name}"
                       "?forcedownload=1"}


SECTIONS = _plain_names([
    {"name": "Allgemeines", "summary": "", "modules": [
        {"id": 1, "modname": "label", "name": "Plan", "noviewlink": True,
         "description": f'<a href="{T}/pluginfile.php/20/mod_folder/content/0/v01.pdf">VO 1</a>'
                        f'<a href="{T}/pluginfile.php/30/mod_label/intro/extra.pdf">Extra</a>'},
        {"id": 2, "modname": "forum", "name": "Forum", "url": f"{T}/mod/forum/view.php?id=2"},
    ]},
    {"name": "Vorlesungen: Folien", "summary": "", "modules": [
        {"id": 3, "modname": "folder", "name": "Folien &amp; Code",
         "contents": [_file(20, "mod_folder", "v01.pdf", rev=8),
                      _file(20, "mod_folder", "a.zip", path="/code/", rev=8)]},
        {"id": 4, "modname": "resource", "name": "Skript", "url": f"{T}/mod/resource/view.php?id=4",
         "contents": [_file(40, "mod_resource", "skript.pdf")]},
        {"id": 5, "modname": "resource", "name": "Skript alt",
         "url": f"{T}/mod/resource/view.php?id=5",
         "contents": [_file(50, "mod_resource", "skript.pdf")]},
        {"id": 6, "modname": "assign", "name": "Abgabe", "url": f"{T}/mod/assign/view.php?id=6",
         "uservisible": False, "availabilityinfo": "<b>Ab 1. Dezember</b>",
         "dates": [{"label": "Fällig:", "timestamp": 1_800_000_000}]},
    ]},
])


def test_names_are_unescaped():
    assert SECTIONS[1]["modules"][0]["name"] == "Folien & Code"


def test_module_url_for_inline_folder_and_label():
    assert module_url({"id": 3, "modname": "folder"}) == f"{T}/mod/folder/view.php?id=3"
    assert module_url({"id": 1, "modname": "label", "noviewlink": True}) is None


def test_parse_course_contents():
    out = parse_course_contents(SECTIONS, "194.023 PP (VU 4,0) 2026W", 7)
    label, forum = out["sections"][0]["activities"]
    assert [f["name"] for f in label["files"]] == ["v01.pdf", "extra.pdf"]
    assert "files" not in forum
    folder = out["sections"][1]["activities"][0]
    assert [f["name"] for f in folder["files"]] == ["v01.pdf", "code/a.zip"]
    assert folder["files"][0]["modified"] and folder["files"][0]["size"] == 10
    assign = out["sections"][1]["activities"][3]
    assert assign["restricted"] == "Ab 1. Dezember"
    assert assign["dates"].startswith("Fällig: 20")


def test_sync_plan_layout():
    paths = [p["path"] for p in sync_plan(SECTIONS)]
    assert paths == [
        "01 Vorlesungen - Folien/Folien & Code/v01.pdf",
        "01 Vorlesungen - Folien/Folien & Code/code/a.zip",
        "01 Vorlesungen - Folien/skript.pdf",
        "01 Vorlesungen - Folien/Skript alt/skript.pdf",       # same name: own subfolder
        "00 Allgemeines/extra.pdf",                              # v01.pdf link = folder file
    ]


def test_course_folder():
    assert course_folder("194.023 Programmierparadigmen (VU 4,0) 2026W", 1) == \
        "194.023 Programmierparadigmen 2026W"
    assert course_folder(None, 5) == "TUWEL 5"
