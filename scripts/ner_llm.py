"""LLM tagging with a judge, measured like the hmBERT tagger (docs/NER.md).

    1. qwen3.8-27b tags a page's reading with the edition's ten labels
       (persName, persName@issuer, persName@recipient, orgName, orgName@issuer,
       orgName@recipient, placeName, origPlace, origDate, date). It returns each
       name as an exact quote; the script finds it in the text.
    2. gpt-oss-120b judges every proposed span: keep or drop, the label, and for
       persons, organisations and places which register entry it is, chosen from
       candidates the script offers (spelling close enough; persons only if the
       edition attests them within ±40 years of the letter), or none.
    3. Both are scored with ner.evaluate() against the edition's tags, exactly
       as the hmBERT cross-validation was.

The pilot runs on the late letters (Nr. >= 1000), the gate's test set. The
few-shot examples and the register knowledge (known spellings, years) come
only from the other letters, so nothing of a test letter is shown.

    ner_llm.py pilot [--limit N] [--workers 4]

Answers are cached in ner_llm/.cache/ (not committed). Needs ~/.gpustack.env
(GPUSTACK_BASE_URL, GPUSTACK_API_KEY) and the UniBE network.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ner  # noqa: E402
from build_site import registers as load_registers  # noqa: E402
from rapidfuzz import fuzz, process  # noqa: E402

TAGGER = "qwen3.8-27b"
JUDGE = "gpt-oss-120b"
OUT = ROOT / "ner_llm"
CACHE = OUT / ".cache"
LATE = 1000

GUIDE = """You tag a page of a letter from the St. Gallen Missiven (letters to and from the city of
St. Gallen, 1400-1650, Early New High German, as read by a machine, with its misreadings). Use the
edition's tag set, exactly these labels:

- persName            a person named in the text (given name and/or surname, as written; without titles
                      like "her", "junckher", "meister", without offices, without "von" + place unless it is
                      part of the name as written)
- persName@issuer     a person who issues the letter: the sender named in the signature or the opening
- persName@recipient  a person the letter is addressed to: in the address or the salutation
- orgName             a group or institution: a city council, a town as political body ("die von Zürich"),
                      the Confederates ("Eidgenossen"), a monastery, a guild
