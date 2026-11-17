"""Which pages need a new machine reading, and which letters they belong to.

    python scripts/reread_list.py [--keys-out FILE] [--json-out FILE]

The backbone of the joint reading is Gemini 4 Argon. A page is listed when its
Argon reading is suspect for a reason another reading can show:

* ``truncated``: Argon stopped at its token ceiling;
* ``loop``: Argon's text repeats itself (compresses to under 18 %);
* ``argon_empty``: Argon has nothing, while two other readers have a page of
  text that agree with each other;
* ``argon_outlier``: two other readers agree with each other and Argon differs
  from both by more than 60 % of the characters;
* ``argon_short``: two other readers agree and Argon has under 60 % of their length.

The other readers are Gemini 3.8 Flash and the two fine-tuned page models
(Qwen3.5-4B, olmOCR2-7B), counted only where their own text is sound (at least
150 characters, no loop). Two readers that agree are the evidence: Flash alone
is not, it writes a plausible letter onto a blank verso (letter 1102, 17.11.2026),
and the fine-tuned models loop on blank pages.

Writes the page keys (``--keys-out``, for ``atr-batch --keys-from``) and a
JSON of pages with their reasons and letters (``--json-out``,
default readings/reread.json).
"""

from __future__ import annotations

import argparse
import json
import re
import zlib
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parents[1]
OTHERS = ["gemini-3.8-flash", "qwen3.5-4b-german-kurrent-xvi-xix-page-v1",
          "olmocr2-7b-german-kurrent-xvi-xix-blb-page-px4m-native-v1"]
AGREE = 0.45      # two other readers agree: under 45 % of characters differ
OUTLIER = 0.6     # Argon differs from both by more than this
SHORT = 0.6       # Argon has under this share of their length


def load(reader: str) -> dict[str, dict]:
    path = ROOT / "readings" / f"{reader}.jsonl"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            r["flat"] = " ".join((r.get("text") or "").split())
            out[r["key"]] = r
    return out


def looped(t: str) -> bool:
    return len(t) >= 400 and len(zlib.compress(t.encode())) / len(t.encode()) < 0.18


def sound(t: str) -> bool:
    return len(t) >= 150 and t != "[]" and len(zlib.compress(t.encode())) / len(t.encode()) > 0.25


def dist(a: str, b: str) -> float:
    return Levenshtein.normalized_distance(a, b)


def reasons(key: str, argon: dict, others: list[dict]) -> list[str]:
    at = argon["flat"] if argon["flat"] != "[]" else ""
    if argon.get("truncated"):
        return ["truncated"]
    if looped(at):
        return ["loop"]
    texts = [o[key]["flat"] for o in others if key in o and sound(o[key]["flat"])]
    pair = next(((a, b) for i, a in enumerate(texts) for b in texts[i + 1:] if dist(a, b) < AGREE), None)
    if not pair:
        return []
    a, b = pair
    if not at:
        return ["argon_empty"]
    if dist(at, a) > OUTLIER and dist(at, b) > OUTLIER:
        return ["argon_outlier"]
    if len(at) < SHORT * min(len(a), len(b)):
        return ["argon_short"]
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keys-out", type=Path)
    ap.add_argument("--json-out", type=Path, default=ROOT / "readings" / "reread.json")
    a = ap.parse_args()
    argon = load("gemini-4-argon")
    others = [load(r) for r in OTHERS]
    pages = {}
    for key, rec in argon.items():
        why = reasons(key, rec, others)
        if why:
            m = re.match(r"StadtASG_Missive_(\d+)_", key)
            pages[key] = {"why": why, "letter": int(m.group(1)) if m else None}
    letters = defaultdict(list)
    for key, p in pages.items():
        letters[p["letter"]].append(key)
    a.json_out.write_text(json.dumps({
        "about": "Pages whose Gemini 4 Argon reading is suspect (scripts/reread_list.py).",
        "pages": dict(sorted(pages.items())),
        "letters": {str(k): sorted(v) for k, v in sorted(letters.items(), key=lambda x: x[0] or 0)},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.keys_out:
        a.keys_out.write_text("".join(k.rsplit(".", 1)[0] + "\n" for k in sorted(pages)), encoding="utf-8")
    c = Counter(w for p in pages.values() for w in p["why"])
    print(f"{len(pages)} of {len(argon)} pages, {len(letters)} letters: " + ", ".join(f"{k} {v}" for k, v in c.most_common()))


if __name__ == "__main__":
    main()
