import pytest

from resume import contact as C
from resume.extract import Line
from resume.models import Link


def L(text, size=10.0, bold=False, ry0=0.05, page=1):
    return Line(text=text, font_size=size, is_bold=bold, ry0=ry0, ry1=ry0 + 0.01, page=page)


# ------------------------------------------------------------------ emails
def test_multiple_unique_emails_in_order():
    text = "Contact: John.Smith@Gmail.com | john@work-corp.io\nalt: john.smith@gmail.com, bad@@x, no_tld@localhost"
    assert C.find_emails(text) == ["john.smith@gmail.com", "john@work-corp.io"]


def test_email_not_split_by_trailing_punctuation_and_obfuscation():
    assert C.find_emails("Email: a.b+c@sub.example.co.uk.") == ["a.b+c@sub.example.co.uk"]
    assert C.find_emails("reach me: jane [at] example [dot] org") == ["jane@example.org"]


# ------------------------------------------------------------------ phones
@pytest.mark.parametrize("text,region,e164", [
    ("+91 98765 43210", "US", "+919876543210"),
    ("+91-9876543210", "US", "+919876543210"),
    ("98765 43210", "US", "+919876543210"),        # Indian mobile without country code
    ("(415) 555-0132", "US", "+14155550132"),
    ("+1 415-555-0132", "GB", "+14155550132"),
    ("+44 20 7946 0958", "US", "+442079460958"),
    ("020 7946 0958", "GB", "+442079460958"),
    ("+49 30 901820", "US", "+4930901820"),
    ("+61 2 9374 4000", "US", "+61293744000"),
])
def test_phone_normalisation(text, region, e164):
    ph = C.find_phones("Phone: " + text, region)
    assert [p.e164 for p in ph][:1] == [e164], ph
    assert ph[0].raw.replace(" ", "")[:3]


def test_phone_ignores_dates_years_and_long_ids():
    text = "Jan 2019 - Mar 2021\n2018 - 2020\n12/03/1995\nEmployee ID 12345678901234567890\nZip 560001\n3.5 GPA 2015 2019"
    assert C.find_phones(text, "US") == []


def test_phone_dedup_and_multiple():
    ph = C.find_phones("+1 (415) 555-0132 / 415.555.0132 / +44 20 7946 0958", "US")
    assert [p.e164 for p in ph] == ["+14155550132", "+442079460958"]


# ------------------------------------------------------------------ links
def test_link_classification_and_dedup_from_text_and_annotations():
    lines = [L("https://www.linkedin.com/in/jane-doe-12ab34 | github.com/janedoe | gitlab.com/jd"),
             L("stackoverflow.com/users/123/jane | leetcode.com/janedoe | kaggle.com/janedoe | twitter.com/jd"),
             L("janedoe.dev | medium.com/@jane")]
    pdf = [{"url": "https://github.com/janedoe/", "label": "GitHub", "page": 1},
           {"url": "mailto:jane@doe.dev", "label": "mail", "page": 1},
           {"url": "https://example.org/paper.pdf", "label": "", "page": 2}]
    links, mails, tels = C.find_links(lines, pdf, lambda i: True, lambda i: False)
    by = {}
    for lk in links:
        by.setdefault(lk.type, []).append(lk.url)
    assert set(by) >= {"linkedin", "github", "gitlab", "stackoverflow", "leetcode", "kaggle", "twitter", "portfolio", "other"}
    assert len(by["github"]) == 1                 # annotation + text deduped
    assert mails == ["jane@doe.dev"]
    assert any(lk.label == "GitHub" for lk in links)


def test_classify_url_edge_cases():
    assert C.classify_url("https://x.com/abc") == "twitter"
    assert C.classify_url("https://foo.github.io/bar") == "portfolio"
    assert C.classify_url("https://notlinkedin.com.evil.io/x") == "other"


# ------------------------------------------------------------------ names
def test_name_by_largest_font_beats_earlier_lines():
    lines = [L("Curriculum Vitae", 9), L("Senior Software Engineer", 16, True), L("Maria Garcia Lopez", 22, True),
             L("maria@x.com", 10)]
    assert C.extract_name(lines, 0, [], []) == ("Maria Garcia Lopez", pytest.approx(0.95))


def test_uppercase_name_is_title_cased_and_credentials_stripped():
    lines = [L("JOHN A. SMITH, PMP", 20, True), L("Project Manager", 12)]
    assert C.extract_name(lines, 0, [], [])[0] == "John A. Smith"


def test_name_ignores_roles_locations_headings_and_contacts():
    lines = [L("Resume", 24), L("Software Engineer", 20), L("San Francisco", 18), L("EXPERIENCE", 17),
             L("jane@x.com", 16), L("Priya Raman", 14)]
    assert C.extract_name(lines, 0, [], [])[0] == "Priya Raman"


def test_name_heuristic_first_lines_when_no_font_info():
    lines = [Line(text="Ananya Iyer"), Line(text="ananya@x.com")]
    name, conf = C.extract_name(lines, 0, [], [])
    assert name == "Ananya Iyer" and conf >= 0.5


def test_name_fallback_to_email_then_linkedin_low_confidence():
    lines = [Line(text="Curriculum Vitae"), Line(text="Phone: +1 415 555 0132")]
    name, conf = C.extract_name(lines, 0, ["raj.malhotra92@gmail.com"], [])
    assert name == "Raj Malhotra" and conf <= 0.35
    name, conf = C.extract_name(lines, 0, [], [Link("https://linkedin.com/in/sarah-o-connor-1a2b3c4d", "linkedin")])
    assert name.startswith("Sarah") and conf <= 0.35


def test_name_with_particles_and_unicode():
    assert C.extract_name([L("José de la Cruz", 20)], 0, [], [])[0] == "José de la Cruz"
    assert C.extract_name([L("Zoë Müller", 20)], 0, [], [])[0] == "Zoë Müller"


def test_no_name_when_nothing_plausible():
    assert C.extract_name([L("123 Main Street"), L("Tel 555 1234")], 0, [], []) == ("", 0.0)


# ------------------------------------------------------------------ location
@pytest.mark.parametrize("line,expected", [
    ("jane@x.com | +1 415 555 0132 | San Francisco, CA", "San Francisco, CA"),
    ("123 Main St, Springfield, IL 62704", "Springfield, IL"),
    ("Bengaluru, Karnataka, India", "Bengaluru, Karnataka, India"),
    ("Location: Pune, Maharashtra", "Pune, Maharashtra"),
    ("Remote", "Remote"),
    ("Berlin, Germany", "Berlin, Germany"),
    ("Mumbai", "Mumbai"),
])
def test_location_extraction(line, expected):
    assert C.find_location([L(line)], [0]) == expected


def test_location_not_taken_from_job_or_skill_text():
    assert C.find_location([L("Senior Engineer, Acme Corp"), L("Python, Java, SQL")], [0, 1]) == ""
