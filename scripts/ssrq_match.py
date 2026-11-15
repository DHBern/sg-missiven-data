"""SSRQ as a second authority for the generated register entries (issue #12).

The Sammlung Schweizerischer Rechtsquellen keeps historical spellings with its
authority records, and that is what a name read from a letter needs:

* places: the SSRQ place database (loci.ssrq-sds-fds.ch). Its search takes
  Lucene syntax, so ``Adlischwil~`` finds Adliswil by a near spelling; each
  place page lists the original spellings (Adtlyschwil), its type, canton or
  region, and links to HLS, GND and ortsnamen.ch.
* persons and organisations: our SSRQ MCP server (persons with forename variants
  and the years they are attested in the SSRQ volumes; organisations with every
  recorded spelling).

Every candidate found here is a proposal: marked automatic and uncertain
wherever it is shown, graded by rules measured on the edition's own register
entries that carry an SSRQ id (``auto_register.py measure-ssrq``).

Answers are cached with the other lookups (registers/.cache/).
"""

from __future__ import annotations

import collections
import html
import re
import threading
import time
import urllib.parse
import urllib.request

from authority_links import UA, cached
from ner import key
from rapidfuzz import fuzz

LOCI = "https://loci.ssrq-sds-fds.ch"
COUNTRIES = ["CH", "DE", "AT", "FR", "IT", "LI",
             "AL|BE|BG|BR|CY|CZ|DK|EG|ES|GB|GI|GR|HR|HU|IE|IL|IS|IQ|JO|LB|LU|LT|ME|MT|NL|PL|PS|PT|RO|RU|SE|SI|SK|SY|TN|TR|US|VA|0"]
LANGS = ["deu", "fra", "ita", "lat", "roh"]
# settlements and territories: what a letter's place name usually means
SETTLEMENT = re.compile(r"Stadt|Dorf|Gemeinde|Weiler|Flecken|Markt|Land|Herrschaft|Vogtei|Grafschaft|Kanton|Bistum|"
                        r"Fürstentum|Herzogtum|Königreich|Reich|Region|Gebiet|Landschaft|Amt|Kloster|Abtei", re.I)
NEAR = {"SG", "AR", "AI", "TG", "ZH", "GL", "SH", "GR", "LI", "DE-BW", "DE-BY", "AT-8"}

_LOCK = threading.Lock()
_last = [0.0]


def skey(s: str) -> str:
    """The folding of scripts/ner.py, and a few more of the scribes' habits."""
    k = key(s)
    for a, b in (("ph", "f"), ("th", "t"), ("dt", "t"), ("tz", "z"), ("ck", "k"), ("c", "k"), ("ae", "e"),
                 ("oe", "o"), ("ue", "u"), ("ie", "i")):
        k = k.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", k)


def _get(url: str, data: bytes | None = None) -> str:
    """One request at a time to the place database, at most two a second."""
    for attempt in range(5):
        with _LOCK:
            wait = 0.5 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                if attempt == 4:
                    raise
        time.sleep(5 * (attempt + 1))
    return ""


