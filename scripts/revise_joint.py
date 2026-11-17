"""Revise the uncertain words of the joint reading against the page image (experimental).

    set -a; . ~/.gpustack.env; set +a
    python scripts/revise_joint.py eval  --images DIR --edition edition.json --keys keys.txt
    python scripts/revise_joint.py draft --images DIR [--images DIR2] --keys keys.txt

The joint reading (scripts/joint_reading.py) marks the words its readers do not
agree on ("unc": uncertain or doubtful, with the other readers' forms). Only
those words are put to a vision model (Qwen3.8-27B on the University of Bern's
GPUStack, no thinking) together with the page image: for each marked word it
answers the word as written on the page. Every other word stays as the joint
reading has it, so the model can fix a misreading but cannot rewrite a page.

``eval`` runs on edited pages and prints the CER of the joint reading and of
the revision against the edition (``--edition``: a JSON object facs -> edition
text). ``draft`` writes ``readings/revised/<model>.jsonl``: key, text, and per
mark the joint word, the model's word and whether it changed. A rerun keeps the
pages already there (``--redo`` replaces them). Runs where the page images are
(tei), with Pillow and httpx.

Measured 17.11.2026 on 40 edited pages with at least 20 % uncertain words: mean
CER 0.1346 for the joint reading, 0.1365 after the revision (better on 4 pages,
worse on 24; the model changed 129 of 3,350 marked words). Not used for the
corpus: the revision does not beat the vote.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "qwen3.8-27b"
MAX_SIDE = 2048

PROMPT = """You see the image of one page of a letter from the archive of St. Gallen (15th–17th century, German, sometimes Latin), and a machine transcription of it.
In the transcription, the words the machine readers did not agree on are marked like this: ⟦7: reading | other reading | other reading⟧, numbered.
For every marked word, look at the image at that place and give the word exactly as it is written there: keep the old spelling, the abbreviations and the letters of the page (u/v, i/j, y, ů, ä as written); do not modernise, do not normalise, do not translate. If the first reading is right, give it unchanged. If the place shows no word, give "".
Answer with JSON only: {"7": "word", "8": "word", ...}, one entry for every number.

