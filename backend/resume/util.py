"""Small shared helpers: deadline, text normalisation, month arithmetic."""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import date
from typing import Callable, Optional

from .errors import ParseTimeout


class Deadline:
    """Cooperative deadline; call :meth:`check` between pipeline stages."""

    def __init__(self, budget_s: Optional[float], clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.budget_s = budget_s
        self._end = None if budget_s is None else clock() + float(budget_s)

    def remaining(self) -> float:
        return float("inf") if self._end is None else self._end - self._clock()

    def expired(self) -> bool:
        return self._end is not None and self._clock() > self._end

    def check(self, stage: str = "") -> None:
        if self.expired():
            raise ParseTimeout(
                f"time budget of {self.budget_s:.1f}s exhausted"
                + (f" during stage '{stage}'" if stage else "")
            )


# --------------------------------------------------------------------------- text

_ZERO_WIDTH = dict.fromkeys(
    map(ord, "​‌‍‎‏⁠⁡⁢⁣﻿­‪‫‬‭‮"),
    None,
)
_QUOTES = {
    ord("‘"): "'", ord("’"): "'", ord("‚"): "'", ord("‛"): "'",
    ord("′"): "'", ord("´"): "'", ord("`"): "'",
    ord("“"): '"', ord("”"): '"', ord("„"): '"', ord("″"): '"',
}
_SPACES = {
    ord(" "): " ", ord(" "): " ", ord(" "): " ", ord(" "): " ",
    ord(" "): " ", ord(" "): " ", ord(" "): " ", ord(" "): " ",
    ord(" "): " ", ord(" "): " ", ord(" "): " ", ord(" "): " ",
    ord("　"): " ", ord(" "): " ",
}
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_PUA_RE = re.compile(r"[-]")
_WS_RE = re.compile(r"[ \t]+")

# Private-use glyphs produced by Symbol/Wingdings bullets.
BULLET_CHARS = "•▪▫●○◦■□◆◇◊➢➤➔►▶→✓✔✦★☆‣⁃∙·✱❖➣➥➜❯›»"


def count_nonprintable(text: str) -> int:
    """Number of control / replacement / private-use characters in ``text``."""
    return len(_CTRL_RE.findall(text)) + text.count("�")


def normalize_line(text: str) -> str:
    """NFKC, ligatures, quotes, NBSP, zero-width and control chars -> clean line.

    Private-use glyphs (Wingdings/Symbol bullets) become ``•``.
    """
    if not text:
        return ""
    text = _PUA_RE.sub("•", text)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZERO_WIDTH).translate(_SPACES).translate(_QUOTES)
    text = _CTRL_RE.sub(" ", text).replace("�", "")
    text = text.replace("\r", "")
    return _WS_RE.sub(lambda m: "\t" if "\t" in m.group(0) else " ", text).strip()


def norm_key(text: str) -> str:
    """Lower-case alphanumeric-token key used for hashing / comparisons."""
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower()))


# --------------------------------------------------------------------------- months

def today() -> date:
    return date.today()


def ym(year: int, month: int) -> int:
    """Month index: ``year * 12 + (month - 1)``."""
    return year * 12 + (month - 1)


def ym_str(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def ym_parse(s: str) -> int:
    y, m = s.split("-")
    return ym(int(y), int(m))


def current_ym() -> int:
    t = today()
    return ym(t.year, t.month)
