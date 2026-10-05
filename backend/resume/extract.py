"""Layout-aware text extraction for PDF, DOCX and TXT.

Every format is turned into the same representation: an ordered list of
:class:`Line` objects (text + font size/bold/colour/bbox/page metadata) plus
hyperlinks, hidden-text findings and document metadata.

PDF
    PyMuPDF ``get_text("dict")`` spans -> mini lines -> column detection
    (full-width bands + left/right columns) -> row merging (so
    ``Title ........ Dates`` stays one line) -> header/footer removal ->
    wrapped-line reflow and de-hyphenation.  Hidden text (white on white, tiny
    fonts, off-page) is *removed* from the lines and reported separately.
DOCX
    Direct, defensive XML walk (no DTD / entity resolution): body paragraphs,
    tables (layout tables are common), text boxes, headers/footers,
    hyperlinks via relationships, list bullets, heading styles.
TXT
    Encoding sniffing, underline rules -> ``rule_below``, indentation based reflow.
"""
from __future__ import annotations

import io
import re
import statistics
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import EmptyResume, EncryptedFile, MaliciousFile, ResumeParseError, TooManyPages
from .util import BULLET_CHARS, Deadline, count_nonprintable, normalize_line
from .validate import (
    Limits,
    decode_text,
    evaluate_pdf_active_content,
    safe_read_member,
    scan_pdf_objects,
    scan_pdf_raw,
)


# --------------------------------------------------------------------------- model

@dataclass
class Line:
    text: str
    page: int = 1
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    font_size: float = 0.0
    is_bold: bool = False
    color: int = 0
    is_bullet: bool = False
    heading_style: bool = False
    rule_below: bool = False
    numbered: bool = False          # the bullet was a list number ("1.") - may still be an entry header
    column: int = 0                 # 0 full width / unknown, 1 left, 2 right
    text_x0: float = 0.0            # x of the first text char after any bullet
    ry0: float = 0.0                # relative vertical position on the page (0..1)
    ry1: float = 0.0
    blank_before: bool = False
    source: str = "body"            # body | header | footer | table | textbox | ocr

    @property
    def is_upper(self) -> bool:
        t = self.text
        return t.isupper() and sum(1 for c in t if c.isalpha()) >= 2


@dataclass
class ExtractedDoc:
    lines: List[Line] = field(default_factory=list)
    links: List[Dict[str, Any]] = field(default_factory=list)       # {url,label,page}
    pages: int = 1
    method: str = ""
    ocr_used: bool = False
    hidden: List[Dict[str, Any]] = field(default_factory=list)       # {text,reason,page}
    warnings: List[str] = field(default_factory=list)
    flags: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    nonprintable: int = 0
    raw_chars: int = 0
    has_photo: bool = False
    encoding: str = ""
    pdf_tokens: Dict[str, int] = field(default_factory=dict)

    def text(self) -> str:
        return lines_to_text(self.lines)


def lines_to_text(lines: List[Line]) -> str:
    out = []
    for ln in lines:
        t = ln.text.replace("\t", "  ")
        out.append(("• " + t) if ln.is_bullet else t)
    return "\n".join(out)


# --------------------------------------------------------------------------- shared helpers

_BUL_RE = re.compile(
    r"^\s*(?:[" + re.escape(BULLET_CHARS) + r"]+|[-–—*+](?=\s)|\d{1,2}[.)](?=\s+[A-Z])|[a-z][.)](?=\s+[A-Z]))\s*"
)
_PAGE_NUM_RE = re.compile(r"^(?:page\s*)?\d{1,3}(?:\s*(?:/|of)\s*\d{1,3})?$", re.I)
_RULE_LINE_RE = re.compile(r"^[\s\-=_*~#.•·▬─━═]{3,}$")
_KEEP_HYPHEN = {
    "full", "high", "low", "cross", "multi", "self", "end", "real", "open", "large", "small", "non",
    "well", "data", "state", "front", "back", "micro", "e", "x", "t", "co", "ci", "re", "pre", "post",
    "anti", "semi", "inter", "intra", "cloud", "test", "event", "user", "customer", "client", "server",
    "time", "long", "short", "hands", "first", "second", "third", "mid", "top", "up", "down", "in",
    "on", "off", "one", "two", "three", "four", "five", "ten", "cost", "fault", "ai", "ml", "api",
    "web", "mobile", "cross", "result", "goal", "detail", "problem", "decision", "fast", "quick",
    "day", "year", "month", "part", "double", "single", "peer", "team", "built", "oriented", "driven",
}


_NUM_MARK_RE = re.compile(r"^\s*(?:\d{1,2}[.)]|[a-z][.)])\s+")


def strip_bullet(text: str) -> Tuple[str, bool]:
    m = _BUL_RE.match(text)
    if m and m.end() < len(text):
        return text[m.end():], True
    return text, False


def is_numbered_marker(raw: str) -> bool:
    """True when the stripped marker was a list number ("1." / "a)") rather than a symbol bullet."""
    return bool(_NUM_MARK_RE.match(raw))


def _join_wrapped(a: str, b: str) -> str:
    """Join two physical lines of the same logical line, de-hyphenating."""
    if a.endswith("-") and len(a) >= 3 and a[-2].isalpha() and b[:1].islower():
        stem = re.split(r"[\s/(,]", a[:-1])[-1].lower()
        if stem in _KEEP_HYPHEN or len(stem) <= 1:
            return a + b
        return a[:-1] + b
    if a.endswith("-") and b[:1].islower():
        return a + b
    return a + " " + b


def _starts_lower(s: str) -> bool:
    return bool(s) and s[0].islower()


