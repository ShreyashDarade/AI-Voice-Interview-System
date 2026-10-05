"""Contact extraction: name, e-mails, phones (E.164), location, links."""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .extract import Line
from .lexicons import (
    CA_PROVINCES, CITIES, COUNTRIES, INDIAN_STATES, LOCATION_WORDS, US_STATES, has_title_word,
)
from .models import Contact, Link, Phone

# --------------------------------------------------------------------------- email

_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")
_OBF_AT = re.compile(r"\s*[\[(]\s*at\s*[\])]\s*", re.I)
_OBF_DOT = re.compile(r"\s*[\[(]\s*dot\s*[\])]\s*", re.I)


def _valid_email(e: str) -> Optional[str]:
    try:
        from email_validator import EmailNotValidError, validate_email

        try:
            return validate_email(e, check_deliverability=False, test_environment=True).normalized.lower()
        except EmailNotValidError:
            return None
    except ImportError:  # pragma: no cover
        return e.lower() if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", e) else None


def find_emails(text: str) -> List[str]:
    if "[at]" in text.lower() or "(at)" in text.lower():
        text = _OBF_DOT.sub(".", _OBF_AT.sub("@", text))
    out: List[str] = []
    for m in _EMAIL_RE.finditer(text):
        cand = m.group(0).rstrip(".")
        # PDFs sometimes glue a following word onto the TLD ("a@b.comPhone")
        cand = re.sub(r"(\.(?:com|org|net|edu|io|in|co|ai|dev))(?=[A-Z][a-z])", r"\1", cand)
        v = _valid_email(cand)
        if v and v not in out:
            out.append(v)
    return out


# --------------------------------------------------------------------------- phone

_DATE_LIKE = re.compile(r"^\(?(?:19|20)\d{2}\)?\s*[-–—/.]\s*\(?(?:(?:19|20)\d{2}|\d{1,2})\)?$")
_DDMMYYYY = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2,4}$")


def find_phones(text: str, default_region: str = "US") -> List[Phone]:
    try:
        import phonenumbers
        from phonenumbers import Leniency, PhoneNumberFormat, PhoneNumberMatcher
    except ImportError:  # pragma: no cover
        return []
    out: Dict[str, Phone] = {}

    def add(num, raw: str, region: str) -> None:
        digits = re.sub(r"\D", "", raw)
        if not (7 <= len(digits) <= 15):
            return
        e164 = phonenumbers.format_number(num, PhoneNumberFormat.E164)
        if e164 not in out:
            out[e164] = Phone(e164=e164, raw=raw.strip(), region=phonenumbers.region_code_for_number(num) or region)

    def acceptable(raw: str) -> bool:
        r = raw.strip()
        if _DATE_LIKE.match(r) or _DDMMYYYY.match(r):
            return False
        digits = re.sub(r"\D", "", r)
        if len(digits) > 13 and not r.startswith("+"):
            return False
        if re.fullmatch(r"(?:19|20)\d{2}\D+(?:19|20)\d{2}", r):
            return False
        return True

    regions = [default_region]
    for line in text.splitlines():
        if not re.search(r"\d{5,}|\d[\d\s().-]{7,}\d", line):
            continue
        for region in regions:
            for m in PhoneNumberMatcher(line, region, leniency=Leniency.VALID):
                if acceptable(m.raw_string):
                    num = m.number
                    # "98450 12345": the 5+5 grouping is the Indian mobile convention, whatever the default region
                    if region != "IN" and re.fullmatch(r"[6-9]\d{4}[\s-]\d{5}", m.raw_string.strip()):
                        try:
                            cand = phonenumbers.parse(m.raw_string, "IN")
                            if phonenumbers.is_valid_number(cand):
                                num = cand
                        except Exception:
                            pass
                    add(num, m.raw_string, region)
        # Indian mobiles written without +91 while default region is elsewhere
        if default_region != "IN":
            for m in re.finditer(r"(?<![\d+])([6-9]\d{4}[\s-]?\d{5})(?!\d)", line):
                raw = m.group(1)
                try:
                    num = phonenumbers.parse(raw, "IN")
                except Exception:
                    continue
                if phonenumbers.is_valid_number(num) and not any(
                    re.sub(r"\D", "", raw) in re.sub(r"\D", "", k) for k in out
                ):
                    add(num, raw, "IN")
        # explicit international prefix that the VALID matcher rejected (rare formats)
        for m in re.finditer(r"\+\d{1,3}[\s().-]*\d[\d\s().-]{6,14}\d", line):
            raw = m.group(0)
            try:
                num = phonenumbers.parse(raw, None)
            except Exception:
                continue
            e164 = phonenumbers.format_number(num, PhoneNumberFormat.E164)
            if e164 not in out and phonenumbers.is_possible_number(num) and acceptable(raw):
                add(num, raw, "")
    return list(out.values())


