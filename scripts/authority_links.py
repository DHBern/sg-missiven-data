"""Authority links for the edition's registers (issue #12).

The edition links many register entries to authority files, but not all, and
not every entry to the files that matter most here (HLS, Wikidata, GND). This
script fills the gaps from identifiers the entries already have, and only from
them:

  * Metagrid's concordance (api.metagrid.ch/widget/<provider>/person/<id>),
    asked with the entry's SSRQ, histHub, GND or HLS id;
  * our Human Lineage server (MCP ``humanlineage.get_by_identifier``), asked
    with its Wikidata, GND or HLS id;
  * Wikidata (P902 HLS, P227 GND, P1566 GeoNames), asked with a QID, or for
    the QID with a GND or HLS id. This is the only route for places and
    organisations, which Metagrid and Human Lineage do not cover.

A name alone never makes a link. Name matches (``candidates``) go to a review
list and nowhere else.

    authority_links.py measure      # hide the HLS link of every person that has
                                    # one and see what each route finds
    authority_links.py lookup       # step 1 over all entries
                                    #   -> registers/authority_links.json
    authority_links.py candidates   # name matches for review
                                    #   -> registers/authority_candidates.tsv

Answers are cached under registers/.cache/ (not committed), so a second run
asks nothing twice.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_site import registers  # noqa: E402
from mcp_client import MCP  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "registers"
CACHE = OUT / ".cache"
UA = "sg-missiven-authority/1.0 (DH Bern research; register linking)"
TODAY = dt.date.today().isoformat()

# single-valued authorities: one id per entry, a second one is a conflict
SINGLE = ("hls", "gnd", "wikidata")

# --------------------------------------------------------------------------- ids

PATTERNS = [
    ("hls", re.compile(r"hls-dhs-dss\.ch/(?:(?:de|fr|it|rm)/)?articles/0*(\d+)")),
    ("gnd", re.compile(r"d-nb\.info/gnd/([0-9X-]+)", re.I)),
    ("wikidata", re.compile(r"wikidata\.org/(?:wiki|entity)/(Q\d+)")),
    ("ssrq", re.compile(r"ssrq-sds-fds\.ch/.*?[?&#]query=((?:per|org|loc)\d+)")),
    ("histhub", re.compile(r"data\.histhub\.ch/(?:person|place|organization)/(\d+)")),
    ("geonames", re.compile(r"geonames\.org/(\d+)")),
]


def ids_of(url: str) -> tuple[str, str] | None:
    for kind, rx in PATTERNS:
        m = rx.search(url or "")
        if m:
            v = m.group(1)
            return kind, (v.zfill(6) if kind == "hls" else v)
    return None


def url_of(kind: str, v: str) -> str:
    return {
        "hls": f"https://hls-dhs-dss.ch/de/articles/{v.zfill(6)}/",
        "gnd": f"https://d-nb.info/gnd/{v}",
        "wikidata": f"https://www.wikidata.org/wiki/{v}",
        "geonames": f"https://www.geonames.org/{v}",
    }[kind]


# link type as the edition's TEI names it, from the URL's host
HOST_TYPE = [
    ("hls-dhs-dss.ch", "hls"), ("d-nb.info", "gnd"), ("wikidata.org", "wikidata"), ("ssrq-sds-fds.ch", "ssrq"),
    ("histhub.ch", "histhub"), ("geonames.org", "geonames"), ("deutsche-biographie.de", "deutsche-biographie"),
    ("rag-online.org", "rag"), ("kalliope-verbund", "kalliope-verbund"), ("viaf.org", "viaf"),
    ("de.wikipedia.org", "wikipedia-de"), ("en.wikipedia.org", "wikipedia-en"), ("fr.wikipedia.org", "wikipedia-fr"),
    ("id.loc.gov", "lcnaf"), ("bnf.fr", "bnf"), ("idref.fr", "idref"), ("bsg.nb.admin.ch", "bsg"),
    ("helveticat", "helveticat"), ("ortsnamen.ch", "ortsnamen"), ("bullinger", "bullinger"), ("dodis.ch", "dodis"),
    ("hfls.ch", "hfls"), ("sikart", "sikart"), ("helvetiasacra", "helvetiasacra"), ("humanlineage.org", "humanlineage"),
]


def type_of(url: str, fallback: str) -> str:
    for host, t in HOST_TYPE:
        if host in url:
            return t
    return re.sub(r"[^a-z0-9]+", "-", fallback.lower()).strip("-") or "link"


# ------------------------------------------------------------------------ fetching

def cached(key: str, fetch):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if f.exists():
        return json.loads(f.read_text())["v"]
    v = fetch()
    f.write_text(json.dumps({"k": key, "v": v}, ensure_ascii=False))
    return v


def http_json(url: str, tries: int = 3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(3 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(3 * (i + 1))
                continue
            raise


MG_PROVIDER = {"ssrq": "ssrq", "histhub": "histhub", "gnd": "gnd", "hls": "hls-dhs-dss"}


def metagrid(kind: str, v: str) -> list[dict]:
    """Every resource Metagrid's concordance holds for one person id."""
    vid = str(int(v)) if kind == "hls" else v
    url = f"https://api.metagrid.ch/widget/{MG_PROVIDER[kind]}/person/{urllib.parse.quote(vid)}.json?lang=de&include=true"
    data = cached("mg:" + url, lambda: http_json(url))
    out = []
    for block in data or []:
        for label, rec in (block or {}).items():
            if isinstance(rec, dict) and rec.get("url"):
                out.append({"label": label, "url": rec["url"]})
    return out