def _merge_into(a: Line, b: Line) -> None:
    a.text = _join_wrapped(a.text, b.text)
    x0 = min(a.bbox[0], b.bbox[0]); y0 = min(a.bbox[1], b.bbox[1])
    x1 = max(a.bbox[2], b.bbox[2]); y1 = max(a.bbox[3], b.bbox[3])
    a.bbox = (x0, y0, x1, y1)
    a.ry1 = max(a.ry1, b.ry1)


def _ends_sentence(s: str) -> bool:
    return bool(re.search(r"[.:;!?]\s*$", s))


def reflow_pdf_lines(lines: List[Line]) -> List[Line]:
    """Join wrapped bullet / paragraph continuation lines (geometry aware).

    * Bullets: a following non-bullet line is a continuation when it sits on the hanging indent
      (or starts lower-case) and the vertical gap is line-spacing sized.
    * Plain paragraphs are joined only when the previous line is a hyphen-wrap, or it is a *full*
      line (reaches the right edge of its column) of >= 8 words and the next one starts lower-case.
    """
    colmax: Dict[Tuple[int, int], float] = {}
    for ln in lines:
        if len(ln.text.split()) >= 5:
            k = (ln.page, ln.column)
            colmax[k] = max(colmax.get(k, 0.0), ln.bbox[2])
    out: List[Line] = []
    for ln in lines:
        if not out:
            out.append(ln)
            continue
        a = out[-1]
        joinable = (
            ln.page == a.page
            and ln.column == a.column
            and not ln.is_bullet
            and not ln.rule_below
            and "\t" not in ln.text
            and "\t" not in a.text
            and abs(ln.font_size - a.font_size) <= 0.6
            and ln.is_bold == a.is_bold
            and not a.rule_below
            and len(ln.text) > 0
        )
        if joinable:
            size = max(a.font_size, 1.0)
            gap = ln.bbox[1] - a.bbox[3]
            tight = -size * 0.3 <= gap <= size * 0.65
            hanging = ln.bbox[0] >= a.text_x0 - 3 if a.is_bullet else False
            if tight and not ln.is_upper and not a.is_upper:
                if a.is_bullet and (hanging or _starts_lower(ln.text)) and len(a.text) > 12 and (not a.numbered or _starts_lower(ln.text)):
                    _merge_into(a, ln)
                    continue
                if not a.is_bullet and _starts_lower(ln.text):
                    right = colmax.get((a.page, a.column), a.bbox[2])
                    col_w = max(right - a.bbox[0], 1.0)
                    full = a.bbox[2] >= right - 0.08 * col_w
                    hyphen_wrap = a.text.endswith("-") and len(a.text) > 3 and a.text[-2].isalpha()
                    if abs(ln.bbox[0] - a.bbox[0]) < 12 and (
                        hyphen_wrap or (full and len(a.text.split()) >= 8 and not _ends_sentence(a.text))
                    ):
                        _merge_into(a, ln)
                        continue
        out.append(ln)
    return out


def reflow_text_lines(lines: List[Line]) -> List[Line]:
    """Reflow for plain text: indentation / lower-case continuation of bullets."""
    out: List[Line] = []
    for ln in lines:
        if out:
            a = out[-1]
            cont = (
                not ln.is_bullet and not ln.blank_before and not ln.rule_below and "\t" not in ln.text
                and a.is_bullet and not a.rule_below
                and (not a.numbered or _starts_lower(ln.text))
                and (ln.text_x0 > a.bbox[0] or _starts_lower(ln.text))
                and len(a.text) > 12
            )
            if cont:
                _merge_into(a, ln)
                continue
        out.append(ln)
    return out


# --------------------------------------------------------------------------- TXT

def extract_txt(data: bytes) -> ExtractedDoc:
    text, enc = decode_text(data)
    doc = ExtractedDoc(method="plain-text", encoding=enc, pages=1)
    doc.nonprintable = count_nonprintable(text)
    doc.raw_chars = len(text)
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blank = False
    lines: List[Line] = []
    n = len(raw_lines)
    for i, raw in enumerate(raw_lines):
        stripped = raw.strip()
        if not stripped:
            blank = True
            continue
        if _RULE_LINE_RE.match(stripped) and len(stripped) >= 3:
            if lines and not blank:
                lines[-1].rule_below = True
            blank = True  # a rule behaves like a paragraph break
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        norm = normalize_line(raw)
        if not norm:
            continue
        body, bullet = strip_bullet(norm)
        ln = Line(
            text=body.strip(),
            bbox=(float(indent), float(i), float(indent) + len(body), float(i)),
            is_bullet=bullet,
            numbered=bullet and is_numbered_marker(norm),
            text_x0=float(indent) + (2 if bullet else 0),
            blank_before=blank,
            ry0=i / max(n, 1),
            ry1=(i + 1) / max(n, 1),
        )
        if not ln.text:
            continue
        lines.append(ln)
        blank = False
    doc.lines = reflow_text_lines(lines)
    return doc


# --------------------------------------------------------------------------- PDF

_URL_NOISE = re.compile(r"\s+")


@dataclass
class _Item:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    bold: bool
    color: int
    chars: int


