"""Turn the raw run outputs into one readings file per reader.

    python scripts/collect_readings.py <raw dir>

``<raw dir>`` holds what the runs on tei wrote: ``readings_<model>.jsonl`` from
the external reader (Gemini) and ``<run>/<model>/<image stem>.json`` from the
gateway batch runner. The batch runner names a page by its image file, which
was fetched as ``<facs stem>.jpg``; the edition's ``facs`` is restored here.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PB = re.compile(r'<pb\b[^>]*\bfacs="([^"]+)"')


def main() -> None:
    raw = Path(sys.argv[1])
    by_stem = {}
    for xml in sorted((ROOT / "data").glob("*.xml")):
        for facs in PB.findall(xml.read_text(encoding="utf-8")):
            by_stem.setdefault(Path(facs).stem, facs)
    out_dir = ROOT / "readings"
    readers: dict[str, dict[str, dict]] = {}
    for path in sorted(raw.rglob("readings_*.jsonl")):
        model = path.stem.removeprefix("readings_")
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("text") is None:
                continue
            readers.setdefault(model, {})[rec["key"]] = {
                "key": rec["key"], "text": rec["text"], "truncated": rec.get("truncated", False),
                "settings": rec.get("settings")}
    for path in sorted(raw.rglob("*.json")):
        if path.name in ("energy.json",) or path.parent.parent == raw.parent:
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != "atr-batch/1":
            continue
        facs = by_stem.get(path.stem)
        if not facs:
            # A page the edition does not contain (a letter not yet edited): it is
            # keyed by its working copy's name, <stem>.jpg, which is also the
            # name the image window serves it under.
            if not path.stem.startswith("StadtASG_Missive_"):
                print(f"  not a Missiven page: {path}", file=sys.stderr)
                continue
            facs = path.stem + ".jpg"
        readers.setdefault(rec["model"], {})[facs] = {
            "key": facs, "text": rec.get("text") or "", "truncated": bool(rec.get("truncated")),
            "segmented_by": rec.get("segmented_by"), "gateway_version": rec.get("gateway_version")}
    for model, pages in readers.items():
        order = {f: i for i, f in enumerate(by_stem.values())}
        rows = sorted(pages.values(), key=lambda r: order.get(r["key"], 1 << 30))
        (out_dir / f"{model}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(f"{model}: {len(rows)} pages")


if __name__ == "__main__":
    main()
