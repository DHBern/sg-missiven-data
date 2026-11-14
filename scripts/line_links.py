"""Link the lines of a transcription to the line boxes on the page image.

The line readers (kraken, TrOCR) found boxes on the page and read each one, but
their segmentation cuts a written line into fragments: a word, a flourish, half
a line. So the boxes are first put together into rows (``rows``), by their
baselines: boxes standing on one line belong to one row, read left to right,
and the rows run top to bottom. (Grouping by the boxes' height merges
neighbouring lines: a box reaches up to the tallest ascender, so the boxes of
two lines overlap by half.) Each row has a text: the line readers' readings
of its fragments, joined.

A transcription (the edition, or any page reader) is then aligned with the rows
in order (``align``), like two texts with a sequence alignment: a line may match
one row, two neighbouring rows (the segmenter split it), or two lines one row
(it merged them); lines and rows may also stay unmatched. A link is kept only
where the transcription line and the row's text are similar enough
(``MIN_SIM``): what cannot be linked stays unlinked rather than guessed.

See docs/LINES.md.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

MIN_SIM = 0.5   # similarity a link needs (0..1, on normalised letters)
SPLIT = 0.04    # penalty for linking one line to two rows, or two lines to one


def norm(s: str) -> str:
    """Compare letters only: no case, no diacritics, the usual u/v and i/j swaps."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ß", "ss").replace("ſ", "s").replace("v", "u").replace("j", "i").replace("y", "i")
    return re.sub(r"[^a-z0-9]+", "", s)