# --------------------------------------------------------------------------- links

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.I)
_BARE_RE = re.compile(
    r"(?<![@\w./-])(?:www\.)?(?:linkedin\.com/(?:in|pub|company)/|github\.com/|gitlab\.com/|stackoverflow\.com/(?:users|story)/|"
    r"leetcode\.com/|kaggle\.com/|twitter\.com/|x\.com/|medium\.com/|behance\.net/|dribbble\.com/|hackerrank\.com/|"
    r"codeforces\.com/profile/|codechef\.com/users/|hashnode\.com/|dev\.to/)[\w\-./%@~]+",
    re.I,
)
_PERSONAL_DOMAIN_RE = re.compile(r"(?<![@\w./-])(?:[\w-]+\.github\.io|[\w-]+\.(?:dev|me|xyz|codes|page|site|portfolio)|[\w-]+\.(?:io|tech|com|in|net|org)/[\w\-./~]+)\b")


def classify_url(url: str) -> str:
    u = url.lower()
    host = urlparse(u if "//" in u else "//" + u).netloc or u.split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    if host.endswith("linkedin.com"):
        return "linkedin"
    if host.endswith("github.com"):
        return "github"
    if host.endswith("github.io"):
        return "portfolio"
    if host.endswith("gitlab.com"):
        return "gitlab"
    if host.endswith("stackoverflow.com") or host.endswith("stackexchange.com"):
        return "stackoverflow"
    if host.endswith("leetcode.com") or host.endswith("leetcode.cn"):
        return "leetcode"
    if host.endswith("kaggle.com"):
        return "kaggle"
    if host in ("twitter.com", "x.com", "mobile.twitter.com"):
        return "twitter"
    if host.endswith(("behance.net", "dribbble.com", "medium.com", "hashnode.com", "dev.to", "substack.com")):
        return "portfolio"
    if any(k in u for k in ("portfolio", "/projects")):
        return "portfolio"
    return "other"


def _canon(url: str) -> str:
    u = url.strip().rstrip("/.,;:)]}'\"")
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    return u.lower()


def find_links(
    lines: Sequence[Line],
    pdf_links: Sequence[Dict],
    header_idx: Callable[[int], bool],
    skip_idx: Callable[[int], bool],
    taxonomy_surfaces: Optional[set] = None,
) -> Tuple[List[Link], List[str], List[str]]:
    """Return ``(links, emails_from_mailto, phones_from_tel)``."""
    seen: Dict[str, Link] = {}
    mails: List[str] = []
    tels: List[str] = []

    def add(url: str, label: str = "") -> None:
        url = url.strip().rstrip(".,;:)]}'\"")
        if not url:
            return
        low = url.lower()
        if low.startswith("mailto:"):
            m = low[7:].split("?")[0]
            if m:
                mails.append(m)
            return
        if low.startswith("tel:"):
            tels.append(url[4:])
            return
        if low.startswith(("javascript:", "file:", "data:")):
            return
        if not re.match(r"^[a-z][a-z0-9+.-]*://", low):
            url = "https://" + url
        if not re.match(r"^https?://", url, re.I):
            return
        key = _canon(url)
        if key not in seen:
            seen[key] = Link(url=url, type=classify_url(url), label=label[:80])
        elif label and not seen[key].label:
            seen[key].label = label[:80]

    for lk in pdf_links:
        add(lk.get("url", ""), lk.get("label", ""))
    for i, ln in enumerate(lines):
        if skip_idx(i):
            continue
        text = ln.text
        for m in _URL_RE.finditer(text):
            add(m.group(0))
        for m in _BARE_RE.finditer(text):
            add(m.group(0))
        if header_idx(i):
            for m in _PERSONAL_DOMAIN_RE.finditer(text):
                frag = m.group(0)
                if "@" in text[max(0, m.start() - 1):m.start()] or frag.lower() in (taxonomy_surfaces or ()):
                    continue
                if classify_url(frag) != "other" or "/" in frag or frag.lower().endswith((".dev", ".me", ".xyz", ".codes", ".page", ".site", ".portfolio")) or ".github.io" in frag.lower():
                    add(frag)
    links = list(seen.values())[:25]
    return links, list(dict.fromkeys(mails)), tels