- orgName@issuer      the issuing body (e.g. "Burgermaister und Rat der Statt Costentz" in the signature)
- orgName@recipient   the addressed body (e.g. the address "Den ... Burgermaister und Rat zuo Sant Gallen")
- placeName           a place: town, village, region, river, as a place (not as a political body)
- origPlace           the place of writing in the date line ("Geben zuo Lindow ...": tag "Lindow")
- origDate            the date of the date line, from the day/feast to the year ("uff mentag nach Galli
                      anno etc. lxxxiii")
- date                any other date in the text

Rules: tag only what is in the text; quote it exactly, character for character, as it stands in the page
(keep the misreadings). No nesting: a place inside an organisation's name is not tagged separately. Every
occurrence is tagged, in reading order. Roles (issuer/recipient) only in address, salutation, signature or
the opening sentence that names the sender; elsewhere the plain label."""

TAG_FORMAT = """Answer with JSON only: {"entities": [{"text": "<exact quote>", "label": "<label>"}, ...]}"""

JUDGE_GUIDE = """You check proposed tags on a page of a letter from the St. Gallen Missiven (1400-1650), using the
edition's tag set and rules below. For each numbered proposal decide:
- keep: true by default. The page is a machine reading with misreadings: a garbled or unknown name is
  still a name, and a name with no register candidate is still kept. Set false ONLY when the span is clearly
  not a name or date at all (an ordinary word, a title or office alone such as "her burgermeister" without
  a name), or when it duplicates a span that overlaps it. Give the reason in "why" when you set false.
- label: the correct label (see the tag set); change it if wrong, including the role
- ref: for persName/orgName/placeName/origPlace labels, the id of the register entry it refers to, chosen
  ONLY from the candidates listed under the proposal, or null. Choose an entry only if name, kind and time
  fit; persons share names across generations, so check the years. When in doubt, null.

Tag set and rules:
""" + GUIDE.split("Use the\nedition's tag set, exactly these labels:\n", 1)[1]

JUDGE_FORMAT = """Answer with JSON only: {"decisions": [{"i": <number>, "keep": true|false, "label": "<label>", "ref": "<id>"|null, "why": "<only when keep is false>"}, ...]}
One decision per proposal, in order. "label" is always one of the ten labels, also when you keep the proposal unchanged.
Proposals come from two taggers and may overlap: of overlapping proposals keep at most one, the best delimited."""


# ------------------------------------------------------------------------- calls

def env() -> tuple[str, str]:
    vals = {}
    for line in Path(os.path.expanduser("~/.gpustack.env")).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return vals["GPUSTACK_BASE_URL"].rstrip("/"), vals["GPUSTACK_API_KEY"]


BASE, KEY = env()


def chat(model: str, messages: list[dict], **extra) -> tuple[str, dict]:
    body = {"model": model, "messages": messages, "temperature": 0, **extra}
    ck = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{ck}.json"
    if f.exists():
        d = json.loads(f.read_text())
        return d["content"], d["usage"]
    for attempt in range(4):
        try:
            t0 = time.time()
            req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.loads(r.read().decode())
            content = d["choices"][0]["message"].get("content") or ""
            usage = {**(d.get("usage") or {}), "seconds": round(time.time() - t0, 1),
                     "finish": d["choices"][0].get("finish_reason")}
            f.write_text(json.dumps({"content": content, "usage": usage}, ensure_ascii=False))
            return content, usage
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                raise
            print(f"  {model}: {str(exc)[:120]} (retry)", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for cand in (text, (re.search(r"\{.*\}", text, re.S) or [None])[0]):
        if not cand:
            continue
        try:
            return json.loads(cand, strict=False)  # models put raw newlines inside strings
        except ValueError:
            continue
    return {"_unreadable": True}


# ------------------------------------------------------------------------ tagging

def locate(text: str, quotes: list[tuple[str, str]]) -> tuple[list[dict], int]:
    """Exact quotes -> spans, in reading order; a quote not found is dropped."""
    out, missed, cursor = [], 0, 0
    for q, label in quotes:
        q = q.strip()
        if not q or label not in ner.TYPES:
            missed += 1
            continue
        i = text.find(q, cursor)
        if i < 0:
            i = text.find(q)
        if i < 0:  # line breaks or doubled spaces in the reading
            rx = re.compile(r"\s+".join(map(re.escape, q.split())))
            m = rx.search(text, cursor) or rx.search(text)
            if not m:
                missed += 1
                continue
            i, j = m.start(), m.end()
        else:
            j = i + len(q)
        if any(not (j <= x["start"] or i >= x["end"]) for x in out):
            continue  # overlaps one already placed
        out.append({"start": i, "end": j, "text": text[i:j], "type": label})
        cursor = j
    out.sort(key=lambda x: x["start"])
    return out, missed


def examples(pages: list[dict], n: int = 3) -> list[dict]:
    """Short pages from early letters with both roles tagged: the few-shot examples."""
    pool = [p for p in pages if p["has_edition"] and (p.get("nr") or 0) < LATE and 300 < len(p["text"]) < 1400
            and any(g["type"].endswith("@issuer") for g in p["projected"])
            and any(g["type"].endswith("@recipient") for g in p["projected"])
            and len(p["projected"]) >= 5]
    pool.sort(key=lambda p: (p.get("year") or 0, p["facs"]))
    if not pool:
        return []
    step = max(1, len(pool) // n)
    return pool[::step][:n]


def tag_messages(page: dict, shots: list[dict]) -> list[dict]:
    msgs = [{"role": "system", "content": GUIDE + "\n\n" + TAG_FORMAT}]
    for s in shots:
        ents = [{"text": g["text"], "label": g["type"]} for g in sorted(s["projected"], key=lambda g: g["start"])]
        msgs.append({"role": "user", "content": s["text"]})
        msgs.append({"role": "assistant", "content": json.dumps({"entities": ents}, ensure_ascii=False)})
    msgs.append({"role": "user", "content": page["text"]})
    return msgs


def tag(page: dict, shots: list[dict]) -> tuple[list[dict], dict]:
    content, usage = chat(TAGGER, tag_messages(page, shots), max_tokens=3000,
                          chat_template_kwargs={"enable_thinking": False})
    data = parse_json(content)
    quotes = [(e.get("text", ""), e.get("label", "")) for e in data.get("entities", []) if isinstance(e, dict)]
    spans, missed = locate(page["text"], quotes)
    return spans, {**usage, "proposed": len(quotes), "missed": missed, "unreadable": int(bool(data.get("_unreadable")))}


def union(hm: list[dict], qw: list[dict]) -> list[dict]:
    """Both taggers' spans as proposals. The same span from both is one proposal
    (labelled as hmBERT has it if they disagree; the judge decides); different
    boundaries stay separate proposals."""
    out = {}
    for src, spans in (("hmBERT", hm), ("qwen3.8", qw)):
        for x in spans:
            k = (x["start"], x["end"])
            if k in out:
                out[k]["by"] += f"+{src}" + (f" as {x['type']}" if out[k]["type"] != x["type"] else "")
            else:
                out[k] = {"start": x["start"], "end": x["end"], "text": x["text"], "type": x["type"], "by": src}
    return sorted(out.values(), key=lambda x: (x["start"], x["end"]))


def load_hmbert(path: Path) -> dict[str, list[dict]]:
    """hmBERT's spans per page from entities/<reader>.jsonl (labels as el@role)."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["key"]] = [{"start": e["start"], "end": e["end"], "text": e["text"],
                              "type": e["el"] + ("@" + e["role"] if e.get("role") else "")}
                             for e in r.get("entities", []) if e.get("el")]
    return out