def _lum(rgb: Tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def _is_near_white(color: int) -> bool:
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return min(r, g, b) >= 240


def _span_is_bold(span: dict) -> bool:
    if span.get("flags", 0) & 16:
        return True
    f = (span.get("font") or "").lower()
    return any(k in f for k in ("bold", "black", "heavy", "semibold", "demi", "extrabold"))


def _gather_page_graphics(page: Any, W: float, H: float) -> Tuple[List[Tuple[Any, float]], List[Tuple[float, float, float]], List[Tuple[float, float, float, float]]]:
    """Return (filled rects with luminance, horizontal rules, image rects)."""
    fills: List[Tuple[Any, float]] = []
    rules: List[Tuple[float, float, float]] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings[:4000]:
        r = d.get("rect")
        if r is None:
            continue
        fill = d.get("fill")
        if fill is not None and len(fill) >= 3:
            lum = _lum(fill[:3])
            if lum < 0.85 and r.width > 3 and r.height > 3:
                fills.append((r, lum))
        h = r.height
        w = r.width
        if w >= 30 and h <= 2.5:
            rules.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
        elif fill is None and w >= 30 and h <= 3:
            rules.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
    imgs: List[Tuple[float, float, float, float]] = []
    try:
        for info in page.get_image_info():
            bb = info.get("bbox")
            if bb:
                imgs.append(tuple(bb))
    except Exception:
        pass
    return fills, rules, imgs


def _on_dark_background(bbox: Tuple[float, float, float, float], fills, imgs, page_area: float) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for r, _lum_v in fills:
        if r.x0 - 1 <= cx <= r.x1 + 1 and r.y0 - 1 <= cy <= r.y1 + 1:
            return True
    for x0, y0, x1, y1 in imgs:
        if x0 <= cx <= x1 and y0 <= cy <= y1 and (x1 - x0) * (y1 - y0) > 0.03 * page_area:
            return True
    return False


def _page_items(page: Any, pno: int, hidden: List[Dict[str, Any]], pymupdf: Any) -> Tuple[List[_Item], List[Tuple[float, float, float]], int, int, List[Tuple[float, float, float, float]]]:
    W, H = float(page.rect.width), float(page.rect.height)
    area = W * H
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_MEDIABOX_CLIP & ~pymupdf.TEXT_PRESERVE_IMAGES
    d = page.get_text("dict", flags=flags, clip=pymupdf.INFINITE_RECT())
    fills, rules, imgs = _gather_page_graphics(page, W, H)
    items: List[_Item] = []
    raw_chars = 0
    nonprint = 0
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            parts: List[Tuple[str, dict]] = []
            for sp in spans:
                t = sp.get("text", "")
                if not t:
                    continue
                raw_chars += len(t)
                nonprint += count_nonprintable(t) + len(re.findall(r"\(cid:\d+\)", t))
                bb = sp["bbox"]
                reason = None
                if sp.get("size", 10) < 3.0 and t.strip():
                    reason = "tiny_font"
                elif t.strip() and (bb[2] < -2 or bb[0] > W + 2 or bb[3] < -2 or bb[1] > H + 2):
                    reason = "off_page"
                elif t.strip() and _is_near_white(sp.get("color", 0)) and not _on_dark_background(bb, fills, imgs, area):
                    reason = "white_text"
                if reason:
                    if t.strip():
                        hidden.append({"text": t.strip(), "reason": reason, "page": pno})
                    continue
                parts.append((t, sp))
            if not parts:
                continue
            # join spans
            text = ""
            prev = None
            for t, sp in parts:
                if prev is not None:
                    ptext, psp = prev
                    gap = sp["bbox"][0] - psp["bbox"][2]
                    size = max(sp.get("size", 10), 1.0)
                    if not (text.endswith(" ") or t.startswith(" ")):
                        if gap > 1.6 * size:
                            text += "\t"
                        elif gap > 0.12 * size:
                            text += " "
                text += t
                prev = (t, sp)
            if not text.strip():
                continue
            xs0 = min(sp["bbox"][0] for _, sp in parts)
            ys0 = min(sp["bbox"][1] for _, sp in parts)
            xs1 = max(sp["bbox"][2] for _, sp in parts)
            ys1 = max(sp["bbox"][3] for _, sp in parts)
            # dominant size / colour / bold by char weight
            sizes: Dict[float, int] = {}
            colors: Dict[int, int] = {}
            bold_chars = 0
            total = 0
            for t, sp in parts:
                n = len(t.strip())
                if not n:
                    continue
                total += n
                sizes[round(sp.get("size", 10), 1)] = sizes.get(round(sp.get("size", 10), 1), 0) + n
                colors[sp.get("color", 0)] = colors.get(sp.get("color", 0), 0) + n
                if _span_is_bold(sp):
                    bold_chars += n
            if not sizes:
                continue
            size = max(sizes.items(), key=lambda kv: kv[1])[0]
            color = max(colors.items(), key=lambda kv: kv[1])[0]
            items.append(_Item(xs0, ys0, xs1, ys1, text, size, bold_chars >= 0.6 * total, color, total))
    return items, rules, raw_chars, nonprint, imgs


def _detect_gutter(items: List[_Item], W: float) -> Optional[float]:
    """Return x of a vertical gutter splitting the page into two columns, else None."""
    if len(items) < 8:
        return None
    narrow = [it for it in items if (it.x1 - it.x0) < 0.62 * W]
    if len(narrow) < 6:
        return None
    lo, hi = 0.12 * W, 0.88 * W
    # only gaps *between* content count (not the page margins)
    lo = max(lo, min(it.x0 for it in narrow) + 6)
    hi = min(hi, max(it.x1 for it in narrow) - 6)
    step = 2.0
    bins = int(W / step) + 2
    cov = [0] * bins
    for it in narrow:
        a = max(0, int(it.x0 / step))
        b = min(bins - 1, int(it.x1 / step))
        for i in range(a, b + 1):
            cov[i] += 1
    tol = max(0, int(0.06 * len(narrow)))        # tolerate a few full-width header lines
    best = None
    i = int(lo / step)
    end = int(hi / step)
    while i < end:
        if cov[i] <= tol:
            j = i
            while j < end and cov[j] <= tol:
                j += 1
            width = (j - i) * step
            if width >= 8 and (best is None or width > best[0]):
                best = (width, (i + j) / 2 * step)
            i = j
        else:
            i += 1
    if best is None:
        return None
    g = best[1]
    left = [it for it in items if it.x1 <= g + 3]
    right = [it for it in items if it.x0 >= g - 3]
    cross = [it for it in items if it.x0 < g - 3 and it.x1 > g + 3]
    n = len(items)
    if len(left) < 4 or len(right) < 4:
        return None
    if len(left) < 0.15 * n or len(right) < 0.15 * n:
        return None
    if len(cross) > 0.2 * n:
        return None
    lw = max(it.x1 for it in left) - min(it.x0 for it in left)
    rw = max(it.x1 for it in right) - min(it.x0 for it in right)
    if lw < 0.09 * W or rw < 0.09 * W:
        return None
    # date-gutter (timeline layouts) -> keep row reading order
    date_re = re.compile(r"(?:19|20)\d{2}|present|current|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", re.I)
    for side in (left, right):
        datey = sum(1 for it in side if date_re.search(it.text) and len(it.text) < 28)
        if datey >= 0.6 * len(side):
            return None
    return g


def _rows(items: List[_Item]) -> List[List[_Item]]:
    """Group items that sit on the same visual row."""
    items = sorted(items, key=lambda it: (round((it.y0 + it.y1) / 2, 1), it.x0))
    rows: List[List[_Item]] = []
    cur: List[_Item] = []
    cy0 = cy1 = 0.0
    for it in items:
        if cur:
            ov = min(cy1, it.y1) - max(cy0, it.y0)
            mh = min(cy1 - cy0, it.y1 - it.y0)
            if mh > 0 and ov >= 0.5 * mh:
                cur.append(it)
                cy0 = min(cy0, it.y0); cy1 = max(cy1, it.y1)
                continue
            rows.append(cur)
        cur = [it]
        cy0, cy1 = it.y0, it.y1
    if cur:
        rows.append(cur)
    return rows


def _row_to_line(row: List[_Item], pno: int, W: float, H: float, column: int) -> Optional[Line]:
    row = sorted(row, key=lambda it: it.x0)
    text = ""
    prev: Optional[_Item] = None
    for it in row:
        t = it.text
        if prev is not None:
            gap = it.x0 - prev.x1
            if not (text.endswith((" ", "\t")) or t.startswith((" ", "\t"))):
                text += "\t" if gap > 1.4 * max(it.size, 1) else " "
        text += t
        prev = it
    norm = normalize_line(text)
    if not norm:
        return None
    body, bullet = strip_bullet(norm)
    if not body.strip():
        return None
    x0 = min(i.x0 for i in row); y0 = min(i.y0 for i in row)
    x1 = max(i.x1 for i in row); y1 = max(i.y1 for i in row)
    text_x0 = x0
    if bullet:
        first = row[0].text.strip()
        if len(row) > 1 and (not first or _BUL_RE.match(first + " x")) and len(first) <= 2:
            text_x0 = row[1].x0
        else:
            text_x0 = x0 + 0.9 * row[0].size
    chars: Dict[float, int] = {}
    cols: Dict[int, int] = {}
    bold = 0
    tot = 0
    for it in row:
        chars[it.size] = chars.get(it.size, 0) + it.chars
        cols[it.color] = cols.get(it.color, 0) + it.chars
        tot += it.chars
        if it.bold:
            bold += it.chars
    size = max(chars.items(), key=lambda kv: kv[1])[0]
    color = max(cols.items(), key=lambda kv: kv[1])[0]
    return Line(
        text=body.strip(), page=pno, bbox=(x0, y0, x1, y1), font_size=size,
        is_bold=bold >= 0.5 * max(tot, 1), color=color, is_bullet=bullet, column=column,
        numbered=bullet and is_numbered_marker(norm),
        text_x0=text_x0, ry0=y0 / max(H, 1), ry1=y1 / max(H, 1),
    )


def order_page(items: List[_Item], pno: int, W: float, H: float) -> List[Line]:
    """Reading order: full-width bands on top of / between left-then-right columns."""
    if not items:
        return []
    g = _detect_gutter(items, W)
    out: List[Line] = []
    if g is None:
        for row in _rows(items):
            ln = _row_to_line(row, pno, W, H, 0)
            if ln:
                out.append(ln)
        return out
    items = sorted(items, key=lambda it: (it.y0, it.x0))

    def side(it: _Item) -> int:
        if it.x0 < g - 3 and it.x1 > g + 3:
            return 0
        return 1 if it.x1 <= g + 3 else 2

    segments: List[Tuple[int, List[_Item]]] = []
    for it in items:
        kind = 0 if side(it) == 0 else 1
        if segments and segments[-1][0] == kind:
            segments[-1][1].append(it)
        else:
            segments.append((kind, [it]))
    for kind, seg in segments:
        if kind == 0:
            for row in _rows(seg):
                ln = _row_to_line(row, pno, W, H, 0)
                if ln:
                    out.append(ln)
        else:
            for col in (1, 2):
                sub = [it for it in seg if side(it) == col]
                for row in _rows(sub):
                    ln = _row_to_line(row, pno, W, H, col)
                    if ln:
                        out.append(ln)
    return out


def _mark_rules(lines: List[Line], rules: List[Tuple[float, float, float]]) -> None:
    for ln in lines:
        x0, y0, x1, y1 = ln.bbox
        for rx0, rx1, ry in rules:
            if y1 - 2 <= ry <= y1 + 8 and min(x1, rx1) - max(x0, rx0) >= 0.5 * max(x1 - x0, 1):
                ln.rule_below = True
                break


def _remove_repeated_margins(lines: List[Line], pages: int) -> List[Line]:
    """Drop page numbers and header/footer lines repeated across pages."""
    if not lines:
        return lines

    def key(t: str) -> str:
        return re.sub(r"\d+", "#", t.lower()).strip()

    margin: Dict[str, set] = {}
    for ln in lines:
        zone = "h" if ln.ry1 < 0.09 else ("f" if ln.ry0 > 0.91 else "")
        if zone:
            margin.setdefault(zone + key(ln.text), set()).add(ln.page)
    repeated = {k for k, pg in margin.items() if pages >= 2 and len(pg) >= 2 and len(pg) >= 0.5 * pages}
    out = []
    for ln in lines:
        zone = "h" if ln.ry1 < 0.09 else ("f" if ln.ry0 > 0.91 else "")
        if zone:
            if _PAGE_NUM_RE.match(ln.text.strip()):
                continue
            if (zone + key(ln.text)) in repeated and (ln.page >= 2 or zone == "f"):
                continue
        out.append(ln)
    return out


def _parse_pdf_date(s: str) -> str:
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", s or "")
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2) or "01", m.group(3) or "01"
    return f"{y}-{mo}-{d}"


