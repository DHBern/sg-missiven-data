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
from tei_pages import read_edition  # noqa: E402

TYPES = ["per", "org", "place", "date"]
LABELS = ["O"] + [f"{p}-{t}" for t in TYPES for p in ("B", "I")]
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


def build_pages(reader: str, limit: int | None):
    readings = {}
    for line in (ROOT / "readings" / f"{reader}.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        readings[r["key"]] = "\n".join(l for l in (r.get("text") or "").splitlines() if l.strip())
    pages = []
    stats = Counter()
    for letter in read_edition(ROOT / "data"):
        for p in letter["pages"]:
            text = readings.get(p["facs"])
            if not text:
                continue
            gold = outer_spans(p["entities"]) if p["text"] else []
            good, unsure = project(p["text"], p["entities"], text) if p["text"] else ([], [])
            stats["gold"] += len(gold)
            stats["projected"] += len(good)
            stats["unsure"] += len(unsure)
            words, offs, labels = words_and_labels(text, good, unsure)
            pages.append({"letter": letter["id"], "facs": p["facs"], "text": text,
                          "edition_text": p["text"], "gold": gold, "projected": good,
                          "words": words, "offs": offs, "labels": labels,
                          "has_edition": bool(p["text"])})
    if limit:
        random.Random(0).shuffle(pages)
        pages = pages[:limit]
    return pages, stats


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
    def __init__(self, train_pages, registers):
        self.surface: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for p in train_pages:
            for g in p["gold"]:
                if g["type"] != "date" and g.get("ref"):
                    self.surface[(g["type"], key(g["text"]))][g["ref"]] += 1
        self.by_kind: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for (kind, k), refs in self.surface.items():
            self.by_kind[kind].append((k, refs.most_common(1)[0][0]))
        for kind, names in registers.items():
            self.by_kind[kind].extend(names)
        self.choices = {kind: [n for n, _ in v] for kind, v in self.by_kind.items()}

    def link(self, typ: str, text: str):
        if typ == "date":
            return None, None
        k = key(text)
        if not k:
            return None, None
        hit = self.surface.get((typ, k))
        if hit:
            return hit.most_common(1)[0][0], 100
        choices = self.choices.get(typ) or []
        best = process.extractOne(k, choices, scorer=fuzz.WRatio, score_cutoff=88)
        if best:
            return self.by_kind[typ][best[2]][1], round(best[1])
        return None, None


# ── evaluation ───────────────────────────────────────────────────────────────

def evaluate(pages) -> dict:
    """Two views. ``projected``: exact span match against the edition's tags as
    carried onto this reading (what the model was asked to learn). ``edition``:
    each tagged name matched to an edition name of the same type on the same page
    by spelling similarity — the comparison with the edition itself, whose text
    differs from the reading's. Linking: of the names matched to an edition name
    that carries a register reference, how many got that reference."""
    res = {}
    for view in ("projected", "edition"):
        per = {t: Counter() for t in TYPES}
        links = Counter()
        for p in pages:
            if not p["has_edition"]:
                continue
            pred = p["pred"]
            if view == "projected":
                gold = {(g["type"], g["start"], g["end"]) for g in p["projected"]}
                got = {(s["type"], s["start"], s["end"]) for s in pred}
                for t in TYPES:
                    g = {x for x in gold if x[0] == t}; s = {x for x in got if x[0] == t}
                    per[t]["tp"] += len(g & s); per[t]["fp"] += len(s - g); per[t]["fn"] += len(g - s)
            else:
                used = set()
                for t in TYPES:
                    golds = [(i, g) for i, g in enumerate(p["gold"]) if g["type"] == t]
                    for s in (s for s in pred if s["type"] == t):
                        best, bi = 0, None
                        for i, g in golds:
                            if i in used:
                                continue
                            sc = fuzz.ratio(key(g["text"]), key(s["text"]))
                            if sc > best:
                                best, bi = sc, i
                        if bi is not None and best >= 75:
                            used.add(bi); per[t]["tp"] += 1
                            g = p["gold"][bi]
                            if t != "date" and g.get("ref"):
                                links["gold_linked"] += 1
                                links["linked"] += bool(s.get("ref"))
                                links["correct"] += s.get("ref") == g["ref"]
                        else:
                            per[t]["fp"] += 1
                    per[t]["fn"] += sum(1 for i, _ in golds if i not in used)
        out = {}
        for t, c in list(per.items()) + [("all", sum(per.values(), Counter()))]:
            pr = c["tp"] / max(1, c["tp"] + c["fp"]); rc = c["tp"] / max(1, c["tp"] + c["fn"])
            out[t] = {"precision": round(pr, 4), "recall": round(rc, 4),
                      "f1": round(2 * pr * rc / max(1e-9, pr + rc), 4), **c}
        if view == "edition":
            out["linking"] = {**links, "accuracy": round(links["correct"] / max(1, links["gold_linked"]), 4)}
        res[view] = out
    return res


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "prepare"])
    ap.add_argument("--reader", default="gemini-4-argon")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--only-folds", type=int, default=None, help="train only this many folds (smoke)")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    pages, stats = build_pages(args.reader, args.limit)
    print(f"{len(pages)} pages read by {args.reader}; edition tags {stats['gold']}, "
          f"projected {stats['projected']}, untrusted {stats['unsure']}", flush=True)
    if args.cmd == "prepare":
        return

    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    registers = register_names()
    done = []
    for f in range(args.folds if args.only_folds is None else args.only_folds):
        test = [p for p in pages if fold_of(p["letter"], args.folds) == f]
        train = [p for p in pages if fold_of(p["letter"], args.folds) != f]
        print(f"fold {f + 1}/{args.folds}: train {len(train)} pages, test {len(test)}", flush=True)
        tok, model = train_fold(train, args.epochs, device, seed=f)
        predict(tok, model, test, device)
        linker = Linker(train, registers)
        for p in test:
            for s in p["pred"]:
                s["ref"], s["link_score"] = linker.link(s["type"], s["text"])
        print("   ", json.dumps(evaluate(test)["edition"]["all"]), flush=True)
        done.extend(test)
        del model

    report = evaluate(done)
    report.update(reader=args.reader, base_model=BASE, folds=args.folds, epochs=args.epochs,
                  pages=len(done), projection=dict(stats))
    out_dir = ROOT / "entities"
    out_dir.mkdir(exist_ok=True)
    suffix = "" if args.limit is None and args.only_folds is None else ".smoke"
    (out_dir / f"{args.reader}{suffix}.report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    with (out_dir / f"{args.reader}{suffix}.jsonl").open("w", encoding="utf-8") as fh:
        for p in sorted(done, key=lambda p: (p["letter"], p["facs"])):
            fh.write(json.dumps({"key": p["facs"], "entities": [
                {k: s[k] for k in ("type", "start", "end", "text", "ref", "link_score") if s.get(k) is not None}
                for s in p["pred"]]}, ensure_ascii=False) + "\n")
    print(json.dumps({k: report[k] for k in ("projected", "edition")}, indent=1))


if __name__ == "__main__":
    main()