# -------------------------------------------------------------------------- judge

class Candidates:
    """Register candidates for a span: close spellings (known from the other
    letters, or the register's own), persons only within the time window."""

    def __init__(self, linker: ner.Linker, reg: dict[str, dict]):
        self.linker, self.reg = linker, reg

    def __call__(self, label: str, text: str, year: int | None, n: int = 5) -> list[dict]:
        kind = ner.coarse(label)
        if kind == "date":
            return []
        k = ner.key(text)
        if not k:
            return []
        pool = [(name, ref) for name, ref in self.linker.by_kind.get(kind, [])
                if kind != "per" or self.linker.near(ref, year)]
        if not pool:
            return []
        hits = process.extract(k, [x for x, _ in pool], scorer=fuzz.WRatio, limit=n * 4, score_cutoff=80)
        out, seen = [], set()
        for _, score, i in hits:
            ref = pool[i][1]
            if ref in seen or ref not in self.reg:
                continue
            seen.add(ref)
            e = self.reg[ref]
            yrs = self.linker.years.get(ref, [])
            out.append({"id": ref, "name": e["name"], "desc": (e.get("desc") or "")[:100],
                        "attested": f"{min(yrs)}-{max(yrs)}" if yrs else "", "score": round(score)})
            if len(out) == n:
                break
        return out


def judge(page: dict, spans: list[dict], cands: Candidates) -> tuple[list[dict], dict]:
    year = page.get("year") or page.get("expected")
    lines = []
    offered = []
    for i, x in enumerate(spans):
        ctx = page["text"][max(0, x["start"] - 60): x["end"] + 60].replace("\n", " / ")
        cs = cands(x["type"], x["text"], int(year) if year else None)
        offered.append({c["id"] for c in cs})
        by = x.get("by")
        lines.append(f'{i}. "{x["text"]}" proposed {x["type"]}' + (f' (by {by})' if by else "") + f'   context: …{ctx}…')
        for c in cs:
            lines.append(f'     candidate {c["id"]}: {c["name"]}' + (f' — {c["desc"]}' if c["desc"] else "")
                         + (f' (attested in the edition {c["attested"]})' if c["attested"] else ""))
    user = (f"Letter year: {int(year) if year else 'unknown'}\n\nPage:\n{page['text']}\n\nProposals:\n"
            + ("\n".join(lines) if lines else "(none)"))
    content, usage = chat(JUDGE, [{"role": "system", "content": JUDGE_GUIDE + "\n\n" + JUDGE_FORMAT},
                                  {"role": "user", "content": user}],
                          max_tokens=6000, reasoning_effort="low")
    data = parse_json(content)
    dec = {d.get("i"): d for d in data.get("decisions", []) if isinstance(d, dict)}
    out = []
    for i, x in enumerate(spans):
        d = dec.get(i)
        if d is None:  # no decision: keep as proposed, unlinked
            out.append({**x, "ref": None, "link_how": None, "link_score": None})
            continue
        if not d.get("keep"):
            continue
        label = d.get("label") if d.get("label") in ner.TYPES else x["type"]
        ref = d.get("ref") if d.get("ref") in offered[i] else None
        out.append({**x, "type": label, "ref": ref, "link_how": "judge" if ref else None, "link_score": None})
    out.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    kept = []
    for x in out:  # the judge was told to keep one of overlapping spans; enforce it
        if kept and x["start"] < kept[-1]["end"]:
            continue
        kept.append(x)
    return kept, {**usage, "decided": len(dec), "of": len(spans), "unreadable": int(bool(data.get("_unreadable")))}


