import json
import time

import pytest

from resume import SCHEMA_VERSION, parse_resume
from resume.tests.fixtures import (
    fresher_docx, fresher_txt, midlevel_pdf, months_ago, senior_two_column_pdf, ym_months_ago,
)
from resume.util import current_ym, ym


def skill_map(r):
    return {s.name: s for s in r.skills}


# ================================================================== 1. fresher with internships + projects (DOCX)
@pytest.fixture(scope="module")
def fresher():
    return parse_resume(fresher_docx(), "rahul.docx")


def test_fresher_contact(fresher):
    c = fresher.contact
    assert c.name == "Rahul Verma"
    assert c.email == "rahul.verma@university.edu"
    assert c.phone == "+919123456780" and c.phones[0].raw == "+91 91234 56780"
    assert c.location == "Pune, Maharashtra"
    assert {l.type: l.url for l in c.links} == {"github": "https://github.com/rahulv", "linkedin": "https://linkedin.com/in/rahul-verma-8a1b2c"}


def test_fresher_experience_is_internships_with_exact_months(fresher):
    assert [(e.title, e.company, e.start, e.end, e.employment_type) for e in fresher.experience] == [
        ("Software Engineering Intern", "Zoho Corporation Pvt Ltd", "2023-06", "2023-08", "internship"),
        ("Data Analyst Intern", "Mu Sigma", "2022-12", "2023-01", "internship"),
    ]
    assert [e.duration_months for e in fresher.experience] == [3, 2]
    assert fresher.total_experience_months == 5 and fresher.total_experience_years == pytest.approx(0.42, abs=0.01)
    assert fresher.seniority_estimate == "fresher"
    assert len(fresher.experience[0].bullets) == 2
    assert {"Java", "Spring Boot", "MySQL", "JUnit"} <= set(fresher.experience[0].skills)
    assert {"Python", "Pandas"} <= set(fresher.experience[1].skills)


def test_fresher_education_projects_certs_languages(fresher):
    be, hsc = fresher.education
    assert (be.degree_level, be.field, be.institution, be.start_year, be.end_year, be.gpa, be.gpa_scale) == (
        "bachelor", "Computer Engineering", "Savitribai Phule Pune University", 2020, 2024, 8.9, 10.0)
    assert (hsc.degree_level, hsc.institution, hsc.end_year, hsc.gpa, hsc.gpa_scale) == ("high_school", "Fergusson College", 2020, 91.2, 100.0)
    assert [p.name for p in fresher.projects] == ["Smart Attendance System", "Library Management App"]
    assert {"Python", "OpenCV", "Flask", "Docker"} <= set(fresher.projects[0].skills)
    assert {"Java", "MySQL"} <= set(fresher.projects[1].skills)
    assert fresher.certifications[0].issuer in ("AWS", "Amazon Web Services") and fresher.certifications[0].year == 2023
    assert [(l.name, l.proficiency) for l in fresher.languages] == [("English", "fluent"), ("Hindi", "native"), ("Marathi", "")]
    assert fresher.summary.startswith("Final year computer engineering student")


def test_fresher_skills_and_per_skill_months(fresher):
    sm = skill_map(fresher)
    assert {"Java", "Python", "C", "SQL", "Git", "Docker", "Linux", "Postman", "Spring Boot", "OpenCV"} <= set(sm)
    assert "R" not in sm and "Go" not in sm
    assert sm["Java"].experience_months == 3 and sm["Python"].experience_months == 2
    assert sm["Docker"].experience_months == 0 and sm["Docker"].evidence == "projects"
    assert sm["Git"].evidence == "skills_section" and sm["C"].evidence == "skills_section"
    assert sm["Java"].evidence == "experience"
    assert sm["Spring"].implied and not sm["Spring Boot"].implied


def test_fresher_sections_privacy_and_flags(fresher):
    names = [s["name"] for s in fresher.sections_detected]
    assert names[0] == "header" and {"summary", "education", "experience", "projects", "skills", "certifications", "languages"} <= set(names)
    assert {"date_of_birth", "gender", "nationality"} <= set(fresher.redacted_fields)
    assert "2002" not in fresher.text
    assert fresher.overall_confidence >= 0.8
    assert fresher.integrity.risk_score == 0.0 or all(f.severity == "info" for f in fresher.integrity.flags)


# ================================================================== 2. mid-level backend dev with overlapping roles (PDF)
@pytest.fixture(scope="module")
def mid():
    pdf, exp = midlevel_pdf()
    return parse_resume(pdf, "marcus.pdf"), exp


def test_mid_contact(mid):
    r, _ = mid
    assert (r.contact.name, r.contact.email, r.contact.phone, r.contact.location) == (
        "Marcus Chen", "marcus.chen@proton.me", "+14155550132", "San Francisco, CA")
    assert r.contact.links[0].type == "github"
    assert r.source.format == "pdf" and r.source.pages == 1 and r.source.extraction_method == "pymupdf-dict"


