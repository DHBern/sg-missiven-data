"""Collect every reading of the series into readings/<reader>.jsonl, on tei.

    python3 scripts/collect_series.py [--sources readings/sources.json] [--dry-run]

Reads the sources named in ``readings/sources.json`` (atr-batch run directories
and jsonl files, see docs/PIPELINE.md) and **merges** them into the readings
already in the repository: a page already there is updated when a source has
it, a page no source has is kept. A reader's page count can therefore only
grow; if a reader would lose pages, nothing is written and the script fails.

Line readers (kraken, TrOCR) also carry the box of every line they read. Those
boxes go to ``readings/lines.jsonl``, one page per line, merged the same way:
the box coordinates as fractions of the image (``sources.json``'s
``line_images`` is where the images they were read from lie, for their size),
each box's baseline, and for each line reader which box each of its text
lines belongs to. The
viewer links the lines of every other reading to these boxes (docs/LINES.md).

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


def jpeg_size(path: Path) -> tuple[int, int] | None:
    """Width and height from a JPEG's frame header, standard library only."""
    try:
        with path.open("rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                while b == b"\xff":
                    b = f.read(1)
                if not b:
                    return None
                marker = b[0]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    continue
                size = int.from_bytes(f.read(2), "big")
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    f.read(1)
                    h = int.from_bytes(f.read(2), "big")
                    w = int.from_bytes(f.read(2), "big")
                    return (w, h) if w and h else None
                f.seek(size - 2, 1)
    except OSError:
        return None


def line_record(key: str, by_model: dict[str, dict], images: Path | None) -> dict | None:
    """One page's boxes, shared by its line readers, with each reader's line order.

    The line readers of a run share one segmentation: the same boxes, in an order
    of their own. The boxes are kept in the order of a TrOCR reader where there is
    one (its segmentation also found the text regions, so its order is the
    reading order), and each reader gets the box index of each of its lines."""
    models = sorted(by_model, key=lambda m: (0 if "trocr" in m else 1, m))
    first = by_model[models[0]]
    size = jpeg_size(images / first["image"]) if images else None
    if not size:
        return None
    w, h = size
    boxes = [tuple(b) for b in first["boxes"]]
    at = {b: i for i, b in enumerate(boxes)}
    readers = {}
    for m in models:
        idx = [at.get(tuple(b), -1) for b in by_model[m]["boxes"]]
        if idx == list(range(len(boxes))):
            readers[m] = None  # same order as the boxes
        elif -1 not in idx:
            readers[m] = idx
    q = lambda v, d: round(min(max(v / d, 0), 1), 4)  # noqa: E731
    return {"key": key, "size": [w, h], "segmented_by": first["segmented_by"],
            "boxes": [[q(x0, w), q(y0, h), q(x1, w), q(y1, h)] for x0, y0, x1, y1 in boxes],
            # each box's baseline, first and last point: where the writing stands
            "baselines": [[q(b[0], w), q(b[1], h), q(b[2], w), q(b[3], h)] if b else None
                          for b in first["baselines"]],
            "readers": readers}


def from_runs(vlm_test: Path, runs: list[str], by_stem: dict[str, str],
              geometry: dict[str, dict[str, dict]] | None = None) -> dict[str, dict[str, dict]]:
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
                lines = rec.get("lines") or []
                if geometry is not None and lines and all(ln.get("bbox") for ln in lines):
                    src = rec.get("source") or {}
                    geometry.setdefault(key, {})[rec["model"]] = {
                        "boxes": [[round(v) for v in ln["bbox"]] for ln in lines],
                        "baselines": [[round(v) for v in (ln["baseline"][0] + ln["baseline"][-1])]
                                      if len(ln.get("baseline") or []) >= 2 else None for ln in lines],
                        "image": src.get("working_copy") or src.get("name") or "",
                        "segmented_by": rec.get("segmented_by")}
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
    geometry: dict[str, dict[str, dict]] = {}
    found = from_runs(Path(cfg["vlm_test"]), cfg.get("runs", []), by_stem, geometry)
    from_jsonl(cfg.get("jsonl", []), by_stem, found)
    images = Path(cfg["line_images"]) if cfg.get("line_images") else None
    lines = {}
    for key, by_model in geometry.items():
        rec = line_record(key, by_model, images)
        if rec:
            lines[key] = rec
    if geometry:
        print(f"  line boxes: {len(lines)} of {len(geometry)} pages (the others: image size unknown)")

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
    if lines:
        path = ROOT / "readings" / "lines.jsonl"
        old = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    old[r["key"]] = r
        merged = {**old, **lines}
        added = len(set(lines) - set(old))
        changed = sum(1 for k in set(lines) & set(old) if lines[k] != old[k])
        plan["lines"] = (path, merged, len(old), added, changed)
    for reader, (path, merged, before, added, changed) in plan.items():
        print(f"{reader}: {before} -> {len(merged)} pages (+{added} new, {changed} changed)")
        if a.dry_run:
            continue
        rows = sorted(merged.values(), key=lambda r: (order.get(r["key"], 1 << 30), r["key"]))
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