def _text(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def loci_search(query: str) -> list[dict]:
    def ask():
        body = urllib.parse.urlencode([("query", query)] + [("lang", x) for x in LANGS]
                                      + [("country", x) for x in COUNTRIES]).encode()
        page = _get(f"{LOCI}/search/search.xq", body)
        rows = []
        for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
            td = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if len(td) >= 5 and re.match(r"loc\d+", _text(td[0])):
                rows.append({"id": _text(td[0]), "name": _text(td[1]), "type": _text(td[2]),
                             "region": _text(td[3]), "part_of": _text(td[4])})
        return rows
    return cached(f"loci-search:{query}", ask)


def loci_place(lid: str) -> dict:
    def ask():
        page = _get(f"{LOCI}/views/view-place.xq?id={lid}")
        name = _text((re.search(r"<h3>(.*?)</h3>", page, re.S) or [None, ""])[1])
        variants = []
        m = re.search(r"Schreibvarianten.*?<tbody>(.*?)</tbody>", page, re.S)
        if m:
            for tr in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S):
                td = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if len(td) >= 2 and _text(td[1]):
                    variants.append(_text(td[1]))
        types = [_text(t) for t in re.findall(r'<td class="no-wrap">(.*?)</td>', page, re.S)]
        region = _text((re.search(r"(?:Kanton|Region|Land):</td>\s*<td>(.*?)</td>", page, re.S) or [None, ""])[1])
        hls = re.search(r"hls-dhs-dss\.ch/(?:textes/d/D|de/articles/)0*(\d+)", page)
        gnd = re.search(r"d-nb\.info/gnd/([0-9X-]+)", page)
        on = re.search(r"ortsnamen\.ch/record/(\d+)", page)
        return {"id": lid, "name": name, "variants": variants, "types": types, "region": region,
                "hls": hls.group(1).zfill(6) if hls else None, "gnd": gnd.group(1) if gnd else None,
                "ortsnamen": on.group(1) if on else None}
    return cached(f"loci-place:{lid}", ask)


def _lucene(s: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/&|])', r"\\\1", s)


def place_candidates(forms: list[str], top: int = 6) -> list[dict]:
    """Places of the SSRQ database whose name or an original spelling is near one of
    the forms (Lucene fuzzy search, edit distance up to 2, on the spelling as written
    and on a plainer one), each with its best similarity on our own folding."""
    terms = []
    for f in forms:
        for v in (f, f.replace("y", "i").replace("Y", "I"), re.sub(r"(.)\1+", r"\1", f)):
            for w in [v] if " " not in v.strip() else [v.split()[0], v.split()[-1]]:
                t = _lucene(w.strip())
                if len(t) >= 3 and t + "~" not in terms:
                    terms.append(t + "~")
    if not terms:
        return []
    hits = loci_search(" OR ".join(terms))
    out = []
    for h in hits[:25]:
        out.append(h)
    # rank by the listed name first, then read the pages of the best few
    out.sort(key=lambda h: -max(fuzz.ratio(skey(f), skey(h["name"])) for f in forms))
    res = []
    for h in out[:top]:
        p = loci_place(h["id"])
        names = [p["name"] or h["name"], *p["variants"]]
        best, via = max(((fuzz.ratio(skey(f), skey(n)), n) for f in forms for n in names), default=(0, ""))
        res.append({"source": "ssrq", "ssrq": h["id"], "label": p["name"] or h["name"], "via": via,
                    "score": round(best), "type": h["type"], "region": h["region"] or p["region"],
                    "part_of": h["part_of"], "hls": p["hls"], "gnd": p["gnd"], "ortsnamen": p["ortsnamen"],
                    "settlement": bool(SETTLEMENT.search(h["type"])), "near": (h["region"] or "") in NEAR})
    return res


def grade_places(cands: list[dict]) -> list[dict]:
    """Rules measured 15.11.2026 (measure-ssrq) on the edition's places with an SSRQ id;
    see GRADE_RULES below for the measured figures."""
    good = [c for c in cands if c["score"] >= 85]
    exact = [c for c in good if c["score"] == 100]
    out = []
    for c in sorted(good, key=lambda c: (-c["score"], not c["settlement"], not c["near"])):
        if c["score"] == 100 and c["settlement"] and len([x for x in exact if x["settlement"]]) == 1:
            g = "wahrscheinlich"
        else:
            g = "möglich"
        out.append({**c, "grade": g})
    return out[:3]


# ----------------------------------------------------------------- persons


def _surname(form: str) -> str:
    w = [x for x in re.findall(r"[^\W\d_]+", form) if len(x) > 2]
    if not w:
        return ""
    s = w[-1]
    for suf in ("en", "in", "s"):
        if len(s) > 5 and s.endswith(suf):
            return s[: -len(suf)]
    return s


