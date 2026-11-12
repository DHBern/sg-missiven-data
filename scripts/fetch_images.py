"""Fetch every page image of the edition from the IIIF server, once.

The images are not in this repository; they are served by
media.sources-online.org under the identifier ``sg-missiven!<facs>``. A
reader needs a local copy, the viewer does not (it reads IIIF directly).

    python scripts/fetch_images.py --out <dir> [--size 1500,]

Throttled on purpose: this is somebody else's public image server.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

IIIF = "https://media.sources-online.org/cantaloupe/iiif/2/sg-missiven!{facs}/full/{size}/0/default.jpg"
PB = re.compile(r'<pb\b[^>]*\bfacs="([^"]+)"')


def facs_list(data_dir: Path) -> list[str]:
    seen: dict[str, None] = {}
    for xml in sorted(data_dir.glob("*.xml")):
        for facs in PB.findall(xml.read_text(encoding="utf-8")):
            seen.setdefault(facs, None)
    return list(seen)


def get(url: str, retries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sg-missiven-readings/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{url}: {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1500,")
    ap.add_argument("--delay", type=float, default=0.25)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = facs_list(Path(args.data))
    missing = []
    for i, facs in enumerate(pages, 1):
        dest = out / (Path(facs).stem + ".jpg")
        if dest.exists() and dest.stat().st_size > 0:
            continue
        try:
            dest.write_bytes(get(IIIF.format(facs=facs, size=args.size)))
        except Exception as e:  # noqa: BLE001 — report and go on
            missing.append(facs)
            print(f"  missing {facs}: {e}", file=sys.stderr)
        if i % 100 == 0:
            print(f"{i}/{len(pages)}", flush=True)
        time.sleep(args.delay)
    print(f"done: {len(pages) - len(missing)}/{len(pages)} images, {len(missing)} missing")
    (out / "_missing.txt").write_text("\n".join(missing) + ("\n" if missing else ""))


if __name__ == "__main__":
    main()