_HL = None
# The server answers "bad parameter or other API misuse" to concurrent calls on
# one session (seen 13.11.2026), so its calls go one at a time.
_HL_LOCK = __import__("threading").Lock()


def humanlineage(kind: str, v: str) -> list[dict]:
    scheme = {"hls": "hls", "gnd": "gnd", "wikidata": "wikidata"}[kind]

    def ask():
        global _HL
        with _HL_LOCK:
            if _HL is None:
                _HL = MCP("humanlineage")
            r = _HL.call("get_by_identifier", scheme=scheme, value=v)
        return {"status": r.get("status"), "persons": [
            {k: p.get(k) for k in ("id", "name", "wikidata_id", "gnd_id", "hls_id", "url", "life_dates")}
            for p in r.get("persons", [])]}
    return [cached(f"hl:{scheme}:{v}", ask)]


SPARQL = "https://query.wikidata.org/sparql"


def wikidata_batch(values: list[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
    """For (kind, id) pairs: the Wikidata items and their HLS / GND / GeoNames ids.
    kind wikidata asks the item itself; gnd and hls ask which item carries the id."""
    out: dict[tuple[str, str], list[dict]] = {}
    todo = []
    for kv in values:
        f = CACHE / ("wd-" + hashlib.sha1(f"{kv}".encode()).hexdigest() + ".json")
        if f.exists():
            out[kv] = json.loads(f.read_text())
        else:
            todo.append(kv)
    prop = {"gnd": "P227", "hls": "P902"}
    for i in range(0, len(todo), 150):
        chunk = todo[i:i + 150]
        rows = []
        for kind, v in chunk:
            if kind == "wikidata":
                rows.append(f'("{kind}" "{v}" wd:{v})')
            else:
                rows.append(f'("{kind}" "{v}" UNDEF)')
        q = """SELECT ?kind ?v ?item ?hls ?gnd ?geo WHERE {
  VALUES (?kind ?v ?given) { %s }
  OPTIONAL { FILTER(?kind = "gnd") ?g wdt:P227 ?v . }
  OPTIONAL { FILTER(?kind = "hls") ?h wdt:P902 ?v . }
  BIND(COALESCE(?given, ?g, ?h) AS ?item)
  FILTER(BOUND(?item))
  OPTIONAL { ?item wdt:P902 ?hls . }
  OPTIONAL { ?item wdt:P227 ?gnd . }
  OPTIONAL { ?item wdt:P1566 ?geo . }
}""" % " ".join(rows)
        data = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(SPARQL, data=urllib.parse.urlencode({"query": q, "format": "json"}).encode(),
                                             headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read().decode())
                break
            except Exception:
                time.sleep(5 * (attempt + 1))
        if data is None:
            continue  # not cached: asked again next run
        got = defaultdict(list)
        for b in data["results"]["bindings"]:
            kv = (b["kind"]["value"], b["v"]["value"])
            got[kv].append({"qid": b["item"]["value"].rsplit("/", 1)[-1],
                            "hls": b.get("hls", {}).get("value"), "gnd": b.get("gnd", {}).get("value"),
                            "geonames": b.get("geo", {}).get("value")})
        for kv in chunk:
            out[kv] = got.get(kv, [])
            f = CACHE / ("wd-" + hashlib.sha1(f"{kv}".encode()).hexdigest() + ".json")
            f.write_text(json.dumps(out[kv]))
        time.sleep(1)
    return out


# --------------------------------------------------------------------------- core

def seeds(entry: dict, hide: set[str] = frozenset()) -> dict[str, set[str]]:
    s: dict[str, set[str]] = defaultdict(set)
    for link in entry.get("links", []):
        if link.get("type") in hide:
            continue
        got = ids_of(link.get("url", ""))
        if got and got[0] not in hide:
            s[got[0]].add(got[1])
    return s


def lookup(reg: dict[str, dict], hide: set[str] = frozenset(), routes=("metagrid", "humanlineage", "wikidata"),
           ids: list[str] | None = None, workers: int = 6) -> dict[str, list[dict]]:
    """Proposed links per entry: [{type, url, value?, source, via}]. Runs two rounds,
    so an id found in the first (a GND from Metagrid) is asked in the second."""
    ids = ids or list(reg)
    known = {k: seeds(reg[k], hide) for k in ids}
    found: dict[str, list[dict]] = defaultdict(list)
    asked: set[tuple] = set()

    def add(k, typ, url, source, via, value=None):
        if any(f["url"] == url for f in found[k]):
            return
        found[k].append({"type": typ, "url": url, "value": value, "source": source, "via": via})
        if value and typ in ("hls", "gnd", "wikidata"):
            known[k][typ].add(value)

    for _round in range(2):
        jobs = []
        for k in ids:
            e = reg[k]
            for kind, vals in list(known[k].items()):
                for v in list(vals):
                    if (kind, v, k) in asked:
                        continue
                    if "metagrid" in routes and e["kind"] == "person" and kind in MG_PROVIDER:
                        jobs.append(("metagrid", k, kind, v))
                    if "humanlineage" in routes and e["kind"] == "person" and kind in ("hls", "gnd", "wikidata"):
                        jobs.append(("humanlineage", k, kind, v))
                    asked.add((kind, v, k))
        with cf.ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(metagrid if r == "metagrid" else humanlineage, kind, v): (r, k, kind, v)
                    for r, k, kind, v in jobs}
            for fut in cf.as_completed(futs):
                r, k, kind, v = futs[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"  {r} {kind}:{v} failed: {exc}", file=sys.stderr)
                    continue
                via = f"{kind}:{v}"
                if r == "metagrid":
                    for item in res:
                        got = ids_of(item["url"])
                        typ = got[0] if got else type_of(item["url"], item["label"])
                        add(k, typ, item["url"], "metagrid", via, got[1] if got else None)
                else:
                    hl = res[0]
                    if hl.get("status") != "match":
                        continue
                    p = hl["persons"][0]
                    for typ, val in (("wikidata", p.get("wikidata_id")), ("gnd", p.get("gnd_id")), ("hls", p.get("hls_id"))):
                        if val:
                            val = val.zfill(6) if typ == "hls" else val
                            add(k, typ, url_of(typ, val), "humanlineage", via, val)
        if "wikidata" in routes:
            pairs = sorted({(kind, v) for k in ids for kind in ("wikidata", "gnd", "hls") for v in known[k].get(kind, ())
                            if ("wd", kind, v, k) not in asked})
            wd = wikidata_batch(pairs)
            for k in ids:
                for kind in ("wikidata", "gnd", "hls"):
                    for v in list(known[k].get(kind, ())):
                        if ("wd", kind, v, k) in asked:
                            continue
                        asked.add(("wd", kind, v, k))
                        items = wd.get((kind, v), [])
                        if len({i["qid"] for i in items}) != 1:
                            continue  # no item, or the id sits on several: no link
                        for i in items:
                            via = f"{kind}:{v}"
                            add(k, "wikidata", url_of("wikidata", i["qid"]), "wikidata", via, i["qid"])
                            if i.get("hls"):
                                add(k, "hls", url_of("hls", i["hls"].zfill(6)), "wikidata", via, i["hls"].zfill(6))
                            if i.get("gnd"):
                                add(k, "gnd", url_of("gnd", i["gnd"]), "wikidata", via, i["gnd"])
                            if i.get("geonames") and reg[k]["kind"] == "place":
                                add(k, "geonames", url_of("geonames", i["geonames"]), "wikidata", via, i["geonames"])
    return found


def settle(entry: dict, proposed: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep what is new and agreed. A single-valued authority (HLS, GND, Wikidata)
    is added only when every route that found one found the same, and the entry
    has none or the same; otherwise it is a conflict and nothing is written."""
    have_urls = {l["url"].rstrip("/") for l in entry.get("links", [])}
    have = seeds(entry)
    add, conflicts = [], []
    by_type = defaultdict(list)
    for p in proposed:
        by_type[p["type"]].append(p)
    for typ, ps in by_type.items():
        if typ in SINGLE:
            vals = {p["value"] for p in ps if p.get("value")}
            existing = have.get(typ, set())
            if existing and vals & existing:
                continue  # the edition's id is among those found: agreement, nothing to add
            if len(vals) > 1 or (existing and vals):
                conflicts.append({"type": typ, "existing": sorted(existing), "found": sorted(vals),
                                  "routes": sorted({f'{p["source"]} via {p["via"]}' for p in ps})})
                continue
            if existing:
                continue
            p = ps[0]
            add.append({"type": typ, "url": p["url"], "source": sorted({q["source"] for q in ps}),
                        "via": sorted({q["via"] for q in ps})})
        else:
            for p in ps:
                if p["url"].rstrip("/") in have_urls or any(a["url"] == p["url"] for a in add):
                    continue
                if typ == "metagrid":
                    continue
                add.append({"type": typ, "url": p["url"], "source": [p["source"]], "via": [p["via"]]})
    return add, conflicts


# ------------------------------------------------------------------------ commands

def cmd_measure(reg: dict[str, dict]) -> None:
    """The persons whose HLS link the edition gives: hide it (and the Metagrid
    concordance, which would hand it back), look it up, compare."""
    hide = {"hls", "hls-dhs-dss", "metagrid"}
    truth = {k: next(iter(seeds(e)["hls"])) for k, e in reg.items() if e["kind"] == "person" and seeds(e).get("hls")}
    print(f"{len(truth)} persons with an HLS link in the edition")
    report = {"n": len(truth), "routes": {}}
    for routes in (("metagrid",), ("humanlineage",), ("wikidata",), ("metagrid", "humanlineage", "wikidata")):
        found = lookup(reg, hide=hide, routes=routes, ids=list(truth))
        right = wrong = none = 0
        wrongs = []
        for k, hls in truth.items():
            vals = {p["value"] for p in found.get(k, []) if p["type"] == "hls" and p.get("value")}
            if not vals:
                none += 1
            elif vals == {hls}:
                right += 1
            else:
                wrong += 1
                wrongs.append({"id": k, "name": reg[k]["name"], "truth": hls, "found": sorted(vals)})
        name = "+".join(routes)
        report["routes"][name] = {"found": right + wrong, "right": right, "wrong": wrong, "none": none,
                                  "precision": round(right / (right + wrong), 3) if right + wrong else None,
                                  "recall": round(right / len(truth), 3), "wrong_cases": wrongs}
        print(f"  {name:32s} found {right + wrong:3d}  right {right:3d}  wrong {wrong:2d}  none {none:3d}")
    OUT.mkdir(exist_ok=True)
    (OUT / "authority_measure.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")


def cmd_lookup(reg: dict[str, dict]) -> None:
    found = lookup(reg)
    links, conflicts = {}, {}
    counts = Counter()
    for k, e in reg.items():
        add, conf = settle(e, found.get(k, []))
        if add:
            links[k] = add
            for a in add:
                counts[(e["kind"], a["type"])] += 1
        if conf:
            conflicts[k] = {"name": e["name"], "conflicts": conf}
    doc = {"about": "Authority links added to the edition's registers by DH Bern (issue #12), joined only on "
                    "identifiers the edition already gives. Not part of the edition.",
           "date": TODAY,
           "sources": {"metagrid": "https://api.metagrid.ch (concordance widget)",
                       "humanlineage": "Human Lineage snapshot via our MCP humanlineage.get_by_identifier "
                                       "(CC BY-SA 4.0, https://humanlineage.org)",
                       "wikidata": "Wikidata Query Service (P902, P227, P1566)"},
           "links": dict(sorted(links.items())), "conflicts": dict(sorted(conflicts.items()))}
    OUT.mkdir(exist_ok=True)
    (OUT / "authority_links.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print(f"{sum(len(v) for v in links.values())} links for {len(links)} entries; {len(conflicts)} conflicts")
    for (kind, typ), n in sorted(counts.items(), key=lambda x: (-x[1])):
        print(f"  {kind:7s} {typ:24s} {n}")


YEARS = re.compile(r"\((\d{3,4})?\s*[–-]\s*(\d{3,4})?\)")


def person_query(e: dict) -> tuple[str, int | None]:
    name = YEARS.sub("", e["name"]).strip()
    if "," in name:
        last, first = (x.strip() for x in name.split(",", 1))
        name = f"{first} {last}".strip()
    m = YEARS.search(e["name"])
    year = int(m.group(1) or m.group(2)) if m and (m.group(1) or m.group(2)) else None
    return name, year


def candidates_for(e: dict, hl: MCP) -> list[dict]:
    name, year = person_query(e)
    if not name or not year:
        return []

    def ask():
        r = hl.call("resolve_name", name=name, century=year // 100 + 1, limit=5, year_tolerance=40)
        return {"status": r.get("status"), "candidates": [
            {k: c.get(k) for k in ("id", "name", "score", "matched", "life_dates", "birth_year", "death_year",
                                   "wikidata_id", "gnd_id", "hls_id", "url", "description", "date_support")}
            for c in r.get("candidates", [])]}
    r = cached(f"hlname:{name}:{year}", ask)
    out = []
    for c in r["candidates"]:
        b, d = c.get("birth_year"), c.get("death_year")
        # the register's year is an attestation: the person must have been alive, give or take
        if b and year < b + 10:
            continue
        if d and year > d + 5:
            continue
        if not (b or d):
            continue
        out.append({**c, "status": r["status"]})
    return out


def cmd_candidates(reg: dict[str, dict], measure_only: bool = False) -> None:
    hl = MCP("humanlineage")
    if measure_only:
        truth = {k: next(iter(seeds(e)["hls"])) for k, e in reg.items() if e["kind"] == "person" and seeds(e).get("hls")}
        right = wrong = none = 0
        for k, hls in truth.items():
            cs = [c for c in candidates_for(reg[k], hl) if c.get("score", 0) >= 0.9]
            top = cs[0] if cs else None
            if not top or not top.get("hls_id"):
                none += 1
            elif top["hls_id"].zfill(6) == hls:
                right += 1
            else:
                wrong += 1
        print(f"name route on {len(truth)} persons with HLS: right {right}, wrong {wrong}, none {none}")
        return
    rows = []
    for k, e in reg.items():
        if e["kind"] != "person":
            continue
        s = seeds(e)
        if s.get("hls") or s.get("wikidata") or s.get("gnd"):
            continue
        for c in candidates_for(e, hl)[:3]:
            rows.append([k, e["name"], c.get("name"), c.get("life_dates") or "", f'{c.get("score") or 0:.2f}',
                         c.get("wikidata_id") or "", c.get("gnd_id") or "", c.get("hls_id") or "", c.get("url") or "",
                         (c.get("description") or "")[:120]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "authority_candidates.tsv").open("w", encoding="utf-8") as f:
        f.write("register_id\tregister_name\tcandidate\tlife_dates\tscore\twikidata\tgnd\thls\thumanlineage\tdescription\n")
        for r in rows:
            f.write("\t".join(str(x).replace("\t", " ") for x in r) + "\n")
    print(f"{len(rows)} candidates for {len({r[0] for r in rows})} persons -> registers/authority_candidates.tsv")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["measure", "lookup", "candidates", "measure-names"])
    a = ap.parse_args()
    reg = registers()
    if a.cmd == "measure":
        cmd_measure(reg)
    elif a.cmd == "lookup":
        cmd_lookup(reg)
    elif a.cmd == "candidates":
        cmd_candidates(reg)
    else:
        cmd_candidates(reg, measure_only=True)


if __name__ == "__main__":
    main()
