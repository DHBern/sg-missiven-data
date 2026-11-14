"""English translations of the regests: the edition's and the drafted ones.

    set -a; . ~/.gpustack.env; set +a
    python scripts/regest_en.py translate --sample 40      # 20 edition + 20 drafts, for a first reading
    python scripts/regest_en.py translate                  # everything not yet translated (or changed)
    python scripts/regest_en.py translate --redo-failing   # once more, what fails a check
    python scripts/regest_en.py check                      # the automatic checks over what is there
    python scripts/regest_en.py review --n 40              # back-translation of a sample, for a person to read

Every regest is German: the edition's (written by its editors, 808 letters) and
the drafts for the letters not yet edited (``scripts/regest.py``). An open model
on the University of Bern's GPUStack (Qwen3.8-27B, no thinking) translates each
one, with a glossary for office titles and fixed phrases; names of persons and
places stay as the regest writes them. For a draft, sender, recipient and place
are translated too, since the letter list shows them.

Each row keeps a hash of the German it was made from, so a rerun translates
only what is new or has changed (a redrafted regest, a regest of a letter read
later). Automatic checks on every translation: every number of the German is in
the English; every name of the edition's registers that the German uses is in
the English; the English is English; the length is in proportion. A translation
that fails a check is asked for once more, with what was wrong; what still fails
keeps its check results in the row and is counted by ``check``.

Output: ``regests/en/edition.jsonl`` and ``regests/en/draft_<reader>.jsonl``.
A translation is a machine translation of the German regest and is marked as
such wherever it is shown; for the edition, it is not the editors' text.
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
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from tei_pages import read_edition  # noqa: E402

MODEL = "qwen3.8-27b"
OUT = ROOT / "regests" / "en"

# office titles and fixed phrases of the regests, one English form each
GLOSSARY = [
    ("Bürgermeister und Rat", "Burgomaster and Council"),
    ("Schultheiss und Rat", "Schultheiss and Council"),
    ("Landammann und Rat", "Landammann and Council"),
    ("Ammann und Rat", "Ammann and Council"),
    ("Statthalter und Rat", "Statthalter and Council"),
    ("Rat", "Council (a town's or land's governing council)"),
    ("Bürger, Bürgerin", "citizen"),
    ("Hintersasse", "resident (Hintersasse)"),
    ("Eidgenossen", "the Confederates"),
    ("Eidgenossenschaft", "the Confederation"),
    ("die (eidgenössischen) Orte", "the (Confederate) cantons"),
    ("Tagsatzung", "the Diet"),
    ("Rechtstag", "court day"),
    ("Vogt", "bailiff"),
    ("Landvogt", "Landvogt"),
    ("Abt, Fürstabt", "abbot, Prince-Abbot"),
    ("Stadtschreiber", "town clerk"),
    ("Bote", "messenger"),
    ("Gesandte", "envoys"),
    ("Gulden, Pfund, Kreuzer, Batzen, Taler", "gulden, pounds, kreuzer, batzen, thaler"),
    ("Kaufleute", "merchants"),
    ("Witwe", "widow"),
    ("Erbe (Sache) / Erben (Personen)", "inheritance / heirs"),
]

SYSTEM = """You translate regests (summaries of letters) of the St. Gallen Missives, letters to the
city of St. Gallen, 1400–1650, from German into English, for historians.

Rules:
- Keep the regest's form: sender, verb, recipient, then the content; present tense; what the
  German gives in indirect speech (Konjunktiv) becomes reported speech ("He says that …").
- Translate faithfully and completely. Add nothing, leave nothing out, explain nothing.
- Names of persons, places and families stay exactly as the German writes them, letters and
  diacritics included: «Zürich», «Konstanz», «St. Gallen», «Hans Müller», «Graf Friedrich von
  Toggenburg» becomes «Count Friedrich von Toggenburg». Do not anglicise them (not Zurich, not Constance).
- Adjectives formed from a place (St. Galler, Zürcher, Lindauer) become «of St. Gallen», «of Zürich»,
  «of Lindau» or «the St. Gallen …», with the place name as above.
- Numbers, years, sums and dates stay as numbers, exactly as they are.
- Use these renderings throughout:
{glossary}

