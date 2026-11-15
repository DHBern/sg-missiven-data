"""Build the readings viewer's data from the TEI edition and the machine readings.

    python scripts/build_site.py            # writes site/data/

Inputs
  data/*.xml                    the edition (TEI)
  person/ place/                the registers
  readings/<reader>.jsonl       one line per page: {"key": <facs>, "text": ...}
  readings/readers.json         who each reader is (label, kind, caveat)
  readings/lines.jsonl          line boxes of the line readers (docs/LINES.md)
  entities/<reader>.jsonl       automatic tagging of a reading (optional)
  regests/en/*.jsonl            English machine translations of the regests (scripts/regest_en.py)
  keywords/draft_<reader>.jsonl automatic keywords for letters not yet edited (experimental)

Outputs (site/data/)
  index.json                    letters with date, sender, recipient, pages
  index_en.json                 letter id -> English regest (machine translation, scripts/regest_en.py)
  readers.json                  readers with corpus scores against the edition
  registers.json                register id -> name and links
  letters/<id>.json             one letter: header, pages, every reading
  tei/<id>.xml                  the letter's TEI (data/ for the edition, tei_unedited/ for the rest), for download

The score is a character error rate against the edition text of the same page:
Σ edits / Σ edition characters, whitespace collapsed to single spaces. It is a
distance to *this edition's* conventions, not a verdict on palaeography: an
edition that writes "uo" for u with o above makes "ů" an error.
"""

from __future__ import annotations

import hashlib
import shutil
import json
import shutil
import re
import statistics
import sys
from pathlib import Path

from lxml import etree
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from tei_pages import NS, local, norm, read_edition  # noqa: E402
import line_links  # noqa: E402

OUT = ROOT / "site" / "data"


def flat(text: str) -> str:
    return " ".join(text.split())


def clean_reading(text: str) -> str:
    """Drop the empty lines a line-based engine leaves for every segment it
    could not read (colour chart, ruler, margins); keep every other line."""
    return "\n".join(line.rstrip() for line in (text or "").splitlines() if line.strip())


def _cleaned_offsets(joined: str):
    """Offsets in a reading as joint_reading.py sees it (non-empty lines, joined)
    -> offsets in the same reading after clean_reading (lines right-trimmed)."""
    import bisect
    lines = joined.split("\n")
    starts, clean_starts, a, b = [], [], 0, 0
    for line in lines:
        starts.append(a); clean_starts.append(b)
        a += len(line) + 1; b += len(line.rstrip()) + 1

    def conv(o: int) -> int:
        i = max(0, bisect.bisect_right(starts, o) - 1)
        return clean_starts[i] + min(o - starts[i], len(lines[i].rstrip()))
    return conv


def uncertain_marks(joint_rec: dict, joint_raw: str, back_raw: str) -> list:
    """The joint reading's uncertain words (joint_reading.py), with their offsets
    moved into the cleaned texts of the joint reading and of its backbone:
    [backbone start, end, joint start, end, level 1|2, % agreement, alternatives]."""
    cj = _cleaned_offsets("\n".join(l for l in (joint_raw or "").splitlines() if l.strip()))
    cb = _cleaned_offsets("\n".join(l for l in (back_raw or "").splitlines() if l.strip()))
    return [[cb(m[0]), cb(m[1]), cj(m[2]), cj(m[3]), m[4], m[5], m[6]] for m in joint_rec.get("unc", [])]


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
                                 "truncated": bool(rec.get("truncated")),
                                 # a line reader's lines, empty ones included: line i is box i
                                 "lines_raw": rec.get("text", "")}
            if reader == "joint" and rec.get("unc"):
                pages[rec["key"]]["unc_rec"] = rec  # placed below, once the backbone is read
            elif reader == "gemini-4-argon":
                pages[rec["key"]]["raw"] = rec.get("text", "")
            if rec.get("source"):
                pages[rec["key"]]["source"] = rec["source"]
        readings[reader] = pages
    # one list per page, with offsets for both the joint reading and its backbone
    joint, back = readings.get("joint", {}), readings.get("gemini-4-argon", {})
    unc = {}
    for key, rec in joint.items():
        raw = rec.pop("unc_rec", None)
        if raw and key in back and raw.get("backbone") == "gemini-4-argon":
            unc[key] = uncertain_marks(raw, raw.get("text", ""), back[key].get("raw", ""))
    for rec in back.values():
        rec.pop("raw", None)
    readings["__unc__"] = unc
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


