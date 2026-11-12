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
    ap.add_argument("mode", choices=["eval", "draft"])
    ap.add_argument("--reader", default="gemini-4-argon")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--complete-against", default=None,
                    help="file listing every page image of the series; a letter is drafted only when all of its pages have been read")
    args = ap.parse_args()

    letters = read_edition(ROOT / "data")
    by_id = {lt["id"]: lt for lt in letters}
    readings = load_reader(args.reader)
    EXAMPLES[:] = pick_examples(letters, readings)
    print("examples:", ", ".join(f"{i} ({by_id[i]['date']})" for i in EXAMPLES), flush=True)
    shots = example_block(by_id, readings)

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
        for nr in complete[: args.n]:
            keys = [k for _, k in sorted(groups[nr])]
            text = letter_text([readings[k] for k in keys])
            if text.strip():
                jobs.append({"id": f"stasg_missiv_unedited_{nr:05d}", "pages": keys, "text": text})

    client = httpx.Client(base_url=os.environ["GPUSTACK_BASE_URL"], timeout=600,
                          headers={"Authorization": f"Bearer {os.environ['GPUSTACK_API_KEY']}"})
    print(f"{args.mode}: {len(jobs)} letters from {args.reader} via {MODEL}", flush=True)
    with ThreadPoolExecutor(args.workers) as ex:
        answers = list(ex.map(lambda j: ask(client, shots, j["text"]), jobs))

    out_dir = ROOT / "regests"
    out_dir.mkdir(exist_ok=True)
    rows, date_hits, date_n, s_sim, r_sim = [], 0, 0, [], []
    for j, a in zip(jobs, answers):
        row = {"id": j["id"], "pages": j["pages"], "reader": args.reader, "model": MODEL,
               "draft": a, "machine": True}
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
    path = out_dir / f"{args.mode}_{args.reader}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    errors = sum(1 for a in answers if a.get("error"))
    print(f"wrote {path.relative_to(ROOT)}: {len(rows)} letters, {errors} errors")
    if args.mode == "eval" and s_sim:
        print(f"year right {date_hits}/{date_n}; sender similarity median "
              f"{sorted(s_sim)[len(s_sim) // 2]}, recipient {sorted(r_sim)[len(r_sim) // 2]}")


if __name__ == "__main__":
    main()