def _median(v: list[float], default: float) -> float:
    v = sorted(v)
    return v[len(v) // 2] if v else default


def rows(boxes: list[list[float]], baselines: list, texts: list[list[str]], aspect: float) -> list[dict]:
    """Group the line readers' boxes into rows, by their baselines.

    Coordinates are fractions of the image; ``aspect`` (height / width) puts
    heights in the unit of widths. ``texts[k]`` holds each line reader's text of
    box k. A box joins the row whose baseline, carried on to the box's middle,
    passes within a third of the line spacing of the box's own baseline. The
    boxes themselves are tall where a flourish reaches up or down, so what is
    shown of a row is each of its boxes cut to the band of its line (``rects``)."""
    frag = []
    for k, (x0, y0, x1, y1) in enumerate(boxes):
        b = baselines[k] if k < len(baselines) else None
        if b:
            bx0, by0, bx1, by1 = b[0], b[1] * aspect, b[2], b[3] * aspect
        else:  # no baseline: the box's bottom stands in for it
            bx0, by0, bx1, by1 = x0, y1 * aspect, x1, y1 * aspect
        slope = (by1 - by0) / (bx1 - bx0) if abs(bx1 - bx0) > 1e-6 else 0.0
        frag.append({"k": k, "x0": x0, "x1": x1, "y0": y0 * aspect, "y1": y1 * aspect,
                     "bx": (bx0 + bx1) / 2, "by": (by0 + by1) / 2, "slope": max(-0.2, min(0.2, slope))})
    if not frag:
        return []
    # line spacing: distance between neighbouring baselines of the long boxes
    long_ = sorted((f for f in frag if f["x1"] - f["x0"] > 0.25), key=lambda f: f["by"])
    gaps = [b["by"] - a["by"] for a, b in zip(long_, long_[1:]) if b["by"] - a["by"] > 0.004]
    spacing = _median(gaps, _median([f["y1"] - f["y0"] for f in frag], 0.03) * 0.7)
    tol = spacing / 3
    out: list[dict] = []
    for f in sorted(frag, key=lambda f: -(f["x1"] - f["x0"])):  # long boxes found the rows
        best, dist = None, tol
        for r in out:
            ref = r["ref"]
            y_at = ref["by"] + ref["slope"] * (f["bx"] - ref["bx"])
            d = abs(y_at - f["by"])
            # two boxes of one line do not stand over each other
            stacked = any(min(f["x1"], g["x1"]) - max(f["x0"], g["x0"]) > 0.5 * min(f["x1"] - f["x0"], g["x1"] - g["x0"])
                          for g in r["f"])
            if d < dist and not stacked:
                best, dist = r, d
        if best is None:
            best = {"ref": f, "f": []}
            out.append(best)
        best["f"].append(f)
    n = len(texts[0]) if texts else 0
    result = []
    for r in out:
        fs = sorted(r["f"], key=lambda g: g["x0"])
        rects = []
        for g in fs:
            base = r["ref"]["by"] + r["ref"]["slope"] * (g["bx"] - r["ref"]["bx"])
            top = max(g["y0"], base - 0.95 * spacing)
            bottom = min(g["y1"], base + 0.3 * spacing)
            if bottom <= top:
                top, bottom = base - 0.8 * spacing, base + 0.2 * spacing
            rects.append([g["x0"], top, g["x1"], bottom])  # y in width units
        ks = [g["k"] for g in fs]
        result.append({"k": ks, "rects": rects, "y": r["ref"]["by"] + r["ref"]["slope"] * (0.5 - r["ref"]["bx"]),
                       "texts": [" ".join(texts[i][m] for i in ks if texts[i][m]) for m in range(n)] if n else []})
    result.sort(key=lambda r: r["y"])
    return result


def _sim(a: list[str], b_by_reader: list[list[str]]) -> list[list[float]]:
    """Best similarity of each string in ``a`` to each row, over the line readers."""
    na = [norm(x) for x in a]
    nb = [[norm(x) for x in b] for b in b_by_reader]
    cols = len(nb[0]) if nb else 0
    out = []
    for x in na:
        row = []
        for j in range(cols):
            best = 0.0
            if x:
                for b in nb:
                    if b[j]:
                        v = fuzz.ratio(x, b[j], score_cutoff=best * 100) / 100.0
                        if v > best:
                            best = v
            row.append(best)
        out.append(row)
    return out


def align(lines: list[str], row_texts: list[list[str]]) -> list[list[int]]:
    """For each transcription line, the rows it is linked to (empty: none).

    ``row_texts[j]`` holds the line readers' texts of row j."""
    n, m = len(lines), len(row_texts)
    if not n or not m:
        return [[] for _ in lines]
    readers = max(len(rt) for rt in row_texts)
    by_reader = [[rt[r] if r < len(rt) else "" for rt in row_texts] for r in range(readers)]
    s11 = _sim(lines, by_reader)
    s12 = _sim(lines, [[a + " " + b for a, b in zip(br, br[1:])] for br in by_reader]) if m > 1 else [[] for _ in lines]
    s21 = _sim([a + " " + b for a, b in zip(lines, lines[1:])], by_reader) if n > 1 else []

    def gain(v: float) -> float:
        return v - MIN_SIM if v >= MIN_SIM else -1.0

    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            v, mv = best[i - 1][j], 1
            if best[i][j - 1] > v:
                v, mv = best[i][j - 1], 2
            c = best[i - 1][j - 1] + gain(s11[i - 1][j - 1])
            if c > v:
                v, mv = c, 3
            if j > 1:
                c = best[i - 1][j - 2] + gain(s12[i - 1][j - 2]) - SPLIT
                if c > v:
                    v, mv = c, 4
            if i > 1:
                c = best[i - 2][j - 1] + gain(s21[i - 2][j - 1]) - SPLIT
                if c > v:
                    v, mv = c, 5
            best[i][j], move[i][j] = v, mv
    out: list[list[int]] = [[] for _ in lines]
    i, j = n, m
    while i > 0 and j > 0:
        mv = move[i][j]
        if mv == 1:
            i -= 1
        elif mv == 2:
            j -= 1
        elif mv == 3:
            out[i - 1] = [j - 1]
            i, j = i - 1, j - 1
        elif mv == 4:
            out[i - 1] = [j - 2, j - 1]
            i, j = i - 1, j - 2
        else:
            out[i - 2] = [j - 1]
            out[i - 1] = [j - 1]
            i, j = i - 2, j - 1
    return out