def test_mid_roles_dates_and_union_of_overlap(mid):
    r, exp = mid
    a, b = r.experience
    assert (a.title, a.company, a.location) == ("Software Developer", "Pied Piper Inc.", "Palo Alto, CA")
    assert (b.title, b.company, b.location) == ("Backend Developer", "Initech LLC", "San Francisco, CA")
    assert (a.start, a.end) == exp["a"] and b.start == exp["b_start"] and b.is_current and b.end is None
    # A: 54..24 months ago (31 inclusive); B: 30 months ago..today (31 inclusive); overlap 7 -> union 55
    assert (a.duration_months, b.duration_months) == (31, 31)
    assert r.total_experience_months == 55 and r.seniority_estimate == "mid"
    assert r.total_experience_years == pytest.approx(55 / 12, abs=0.01)


def test_mid_overlap_is_warning_and_flag_not_error(mid):
    r, _ = mid
    assert "timeline_overlap_fulltime" in r.warnings
    f = r.integrity.get("timeline_overlap_fulltime")
    assert f.severity == "low" and f.details["pairs"][0]["months"] == 7


def test_mid_skills_attributed_to_the_right_job(mid):
    r, _ = mid
    a, b = r.experience
    assert {"Java", "Spring Boot", "PostgreSQL", "Apache Kafka", "Jenkins"} <= set(a.skills)
    assert {"Python", "FastAPI", "GraphQL", "AWS", "Terraform"} <= set(b.skills)
    assert "Java" not in b.skills and "Python" not in a.skills
    sm = skill_map(r)
    assert sm["Java"].experience_months == 31 and sm["Python"].experience_months == 31
    assert sm["Go"].experience_months == 0 and sm["Go"].evidence == "skills_section"
    assert sm["Apache Kafka"].experience_months == 31
    assert r.education[0].degree_level == "bachelor" and r.education[0].field == "Computer Science"


def test_mid_claims_and_plan(mid):
    r, _ = mid
    kinds = {c.kind for c in r.probe_plan.verification_claims}
    assert {"percentage", "scale"} <= kinds
    assert r.probe_plan.seniority == "mid"
    assert any(t.reason_code == "integrity_flag_verification" for t in r.probe_plan.topics)


# ================================================================== 3. senior, two columns, promotions at the same company
@pytest.fixture(scope="module")
def senior():
    return parse_resume(senior_two_column_pdf(), "elena.pdf")


def test_senior_contact_from_sidebar_and_header(senior):
    c = senior.contact
    assert c.name == "Elena Petrova" and c.email == "elena.petrova@mail.com" and c.phone == "+442079460958"
    assert c.location == "London, United Kingdom"
    assert c.links[0].type == "linkedin"


def test_senior_reading_order_sections(senior):
    names = [s["name"] for s in senior.sections_detected]
    assert names == ["header", "contact", "skills", "education", "languages", "summary", "experience"]
    assert senior.summary.startswith("Principal engineer and manager with 14 years")


def test_senior_promotions_same_company(senior):
    rows = [(e.title, e.company, e.start, e.end) for e in senior.experience]
    assert rows == [
        ("Principal Engineer", "Globex Corporation", "2021-03", None),
        ("Senior Software Engineer", "Globex Corporation", "2017-06", "2021-02"),
        ("Software Engineer", "Globex Corporation", "2014-08", "2017-05"),
        ("Software Developer", "Initech Ltd", "2010-09", "2014-07"),
    ]
    assert senior.experience[0].location == "London" and senior.experience[3].location == "Sofia"
    assert senior.experience[0].is_current
    # contiguous roles -> union equals the whole span, no overlap warning
    assert senior.total_experience_months == current_ym() - ym(2010, 9) + 1
    assert senior.seniority_estimate == "lead"
    assert "timeline_overlap_fulltime" not in senior.warnings
    assert [len(e.bullets) for e in senior.experience] == [2, 1, 1, 1]


def test_senior_education_from_sidebar(senior):
    ms, bs = senior.education
    assert (ms.degree_level, ms.field, ms.institution, ms.start_year, ms.end_year) == ("master", "Computer Science", "Imperial College London", 2008, 2010)
    assert (bs.degree_level, bs.field, bs.institution, bs.start_year, bs.end_year) == ("bachelor", "Mathematics", "University of Sofia", 2004, 2008)
    assert [(l.name, l.proficiency) for l in senior.languages] == [("English", "fluent"), ("Bulgarian", "native")]


