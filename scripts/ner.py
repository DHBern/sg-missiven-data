"""Automatic tagging of a machine reading, learned from the edition's own tagging.

The edition tags persons, organisations, places and dates (``persName``,
``orgName``, ``placeName``, ``origDate``/``date``). Its text is not the
reading's text, so the tags are first **projected**: the edition text of a page
is aligned character by character with the reading, and each tagged span is
carried over to the words it lands on. A span whose landing text differs too
much from the edited name is not trusted either way — its words are left out of
training rather than taught as "no entity".

A token classifier (hmBERT, ``dbmdz/bert-base-historic-multilingual-cased``)
is then trained on the projected readings in five folds **by letter**: every
page is tagged by a model that never saw that letter, so the evaluation is
honest and the whole corpus is covered.

Linking resolves a tagged name to the edition's registers (``stadtasg-actors-*``,
``stadtasg-places-*``): first by the surface forms the training letters link,
then by the register's own names and spellings. The evaluation scores both
against the edition.

    python scripts/ner.py run --reader gemini-4-argon      # train 5 folds, tag, link, score
    python scripts/ner.py run --reader gemini-4-argon --folds 1 --epochs 1 --limit 200   # smoke
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
import zlib
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from rapidfuzz import fuzz, process  # noqa: E402
from rapidfuzz.distance import Levenshtein  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dates import Timeline, year_from_text  # noqa: E402
from tei_pages import read_edition  # noqa: E402

# the edition's elements, with the two roles it marks (docs/NER.md)
TYPES = ["persName", "persName@issuer", "persName@recipient", "orgName", "orgName@issuer",
         "orgName@recipient", "placeName", "origPlace", "origDate", "date"]
COARSE = {"persName": "per", "orgName": "org", "placeName": "place", "origPlace": "place",
          "origDate": "date", "date": "date"}
WINDOW = 40          # years: a register person is a candidate only if attested this close
CUTOFF = 95          # fuzzy link score below which no @ref is written
MIN_PRECISION = 0.9  # a link kind measured below this writes no @ref
MODEL_DIR = ROOT / "models"
LABELS = ["O"] + [f"{p}-{t}" for t in TYPES for p in ("B", "I")]


def fine(ent: dict) -> str:
    """The training label of an edition tag: element, plus the role where the
    edition marks issuer or recipient."""
    el = ent.get("el") or {"per": "persName", "org": "orgName", "place": "placeName", "date": "date"}[ent["type"]]
    if el == "placeName" and ent.get("role") == "issuer":
        return "placeName"
    role = ent.get("role") if ent.get("role") in ("issuer", "recipient") else ""
    return f"{el}@{role}" if role and f"{el}@{role}" in TYPES else el


def coarse(label: str) -> str:
    return COARSE[label.split("@")[0]]
L2I = {l: i for i, l in enumerate(LABELS)}
WORD = re.compile(r"\w+|[^\w\s]")
BASE = "dbmdz/bert-base-historic-multilingual-cased"
CHUNK_WORDS = 160


# ── data ─────────────────────────────────────────────────────────────────────

def outer_spans(spans: list[dict]) -> list[dict]:
    """Nested tags (a place inside an organisation) cannot both be BIO labels:
    keep the outer one, as the edition reads it."""
    keep: list[dict] = []
    for s in sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"]))):
        if keep and s["start"] < keep[-1]["end"]:
            continue
        keep.append(s)
    return keep


def char_map(a: str, b: str) -> list[int]:
    """For every position 0..len(a) in ``a``, the corresponding position in ``b``."""
    m = [0] * (len(a) + 1)
    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(a, b):
        for k in range(i1, i2 + 1):
            if tag == "delete":
                m[k] = j1
            elif i2 > i1:
                m[k] = j1 + round((k - i1) * (j2 - j1) / (i2 - i1))
            else:
                m[k] = j1
    m[len(a)] = len(b)
    return m


def snap(text: str, s: int, e: int) -> tuple[int, int]:
    while s > 0 and (text[s - 1].isalnum()):
        s -= 1
    while e < len(text) and text[e].isalnum():
        e += 1
    while s < e and not text[s].isalnum():
        s += 1
    while e > s and not (text[e - 1].isalnum() or text[e - 1] == "."):
        e -= 1
    return s, e


def project(ed_text: str, spans: list[dict], reading: str) -> tuple[list[dict], list[tuple[int, int]]]:
    """Carry the edition's spans onto the reading. Returns trusted spans and
    the character ranges of untrusted ones (to be ignored in training)."""
    m = char_map(ed_text, reading)
    good, unsure = [], []
    for sp in outer_spans(spans):
        s, e = m[sp["start"]], m[sp["end"]]
        if e <= s:
            continue
        s, e = snap(reading, s, e)
        if e <= s:
            continue
        got = reading[s:e]
        gold = ed_text[sp["start"]:sp["end"]]
        score = fuzz.ratio(" ".join(gold.split()).lower(), " ".join(got.split()).lower())
        if score >= 70:
            good.append({**sp, "start": s, "end": e, "text": got})
        else:
            unsure.append((s, e))
    return good, unsure


def words_and_labels(text: str, spans: list[dict], unsure: list[tuple[int, int]]):
    words, offs, labels = [], [], []
    for m in WORD.finditer(text):
        words.append(m.group())
        offs.append((m.start(), m.end()))
        labels.append("O")
    for sp in spans:
        first = True
        for i, (a, b) in enumerate(offs):
            if a >= sp["start"] and b <= sp["end"]:
                labels[i] = ("B-" if first else "I-") + sp["type"]
                first = False
    for s, e in unsure:
        for i, (a, b) in enumerate(offs):
            if a < e and b > s and labels[i] == "O":
                labels[i] = None  # ignored in the loss
    return words, offs, labels


def fold_of(letter_id: str, k: int) -> int:
    return zlib.crc32(letter_id.encode()) % k


def build_pages(reader: str, limit: int | None, unedited: bool = False):
    readings = {}
    for line in (ROOT / "readings" / f"{reader}.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        readings[r["key"]] = "\n".join(l for l in (r.get("text") or "").splitlines() if l.strip())
    pages = []
    stats = Counter()
    letters = read_edition(ROOT / "data")
    timeline = Timeline([(n, int(lt["date"][:4])) for lt in letters
                         for n in [letter_nr(lt["title"])] if n and lt["date"][:4].isdigit()])
    for letter in letters:
        year = int(letter["date"][:4]) if letter["date"][:4].isdigit() else None
        nr = letter_nr(letter["title"])
        for p in letter["pages"]:
            text = readings.get(p["facs"])
            if not text:
                continue
            ents = [{**e, "type": fine(e), "coarse": e["type"]} for e in p["entities"]]
            gold = outer_spans(ents) if p["text"] else []
            good, unsure = project(p["text"], ents, text) if p["text"] else ([], [])
            stats["gold"] += len(gold)
            stats["projected"] += len(good)
            stats["unsure"] += len(unsure)
            words, offs, labels = words_and_labels(text, good, unsure)
            pages.append({"letter": letter["id"], "facs": p["facs"], "text": text,
                          "edition_text": p["text"], "gold": gold, "projected": good,
                          "words": words, "offs": offs, "labels": labels,
                          "has_edition": bool(p["text"]), "year": year, "nr": nr,
                          "expected": timeline.expected_year(nr, exclude=nr) if nr else None})
    if unedited:
        pages += unedited_pages(readings, letters, timeline)
    if limit:
        random.Random(0).shuffle(pages)
        pages = pages[:limit]
    return pages, stats


def letter_nr(title: str) -> int | None:
    m = re.search(r"Nr\.\s*(\d+)", title or "")
    return int(m.group(1)) if m else None


def unedited_pages(readings: dict, letters: list[dict], timeline: Timeline) -> list[dict]:
    """Pages read but not in the edition, with the letter's year: from its
    drafted regest (year read by rule, scripts/regest.py), else from the
    neighbouring letters."""
    known = {Path(p["facs"]).stem.lower() for lt in letters for p in lt["pages"]}
    drafts = {}
    for path in (ROOT / "regests").glob("draft_*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                drafts[r["id"]] = r.get("draft") or {}
    facs_re = re.compile(r"^StadtASG_Missive_(\d+)_", re.I)
    out = []
    for key, text in readings.items():
        m = facs_re.match(key)
        if not m or Path(key).stem.lower() in known or not text:
            continue
        nr = int(m.group(1))
        lid = f"stasg_missiv_unedited_{nr:05d}"
        y = drafts.get(lid, {}).get("year")
        expected = timeline.expected_year(nr)
        year = int(y) if str(y or "").isdigit() else (round(expected) if expected else None)
        words, offs, labels = words_and_labels(text, [], [])
        out.append({"letter": lid, "facs": key, "text": text, "edition_text": "", "gold": [],
                    "projected": [], "words": words, "offs": offs, "labels": labels,
                    "has_edition": False, "unedited": True, "year": year, "nr": nr,
                    "expected": expected})
    return out


# ── model ────────────────────────────────────────────────────────────────────

def chunks(page):
    n = len(page["words"])
    for a in range(0, max(n, 1), CHUNK_WORDS):
        yield a, min(n, a + CHUNK_WORDS)


def encode(tok, words, labels=None):
    enc = tok(words, is_split_into_words=True, truncation=True, max_length=512)
    if labels is not None:
        ids, prev = [], None
        for wid in enc.word_ids():
            if wid is None:
                ids.append(-100)
            elif wid != prev:
                lab = labels[wid]
                ids.append(-100 if lab is None else L2I[lab])
            else:
                ids.append(-100)
            prev = wid
        enc["labels"] = ids
    return enc


def train_fold(train_pages, epochs, device, seed=0):
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer, DataCollatorForTokenClassification

    torch.manual_seed(seed)
    tok = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForTokenClassification.from_pretrained(
        BASE, num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=L2I).to(device)
    items = []
    for p in train_pages:
        if not p["has_edition"]:
            continue
        for a, b in chunks(p):
            if b > a:
                items.append(encode(tok, p["words"][a:b], p["labels"][a:b]))
    coll = DataCollatorForTokenClassification(tok)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-5, weight_decay=0.01)
    bs = 16
    steps = epochs * ((len(items) + bs - 1) // bs)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, (steps - s) / steps))
    model.train()
    rng = random.Random(seed)
    step = 0
    for ep in range(epochs):
        rng.shuffle(items)
        t0, tot = time.time(), 0.0
        for i in range(0, len(items), bs):
            batch = coll([{k: v for k, v in it.items()} for it in items[i:i + bs]])
            batch = {k: v.to(device) for k, v in batch.items()}
            loss = model(**batch).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
            tot += loss.item(); step += 1
        print(f"    epoch {ep + 1}/{epochs}: loss {tot / max(1, (len(items) + bs - 1) // bs):.4f} ({time.time() - t0:.0f}s)", flush=True)
    model.eval()
    return tok, model


def predict(tok, model, pages, device):
    import torch
    for p in pages:
        labels = ["O"] * len(p["words"])
        for a, b in chunks(p):
            if b <= a:
                continue
            enc = encode(tok, p["words"][a:b])
            with torch.no_grad():
                logits = model(**{k: torch.tensor([v]).to(device) for k, v in enc.items()}).logits[0]
            pred = logits.argmax(-1).tolist()
            prev = None
            for ti, wid in enumerate(enc.word_ids()):
                if wid is not None and wid != prev:
                    labels[a + wid] = LABELS[pred[ti]]
                prev = wid
        p["pred"] = spans_from_labels(p, labels)


def spans_from_labels(p, labels):
    out, cur = [], None
    for i, lab in enumerate(labels):
        typ = lab[2:] if lab != "O" else None
        if lab.startswith("B-") or (lab.startswith("I-") and (cur is None or cur["type"] != typ)):
            if cur:
                out.append(cur)
            cur = {"type": typ, "start": p["offs"][i][0], "end": p["offs"][i][1]}
        elif lab.startswith("I-") and cur:
            cur["end"] = p["offs"][i][1]
        else:
            if cur:
                out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    for s in out:
        s["text"] = p["text"][s["start"]:s["end"]]
    return out


# ── linking ──────────────────────────────────────────────────────────────────

def key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ß", "ss").replace("v", "u").replace("y", "i").replace("w", "u")
    s = re.sub(r"(.)\1+", r"\1", s)
    return " ".join(re.findall(r"[a-z0-9]+", s))


def register_names() -> dict[str, list[tuple[str, str]]]:
    """kind -> [(normalised name, ref)] from the registers, all spellings."""
    from lxml import etree
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    T = "{http://www.tei-c.org/ns/1.0}"
    for path, kinds in ((ROOT / "person" / "person.xml", {"person": "per", "org": "org"}),
                        (ROOT / "place" / "place.xml", {"place": "place"})):
        root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
        for tag, kind in kinds.items():
            for el in root.iter(T + tag):
                ref = el.get("{http://www.w3.org/XML/1998/namespace}id")
                for n in el:
                    if isinstance(n.tag, str) and n.tag.endswith("Name") and n.get("type") != "sort":
                        name = re.sub(r"\s*\([^)]*\)\s*$", "", "".join(n.itertext()))
                        if name.strip():
                            out[kind].append((key(name), ref))
    return out


class Linker:
    """A tagged name -> register entry, by the rules of docs/NER.md.

    Known spellings come from the training letters; register spellings are the
    fallback. Persons only within ±WINDOW years of an attestation in the
    training letters, because their names recur across generations."""

    def __init__(self, train_pages, registers):
        self.surface: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.years: dict[str, list[int]] = defaultdict(list)
        for p in train_pages:
            for g in p["gold"]:
                c = coarse(g["type"])
                if c != "date" and g.get("ref"):
                    for ref in g["ref"].split():
                        self.surface[(c, key(g["text"]))][ref] += 1
                        if p.get("year"):
                            self.years[ref].append(p["year"])
        self.by_kind: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for (kind, k), refs in self.surface.items():
            self.by_kind[kind].append((k, refs.most_common(1)[0][0]))
        for kind, names in registers.items():
            self.by_kind[kind].extend(names)

    def near(self, ref: str, year: int | None) -> bool:
        return year is None or any(abs(y - year) <= WINDOW for y in self.years.get(ref, []))

    def link(self, label: str, text: str, year: int | None = None):
        """(ref, score, how): how is 'seen' (a spelling the training letters
        link) or 'fuzzy'; (None, None, None) when nothing qualifies."""
        typ = coarse(label)
        if typ == "date":
            return None, None, None
        k = key(text)
        if not k:
            return None, None, None
        hit = self.surface.get((typ, k))
        if hit:
            refs = [(r, n) for r, n in hit.most_common() if typ != "per" or self.near(r, year)]
            if refs:
                return refs[0][0], 100, "seen"
        cands = [(n, r) for n, r in self.by_kind.get(typ, []) if typ != "per" or self.near(r, year)]
        if not cands:
            return None, None, None
        best = process.extractOne(k, [n for n, _ in cands], scorer=fuzz.WRatio, score_cutoff=CUTOFF)
        if best:
            return cands[best[2]][1], round(best[1]), "fuzzy"
        return None, None, None


# ── evaluation ───────────────────────────────────────────────────────────────

def evaluate(pages, fine_types: bool = False) -> dict:
    """Two views. ``projected``: exact span match against the edition's tags as
    carried onto this reading (what the model was asked to learn). ``edition``:
    each tagged name matched to an edition name of the same type on the same page
    by spelling similarity — the comparison with the edition itself, whose text
    differs from the reading's. Types are the edition's coarse kinds (per, org,
    place, date), or with ``fine_types`` the ten labels with roles. Linking:
    precision of every written @ref by type and kind of match, and how many of
    the edition's linked names got the right one."""
    tmap = (lambda t: t) if fine_types else coarse
    kinds = TYPES if fine_types else ["per", "org", "place", "date"]
    res = {}
    for view in ("projected", "edition"):
        per = {t: Counter() for t in kinds}
        links = Counter()
        for p in pages:
            if not p["has_edition"]:
                continue
            pred = [{**x, "t": tmap(x["type"])} for x in p["pred"]]
            golds_all = [{**g, "t": tmap(g["type"])} for g in (p["projected"] if view == "projected" else p["gold"])]
            if view == "projected":
                gold = {(g["t"], g["start"], g["end"]) for g in golds_all}
                got = {(x["t"], x["start"], x["end"]) for x in pred}
                for t in kinds:
                    g = {x for x in gold if x[0] == t}; q = {x for x in got if x[0] == t}
                    per[t]["tp"] += len(g & q); per[t]["fp"] += len(q - g); per[t]["fn"] += len(g - q)
                continue
            used = set()
            for t in kinds:
                golds = [(i, g) for i, g in enumerate(golds_all) if g["t"] == t]
                for x in (x for x in pred if x["t"] == t):
                    best, bi = 0, None
                    for i, g in golds:
                        if i in used:
                            continue
                        sc = fuzz.ratio(key(g["text"]), key(x["text"]))
                        if sc > best:
                            best, bi = sc, i
                    if bi is not None and best >= 75:
                        used.add(bi); per[t]["tp"] += 1
                        g = golds_all[bi]
                        c = coarse(x["type"])
                        if c != "date" and g.get("ref"):
                            links["gold_linked"] += 1
                            links["linked"] += bool(x.get("ref"))
                            links["correct"] += bool(x.get("ref")) and x["ref"] in g["ref"].split()
                            if x.get("ref"):
                                links[f"{c}/{x.get('link_how')}/written"] += 1
                                links[f"{c}/{x.get('link_how')}/right"] += x["ref"] in g["ref"].split()
                    else:
                        per[t]["fp"] += 1
                per[t]["fn"] += sum(1 for i, _ in golds if i not in used)
        out = {}
        for t, c in list(per.items()) + [("all", sum(per.values(), Counter()))]:
            pr = c["tp"] / max(1, c["tp"] + c["fp"]); rc = c["tp"] / max(1, c["tp"] + c["fn"])
            out[t] = {"precision": round(pr, 4), "recall": round(rc, 4),
                      "f1": round(2 * pr * rc / max(1e-9, pr + rc), 4), **c}
        if view == "edition" and not fine_types:
            prec = {}
            for k in [k for k in links if k.endswith("/written")]:
                base = k[: -len("/written")]
                prec[base] = {"written": links[k], "right": links[base + "/right"],
                              "precision": round(links[base + "/right"] / max(1, links[k]), 4)}
            out["linking"] = {"gold_linked": links["gold_linked"], "linked": links["linked"],
                              "correct": links["correct"],
                              "recall": round(links["correct"] / max(1, links["gold_linked"]), 4),
                              "precision_by_kind": prec}
        res[view] = out
    return res