# --------------------------------------------------------------------------- location

_US_CODES = set(US_STATES) | set(CA_PROVINCES)
_NAME_SETS = {w.lower() for w in list(US_STATES.values()) + list(CA_PROVINCES.values()) + INDIAN_STATES + COUNTRIES + CITIES}
_LABEL_RE = re.compile(r"^(?:address|location|based in|city|current location|residence|permanent address|current address)\s*[:\-]\s*", re.I)
_CODE_RE = re.compile(r"((?:[A-Z][A-Za-z.'\-]+\s){0,2}[A-Z][A-Za-z.'\-]+),\s*([A-Z]{2})\b(?:\s+\d{5}(?:-\d{4})?)?")
_CITY_COMMA_RE = re.compile(r"((?:[A-Z][A-Za-z.'\-]+\s){0,2}[A-Z][A-Za-z.'\-]+),\s*((?:[A-Z][A-Za-z.'\-]+\s){0,2}[A-Z][A-Za-z.'\-]+)(?:,\s*((?:[A-Z][A-Za-z.'\-]+\s?){1,3}))?")


def find_location(lines: Sequence[Line], idxs: Sequence[int]) -> str:
    for i in idxs:
        raw = lines[i].text
        if "@" in raw:
            raw = re.sub(r"\S+@\S+", " ", raw)
        raw = re.sub(r"https?://\S+|www\.\S+", " ", raw)
        for frag in re.split(r"\s*(?:\||•|·|\t|;)\s*|\s{2,}", raw):
            frag = _LABEL_RE.sub("", frag.strip())
            if not frag or len(frag) > 90:
                continue
            if re.fullmatch(r"remote(?:\s*[,/-]\s*[A-Za-z ]+)?", frag, re.I):
                return frag.strip().title() if frag.islower() else frag.strip()
            ms = list(_CODE_RE.finditer(frag))
            for m in reversed(ms):
                if m.group(2) in _US_CODES:
                    return f"{m.group(1).strip()}, {m.group(2)}"
            for m in _CITY_COMMA_RE.finditer(frag):
                a, b, c = m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()
                if b.lower() in _NAME_SETS or a.lower() in _NAME_SETS:
                    if c and c.lower() in _NAME_SETS:
                        return f"{a}, {b}, {c}"
                    return f"{a}, {b}"
            low = frag.lower().strip(" ,.")
            if low in _NAME_SETS and low in {c.lower() for c in CITIES}:
                return frag.strip(" ,.")
    return ""


# --------------------------------------------------------------------------- name

_NAME_BLACKLIST = {
    "resume", "cv", "curriculum", "vitae", "profile", "contact", "summary", "email", "e-mail", "phone", "address",
    "linkedin", "github", "objective", "page", "portfolio", "mobile", "tel", "telephone", "references", "education",
    "experience", "skills", "projects", "about", "me", "name", "personal", "details", "information", "of", "the",
    "and", "career", "professional", "technical", "work", "history", "employment", "curricula", "dob", "date", "birth",
}
_PARTICLES = {"de", "da", "van", "von", "bin", "al", "el", "la", "le", "di", "du", "dos", "das", "mc", "mac", "ibn", "ben", "bint", "der", "den", "st."}
_CRED_RE = re.compile(r",?\s*\b(?:Ph\.?D\.?|MBA|M\.?S\.?|M\.?Sc\.?|B\.?Tech|M\.?Tech|B\.?E\.?|M\.?E\.?|PMP|CPA|CFA|MD|Esq\.?|P\.?Eng\.?|CISSP|AWS|PE|Jr\.?|Sr\.?)\b\.?\s*$")


