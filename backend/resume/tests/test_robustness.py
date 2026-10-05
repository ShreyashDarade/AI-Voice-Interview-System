import os
import random
import subprocess
import sys

import pytest

from resume import Limits, ResumeParseError, parse_resume
from resume import contact as contact_mod
from resume.tests.fixtures import fresher_docx, fresher_txt, midlevel_pdf, senior_two_column_pdf


def test_package_does_not_import_django():
    code = ("import sys, resume; from resume import parse_resume, parse_resume_isolated, build_probe_plan;"
            "from resume.tests.fixtures import fresher_txt;"
            "r = parse_resume(fresher_txt().encode(), 'x.txt'); r.to_dict();"
            "assert not [m for m in sys.modules if m.split('.')[0] in ('django', 'rest_framework')], 'django imported'")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    assert out.returncode == 0, out.stderr


def test_stage_failure_becomes_warning_not_exception(monkeypatch):
    monkeypatch.setenv("RESUME_STRICT", "0")
    monkeypatch.setattr(contact_mod, "find_emails", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    r = parse_resume(fresher_txt().encode(), "x.txt")
    assert "stage_failed:contact" in r.warnings
    assert r.experience and r.education and r.skills          # everything else survived
    assert r.contact.email == ""
    assert r.to_dict()["warnings"]


def test_strict_mode_surfaces_bugs(monkeypatch):
    monkeypatch.setenv("RESUME_STRICT", "1")
    monkeypatch.setattr(contact_mod, "find_emails", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        parse_resume(fresher_txt().encode(), "x.txt")


def test_text_and_line_caps():
    body = "Jane Doe\njane@x.org\nEXPERIENCE\n" + "Engineer, Acme Inc.  Jan 2019 - Dec 2019\n" + ("- " + "word " * 2000 + "\n") * 5
    r = parse_resume(body.encode(), "x.txt", limits=Limits(max_line_chars=500, max_text_chars=1500))
    assert "line_truncated" in r.warnings and "text_truncated" in r.warnings
    assert max(len(b) for e in r.experience for b in e.bullets) <= 500


def _mutations(blob: bytes, n: int, seed: int):
    rng = random.Random(seed)
    for _ in range(n):
        b = bytearray(blob)
        mode = rng.choice(["flip", "trunc", "chunk", "insert"])
        if mode == "flip":
            for _ in range(rng.randint(1, 30)):
                b[rng.randrange(len(b))] = rng.randrange(256)
        elif mode == "trunc":
            b = b[: rng.randrange(1, len(b))]
        elif mode == "chunk":
            a = rng.randrange(len(b))
            b[a:a + rng.randint(1, 400)] = b""
        else:
            a = rng.randrange(len(b))
            b[a:a] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 200)))
        yield bytes(b)


@pytest.mark.parametrize("name,blob", [
    ("pdf", senior_two_column_pdf()), ("pdf", midlevel_pdf()[0]), ("docx", fresher_docx()), ("txt", fresher_txt().encode()),
])
def test_mutated_files_never_crash_or_hang(name, blob):
    ok = rejected = 0
    for mutated in _mutations(blob, 25, seed=len(blob)):
        try:
            parse_resume(mutated, f"x.{name}", time_budget_s=20)
            ok += 1
        except ResumeParseError:
            rejected += 1
    assert ok + rejected == 25


def test_token_soup_text_never_raises():
    rng = random.Random(5)
    words = ("Experience Education Skills SUMMARY Software Engineer Acme Inc. University Jan 2019 2020 Present - – to | , • B.Tech MBA "
             "Python R Go C++ SAP Excel led built 40% $2M 6 months Remote Austin TX john@x.com +1 415 555 0132 Gender: Male").split()
    heads = ["EXPERIENCE", "Education", "SKILLS:", "Projects", "Languages", "Work History"]
    for _ in range(60):
        lines = []
        for _ in range(rng.randint(3, 50)):
            lines.append(rng.choice(heads) if rng.random() < 0.12 else " ".join(rng.choice(words) for _ in range(rng.randint(0, 16))))
        try:
            parse_resume("\n".join(lines).encode(), "x.txt")
        except ResumeParseError:
            pass
