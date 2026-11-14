"""Draft regests for letters from a machine reading, in the edition's style.

    set -a; . ~/.gpustack.env; set +a
    python scripts/regest.py eval  --reader gemini-4-argon --n 20      # edited letters, compared with the editors'
    python scripts/regest.py draft --reader gemini-4-argon --n 100     # letters not yet edited

A letter's pages (the reader's text, in page order) go to an open model on the
University of Bern's GPUStack (Qwen3.8-27B, no thinking) with a few of the
edition's own regests as examples. The answer is JSON: sender, recipient, place
of writing, date (ISO where the letter gives one), and the regest itself, in
the edition's form: "<sender> <verb> <recipient>, …", present tense, the
content in indirect speech, 30–100 words.

The examples are fixed edited letters (one per period); ``eval`` never drafts a
regest for one of them. ``eval`` writes the editors' regest beside the draft so
the two can be read side by side, and scores what can be scored: the date
against ``correspAction/date``, sender and recipient by spelling similarity.

Output: ``regests/<mode>_<reader>.jsonl``. A draft is a draft: it is produced
from an uncorrected reading by a language model and is marked as such wherever
it is shown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dates import Timeline, year_from_text  # noqa: E402
from tei_pages import read_edition  # noqa: E402

MODEL = "qwen3.8-27b"
FACS = re.compile(r"^StadtASG_Missive_(\d+)_(\d+)", re.I)
EXAMPLES: list[str] = []  # chosen in main(): one per quarter of the date range; never drafted in eval


def pick_examples(letters: list[dict], readings: dict, k: int = 4) -> list[str]:
    ok = sorted((lt for lt in letters if lt["date"] and lt["regest"] and len(lt["pages"]) <= 2
                 and all(readings.get(p["facs"]) for p in lt["pages"] if p["text"])),
                key=lambda lt: lt["date"])
    return [ok[int(i * (len(ok) - 1) / (k - 1))]["id"] for i in range(k)]

SYSTEM = """Du bist Editorin der St. Galler Missiven (Briefe an die Stadt St. Gallen, 1400–1650).
Du schreibst Regesten im Stil der Edition: Absender, Verb, Empfänger, dann der Inhalt
in indirekter Rede (Konjunktiv), Präsens, sachlich, 30 bis 100 Wörter, heutige
Schreibung der Namen (z.B. «Bürgermeister und Rat zu St. Gallen», «Konstanz»).
Erfinde nichts: was der Brief nicht sagt, steht nicht im Regest. Der Brieftext ist eine
ungeprüfte maschinelle Lesung und kann Lesefehler enthalten; Namen, die du nicht sicher
lesen kannst, gibst du so wieder, wie sie dastehen.

