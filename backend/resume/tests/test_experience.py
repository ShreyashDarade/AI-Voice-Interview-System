import pytest

from resume import parse_resume
from resume.experience import seniority_from, classify_header
from resume.util import current_ym, ym


def parse(body, extra=""):
    text = f"Alex Morgan\nalex.morgan@mail.com | +1 415 555 0199\n\nEXPERIENCE\n{body}\n{extra}"
    return parse_resume(text.encode(), "cv.txt")


def e_tuple(e):
    return (e.title, e.company, e.start, e.end, e.is_current)


FORMATS = [
    # id, body, expected (title, company, start, end, current)
    ("pipe", "Senior Software Engineer | Acme Technologies Pvt Ltd | Jan 2020 - Mar 2022\n- Built stuff",
     ("Senior Software Engineer", "Acme Technologies Pvt Ltd", "2020-01", "2022-03", False)),
    ("title_comma_company_dates_right", "Software Engineer, Globex Corp   Jun 2018 - Dec 2019\n- Built stuff",
     ("Software Engineer", "Globex Corp", "2018-06", "2019-12", False)),
    ("company_dash_title", "Initech Solutions - Backend Developer   2017 - 2019\n- Built stuff",
     ("Backend Developer", "Initech Solutions", "2017-07", "2019-06", False)),
    ("title_at_company", "Data Analyst at Umbrella Labs (Sep 2019 - Present)\n- Built stuff",
     ("Data Analyst", "Umbrella Labs", "2019-09", None, True)),
    ("title_atsign_company", "DevOps Engineer @ Hooli Inc. | 03/2018 – 05/2020\n- Built stuff",
     ("DevOps Engineer", "Hooli Inc.", "2018-03", "2020-05", False)),
    ("title_line_above", "Product Manager\nWayne Enterprises, Gotham, NY\nFeb 2016 - Nov 2018\n- Built stuff",
     ("Product Manager", "Wayne Enterprises", "2016-02", "2018-11", False)),
    ("company_line_then_title_dates", "Stark Industries\nQA Engineer      Apr 2014 - Jan 2016\n- Built stuff",
     ("QA Engineer", "Stark Industries", "2014-04", "2016-01", False)),
    ("dates_line_first", "Aug 2011 - Dec 2013\nSystems Administrator\nCyberdyne Systems\n- Built stuff",
     ("Systems Administrator", "Cyberdyne Systems", "2011-08", "2013-12", False)),
    ("company_title_ambiguous_by_lexicon", "Tyrell Corporation, Senior Data Scientist, Los Angeles, CA   Oct 2012 - Sep 2014\n- Built stuff",
     ("Senior Data Scientist", "Tyrell Corporation", "2012-10", "2014-09", False)),
    ("yearonly", "Java Developer, Soylent Corp 2012 - 2014\n- Built stuff",
     ("Java Developer", "Soylent Corp", "2012-07", "2014-06", False)),
    ("apostrophe_year", "UX Designer | Pied Piper | Sept '19 – Dec '20\n- Built stuff",
     ("UX Designer", "Pied Piper", "2019-09", "2020-12", False)),
    ("iso_dates", "Support Engineer - Aperture Science Ltd | 2021-04 to 2022-08\n- Built stuff",
     ("Support Engineer", "Aperture Science Ltd", "2021-04", "2022-08", False)),
    ("present_variants", "Research Scientist, Black Mesa Research   January 2021 – Till date\n- Built stuff",
     ("Research Scientist", "Black Mesa Research", "2021-01", None, True)),
]


@pytest.mark.parametrize("id_,body,expected", FORMATS, ids=[f[0] for f in FORMATS])
def test_header_formats(id_, body, expected):
    r = parse(body)
    assert len(r.experience) == 1, [e_tuple(e) for e in r.experience]
    got = e_tuple(r.experience[0])
    assert got == expected
    assert r.experience[0].bullets == ["Built stuff"]


def test_location_extracted_from_company_line():
    r = parse("Software Engineer | Initech | Austin, TX | Jan 2020 - Dec 2020\n- x")
    e = r.experience[0]
    assert (e.title, e.company, e.location) == ("Software Engineer", "Initech", "Austin, TX")
    r = parse("Software Engineer, Initech Ltd, Sofia   Jan 2020 - Dec 2020\n- x")
    assert (r.experience[0].company, r.experience[0].location) == ("Initech Ltd", "Sofia")


