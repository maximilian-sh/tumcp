import os

from tumcp.client import files
from tumcp.client.files import Folder, file_key, file_links, rel_path, safe_name


def test_safe_name():
    assert safe_name("05.10.2026: Intro") == "05.10.2026 - Intro"
    assert safe_name("../../etc/passwd") == "_.._etc_passwd"
    assert safe_name("a%20b.pdf") == "a b.pdf"
    assert safe_name("") == "_"
    assert rel_path("01 Vorlesungen", None, "", "x.pdf") == "01 Vorlesungen/x.pdf"


def test_file_key_ignores_link_form_and_folder_revision():
    a = "https://tuwel.tuwien.ac.at/pluginfile.php/5088040/mod_folder/content/0/pp26v01.pdf"
    b = ("https://tuwel.tuwien.ac.at/webservice/pluginfile.php/5088040/mod_folder/content/8/"
         "pp26v01.pdf?forcedownload=1")
    assert file_key(a) == file_key(b)


def test_file_links_only_tuwel_files_once():
    html = ('<p><a href="https://tuwel.tuwien.ac.at/pluginfile.php/1/mod_label/intro/a.pdf">A</a>'
            '<a href="https://tuwel.tuwien.ac.at/pluginfile.php/1/mod_label/intro/a.pdf">again</a>'
            '<a href="https://example.com/b.pdf">B</a>'
            '<a href="https://tuwel.tuwien.ac.at/mod/forum/view.php?id=1">forum</a></p>')
    assert [(f["name"], f["label"]) for f in file_links(html)] == [("a.pdf", "A")]


def _write(folder: Folder, rel: str, data: bytes, modified: int | None) -> None:
    out = folder.root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    folder.record(rel, modified)


def test_folder_plan(tmp_path):
    folder = Folder(tmp_path)
    assert folder.plan("x.pdf", 3, 1000) == "new"
    _write(folder, "x.pdf", b"abc", 1000)
    folder.save()

    folder = Folder(tmp_path)                                  # manifest survives
    assert folder.plan("x.pdf", 3, 1000) is None               # up to date
    assert folder.plan("x.pdf", 4, 2000) == "updated"          # newer on TUWEL
    assert folder.plan("x.pdf", 4, 2000, overwrite=True) == "updated"

    (tmp_path / "x.pdf").write_bytes(b"annotated")             # user edited it
    assert folder.plan("x.pdf", 4, 2000) == "kept"
    assert folder.plan("x.pdf", 4, 2000, overwrite=True) == "updated"


def test_folder_plan_adopts_identical_file_and_keeps_foreign_one(tmp_path):
    (tmp_path / "same.pdf").write_bytes(b"abc")
    os.utime(tmp_path / "same.pdf", (1000, 1000))
    (tmp_path / "other.pdf").write_bytes(b"mine")
    folder = Folder(tmp_path)
    assert folder.plan("same.pdf", 3, 1000) is None
    assert folder.plan("other.pdf", 3, 1000) == "kept"


def test_folder_plan_linked_file_without_date(tmp_path):
    folder = Folder(tmp_path)
    assert folder.plan("l.pdf", None, None) == "new"
    _write(folder, "l.pdf", b"x", None)
    assert folder.plan("l.pdf", None, None) is None
    (tmp_path / "l.pdf").write_bytes(b"changed")
    assert folder.plan("l.pdf", None, None) == "kept"


def test_max_bytes_is_sane():
    assert files.MAX_BYTES >= 100 * 2**20
