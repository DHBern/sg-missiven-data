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
    """The edition's person, organisation and place registers. Places keep their
    coordinates (``geo`` = [lat, lon], absent where the register has none), their
    GeoNames feature class and country; everyone keeps the register's own
    description and authority links."""
    t = f"{{{NS['t']}}}"
    reg: dict[str, dict] = {}
    for path, kinds in ((ROOT / "person" / "person.xml", ("person", "org", "personGrp")),
                        (ROOT / "place" / "place.xml", ("place",))):
        root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
        for kind in kinds:
            for el in root.iter(t + kind):
                xid = el.get("{http://www.w3.org/XML/1998/namespace}id")
                if not xid:
                    continue
                names = {n.get("type"): norm("".join(n.itertext()))
                         for n in el if local(n) in ("persName", "orgName", "placeName")}
                links = [{"type": p.get("type"), "url": p.get("target")}
                         for p in el.iter(t + "ptr") if p.get("target")]
                entry = {"kind": "org" if kind == "personGrp" else kind,
                         "name": names.get("full") or names.get("main") or xid,
                         "sort": names.get("sort") or (names.get("main") or xid).lower(),
                         "links": links}
                desc = el.find(f"{t}note[@type='description']")
                if desc is not None and norm("".join(desc.itertext())):
                    entry["desc"] = norm("".join(desc.itertext()))
                if kind == "place":
                    entry["ftype"] = el.get("type") or ""
                    geo = el.find(f"{t}location/{t}geo")
                    if geo is not None and geo.text and len(geo.text.split()) == 2:
                        lat, lon = (float(x) for x in geo.text.split())
                        entry["geo"] = [round(lat, 5), round(lon, 5)]
                    country = el.find(f"{t}location/{t}country[@{{http://www.w3.org/XML/1998/namespace}}lang='de']")
                    if country is not None and country.text:
                        entry["country"] = norm(country.text)
                reg[xid] = entry
    return reg


def occurrences(letters: list[dict], reg: dict[str, dict]) -> None:
    """Add to every register entry the letters that name it: in the text (with the
    spelling there) or as sender, recipient or place of writing in the header.
    ``occ`` = [[letter id, form, role], ...]; role is "" for the text, "sender",
    "recipient" or "place" (place of writing / receipt) for the header."""
    seen: set[tuple] = set()
    for letter in letters:
        if not letter.get("edited"):
            continue
        hits = []
        for role, acts in (("sender", letter["sender"]), ("recipient", letter["recipient"])):
            for a in acts:
                hits.append((a["ref"], a["text"], "place" if a["type"] == "place" else role))
        for page in letter["pages"]:
            for e in page["entities"]:
                if e["type"] in ("per", "org", "place"):
                    hits.append((e["ref"], e["text"], ""))
        for ref, form, role in hits:
            for r in (ref or "").split():
                r = r.lstrip("#")
                if r not in reg or (r, letter["id"], form, role) in seen:
                    continue
                seen.add((r, letter["id"], form, role))
                reg[r].setdefault("occ", []).append([letter["id"], form, role])


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


def load_drafts() -> dict[str, dict]:
    """Regests drafted by a language model from a machine reading (scripts/regest.py).
    Shown only as drafts, never in place of the edition's regest."""
    out: dict[str, dict] = {}
    for path in sorted((ROOT / "regests").glob("*.jsonl")) if (ROOT / "regests").exists() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            d = r.get("draft") or {}
            if d.get("error") or not d.get("regest"):
                continue
            out[r["id"]] = {k: d.get(k) for k in ("sender", "recipient", "place", "date_text", "year", "year_how", "date", "regest")} | {
                "reader": r.get("reader"), "model": r.get("model")}
    return out


def write_missing_geo(reg: dict[str, dict]) -> None:
    """docs/places_without_coordinates.md: the register's places the map cannot show."""
    rows = sorted((e for e in reg.values() if e["kind"] == "place" and "geo" not in e),
                  key=lambda e: (-len({o[0] for o in e.get("occ", [])}), e["sort"]))
    ids = {id(e): k for k, e in reg.items()}
    lines = ["# Places without coordinates", "",
             f"{len(rows)} of {sum(1 for e in reg.values() if e['kind'] == 'place')} places in "
             "`place/place.xml` have no `<geo>`, so the viewer's map leaves them out. "
             "Sorted by the number of edited letters that name them. Written by "
             "`scripts/build_site.py`; do not edit by hand.", "",
             "Type is the GeoNames feature class (A region, H water, L area, P settlement, "
             "S building). *Coordinates from* names the authority links that carry "
             "coordinates of their own, so a geocoder can take them from there.", "",
             "| Register id | Name | Type | Country | Letters | Description | Coordinates from | Links |",
             "|---|---|---|---|---:|---|---|---|"]
    for e in rows:
        letters_n = len({o[0] for o in e.get("occ", [])})
        links = " ".join(f"[{lk['type']}]({lk['url']})" for lk in e["links"])
        src = ", ".join(sorted({lk["type"] for lk in e["links"]} & {"geonames", "wikidata", "ssrq"})) or "—"
        cells = [f"`{ids[id(e)]}`", e["name"], e.get("ftype", ""), e.get("country", ""),
                 str(letters_n), e.get("desc", ""), src, links]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    (ROOT / "docs" / "places_without_coordinates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    letters = read_edition(ROOT / "data")
    for letter in letters:
        letter["edited"] = True
    readings = load_readings()
    letters += unedited_letters(letters, readings)
    entities = load_entities()
    drafts = load_drafts()
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
        if letter["id"] in drafts:
            letter["auto_regest"] = drafts[letter["id"]]
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
        d = drafts.get(letter["id"])
        if d and not letter["edited"]:
            index[-1].update(sender=[d.get("sender") or ""], recipient=[d.get("recipient") or ""],
                             regest=d.get("regest") or "", draft=True,
                             year=d.get("year"), date_text=d.get("date_text"))

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
    reg = registers()
    occurrences(letters, reg)
    (OUT / "registers.json").write_text(json.dumps(reg, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_missing_geo(reg)
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