def _name_fragment_ok(frag: str) -> bool:
    frag = frag.strip(" ,.-–—")
    if not frag or len(frag) > 45 or any(ch.isdigit() for ch in frag) or "@" in frag or "/" in frag or ":" in frag:
        return False
    toks = frag.split()
    if not 2 <= len(toks) <= 5:
        return False
    low = [t.lower().strip(".,") for t in toks]
    if any(t in _NAME_BLACKLIST for t in low):
        return False
    if has_title_word(frag):
        return False
    if all(t in LOCATION_WORDS for t in low) or frag.lower() in LOCATION_WORDS:
        return False
    caps = 0
    for t in toks:
        core = t.strip(".,'’")
        if not core:
            return False
        if not all(ch.isalpha() or ch in "'’.-" for ch in t):
            return False
        if core.lower() in _PARTICLES:
            continue
        if core[0].isupper():
            caps += 1
        else:
            return False
    return caps >= 2


def _name_from_line(text: str) -> str:
    for frag in re.split(r"\s*(?:\||•|·|\t|\s{2,}| - | – | — |\()\s*", text):
        frag = _CRED_RE.sub("", frag.strip())
        if _name_fragment_ok(frag):
            frag = frag.strip(" ,.-–—")
            if frag.isupper():
                frag = " ".join(w.capitalize() if w.lower() not in _PARTICLES else w.lower() for w in frag.split())
            return frag
    return ""


def _name_from_email(email: str) -> str:
    local = email.split("@")[0]
    parts = [p for p in re.split(r"[._\-+]+|\d+", local) if p]
    if len(parts) >= 2 and all(p.isalpha() and len(p) >= 2 for p in parts[:2]):
        return " ".join(p.capitalize() for p in parts[:2])
    return ""


def _name_from_links(links: Sequence[Link]) -> str:
    for lk in links:
        if lk.type == "linkedin":
            m = re.search(r"/in/([A-Za-z0-9\-_%]+)", lk.url)
            if m:
                slug = re.sub(r"-[0-9a-f]{6,}$", "", m.group(1))
                parts = [p for p in re.split(r"[-_%]+", slug) if p.isalpha() and len(p) >= 2]
                if len(parts) >= 2:
                    return " ".join(p.capitalize() for p in parts[:3])
    return ""


def extract_name(lines: Sequence[Line], first_heading_idx: int, emails: Sequence[str], links: Sequence[Link]) -> Tuple[str, float]:
    limit = min(first_heading_idx if first_heading_idx > 0 else 30, 40)
    cands: List[Tuple[float, int, str]] = []
    for i in range(min(limit, len(lines))):
        ln = lines[i]
        if ln.page != 1 or ln.ry0 > 0.45 or ln.source == "footer":
            continue
        if ln.is_bullet:
            continue
        nm = _name_from_line(ln.text)
        if nm:
            cands.append((ln.font_size + (0.3 if ln.is_bold else 0.0), i, nm))
    if cands:
        examined = [lines[i].font_size for i in range(min(limit, len(lines))) if lines[i].page == 1 and lines[i].ry0 <= 0.45]
        max_size = max(examined) if examined else 0.0
        size, i, nm = sorted(cands, key=lambda c: (-c[0], c[1]))[0]
        if size > 0 and max_size > 0:
            conf = 0.95 if size >= max_size - 0.01 else 0.8
        else:
            conf = 0.7
        if i > 10:
            conf -= 0.15
        return nm, max(conf, 0.5)
    for e in emails:
        nm = _name_from_email(e)
        if nm:
            return nm, 0.3
    nm = _name_from_links(links)
    if nm:
        return nm, 0.3
    return "", 0.0
