"""Skill taxonomy + O(text) matcher.

* ``data/skills.json`` (loaded lazily with :mod:`importlib.resources`, cached)
  holds ~1000 canonical skills with aliases, ``implies`` edges and ambiguity
  metadata.
* All surface forms are compiled into **one** trie-shaped regular expression
  with token boundaries that understand symbols (``c++``, ``c#``, ``.net``,
  ``node.js``, ``ci/cd``).  One pass over the text finds every candidate.
* Ambiguous / short / common-word surfaces (``R``, ``Go``, ``C``, ``SAP``,
  ``Spark``, ``Swift``, ``Excel``...) are only accepted when they appear in a
  skills section, as an item of a delimited skill list, or next to
  context words (``ggplot``, ``goroutine``, ``rails``...).  Case-sensitive
  short forms (``AI``, ``SRE``, ``iOS``) additionally need their canonical case
  outside skills lists.
* Typos (``Kubernates``) are fixed with rapidfuzz, only for long names, only
  for items of a skills-section list, conservative cut-off.

Soft skills live in a separate list.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------- data classes

@dataclass
class Skill:
    name: str
    category: str
    aliases_matched: List[str] = field(default_factory=list)
    evidence: str = "other"          # strongest evidence level
    mentions: int = 0
    first_seen_in: str = ""
    evidence_sources: List[str] = field(default_factory=list)
    experience_months: int = 0
    implied: bool = False


@dataclass
class Hit:
    line: int
    start: int
    end: int
    skill: int                       # taxonomy index
    surface: str                     # text as written
    fuzzy: bool = False


EVIDENCE_PRIORITY = ["experience", "projects", "certifications", "summary", "skills_section", "other"]
_SECTION_TO_EVIDENCE = {
    "skills": "skills_section", "experience": "experience", "projects": "projects",
    "summary": "summary", "certifications": "certifications", "header": "summary",
}

_STRIP_SPANS = re.compile(
    r"(?:https?://|www\.)\S+|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\b(?:linkedin|github|gitlab|stackoverflow|kaggle|leetcode)\.com/\S*",
    re.I,
)
_DELIMS = set(",;|/•·•")
_ITEM_SPLIT = re.compile(r"\s*[,;|•·]\s*|\s{2,}|\t+|\s+/\s+")


# --------------------------------------------------------------------------- taxonomy

class Taxonomy:
    def __init__(self, raw: dict):
        self.skills: List[dict] = raw["skills"]
        self.by_name: Dict[str, int] = {s["name"]: i for i, s in enumerate(self.skills)}
        weak = set(raw.get("weak_surfaces", []))
        self.surfaces: Dict[str, Tuple[int, bool, bool]] = {}   # key -> (skill, ambiguous, case_sensitive)
        self.context_re: Dict[int, Optional["re.Pattern[str]"]] = {}
        for i, s in enumerate(self.skills):
            amb_skill = bool(s.get("ambiguous"))
            cs_skill = bool(s.get("case_sensitive"))
            name_key = self._key(s["name"])
            amb_alias = {self._key(a) for a in s.get("ambiguous_aliases", [])}
            forms = [(s["name"], True)] + [(a, False) for a in s.get("aliases", [])]
            for form, is_name in forms:
                key = self._key(form)
                if not key or key in self.surfaces:
                    continue
                single = " " not in key
                short = single and len(key) <= 3
                amb = (
                    key in amb_alias
                    or key in weak
                    or (single and key.isalpha() and len(key) <= 2)
                    or (amb_skill and (short or (is_name and single and len(key) <= 10) or key == name_key and single))
                )
                cs = cs_skill and single and len(key) <= 4
                # "AI", "SRE", "iOS": the canonical name itself is case-sensitive if it has caps
                self.surfaces[key] = (i, bool(amb), bool(cs))
            ctx = [c.lower() for c in s.get("requires_context", [])]
            if ctx:
                self.context_re[i] = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(c) for c in sorted(ctx, key=len, reverse=True)) + r")(?![a-z0-9])")
            else:
                self.context_re[i] = None
        self.implies: Dict[int, List[int]] = {}
        for i, s in enumerate(self.skills):
            self.implies[i] = [self.by_name[n] for n in s.get("implies", []) if n in self.by_name]
        self.regex = re.compile(self._build_pattern(self.surfaces.keys()), re.I)
        # fuzzy candidates: long, single-token, alphabetic, unambiguous, non-soft
        self.fuzzy_choices: List[str] = []
        self.fuzzy_owner: Dict[str, int] = {}
        for key, (i, amb, cs) in self.surfaces.items():
            if (len(key) >= 7 and key.isalpha() and not amb and not cs
                    and self.skills[i]["category"] != "soft_skill"):
                self.fuzzy_choices.append(key)
                self.fuzzy_owner[key] = i

    @staticmethod
    def _key(s: str) -> str:
        return re.sub(r"[ \t\-]+", " ", s.strip().lower())

    @staticmethod
    def _lit(ch: str) -> str:
        if ch == " ":
            return r"[ \t\-]+"
        return re.escape(ch)

    def _build_pattern(self, keys: Iterable[str]) -> str:
        root: dict = {}
        for k in keys:
            node = root
            for ch in k:
                node = node.setdefault(ch, {})
            node[""] = True

        lit = self._lit

        def rec(node: dict) -> str:
            end = "" in node
            alts = [lit(ch) + rec(node[ch]) for ch in sorted(k for k in node if k)]
            if not alts:
                return ""
            if len(alts) == 1 and not end:
                return alts[0]
            return "(?:" + "|".join(alts) + ")" + ("?" if end else "")

        return r"(?<![A-Za-z0-9_.])(?:" + rec(root) + r")(?![A-Za-z_])(?!\d{3})"


_TAX: Optional[Taxonomy] = None
_TAX_LOCK = threading.Lock()


def get_taxonomy() -> Taxonomy:
    """Load (once) and return the taxonomy."""
    global _TAX
    if _TAX is None:
        with _TAX_LOCK:
            if _TAX is None:
                from importlib import resources

                text = resources.files(__package__).joinpath("data", "skills.json").read_text(encoding="utf-8")
                _TAX = Taxonomy(json.loads(text))
    return _TAX


# --------------------------------------------------------------------------- matcher

def _mask(text: str) -> str:
    return _STRIP_SPANS.sub(lambda m: " " * len(m.group(0)), text)


def _item_bounds_ok(line: str, s: int, e: int) -> bool:
    """True when [s:e] is a standalone item of a delimited list."""
    i = s - 1
    while i >= 0 and line[i] == " ":
        i -= 1
    j = e
    while j < len(line) and line[j] == " ":
        j += 1
    before = line[i] if i >= 0 else None
    after = line[j] if j < len(line) else None
    left_ok = before is None or before in _DELIMS or before in ":(\t[" or before == "-"
    right_ok = after is None or after in _DELIMS or after in "):.\t]" or after == "-"
    return left_ok and right_ok


def _casing_ok(text: str, name: str) -> bool:
    if text == name:
        return True
    t = text
    if t.isupper():
        return True
    if len(t) > 1 and t[:-1].isupper() and t[-1] == "s":     # LLMs, SLOs
        return True
    return False


def _line_is_list(line: str) -> bool:
    return sum(1 for ch in line if ch in ",;|•·") >= 2 or line.count("  ") >= 2 or line.count("\t") >= 2


def match_lines(
    lines: Sequence[str],
    line_sections: Sequence[str],
    fuzzy: bool = True,
    tax: Optional[Taxonomy] = None,
) -> List[Hit]:
    """Find all accepted skill mentions.  ``line_sections[i]`` is the section name of line i."""
    tax = tax or get_taxonomy()
    n = len(lines)
    text_parts = [_mask(l) for l in lines]
    full = "\n".join(text_parts)
    starts: List[int] = []
    pos = 0
    for part in text_parts:
        starts.append(pos)
        pos += len(part) + 1
    import bisect

    raw_hits: List[Tuple[int, int, int, int, str, bool, bool]] = []   # line,s,e,skill,text,amb,cs
    for m in tax.regex.finditer(full):
        gs, ge = m.start(), m.end()
        li = bisect.bisect_right(starts, gs) - 1
        ls = starts[li]
        s, e = gs - ls, ge - ls
        line = text_parts[li]
        if e > len(line):           # match crossed a newline (should not happen; guard)
            continue
        text = line[s:e]
        key = tax._key(text)
        ent = tax.surfaces.get(key)
        if ent is None:
            continue
        skill, amb, cs = ent
        if len(key) <= 2 and key.isalpha():
            nxt = line[e] if e < len(line) else ""
            prv = line[s - 1] if s > 0 else ""
            if (nxt and (nxt in "+#&" or nxt.isdigit())) or (prv and prv in "&'’") or (nxt == "'" and len(key) == 1):
                continue
            if nxt == "-" and line[e + 1:e + 6].lower() in ("level", "based") and len(key) == 1:
                continue
        raw_hits.append((li, s, e, skill, text, amb, cs))

    # unambiguous skills per line (for list-context rule)
    confident_per_line: Dict[int, int] = {}
    for li, s, e, sk, text, amb, cs in raw_hits:
        if not amb and not cs:
            confident_per_line[li] = confident_per_line.get(li, 0) + 1

    hits: List[Hit] = []
    for li, s, e, sk, text, amb, cs in raw_hits:
        if amb or cs:
            line = text_parts[li]
            sec = line_sections[li] if li < len(line_sections) else ""
            in_skills = sec == "skills"
            list_ctx = False
            if in_skills:
                list_ctx = _item_bounds_ok(line, s, e) if len(tax._key(text)) <= 2 else True
            else:
                if _item_bounds_ok(line, s, e) and _line_is_list(line) and confident_per_line.get(li, 0) >= 2:
                    list_ctx = True
            if cs and not _casing_ok(text, tax.skills[sk]["name"]) and not list_ctx:
                continue
            if cs and not _casing_ok(text, tax.skills[sk]["name"]) and list_ctx and not in_skills:
                continue
            if amb and not list_ctx:
                cre = tax.context_re.get(sk)
                if cre is None:
                    continue
                if tax._key(text) in ("c", "r", "go", "d") and not _casing_ok(text, tax.skills[sk]["name"]):
                    continue
                lo = max(0, li - 1)
                hi = min(n, li + 2)
                window = " ".join(lines[lo:hi]).lower()
                # remove the matched text from the window once so the skill's own name cannot count as context
                found = False
                for cm in cre.finditer(window):
                    if cm.group(0) != text.lower():
                        found = True
                        break
                if not found:
                    continue
        hits.append(Hit(li, s, e, sk, text))

    if fuzzy:
        exact_lines: Dict[int, Set[Tuple[int, int]]] = {}
        for h in hits:
            exact_lines.setdefault(h.line, set()).add((h.start, h.end))
        hits.extend(_fuzzy_hits(lines, line_sections, exact_lines, tax))
    hits.sort(key=lambda h: (h.line, h.start))
    return hits


def _fuzzy_hits(lines, line_sections, exact_lines, tax: Taxonomy) -> List[Hit]:
    try:
        from rapidfuzz import fuzz, process
    except ImportError:  # pragma: no cover
        return []
    out: List[Hit] = []
    seen_items: Set[str] = set()
    for li, line in enumerate(lines):
        if li >= len(line_sections) or line_sections[li] != "skills":
            continue
        pos = 0
        # strip a leading "Label:" prefix
        label = re.match(r"^[A-Za-z &/]{2,30}:\s*", line)
        base = label.end() if label else 0
        for part in _ITEM_SPLIT.split(line[base:]):
            item = part.strip(" .()[]")
            if not item or not item.isalpha() or len(item) < 7 or item.lower() in seen_items:
                continue
            idx = line.find(item, base)
            if idx < 0:
                continue
            covered = any(s <= idx and idx + len(item) <= e for s, e in exact_lines.get(li, ()))
            if covered:
                continue
            key = item.lower()
            if key in tax.surfaces:
                continue
            hit = process.extractOne(key, tax.fuzzy_choices, scorer=fuzz.ratio, score_cutoff=88)
            if not hit:
                continue
            cand = hit[0]
            if cand[0] != key[0] or abs(len(cand) - len(key)) > 2:
                continue
            seen_items.add(key)
            out.append(Hit(li, idx, idx + len(item), tax.fuzzy_owner[cand], item, fuzzy=True))
    return out


# --------------------------------------------------------------------------- aggregation

def _evidence_of(section: str) -> str:
    return _SECTION_TO_EVIDENCE.get(section, "other")


@dataclass
class SkillResult:
    skills: List[Skill]
    soft_skills: List[Skill]
    hits: List[Hit]
    by_index: Dict[int, Skill]          # taxonomy index -> aggregated skill (tech and soft)


def aggregate(hits: List[Hit], line_sections: Sequence[str], tax: Optional[Taxonomy] = None) -> SkillResult:
    tax = tax or get_taxonomy()
    agg: Dict[int, Skill] = {}
    order: List[int] = []
    for h in hits:
        sk = agg.get(h.skill)
        sec = line_sections[h.line] if h.line < len(line_sections) else ""
        ev = _evidence_of(sec)
        if sk is None:
            d = tax.skills[h.skill]
            sk = Skill(name=d["name"], category=d["category"], first_seen_in=sec or "header")
            agg[h.skill] = sk
            order.append(h.skill)
        sk.mentions += 1
        if ev not in sk.evidence_sources:
            sk.evidence_sources.append(ev)
        al = h.surface
        if al.lower() != sk.name.lower() and al not in sk.aliases_matched and len(sk.aliases_matched) < 8:
            sk.aliases_matched.append(al)
    # implications (transitive, explicit evidence inherited from implying skill)
    explicit = list(order)
    for idx in explicit:
        src = agg[idx]
        stack = list(tax.implies.get(idx, []))
        seen: Set[int] = set()
        while stack:
            t = stack.pop()
            if t in seen:
                continue
            seen.add(t)
            stack.extend(tax.implies.get(t, []))
            if t in agg:
                if agg[t].implied:      # explicit mentions keep their own evidence only
                    for ev in src.evidence_sources:
                        if ev not in agg[t].evidence_sources:
                            agg[t].evidence_sources.append(ev)
                continue
            d = tax.skills[t]
            ns = Skill(name=d["name"], category=d["category"], implied=True, first_seen_in=src.first_seen_in,
                       evidence_sources=list(src.evidence_sources))
            agg[t] = ns
            order.append(t)
    for sk in agg.values():
        srcs = sk.evidence_sources or ["other"]
        sk.evidence = min(srcs, key=lambda e: EVIDENCE_PRIORITY.index(e) if e in EVIDENCE_PRIORITY else 99)
    tech, soft = [], []
    for idx in order:
        (soft if agg[idx].category == "soft_skill" else tech).append(agg[idx])
    return SkillResult(tech, soft, hits, agg)


def closure(indices: Iterable[int], tax: Optional[Taxonomy] = None) -> Set[int]:
    """Skills plus everything they imply."""
    tax = tax or get_taxonomy()
    out: Set[int] = set()
    stack = list(indices)
    while stack:
        t = stack.pop()
        if t in out:
            continue
        out.add(t)
        stack.extend(tax.implies.get(t, []))
    return out


def find_skills_in_text(text: str, section: str = "experience") -> List[str]:
    """Convenience: canonical names of skills mentioned in ``text`` (single section)."""
    lines = [l for l in text.splitlines() if l.strip()]
    hits = match_lines(lines, [section] * len(lines), fuzzy=(section == "skills"))
    res = aggregate(hits, [section] * len(lines))
    return [s.name for s in res.skills + res.soft_skills]
