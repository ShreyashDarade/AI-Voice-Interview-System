import pytest

from resume.dates import find_date_ranges, gaps, merge_intervals, parse_duration, strip_ranges, union_months
from resume.util import current_ym, ym, ym_str

CASES = [
    # text, start, end ('PRESENT' for current), kind
    ("Jan 2020 – Present", "2020-01", "PRESENT", "range"),
    ("January 2020 - March 2022", "2020-01", "2022-03", "range"),
    ("03/2018 – 05/2020", "2018-03", "2020-05", "range"),
    ("2019 – 2021", "2019-07", "2021-06", "range"),
    ("2019-2021", "2019-07", "2021-06", "range"),
    ("Sept '19 – Dec '20", "2019-09", "2020-12", "range"),
    ("Sep 2019 to Dec 2020", "2019-09", "2020-12", "range"),
    ("2021-04 to 2022-08", "2021-04", "2022-08", "range"),
    ("Jun 2021 until Now", "2021-06", "PRESENT", "range"),
    ("Aug 2018 till date", "2018-08", "PRESENT", "range"),
    ("Aug 2018 - Till Date", "2018-08", "PRESENT", "range"),
    ("Mar 2019 — Ongoing", "2019-03", "PRESENT", "range"),
    ("Apr 2019 – Sep 2019", "2019-04", "2019-09", "range"),
    ("(Jan 2020 - Mar 2021)", "2020-01", "2021-03", "range"),
    ("Jan - Mar 2020", "2020-01", "2020-03", "range"),
    ("Jan 2020 to Current", "2020-01", "PRESENT", "range"),
    ("Software Engineer | Jan 2020 – Currently", "2020-01", "PRESENT", "range"),
    ("12/2015 - 11/2017", "2015-12", "2017-11", "range"),
    ("2018 - Present", "2018-07", "PRESENT", "range"),
    ("Summer 2021", "2021-06", "2021-08", "season"),
    ("Summer 2021 - Fall 2021", "2021-06", "2021-11", "range"),
    ("2019-20", "2019-07", "2020-06", "range"),
    ("May 2020 – Jul. 2020", "2020-05", "2020-07", "range"),
    ("2015/06 - 2016/12", "2015-06", "2016-12", "range"),
    ("Dec 2022 - Jan 2023", "2022-12", "2023-01", "range"),
    ("Jan 2020", "2020-01", None, "point"),
]


@pytest.mark.parametrize("text,start,end,kind", CASES)
def test_date_range_table(text, start, end, kind):
    rs = find_date_ranges(text)
    assert rs, text
    r = rs[0]
    assert r.kind == kind
    assert ym_str(r.start) == start
    if end == "PRESENT":
        assert r.is_current and r.end is None
    elif end is None:
        assert not r.is_current
    else:
        assert not r.is_current and ym_str(r.end) == end


@pytest.mark.parametrize("text", [
    "12/03/1995",                       # date of birth style
    "Reduced costs from 2000+ users",   # number that looks like a year
    "No dates here at all",
    "Phone: 555-1234",
    "Version 3.2.1 released",
    "Python 3.11",
    "ISO 27001",
    "1.5 years of experience",          # durations are not date ranges
])
def test_non_dates_are_not_ranges(text):
    assert [r for r in find_date_ranges(text) if r.kind in ("range", "season")] == []


def test_multiple_ranges_in_one_line_are_paired_left_to_right():
    rs = find_date_ranges("2019 – 2021 | Jan 2022 - Present")
    assert [r.kind for r in rs] == ["range", "range"]
    assert ym_str(rs[1].start) == "2022-01" and rs[1].is_current


def test_strip_ranges_removes_empty_brackets_and_separators():
    text = "Acme Corp (Jan 2020 – Present) | Remote"
    out = strip_ranges(text, find_date_ranges(text))
    assert "2020" not in out and "()" not in out and out.startswith("Acme Corp")


@pytest.mark.parametrize("text,months", [
    ("1.5 years", 18), ("6 months", 6), ("2 yrs 3 mos", 27), ("3+ years", 36), ("no duration", None),
])
def test_parse_duration(text, months):
    assert parse_duration(text) == months


def test_union_of_overlapping_intervals_not_double_counted():
    a = (ym(2020, 1), ym(2020, 12))     # 12 months
    b = (ym(2020, 7), ym(2021, 6))      # overlaps 6 months with a
    assert union_months([a, b]) == 18
    assert union_months([a]) == 12


def test_union_adjacent_months_merge_and_gaps_reported():
    a = (ym(2020, 1), ym(2020, 6))
    b = (ym(2020, 7), ym(2020, 12))     # adjacent, no gap
    c = (ym(2022, 1), ym(2022, 3))
    assert merge_intervals([c, b, a]) == [(ym(2020, 1), ym(2020, 12)), (ym(2022, 1), ym(2022, 3))]
    g = gaps([a, b, c])
    assert g == [(ym(2021, 1), ym(2021, 12), 12)]
    assert union_months([a, b, c]) == 15


def test_union_nested_interval_and_invalid_interval_ignored():
    outer = (ym(2018, 1), ym(2020, 12))
    inner = (ym(2019, 3), ym(2019, 9))
    bad = (ym(2022, 5), ym(2022, 1))
    assert union_months([outer, inner, bad]) == 36


def test_present_means_today():
    r = find_date_ranges("Jan 2024 - Present")[0]
    assert r.effective_end == current_ym()
    assert union_months([(r.start, r.effective_end)]) == current_ym() - r.start + 1