# ------------------------------------------------------------------------- report

def scores(pages: list[dict], key_pred: str) -> dict:
    for p in pages:
        p["pred"] = p[key_pred]
    coarse = ner.evaluate(pages)["edition"]
    fine = ner.evaluate(pages, fine_types=True)["edition"]
    return {"coarse": {t: coarse[t]["f1"] for t in ("per", "org", "place", "date", "all")},
            "fine": {t: fine[t]["f1"] for t in ner.TYPES + ["all"]},
            "linking": coarse["linking"]}


def tag_unedited(a, shots, linker, cands) -> None:
    """hmBERT + qwen3.8 proposals, gpt-oss decides, on every unedited page that
    hmBERT has tagged. Replaces those pages' records in entities/<reader>.jsonl
    (hmBERT's alone are kept in entities/hmbert/). Register links only of the
    kinds the combined measurement put at precision >= 0.9."""
    path = ROOT / "entities" / f"{a.reader}.jsonl"
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            records[r["key"]] = r
    backup = ROOT / "entities" / "hmbert" / f"{a.reader}.jsonl"
    if not backup.exists():
        backup.parent.mkdir(exist_ok=True)
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    hm = load_hmbert(backup)
    report = json.loads((OUT / "combined_report.json").read_text())
    prec = report["hmbert+qwen+judge"]["linking"]["precision_by_kind"]
    allowed = {k.split("/")[0] for k, v in prec.items() if k.endswith("/judge") and v["precision"] >= ner.MIN_PRECISION
               and v["written"] >= 20}
    print(f"links written for: {sorted(allowed)}", flush=True)
    pages, _ = ner.build_pages(a.reader, None, unedited=True)
    todo = [p for p in pages if not p["has_edition"] and p["facs"] in records
            and records[p["facs"]].get("method") != "combined"]
    if a.limit:
        todo = todo[: a.limit]
    print(f"{len(todo)} unedited pages to tag", flush=True)
    t0, done = time.time(), 0

    def one(p):
        spans, _ = tag(p, shots)
        judged, _ = judge(p, union(hm.get(p["facs"], []), spans), cands)
        return p, judged

    def flush():
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records.values()), encoding="utf-8")
        tmp.replace(path)

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for fut in cf.as_completed([ex.submit(one, p) for p in todo]):
            try:
                p, judged = fut.result()
            except Exception as exc:  # noqa: BLE001  one page failing must not stop the run
                print(f"  failed: {exc}", file=sys.stderr)
                continue
            year = p.get("year") or p.get("expected")
            ents = []
            for x in judged:
                if x.get("ref") and ner.coarse(x["type"]) not in allowed:
                    x["ref"] = None
                if x["type"] == "origDate":
                    x["when"], x["date_cert"] = ner.when_of(x["text"], year)
                e = ner.entity_out(x)
                if x.get("ref"):
                    e["link_how"], e["cert"] = "judge", "medium"
                if x.get("by"):
                    e["by"] = x["by"]
                ents.append(e)
            records[p["facs"]] = {**records[p["facs"]], "entities": ents, "method": "combined",
                                  "tagger": [ner.BASE, TAGGER], "judge": JUDGE}
            done += 1
            if done % 100 == 0:
                flush()
                print(f"  {done}/{len(todo)} pages, {(time.time() - t0) / 60:.0f} min", flush=True)
    flush()
    print(f"tagged {done} pages; {path.relative_to(ROOT)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["pilot", "combined", "tag"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--reader", default="gemini-4-argon")
    a = ap.parse_args()

    pages, _ = ner.build_pages(a.reader, None)
    test = [p for p in pages if p["has_edition"] and (p.get("nr") or 0) >= LATE]
    train = [p for p in pages if p["has_edition"] and (p.get("nr") or 0) < LATE]
    test.sort(key=lambda p: p["facs"])
    if a.limit:
        test = test[: a.limit]
    shots = examples(train)
    print(f"{len(test)} late-letter pages; {len(shots)} examples from early letters: "
          + ", ".join(s["facs"] for s in shots), flush=True)
    reg = load_registers()
    linker = ner.Linker(train, ner.register_names())
    cands = Candidates(linker, reg)

    if a.cmd == "tag":
        tag_unedited(a, shots, ner.Linker([p for p in pages if p["has_edition"]], ner.register_names()),
                     Candidates(ner.Linker([p for p in pages if p["has_edition"]], ner.register_names()), reg))
        return

    usage = Counter()
    t0 = time.time()

    hm = load_hmbert(ROOT / "entities" / f"{a.reader}.jsonl") if a.cmd == "combined" else {}
    if a.cmd == "combined":
        missing = [p["facs"] for p in test if p["facs"] not in hm]
        print(f"hmBERT spans for {len(test) - len(missing)} of {len(test)} pages", flush=True)

    def one(p):
        spans, u1 = tag(p, shots)
        proposals = union(hm.get(p["facs"], []), spans) if a.cmd == "combined" else spans
        judged, u2 = judge(p, proposals, cands)
        return p, spans, judged, u1, u2

    done = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for fut in cf.as_completed([ex.submit(one, p) for p in test]):
            p, spans, judged, u1, u2 = fut.result()
            p["qwen"] = [dict(x) for x in spans]
            p["judged"] = judged
            for k in ("proposed", "missed"):
                usage[k] += u1.get(k, 0)
            usage["tag_unreadable"] += u1.get("unreadable", 0); usage["judge_unreadable"] += u2.get("unreadable", 0)
            usage["tag_s"] += u1.get("seconds", 0); usage["judge_s"] += u2.get("seconds", 0)
            usage["tag_tokens"] += u1.get("completion_tokens", 0); usage["judge_tokens"] += u2.get("completion_tokens", 0)
            usage["undecided"] += u2["of"] - u2["decided"]
            usage["tag_cut"] += u1.get("finish") == "length"; usage["judge_cut"] += u2.get("finish") == "length"
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(test)} pages, {time.time() - t0:.0f} s", flush=True)

    # qwen alone, linked by the rules (all link kinds written, to measure them)
    for p in test:
        p["qwen_rules"] = [dict(x) for x in p["qwen"]]
    for p in test:
        p["pred"] = p["qwen_rules"]
    ner.apply_links(test, linker, None)
    report = {"pages": len(test), "examples": [s["facs"] for s in shots], "tagger": TAGGER, "judge": JUDGE,
              "usage": dict(usage), "minutes": round((time.time() - t0) / 60, 1),
              "qwen": scores(test, "qwen_rules"),
              ("hmbert+qwen+judge" if a.cmd == "combined" else "qwen+judge"): scores(test, "judged"),
              "hmbert_late_reference": "entities/gemini-4-argon.report.json, late_letters"}
    OUT.mkdir(exist_ok=True)
    (OUT / f"{a.cmd}_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    with (OUT / f"{a.cmd}_pages.jsonl").open("w", encoding="utf-8") as fh:
        for p in test:
            fh.write(json.dumps({"key": p["facs"], "qwen": [ner.entity_out(x) for x in p["qwen_rules"]],
                                 "judged": [ner.entity_out(x) for x in p["judged"]]}, ensure_ascii=False) + "\n")
    for name in ("qwen", "hmbert+qwen+judge" if a.cmd == "combined" else "qwen+judge"):
        r = report[name]
        print(f"{name:11s} " + "  ".join(f"{t} {v:.3f}" for t, v in r["coarse"].items()))
        print("            links: " + ", ".join(f"{k} {v['right']}/{v['written']}={v['precision']}"
                                            for k, v in r["linking"]["precision_by_kind"].items()))
    print(json.dumps(dict(usage)))


if __name__ == "__main__":
    main()