def _load_ocr() -> Optional[Callable[[bytes, Optional[int]], str]]:
    """Return an OCR callable ``png_bytes, timeout -> text`` or ``None`` if unavailable."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        pytesseract.get_tesseract_version()
    except Exception:
        return None

    def run(png: bytes, timeout: Optional[int] = None) -> str:
        img = Image.open(io.BytesIO(png))
        kwargs = {"timeout": timeout} if timeout else {}
        return pytesseract.image_to_string(img, **kwargs)

    return run


def extract_pdf(data: bytes, limits: Limits, deadline: Deadline, ocr: bool = True) -> ExtractedDoc:
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        import fitz as pymupdf  # type: ignore

    try:
        prev_display = pymupdf.TOOLS.mupdf_display_errors()
        pymupdf.TOOLS.mupdf_display_errors(False)          # hostile PDFs would otherwise spam stderr
    except Exception:
        prev_display = None
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        _restore_display(pymupdf, prev_display)
        raise ResumeParseError(f"PDF could not be opened: {str(exc)[:120]}") from exc
    try:
        if doc.needs_pass:
            raise EncryptedFile("PDF is password protected; remove the password and upload again.")
        n_pages = doc.page_count
        if n_pages <= 0:
            raise ResumeParseError("PDF has no pages")
        if n_pages > limits.hard_max_pages:
            raise TooManyPages(f"PDF has {n_pages} pages; the hard limit is {limits.hard_max_pages}.")
        out = ExtractedDoc(method="pymupdf-dict", pages=n_pages)

        # active content
        raw = scan_pdf_raw(data)
        try:
            objs = scan_pdf_objects(doc)
        except Exception:
            objs = {}
        merged, fatal, flags = evaluate_pdf_active_content(raw, objs)
        out.pdf_tokens = {k: v for k, v in merged.items() if v}
        out.flags.extend(flags)
        if fatal:
            raise MaliciousFile(fatal)
        deadline.check("pdf-scan")

        read_pages = min(n_pages, limits.max_pages)
        if n_pages > read_pages:
            out.warnings.append("truncated_pages")
            out.flags.append({
                "code": "truncated_pages", "severity": "info",
                "message": f"Only the first {read_pages} of {n_pages} pages were analysed.",
                "details": {"pages": n_pages, "analysed": read_pages},
            })
        md = doc.metadata or {}
        out.metadata = {
            "producer": md.get("producer") or "", "creator": md.get("creator") or "",
            "title": md.get("title") or "", "author": md.get("author") or "",
            "created": _parse_pdf_date(md.get("creationDate") or ""),
            "modified": _parse_pdf_date(md.get("modDate") or ""),
            "pages": n_pages,
        }

        all_lines: List[Line] = []
        page_text_chars: Dict[int, int] = {}
        for pno in range(read_pages):
            deadline.check(f"pdf-page-{pno + 1}")
            page = doc[pno]
            W, H = float(page.rect.width), float(page.rect.height)
            items, rules, raw_chars, nonprint, imgs = _page_items(page, pno + 1, out.hidden, pymupdf)
            out.raw_chars += raw_chars
            out.nonprintable += nonprint
            plines = order_page(items, pno + 1, W, H)
            _mark_rules(plines, rules)
            page_text_chars[pno + 1] = sum(len(l.text) for l in plines)
            all_lines.extend(plines)
            if pno == 0:
                area = W * H
                for x0, y0, x1, y1 in imgs:
                    w, h = x1 - x0, y1 - y0
                    if 0.008 * area < w * h < 0.22 * area and 0.55 <= (w / max(h, 1)) <= 1.5 and y0 < 0.45 * H:
                        out.has_photo = True
            try:
                for lk in page.get_links():
                    uri = lk.get("uri")
                    if uri and lk.get("kind") == pymupdf.LINK_URI:
                        label = ""
                        try:
                            label = re.sub(r"\s+", " ", page.get_textbox(lk["from"])).strip()[:120]
                        except Exception:
                            pass
                        out.links.append({"url": uri.strip(), "label": label, "page": pno + 1})
            except Exception:
                out.warnings.append("link_extraction_failed")

        # sparse -> OCR
        total_chars = sum(page_text_chars.values())
        sparse = total_chars < 200 * max(read_pages, 1)
        if sparse:
            sparse_pages = [p for p, c in page_text_chars.items() if c < 100]
            runner = _load_ocr() if ocr else None
            if runner is None:
                out.warnings.append("ocr_unavailable_scanned_pdf" if ocr else "sparse_text_ocr_disabled")
            else:
                for pno1 in sparse_pages:
                    deadline.check(f"ocr-page-{pno1}")
                    page = doc[pno1 - 1]
                    try:
                        png = page.get_pixmap(dpi=200).tobytes("png")
                        rem = deadline.remaining()
                        txt = runner(png, int(rem) if rem != float("inf") and rem >= 1 else None)
                    except Exception as exc:
                        out.warnings.append("ocr_failed")
                        continue
                    ocr_lines = _ocr_text_to_lines(txt, pno1)
                    if sum(len(l.text) for l in ocr_lines) > page_text_chars.get(pno1, 0):
                        all_lines = [l for l in all_lines if l.page != pno1] + ocr_lines
                        out.ocr_used = True
                if out.ocr_used:
                    all_lines.sort(key=lambda l: l.page)  # stable: keeps in-page order
                    out.method = "pymupdf-dict+ocr"
            if not all_lines:
                raise EmptyResume(
                    "No extractable text: this looks like a scanned/image-only PDF and OCR is unavailable"
                    if (not out.ocr_used) else "OCR produced no text"
                )
        if not out.ocr_used:
            all_lines = _remove_repeated_margins(all_lines, read_pages)
            all_lines = reflow_pdf_lines(all_lines)
        out.lines = all_lines
        if not out.lines:
            raise EmptyResume("PDF contains no visible text")
        return out
    finally:
        try:
            doc.close()
        except Exception:
            pass
        _restore_display(pymupdf, prev_display)


def _restore_display(pymupdf: Any, prev: Optional[bool]) -> None:
    if prev is not None:
        try:
            pymupdf.TOOLS.mupdf_display_errors(prev)
        except Exception:
            pass


def _ocr_text_to_lines(txt: str, page: int) -> List[Line]:
    lines: List[Line] = []
    raw = (txt or "").splitlines()
    n = max(len(raw), 1)
    for i, r in enumerate(raw):
        norm = normalize_line(r)
        if not norm:
            continue
        body, bullet = strip_bullet(norm)
        if body.strip():
            lines.append(Line(text=body.strip(), page=page, is_bullet=bullet, source="ocr",
                              ry0=i / n, ry1=(i + 1) / n, bbox=(0.0, float(i), 0.0, float(i))))
    return reflow_text_lines(lines)


# --------------------------------------------------------------------------- DOCX

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _xml_parser():
    from lxml import etree  # type: ignore

    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
        huge_tree=False, remove_blank_text=False, recover=False,
    )


def _parse_xml(blob: bytes):
    from lxml import etree  # type: ignore

    head = blob[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise MaliciousFile("DOCX XML contains a DTD / entity declaration (not produced by Word).")
    try:
        return etree.fromstring(blob, _xml_parser())
    except etree.XMLSyntaxError as exc:
        raise ResumeParseError(f"DOCX XML is malformed: {str(exc)[:100]}") from exc


def _near_white_hex(val: Optional[str]) -> bool:
    if not val or len(val) != 6:
        return False
    try:
        r, g, b = int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
    except ValueError:
        return False
    return min(r, g, b) >= 240


def _dark_hex(val: Optional[str]) -> bool:
    if not val or len(val) != 6:
        return False
    try:
        r, g, b = int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
    except ValueError:
        return False
    return _lum((r / 255, g / 255, b / 255)) < 0.6


def _on(el, tag: str) -> bool:
    """OOXML boolean property (<w:b/> == true, w:val=0/false/off == false)."""
    node = el.find(_q(_W, tag)) if el is not None else None
    if node is None:
        return False
    v = node.get(_q(_W, "val"))
    return v is None or v.lower() not in ("0", "false", "off", "none")


class _DocxCtx:
    def __init__(self, zf: zipfile.ZipFile, limits: Limits, deadline: Deadline):
        self.zf = zf
        self.limits = limits
        self.deadline = deadline
        self.styles: Dict[str, Dict[str, Any]] = {}
        self.default_size = 11.0
        self.hidden: List[Dict[str, Any]] = []
        self.links: List[Dict[str, Any]] = []
        self.has_photo = False
        self.nonprint = 0
        self.raw_chars = 0
        self.rels: Dict[str, str] = {}

    def load_rels(self, part: str) -> Dict[str, str]:
        d, _, f = part.rpartition("/")
        relname = f"{d}/_rels/{f}.rels" if d else f"_rels/{f}.rels"
        out: Dict[str, str] = {}
        try:
            root = _parse_xml(safe_read_member(self.zf, relname, self.limits))
        except KeyError:
            return out
        except MaliciousFile:
            raise
        except ResumeParseError:
            return out
        for rel in root:
            rid, target, mode = rel.get("Id"), rel.get("Target"), rel.get("TargetMode")
            if rid and target and (mode == "External" or "hyperlink" in (rel.get("Type") or "")):
                out[rid] = target
        return out

    def load_styles(self) -> None:
        try:
            root = _parse_xml(safe_read_member(self.zf, "word/styles.xml", self.limits))
        except KeyError:
            return
        except MaliciousFile:
            raise
        except ResumeParseError:
            return
        dd = root.find(f"{_q(_W, 'docDefaults')}/{_q(_W, 'rPrDefault')}/{_q(_W, 'rPr')}/{_q(_W, 'sz')}")
        if dd is not None and dd.get(_q(_W, "val")):
            try:
                self.default_size = int(dd.get(_q(_W, "val"))) / 2
            except ValueError:
                pass
        for st in root.findall(_q(_W, "style")):
            sid = st.get(_q(_W, "styleId")) or ""
            name_el = st.find(_q(_W, "name"))
            name = (name_el.get(_q(_W, "val")) if name_el is not None else "") or sid
            rpr = st.find(_q(_W, "rPr"))
            size = None
            if rpr is not None and rpr.find(_q(_W, "sz")) is not None:
                try:
                    size = int(rpr.find(_q(_W, "sz")).get(_q(_W, "val"))) / 2
                except (TypeError, ValueError):
                    size = None
            ppr_s = st.find(_q(_W, "pPr"))
            numbered = (ppr_s is not None and ppr_s.find(_q(_W, "numPr")) is not None) or bool(
                re.match(r"list\s+(?:bullet|number|paragraph|continue)", name.lower()))
            self.styles[sid] = {
                "name": name, "size": size, "bold": _on(rpr, "b") if rpr is not None else False, "numbered": numbered,
                "heading": bool(re.match(r"(heading\s*\d|title|subtitle)", name.lower())) or sid.lower().startswith(("heading", "title")),
            }


def _run_visible_props(r, ctx: _DocxCtx, para_dark: bool) -> Tuple[bool, Optional[float], bool]:
    """Return (hidden?, size, bold) for a run."""
    rpr = r.find(_q(_W, "rPr"))
    hidden = False
    size = None
    bold = False
    if rpr is not None:
        if _on(rpr, "vanish") or _on(rpr, "webHidden"):
            hidden = True
        col = rpr.find(_q(_W, "color"))
        if col is not None and _near_white_hex((col.get(_q(_W, "val")) or "").lower().replace("#", "")):
            shd = rpr.find(_q(_W, "shd"))
            fill = (shd.get(_q(_W, "fill")) if shd is not None else None) or ""
            if not (para_dark or _dark_hex(fill.lower())):
                hidden = True
        sz = rpr.find(_q(_W, "sz"))
        if sz is not None:
            try:
                size = int(sz.get(_q(_W, "val"))) / 2
            except (TypeError, ValueError):
                size = None
            if size is not None and size < 3.0:
                hidden = True
        bold = _on(rpr, "b")
    return hidden, size, bold


def _iter_runs(p):
    """Yield run-like elements in order, skipping text boxes and mc:Fallback."""
    stack = list(reversed(list(p)))
    while stack:
        el = stack.pop()
        tag = el.tag
        if tag in (_q(_MC, "Fallback"), _q(_W, "txbxContent"), _q(_W, "del"), _q(_W, "pPr")):
            continue
        if tag == _q(_W, "r"):
            yield el
            continue
        if isinstance(tag, str) and tag.startswith("{"):
            stack.extend(reversed(list(el)))


def _para_lines(p, ctx: _DocxCtx, source: str, rels: Dict[str, str]) -> List[Line]:
    ppr = p.find(_q(_W, "pPr"))
    style_id = ""
    bullet = False
    para_dark = False
    if ppr is not None:
        ps = ppr.find(_q(_W, "pStyle"))
        if ps is not None:
            style_id = ps.get(_q(_W, "val")) or ""
        bullet = ppr.find(_q(_W, "numPr")) is not None
        shd = ppr.find(_q(_W, "shd"))
        if shd is not None and _dark_hex((shd.get(_q(_W, "fill")) or "").lower()):
            para_dark = True
    style = ctx.styles.get(style_id, {})
    if style.get("numbered") and (ppr is None or ppr.find(_q(_W, "numPr")) is None) and "continue" not in (style.get("name") or "").lower():
        bullet = True

    # hyperlinks (w:hyperlink r:id / anchor fields)
    for h in p.iter(_q(_W, "hyperlink")):
        rid = h.get(_q(_R, "id"))
        if rid and rid in rels:
            label = "".join(t.text or "" for t in h.iter(_q(_W, "t")))[:120]
            ctx.links.append({"url": rels[rid].strip(), "label": label.strip(), "page": 1})
    for it in p.iter(_q(_W, "instrText")):
        m = re.search(r'HYPERLINK\s+"([^"]+)"', it.text or "")
        if m:
            ctx.links.append({"url": m.group(1), "label": "", "page": 1})
    for fs in p.iter(_q(_W, "fldSimple")):
        m = re.search(r'HYPERLINK\s+"([^"]+)"', fs.get(_q(_W, "instr")) or "")
        if m:
            ctx.links.append({"url": m.group(1), "label": "", "page": 1})

    segs: List[List[Tuple[str, Optional[float], bool]]] = [[]]
    for r in _iter_runs(p):
        hidden, size, bold = _run_visible_props(r, ctx, para_dark)
        text_parts: List[str] = []
        for child in r:
            t = child.tag
            if t == _q(_W, "t"):
                text_parts.append(child.text or "")
            elif t == _q(_W, "tab"):
                text_parts.append("\t")
            elif t == _q(_W, "noBreakHyphen"):
                text_parts.append("-")
            elif t == _q(_W, "sym"):
                text_parts.append("•")
            elif t in (_q(_W, "br"), _q(_W, "cr")):
                if child.get(_q(_W, "type")) in (None, "textWrapping"):
                    text_parts.append("\n")
            elif t == _q(_W, "drawing") or t == _q(_W, "pict"):
                if source == "body":
                    for ext in child.iter(_q(_WP, "extent")):
                        try:
                            cx, cy = int(ext.get("cx")), int(ext.get("cy"))
                        except (TypeError, ValueError):
                            continue
                        if 365760 <= cx <= 2377440 and 365760 <= cy <= 2377440 and 0.6 <= cx / cy <= 1.6 and child.find(f".//{_q(_PIC, 'pic')}") is not None:
                            ctx.has_photo = True
        txt = "".join(text_parts)
        ctx.raw_chars += len(txt)
        ctx.nonprint += count_nonprintable(txt)
        if hidden:
            if txt.strip():
                ctx.hidden.append({"text": txt.strip(), "reason": "hidden_run", "page": 1})
            continue
        for k, piece in enumerate(txt.split("\n")):
            if k > 0:
                segs.append([])
            if piece:
                segs[-1].append((piece, size, bold))

    out: List[Line] = []
    for seg in segs:
        text = "".join(s for s, _, _ in seg)
        norm = normalize_line(text)
        if not norm:
            continue
        body, b2 = strip_bullet(norm)
        if not body.strip():
            continue
        sizes = [s for _, s, _ in seg if s]
        size = max(sizes) if sizes else (style.get("size") or ctx.default_size)
        vis = [(len(s.strip()), bd) for s, _, bd in seg if s.strip()]
        tot = sum(n for n, _ in vis) or 1
        bold = (sum(n for n, bd in vis if bd) >= 0.7 * tot) or bool(style.get("bold") and not vis == [])
        out.append(Line(
            text=body.strip(), font_size=float(size), is_bold=bool(bold),
            is_bullet=bullet or b2, heading_style=bool(style.get("heading")), source=source,
            numbered=(not bullet) and b2 and is_numbered_marker(norm),
        ))
    # text boxes anchored in this paragraph
    for tb in _top_level_textboxes(p):
        for sub in tb:
            if sub.tag == _q(_W, "p"):
                for ln in _para_lines(sub, ctx, "textbox", rels):
                    out.append(ln)
            elif sub.tag == _q(_W, "tbl"):
                out.extend(_table_lines(sub, ctx, "textbox", rels))
    return out


def _top_level_textboxes(p) -> list:
    found = []
    stack = list(p)
    while stack:
        el = stack.pop(0)
        tag = el.tag
        if tag == _q(_MC, "Fallback"):
            continue
        if tag == _q(_W, "txbxContent"):
            found.append(el)
            continue
        if isinstance(tag, str):
            stack = list(el) + stack
    return found


def _table_lines(tbl, ctx: _DocxCtx, source: str, rels: Dict[str, str]) -> List[Line]:
    out: List[Line] = []
    for tr in tbl.findall(_q(_W, "tr")):
        cells = tr.findall(_q(_W, "tc"))
        cell_lines: List[List[Line]] = []
        for tc in cells:
            cell_lines.append(_block_lines(tc, ctx, "table", rels))
        non_empty = [c for c in cell_lines if c]
        if 2 <= len(non_empty) <= 4 and all(len(c) == 1 and not c[0].is_bullet for c in non_empty):
            joined = "\t".join(c[0].text for c in non_empty)
            base = non_empty[0][0]
            out.append(Line(text=joined, font_size=base.font_size, is_bold=base.is_bold,
                            heading_style=base.heading_style, source="table"))
        else:
            for c in non_empty:
                out.extend(c)
    return out


def _block_lines(container, ctx: _DocxCtx, source: str, rels: Dict[str, str]) -> List[Line]:
    out: List[Line] = []
    for child in container:
        tag = child.tag
        if tag == _q(_W, "p"):
            out.extend(_para_lines(child, ctx, source, rels))
        elif tag == _q(_W, "tbl"):
            out.extend(_table_lines(child, ctx, source, rels))
        elif tag == _q(_W, "sdt"):
            content = child.find(_q(_W, "sdtContent"))
            if content is not None:
                out.extend(_block_lines(content, ctx, source, rels))
        elif tag == _q(_MC, "AlternateContent"):
            choice = child.find(_q(_MC, "Choice"))
            if choice is not None:
                out.extend(_block_lines(choice, ctx, source, rels))
    return out


def extract_docx(data: bytes, limits: Limits, deadline: Deadline) -> ExtractedDoc:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError) as exc:
        raise ResumeParseError(f"corrupt DOCX: {exc}") from exc
    with zf:
        ctx = _DocxCtx(zf, limits, deadline)
        ctx.load_styles()
        deadline.check("docx-styles")
        try:
            body_root = _parse_xml(safe_read_member(zf, "word/document.xml", limits))
        except KeyError as exc:
            raise ResumeParseError("DOCX has no word/document.xml") from exc
        rels = ctx.load_rels("word/document.xml")
        body = body_root.find(_q(_W, "body"))
        if body is None:
            raise ResumeParseError("DOCX has no body")
        body_lines = _block_lines(body, ctx, "body", rels)
        deadline.check("docx-body")

        header_lines: List[Line] = []
        footer_lines: List[Line] = []
        names = sorted(n for n in zf.namelist() if re.match(r"word/(header|footer)\d*\.xml$", n))
        seen_hf: set = set()
        for n in names[:12]:
            try:
                root = _parse_xml(safe_read_member(zf, n, limits))
            except MaliciousFile:
                raise
            except (KeyError, ResumeParseError):
                continue
            hrels = ctx.load_rels(n)
            is_header = "/header" in n
            for ln in _block_lines(root, ctx, "header" if is_header else "footer", hrels):
                k = ln.text.strip().lower()
                if k in seen_hf:
                    continue
                seen_hf.add(k)
                if is_header:
                    ln.source = "header"
                    header_lines.append(ln)
                elif re.search(r"@|https?://|www\.|linkedin|github|\+?\d[\d\s().-]{8,}", ln.text):
                    ln.source = "footer"
                    footer_lines.append(ln)
        # drop pure page-number lines
        header_lines = [l for l in header_lines if not _PAGE_NUM_RE.match(l.text)]
        out = ExtractedDoc(method="docx-xml", pages=1)
        out.lines = header_lines + body_lines + footer_lines
        n = max(len(out.lines), 1)
        for i, ln in enumerate(out.lines):
            ln.ry0 = i / n
            ln.ry1 = (i + 1) / n
            ln.bbox = (0.0, float(i), 0.0, float(i))
        # reflow: bullets in docx that start lowercase continuing previous? not needed
        out.links = ctx.links
        out.hidden = ctx.hidden
        out.has_photo = ctx.has_photo
        out.nonprint = ctx.nonprint
        out.raw_chars = ctx.raw_chars
        # core properties
        try:
            core = _parse_xml(safe_read_member(zf, "docProps/core.xml", limits))
            def g(tag: str) -> str:
                for el in core.iter():
                    if isinstance(el.tag, str) and el.tag.endswith("}" + tag):
                        return (el.text or "").strip()
                return ""
            out.metadata = {
                "creator": g("creator"), "last_modified_by": g("lastModifiedBy"),
                "created": g("created")[:10], "modified": g("modified")[:10], "producer": "",
            }
        except Exception:
            out.metadata = {}
        try:
            app = safe_read_member(zf, "docProps/app.xml", limits).decode("utf-8", "ignore")
            m = re.search(r"<Application>([^<]{1,80})</Application>", app)
            if m:
                out.metadata["producer"] = m.group(1)
        except Exception:
            pass
        if not out.lines:
            raise EmptyResume("DOCX contains no visible text")
        return out