def test_senior_skills_months_and_leadership_plan(senior):
    sm = skill_map(senior)
    assert sm["Java"].experience_months == ym(2021, 2) - ym(2010, 9) + 1       # roles that mention Java: 2010-09 .. 2021-02
    assert sm["Kotlin"].experience_months == ym(2021, 2) - ym(2017, 6) + 1
    assert sm["Apache Kafka"].experience_months == current_ym() - ym(2021, 3) + 1
    reasons = {t.reason_code for t in senior.probe_plan.topics}
    assert "leadership_claim" in reasons and "recent_primary_skill" in reasons
    claims = {c.kind: c.claim for c in senior.probe_plan.verification_claims}
    assert "12 engineers" in claims["team_size"] and "45%" in claims["percentage"]


# ================================================================== schema / serialisation
def test_to_dict_is_json_serialisable_and_round_trips(fresher, mid, senior):
    for r in (fresher, mid[0], senior):
        d = r.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION == "1.0"
        s = json.dumps(d)
        assert json.loads(s) == d
        assert len(s) < 120_000
        assert "text" not in d


def test_schema_keys(senior):
    d = senior.to_dict()
    assert set(d) >= {"schema_version", "source", "contact", "summary", "skills", "soft_skills", "experience", "education", "projects",
                      "certifications", "languages", "total_experience_months", "seniority_estimate", "field_confidence",
                      "overall_confidence", "warnings", "sections_detected", "redacted_fields", "integrity", "probe_plan"}
    assert set(d["source"]) == {"format", "pages", "bytes", "sha256", "text_sha256", "extraction_method", "ocr_used"}
    assert set(d["contact"]) >= {"name", "email", "emails", "phone", "phones", "location", "links"}
    assert set(d["skills"][0]) == {"name", "category", "aliases_matched", "evidence", "mentions", "first_seen_in", "evidence_sources", "experience_months", "implied"}
    assert set(d["experience"][0]) == {"title", "company", "location", "start", "end", "is_current", "duration_months", "bullets", "skills",
                                       "confidence", "employment_type", "date_text", "date_precision"}
    assert set(d["education"][0]) == {"degree_level", "degree_raw", "field", "institution", "start_year", "end_year", "gpa", "gpa_scale", "confidence"}
    assert set(d["integrity"]) == {"flags", "risk_score", "fingerprint", "metadata"}
    assert set(d["integrity"]["fingerprint"]) == {"simhash", "text_sha256", "shingles"}
    assert 0 <= d["overall_confidence"] <= 1 and all(0 <= v <= 1 for v in d["field_confidence"].values())
    assert d["source"]["sha256"] and len(d["source"]["sha256"]) == 64


def test_parse_is_deterministic():
    pdf = senior_two_column_pdf()
    a = parse_resume(pdf, "a.pdf").to_dict()
    b = parse_resume(pdf, "a.pdf").to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_txt_resume_end_to_end():
    r = parse_resume(fresher_txt().encode(), "ananya.txt")
    assert (r.contact.name, r.contact.email, r.contact.phone, r.contact.location) == ("Ananya Iyer", "ananya.iyer@gmail.com", "+919845012345", "Bengaluru, Karnataka")
    e = r.experience[0]
    assert (e.title, e.company, e.location, e.start, e.end, e.employment_type) == ("Python Developer Intern", "Fynd", "Mumbai", "2023-01", "2023-06", "internship")
    assert {"Python", "Apache Airflow", "Docker", "PostgreSQL", "R"} <= {s.name for s in r.skills}
    assert r.education[0].gpa == 9.1 and r.total_experience_months == 6


# ================================================================== performance
def test_two_page_resume_parses_fast_after_warmup():
    from resume.tests.helpers import PdfBuilder

    b = PdfBuilder()
    body = []
    for i in range(6):
        body += [(f"Software Engineer, Company {i} Inc.   Jan 20{10 + i} - Dec 20{10 + i}", 10.5, True),
                 "• Built services with Python, Django, PostgreSQL, Docker and Kubernetes on AWS for millions of users every day",
                 "• Reduced latency by 30% and mentored a team of 4 engineers across two time zones"] * 1
    b.flow(50, 60, [("Jane Doe", 20, True), "jane@doe.dev | +1 415 555 0132 | Austin, TX", ("EXPERIENCE", 12, True)] + body)
    b.new_page()
    b.flow(50, 60, [("EDUCATION", 12, True), "B.S. in Computer Science, University of Texas, 2006 - 2010", ("SKILLS", 12, True),
                    "Python, Java, SQL, Docker, Kubernetes, AWS, Kafka, Redis, Git, Terraform"])
    pdf = b.bytes()
    parse_resume(pdf)                                   # warm the taxonomy
    t0 = time.perf_counter()
    for _ in range(5):
        r = parse_resume(pdf)
    per = (time.perf_counter() - t0) / 5
    assert r.source.pages == 2
    assert per < 0.3, per
