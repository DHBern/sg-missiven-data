"""Register entries for names found only in the letters not yet edited (issue #12; automatic, experimental).

The edition's registers know the persons, organisations and places of the
edited letters. The automatic tagging of the letters not yet edited
(docs/NER.md) finds many names that are not in them. This script makes a
register entry, marked as generated automatically, for each such name and
looks for it in HLS, Wikidata and Metagrid. Every link it proposes carries a
grade:

* ``sicher`` (certain), ``wahrscheinlich`` (likely), ``möglich`` (possible),

set by rules on the spelling and, for persons, on the dates (the letters'
years against the candidate's life). The rules are measured on entries of the
edition's own registers whose authority ids are known (``measure``); the
measured precision of each grade is written beside the links.

    auto_register.py groups              # the names, counted -> stdout
    auto_register.py measure             # the grades on known register entries
    auto_register.py prefetch            # places/orgs via the Wikidata Query Service, when the search API refuses (429)
    auto_register.py link [--workers 4]  # -> registers/auto_register.json

A name alone is all there is to go on, so nothing here is certain in the
edition's sense: the entries and their links are marked as automatic wherever
they are shown and never go into the edition's TEI. Answers are cached under
registers/.cache/ (not committed).
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from authority_links import UA, cached, metagrid, type_of, url_of  # noqa: E402
from mcp_client import MCP  # noqa: E402
from ner import key  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

OUT = ROOT / "registers" / "auto_register.json"
FACS = re.compile(r"^StadtASG_Missive_(\d+)_", re.I)
ST_GALLEN = (47.4245, 9.3767)
GRADES = ("sicher", "wahrscheinlich", "möglich")
WD_API = "https://www.wikidata.org/w/api.php"

# ------------------------------------------------------------------ the names


def letter_years(reader: str) -> dict[int, int]:
    """Letter number -> year: the drafted regest's year where it lies in the
    series' span, else the nearest dated neighbours' (the series is numbered
    chronologically)."""
    years: dict[int, int] = {}
    path = ROOT / "regests" / f"draft_{reader}.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        r = json.loads(line)
        y = (r.get("draft") or {}).get("year")
        if isinstance(y, int) and 1400 <= y <= 1660:
            years[int(r["id"].rsplit("_", 1)[1])] = y
    # the edited letters date the gaps too
    for f in (ROOT / "data").glob("stasg_missiv_*.xml"):
        m = re.search(r'<origDate[^>]*\bwhen="(\d{4})', f.read_text(encoding="utf-8", errors="ignore"))
        n = re.search(r"(\d+)$", f.stem)
        if m and n:
            years.setdefault(int(n.group(1)), int(m.group(1)))
    return years


def year_of(nr: int, years: dict[int, int]) -> int | None:
    if nr in years:
        return years[nr]
    near = [years[n] for d in range(1, 15) for n in (nr - d, nr + d) if n in years]
    return sorted(near)[len(near) // 2] if near else None


def first_names() -> set[str]:
    """Forenames the edition's person register uses ("Surname, Forename"), folded."""
    names: set[str] = set()
    xml = (ROOT / "person" / "person.xml").read_text(encoding="utf-8")
    for m in re.finditer(r"<persName[^>]*>([^<]+)</persName>", xml):
        if "," in m.group(1):
            for w in key(m.group(1).split(",", 1)[1]).split():
                if len(w) > 2:
                    names.add(w)
    return names


def edition_forms() -> dict[str, set[str]]:
    """Folded names the edition's registers and edited letters already use, per type."""
    forms: dict[str, set[str]] = collections.defaultdict(set)
    tags = {"persName": "per", "orgName": "org", "placeName": "place"}
    for path in [ROOT / "person" / "person.xml", ROOT / "place" / "place.xml", *sorted((ROOT / "data").glob("*.xml"))]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for tag, t in tags.items():
            for m in re.finditer(rf"<{tag}\b[^>]*>(.*?)</{tag}>", text, re.S):
                k = key(re.sub(r"<[^>]+>", "", m.group(1)))
                if k:
                    forms[t].add(k)
                    if t == "per" and "," in m.group(1):  # "Surname, Forename" -> "forename surname"
                        last, first = m.group(1).split(",", 1)
                        forms[t].add(key(f"{first} {last}"))
    return forms


