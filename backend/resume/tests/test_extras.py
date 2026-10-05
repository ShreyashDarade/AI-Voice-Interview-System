import pytest

from resume import parse_resume


def parse(sections):
    return parse_resume(("Pat Lee\npat.lee@mail.com\n\n" + sections).encode(), "cv.txt")


def test_certifications_name_issuer_year():
    r = parse("""CERTIFICATIONS
- AWS Certified Solutions Architect - Associate, Amazon Web Services, 2022
- Google Professional Data Engineer (Google Cloud) 2021
- Certified Scrum Master | Scrum Alliance | Mar 2020
- Machine Learning Specialization by Coursera, 2019
- Kubernetes Administrator (CKA)
""")
    c = {x.name: x for x in r.certifications}
    assert len(r.certifications) == 5
    first = r.certifications[0]
    assert "Solutions Architect" in first.name and first.issuer in ("Amazon Web Services", "AWS") and first.year == 2022
    assert r.certifications[1].year == 2021 and r.certifications[1].issuer == "Google Cloud"
    assert r.certifications[2].issuer == "Scrum Alliance" and r.certifications[2].year == 2020
    assert r.certifications[3].issuer == "Coursera" and r.certifications[3].year == 2019
    assert r.certifications[4].year is None


def test_certification_continuation_lines_joined():
    r = parse("CERTIFICATIONS\n- Professional Cloud Architect\n  certification from Google, 2023\n- Terraform Associate, HashiCorp, 2022\n")
    assert len(r.certifications) == 2 and r.certifications[0].year == 2023


def test_projects_name_description_skills_links():
    r = parse("""PROJECTS
Smart Inventory System | Python, Django, PostgreSQL
- Built stock-tracking web app used by 5 warehouses
- Source: https://github.com/patlee/inventory
Weather Bot (Node.js, Telegram API)
- Sends daily forecasts using Redis for caching
Tech Stack: Docker, AWS
""")
    assert [p.name for p in r.projects] == ["Smart Inventory System", "Weather Bot"]
    p0, p1 = r.projects
    assert {"Python", "Django", "PostgreSQL"} <= set(p0.skills)
    assert p0.links == ["https://github.com/patlee/inventory"]
    assert "stock-tracking" in p0.description
    assert {"Node.js", "Redis", "Docker", "AWS"} <= set(p1.skills)


def test_languages_with_proficiency_and_programming_languages_relabelled_as_skills():
    r = parse("LANGUAGES\nEnglish (Fluent), Hindi - Native | German: B1\n")
    assert [(l.name, l.proficiency) for l in r.languages] == [("English", "fluent"), ("Hindi", "native"), ("German", "conversational")]
    r = parse("LANGUAGES\nPython, Java, C++, SQL\n")
    assert r.languages == [] and {"Python", "Java", "C++", "SQL"} <= {s.name for s in r.skills}
    assert any(s["name"] == "skills" for s in r.sections_detected)


def test_summary_from_summary_section_and_fallback():
    r = parse("PROFILE\nBackend engineer who loves distributed systems.\nSecond line.\nEXPERIENCE\nDev, Alpha Inc.  Jan 2020 - Dec 2020\n- x\n")
    assert r.summary == "Backend engineer who loves distributed systems. Second line."
    r = parse_resume(b"Pat Lee\npat@x.com\nA seasoned backend engineer with a long record of shipping reliable distributed systems across fintech companies\nSKILLS\nPython\n", "x.txt")
    assert r.summary.startswith("A seasoned backend engineer")


def test_soft_skills_collected_separately():
    r = parse("SKILLS\nPython, Leadership, Communication, Teamwork\n")
    assert {"Leadership", "Communication", "Teamwork"} == {s.name for s in r.soft_skills}
    assert "Leadership" not in {s.name for s in r.skills}
