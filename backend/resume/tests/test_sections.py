import pytest

from resume.extract import Line, extract_txt
from resume.sections import classify_heading_text, segment, split_inline_labels


def L(text, size=10.0, bold=False, bullet=False, **kw):
    return Line(text=text, font_size=size, is_bold=bold, is_bullet=bullet, **kw)


@pytest.mark.parametrize("heading,section", [
    ("Work Experience", "experience"), ("PROFESSIONAL EXPERIENCE", "experience"), ("Employment History", "experience"),
    ("Career History", "experience"), ("Work History", "experience"), ("Experience", "experience"),
    ("Relevant Experience", "experience"), ("Internships", "experience"), ("Education", "education"),
    ("Academic Background", "education"), ("Educational Qualifications", "education"), ("Technical Skills", "skills"),
    ("Core Competencies", "skills"), ("Key Skills", "skills"), ("Technologies", "skills"), ("Skills & Tools", "skills"),
    ("Areas of Expertise", "skills"), ("Personal Projects", "projects"), ("Academic Projects", "projects"),
    ("Selected Projects", "projects"), ("Licenses & Certifications", "certifications"), ("Courses", "certifications"),
    ("Training", "certifications"), ("Professional Summary", "summary"), ("Profile", "summary"), ("Objective", "summary"),
    ("About Me", "summary"), ("Awards", "awards"), ("Achievements", "awards"), ("Publications", "publications"),
    ("Languages", "languages"), ("Interests", "interests"), ("Hobbies", "interests"), ("Volunteering", "volunteering"),
    ("References", "references"), ("Declaration", "declaration"), ("Personal Details", "personal"),
])
def test_heading_lexicon(heading, section):
    sec, exact = classify_heading_text(heading)
    assert sec == section and exact


@pytest.mark.parametrize("text", [
    "Experience with Python and distributed systems",
    "Experience with Python",
    "Education is important to me and I enjoy learning",
    "Skills gained during the internship include communication",
    "Projects completed on time and under budget",
    "Summary of results was shared with the board",
])
def test_sentences_starting_with_heading_words_are_not_headings(text):
    assert classify_heading_text(text)[0] is None
    secs = segment([L("Jane Doe"), L("jane@x.com"), L(text), L("• did things", bullet=True)])
    assert [s.name for s in secs] == ["header"]


def test_bullet_that_equals_a_heading_word_is_not_a_heading():
    lines = [L("Jane Doe"), L("Experience", bullet=True), L("Built things")]
    assert [s.name for s in segment(lines)] == ["header"]


def test_fuzzy_heading_requires_formatting():
    plain = [L("Jane"), L("Proffesional Experiance"), L("Did stuff here")]
    assert [s.name for s in segment(plain)] == ["header"]
    formatted = [L("Jane"), L("PROFFESIONAL EXPERIANCE", size=13, bold=True), L("Did stuff here")]
    assert [s.name for s in segment(formatted)] == ["header", "experience"]


def test_segment_order_ranges_and_header_block():
    lines = [
        L("Jane Doe", 20), L("jane@example.org"),
        L("EXPERIENCE", 12, True), L("Engineer at Acme  2019 - 2021"), L("• Built things", bullet=True),
        L("EDUCATION", 12, True), L("B.Sc. Physics, MIT, 2015"),
        L("SKILLS:", 12, True), L("Python, Go"),
    ]
    secs = segment(lines)
    assert [s.name for s in secs] == ["header", "experience", "education", "skills"]
    assert [(s.start, s.end) for s in secs] == [(0, 2), (2, 5), (5, 7), (7, 9)]
    assert secs[1].heading == "EXPERIENCE" and secs[1].content_start == 3
    assert secs[0].name == "header" and secs[0].content_start == 0


def test_inline_skills_label_is_split_into_heading_and_content():
    lines = [L("Jane"), L("Technical Skills: Python, Java, SQL"), L("Languages: English, Hindi")]
    out = split_inline_labels(lines)
    assert [l.text for l in out] == ["Jane", "Technical Skills:", "Python, Java, SQL", "Languages: English, Hindi"]
    secs = segment(out)
    assert [s.name for s in secs] == ["header", "skills"]


def test_decorated_headings_and_colons():
    lines = [L("Name"), L("— WORK EXPERIENCE —", bold=True), L("x 2019 - 2020"), L("## Education"), L("y"), L("Projects:"), L("z")]
    assert [s.name for s in segment(lines)] == ["header", "experience", "education", "projects"]


def test_docx_heading_style_and_rule_underline_boost_confidence():
    lines = [L("Name"), Line(text="Experience", heading_style=True), L("a"), Line(text="Skills", rule_below=True), L("b")]
    assert [s.name for s in segment(lines)] == ["header", "experience", "skills"]


def test_txt_underlined_headings():
    doc = extract_txt(b"Jane Doe\njane@x.com\n\nExperience\n==========\nEngineer at Acme 2019 - 2021\n\nEducation\n---------\nBSc 2015\n")
    secs = segment(doc.lines)
    assert [s.name for s in secs] == ["header", "experience", "education"]
    assert all("====" not in l.text for l in doc.lines)


def test_no_headings_gives_single_header_section():
    secs = segment([L("just"), L("text")])
    assert [s.name for s in secs] == ["header"] and secs[0].end == 2