def groups(reader: str) -> tuple[list[dict], collections.Counter]:
    """The unlinked names of the letters not yet edited, grouped by type and
    folded spelling; dropped: bare forenames, fragments, names the edition
    already has."""
    years = letter_years(reader)
    firsts = first_names()
    known = edition_forms()
    g: dict[tuple[str, str], dict] = {}
    why: collections.Counter = collections.Counter()
    for line in (ROOT / "entities" / f"{reader}.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if not r.get("unedited"):
            continue
        nr = int(FACS.match(r["key"]).group(1))
        for e in r["entities"]:
            t = e["type"]
            if t not in ("per", "org", "place") or e.get("ref"):
                continue
            k = key(e["text"])
            words = k.split()
            if len(k) < 4 or any(c.isdigit() for c in k):
                why[f"{t}: fragment"] += 1
                continue
            if t == "per" and all(w in firsts for w in words):
                why["per: forename only"] += 1
                continue
            if k in known[t]:
                why[f"{t}: in the edition"] += 1
                continue
            x = g.setdefault((t, k), {"type": t, "key": k, "forms": collections.Counter(), "letters": set(), "n": 0})
            x["forms"][" ".join(e["text"].split())] += 1
            x["letters"].add(nr)
            x["n"] += 1
    out = []
    for x in g.values():
        ys = sorted(y for y in (year_of(n, years) for n in x["letters"]) if y)
        out.append({**x, "letters": sorted(x["letters"]), "years": [ys[0], ys[-1]] if ys else None,
                    "name": x["forms"].most_common(1)[0][0]})
    out.sort(key=lambda x: (x["type"], -len(x["letters"]), x["key"]))
    return out, why


# --------------------------------------------------------------- candidates

_lock: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)
_mcp: dict[str, MCP] = {}


def mcp_call(server: str, tool: str, **args):
    # one call at a time per server: the sessions do not take concurrent calls
    with _lock[server]:
        if server not in _mcp:
            _mcp[server] = MCP(server).open()
        return _mcp[server].call(tool, **args)


def life(b, d) -> str:
    return f"{b or '?'}–{d or '?'}" if (b or d) else ""


def year_int(v) -> int | None:
    m = re.match(r"-?(\d{3,4})", str(v or ""))
    return int(m.group(1)) if m else None


def person_candidates(name: str) -> list[dict]:
    """HLS's persons and Human Lineage's (Wikidata) for a name, with life dates."""
    out = []
    hls = cached(f"hlsp:{name}", lambda: mcp_call("hls", "search_persons", query=name, limit=10))
    for c in hls or []:
        nm = " ".join(x for x in (c.get("first_name"), c.get("family_name")) if x) or c.get("title", "")
        out.append({"source": "hls.search_persons", "name": nm, "label": c.get("title") or nm,
                    "birth": year_int(c.get("birth_date")), "death": year_int(c.get("death_date")),
                    "hls": str(c.get("article_id") or "").zfill(6) or None, "wikidata": None, "gnd": None})
    hl = cached(f"hlres:{name}", lambda: mcp_call("humanlineage", "resolve_name", name=name, limit=8))
    for c in (hl or {}).get("candidates", []):
        out.append({"source": "humanlineage.resolve_name", "name": c.get("name") or "", "label": c.get("name") or "",
                    "birth": c.get("birth_year"), "death": c.get("death_year"), "hls": c.get("hls_id"),
                    "wikidata": c.get("wikidata_id"), "gnd": c.get("gnd_id"), "humanlineage": c.get("url"),
                    "desc": c.get("description")})
    return out


