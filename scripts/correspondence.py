"""Who wrote to St. Gallen, and when: the data of the viewer's page «Korrespondenz».

Called by build_site.py, which writes site/data/correspondence.json.

Every letter of the series between St. Gallen and someone else gets one
partner: the sender of a letter to the city, the recipient of a letter from it.
The partner is placed by the town its name gives ("Bürgermeister und Rat zu
Lindau" -> Lindau), with the coordinates of the edition's place register. A few
frequent towns the register has no coordinates for are listed below (EXTRA).
Partners named without a town (single persons, "Eidgenössische Räte") are left
out of the counts and counted as such.

The year is the edition's date, or for a letter not yet edited the year of its
regest draft (scripts/regest.py), which is uncertain. The page therefore shows
decades and windows of years, never single years.

The output, per partner: name, coordinates, region, distance from St. Gallen
and the forms of its name; rows of [partner, year, direction, edited, letters];
the letters per year; and letter id -> partner, for the letter list's filter.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

SG = (47.4245, 9.3767)
SG_RE = re.compile(r"st\.?\s*gall", re.I)

# towns the place register names without coordinates (approximate town centres)
EXTRA = {
    "Ravensburg": (47.782, 9.611, "Deutschland"), "Wangen": (47.686, 9.834, "Deutschland"),
    "Leutkirch": (47.826, 10.022, "Deutschland"), "Biberach": (48.098, 9.787, "Deutschland"),
    "Stein am Rhein": (47.659, 8.859, "Schweiz"), "Radolfzell": (47.737, 8.970, "Deutschland"),
    "Ybbs": (48.176, 15.083, "Österreich"), "Frankfurt": (50.110, 8.682, "Deutschland"),
    "Esslingen": (48.739, 9.307, "Deutschland"), "Villingen": (48.060, 8.459, "Deutschland"),
}
# spellings that name a town under another form
ALIAS = {"Bischofzell": "Bischofszell", "Äusseren Rhoden": "Appenzell Ausserrhoden",
         "Äusseren Roden": "Appenzell Ausserrhoden", "äußeren Roden": "Appenzell Ausserrhoden"}
EXTRA_ALIAS = {"Appenzell Ausserrhoden": (47.408, 9.465, "Schweiz")}  # seat: Trogen

REGIONS = ["Eidgenossenschaft und Zugewandte", "Bodensee und Oberschwaben", "Reich und weiter"]

PATTERNS = [
    r"(Äusseren Rhoden|Äusseren Roden|äußeren Roden)",
    r"\b(?:zu|in|von|der Stadt|der Reichsstadt|Reichsstadt|Stadt)\s+([A-ZÄÖÜ][\w.\-]+(?: am [A-ZÄÖÜ]\w+| [A-ZÄÖÜ]\w+)?)",
    r"^([A-ZÄÖÜ][\w.\-]+)$",
]


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)).strip()


def km(a: tuple[float, float], b: tuple[float, float]) -> int:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(2 * 6371 * math.asin(math.sqrt(h)))


def gazetteer(reg: dict[str, dict]) -> dict[str, dict]:
    """folded town name -> coordinates, from the place register (the entry most often named wins)."""
    geo: dict[str, dict] = {}
    for rid, e in reg.items():
        if e.get("kind") != "place" or not e.get("geo") or e.get("auto"):
            continue
        name = re.sub(r"\s*\(.*\)$", "", e["name"]).strip()
        n = len(e.get("occ", []))
        k = fold(name)
        if k not in geo or n > geo[k]["n"]:
            geo[k] = {"name": name, "lat": e["geo"][0], "lon": e["geo"][1], "n": n,
                      "country": e.get("country") or "", "ref": rid}
    for name, (lat, lon, country) in {**EXTRA, **EXTRA_ALIAS}.items():
        geo.setdefault(fold(name), {"name": name, "lat": lat, "lon": lon, "n": 0, "country": country, "ref": None})
    for a, b in ALIAS.items():
        if fold(b) in geo:
            geo[fold(a)] = geo[fold(b)]
    return geo


def place_of(name: str, geo: dict[str, dict]) -> dict | None:
    for p in PATTERNS:
        for m in re.finditer(p, name):
            cand = m.group(1).strip().rstrip(".")
            for c in (cand, cand.split(" ")[0]):
                if fold(c) in geo:
                    return geo[fold(c)]
    return None


def region(g: dict) -> str:
    if g["country"] == "Schweiz":
        return REGIONS[0]
    if 8.6 <= g["lon"] <= 10.4 and 47.3 <= g["lat"] <= 48.15 and g["country"] in ("Deutschland", "Österreich", "Liechtenstein"):
        return REGIONS[1]
    return REGIONS[2]


def year_of(l: dict) -> int | None:
    m = re.match(r"(\d{4})", l.get("date") or "")
    if m:
        return int(m.group(1))
    y = l.get("year")
    return int(y) if isinstance(y, int) or (isinstance(y, str) and y.isdigit()) else None


def build(index: list[dict], reg: dict[str, dict]) -> dict:
    geo = gazetteer(reg)
    partners: dict[str, dict] = {}
    counts: Counter = Counter()
    totals: Counter = Counter()
    letter_partner: dict[str, str] = {}
    unplaced: Counter = Counter()
    for l in index:
        y = year_of(l)
        if not y:
            continue
        ed = 1 if l.get("edited") is not False else 0
        totals[(y, ed)] += 1
        snd = [x for x in l.get("sender") or [] if x]
        rcp = [x for x in l.get("recipient") or [] if x]
        other_s = [x for x in snd if not SG_RE.search(x)]
        other_r = [x for x in rcp if not SG_RE.search(x)]
        if any(SG_RE.search(x) for x in rcp) and other_s:
            who, d = other_s[0], 0
        elif any(SG_RE.search(x) for x in snd) and other_r:
            who, d = other_r[0], 1
        else:
            continue
        g = place_of(who, geo)
        if not g:
            unplaced[who] += 1
            continue
        key = g["name"]
        if key not in partners:
            partners[key] = {"name": key, "lat": round(g["lat"], 4), "lon": round(g["lon"], 4), "ref": g["ref"],
                             "region": region(g), "km": km(SG, (g["lat"], g["lon"])), "forms": Counter()}
        partners[key]["forms"][who] += 1
        counts[(key, y, d, ed)] += 1
        letter_partner[l["id"]] = key
    total_of = Counter()
    for (k, _, _, _), n in counts.items():
        total_of[k] += n
    order = sorted(partners, key=lambda k: -total_of[k])
    idx = {k: i for i, k in enumerate(order)}
    out_partners = []
    for k in order:
        p = partners[k]
        out_partners.append({**p, "forms": [f for f, _ in p["forms"].most_common(3)], "n": total_of[k]})
    return {
        "sg": {"name": "St. Gallen", "lat": SG[0], "lon": SG[1]},
        "regions": REGIONS,
        "partners": out_partners,
        "rows": [[idx[k], y, d, ed, n] for (k, y, d, ed), n in sorted(counts.items())],
        "totals": [[y, ed, n] for (y, ed), n in sorted(totals.items())],
        "letters": {lid: idx[k] for lid, k in letter_partner.items()},
        "unplaced": {"letters": sum(unplaced.values()), "top": [[w, n] for w, n in unplaced.most_common(10)]},
    }
