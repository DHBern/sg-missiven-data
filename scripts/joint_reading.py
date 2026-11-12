"""The joint reading: the independent machine readings combined into one.

A plain majority vote loses to the best single reader when one reader dominates
(agentic_historian ``fusion.py``, measured on 187 pages: 0.152 against 0.106).
So this is a **weighted backbone**: the best reader's text is kept word for word
unless other readers, whose combined weight exceeds the backbone's, agree on the
same different word at that place.

* Weights are log-odds of a reader's word accuracy, ``log((1-e)/e)`` with ``e``
  its CER, measured on the **other half** of the letters (split by letter id),
  so no page is combined with weights learned from its own edition text.
* The fine-tuned VLM is left out: it saw most of these pages in training, and a
  joint reading that leans on it would inherit that advantage.
* Only one-to-one word substitutions are voted on; insertions and deletions
  stay as the backbone has them. Line breaks are the backbone's.

    python scripts/joint_reading.py [--backbone gemini-4-argon] [--readers a,b,c]

Writes ``readings/joint.jsonl`` (``source``: which readers carried each change)
and prints the corpus CER of the backbone and of the joint reading.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import sys
import zlib
from collections import defaultdict
from pathlib import Path

from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from tei_pages import read_edition  # noqa: E402

DEFAULT_READERS = ["gemini-3.8-flash", "kraken-bern-combined-v1", "kraken-cursive_inzigkofen",
                   "trocr-kurrent-xvi-xvii", "trocr-medieval-escriptmask"]
TOKEN = re.compile(r"\S+")


def load(reader: str) -> dict[str, str]:
    path = ROOT / "readings" / f"{reader}.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[r["key"]] = "\n".join(l for l in (r.get("text") or "").splitlines() if l.strip())
    return out


def flat(s: str) -> str:
    return " ".join(s.split())


def norm_tok(t: str) -> str:
    return re.sub(r"[^\w]", "", t.lower())


def half(letter: str) -> int:
    return zlib.crc32(letter.encode()) % 2


def cer(pairs) -> float:
    e = c = 0
    for ref, hyp in pairs:
        e += Levenshtein.distance(flat(ref), flat(hyp)); c += len(flat(ref))
    return e / max(1, c)


def combine(backbone: str, others: dict[str, str], weights: dict[str, float], wb: float):
    """Returns the joint text and the list of changes made."""
    lines = backbone.split("\n")
    btoks = [(li, m) for li, line in enumerate(lines) for m in TOKEN.finditer(line)]
    bnorm = [norm_tok(m.group()) for _, m in btoks]
    votes: list[dict[str, list[str]]] = [defaultdict(list) for _ in btoks]
    for reader, text in others.items():
        otoks = TOKEN.findall(text)
        onorm = [norm_tok(t) for t in otoks]
        sm = difflib.SequenceMatcher(None, bnorm, onorm, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    votes[i1 + k][otoks[j1 + k]].append(reader)
            elif tag == "replace" and (i2 - i1) == (j2 - j1):
                for k in range(i2 - i1):
                    votes[i1 + k][otoks[j1 + k]].append(reader)
    changes = []
    new_tokens = []
    for idx, (li, m) in enumerate(btoks):
        mine = m.group()
        support: dict[str, float] = defaultdict(float)
        backers: dict[str, list[str]] = defaultdict(list)
        support[norm_tok(mine)] += wb
        for tok, readers in votes[idx].items():
            for r in readers:
                support[norm_tok(tok)] += weights[r]
                backers[tok].append(r)
        best_norm = max(support, key=support.get)
        if best_norm and best_norm != norm_tok(mine) and support[best_norm] > support[norm_tok(mine)]:
            cands = [t for t in backers if norm_tok(t) == best_norm]
            pick = max(cands, key=lambda t: len(backers[t]))
            # keep the backbone's punctuation around the word
            lead = re.match(r"^\W*", mine).group(); trail = re.search(r"\W*$", mine).group()
            core = re.sub(r"^\W+|\W+$", "", pick) or pick
            new = lead + core + trail
            changes.append({"line": li, "from": mine, "to": new,
                            "by": sorted({r for t in cands for r in backers[t]})})
            new_tokens.append((li, m, new))
        else:
            new_tokens.append((li, m, mine))
    out_lines = []
    for li, line in enumerate(lines):
        pieces, pos = [], 0
        for l2, m, new in new_tokens:
            if l2 != li:
                continue
            pieces.append(line[pos:m.start()]); pieces.append(new); pos = m.end()
        pieces.append(line[pos:])
        out_lines.append("".join(pieces))
    return "\n".join(out_lines), changes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backbone", default="gemini-4-argon")
    ap.add_argument("--readers", default=",".join(DEFAULT_READERS))
    ap.add_argument("--min-coverage", type=float, default=0.95,
                    help="leave out a reader that has read less than this share of the backbone's pages")
    args = ap.parse_args()

    edition = {}
    letter_of = {}
    for letter in read_edition(ROOT / "data"):
        for p in letter["pages"]:
            edition[p["facs"]] = p["text"]
            letter_of[p["facs"]] = letter["id"]
    back = load(args.backbone)
    readers = {}
    for r in args.readers.split(","):
        pages = load(r)
        cov = sum(1 for k in back if k in pages) / max(1, len(back))
        if cov < args.min_coverage:
            print(f"  {r}: read {cov:.0%} of the backbone's pages — left out")
            continue
        readers[r] = pages
    print(f"backbone {args.backbone}; voting readers: {', '.join(readers) or 'none'}")

    # weights from the other half of the letters
    w = {h: {} for h in (0, 1)}
    for h in (0, 1):
        other = [k for k in back if edition.get(k) and half(letter_of[k]) != h]
        e = cer((edition[k], back[k]) for k in other)
        w[h]["__backbone__"] = math.log((1 - e) / e)
        for r, pages in readers.items():
            e = cer((edition[k], pages.get(k, "")) for k in other)
            w[h][r] = math.log((1 - e) / e) if 0 < e < 1 else 0.0
        print(f"  weights for half {h}: " + ", ".join(f"{k}={v:.2f}" for k, v in w[h].items()))

    out, n_changes = [], 0
    pairs_b, pairs_j = [], []
    for key, text in back.items():
        h = half(letter_of.get(key, key))
        others = {r: pages[key] for r, pages in readers.items() if pages.get(key)}
        joint, changes = combine(text, others, w[h], w[h]["__backbone__"]) if others else (text, [])
        n_changes += len(changes)
        out.append({"key": key, "text": joint, "backbone": args.backbone,
                    "readers": sorted(others), "changes": changes})
        if edition.get(key):
            pairs_b.append((edition[key], text)); pairs_j.append((edition[key], joint))
    (ROOT / "readings" / "joint.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    print(f"{len(out)} pages, {n_changes} words changed")
    print(f"CER backbone {cer(pairs_b):.4f}  joint {cer(pairs_j):.4f}  over {len(pairs_b)} pages")


if __name__ == "__main__":
    main()
