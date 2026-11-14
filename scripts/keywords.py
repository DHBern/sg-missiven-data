"""Keywords for the letters not yet edited, from the edition's own vocabulary (experimental).

    set -a; . ~/.gpustack.env; set +a
    python scripts/keywords.py eval  --n 60       # edited letters, compared with the editors' keywords
    python scripts/keywords.py draft --n 4000     # letters not yet edited, with a draft regest

The edition gives each letter about eight keywords from a vocabulary of some
600 (TEI ``textClass/keywords``). A language model (Qwen3.8-27B on the
University of Bern's GPUStack, no thinking) gets that vocabulary, the machine
reading of a letter (Gemini 4 Argon) and, where there is one, its draft regest
(``scripts/regest.py``), and picks keywords **from the vocabulary only**; any
other word in its answer is dropped. Four edited letters serve as examples.

``eval`` scores against the editors' keywords: precision (how many of the
model's keywords the editors also gave), recall (how many of the editors' it
found), F1. ``draft`` writes ``keywords/draft_<reader>.jsonl``; a rerun keeps
what is there (``--redo`` replaces it). Every keyword is marked as automatic
wherever it is shown.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from regest import FACS, letter_text, load_reader  # noqa: E402
from tei_pages import read_edition  # noqa: E402

MODEL = "qwen3.8-27b"
MAX_CHARS = 9000  # a letter's text beyond this is cut (the long ones are enclosures)

SYSTEM = """Du verschlagwortest Briefe der St. Galler Missiven (Briefe an die Stadt St. Gallen, 1400–1650)
wie die Editorinnen der Edition. Wähle 4 bis 12 Schlagwörter, die den Inhalt des Briefs bezeichnen:
worum es geht (z.B. Schulden, Erbe, Krieg, Rechtstag), wer handelt (z.B. Vogt, Abt, Bote),
was der Brief tut (z.B. Schriftliche Antwort, Unterstützung, Klage).
Du darfst NUR Schlagwörter aus der folgenden Liste verwenden, genau so geschrieben.
Lieber weniger und sichere als viele. Der Brieftext ist eine ungeprüfte maschinelle Lesung.

Liste der Schlagwörter (in Klammern: wie oft die Edition sie vergibt):
{vocab}