# ── dates ────────────────────────────────────────────────────────────────────

MONTHS = {"jan": 1, "feb": 2, "mer": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "mey": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "octo": 10, "nov": 11, "dez": 12,
          "dec": 12, "wint": 11, "christ": 12, "hornung": 2, "brach": 6, "heu": 7, "herbst": 9}


def when_of(text: str, expected: float | None) -> tuple[str | None, str]:
    """A date line -> (@when, @cert). Day and month only when the line gives the
    day in figures and the month by name; a feast-day dating stays at the year."""
    year, how = year_from_text(text, expected)
    if year is None:
        return None, ""
    cert = "medium" if how == "century" else ""
    m = re.search(r"(?<!\d)(\d{1,2})\s*(?:\.|ten|tn|t|en|n)?\s*(?:tag\s+)?(?:des\s+|deß\s+|monats\s+)?([A-Za-zäöü]+)", text)
    if m:
        word = m.group(2).lower()
        mon = next((v for k, v in MONTHS.items() if word.startswith(k)), None)
        day = int(m.group(1))
        if mon and 1 <= day <= 31:
            return f"{year:04d}-{mon:02d}-{day:02d}", cert
    return f"{year:04d}", cert


def date_eval(pages) -> dict:
    """origDate/@when as computed, against the edition's, on the edited pages."""
    c = Counter()
    for p in pages:
        if not p["has_edition"]:
            continue
        for g in p["gold"]:
            if g["type"] != "origDate" or not g.get("ref"):
                continue
            when, _ = when_of(g["text"], p.get("expected"))
            c["n"] += 1
            c["year_right"] += bool(when) and when[:4] == g["ref"][:4]
            c["full_given"] += bool(when) and len(when) == 10
            c["full_right"] += bool(when) and len(when) == 10 and when == g["ref"][:10]
    return dict(c)


