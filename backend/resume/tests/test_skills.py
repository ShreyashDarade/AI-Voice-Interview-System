import json
import time

import pytest

from resume import skills as S


def run(text, section="experience"):
    lines = [l for l in text.split("\n") if l.strip()]
    hits = S.match_lines(lines, [section] * len(lines))
    res = S.aggregate(hits, [section] * len(lines))
    return {s.name: s for s in res.skills + res.soft_skills}


def names(text, section="experience"):
    return set(run(text, section))


def explicit(text, section="experience"):
    return {n for n, s in run(text, section).items() if not s.implied}


# ------------------------------------------------------------------ taxonomy sanity

def test_taxonomy_size_and_integrity():
    tax = S.get_taxonomy()
    assert 700 <= len(tax.skills) <= 1200
    cats = {s["category"] for s in tax.skills}
    for needed in ("programming_language", "web_frontend", "web_backend", "data_science_ml", "database", "cloud_devops",
                   "security", "testing", "mobile", "embedded_iot", "game_dev", "data_engineering", "bi_analytics",
                   "design", "product_project", "soft_skill", "erp_crm_domain"):
        assert needed in cats
    for s in tax.skills:
        for target in s.get("implies", []):
            assert target in tax.by_name, (s["name"], target)
    # every surface maps to exactly one skill (dict) and canonical names are unique
    assert len({s["name"] for s in tax.skills}) == len(tax.skills)


def test_taxonomy_loaded_once_and_lazily_cached():
    assert S.get_taxonomy() is S.get_taxonomy()


def test_json_data_file_is_valid_json_package_resource():
    from importlib import resources

    raw = resources.files("resume").joinpath("data", "skills.json").read_text(encoding="utf-8")
    assert json.loads(raw)["skills"]


# ------------------------------------------------------------------ false-positive regressions

@pytest.mark.parametrize("text", [
    "Senior software engineer going to the market on the sapphire coast",
    "I will go there tomorrow and rust never sleeps; swift response and ruby red",
    "The sre team handled incidents",                       # lowercase sre embedded in prose
    "Worked at Sapient and Sapphire Labs as a character designer",
    "Gross margin of 3.2 for category C customers",         # a lone "C" without programming context
    "Vitamin C and R&D budget",
    "I excel in teamwork and love to go the extra mile",
    "My strengths: an engineer, a river, an eagle",          # 'r' inside normal words
])
def test_no_false_positives_from_substrings_or_common_words(text):
    found = explicit(text)
    for bad in ("R", "Go", "SAP", "SRE", "C", "Rust", "Swift", "Ruby", "Excel"):
        assert bad not in found, (text, found)


def test_r_not_matched_inside_engineer_and_c_alone():
    assert "R" not in names("Principal engineer, developer and manager")
    assert "C" not in names("Grade C student")


def test_java_and_javascript_not_conflated():
    only_java = explicit("Built services in Java")
    assert "Java" in only_java and "JavaScript" not in only_java
    only_js = explicit("Built UIs in JavaScript and TypeScript")
    assert "JavaScript" in only_js and "Java" not in only_js
    both = explicit("Java and JavaScript")
    assert {"Java", "JavaScript"} <= both


def test_symbol_skills_found():
    found = explicit("Built firmware in C++ and C#, services on .NET and Node.js, with CI/CD, F# and Objective-C")
    assert {"C++", "C#", ".NET", "Node.js", "CI/CD", "F#", "Objective-C"} <= found


def test_c_plus_plus_is_not_c():
    assert "C" not in explicit("Wrote C++ code")
    assert "C++" in explicit("Wrote C++ code")


@pytest.mark.parametrize("alias,canonical", [
    ("k8s", "Kubernetes"), ("postgres", "PostgreSQL"), ("Postgres SQL", "PostgreSQL"), ("nodejs", "Node.js"),
    ("node", "Node.js"), ("csharp", "C#"), ("c sharp", "C#"), ("golang", "Go"), ("GCP", "Google Cloud Platform"),
    ("google cloud platform", "Google Cloud Platform"), ("reactjs", "React"), ("react.js", "React"),
    ("js", "JavaScript"), ("ecmascript", "JavaScript"), ("dotnet", ".NET"), ("scikit learn", "scikit-learn"),
    ("sklearn", "scikit-learn"), ("machine-learning", "Machine Learning"), ("tf2", "TensorFlow"),
    ("amazon web services", "AWS"), ("docker-compose", "Docker"), ("Mongo", "MongoDB"), ("py3", "Python"),
])
def test_aliases_resolve_to_canonical_names(alias, canonical):
    found = names(f"Skills: {alias}", "skills")
    assert canonical in found, (alias, found)


def test_aliases_matched_recorded():
    sk = run("Deployed on k8s and Kubernetes")["Kubernetes"]
    assert "k8s" in sk.aliases_matched and sk.mentions == 2


def test_implies_edges():
    r = run("Built a Django app and a React front-end")
    assert r["Python"].implied and r["JavaScript"].implied
    assert not r["Django"].implied
    assert "experience" in r["Python"].evidence_sources
    # explicit mention overrides the implied flag
    r2 = run("Django and Python")
    assert not r2["Python"].implied