def test_multiple_entries_bullets_belong_to_nearest_preceding():
    r = parse("""Software Engineer, Alpha Inc.   Jan 2020 - Dec 2021
- Alpha bullet one
- Alpha bullet two with Python
Intern, Beta LLC   Jun 2019 - Aug 2019
- Beta bullet using Java
""")
    assert [e.company for e in r.experience] == ["Alpha Inc.", "Beta LLC"]
    assert r.experience[0].bullets == ["Alpha bullet one", "Alpha bullet two with Python"]
    assert r.experience[1].bullets == ["Beta bullet using Java"]
    assert "Python" in r.experience[0].skills and "Python" not in r.experience[1].skills
    assert "Java" in r.experience[1].skills


def test_employment_type_flags():
    r = parse("""Software Engineering Intern, Alpha Inc.   Jun 2019 - Aug 2019
- x
Freelance Web Developer   Jan 2020 - Dec 2020
- x
Contract Data Engineer, Beta LLC   Jan 2021 - Dec 2021
- x
Part-time Tutor, Gamma Academy   Jan 2022 - Jun 2022
- x
Backend Engineer, Delta Corp   Jan 2023 - Dec 2023
- x
""")
    assert [e.employment_type for e in r.experience] == ["internship", "freelance", "contract", "part_time", "full_time"]


def test_internship_section_marks_entries_as_internships():
    text = "Alex Morgan\nalex@x.com\nINTERNSHIPS\nDeveloper, Alpha Inc.   Jun 2019 - Aug 2019\n- x\n"
    r = parse_resume(text.encode(), "cv.txt")
    assert r.experience[0].employment_type == "internship"


def test_education_dates_are_not_work():
    text = """Alex Morgan
alex@x.com
EDUCATION
B.Tech in Computer Science, IIT Delhi, 2014 - 2018
M.S. in Data Science, Stanford University, 2018 - 2020
EXPERIENCE
Engineer, Alpha Inc.   Jan 2021 - Dec 2021
- x
"""
    r = parse_resume(text.encode(), "cv.txt")
    assert len(r.experience) == 1 and r.total_experience_months == 12
    assert len(r.education) == 2


def test_promotions_under_one_group_header():
    r = parse("""Globex Corporation (Jan 2015 - Present)
Senior Engineer   Jan 2020 - Present
- led work
Engineer   Jan 2015 - Dec 2019
- did work
""")
    assert [(e.title, e.company) for e in r.experience] == [("Senior Engineer", "Globex Corporation"), ("Engineer", "Globex Corporation")]
    assert r.total_experience_months == current_ym() - ym(2015, 1) + 1


def test_promotions_company_line_once_then_roles():
    r = parse("""Initech Ltd
Principal Engineer   Mar 2021 - Present
- a
Senior Engineer   Jun 2017 - Feb 2021
- b
Engineer   Aug 2014 - May 2017
- c
""")
    assert [e.company for e in r.experience] == ["Initech Ltd"] * 3
    assert [e.title for e in r.experience] == ["Principal Engineer", "Senior Engineer", "Engineer"]
    # consecutive months: union has no gaps
    assert r.total_experience_months == current_ym() - ym(2014, 8) + 1


def test_overlapping_roles_not_double_counted():
    r = parse("A Dev, Alpha Inc.   Jan 2020 - Dec 2020\n- x\nB Dev, Beta LLC   Jul 2020 - Jun 2021\n- y\n")
    assert sum(e.duration_months for e in r.experience) == 24
    assert r.total_experience_months == 18


def test_months_are_counted_inclusively_with_month_granularity():
    r = parse("Dev, Alpha Inc.   Jan 2020 - Mar 2020\n- x")
    assert r.experience[0].duration_months == 3 and r.total_experience_months == 3
    r = parse("Dev, Alpha Inc.   Dec 2019 - Jan 2020\n- x")
    assert r.total_experience_months == 2


def test_present_runs_to_today():
    r = parse("Dev, Alpha Inc.   Jan 2024 - Present\n- x")
    assert r.total_experience_months == current_ym() - ym(2024, 1) + 1 and r.experience[0].end is None