# ── main ─────────────────────────────────────────────────────────────────────

def apply_links(pages, linker, allowed: set[str] | None) -> None:
    """Link every tagged name, write only the kinds of link the measurement
    allows (``allowed`` = {"org/seen", ...}; None = all), give origDate its @when."""
    for p in pages:
        for x in p["pred"]:
            ref, score, how = linker.link(x["type"], x["text"], p.get("year"))
            if ref and allowed is not None and f"{coarse(x['type'])}/{how}" not in allowed:
                ref = None
            x["ref"], x["link_score"], x["link_how"] = (ref, score, how) if ref else (None, score, how)
            if x["type"] == "origDate":
                x["when"], x["date_cert"] = when_of(x["text"], p.get("year") or p.get("expected"))


def entity_out(x: dict) -> dict:
    el, _, role = x["type"].partition("@")
    out = {"type": coarse(x["type"]), "el": el, "start": x["start"], "end": x["end"], "text": x["text"]}
    if role:
        out["role"] = role
    if x.get("ref"):
        out["ref"] = x["ref"]
        out["link_score"] = x["link_score"]
        out["link_how"] = x["link_how"]
        if x["link_how"] == "fuzzy":
            out["cert"] = "medium"
    if x.get("when"):
        out["when"] = x["when"]
        if x.get("date_cert"):
            out["when_cert"] = x["date_cert"]
    return out


