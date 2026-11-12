"""Build the readings viewer's data from the TEI edition and the machine readings.

    python scripts/build_site.py            # writes site/data/

Inputs
  data/*.xml                    the edition (TEI)
  person/ place/                the registers
  readings/<reader>.jsonl       one line per page: {"key": <facs>, "text": ...}
  readings/readers.json         who each reader is (label, kind, caveat)
  entities/<reader>.jsonl       automatic tagging of a reading (optional)

Outputs (site/data/)
  index.json                    letters with date, sender, recipient, pages
  readers.json                  readers with corpus scores against the edition
  registers.json                register id -> name and links
  letters/<id>.json             one letter: header, pages, every reading

The score is a character error rate against the edition text of the same page:
Σ edits / Σ edition characters, whitespace collapsed to single spaces. It is a
distance to *this edition's* conventions, not a verdict on palaeography: an
edition that writes "uo" for u with o above makes "ů" an error.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from lxml import etree
from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from tei_pages import NS, local, norm, read_edition  # noqa: E402

OUT = ROOT / "site" / "data"


def flat(text: str) -> str:
    return " ".join(text.split())


def clean_reading(text: str) -> str:
    """Drop the empty lines a line-based engine leaves for every segment it
    could not read (colour chart, ruler, margins); keep every other line."""
    return "\n".join(line.rstrip() for line in (text or "").splitlines() if line.strip())


def load_readings() -> dict[str, dict[str, dict]]:
    readings: dict[str, dict[str, dict]] = {}
    for path in sorted((ROOT / "readings").glob("*.jsonl")):
        reader = path.stem
        pages: dict[str, dict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            pages[rec["key"]] = {"text": clean_reading(rec.get("text", "")),
                                 "truncated": bool(rec.get("truncated"))}
            if rec.get("source"):
                pages[rec["key"]]["source"] = rec["source"]
        readings[reader] = pages
    return readings


def load_entities() -> dict[str, dict[str, list]]:
    out: dict[str, dict[str, list]] = {}
    for path in sorted((ROOT / "entities").glob("*.jsonl")) if (ROOT / "entities").exists() else []:
        pages = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                pages[rec["key"]] = rec["entities"]
        out[path.stem] = pages
    return out


def registers() -> dict[str, dict]:
    reg: dict[str, dict] = {}
    for path, kinds in ((ROOT / "person" / "person.xml", ("person", "org")),
                        (ROOT / "place" / "place.xml", ("place",))):
        root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
        for kind in kinds:
            for el in root.iter(f"{{{NS['t']}}}{kind}"):
                xid = el.get("{http://www.w3.org/XML/1998/namespace}id")
                if not xid:
                    continue
                names = {n.get("type"): norm("".join(n.itertext()))
                         for n in el if local(n) in ("persName", "orgName", "placeName")}
                links = [{"type": p.get("type"), "url": p.get("target")}
                         for p in el.iter(f"{{{NS['t']}}}ptr") if p.get("target")]
                reg[xid] = {"kind": kind, "name": names.get("full") or names.get("main") or xid,
                            "links": links}
    return reg


FACS = re.compile(r"^StadtASG_Missive_(\d+)_(\d+)", re.I)


def unedited_letters(edited: list[dict], readings: dict[str, dict[str, dict]]) -> list[dict]:
    """Pages that some reader has read but the edition does not contain, grouped
    into letters by the letter number in the image name. They have no edited
    text, so they are read but never scored."""
    known = {p["facs"].lower() for letter in edited for p in letter["pages"]}
    known_stems = {Path(f).stem for f in known}
    by_letter: dict[int, dict[str, int]] = {}
    for pages in readings.values():
        for facs in pages:
            if facs.lower() in known or Path(facs.lower()).stem in known_stems:
                continue
            m = FACS.match(facs)
            if not m:
                continue
            by_letter.setdefault(int(m.group(1)), {})[facs] = int(m.group(2))
    out = []
    for nr, pages in sorted(by_letter.items()):
        out.append({
            "id": f"stasg_missiv_unedited_{nr:05d}", "edited": False,
            "title": f"StadtASG Missive, Nr. {nr}", "idno": f"StadtASG Missive_{nr}",
            "date": "", "sender": [], "recipient": [], "regest": "", "keywords": [],
            "pages": [{"facs": f, "n": str(i + 1), "html": "", "text": "", "entities": []}
                      for i, f in enumerate(sorted(pages, key=pages.get))],
        })
    return out


def main() -> None:
    letters = read_edition(ROOT / "data")
    for letter in letters:
        letter["edited"] = True
    readings = load_readings()
    letters += unedited_letters(letters, readings)
    entities = load_entities()
    readers = json.loads((ROOT / "readings" / "readers.json").read_text(encoding="utf-8"))
    (OUT / "letters").mkdir(parents=True, exist_ok=True)

    totals = {r: {"edits": 0, "chars": 0, "pages": 0, "per_page": [], "read": 0} for r in readings}
    index = []
    for letter in letters:
        for page in letter["pages"]:
            ref = flat(page["text"])
            page["readings"] = {}
            for reader, pages in readings.items():
                rec = pages.get(page["facs"])
                if rec is None:
                    continue
                totals[reader]["read"] += 1
                entry = {"text": rec["text"]}
                if rec.get("truncated"):
                    entry["truncated"] = True
                if rec.get("source"):
                    entry["source"] = rec["source"]
                if ref:
                    edits = Levenshtein.distance(ref, flat(rec["text"]))
                    entry["cer"] = round(edits / len(ref), 4)
                    t = totals[reader]
                    t["edits"] += edits
                    t["chars"] += len(ref)
                    t["pages"] += 1
                    t["per_page"].append(edits / len(ref))
                page["readings"][reader] = entry
            for reader, pages in entities.items():
                if page["facs"] in pages:
                    page.setdefault("auto_entities", {})[reader] = pages[page["facs"]]
        (OUT / "letters" / f"{letter['id']}.json").write_text(
            json.dumps(letter, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index.append({
            "id": letter["id"], "title": letter["title"], "idno": letter["idno"],
            "date": letter["date"],
            "sender": [a["text"] for a in letter["sender"] if a["type"] != "place"],
            "recipient": [a["text"] for a in letter["recipient"] if a["type"] != "place"],
            "regest": letter["regest"], "pages": len(letter["pages"]),
            "edited": letter["edited"],
        })

    for reader in readers:
        t = totals.get(reader["id"])
        if not t or not t["chars"]:
            reader.update(pages_read=t["read"] if t else 0)
            continue
        reader.update(pages_read=t["read"], pages_scored=t["pages"],
                      cer=round(t["edits"] / t["chars"], 4),
                      median_page_cer=round(statistics.median(t["per_page"]), 4))
    readers = [r for r in readers if r["id"] == "edition" or r.get("pages_read")]

    def nr(e):
        m = re.search(r"Nr\.\s*(\d+)", e["title"])
        return (int(m.group(1)) if m else 1 << 30, not e["edited"], e["id"])
    index.sort(key=nr)
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "readers.json").write_text(json.dumps(readers, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "registers.json").write_text(json.dumps(registers(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pages = sum(len(letter["pages"]) for letter in letters)
    unedited = [letter for letter in letters if not letter["edited"]]
    print(f"{len(letters)} letters, {pages} pages; not yet edited: {len(unedited)} letters, "
          f"{sum(len(letter['pages']) for letter in unedited)} pages")
    for r in readers:
        if r.get("cer") is not None:
            print(f"  {r['id']:<48} read {r['pages_read']:>5}  scored {r['pages_scored']:>5}"
                  f"  CER {r['cer']:.4f}  median {r['median_page_cer']:.4f}")


if __name__ == "__main__":
    main()