def test_durations_only_are_low_confidence_and_counted_without_dates():
    r = parse("Software Engineer at Acme (2 years)\nData Analyst, Beta Ltd - 6 months\n")
    assert [e.duration_months for e in r.experience] == [24, 6]
    assert all(e.confidence <= 0.35 and e.start is None for e in r.experience)
    assert r.total_experience_months == 30
    assert "experience_durations_only" in r.warnings


def test_entries_without_experience_heading_use_fallback_with_warning():
    text = "Alex Morgan\nalex@x.com\nSoftware Engineer, Alpha Inc.   Jan 2020 - Dec 2021\n- Built Python things\n\nSKILLS\nPython\n"
    r = parse_resume(text.encode(), "cv.txt")
    assert len(r.experience) == 1 and "experience_section_not_found" in r.warnings


def test_sentence_with_dates_inside_bullet_is_not_an_entry():
    r = parse("Dev, Alpha Inc.   Jan 2020 - Dec 2020\n- Worked from Mar 2020 to Jun 2020 on the migration project\n"
              "  and from 2000 to 2005 users grew\n")
    assert len(r.experience) == 1


def test_confidence_higher_for_complete_entries():
    full = parse("Senior Engineer, Alpha Inc.   Jan 2020 - Dec 2021\n- x").experience[0].confidence
    partial = parse("Alpha Inc.   2020 - 2021").experience[0].confidence
    assert full > partial


def test_classify_header_unit():
    h = classify_header(["Staff Engineer", "Acme Robotics Inc.", "Remote"])
    assert (h.title, h.company, h.location) == ("Staff Engineer", "Acme Robotics Inc.", "Remote")
    h = classify_header(["Barista at Joe's Coffee"])
    assert h.company == "Joe's Coffee"


@pytest.mark.parametrize("months,titles,level", [
    (0, [], "fresher"), (11, [], "fresher"), (12, [], "junior"), (35, [], "junior"), (36, [], "mid"), (59, [], "mid"),
    (60, [], "senior"), (95, [], "senior"), (96, [], "lead"), (300, [], "lead"),
    (50, ["Tech Lead"], "senior"),           # bumped (>= 60% of 60)
    (22, ["Engineering Manager"], "mid"),     # bumped from junior (22 >= 0.6*36)
    (20, ["Engineering Manager"], "junior"),  # not far enough into the next band
    (8, ["Team Lead"], "junior"),             # 8 >= 0.6*12
    (3, ["Team Lead"], "fresher"),            # never contradicts the months
    (40, ["Lead Intern"], "mid"),
    (60, ["Principal Engineer"], "lead"),
])
def test_seniority(months, titles, level):
    assert seniority_from(months, titles) == level


def test_numbered_entries_with_labelled_role_and_repeated_duration_line():
    r = parse("""1. Infosys Ltd, Bangalore                           Aug 2016 to Mar 2019
   Role: Systems Engineer
   - Worked on Java and Oracle based banking application
2. Tata Consultancy Services, Kochi                 Apr 2019 to Till date
   Designation: IT Analyst
   Duration: Apr 2019 - Present
   - Developing REST services using Spring Boot
""")
    assert [(e.title, e.company, e.location, e.start, e.end) for e in r.experience] == [
        ("Systems Engineer", "Infosys Ltd", "Bangalore", "2016-08", "2019-03"),
        ("IT Analyst", "Tata Consultancy Services", "Kochi", "2019-04", None),
    ]
    assert r.experience[0].bullets == ["Worked on Java and Oracle based banking application"]
    assert r.experience[1].bullets == ["Developing REST services using Spring Boot"]


def test_company_line_then_title_line_then_dates_line_variants():
    r = parse("Acme Corp | Austin, TX\nSenior Engineer | Jan 2020 – Present\n- x\n")
    e = r.experience[0]
    assert (e.title, e.company, e.location) == ("Senior Engineer", "Acme Corp", "Austin, TX")
    r = parse("Jan 2020 – Present | Senior Engineer | Acme Corp\n- x\n")
    e = r.experience[0]
    assert (e.title, e.company, e.is_current) == ("Senior Engineer", "Acme Corp", True)