_WD_LOCK = threading.Lock()
_wd_last = [0.0]


def wd_get(params: dict) -> dict:
    """One Wikidata API call at a time, at most two a second; on 429 or 503
    wait as long as Wikidata asks (Retry-After) and try again."""
    url = f"{WD_API}?{urllib.parse.urlencode(params)}"
    for attempt in range(8):
        with _WD_LOCK:
            wait = 0.5 - (time.time() - _wd_last[0])
            if wait > 0:
                time.sleep(wait)
            _wd_last[0] = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                delay = int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1)
            except (urllib.error.URLError, TimeoutError):
                delay = 5 * (attempt + 1)
            time.sleep(min(delay, 120))
    raise RuntimeError(f"Wikidata did not answer: {params.get('action')}")


def wd_search(text: str, kind: str) -> list[str]:
    def ask():
        r = wd_get({"action": "wbsearchentities", "search": text, "language": "de", "uselang": "de",
                    "type": "item", "limit": 10, "format": "json"})
        return [h["id"] for h in r.get("search", [])]
    return cached(f"wds:{kind}:{text}", ask)


def wd_entities(qids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(qids), 50):
        part = qids[i:i + 50]

        def ask(part=part):
            ents = wd_get({"action": "wbgetentities", "ids": "|".join(part), "languages": "de|fr|it|en|la",
                           "props": "labels|aliases|claims|descriptions", "format": "json"}).get("entities", {})
            slim = {}
            for qid, e in ents.items():
                cl = e.get("claims", {})

                def vals(p):
                    return [c["mainsnak"].get("datavalue", {}).get("value") for c in cl.get(p, [])
                            if c.get("mainsnak", {}).get("datavalue")]
                coord = next((v for v in vals("P625") if isinstance(v, dict)), None)
                slim[qid] = {
                    "names": sorted({x["value"] for x in e.get("labels", {}).values()}
                                    | {a["value"] for al in e.get("aliases", {}).values() for a in al}),
                    "label": (e.get("labels", {}).get("de") or e.get("labels", {}).get("en") or {}).get("value", qid),
                    "desc": (e.get("descriptions", {}).get("de") or e.get("descriptions", {}).get("en") or {}).get("value", ""),
                    "coord": [coord["latitude"], coord["longitude"]] if coord else None,
                    "hls": (vals("P902") or [None])[0], "gnd": (vals("P227") or [None])[0],
                    "p31": [v.get("id") for v in vals("P31") if isinstance(v, dict)],
                    "human": any(isinstance(v, dict) and v.get("id") == "Q5" for v in vals("P31")),
                }
            return slim
        out.update(cached("wde:" + "|".join(part), ask))
    return out


WDQS = "https://query.wikidata.org/sparql"
LANGS = ("de", "fr", "it", "en", "la")