Answer only with JSON with the same keys as the input, each value translated
(null stays null)."""

PROMPT = SYSTEM.format(glossary="\n".join(f"  {de} → {en}" for de, en in GLOSSARY))
PROMPT_DIGEST = hashlib.sha256(PROMPT.encode()).hexdigest()[:8]

_DE = re.compile(r"\b(der|die|das|und|des|den|dem|zu|von|mit|für|wegen|bittet|bitten|sei|seien|habe|ihm|ihr|sich)\b", re.I)
_EN = re.compile(r"\b(the|and|of|to|with|for|that|requests|asks|thanks|he|she|they|his|their|has|have)\b", re.I)
FUNCTION_WORDS = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "und", "von", "vom", "zu", "zum", "zur",
                  "im", "am", "an", "auf", "aus", "bei", "in", "mit", "nach", "ob", "uff", "unter", "zem", "zer"}
NUM = re.compile(r"\d+")
WORD = re.compile(r"[A-ZÄÖÜ][\wÄÖÜäöüßéèàç'-]+")
ARTICLE = re.compile(r"\b(?:der|die|das|den|dem|des|ein|eine|einen|einem|einer|eines|seine[mnrs]?|ihre[mnrs]?|seiner|ihrer)\s+([A-ZÄÖÜ][\wäöüß]+)")


def src_hash(item: dict) -> str:
    return hashlib.sha1(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def register_names(texts: list[str]) -> set[str]:
    """Words of the names in the edition's registers (persons, groups, places) that
    the regests use as names: a word that the regests also write after an article
    («der Rat», «den Grafen») is a common noun, not a name, and is not checked."""
    words: set[str] = set()
    for path in (ROOT / "person" / "person.xml", ROOT / "place" / "place.xml"):
        root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
        for el in root.iter():
            if isinstance(el.tag, str) and el.tag.split("}")[-1] in ("persName", "orgName", "placeName"):
                words.update(w for w in WORD.findall("".join(el.itertext())) if len(w) >= 3)
    nouns = collections.Counter(m for t in texts for m in ARTICLE.findall(t))
    return {w for w in words if nouns[w] < 2 and w.lower() not in FUNCTION_WORDS}


def german_items() -> list[dict]:
    """Every German regest, as {"id", "kind", "de": {key: text}}."""
    items = []
    for lt in read_edition(ROOT / "data"):
        if lt["regest"].strip():
            items.append({"id": lt["id"], "kind": "edition", "de": {"regest": lt["regest"].strip()}})
    for path in sorted((ROOT / "regests").glob("draft_*.jsonl")):
        reader = path.stem.removeprefix("draft_")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            d = r.get("draft") or {}
            if d.get("error") or not d.get("regest"):
                continue
            items.append({"id": r["id"], "kind": f"draft_{reader}",
                          "de": {k: d.get(k) for k in ("regest", "sender", "recipient", "place")}})
    return items


def checks(de: dict, en: dict, names: set[str]) -> dict:
    src = " ".join(v for v in de.values() if v)
    out = " ".join(str(v) for v in en.values() if v)
    nums = collections.Counter(NUM.findall(src)) - collections.Counter(NUM.findall(out))
    used = {w for w in WORD.findall(src) if w in names}
    missing = sorted(w for w in used if w not in out)
    t = en.get("regest") or ""
    ratio = round(len(t.split()) / max(1, len(de["regest"].split())), 2)
    return {"numbers_missing": sorted(nums), "names_missing": missing, "names": len(used),
            "english": len(_EN.findall(t)) > len(_DE.findall(t)), "ratio": ratio,
            "keys": sorted(k for k in de if de[k] and not en.get(k))}


def failed(c: dict) -> list[str]:
    why = []
    if c["numbers_missing"]:
        why.append("these numbers of the German are missing: " + ", ".join(c["numbers_missing"]))
    if c["names_missing"]:
        why.append("these names must stay exactly as in the German: " + ", ".join(c["names_missing"]))
    if not c["english"]:
        why.append("the regest is not in English")
    if not 0.7 <= c["ratio"] <= 1.9:
        why.append("the translation is much shorter or longer than the German")
    if c["keys"]:
        why.append("these keys have no translation: " + ", ".join(c["keys"]))
    return why


def translate(client: httpx.Client, item: dict, names: set[str]) -> dict:
    de = {k: v for k, v in item["de"].items()}
    msgs = [{"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(de, ensure_ascii=False)}]
    last, best = None, None
    for attempt in range(4):
        try:
            r = client.post("/chat/completions", json={
                "model": MODEL, "temperature": 0.1, "max_tokens": 1500,
                "chat_template_kwargs": {"enable_thinking": False}, "messages": msgs})
            r.raise_for_status()
            content = (r.json()["choices"][0]["message"]["content"] or "").strip()
            m = re.search(r"\{.*\}", content, re.S)
            en = json.loads(m.group(0) if m else content)
            en = {k: (en.get(k) if de.get(k) else None) for k in de}
            c = checks(de, en, names)
            if best is None or len(failed(c)) <= len(failed(best["checks"])):
                best = {"en": en, "checks": c}
            best["asked"] = attempt + 1
            why = failed(c)
            if not why or best["asked"] >= 3:
                return best
            if not c["english"]:
                # the model gave the German back: a new conversation, the task said in the request itself
                msgs = [msgs[0], {"role": "user", "content": "Translate the values of this JSON from German into English "
                                  "and answer only with the JSON:\n" + json.dumps(de, ensure_ascii=False)}]
                continue
            # once more, in the same conversation, saying what was wrong
            msgs += [{"role": "assistant", "content": content},
                     {"role": "user", "content": "Please correct the translation: " + "; ".join(why)
                      + ". Answer again only with the JSON."}]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (attempt + 1))
    return best or {"error": str(last)[:300]}


def path_of(kind: str) -> Path:
    return OUT / f"{kind}.jsonl"


def load(kind: str) -> dict[str, dict]:
    p = path_of(kind)
    if not p.exists():
        return {}
    return {r["id"]: r for r in (json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip())}


def save(kind: str, rows: dict[str, dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # through a temporary file: a run stopped while writing must not leave half a file
    tmp = path_of(kind).with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(rows[k], ensure_ascii=False) + "\n" for k in sorted(rows)), encoding="utf-8")
    tmp.replace(path_of(kind))


def client() -> httpx.Client:
    return httpx.Client(base_url=os.environ["GPUSTACK_BASE_URL"], timeout=600,
                        headers={"Authorization": f"Bearer {os.environ['GPUSTACK_API_KEY']}"})


def cmd_translate(args) -> None:
    items = german_items()
    names = register_names([i["de"]["regest"] for i in items])
    kinds = sorted({i["kind"] for i in items})
    have = {k: load(k) for k in kinds}
    todo = [i for i in items if (have[i["kind"]].get(i["id"]) or {}).get("src") != src_hash(i["de"])
            or (have[i["kind"]][i["id"]].get("error"))
            or (args.redo_failing and bool(failed(checks(i["de"], have[i["kind"]][i["id"]]["en"], names))))]
    if args.sample:
        picked = []
        for k in kinds:
            pool = [i for i in items if i["kind"] == k]
            step = max(1, len(pool) // (args.sample // len(kinds)))
            picked += pool[::step][: args.sample // len(kinds)]
        todo = [i for i in picked if i in todo]
    todo = todo[: args.n] if args.n else todo
    print(f"{len(items)} German regests ({', '.join(f'{k} {sum(i['kind'] == k for i in items)}' for k in kinds)}); "
          f"{len(todo)} to translate via {MODEL}; {len(names)} register name words checked", flush=True)
    c = client()
    CHUNK = 40
    with ThreadPoolExecutor(args.workers) as ex:
        for c0 in range(0, len(todo), CHUNK):
            part = todo[c0:c0 + CHUNK]
            for item, res in zip(part, ex.map(lambda i: translate(c, i, names), part)):
                have[item["kind"]][item["id"]] = {"id": item["id"], "src": src_hash(item["de"]), "model": MODEL,
                                                  "prompt": PROMPT_DIGEST, "machine": True, **res}
            for k in kinds:
                save(k, have[k])
            print(f"  {min(c0 + CHUNK, len(todo))}/{len(todo)}", flush=True)
    report(have)


def report(have: dict[str, dict[str, dict]]) -> None:
    for kind, rows in have.items():
        rs = [r for r in rows.values() if "checks" in r]
        if not rs:
            continue
        n = len(rs)
        names = sum(r["checks"]["names"] for r in rs)
        lost = sum(len(r["checks"]["names_missing"]) for r in rs)
        print(f"{kind}: {n} translated, {sum(1 for r in rows.values() if r.get('error'))} errors; "
              f"numbers kept in {sum(not r['checks']['numbers_missing'] for r in rs)}/{n}; "
              f"names kept {names - lost}/{names} ({100 * (names - lost) / max(1, names):.1f} %); "
              f"English {sum(r['checks']['english'] for r in rs)}/{n}; "
              f"length out of proportion {sum(not 0.7 <= r['checks']['ratio'] <= 1.9 for r in rs)}; "
              f"asked twice {sum(r.get('asked', 1) > 1 for r in rs)}; still failing {sum(bool(failed(r['checks'])) for r in rs)}")


def cmd_check(args) -> None:
    items = german_items()
    names = register_names([i["de"]["regest"] for i in items])
    have = {k: load(k) for k in sorted({i["kind"] for i in items})}
    by = {(i["kind"], i["id"]): i for i in items}
    stale = 0
    for kind, rows in have.items():
        for r in rows.values():
            it = by.get((kind, r["id"]))
            if it is None or r.get("error"):
                continue
            stale += r["src"] != src_hash(it["de"])
            r["checks"] = checks(it["de"], r["en"], names)
    report(have)
    print(f"{stale} translations made from a German text that has changed since (rerun translate)")
    if args.write:
        for k, rows in have.items():
            save(k, rows)


def cmd_review(args) -> None:
    """Back-translate a sample into German, side by side with the original, for a person to read."""
    items = german_items()
    kinds = sorted({i["kind"] for i in items})
    have = {k: load(k) for k in kinds}
    rows = []
    for k in kinds:
        done = [i for i in items if i["kind"] == k and "en" in (have[k].get(i["id"]) or {})]
        step = max(1, len(done) // (args.n // len(kinds)))
        rows += [(i, have[k][i["id"]]) for i in done[::step][: args.n // len(kinds)]]
    c = client()

    def back(en: str) -> str:
        r = c.post("/chat/completions", json={
            "model": MODEL, "temperature": 0.1, "max_tokens": 1500, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": "Übersetze den folgenden englischen Text wörtlich ins Deutsche. Antworte nur mit der Übersetzung."},
                         {"role": "user", "content": en}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    with ThreadPoolExecutor(args.workers) as ex:
        backs = list(ex.map(lambda x: back(x[1]["en"]["regest"]), rows))
    lines = [f"# English regests: sample of {len(rows)} for review\n",
             "German original, machine translation, and the translation translated back into German.\n"]
    for (i, r), b in zip(rows, backs):
        lines += [f"## {i['id']} ({i['kind']})\n"]
        if i["de"].get("sender"):
            lines.append(f"- **Parties:** {i['de']['sender']} → {i['de']['recipient']} · *{r['en'].get('sender')} → {r['en'].get('recipient')}*\n")
        lines += [f"**DE:** {i['de']['regest']}\n", f"**EN:** {r['en']['regest']}\n", f"**back:** {b}\n"]
        f = failed(r["checks"])
        if f:
            lines.append(f"**checks:** {'; '.join(f)}\n")
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("translate")
    t.add_argument("--sample", type=int, default=0, help="only an even sample of this many, split over edition and drafts")
    t.add_argument("--n", type=int, default=0)
    t.add_argument("--workers", type=int, default=4)
    t.add_argument("--redo-failing", action="store_true", help="translate again what fails a check")
    ch = sub.add_parser("check")
    ch.add_argument("--write", action="store_true", help="write the recomputed checks into the files")
    rv = sub.add_parser("review")
    rv.add_argument("--n", type=int, default=40)
    rv.add_argument("--workers", type=int, default=4)
    rv.add_argument("--out", default="regests/en/review.md")
    args = ap.parse_args()
    {"translate": cmd_translate, "check": cmd_check, "review": cmd_review}[args.cmd](args)


if __name__ == "__main__":
    main()