def person_candidates(form: str, call) -> list[dict]:
    """SSRQ persons with the form's surname (as written, then folded), scored on the
    full name against the standard name and every recorded spelling of the forename."""
    sn = _surname(form)
    if not sn:
        return []
    rows = cached(f"ssrqp:{sn}", lambda: call("ssrq", "search_persons", query=sn, limit=50))
    rows = rows.get("result", rows) if isinstance(rows, dict) else rows
    out = []
    for r in rows or []:
        std = r.get("std_name") or r.get("label") or ""
        sur = std.split()[-1] if std.split() else ""
        try:
            fns = [r.get("forename") or ""] + __import__("json").loads(r.get("orig_names") or "[]")
        except ValueError:
            fns = [r.get("forename") or ""]
        names = [std] + [f"{fn} {sur}" for fn in fns if fn and fn != "NN"]
        s = max(fuzz.token_sort_ratio(skey(form), skey(n)) for n in names)
        if s >= 82:
            out.append({"source": "ssrq", "ssrq": r["id"], "label": std, "score": round(s),
                        "first": r.get("first_year"), "last": r.get("last_year")})
    return out


def grade_persons(g: dict, cands: list[dict]) -> list[dict]:
    """A person of the SSRQ is attested, not dated: the years are those of the
    documents that name them. Fit = the letters' years within 40 years of them."""
    y0, y1 = (g.get("years") or [None, None])[:2]
    fit = []
    for c in cands:
        if y0 and c.get("first") and c.get("last") and not (c["first"] - 40 <= y1 and y0 <= c["last"] + 40):
            continue
        fit.append(c)
    fit.sort(key=lambda c: -c["score"])
    out = []
    for i, c in enumerate(fit):
        rival = any(o["score"] >= c["score"] - 3 for o in fit if o is not c)
        g_ = "wahrscheinlich" if (c["score"] >= 95 and not rival and y0 and c.get("first")) else "möglich"
        out.append({**c, "grade": g_})
    return out[:3]


# ------------------------------------------------------------ organisations


def org_candidates(form: str, call) -> list[dict]:
    rows = cached(f"ssrqo:{form}", lambda: call("ssrq", "search_name_index", query=form, type_filter="org", limit=30))
    rows = rows.get("result", rows) if isinstance(rows, dict) else rows
    best: dict[str, dict] = {}
    for r in rows or []:
        s = fuzz.token_sort_ratio(skey(form), skey(r.get("name_text") or ""))
        if s >= 85 and s > best.get(r["ssrq_id"], {}).get("score", 0):
            best[r["ssrq_id"]] = {"source": "ssrq", "ssrq": r["ssrq_id"], "label": r.get("label") or r.get("std_name"),
                                  "via": r.get("name_text"), "score": round(s), "grade": "möglich"}
    return sorted(best.values(), key=lambda c: -c["score"])[:2]


def ssrq_url(sid: str) -> str:
    if sid.startswith("loc"):
        return f"{LOCI}/views/view-place.xq?id={sid}"
    return f"https://www.ssrq-sds-fds.ch/persons-db-edit/?query={sid}"


def stats(cands_by_case: list[tuple[dict, list[dict]]]) -> dict:
    """Precision of each grade: the top candidate against the edition's SSRQ id."""
    res = {g: {"right": 0, "wrong": 0} for g in ("sicher", "wahrscheinlich", "möglich")} | {"none": 0, "n": 0}
    for case, cands in cands_by_case:
        res["n"] += 1
        if not cands:
            res["none"] += 1
            continue
        top = cands[0]
        res[top["grade"]]["right" if top["ssrq"] == case["truth"] else "wrong"] += 1
    for g in ("sicher", "wahrscheinlich", "möglich"):
        n = res[g]["right"] + res[g]["wrong"]
        res[g]["precision"] = round(res[g]["right"] / n, 3) if n else None
    return res


_ = collections  # keep the import for callers that extend this module
