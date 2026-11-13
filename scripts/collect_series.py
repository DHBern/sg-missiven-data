"""Collect every reading of the series into readings/<reader>.jsonl, on tei.

    python3 scripts/collect_series.py [--sources readings/sources.json] [--dry-run]

Reads the sources named in ``readings/sources.json`` (atr-batch run directories
and jsonl files, see docs/PIPELINE.md) and **merges** them into the readings
already in the repository: a page already there is updated when a source has
it, a page no source has is kept. A reader's page count can therefore only
grow; if a reader would lose pages, nothing is written and the script fails.

Pages are keyed as the viewer keys them: the edition's ``facs`` where the
edition has the page, otherwise ``<image stem>.jpg``. Standard library only, so
it runs with tei's system Python.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PB = re.compile(r'<pb\b[^>]*\bfacs="([^"]+)"')


def facs_by_stem() -> dict[str, str]:
    out: dict[str, str] = {}
    for xml in sorted((ROOT / "data").glob("*.xml")):
        for facs in PB.findall(xml.read_text(encoding="utf-8")):
            out.setdefault(Path(facs).stem, facs)
    return out


def key_for(stem: str, by_stem: dict[str, str]) -> str | None:
    stem = Path(stem).stem if stem.lower().endswith((".jpg", ".jpeg", ".png", ".tif")) else stem
    if stem in by_stem:
        return by_stem[stem]
    return stem + ".jpg" if stem.startswith("StadtASG_Missive_") else None


def from_runs(vlm_test: Path, runs: list[str], by_stem: dict[str, str]) -> dict[str, dict[str, dict]]:
    readers: dict[str, dict[str, dict]] = {}
    for run in runs:
        rdir = vlm_test / run
        if not rdir.is_dir():
            print(f"  run not found: {rdir}", file=sys.stderr)
            continue
        for mdir in sorted(p for p in rdir.iterdir() if p.is_dir() and p.name != "tmp"):
            n = 0
            for path in mdir.glob("*.json"):
                try:
                    rec = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(rec, dict) or rec.get("schema") != "atr-batch/1":
                    continue
                key = key_for(path.stem, by_stem)
                if not key:
                    continue
                readers.setdefault(rec["model"], {})[key] = {
                    "key": key, "text": rec.get("text") or "", "truncated": bool(rec.get("truncated")),
                    "segmented_by": rec.get("segmented_by"), "gateway_version": rec.get("gateway_version"),
                    "run": run}
                n += 1
            print(f"  {run}/{mdir.name}: {n} pages")
    return readers


def from_jsonl(specs: list[dict], by_stem: dict[str, str], readers: dict[str, dict[str, dict]]) -> None:
    for spec in specs:
        n = 0
        for path in sorted(glob.glob(spec["glob"])):
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("text") is None:
                    continue
                key = key_for(rec["key"], by_stem)
                if not key:
                    continue
                readers.setdefault(spec["reader"], {})[key] = {
                    "key": key, "text": rec["text"], "truncated": bool(rec.get("truncated")),
                    "settings": rec.get("settings"), "run": Path(path).parent.name}
                n += 1
        print(f"  {spec['reader']} (jsonl): {n} lines")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=str(ROOT / "readings" / "sources.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = json.loads(Path(a.sources).read_text(encoding="utf-8"))
    by_stem = facs_by_stem()
    found = from_runs(Path(cfg["vlm_test"]), cfg.get("runs", []), by_stem)
    from_jsonl(cfg.get("jsonl", []), by_stem, found)

    order = {f: i for i, f in enumerate(by_stem.values())}
    plan = {}
    for reader, pages in sorted(found.items()):
        path = ROOT / "readings" / f"{reader}.jsonl"
        old = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    old[r["key"]] = r
        merged = {**old, **pages}
        if len(merged) < len(old):
            sys.exit(f"{reader}: would shrink from {len(old)} to {len(merged)} pages; nothing written")
        added = len(set(pages) - set(old))
        changed = sum(1 for k in set(pages) & set(old) if pages[k].get("text") != old[k].get("text"))
        plan[reader] = (path, merged, len(old), added, changed)
    for reader, (path, merged, before, added, changed) in plan.items():
        print(f"{reader}: {before} -> {len(merged)} pages (+{added} new, {changed} changed)")
        if a.dry_run:
            continue
        rows = sorted(merged.values(), key=lambda r: (order.get(r["key"], 1 << 30), r["key"]))
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
