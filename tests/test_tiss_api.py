"""Exam dates and the staff directory from the public TISS API (synthetic data)."""
from tumcp.client.tiss import parse_exam_dates, parse_people

EXAMS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tuvienna xmlns="https://tiss.tuwien.ac.at/api/schemas/exams/v10" version="1.0">
<exam id="2"><title>Test 2  (2027-01-25)</title>
  <examinationBegin>2027-01-25T14:00:00.000+01:00</examinationBegin>
  <applicationEnd>2027-01-21T10:00:00.000+01:00</applicationEnd>
  <deregistrationEnd>2027-01-24T23:59:59.000+01:00</deregistrationEnd></exam>
<exam id="1"><title>Main exam  (2026-11-23)</title>
  <examinationBegin>2026-11-23T13:00:00.000Z</examinationBegin>
  <applicationEnd>2026-11-19T10:00:00.000+01:00</applicationEnd></exam>
</tuvienna>"""


def test_parse_exam_dates():
    assert parse_exam_dates(EXAMS) == [
        {"title": "Main exam", "date": "2026-11-23 14:00",          # UTC -> Vienna
         "registration_until": "2026-11-19 10:00", "deregistration_until": None},
        {"title": "Test 2", "date": "2027-01-25 14:00",
         "registration_until": "2027-01-21 10:00", "deregistration_until": "2027-01-24 23:59"},
    ]


def _person(first, last, employee=True, **extra):
    return {"first_name": first, "last_name": last, "card_uri": f"/person/{last}",
            "main_email": f"{first}.{last}@tuwien.ac.at".lower(),
            "employee": [{"org_ref": {"code": "E194-05", "name_en": "Compilers and Languages"},
                          "display_function": "Lecturer",
                          "room": {"room_code": "HK0419",
                                   "address": {"street": "Favoritenstraße 16",
                                               "zip_code": "1040", "city": "Wien"}}}]
            if employee else [], **extra}


def test_parse_people_staff_only_exact_match_first():
    data = {"results": [
        _person("Eva", "Kalina"),
        _person("Some", "Student", employee=False),
        _person("Ada", "Kallina", preceding_titles="Dr.",
                consultation_hours=["Mo 10:00-11:00, "
                                    "&lt;a href=&quot;x&quot;&gt;HK 04 19&lt;/a&gt;"]),
    ]}
    people = parse_people(data, "Kallina")
    assert [p["name"] for p in people] == ["Dr. Ada Kallina", "Eva Kalina"]
    ada = people[0]
    assert ada["consultation_hours"] == ["Mo 10:00-11:00, HK 04 19"]
    assert ada["positions"] == [{"unit": "Compilers and Languages", "code": "E194-05",
                                 "function": "Lecturer",
                                 "room": "HK0419 (Favoritenstraße 16, 1040 Wien)"}]
    assert ada["url"] == "https://tiss.tuwien.ac.at/person/Kallina"
    assert "phone" not in ada                                   # empty fields are dropped
