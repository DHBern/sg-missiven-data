"""Similar letters: embeddings, maps in 2D and 3D, and each letter's nearest letters.

After the method of thodel/ssrq_print (ssrq_print/embed.py, embmap.py), for the letters of this series.

    set -a; . ~/.gpustack.env; set +a
    python scripts/build_site.py                 # the letters as the viewer has them (site/data/)
    python scripts/similarity.py embed           # vectors (cached in .cache-sim/, not committed)
    python scripts/similarity.py map             # -> similarity/map_regest.json, map_source.json, neighbours.json

Two sets, one vector per letter each:
  regest   the edition's regest, or the draft regest (scripts/regest.py) of a letter not yet edited;
  source   the letter's text: the edition's transcription, or the joint reading of the machine readings,
           in sections of about 1,500 characters cut at sentence ends; the sections' vectors are averaged.
           Letters with under 300 characters of text (an address leaf alone) get no text vector.
Model: qwen3-embedding-4b on the University of Bern's GPUStack (2,560 dimensions), with an instruction before the
text as Qwen3-Embedding expects. Every vector is cached by model, instruction and text, so a rerun only sends new
texts.

Map: PCA to 50 dimensions, then UMAP (cosine, 30 neighbours, fixed seed) in 2 and in 3 dimensions; HDBSCAN groups
the 2D map, and each group is named by the regest words it has far more often than the whole series (TF-IDF).
Neighbours: per letter the ten letters nearest by the mean of the two cosine similarities (regest and text).

Edited and draft regests are written differently (by the editors, by a language model): colour the map by
"Regest" to see how much of the layout that explains.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"
OUT = ROOT / "similarity"
CACHE = ROOT / ".cache-sim"
MODEL = "qwen3-embedding-4b"
BATCH = 32
CHUNK = 1500
MIN_CHUNK = 80
MIN_TEXT = 300   # an address leaf alone (about 100 characters) would be «similar» to every letter
K = 10
INSTRUCT = {
    "regest": "Instruct: Given the summary of a letter from the archive of the city of St. Gallen (15th-17th "
              "century), find letters on the same matter\nQuery: ",
    "source": "Instruct: Given a passage of a historical German letter, find passages on the same matter\nQuery: ",
}
SENT = re.compile(r"(?<=[.;:!?])\s+(?=\S)")
STOP = set("""der die das und zu in den von mit sich für auf dem des ein eine einen einem einer eines ist sind
wird werden hat haben als an aus bei bitte bittet dass er sie es ihr ihre ihren ihrem ihres sein seine seinen seinem
seiner seines nicht auch so wie um über nach vor wegen durch gegen sowie oder noch nur schon wenn weil damit diese
dieser dieses diesen jener am im zum zur vom beim bis unter ob sei seien habe hätten wolle wollen könne können soll
sollen solle möge mögen teilt mit schreibt bürgermeister rat räte rats rates stadt st gallen gallens herren herr
bürger burgermeister schultheiss vogt brief briefe schreiben schreibens zuschrift ersucht ersuchen dankt danken
informiert berichtet meldet bittet erklärt fordert antwortet""".split())


def load_letters() -> list[dict]:
    ix = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
    out = []
    for l in ix:
        d = json.loads((DATA / "letters" / f"{l['id']}.json").read_text(encoding="utf-8"))
        text = "\n".join((p.get("text") or ((p.get("readings") or {}).get("joint") or {}).get("text") or "").strip()
                         for p in d["pages"]).strip()
        year = None
        m = re.match(r"(\d{4})", l.get("date") or "")
        if m:
            year = int(m.group(1))
        elif isinstance(l.get("year"), int):
            year = l["year"]
        out.append({"id": l["id"], "regest": (l.get("regest") or "").strip(), "text": text, "year": year,
                    "edited": l.get("edited") is not False, "sender": l.get("sender") or [],
                    "recipient": l.get("recipient") or [], "title": l.get("title") or l["id"]})
    return out


def chunks(text: str) -> list[str]:
    cuts = [0] + [m.end() for m in SENT.finditer(text)] + [len(text)]
    spans, start, end = [], 0, 0
    for c in cuts[1:]:
        if c - start > CHUNK and end > start:
            spans.append((start, end))
            start = end
        while c - start > CHUNK:
            cut = text.rfind(" ", start, start + CHUNK)
            cut = cut if cut > start + CHUNK // 2 else start + CHUNK
            spans.append((start, cut))
            start = cut
        end = c
    if end > start:
        spans.append((start, end))
    out = [" ".join(text[a:b].split()) for a, b in spans]
    return [t for t in out if len(t) >= MIN_CHUNK]


# ------------------------------------------------------------------ embeddings

def db() -> sqlite3.Connection:
    CACHE.mkdir(exist_ok=True)
    con = sqlite3.connect(CACHE / "embed.sqlite")
    con.execute("create table if not exists v (h text primary key, v blob)")
    return con


def key(kind: str, text: str) -> str:
    return hashlib.sha256(f"{MODEL}\n{INSTRUCT[kind]}\n{text}".encode()).hexdigest()


def embed_texts(kind: str, texts: list[str]) -> np.ndarray:
    import httpx
    con = db()
    hs = [key(kind, t) for t in texts]
    have = {}
    for i in range(0, len(hs), 500):
        part = hs[i:i + 500]
        for h, v in con.execute(f"select h, v from v where h in ({','.join('?' * len(part))})", part):
            have[h] = v
    todo = [(h, t) for h, t in dict(zip(hs, texts)).items() if h not in have]
    print(f"{kind}: {len(texts)} texts, {len(todo)} to embed", flush=True)
    client = httpx.Client(base_url=os.environ["GPUSTACK_BASE_URL"], timeout=300,
                          headers={"Authorization": f"Bearer {os.environ['GPUSTACK_API_KEY']}"})
    t0 = time.time()
    for i in range(0, len(todo), BATCH):
        part = todo[i:i + BATCH]
        for attempt in range(5):
            try:
                r = client.post("/embeddings", json={"model": MODEL, "input": [INSTRUCT[kind] + t for _, t in part]})
                r.raise_for_status()
                vecs = [e["embedding"] for e in sorted(r.json()["data"], key=lambda e: e["index"])]
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 4:
                    raise
                print(f"  retry after {type(e).__name__}: {str(e)[:120]}", flush=True)
                time.sleep(15 * (attempt + 1))
        rows = []
        for (h, _), v in zip(part, vecs):
            a = np.asarray(v, dtype=np.float32)
            a /= np.linalg.norm(a) + 1e-9
            rows.append((h, a.astype(np.float16).tobytes()))
            have[h] = rows[-1][1]
        con.executemany("insert or replace into v values (?, ?)", rows)
        con.commit()
        done = i + len(part)
        if done % (BATCH * 20) == 0 or done == len(todo):
            print(f"  {done}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    return np.stack([np.frombuffer(have[h], dtype=np.float16) for h in hs]).astype(np.float32)


def vectors(kind: str, letters: list[dict]) -> tuple[list[str], np.ndarray]:
    """One unit vector per letter that has the text (regest, or source sections averaged)."""
    if kind == "regest":
        ls = [l for l in letters if l["regest"]]
        return [l["id"] for l in ls], embed_texts(kind, [l["regest"] for l in ls])
    owner, texts = [], []
    for l in letters:
        if len(l["text"]) < MIN_TEXT:
            continue
        for c in chunks(l["text"]):
            owner.append(l["id"])
            texts.append(c)
    X = embed_texts(kind, texts)
    ids = list(dict.fromkeys(owner))
    pos = {p: i for i, p in enumerate(ids)}
    V = np.zeros((len(ids), X.shape[1]), dtype=np.float32)
    for o, x in zip(owner, X):
        V[pos[o]] += x
    V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
    return ids, V


def save(kind: str, ids: list[str], V: np.ndarray) -> None:
    np.save(CACHE / f"{kind}.npy", V.astype(np.float16))
    (CACHE / f"{kind}.json").write_text(json.dumps(ids))


def load(kind: str) -> tuple[list[str], np.ndarray]:
    return json.loads((CACHE / f"{kind}.json").read_text()), np.load(CACHE / f"{kind}.npy").astype(np.float32)


# ------------------------------------------------------------------ maps and neighbours

def words(s: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-zÄÖÜäöüß]{5,}", s) if w.lower() not in STOP]


def group_names(groups: np.ndarray, regests: list[str], top: int = 3) -> dict[str, str]:
    ws = [set(words(r)) for r in regests]
    lower = [{w.lower() for w in x} for x in ws]
    df = Counter(w for x in lower for w in x)
    form: dict[str, Counter] = {}
    for x in ws:
        for w in x:
            form.setdefault(w.lower(), Counter())[w] += 1
    n = len(ws)
    out = {}
    for g in sorted(set(groups.tolist()) - {-1}):
        idx = np.where(groups == g)[0]
        tf = Counter(w for i in idx for w in lower[i])
        score = {w: (c / len(idx)) * math.log(n / df[w]) for w, c in tf.items() if c >= 3}
        best = sorted(score.items(), key=lambda x: -x[1])[:top]
        out[str(g)] = ", ".join(form[w].most_common(1)[0][0] for w, _ in best)
    return out


def layout(V: np.ndarray, seed: int = 42) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import umap
    from sklearn.cluster import HDBSCAN
    from sklearn.decomposition import PCA
    Y = PCA(n_components=50, random_state=seed).fit_transform(V)
    xy = umap.UMAP(n_components=2, n_neighbors=30, min_dist=0.1, metric="cosine", random_state=seed).fit_transform(Y)
    xyz = umap.UMAP(n_components=3, n_neighbors=30, min_dist=0.1, metric="cosine", random_state=seed).fit_transform(Y)
    groups = HDBSCAN(min_cluster_size=25, min_samples=8).fit_predict(xy)
    lim = lambda a: (a - a.min(0)) / (a.max(0) - a.min(0) + 1e-9)
    return lim(xy), lim(xyz), groups


def partners() -> dict[str, tuple[str, str]]:
    """letter id -> (partner place, region), from the page «Korrespondenz» (scripts/correspondence.py)."""
    p = DATA / "correspondence.json"
    if not p.exists():
        return {}
    c = json.loads(p.read_text(encoding="utf-8"))
    return {lid: (c["partners"][i]["name"], c["partners"][i]["region"]) for lid, i in c["letters"].items()}


def direction(l: dict) -> str:
    sg = re.compile(r"st\.?\s*gall", re.I)
    if any(sg.search(x) for x in l["recipient"]):
        return "an St. Gallen"
    if any(sg.search(x) for x in l["sender"]):
        return "von St. Gallen"
    return "andere"


def build_maps(letters: list[dict]) -> None:
    by_id = {l["id"]: l for l in letters}
    part = partners()
    OUT.mkdir(exist_ok=True)
    sets = {}
    for kind in ("regest", "source"):
        ids, V = load(kind)
        sets[kind] = (ids, V)
        t0 = time.time()
        xy, xyz, groups = layout(V)
        rows = []
        for i in ids:
            l = by_id[i]
            who = "; ".join(l["sender"][:1]) + " → " + "; ".join(l["recipient"][:1]) if l["sender"] or l["recipient"] else ""
            pl, rg = part.get(i, ("", ""))
            rows.append([i, l["year"], l["title"].replace("StadtASG Missive, ", ""), 1 if l["edited"] else 0,
                         direction(l), pl, rg, who[:120], l["regest"][:160]])
        out = {"set": kind, "model": MODEL,
               "fields": ["id", "year", "no", "edited", "direction", "partner", "region", "who", "regest"],
               "rows": rows, "xy": np.round(xy, 4).tolist(), "xyz": np.round(xyz, 4).tolist(),
               "group": groups.tolist(), "groups": group_names(groups, [by_id[i]["regest"] for i in ids])}
        (OUT / f"map_{kind}.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                                              encoding="utf-8")
        print(f"{kind}: {len(ids)} letters, {len(out['groups'])} groups ({int((groups == -1).sum())} without), "
              f"{time.time() - t0:.0f}s", flush=True)
    # neighbours: mean of the two similarities where a letter has both, else the one it has
    (ri, R), (si, S) = sets["regest"], sets["source"]
    ids = sorted(set(ri) | set(si), key=lambda x: [by_id[x]["year"] or 9999, x])
    rp, sp = {x: k for k, x in enumerate(ri)}, {x: k for k, x in enumerate(si)}
    SR = R @ R.T
    SS = S @ S.T
    nb = {}
    for x in ids:
        a, b = rp.get(x), sp.get(x)
        cand = set()
        if a is not None:
            cand |= {ri[k] for k in np.argpartition(-SR[a], 40)[:40]}
        if b is not None:
            cand |= {si[k] for k in np.argpartition(-SS[b], 40)[:40]}
        cand.discard(x)
        scored = []
        for y in cand:
            r = float(SR[a, rp[y]]) if a is not None and y in rp else None
            s = float(SS[b, sp[y]]) if b is not None and y in sp else None
            vals = [v for v in (r, s) if v is not None]
            scored.append((sum(vals) / len(vals), y, r, s))
        scored.sort(reverse=True)
        nb[x] = [[y, None if r is None else round(r, 3), None if s is None else round(s, 3)]
                 for _, y, r, s in scored[:K]]
    (OUT / "neighbours.json").write_text(json.dumps(
        {"about": "Per letter the ten nearest letters: [id, regest similarity, text similarity] (cosine, "
                  f"{MODEL}); ranked by their mean (scripts/similarity.py).", "neighbours": nb},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"neighbours: {len(nb)} letters -> {OUT / 'neighbours.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["embed", "map", "all"])
    a = ap.parse_args()
    if not (DATA / "index.json").exists():
        sys.exit("no site/data/index.json: run scripts/build_site.py first")
    letters = load_letters()
    if a.cmd in ("embed", "all"):
        CACHE.mkdir(exist_ok=True)
        for kind in ("regest", "source"):
            ids, V = vectors(kind, letters)
            save(kind, ids, V)
            print(f"{kind}: {len(ids)} letters with a vector")
    if a.cmd in ("map", "all"):
        build_maps(letters)


if __name__ == "__main__":
    main()