def test_react_native_is_not_plain_react_explicit():
    r = run("Shipped apps with React Native")
    assert "React Native" in r
    assert not (r.get("React") and not r["React"].implied)


def test_case_insensitive_for_unambiguous_skills():
    assert {"Docker", "Kubernetes", "PostgreSQL"} <= explicit("DOCKER kubernetes PostgreSQL")


# ------------------------------------------------------------------ ambiguous-skill context rules

def test_ambiguous_skills_accepted_in_skills_section_lists():
    f = explicit("Python, R, Go, C, SAP, Swift, Rust, Ruby, Excel, Spark", "skills")
    assert {"R", "Go", "C", "SAP", "Swift", "Rust", "Ruby", "Excel"} <= f
    assert "Apache Spark" in f


def test_ambiguous_skills_rejected_in_prose_without_context():
    assert "Go" not in explicit("We decided to Go ahead with the plan")
    assert "R" not in explicit("Plan R was the backup")
    assert "SAP" not in explicit("SAP sent us a note")      # no ERP context


def test_ambiguous_accepted_with_context_words():
    assert "R" in explicit("Statistical analysis in R using ggplot2 and tidyverse")
    assert "Go" in explicit("Wrote concurrent microservices in Go using goroutines and gRPC")
    assert "SAP" in explicit("Implemented SAP FICO modules for the ERP rollout")
    assert "Swift" in explicit("Developed iOS apps in Swift with Xcode")
    assert "Rust" in explicit("Systems programming in Rust with tokio and cargo")
    assert "Ruby" in names("Ruby on Rails developer with rspec")
    assert "Excel" in explicit("Financial models in Excel with pivot tables and VBA")
    assert "Apache Spark" in explicit("Big data pipelines with Spark and Hadoop on Databricks")


def test_ambiguous_accepted_as_list_items_in_experience_when_other_skills_present():
    assert "R" in explicit("Tools used: Python, R, SQL, Tableau and Git")


def test_case_sensitive_short_forms_need_canonical_case():
    assert "Artificial Intelligence" in explicit("AI engineer")
    assert "Artificial Intelligence" not in explicit("He said ai is cool")
    assert "iOS" in explicit("Built an iOS app")
    assert "iOS" not in explicit("a ios thing")
    assert "Site Reliability Engineering" in explicit("SRE on-call rotation")


def test_urls_and_emails_do_not_produce_skills():
    assert "Python" not in explicit("contact john@python.org or visit https://github.com/go-python/gopy")


def test_soft_skills_are_separate_list():
    lines = ["Strong communication skills and leadership; Python developer", "Skills: Teamwork, Python"]
    hits = S.match_lines(lines, ["experience", "skills"])
    res = S.aggregate(hits, ["experience", "skills"])
    assert "Python" in {s.name for s in res.skills}
    soft = {s.name for s in res.soft_skills}
    assert {"Communication", "Teamwork"} <= soft
    assert not ({s.name for s in res.skills} & soft)


# ------------------------------------------------------------------ fuzzy typos

def test_fuzzy_typo_in_skills_section_only():
    f = run("Kubernates, Postgresql, Javascrpt", "skills")
    assert "Kubernetes" in f
    assert "Kubernates" in f["Kubernetes"].aliases_matched
    # not applied to running text
    assert "Kubernetes" not in names("We use Kubernates daily", "experience")


def test_fuzzy_is_conservative():
    f = explicit("Kubernetes, Documents, Management, Development, Programming", "skills")
    assert "Kubernetes" in f
    assert f <= {"Kubernetes", "Programming"} and "Documentation" not in f


# ------------------------------------------------------------------ evidence + first_seen

def test_evidence_levels_and_first_seen():
    lines = ["Docker, Kafka", "Built pipelines with Kafka", "Certified Kubernetes Administrator"]
    secs = ["skills", "experience", "certifications"]
    res = S.aggregate(S.match_lines(lines, secs), secs)
    by = {s.name: s for s in res.skills}
    assert by["Docker"].evidence == "skills_section" and by["Docker"].first_seen_in == "skills"
    assert by["Apache Kafka"].evidence == "experience" and by["Apache Kafka"].mentions == 2
    assert by["Kubernetes"].evidence == "certifications"


# ------------------------------------------------------------------ performance

def test_matching_is_linear_and_fast():
    S.get_taxonomy()
    line = "Developed microservices using Java Spring Boot, Kafka and PostgreSQL on AWS with Docker and Kubernetes."
    lines = [line] * 300
    t0 = time.perf_counter()
    S.match_lines(lines, ["experience"] * len(lines))
    small = time.perf_counter() - t0
    lines = [line] * 3000
    t0 = time.perf_counter()
    S.match_lines(lines, ["experience"] * len(lines))
    big = time.perf_counter() - t0
    assert small < 0.5
    assert big < max(small * 25, 0.5)        # 10x text -> far below quadratic growth


def test_ambiguous_library_names_do_not_match_ordinary_words_near_programming_words():
    text = "Software Development Engineer II\nBuilt Azure services handling 10M requests/day with a Python backend"
    lines = text.split("\n")
    res = S.aggregate(S.match_lines(lines, ["experience"] * 2), ["experience"] * 2)
    assert "Requests" not in {s.name for s in res.skills}
    assert "Python" in {s.name for s in res.skills}