Antworte ausschliesslich mit JSON:
{"sender": "...", "recipient": "...", "place": "Ausstellungsort oder null",
 "date_text": "die Datumszeile wörtlich, wie sie im Brief steht, oder null",
 "year": "das Jahr als Zahl, wenn der Brief es nennt (auch abgekürzt wie «anno etc. 53» im Jahrhundert der Hand), sonst null",
 "date": "JJJJ-MM-TT nur, wenn der Brief Tag und Monat als Zahlen nennt; Heiligen- und Festtagsdatierungen NICHT umrechnen, dann null",
 "regest": "..."}"""


PROMPT_DIGEST = hashlib.sha256(SYSTEM.encode()).hexdigest()[:8]


def nr_of(letter_id: str, title: str = "") -> int | None:
    m = re.search(r"Nr\.\s*(\d+)", title) or re.search(r"unedited_(\d+)$", letter_id)
    return int(m.group(1)) if m else None


def settle_years(rows: list[dict], letters: list[dict]) -> dict[str, int]:
    """Replace the model's year by the one the date line writes (scripts/dates.py).

    The model closed abbreviated years in the wrong century ("anno etc. lxv°" as
    1515): 13 of 30 right in the evaluation, against 17 (20 within a year) for
    the date line read by rule with the century from the neighbouring letters.
    Anchors: the edited letters, and every drafted letter whose date line gives
    four figures. The model's answer stays in ``year_model``; ``year_how`` says
    where the year came from ("written", "century" or "model")."""
    edited = {lt["id"]: lt for lt in letters}
    anchors = [(nr_of(lt["id"], lt["title"]), int(lt["date"][:4])) for lt in letters if lt["date"][:4].isdigit()]
    for r in rows:
        y, how = year_from_text((r.get("draft") or {}).get("date_text"), None)
        if how == "written" and r["id"] not in edited:
            anchors.append((nr_of(r["id"]), y))
    timeline = Timeline([(n, y) for n, y in anchors if n is not None])
    counts: dict[str, int] = {}
    for r in rows:
        d = r.get("draft") or {}
        if d.get("error"):
            continue
        d.setdefault("year_model", d.get("year"))
        nr = nr_of(r["id"], edited[r["id"]]["title"] if r["id"] in edited else "")
        # in eval, a letter must not date itself
        expected = timeline.expected_year(nr, exclude=nr if r["id"] in edited else None) if nr else None
        y, how = year_from_text(d.get("date_text"), expected)
        if y is None:
            y, how = d.get("year_model"), "model" if d.get("year_model") else ""
        d["year"], d["year_how"] = y, how
        counts[how or "none"] = counts.get(how or "none", 0) + 1
    return counts


def letter_text(pages: list[str]) -> str:
    return "\n\n".join(f"[Seite {i + 1}]\n{t.strip()}" for i, t in enumerate(pages) if t.strip())


def load_reader(reader: str) -> dict[str, str]:
    out = {}
    for line in (ROOT / "readings" / f"{reader}.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[r["key"]] = r.get("text") or ""
    return out


def actors(acts):
    return ", ".join(a["text"] for a in acts if a["type"] != "place")


def example_block(letters_by_id: dict, readings: dict) -> list[dict]:
    msgs = []
    for lid in EXAMPLES:
        lt = letters_by_id[lid]
        text = letter_text([readings.get(p["facs"], "") or p["text"] for p in lt["pages"]])
        answer = {"sender": actors(lt["sender"]), "recipient": actors(lt["recipient"]),
                  "place": next((a["text"] for a in lt["sender"] if a["type"] == "place"), None),
                  "date_text": next((e["text"] for p in lt["pages"] for e in p["entities"] if e["type"] == "date"), None),
                  "year": int(lt["date"][:4]) if lt["date"][:4].isdigit() else None,
                  "date": None, "regest": lt["regest"]}
        msgs.append({"role": "user", "content": f"Brief:\n{text}"})
        msgs.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    return msgs


def ask(client: httpx.Client, shots: list[dict], text: str) -> dict:
    body = {"model": MODEL, "temperature": 0.2, "max_tokens": 1200,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": SYSTEM}, *shots,
                         {"role": "user", "content": f"Brief:\n{text}"}]}
    last = None
    for attempt in range(4):
        try:
            r = client.post("/chat/completions", json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
            content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
            m = re.search(r"\{.*\}", content, re.S)
            return json.loads(m.group(0) if m else content)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (attempt + 1))
    return {"error": str(last)[:300]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["eval", "draft", "years"],
                    help="years: settle the year in existing eval and draft files by rule, no model calls")
    ap.add_argument("--reader", default="gemini-4-argon")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--redo", action="store_true",
                    help="draft again letters that already have a draft (default: keep them; a rerun only adds)")
    ap.add_argument("--complete-against", default=None,
                    help="file listing every page image of the series; a letter is drafted only when all of its pages have been read")
    args = ap.parse_args()

    letters = read_edition(ROOT / "data")
    if args.mode == "years":
        for mode in ("eval", "draft"):
            path = ROOT / "regests" / f"{mode}_{args.reader}.jsonl"
            if not path.exists():
                continue
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            print(path.relative_to(ROOT), settle_years(rows, letters))
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            if mode == "eval":
                gold = [(r["draft"], r["edition"]["date"][:4]) for r in rows if r["edition"].get("date", "")[:4].isdigit()]
                print(f"  year right: rule {sum(str(d.get('year')) == g for d, g in gold)}/{len(gold)}, "
                      f"model {sum(str(d.get('year_model') or '')[:4] == g for d, g in gold)}/{len(gold)}")
        return
    by_id = {lt["id"]: lt for lt in letters}
    readings = load_reader(args.reader)
    EXAMPLES[:] = pick_examples(letters, readings)
    print("examples:", ", ".join(f"{i} ({by_id[i]['date']})" for i in EXAMPLES), flush=True)
    shots = example_block(by_id, readings)

    out_dir = ROOT / "regests"
    path = out_dir / f"{args.mode}_{args.reader}.jsonl"
    kept: dict[str, dict] = {}
    if args.mode == "draft" and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if not (r.get("draft") or {}).get("error"):
                    kept[r["id"]] = r
    jobs = []
    if args.mode == "eval":
        pool = [lt for lt in letters if lt["id"] not in EXAMPLES and lt["regest"]
                and all(readings.get(p["facs"]) for p in lt["pages"] if p["text"])]
        step = max(1, len(pool) // args.n)
        for lt in pool[::step][: args.n]:
            jobs.append({"id": lt["id"], "pages": [p["facs"] for p in lt["pages"]],
                         "text": letter_text([readings.get(p["facs"], "") for p in lt["pages"]]),
                         "gold": {"sender": actors(lt["sender"]), "recipient": actors(lt["recipient"]),
                                  "date": lt["date"], "regest": lt["regest"]}})
    else:
        known = {p["facs"].lower() for lt in letters for p in lt["pages"]}
        known_stems = {Path(f).stem for f in known}
        groups: dict[int, list[tuple[int, str]]] = {}
        for key, text in readings.items():
            if key.lower() in known or Path(key.lower()).stem in known_stems:
                continue
            m = FACS.match(key)
            if m:
                groups.setdefault(int(m.group(1)), []).append((int(m.group(2)), key))
        expected: dict[int, int] = {}
        if args.complete_against:
            for name in Path(args.complete_against).read_text().split():
                m = FACS.match(name)
                if m:
                    expected[int(m.group(1))] = expected.get(int(m.group(1)), 0) + 1
        complete = [nr for nr in sorted(groups) if not expected or len(groups[nr]) >= expected.get(nr, 0)]
        print(f"{len(groups)} letters read, {len(complete)} complete", flush=True)
        if not args.redo:
            before = len(complete)
            complete = [nr for nr in complete if f"stasg_missiv_unedited_{nr:05d}" not in kept]
            print(f"{before - len(complete)} already drafted, kept (--redo to draft them again)", flush=True)
        for nr in complete[: args.n]:
            keys = [k for _, k in sorted(groups[nr])]
            text = letter_text([readings[k] for k in keys])
            if text.strip():
                jobs.append({"id": f"stasg_missiv_unedited_{nr:05d}", "pages": keys, "text": text})

    client = httpx.Client(base_url=os.environ["GPUSTACK_BASE_URL"], timeout=600,
                          headers={"Authorization": f"Bearer {os.environ['GPUSTACK_API_KEY']}"})
    print(f"{args.mode}: {len(jobs)} letters from {args.reader} via {MODEL}", flush=True)
    out_dir.mkdir(exist_ok=True)
    answers = []
    # in chunks, each written out at once: a long draft run that stops keeps what it has,
    # and the next run (which keeps existing drafts) goes on from there
    CHUNK = 40
    with ThreadPoolExecutor(args.workers) as ex:
        for c0 in range(0, len(jobs), CHUNK):
            answers += list(ex.map(lambda j: ask(client, shots, j["text"]), jobs[c0:c0 + CHUNK]))
            if args.mode == "draft" and c0 + CHUNK < len(jobs):
                part = [{"id": j["id"], "pages": j["pages"], "reader": args.reader, "model": MODEL,
                         "prompt": PROMPT_DIGEST, "draft": a, "machine": True} for j, a in zip(jobs, answers)]
                settle_years(part, letters)
                done = {r["id"] for r in part}
                rows_now = sorted([r for i, r in kept.items() if i not in done] + part, key=lambda r: r["id"])
                path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows_now), encoding="utf-8")
                print(f"  {len(answers)}/{len(jobs)} drafted, {sum(1 for x in answers if x.get('error'))} errors", flush=True)

    rows, date_hits, date_n, s_sim, r_sim = [], 0, 0, [], []
    for j, a in zip(jobs, answers):
        row = {"id": j["id"], "pages": j["pages"], "reader": args.reader, "model": MODEL,
               "prompt": PROMPT_DIGEST, "draft": a, "machine": True}
        if "gold" in j:
            g = j["gold"]
            row["edition"] = g
            if g["date"]:
                date_n += 1
                date_hits += str(a.get("year") or "")[:4] == g["date"][:4]
            if not a.get("error"):
                s_sim.append(fuzz.token_set_ratio(a.get("sender") or "", g["sender"]))
                r_sim.append(fuzz.token_set_ratio(a.get("recipient") or "", g["recipient"]))
        rows.append(row)
    print("years:", settle_years(rows, letters), flush=True)
    if args.mode == "draft":
        new = {r["id"] for r in rows}
        rows = sorted([r for i, r in kept.items() if i not in new] + rows, key=lambda r: r["id"])
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    errors = sum(1 for a in answers if a.get("error"))
    print(f"wrote {path.relative_to(ROOT)}: {len(rows)} letters ({len(answers)} drafted now), {errors} errors")
    if args.mode == "eval" and s_sim:
        print(f"year right {date_hits}/{date_n}; sender similarity median "
              f"{sorted(s_sim)[len(s_sim) // 2]}, recipient {sorted(r_sim)[len(r_sim) // 2]}")


if __name__ == "__main__":
    main()