def sparql(query: str) -> list[dict]:
    data = urllib.parse.urlencode({"query": query}).encode()
    for attempt in range(6):
        try:
            req = urllib.request.Request(WDQS, data=data, headers={
                "User-Agent": UA, "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())["results"]["bindings"]
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            delay = int(e.headers.get("Retry-After") or 0) or 10 * (attempt + 1)
        except (urllib.error.URLError, TimeoutError):
            delay = 10 * (attempt + 1)
        time.sleep(min(delay, 120))
    raise RuntimeError("Wikidata Query Service did not answer")


def lit(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cmd_prefetch(reader: str, workers: int) -> None:
    """Fill the Wikidata cache for places and organisations through the Query
    Service, many names per query, when the search API refuses this address
    (429, 14.11.2026). It finds a name only where it is an item's label or alias
    in German, French, Italian, English or Latin, exactly: the search API also
    finds prefixes, so a name looked up this way may lack the «möglich»
    candidates a search would have given. The answers go into the cache under the
    same keys as ``wd_search`` and ``wd_entities``; ``link`` then runs from it."""
    from authority_links import CACHE
    has = lambda k: (CACHE / (hashlib.sha1(k.encode()).hexdigest() + ".json")).exists()  # noqa: E731
    put = lambda k, v: (CACHE / (hashlib.sha1(k.encode()).hexdigest() + ".json")).write_text(  # noqa: E731
        json.dumps({"k": k, "v": v}, ensure_ascii=False))
    # the names of the link run, and the edition's places that measure the grades
    gs = [g for g in groups(reader)[0] if g["type"] in ("place", "org")] + [c for c in truth_sets() if c["type"] == "place"]
    forms_of = lambda g: [f for f, _ in g["forms"].most_common(2)] if isinstance(g["forms"], collections.Counter) else g["forms"][:2]  # noqa: E731
    kind_of = lambda g: "place" if g["type"] == "place" else "org"  # noqa: E731
    todo = sorted({(kind_of(g), f) for g in gs for f in forms_of(g) if not has(f"wds:{kind_of(g)}:{f}")})
    print(f"{len(gs)} places and organisations; {len(todo)} names to look up by SPARQL", flush=True)
    found: dict[str, set[str]] = collections.defaultdict(set)
    texts = sorted({f for _, f in todo})
    batches = [texts[i:i + 40] for i in range(0, len(texts), 40)]

    def search(batch):
        values = " ".join(f"{lit(t)}@{lg}" for t in batch for lg in LANGS)
        return sparql(f"SELECT ?n ?i WHERE {{ VALUES ?n {{ {values} }} ?i rdfs:label|skos:altLabel ?n . }}")
    with cf.ThreadPoolExecutor(workers) as ex:
        for k, rows in enumerate(ex.map(search, batches), 1):
            for b in rows:
                found[b["n"]["value"]].add(b["i"]["value"].rsplit("/", 1)[1])
            if k % 20 == 0:
                print(f"  search {k}/{len(batches)}", flush=True)
    for kind, t in todo:
        put(f"wds:{kind}:{t}", sorted(found.get(t, ()), key=lambda q: int(q[1:]))[:10])

    # entities, under the keys wd_entities will ask for
    parts = []
    for g in gs:
        qids: list[str] = []
        for f in forms_of(g):
            for q in wd_search(f, kind_of(g)):
                if q not in qids:
                    qids.append(q)
        parts += [qids[i:i + 50] for i in range(0, len(qids), 50) if not has("wde:" + "|".join(qids[i:i + 50]))]
    need = sorted({q for p in parts for q in p}, key=lambda q: int(q[1:]))
    print(f"{len(parts)} entity sets, {len(need)} items to fetch", flush=True)
    slim: dict[str, dict] = {}

    def fetch(batch):
        values = " ".join(f"wd:{q}" for q in batch)
        langs = ", ".join(lit(lg) for lg in LANGS)
        return sparql(f"""SELECT ?i ?p ?v ?l WHERE {{ VALUES ?i {{ {values} }}
          {{ ?i rdfs:label ?v . BIND("label" AS ?p) FILTER(lang(?v) IN ({langs})) }}
          UNION {{ ?i skos:altLabel ?v . BIND("alias" AS ?p) FILTER(lang(?v) IN ({langs})) }}
          UNION {{ ?i schema:description ?v . BIND("desc" AS ?p) FILTER(lang(?v) IN ("de", "en")) }}
          UNION {{ ?i wdt:P625 ?v . BIND("coord" AS ?p) }} UNION {{ ?i wdt:P902 ?v . BIND("hls" AS ?p) }}
          UNION {{ ?i wdt:P227 ?v . BIND("gnd" AS ?p) }} UNION {{ ?i wdt:P31 ?v . BIND("p31" AS ?p) }}
          BIND(lang(?v) AS ?l) }}""")
    batches = [need[i:i + 50] for i in range(0, len(need), 50)]
    with cf.ThreadPoolExecutor(workers) as ex:
        for k, rows in enumerate(ex.map(fetch, batches), 1):
            for b in rows:
                q = b["i"]["value"].rsplit("/", 1)[1]
                e = slim.setdefault(q, {"names": set(), "label": {}, "desc": {}, "coord": None,
                                        "hls": None, "gnd": None, "p31": []})
                p, v, lg = b["p"]["value"], b["v"]["value"], b.get("l", {}).get("value", "")
                if p in ("label", "alias"):
                    e["names"].add(v)
                    if p == "label":
                        e["label"][lg] = v
                elif p == "desc":
                    e["desc"][lg] = v
                elif p == "coord" and e["coord"] is None:
                    m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", v)
                    if m:
                        e["coord"] = [float(m.group(2)), float(m.group(1))]
                elif p in ("hls", "gnd"):
                    e[p] = e[p] or v
                elif p == "p31":
                    e["p31"].append(v.rsplit("/", 1)[1])
            if k % 20 == 0:
                print(f"  entities {k}/{len(batches)}", flush=True)
    for q, e in slim.items():
        e.update(names=sorted(e["names"]), label=e["label"].get("de") or e["label"].get("en") or q,
                 desc=e["desc"].get("de") or e["desc"].get("en") or "", human="Q5" in e["p31"])
    for p in parts:
        put("wde:" + "|".join(p), {q: slim[q] for q in p if q in slim})
    print(f"cached {len(todo)} searches and {len(parts)} entity sets", flush=True)


def km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def place_candidates(forms: list[str], kind: str = "place") -> list[dict]:
    qids: list[str] = []
    for f in forms:
        for q in wd_search(f, kind):
            if q not in qids:
                qids.append(q)
    ents = wd_entities(qids)
    out = []
    for q in qids:
        e = ents.get(q)
        if not e or e["human"]:
            continue
        if kind == "place" and not e["coord"]:
            continue
        out.append({"source": "wikidata.wbsearchentities", "label": e["label"], "desc": e["desc"], "names": e["names"],
                    "wikidata": q, "hls": e["hls"], "gnd": e["gnd"],
                    "km": round(km(ST_GALLEN, e["coord"])) if e["coord"] else None})
    return out


# ------------------------------------------------------------------- grading


def sim(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(key(a), key(b))


def grade_person(g: dict, cands: list[dict]) -> list[dict]:
    """sicher: forename and surname, the spelling matches (>= 95), the dates fit
    and no other candidate whose dates fit comes near; wahrscheinlich: matches
    (>= 90), dates fit, clearly the best; möglich: close (>= 82), dates fit."""
    if not g.get("years"):
        return []
    y0, y1 = g["years"]
    fit = []
    for c in cands:
        b, d = c.get("birth"), c.get("death")
        if not (b or d):
            continue  # no dates, no check: not proposed
        if b and y0 < b + 12:
            continue
        if d and y1 > d + 3:
            continue
        if b and not d and y1 > b + 90:
            continue
        if d and not b and y0 < d - 80:
            continue
        s = max(sim(f, c["name"]) for f in g["forms"])
        if s >= 82:
            fit.append({**c, "score": round(s)})
    # the same person from both sources is one candidate
    merged: dict[str, dict] = {}
    for c in sorted(fit, key=lambda c: -c["score"]):
        k = c.get("hls") or c.get("wikidata") or c["label"]
        if k in merged:
            for f in ("hls", "wikidata", "gnd", "humanlineage"):
                merged[k][f] = merged[k].get(f) or c.get(f)
            continue
        twin = next((m for m in merged.values() if (c.get("hls") and m.get("hls") == c.get("hls"))
                     or (c.get("wikidata") and m.get("wikidata") == c.get("wikidata"))), None)
        if twin:
            for f in ("hls", "wikidata", "gnd", "humanlineage"):
                twin[f] = twin.get(f) or c.get(f)
            continue
        merged[k] = dict(c)
    ranked = sorted(merged.values(), key=lambda c: -c["score"])
    two_names = len(g["key"].split()) >= 2
    out = []
    for i, c in enumerate(ranked):
        rivals = [r for j, r in enumerate(ranked) if j != i and r["score"] >= c["score"] - 5]
        if i == 0 and two_names and c["score"] >= 95 and not rivals:
            c["grade"] = "sicher"
        elif i == 0 and c["score"] >= 90 and not rivals:
            c["grade"] = "wahrscheinlich"
        else:
            c["grade"] = "möglich"
        out.append(c)
    return out[:3]


NOT_A_PLACE = ("Bahnhof ", "Gare ", "Station ", "Landkreis ", "Bezirk ")


def grade_place(g: dict, cands: list[dict]) -> list[dict]:
    """Measured on the edition's own places as the letters spell them
    (14.11.2026): an exact name match is right in 73 % of cases when the item has
    an HLS article and in about 22 % when it has none, and no rule on distance
    or uniqueness came near 90 %. So a place is never «sicher»:
    wahrscheinlich = a name of the item is the spelling (folded) and the item has
    an HLS id (the nearest such to St. Gallen first); möglich = an exact match
    without HLS id, or a close spelling (>= 85). Railway stations and districts
    are not proposed."""
    exact, close = [], []
    for c in cands:
        if c["label"].startswith(NOT_A_PLACE):
            continue
        names = {key(n) for n in c["names"]}
        if g["key"] in names or any(key(f) in names for f in g["forms"]):
            exact.append(c)
        else:
            s = max((fuzz.ratio(key(f), n) for f in g["forms"] for n in names), default=0)
            if s >= 85:
                close.append({**c, "score": round(s)})
    exact.sort(key=lambda c: (not c.get("hls"), c["km"] if c["km"] is not None else 1e9))
    out = [{**c, "score": 100, "grade": "wahrscheinlich" if i == 0 and c.get("hls") else "möglich"}
           for i, c in enumerate(exact)]
    close.sort(key=lambda c: (-c["score"], c["km"] if c["km"] is not None else 1e9))
    out += [{**c, "grade": "möglich"} for c in close]
    return out[:3]


def grade_org(g: dict, cands: list[dict]) -> list[dict]:
    """Organisations: only an exact name of a Wikidata item, and at most möglich."""
    out = []
    for c in cands:
        names = {key(n) for n in c["names"]}
        if g["key"] in names or any(key(f) in names for f in g["forms"]):
            out.append({**c, "score": 100, "grade": "möglich"})
    return out[:2]


def candidates(g: dict) -> list[dict]:
    forms = [f for f, _ in g["forms"].most_common(2)] if isinstance(g["forms"], collections.Counter) else g["forms"][:2]
    if g["type"] == "per":
        cands = []
        for f in forms:
            cands += person_candidates(f)
        return grade_person({**g, "forms": forms}, cands)
    if g["type"] == "place":
        return grade_place({**g, "forms": forms}, place_candidates(forms))
    return grade_org({**g, "forms": forms}, place_candidates(forms, kind="org"))


def links_of(c: dict) -> list[dict]:
    """The authority links one graded candidate gives; Metagrid's concordance
    for persons with an HLS or GND id."""
    out = []
    for kind in ("hls", "wikidata", "gnd"):
        if c.get(kind):
            out.append({"type": kind, "url": url_of(kind, c[kind])})
    if c.get("humanlineage"):
        out.append({"type": "humanlineage", "url": c["humanlineage"]})
    return out


def metagrid_links(c: dict) -> list[dict]:
    out = []
    for kind in ("hls", "gnd"):
        if c.get(kind):
            for r in metagrid(kind, c[kind]):
                if "metagrid" in r["url"] or "api." in r["url"]:
                    continue
                out.append({"type": type_of(r["url"], r["label"]), "url": r["url"], "via": f"metagrid:{kind}"})
            break
    return out


# ------------------------------------------------------------------- commands


def truth_sets() -> list[dict]:
    """Test cases from the edition: register entries with a known HLS or
    Wikidata id (the edition's own and those issue #12 joined on its ids), as
    the edited letters spell them, with those letters' years."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_site import registers
    from authority_links import ids_of
    reg = registers()
    added = json.loads((ROOT / "registers" / "authority_links.json").read_text()).get("links", {}) \
        if (ROOT / "registers" / "authority_links.json").exists() else {}
    years = letter_years("gemini-4-argon")
    occ: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    for f in sorted((ROOT / "data").glob("stasg_missiv_*.xml")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        n = re.search(r"(\d+)$", f.stem)
        if not n:
            continue
        nr = int(n.group(1))
        for m in re.finditer(r'<(persName|placeName)\b[^>]*\bref="#?([^"\s]+)[^"]*"[^>]*>(.*?)</\1>', text, re.S):
            occ[m.group(2)].append((" ".join(re.sub(r"<[^>]+>", "", m.group(3)).split()), nr))
    cases = []
    for rid, e in reg.items():
        if e["kind"] not in ("person", "place") or rid not in occ:
            continue
        ids = {}
        for l in e["links"] + added.get(rid, []):
            k = ids_of(l["url"])
            if k and k[0] in ("hls", "wikidata"):
                ids.setdefault(k[0], k[1])
        if not ids:
            continue
        forms = collections.Counter(f for f, _ in occ[rid] if f)
        ys = sorted(y for y in (year_of(n, years) for _, n in occ[rid]) if y)
        if not forms or not ys:
            continue
        t = "per" if e["kind"] == "person" else "place"
        cases.append({"id": rid, "type": t, "key": key(forms.most_common(1)[0][0]), "forms": forms,
                      "years": [ys[0], ys[-1]], "truth": ids, "name": e["name"]})
    return cases


def cmd_measure(workers: int) -> dict:
    cases = truth_sets()
    print(f"{len(cases)} register entries with a known HLS or Wikidata id "
          f"({sum(c['type'] == 'per' for c in cases)} persons, {sum(c['type'] == 'place' for c in cases)} places)", flush=True)
    res: dict = {t: {g: {"right": 0, "wrong": 0} for g in GRADES} | {"none": 0, "n": 0} for t in ("per", "place")}
    wrong: list = []

    def one(c):
        try:
            return c, candidates(c)
        except Exception as exc:  # noqa: BLE001
            print(f"  failed {c['name']}: {exc}", file=sys.stderr, flush=True)
            return c, None
    with cf.ThreadPoolExecutor(workers) as ex:
        for c, cands in ex.map(one, cases):
            if cands is None:
                continue
            r = res[c["type"]]
            r["n"] += 1
            top = cands[0] if cands else None
            if not top:
                r["none"] += 1
                continue
            ok = any(top.get(k) and str(top[k]).zfill(6 if k == "hls" else 0) == str(v).zfill(6 if k == "hls" else 0)
                     for k, v in c["truth"].items())
            r[top["grade"]]["right" if ok else "wrong"] += 1
            if not ok:
                wrong.append({"entry": c["name"], "spelling": c["forms"].most_common(1)[0][0], "years": c["years"],
                              "grade": top["grade"], "proposed": top["label"], "truth": c["truth"]})
    for t, r in res.items():
        for gr in GRADES:
            n = r[gr]["right"] + r[gr]["wrong"]
            r[gr]["precision"] = round(r[gr]["right"] / n, 3) if n else None
    report = {"date": dt.date.today().isoformat(), "results": res, "wrong": wrong}
    (ROOT / "registers" / "auto_register_measure.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                                   encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return report


def cmd_link(reader: str, workers: int, limit: int | None) -> None:
    gs, why = groups(reader)
    # persons first: they go to our own servers, while Wikidata (places,
    # organisations) answers slowly
    gs.sort(key=lambda g: ("per", "place", "org").index(g["type"]))
    if limit:
        gs = gs[:limit]
    measure_path = ROOT / "registers" / "auto_register_measure.json"
    measured = json.loads(measure_path.read_text())["results"] if measure_path.exists() else None
    print(f"{len(gs)} names to look up", flush=True)
    entries: dict[str, dict] = {}
    done = failed = 0

    def one(g):
        try:
            cands = candidates(g)
        except Exception as exc:  # noqa: BLE001
            print(f"  failed {g['name']}: {exc}", file=sys.stderr, flush=True)
            return g, None
        for c in cands:
            c["links"] = links_of(c)
            if c["grade"] != "möglich" and g["type"] == "per":
                c["links"] += metagrid_links(c)
        return g, cands
    with cf.ThreadPoolExecutor(workers) as ex:
        for g, cands in ex.map(one, gs):
            if cands is None:
                failed += 1
                continue
            # the id stays the same as long as the spelling does, however the list grows
            prefix = {"per": "auto-per", "org": "auto-org", "place": "auto-pla"}[g["type"]]
            eid = f"{prefix}-{hashlib.sha1(g['key'].encode()).hexdigest()[:10]}"
            entries[eid] = {
                "kind": {"per": "person", "org": "org", "place": "place"}[g["type"]],
                "name": g["name"], "key": g["key"], "auto": True,
                "forms": [f for f, _ in g["forms"].most_common(8)], "mentions": g["n"],
                "letters": [f"stasg_missiv_unedited_{n:05d}" for n in g["letters"]], "years": g["years"],
                "candidates": [{k: c.get(k) for k in ("label", "desc", "grade", "score", "source", "birth", "death", "km",
                                                      "hls", "wikidata", "gnd", "links") if c.get(k) is not None}
                               for c in cands],
            }
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(gs)}", flush=True)
    grades = collections.Counter((e["kind"], e["candidates"][0]["grade"] if e["candidates"] else "none")
                                 for e in entries.values())
    OUT.write_text(json.dumps({
        "about": "Register entries generated automatically for names that the automatic tagging found in the letters "
                 "not yet edited and that the edition's registers do not have (issue #12). Not part of the edition; "
                 "every link is a proposal, graded sicher / wahrscheinlich / möglich.",
        "date": dt.date.today().isoformat(), "reader": reader,
        "rules": {"per": grade_person.__doc__, "place": grade_place.__doc__, "org": grade_org.__doc__},
        "measured": measured, "dropped": dict(why),
        "counts": {f"{k}/{g}": n for (k, g), n in sorted(grades.items())},
        "entries": entries}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(entries)} entries, {failed} failed (rerun: answers are cached); "
          f"{dict(grades)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["groups", "measure", "prefetch", "link"])
    ap.add_argument("--reader", default="gemini-4-argon")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.cmd == "groups":
        gs, why = groups(a.reader)
        c = collections.Counter(g["type"] for g in gs)
        print(f"{len(gs)} names: {dict(c)}; dropped {dict(why)}")
        for t in ("per", "org", "place"):
            sub = [g for g in gs if g["type"] == t]
            print(t, "in >=2 letters:", sum(len(g["letters"]) >= 2 for g in sub), "no year:", sum(not g["years"] for g in sub))
            for g in sub[:8]:
                print("   ", len(g["letters"]), g["name"], g["years"])
    elif a.cmd == "measure":
        cmd_measure(a.workers)
    elif a.cmd == "prefetch":
        cmd_prefetch(a.reader, min(a.workers, 3))
    else:
        cmd_link(a.reader, a.workers, a.limit)


if __name__ == "__main__":
    main()
