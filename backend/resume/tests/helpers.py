"""Programmatic fixture builders (no committed binaries)."""
from __future__ import annotations

import io
import zipfile
from typing import Iterable, List, Optional, Sequence, Tuple

import pymupdf

A4 = (595, 842)
_FONTS = {}


def _font(name):
    if name not in _FONTS:
        _FONTS[name] = pymupdf.Font(name)
    return _FONTS[name]


class PdfBuilder:
    """Tiny layout helper on top of PyMuPDF."""

    def __init__(self, size=A4):
        self.doc = pymupdf.open()
        self.size = size
        self.page = None
        self.new_page()

    def new_page(self):
        self.page = self.doc.new_page(width=self.size[0], height=self.size[1])
        return self.page

    def text(self, x, y, txt, size=10.5, bold=False, color=(0, 0, 0)):
        self.page.insert_text((x, y), txt, fontsize=size, fontname="hebo" if bold else "helv", color=color)

    def flow(self, x, y, items: Sequence, width=None, line_gap=1.42):
        """items: str | (str, size) | (str, size, bold) | (str, size, bold, color) ; '' = small gap.

        Long lines wrap at ``width``; wrapped continuation lines are indented by 10pt when the
        text starts with a bullet character.
        """
        width = width or (self.size[0] - x - 40)
        for it in items:
            if isinstance(it, str):
                it = (it,)
            txt = it[0]
            size = it[1] if len(it) > 1 else 10.5
            bold = it[2] if len(it) > 2 else False
            color = it[3] if len(it) > 3 else (0, 0, 0)
            if txt == "":
                y += size * 0.7
                continue
            font = _font("hebo" if bold else "helv")
            bullet = txt.startswith("•")
            words = txt.split(" ")
            cur = ""
            first = True
            for w in words:
                trial = (cur + " " + w).strip()
                avail = width - (0 if first or not bullet else 12)
                if font.text_length(trial, fontsize=size) > avail and cur:
                    self.text(x + (0 if first or not bullet else 12), y, cur, size, bold, color)
                    y += size * line_gap
                    cur = w
                    first = False
                else:
                    cur = trial
            self.text(x + (0 if first or not bullet else 12), y, cur, size, bold, color)
            y += size * line_gap
        return y

    def rect(self, x0, y0, x1, y1, fill=(0, 0, 0)):
        self.page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=fill, fill=fill)

    def line(self, x0, y, x1, width=0.8):
        self.page.draw_line((x0, y), (x1, y), width=width)

    def bytes(self, **kw) -> bytes:
        return self.doc.tobytes(**kw)


def simple_pdf(lines: Iterable, size=A4) -> bytes:
    b = PdfBuilder(size)
    b.flow(50, 60, list(lines))
    return b.bytes()


def docx_bytes(build) -> bytes:
    """``build(document)`` receives a python-docx Document."""
    import docx

    d = docx.Document()
    build(d)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def zip_bytes(members: dict, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


MINIMAL_PDF_TMPL = (
    b"%%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R %(catalog_extra)s >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
    b"/Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    b"4 0 obj\n<< /Length %(len)d >>\nstream\n%(content)s\nendstream\nendobj\n"
    b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R /Size 6 >>\n%%%%EOF\n"
)


def raw_pdf(catalog_extra: bytes = b"", text: str = "Jane Doe  jane@doe.dev  Python developer") -> bytes:
    content = ("BT /F1 12 Tf 50 750 Td (" + text + ") Tj ET").encode()
    return MINIMAL_PDF_TMPL % {b"catalog_extra": catalog_extra, b"len": len(content), b"content": content}


# ---- picklable targets for parse_resume_isolated tests (must live in an importable module)
def sleeper(payload):
    import time

    time.sleep(120)


def crasher(payload):
    import os

    os._exit(3)


def raiser(payload):
    from resume.errors import MaliciousFile

    raise MaliciousFile("boom from the child")
