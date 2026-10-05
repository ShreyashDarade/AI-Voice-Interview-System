import json

import pytest

from interview.resume_parser import ResumeParser, to_legacy
from resume import MaliciousFile, ParseTimeout, ResumeParseError, parse_resume, parse_resume_isolated
from resume.tests import helpers as H
from resume.tests.fixtures import fresher_docx, fresher_txt, midlevel_pdf, senior_two_column_pdf

OLD_KEYS = {"raw_text", "name", "email", "phone", "skills", "education", "work_history", "experience_years", "links"}
NEW_KEYS = {"engine", "integrity", "probe_plan"}


@pytest.fixture()
def pdf_file(tmp_path):
    p = tmp_path / "resume.pdf"
    p.write_bytes(midlevel_pdf()[0])
    return p


def test_shim_returns_old_and_new_keys(pdf_file):
    out = ResumeParser().parse(str(pdf_file))
    assert OLD_KEYS | NEW_KEYS <= set(out)
    assert out["name"] == "Marcus Chen" and out["email"] == "marcus.chen@proton.me" and out["phone"] == "+14155550132"
    assert isinstance(out["raw_text"], str) and "Backend Developer" in out["raw_text"]
    assert isinstance(out["skills"], list) and all(isinstance(s, str) for s in out["skills"])
    assert {"Java", "Python", "Kubernetes"} <= set(out["skills"])
    assert len(set(out["skills"])) == len(out["skills"])


def test_shim_work_history_is_populated_unlike_the_old_parser(pdf_file):
    out = ResumeParser().parse(pdf_file)
    wh = out["work_history"]
    assert len(wh) == 2
    for job in wh:
        assert {"title", "company", "duration", "description"} <= set(job)
        assert job["title"] and job["company"] and job["duration"] and job["description"]
    assert wh[1]["title"] == "Backend Developer" and wh[1]["duration"].endswith("Present")
    assert "Designed GraphQL" in wh[1]["description"]


def test_shim_experience_years_uses_union_months(pdf_file):
    out = ResumeParser().parse(pdf_file)
    assert isinstance(out["experience_years"], float)
    assert out["experience_years"] == round(55 / 12, 1) == 4.6        # overlapping jobs are not double counted (old: 6.2)


def test_shim_education_links_and_json_serialisable(pdf_file):
    out = ResumeParser().parse(pdf_file)
    ed = out["education"][0]
    assert ed["degree"].startswith("B.S.") and "Computer Science" in ed["degree"]
    assert ed["institution"].startswith("University of Illinois") and ed["year"] == "2019"
    assert out["links"][0]["type"] == "github" and out["links"][0]["url"].startswith("https://github.com/")
    json.dumps(out)                                                   # stored in a Django JSONField
    assert "text" not in out["engine"] and out["engine"]["schema_version"] == "1.0"
    assert out["integrity"]["flags"] is not None and out["probe_plan"]["topics"]


def test_shim_other_formats(tmp_path):
    d = tmp_path / "r.docx"
    d.write_bytes(fresher_docx())
    t = tmp_path / "r.txt"
    t.write_text(fresher_txt(), encoding="utf-8")
    for p, name in ((d, "Rahul Verma"), (t, "Ananya Iyer")):
        out = ResumeParser().parse(p)
        assert out["name"] == name and OLD_KEYS <= set(out)


def test_shim_errors_like_before(tmp_path):
    with pytest.raises(FileNotFoundError):
        ResumeParser().parse(str(tmp_path / "missing.pdf"))
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        ResumeParser().parse(str(empty))
    blank = tmp_path / "blank.txt"
    blank.write_text("   \n  \n")
    with pytest.raises(ValueError):
        ResumeParser().parse(str(blank))
    doc = tmp_path / "old.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    with pytest.raises(ValueError):
        ResumeParser().parse(str(doc))
    bad = tmp_path / "evil.pdf"
    bad.write_bytes(H.raw_pdf(b"/OpenAction << /S /Launch /F (cmd.exe) >>"))
    with pytest.raises(ValueError):
        ResumeParser().parse(str(bad))


def test_to_legacy_is_pure_mapping():
    engine = parse_resume(senior_two_column_pdf(), "x.pdf")
    out = to_legacy(engine.to_dict(), engine.text)
    assert out["work_history"][0]["title"] == "Principal Engineer" and out["work_history"][0]["duration"] == "2021-03 - Present"


# ------------------------------------------------------------------ isolated parsing
def test_isolated_parse_matches_in_process_result():
    pdf = senior_two_column_pdf()
    iso = parse_resume_isolated(pdf, "a.pdf", hard_timeout_s=60)
    local = parse_resume(pdf, "a.pdf").to_dict()
    assert json.dumps(iso, sort_keys=True) == json.dumps(local, sort_keys=True)


def test_isolated_accepts_path_and_include_text(tmp_path):
    p = tmp_path / "cv.txt"
    p.write_text(fresher_txt(), encoding="utf-8")
    d = parse_resume_isolated(p, include_text=True, hard_timeout_s=60)
    assert d["contact"]["name"] == "Ananya Iyer" and "Ananya Iyer" in d["text"]
    assert "text" not in parse_resume_isolated(p, hard_timeout_s=60)


def test_isolated_propagates_documented_errors():
    with pytest.raises(MaliciousFile):
        parse_resume_isolated(H.raw_pdf(b"/OpenAction << /S /JavaScript /JS (x) >>"), "x.pdf", hard_timeout_s=60)
    with pytest.raises(MaliciousFile):
        parse_resume_isolated(b"x", "x.pdf", hard_timeout_s=60, _target=H.raiser)


def test_isolated_hard_timeout_kills_hung_worker():
    import time

    t0 = time.monotonic()
    with pytest.raises(ParseTimeout):
        parse_resume_isolated(b"x", "x.pdf", hard_timeout_s=2, _target=H.sleeper)
    assert time.monotonic() - t0 < 20


def test_isolated_survives_worker_crash():
    with pytest.raises(ResumeParseError) as ei:
        parse_resume_isolated(b"x", "x.pdf", hard_timeout_s=30, _target=H.crasher)
    assert "crash" in str(ei.value).lower()
    # the calling process is fine and can parse again
    assert parse_resume(fresher_txt().encode(), "a.txt").contact.name == "Ananya Iyer"


def test_isolated_forwards_limits_to_the_worker():
    from resume import FileTooLarge, Limits

    with pytest.raises(FileTooLarge):
        parse_resume_isolated(b"x" * 100, "a.txt", limits=Limits(max_bytes=10), hard_timeout_s=60)
