"""The year of a letter from its date line, with the century from its neighbours.

A dating line writes the year in full ("1573", "Ao 1620"), abbreviated in
Arabic figures ("a° etc. 73. jare") or in Roman figures and Latin ordinals
("anno etc. lxv°", "im lij jar", "anno etc. L secundo", "septuagesimo
octavo"). An abbreviated year leaves the century open; a language model
closed it wrongly in a third of the regest evaluation (1465 read as 1515,
1452 as 1551).

The series is numbered in chronological order: in the edition the median
year of a letter's six numbered neighbours is its own year in half the letters
and within six years in 99 % (808 letters). So ``expected_year(nr)`` places a
letter in time from the edited letters around it, and ``year_from_text`` takes
the century that puts the written year closest to that.
"""

from __future__ import annotations

import bisect
import re

ORDINAL = {"primo": 1, "secundo": 2, "tertio": 3, "quarto": 4, "quinto": 5, "sexto": 6,
           "septimo": 7, "octavo": 8, "nono": 9, "decimo": 10,
           "vicesimo": 20, "vigesimo": 20, "tricesimo": 30, "trigesimo": 30,
           "quadragesimo": 40, "quinquagesimo": 50, "sexagesimo": 60,
           "septuagesimo": 70, "octuagesimo": 80, "octogesimo": 80, "nonagesimo": 90}
ROMAN = {"i": 1, "j": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
# where the year starts: after "anno", "a°", "ao", "jar(s)", "im", or a bare four-digit year
ANCHOR = re.compile(r"\b(?:anno|ano|a[°o]|im|jar|jars|jahr|jahrs)\b\.?|\ba°", re.I)


def roman(tok: str) -> int | None:
    tok = tok.lower()
    if not tok or any(c not in ROMAN for c in tok):
        return None
    total = 0
    for a, b in zip(tok, tok[1:] + " "):
        v = ROMAN[a]
        total += -v if b != " " and ROMAN.get(b, 0) > v else v
    return total


def written_year(text: str) -> tuple[int | None, bool]:
    """(the year as written, whether it is complete). 73 -> (73, False); 1573 -> (1573, True)."""
    if not text:
        return None, False
    full = re.findall(r"(?<!\d)(1[2-7]\d\d)(?!\d)", text)
    if full:
        return int(full[-1]), True
    m = re.search(r"\bim\s+([ivxlcj]+)\s+(?:jar|jahr)", text, re.I)  # "im lij jar"
    if m:
        v = roman(m.group(1))
        if v:
            return v, False
    # the last anchor that still has something after it ("... anno etc. lxv°", not "... jar")
    anchors = [a for a in ANCHOR.finditer(text) if text[a.end():].strip(" .,:;")]
    m = anchors[-1] if anchors else None
    tail = text[m.end():] if m else text
    tail = re.sub(r"\b(?:etc|rc|domini|dni|dmi|d[nm]i)\b\.?", " ", tail, flags=re.I)
    num = re.search(r"(?<!\d)(\d{1,3})(?!\d)", tail)
    if num and m:
        return int(num.group(1)), int(num.group(1)) >= 1000
    total, seen = 0, False
    for tok in re.findall(r"[A-Za-z]+", tail):
        t = tok.lower()
        if t in ORDINAL:
            total += ORDINAL[t]
            seen = True
            continue
        core = re.sub(r"(?:mo|to|no|o)$", "", t) if roman(t) is None else t
        for head, val in ORDINAL.items():  # "lxprimo": Roman figures run into an ordinal
            if t.endswith(head) and roman(t[: -len(head)]):
                total += roman(t[: -len(head)]) + val
                seen = True
                break
        else:
            v = roman(core)
            if v is not None and core:
                total += v
                seen = True
            elif seen:
                break
    if seen and total:
        return total, total >= 1000
    return None, False


class Timeline:
    """Letter number -> expected year.

    Anchors are ``(nr, year)`` pairs: the edited letters, and any letter whose
    date line writes its year in four figures. Between two anchors the year is
    interpolated; the median of the anchors at the same or the nearest number on
    each side smooths a single misdated anchor. Near the edition's own letters
    this is exact in half the cases; far from them (the series has stretches of
    a thousand numbers without an edited letter) it is only good enough to pick
    the century of an abbreviated year, which needs it within ±49 years."""

    def __init__(self, points: list[tuple[int, int]]):
        by_nr: dict[int, list[int]] = {}
        for n, y in points:
            by_nr.setdefault(n, []).append(y)
        self.points = sorted((n, sorted(ys)[len(ys) // 2]) for n, ys in by_nr.items())
        self.nrs = [n for n, _ in self.points]

    def expected_year(self, nr: int, exclude: int | None = None) -> float | None:
        pts = [(n, y) for n, y in self.points if n != exclude]
        if not pts:
            return None
        nrs = [n for n, _ in pts]
        i = bisect.bisect_left(nrs, nr)
        left, right = pts[max(0, i - 3):i], pts[i:i + 3]
        if not left or not right:
            near = sorted(y for _, y in left + right)
            return float(near[len(near) // 2])
        ly = sorted(y for _, y in left)[len(left) // 2]
        ry = sorted(y for _, y in right)[len(right) // 2]
        ln, rn = left[-1][0], right[0][0]
        return float(ly if rn == ln else ly + (ry - ly) * (nr - ln) / (rn - ln))


def year_from_text(text: str | None, expected: float | None) -> tuple[int | None, str]:
    """(year, how): how is 'written' (four figures in the text), 'century' (the
    century taken from the neighbours), or '' when the text gives no year."""
    year, complete = written_year(text or "")
    if year is None:
        return None, ""
    if complete:
        return year, "written"
    if expected is None or year >= 100:
        return None, ""
    best = min((c * 100 + year for c in range(12, 18)), key=lambda y: abs(y - expected))
    return best, "century"