def write_unlinked(pages, path: Path) -> int:
    """Names tagged in unedited letters without a register entry: candidates for
    new entries, grouped by type and normalised spelling."""
    groups: dict[tuple[str, str], dict] = {}
    for p in pages:
        if not p.get("unedited"):
            continue
        for x in p["pred"]:
            c = coarse(x["type"])
            if c == "date" or x.get("ref"):
                continue
            g = groups.setdefault((c, key(x["text"])), {"forms": Counter(), "letters": set()})
            g["forms"][" ".join(x["text"].split())] += 1
            g["letters"].add(p["letter"])
    rows = sorted(groups.items(), key=lambda kv: (-sum(kv[1]["forms"].values()), kv[0]))
    with path.open("w", encoding="utf-8") as fh:
        fh.write("type\tkey\tcount\tletters\tspellings\tletter_ids\n")
        for (c, k), g in rows:
            fh.write(f"{c}\t{k}\t{sum(g['forms'].values())}\t{len(g['letters'])}\t"
                     f"{' | '.join(f for f, _ in g['forms'].most_common(5))}\t{' '.join(sorted(g['letters'])[:20])}\n")
    return len(rows)


def save_entities(pages, reader: str, suffix: str = "") -> Path:
    out_dir = ROOT / "entities"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{reader}{suffix}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for p in sorted(pages, key=lambda p: (p["letter"], p["facs"])):
            if "pred" in p:
                fh.write(json.dumps({"key": p["facs"], "auto": True, "unedited": bool(p.get("unedited")),
                                     "entities": [entity_out(x) for x in p["pred"]]}, ensure_ascii=False) + "\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "tag", "prepare"],
                    help="run: cross-validate, then train the final model and tag the unedited pages; "
                         "tag: tag unedited pages with the saved final model (new readings)")
    ap.add_argument("--reader", default="gemini-4-argon")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--only-folds", type=int, default=None, help="train only this many folds (smoke)")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    pages, stats = build_pages(args.reader, args.limit, unedited=True)
    edited = [p for p in pages if not p.get("unedited")]
    unedited = [p for p in pages if p.get("unedited")]
    print(f"{len(edited)} edited pages read by {args.reader} (edition tags {stats['gold']}, "
          f"projected {stats['projected']}, untrusted {stats['unsure']}); {len(unedited)} unedited pages", flush=True)
    if args.cmd == "prepare":
        print(Counter(lab[2:] for p in edited for lab in p["labels"] if lab and lab.startswith("B-")))
        return

    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    registers = register_names()
    smoke = args.limit is not None or args.only_folds is not None
    suffix = ".smoke" if smoke else ""
    report_path = ROOT / "entities" / f"{args.reader}{suffix}.report.json"
    model_dir = MODEL_DIR / f"ner-{args.reader}{suffix}"
    t_start = time.time()

    if args.cmd == "tag":
        from transformers import AutoModelForTokenClassification, AutoTokenizer
        report = json.loads(report_path.read_text(encoding="utf-8"))
        allowed = set(report["policy"]["allowed"])
        tok = AutoTokenizer.from_pretrained(model_dir)
        model = AutoModelForTokenClassification.from_pretrained(model_dir).to(device).eval()
        old = {}
        path = ROOT / "entities" / f"{args.reader}{suffix}.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                old[r["key"]] = r
        todo = [p for p in unedited if p["facs"] not in old]
        print(f"tag: {len(todo)} unedited pages not yet tagged", flush=True)
        predict(tok, model, todo, device)
        apply_links(todo, Linker(edited, registers), allowed)
        with path.open("a", encoding="utf-8") as fh:
            for p in todo:
                fh.write(json.dumps({"key": p["facs"], "auto": True, "unedited": True,
                                     "entities": [entity_out(x) for x in p["pred"]]}, ensure_ascii=False) + "\n")
        for p in unedited:  # the pages tagged before, for the list of unlinked names
            if p["facs"] in old:
                p["pred"] = [{**x, "type": x["el"] + ("@" + x["role"] if x.get("role") else "")}
                             for x in old[p["facs"]]["entities"]]
        n = write_unlinked([p for p in unedited if "pred" in p], ROOT / "entities" / f"unlinked_{args.reader}{suffix}.tsv")
        print(f"appended {len(todo)} pages to {path.relative_to(ROOT)}; {n} unlinked name groups", flush=True)
        return

    # 1. cross-validation on the edited pages
    done = []
    for f in range(args.folds if args.only_folds is None else args.only_folds):
        test = [p for p in edited if fold_of(p["letter"], args.folds) == f]
        train = [p for p in edited if fold_of(p["letter"], args.folds) != f]
        print(f"fold {f + 1}/{args.folds}: train {len(train)} pages, test {len(test)}", flush=True)
        tok, model = train_fold(train, args.epochs, device, seed=f)
        predict(tok, model, test, device)
        apply_links(test, Linker(train, registers), None)
        print("   ", json.dumps(evaluate(test)["edition"]["all"]), flush=True)
        done.extend(test)
        del model

    report = {"coarse": evaluate(done), "fine": evaluate(done, fine_types=True)}
    late = [p for p in done if (p.get("nr") or 0) >= 1000]
    report["late_letters"] = {"pages": len(late), **evaluate(late)}
    report["dates"] = date_eval(done)
    prec = report["coarse"]["edition"]["linking"]["precision_by_kind"]
    allowed = sorted(k for k, v in prec.items() if v["precision"] >= MIN_PRECISION and v["written"] >= 20)
    report["policy"] = {"window_years": WINDOW, "fuzzy_cutoff": CUTOFF, "min_precision": MIN_PRECISION,
                        "allowed": allowed, "measured": prec}
    # the written tags follow the policy too
    for p in done:
        for x in p["pred"]:
            if x.get("ref") and f"{coarse(x['type'])}/{x['link_how']}" not in allowed:
                x["ref"] = None
    report.update(reader=args.reader, base_model=BASE, folds=args.folds, epochs=args.epochs,
                  pages=len(done), projection=dict(stats), labels=TYPES)
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"coarse": report["coarse"]["edition"], "late": report["late_letters"]["edition"]["all"],
                      "dates": report["dates"], "allowed": allowed}, indent=1), flush=True)

    # 2. final model on every edited page; 3. tag the unedited pages
    print(f"final model on {len(edited)} pages ({(time.time() - t_start) / 60:.0f} min so far)", flush=True)
    tok, model = train_fold(edited, args.epochs, device, seed=99)
    model_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(model_dir)
    tok.save_pretrained(model_dir)
    predict(tok, model, unedited, device)
    apply_links(unedited, Linker(edited, registers), set(allowed))
    path = save_entities(done + unedited, args.reader, suffix)
    n = write_unlinked(unedited, ROOT / "entities" / f"unlinked_{args.reader}{suffix}.tsv")
    tagged = sum(len(p["pred"]) for p in unedited)
    linked = sum(1 for p in unedited for x in p["pred"] if x.get("ref"))
    print(f"wrote {path.relative_to(ROOT)}: {len(done)} edited pages (cross-validated), {len(unedited)} unedited pages "
          f"({tagged} names, {linked} linked); {n} unlinked name groups; model in {model_dir.relative_to(ROOT)}; "
          f"{(time.time() - t_start) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