_WORD = re.compile(r"\w", re.U)


def project_entities(src: str, dst: str, tags: list[dict]) -> tuple[list[dict], int]:
    """Carry tags made on one reading over to another reading of the same page,
    by a character alignment of the two texts. The joint reading is the Gemini 4
    Argon reading with some words replaced (scripts/joint_reading.py), so a tag
    lands on the same place, where needed in the joint reading's spelling. The
    ends are moved to whole words; a tag whose text the joint reading lost
    (nothing left, or under 60 % alike) is dropped. Returns (tags, dropped)."""
    if src == dst:
        return [dict(t) for t in tags], 0
    m = [0] * (len(src) + 1)
    for op, i1, i2, j1, j2 in Levenshtein.opcodes(src, dst):
        for t in range(i1, i2 + 1):
            m[t] = j1 + (t - i1 if op == "equal" else min(t - i1, j2 - j1))
    out, dropped = [], 0
    for t in tags:
        if not (0 <= t["start"] < t["end"] <= len(src)):
            dropped += 1
            continue
        a, b = m[t["start"]], m[t["end"]]
        while a > 0 and _WORD.match(dst[a - 1]) and a < len(dst) and _WORD.match(dst[a]):
            a -= 1  # back to the start of the word
        while 0 < b < len(dst) and _WORD.match(dst[b - 1]) and _WORD.match(dst[b]):
            b += 1  # on to the end of the word
        while a < b and dst[a].isspace():
            a += 1
        while b > a and dst[b - 1].isspace():
            b -= 1
        new = dst[a:b]
        if not new or fuzz.ratio(new, t["text"]) < 60:
            dropped += 1
            continue
        out.append({**t, "start": a, "end": b, "text": new} | ({"from_text": t["text"]} if new != t["text"] else {}))
    return out, dropped


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


def add_authority_links(reg: dict[str, dict]) -> int:
    """Links DH Bern found for the registers (scripts/authority_links.py, issue #12).
    They join on identifiers the edition already gives and are kept apart from the
    edition's own: each carries ``added`` with its sources."""
    path = ROOT / "registers" / "authority_links.json"
    if not path.exists():
        return 0
    n = 0
    for rid, links in json.loads(path.read_text(encoding="utf-8")).get("links", {}).items():
        entry = reg.get(rid)
        if entry is None:
            continue
        have = {l["url"].rstrip("/") for l in entry["links"]}
        for l in links:
            if l["url"].rstrip("/") in have:
                continue
            entry["links"].append({"type": l["type"], "url": l["url"], "added": "+".join(l["source"])})
            n += 1
    return n