Transcription:
"""


def load_joint() -> dict[str, dict]:
    out = {}
    for line in (ROOT / "readings" / "joint.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[r["key"]] = r
    return out


def marked(rec: dict) -> tuple[str, list[dict]]:
    """The joint text with its uncertain words marked, and the marks in order."""
    text = rec["text"]
    marks = sorted(rec.get("unc") or [], key=lambda m: m[2])
    parts, out, pos = [], [], 0
    for i, m in enumerate(marks, 1):
        js, je = m[2], m[3]
        if js < pos:
            continue
        word = text[js:je]
        alts = [a[0] for a in m[6] if a[0] and a[0] != word]
        parts.append(text[pos:js])
        parts.append("⟦" + " | ".join([f"{i}: {word}"] + alts) + "⟧")
        out.append({"n": str(i), "start": js, "end": je, "joint": word, "alts": alts})
        pos = je
    parts.append(text[pos:])
    return "".join(parts), out


def image_b64(key: str, dirs: list[Path]) -> str | None:
    from PIL import Image
    stem = key.rsplit(".", 1)[0]
    for d in dirs:
        for ext in (".jpg", ".JPG", ".jpeg", ".png"):
            p = d / (stem + ext)
            if p.exists():
                im = Image.open(p).convert("RGB")
                im.thumbnail((MAX_SIDE, MAX_SIDE))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88)
                return base64.b64encode(buf.getvalue()).decode()
    return None


def ask(client, img: str, text: str) -> dict[str, str]:
    body = {"model": MODEL, "temperature": 0, "max_tokens": 4096,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
                {"type": "text", "text": PROMPT + text}]}]}
    for attempt in range(4):
        try:
            r = client.post("/chat/completions", json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
            m = re.search(r"\{.*\}", content, re.S)
            return {str(k): (v if isinstance(v, str) else "") for k, v in json.loads(m.group(0)).items()} if m else {}
        except Exception as e:  # noqa: BLE001  (network, JSON)
            if attempt == 3:
                raise
            print(f"  retry after {type(e).__name__}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    return {}


def revise(rec: dict, answer: dict[str, str], marks: list[dict]) -> tuple[str, list[dict]]:
    text, out = rec["text"], []
    for m in reversed(marks):
        new = answer.get(m["n"], m["joint"]).strip()
        if not new or " " in new or len(new) > 3 * len(m["joint"]) + 4:
            new = m["joint"]  # an empty answer, a phrase or a runaway keeps the joint word
        text = text[:m["start"]] + new + text[m["end"]:]
        out.append({"joint": m["joint"], "model": new, "changed": new != m["joint"]})
    return text, out[::-1]


def cer(a: str, b: str) -> float:
    from rapidfuzz.distance import Levenshtein
    a, b = " ".join(a.split()), " ".join(b.split())
    return Levenshtein.distance(a, b) / max(1, len(b))


def main() -> None:
    import httpx
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["eval", "draft"])
    ap.add_argument("--images", action="append", required=True, type=Path)
    ap.add_argument("--keys", required=True, type=Path, help="one page key per line (with or without extension)")
    ap.add_argument("--edition", type=Path, help="eval: JSON object facs -> edition text")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()

    joint = load_joint()
    by_stem = {k.rsplit(".", 1)[0]: k for k in joint}
    keys = [by_stem.get(l.strip().rsplit(".", 1)[0] if l.strip().lower().endswith((".jpg", ".png")) else l.strip())
            for l in a.keys.read_text().splitlines() if l.strip()]
    keys = [k for k in keys if k and joint[k].get("unc")]
    out_path = ROOT / "readings" / "revised" / f"{MODEL}.jsonl"
    done = {}
    if a.mode == "draft" and out_path.exists() and not a.redo:
        for line in out_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done[r["key"]] = r
    todo = [k for k in keys if k not in done]
    print(f"{a.mode}: {len(todo)} pages with marks ({len(done)} already revised)")
    client = httpx.Client(base_url=os.environ["GPUSTACK_BASE_URL"], timeout=600,
                          headers={"Authorization": f"Bearer {os.environ['GPUSTACK_API_KEY']}"})

    def one(key: str):
        img = image_b64(key, a.images)
        if not img:
            return key, None, "no image"
        text, marks = marked(joint[key])
        try:
            ans = ask(client, img, text)
        except Exception as e:  # noqa: BLE001
            return key, None, f"{type(e).__name__}: {e}"
        new, changes = revise(joint[key], ans, marks)
        return key, {"key": key, "text": new, "model": MODEL, "marks": changes,
                     "answered": len(ans), "asked": len(marks)}, None

    results, errors = {}, 0
    with ThreadPoolExecutor(a.workers) as ex:
        for i, (key, rec, err) in enumerate(ex.map(one, todo), 1):
            if err:
                errors += 1
                print(f"  {key}: {err}", file=sys.stderr)
            else:
                results[key] = rec
            if i % 20 == 0:
                print(f"  {i}/{len(todo)}, {errors} errors", flush=True)
                if a.mode == "draft":
                    write(out_path, {**done, **results})
    if a.mode == "draft":
        write(out_path, {**done, **results})
        n = sum(len(r["marks"]) for r in results.values())
        ch = sum(m["changed"] for r in results.values() for m in r["marks"])
        print(f"revised {len(results)} pages, {errors} errors; {ch} of {n} marked words changed -> {out_path}")
        return
    ed = json.loads(a.edition.read_text(encoding="utf-8"))
    rows = []
    for key, r in results.items():
        e = ed.get(key)
        if e:
            rows.append((cer(joint[key]["text"], e), cer(r["text"], e),
                         sum(m["changed"] for m in r["marks"]), len(r["marks"])))
    if not rows:
        sys.exit("no page with an edition text")
    j = sum(x[0] for x in rows) / len(rows)
    v = sum(x[1] for x in rows) / len(rows)
    better = sum(x[1] < x[0] for x in rows)
    worse = sum(x[1] > x[0] for x in rows)
    print(f"{len(rows)} edited pages, {errors} errors; mean CER joint {j:.4f} -> revised {v:.4f}; "
          f"better on {better}, worse on {worse}; {sum(x[2] for x in rows)} of {sum(x[3] for x in rows)} marks changed")


def write(path: Path, recs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs.values()), encoding="utf-8")


if __name__ == "__main__":
    main()
