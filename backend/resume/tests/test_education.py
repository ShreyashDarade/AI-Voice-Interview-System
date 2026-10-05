import pytest

from resume import parse_resume
from resume.education import extract_gpa, find_degree


def edu(body):
    text = f"Pat Lee\npat.lee@mail.com\n\nEDUCATION\n{body}\n"
    return parse_resume(text.encode(), "cv.txt").education


@pytest.mark.parametrize("text,level", [
    ("B.Tech in Computer Science", "bachelor"), ("BTech CSE", "bachelor"), ("B.E. in Mechanical Engineering", "bachelor"),
    ("BE Computer Engineering", "bachelor"), ("B.Sc. Physics", "bachelor"), ("BS in Biology", "bachelor"), ("BA English", "bachelor"),
    ("BCA", "bachelor"), ("B.Com (Hons)", "bachelor"), ("Bachelor of Science in Chemistry", "bachelor"),
    ("Bachelor's degree in Economics", "bachelor"), ("M.Tech VLSI", "master"), ("ME in Electronics", "master"),
    ("M.S. in Computer Science", "master"), ("MS Data Science", "master"), ("M.Sc. Statistics", "master"), ("MCA", "master"),
    ("Master of Science in Robotics", "master"), ("Masters in Finance", "master"), ("MBA, Finance", "mba"),
    ("Master of Business Administration", "mba"), ("PGDM Marketing", "mba"), ("Ph.D. in Physics", "doctorate"),
    ("PhD Computer Vision", "doctorate"), ("Doctor of Philosophy in Math", "doctorate"), ("Diploma in Computer Engineering", "diploma"),
    ("Associate degree in Nursing", "associate"), ("A.A.S. in Welding", "associate"), ("High School Diploma", "high_school"),
    ("12th (HSC), State Board", "high_school"), ("10th Standard CBSE", "high_school"), ("A-Levels: Maths, Physics", "high_school"),
    ("Higher Secondary", "high_school"), ("Class XII", "high_school"), ("Nanodegree in Data Engineering", "certificate"),
])
def test_degree_levels(text, level):
    assert find_degree(text)[0] == level, text


@pytest.mark.parametrize("text", ["Be the change", "Tell me about it", "As a team", "Worked with ME and others", "Bsides conference"])
def test_ordinary_words_are_not_degrees(text):
    assert find_degree(text) is None


def test_field_institution_years_gpa_one_line():
    e = edu("B.Tech in Computer Science and Engineering, Indian Institute of Technology Delhi, 2014 - 2018, CGPA 8.7/10")[0]
    assert (e.degree_level, e.field, e.start_year, e.end_year, e.gpa, e.gpa_scale) == ("bachelor", "Computer Science and Engineering", 2014, 2018, 8.7, 10.0)
    assert e.institution == "Indian Institute of Technology Delhi"


def test_field_in_parentheses_and_abbreviations():
    e = edu("B.E. (CSE), BMS College of Engineering, 2012 - 2016")[0]
    assert e.field == "Computer Science and Engineering" and e.institution == "BMS College of Engineering"
    e = edu("Bachelor of Technology - Electronics, NIT Trichy, 2010-2014")[0]
    assert e.field == "Electronics" and "NIT" in e.institution


def test_multi_line_us_style_institution_first():
    es = edu("""Stanford University                          2018 - 2020
M.S. in Computer Science, GPA 3.9/4.0
University of California, Berkeley               2014 - 2018
B.S. in Electrical Engineering and Computer Sciences, GPA: 3.7/4.0""")
    assert [(e.degree_level, e.institution, e.start_year, e.end_year, e.gpa, e.gpa_scale) for e in es] == [
        ("master", "Stanford University", 2018, 2020, 3.9, 4.0),
        ("bachelor", "University of California, Berkeley", 2014, 2018, 3.7, 4.0),
    ]
    assert es[0].field == "Computer Science"


def test_multi_line_degree_first_style():
    es = edu("""MBA, Finance
Indian School of Business
2019 - 2021
B.Com
Delhi University
2013 - 2016, 78%""")
    assert [(e.degree_level, e.institution, e.end_year) for e in es] == [("mba", "Indian School of Business", 2021), ("bachelor", "Delhi University", 2016)]
    assert es[1].gpa == 78.0 and es[1].gpa_scale == 100.0


def test_graduation_year_only_and_expected():
    e = edu("B.S. Computer Science, Ohio State University, Expected May 2025")[0]
    assert (e.start_year, e.end_year) == (None, 2025)
    e = edu("BS Mathematics - University of Oslo (2019)")[0]
    assert e.end_year == 2019


def test_years_not_anchors_by_themselves():
    assert edu("Attended workshops in 2019 and 2020\nJoined a hackathon 2018") == []


def test_education_found_without_heading_for_strong_degrees():
    text = "Pat Lee\npat@x.com\nPh.D. in Physics, MIT, 2012-2016\nSKILLS\nPython\n"
    r = parse_resume(text.encode(), "cv.txt")
    assert r.education and r.education[0].degree_level == "doctorate"


@pytest.mark.parametrize("text,expected", [
    ("GPA: 3.8/4.0", (3.8, 4.0)), ("CGPA 9.1/10", (9.1, 10.0)), ("3.5/4", (3.5, 4.0)), ("GPA 3.9", (3.9, 4.0)),
    ("CGPA: 8.4", (8.4, 10.0)), ("CPI: 9.2", (9.2, 10.0)), ("Percentage: 85.5%", (85.5, 100.0)), ("92%", (92.0, 100.0)),
    ("GPA: 4.5/4.0", (None, None)), ("no grades", (None, None)),
])
def test_gpa_scales(text, expected):
    assert extract_gpa(text) == expected