def fold(s: str) -> str:
    """A name's folded spelling, as scripts/ner.py ``key`` makes it."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ß", "ss").replace("v", "u").replace("y", "i").replace("w", "u")
    s = re.sub(r"(.)\1+", r"\1", s)
    return " ".join(re.findall(r"[a-z0-9]+", s))


def auto_register() -> tuple[dict, dict[tuple[str, str], str]]:
    """registers/auto_register.json and its entries by (type, folded spelling)."""
    path = ROOT / "registers" / "auto_register.json"
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    kind_type = {"person": "per", "org": "org", "place": "place"}
    return data, {(kind_type[e["kind"]], e["key"]): eid for eid, e in data["entries"].items()}


def mark_auto_refs(letter: dict, by_key: dict[tuple[str, str], str]) -> None:
    """Point the unlinked tags of a letter not yet edited to the automatic
    register entry of their spelling (``ref``, marked ``auto_reg``)."""
    if letter.get("edited") or not by_key:
        return
    for page in letter["pages"]:
        for ents in (page.get("auto_entities") or {}).values():
            for x in ents:
                if x.get("ref") or x.get("type") not in ("per", "org", "place"):
                    continue
                # a tag carried to the joint reading keeps the entry of the spelling it was made on
                eid = by_key.get((x["type"], fold(x.get("from_text") or x["text"])))
                if eid:
                    x["ref"], x["auto_reg"] = eid, 1


def add_auto_register(reg: dict[str, dict], data: dict) -> int:
    """Register entries generated automatically for names found only in the
    letters not yet edited (scripts/auto_register.py, issue #12). Each carries
    ``auto`` and its graded candidates (``cands``: label, description, grade,
    links); the tags of those letters that have the entry's spelling point to it,
    marked ``auto_reg``. Not part of the edition."""
    if not data:
        return 0
    for eid, e in data["entries"].items():
        cands = []
        for c in e.get("candidates", []):
            life = f"{c.get('birth') or '?'}–{c.get('death') or '?'}" if (c.get("birth") or c.get("death")) else ""
            cands.append({"label": c.get("label", ""), "desc": " · ".join(x for x in (life, c.get("desc", "")) if x),
                          "km": c.get("km"), "grade": c["grade"], "links": [{"type": l["type"], "url": l["url"]} for l in c.get("links", [])]}
                         | ({"src": "ssrq", "unc": 1} if c.get("source") == "ssrq" or c.get("uncertain") else {}))
        reg[eid] = {"kind": e["kind"], "name": e["name"], "sort": e["key"], "auto": True, "links": [],
                    "forms": e.get("forms", []), "cands": cands}
    reg["_auto"] = {"kind": "meta", "measured": data.get("measured"), "measured_ssrq": data.get("measured_ssrq"),
                    "date": data.get("date")}
    return len(data["entries"])


def occurrences(letters: list[dict], reg: dict[str, dict]) -> None:
    """Add to every register entry the letters that name it: in the text (with the
    spelling there) or as sender, recipient or place of writing in the header.
    ``occ`` = [[letter id, form, role], ...]; role is "" for the text, "sender",
    "recipient" or "place" (place of writing / receipt) for the header."""
    seen: set[tuple] = set()
    for letter in letters:
        if not letter.get("edited"):
            # the automatic tagging of letters not yet edited: kept apart (occ_auto)
            for page in letter["pages"]:
                for reader, ents in (page.get("auto_entities") or {}).items():
                    if reader == "joint":
                        continue  # the same tags as Gemini 4 Argon's, carried over: not counted twice
                    for e in ents:
                        r = e.get("ref")
                        role = "place" if e.get("el") == "origPlace" else e.get("role", "")
                        if r in reg and (r, letter["id"], e["text"], role, "auto") not in seen:
                            seen.add((r, letter["id"], e["text"], role, "auto"))
                            reg[r].setdefault("occ_auto", []).append([letter["id"], e["text"], role])
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
            out[r["id"]] = {k: d.get(k) for k in ("sender", "recipient", "place", "date_text", "year", "year_how", "year_note", "date", "regest")} | {
                "reader": r.get("reader"), "model": r.get("model")}
    return out


def _src_hash(item: dict) -> str:
    # the same hash as scripts/regest_en.py (which needs httpx, not installed for the build)
    return hashlib.sha1(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def load_english() -> dict[str, dict[str, dict]]:
    """Machine translations of the regests (scripts/regest_en.py), by kind
    ("edition", "draft_<reader>") and letter id. A translation made from a German
    text that has changed since is left out: better none than one of another text."""
    out: dict[str, dict[str, dict]] = {}
    for path in sorted((ROOT / "regests" / "en").glob("*.jsonl")) if (ROOT / "regests" / "en").exists() else []:
        rows = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("en") and (r["en"].get("regest") or "").strip():
                    rows[r["id"]] = r
        out[path.stem] = rows
    return out


def english_of(kind: str, lid: str, de: dict, en: dict[str, dict[str, dict]]) -> dict | None:
    r = en.get(kind, {}).get(lid)
    if r is None or r.get("src") != _src_hash(de):
        return None
    return {k: v for k, v in r["en"].items() if v} | {"model": r.get("model")}


def load_auto_keywords() -> dict[str, list[str]]:
    """Keywords a language model picked from the edition's vocabulary for the
    letters not yet edited (scripts/keywords.py). Experimental, shown as such."""
    out: dict[str, list[str]] = {}
    for path in sorted((ROOT / "keywords").glob("draft_*.jsonl")) if (ROOT / "keywords").exists() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("keywords"):
                    out[r["id"]] = r["keywords"]
    return out


def write_missing_geo(reg: dict[str, dict]) -> None:
    """docs/places_without_coordinates.md: the register's places the map cannot show
    (the edition's register only, not the entries generated automatically)."""
    reg = {k: e for k, e in reg.items() if isinstance(e, dict) and e.get("kind") and not e.get("auto")}
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


SEARCH_SHARD_CHARS = 1_500_000  # text per search shard: loaded in parallel, only by the search view


def write_search(letters: list[dict], index: list[dict], reg: dict, best_reader: str) -> None:
    """site/data/search/: meta.json (one row per letter, the facets) and
    text-N.json (the page texts, split so the browser loads them in parallel
    and only when someone searches the text).

    The text of a page is the edition's where the edition has one, else the
    best machine reading (``best_reader``) — so a search covers the whole
    series, and every hit says which of the two it comes from."""
    out = OUT / "search"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("text-*.json"):
        old.unlink()
    by_id = {e["id"]: e for e in index}
    meta, shards, cur, size = [], [], {}, 0
    for letter in letters:
        e = by_id[letter["id"]]
        year = e["date"][:4] if e.get("date", "")[:4].isdigit() else str(e.get("year") or "")
        places = [a["text"] for a in letter["sender"] if a["type"] == "place"]
        if not letter["edited"] and (letter.get("auto_regest") or {}).get("place"):
            places = [letter["auto_regest"]["place"]]
        pages, srcs = [], set()
        for i, page in enumerate(letter["pages"], 1):
            if page["text"]:
                pages.append([i, page["text"], "edition"]); srcs.add("edition")
            elif (page.get("readings") or {}).get(best_reader, {}).get("text"):
                pages.append([i, page["readings"][best_reader]["text"], best_reader]); srcs.add("machine")
        meta.append({"id": letter["id"], "y": int(year) if year.isdigit() else None,
                     "ed": letter["edited"], "s": e["sender"], "r": e["recipient"], "op": places,
                     "kw": letter.get("keywords", []), "kwa": letter.get("auto_keywords", []), "dr": bool(e.get("draft")),
                     "src": sorted(srcs), "sh": len(shards)})
        cur[letter["id"]] = pages
        size += sum(len(t) for _, t, _ in pages)
        if size >= SEARCH_SHARD_CHARS:
            shards.append(cur); cur, size = {}, 0
    if cur:
        shards.append(cur)
    for i, shard in enumerate(shards):
        (out / f"text-{i}.json").write_text(json.dumps(shard, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({"reader": best_reader, "shards": len(shards), "letters": meta},
                                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_lines() -> dict[str, dict]:
    path = ROOT / "readings" / "lines.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["key"]] = rec
    return out


def link_lines(page: dict, geo: dict, readings: dict[str, dict[str, dict]]) -> None:
    """Rows on the image for this page, and for every text which rows each line is on.

    ``page["rows"]`` holds per row its boxes, in ten-thousandths of the image
    width (the unit OpenSeadragon places overlays in); ``ln`` beside a text
    gives, per line, the rows it is linked to, an empty list where no link was
    good enough."""
    facs = page["facs"]
    readers = [m for m in geo["readers"] if facs in readings.get(m, {})]
    if not readers:
        return
    nb = len(geo["boxes"])
    texts = [["" for _ in readers] for _ in range(nb)]
    line_box = {}
    for mi, m in enumerate(readers):
        idx = geo["readers"][m]
        raw = readings[m][facs]["lines_raw"].split("\n")
        box = [(idx[i] if idx else i) if i < nb else -1 for i in range(len(raw))]
        for i, k in enumerate(box):
            if 0 <= k < nb:
                texts[k][mi] = raw[i]
        # the viewer shows the reading without its empty lines (clean_reading)
        line_box[m] = [k for line, k in zip(raw, box) if line.strip()]
    w, h = geo["size"]
    ar = h / w
    rows = line_links.rows(geo["boxes"], geo.get("baselines") or [], texts, ar)
    if not rows:
        return
    row_of = {k: j for j, r in enumerate(rows) for k in r["k"]}
    page["rows"] = [[[round(v * 1e4) for v in rect] for rect in r["rects"]] for r in rows]
    row_texts = [r["texts"] for r in rows]
    if page.get("text"):
        page["ln"] = line_links.align(page["text"].split("\n"), row_texts)
    for reader, entry in page["readings"].items():
        if reader in line_box:
            entry["ln"] = [[row_of[k]] if k in row_of else [] for k in line_box[reader]]
        elif entry.get("text"):
            entry["ln"] = line_links.align(entry["text"].split("\n"), row_texts)


def main() -> None:
    letters = read_edition(ROOT / "data")
    for letter in letters:
        letter["edited"] = True
    readings = load_readings()
    unc = readings.pop("__unc__", {})
    letters += unedited_letters(letters, readings)
    entities = load_entities()
    # the tags were made on the Gemini 4 Argon reading; the joint reading, built on it, gets them too
    if "gemini-4-argon" in entities and "joint" in readings:
        argon, joint_pages = readings.get("gemini-4-argon", {}), readings["joint"]
        carried, dropped, moved = {}, 0, 0
        for facs, tags in entities["gemini-4-argon"].items():
            if facs in argon and facs in joint_pages and tags:
                carried[facs], d = project_entities(argon[facs]["text"], joint_pages[facs]["text"], tags)
                dropped += d
                moved += sum(1 for t in carried[facs] if "from_text" in t)
        entities["joint"] = carried
        print(f"tags carried to the joint reading: {sum(map(len, carried.values()))} on {len(carried)} pages, "
              f"{moved} in the joint reading's spelling, {dropped} dropped", flush=True)
    drafts = load_drafts()
    english = load_english()
    index_en: dict[str, dict] = {}
    stale_en = 0
    auto_kw = load_auto_keywords()
    lines = load_lines()
    linked = {"pages": 0, "lines": 0, "of": 0}
    readers = json.loads((ROOT / "readings" / "readers.json").read_text(encoding="utf-8"))
    (OUT / "letters").mkdir(parents=True, exist_ok=True)

    totals = {r: {"edits": 0, "chars": 0, "pages": 0, "per_page": [], "read": 0} for r in readings}
    index = []
    # the fine-tuned readers trained on the edited pages with text, except the
    # held-out ones (readings/heldout_pages.txt): the viewer warns on those only
    heldout_file = ROOT / "readings" / "heldout_pages.txt"
    heldout = {l.strip() for l in heldout_file.read_text().splitlines() if l.strip()} if heldout_file.exists() else set()
    auto_data, auto_keys = auto_register()
    for letter in letters:
        for page in letter["pages"]:
            ref = flat(page["text"])
            if letter["edited"] and ref:
                page["fit"] = "heldout" if Path(page["facs"]).stem in heldout else "train"
            page["readings"] = {}
            if unc.get(page["facs"]):
                page["unc"] = unc[page["facs"]]
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
            if page["facs"] in lines:
                link_lines(page, lines[page["facs"]], readings)
                if page.get("rows"):
                    linked["pages"] += 1
                    for ln in [page.get("ln")] + [e.get("ln") for e in page["readings"].values()]:
                        if ln:
                            linked["lines"] += sum(1 for x in ln if x)
                            linked["of"] += len(ln)
            for reader, pages in entities.items():
                if page["facs"] in pages:
                    page.setdefault("auto_entities", {})[reader] = pages[page["facs"]]
        if letter["id"] in drafts:
            letter["auto_regest"] = drafts[letter["id"]]
        if letter["edited"] and letter["regest"].strip():
            en = english_of("edition", letter["id"], {"regest": letter["regest"].strip()}, english)
            if en:
                letter["regest_en"] = en
                index_en[letter["id"]] = {"regest": en["regest"]}
            elif letter["id"] in english.get("edition", {}):
                stale_en += 1
        d = drafts.get(letter["id"])
        if d:
            kind = f"draft_{d.get('reader')}"
            en = english_of(kind, letter["id"], {k: d.get(k) for k in ("regest", "sender", "recipient", "place")}, english)
            if en:
                letter["auto_regest"] = {**d, "en": en}
                if not letter["edited"]:
                    index_en[letter["id"]] = {"regest": en["regest"], "sender": [en.get("sender") or ""],
                                              "recipient": [en.get("recipient") or ""]}
            elif letter["id"] in english.get(kind, {}):
                stale_en += 1
        if not letter["edited"] and auto_kw.get(letter["id"]):
            letter["auto_keywords"] = auto_kw[letter["id"]]
        mark_auto_refs(letter, auto_keys)
        # the letter's TEI, copied so the viewer can offer it for download from its own origin
        for folder in ("data", "tei_unedited"):
            src = ROOT / folder / f"{letter['id']}.xml"
            if src.exists():
                (OUT / "tei").mkdir(exist_ok=True)
                shutil.copyfile(src, OUT / "tei" / src.name)
                letter["tei"] = f"{folder}/{src.name}"
                break
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
    # English regests in a file of their own, loaded only by the English interface
    (OUT / "index_en.json").write_text(json.dumps(index_en, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"English regests: {len(index_en)} in the index, {stale_en} left out (German changed since)", flush=True)
    (OUT / "readers.json").write_text(json.dumps(readers, ensure_ascii=False, indent=1), encoding="utf-8")
    reg = registers()
    added = add_authority_links(reg)
    if added:
        print(f"registers: {added} authority links added by DH Bern (registers/authority_links.json)")
    auto = add_auto_register(reg, auto_data)
    if auto:
        print(f"registers: {auto} entries generated automatically for names found only in letters not yet edited "
              "(registers/auto_register.json)")
    occurrences(letters, reg)
    (OUT / "registers.json").write_text(json.dumps(reg, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_missing_geo(reg)
    scored = [r for r in readers if r.get("cer") is not None and r["id"] != "joint"]
    best = min(scored, key=lambda r: r["cer"])["id"] if scored else "gemini-4-argon"
    write_search(letters, index, reg, best)
    pages = sum(len(letter["pages"]) for letter in letters)
    unedited = [letter for letter in letters if not letter["edited"]]
    print(f"{len(letters)} letters, {pages} pages; not yet edited: {len(unedited)} letters, "
          f"{sum(len(letter['pages']) for letter in unedited)} pages")
    if linked["pages"]:
        print(f"line links: {linked['pages']} pages with rows; {linked['lines']} of {linked['of']} text lines linked")
    for r in readers:
        if r.get("cer") is not None:
            print(f"  {r['id']:<48} read {r['pages_read']:>5}  scored {r['pages_scored']:>5}"
                  f"  CER {r['cer']:.4f}  median {r['median_page_cer']:.4f}")


if __name__ == "__main__":
    main()