Antworte ausschliesslich mit JSON: {{"keywords": ["...", "..."]}}"""


def vocabulary(letters: list[dict]) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for lt in letters:
        for k in {k.strip() for k in lt.get("keywords", []) if k.strip()}:
            c[k] += 1
    return c


def ask(client: httpx.Client, system: str, shots: list[dict], text: str) -> dict:
    body = {"model": MODEL, "temperature": 0.1, "max_tokens": 400,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": system}, *shots, {"role": "user", "content": text}]}
    last = None
    for attempt in range(4):
        try:
            r = client.post("/chat/completions", json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
            m = re.search(r"\{.*\}", content, re.S)
            return json.loads(m.group(0) if m else content)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (attempt + 1))
    return {"error": str(last)[:300]}


def user_text(text: str, regest: str | None) -> str:
    t = text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n[…]"
    return (f"Regest (Entwurf): {regest}\n\n" if regest else "") + f"Brief:\n{t}"


def clean(answer: dict, vocab: collections.Counter) -> list[str]:
    lower = {k.lower(): k for k in vocab}
    out = []
    for k in answer.get("keywords") or []:
        k = lower.get(str(k).strip().lower())
        if k and k not in out:
            out.append(k)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["eval", "draft"])
    ap.add_argument("--reader", default="gemini-4-argon")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()

    letters = read_edition(ROOT / "data")
    vocab = vocabulary(letters)
    system = SYSTEM.format(vocab="; ".join(f"{k} ({n})" for k, n in sorted(vocab.items(), key=lambda kv: kv[0].lower())))
    digest = hashlib.sha256(system.encode()).hexdigest()[:8]
    readings = load_reader(args.reader)
    usable = [lt for lt in letters if lt.get("keywords") and lt["regest"]
              and all(readings.get(p["facs"]) for p in lt["pages"] if p["text"])]
    # four examples spread over the dates, never scored
    usable.sort(key=lambda lt: lt["date"])
    examples = [usable[int(i * (len(usable) - 1) / 3)] for i in range(4)]
    shots = []
    for lt in examples:
        text = letter_text([readings.get(p["facs"], "") for p in lt["pages"]])
        shots += [{"role": "user", "content": user_text(text, lt["regest"])},
                  {"role": "assistant", "content": json.dumps({"keywords": [k for k in lt["keywords"] if k.strip()]}, ensure_ascii=False)}]

    out_dir = ROOT / "keywords"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{args.mode}_{args.reader}.jsonl"
    kept: dict[str, dict] = {}
    if args.mode == "draft" and path.exists() and not args.redo:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if not r.get("error"):
                    kept[r["id"]] = r

    jobs = []
    if args.mode == "eval":
        ex = {lt["id"] for lt in examples}
        pool = [lt for lt in usable if lt["id"] not in ex]
        pool.sort(key=lambda lt: lt["id"])
        step = max(1, len(pool) // args.n)
        for lt in pool[::step][: args.n]:
            # the eval gives the editors' regest, the draft run a drafted one: the eval is the better case
            jobs.append({"id": lt["id"], "text": user_text(letter_text([readings.get(p["facs"], "") for p in lt["pages"]]), lt["regest"]),
                         "gold": [k for k in lt["keywords"] if k.strip()]})
    else:
        drafts = {}
        rpath = ROOT / "regests" / f"draft_{args.reader}.jsonl"
        if rpath.exists():
            for line in rpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    d = r.get("draft") or {}
                    if not d.get("error"):
                        drafts[r["id"]] = (r["pages"], d.get("regest"))
        known = {p["facs"].lower() for lt in letters for p in lt["pages"]}
        known_stems = {Path(f).stem for f in known}
        groups: dict[int, list[tuple[int, str]]] = {}
        for key in readings:
            if key.lower() in known or Path(key.lower()).stem in known_stems:
                continue
            m = FACS.match(key)
            if m:
                groups.setdefault(int(m.group(1)), []).append((int(m.group(2)), key))
        # keywords follow the regests: a letter is done when it has a draft regest
        todo = [lid for lid in sorted(drafts) if lid not in kept]
        print(f"{len(drafts)} letters with a draft regest, {len(drafts) - len(todo)} already with keywords", flush=True)
        for lid in todo[: args.n]:
            pages, regest = drafts[lid]
            text = letter_text([readings.get(k, "") for k in pages])
            if text.strip():
                jobs.append({"id": lid, "pages": pages, "text": user_text(text, regest)})

    client = httpx.Client(base_url=os.environ["GPUSTACK_BASE_URL"], timeout=600,
                          headers={"Authorization": f"Bearer {os.environ['GPUSTACK_API_KEY']}"})
    print(f"{args.mode}: {len(jobs)} letters via {MODEL}, vocabulary {len(vocab)}", flush=True)
    rows = []
    CHUNK = 40
    with ThreadPoolExecutor(args.workers) as ex:
        for c0 in range(0, len(jobs), CHUNK):
            part = jobs[c0:c0 + CHUNK]
            for j, a in zip(part, ex.map(lambda j: ask(client, system, shots, j["text"]), part)):
                row = {"id": j["id"], "reader": args.reader, "model": MODEL, "prompt": digest, "machine": True}
                if a.get("error"):
                    row["error"] = a["error"]
                else:
                    row["keywords"] = clean(a, vocab)
                    row["dropped"] = [k for k in (a.get("keywords") or []) if str(k).strip().lower() not in {x.lower() for x in vocab}]
                if "gold" in j:
                    row["edition"] = j["gold"]
                if "pages" in j:
                    row["pages"] = j["pages"]
                rows.append(row)
            done = {r["id"] for r in rows}
            out = sorted([r for i, r in kept.items() if i not in done] + rows, key=lambda r: r["id"])
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
            print(f"  {len(rows)}/{len(jobs)}, {sum(1 for r in rows if r.get('error'))} errors", flush=True)

    if args.mode == "eval":
        tp = fp = fn = 0
        per = []
        for r in rows:
            if r.get("error"):
                continue
            got, gold = set(r["keywords"]), set(r["edition"])
            tp += len(got & gold); fp += len(got - gold); fn += len(gold - got)
            per.append(len(got & gold) / len(gold) if gold else 0)
        p = tp / (tp + fp) if tp + fp else 0
        rc = tp / (tp + fn) if tp + fn else 0
        f1 = 2 * p * rc / (p + rc) if p + rc else 0
        print(f"precision {p:.2f}  recall {rc:.2f}  F1 {f1:.2f}  over {len(per)} letters; "
              f"{sum(len(r.get('dropped', [])) for r in rows)} keywords outside the vocabulary dropped")


if __name__ == "__main__":
    main()
