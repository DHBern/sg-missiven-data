#!/usr/bin/env python3
"""Download the files of a Dropbox shared folder from a manifest, gently.

    python3 scripts/dropbox_fetch.py manifest.tsv DEST [--gb-per-day 15] [--derive-to DIR --long-edge 1500]

manifest.tsv: filename <TAB> bytes <TAB> shared-link href (…&dl=0), as listed
by the shared folder's own listing. Each file is fetched with dl=1 into DEST
through a temporary name; a file already in DEST at the listed size is
skipped, so the script resumes. A daily byte budget keeps the shared link
below Dropbox's bandwidth limit for its owner: when the budget is spent, it
sleeps until 24 h after the day's first download.

With --derive-to, every original (fetched now or already present) also gets a
working copy there: long edge --long-edge px, JPEG quality 85, named
<stem>.jpg. That copy is what the readers read and what an image window
would serve; the original stays untouched.
"""
import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("dest")
    ap.add_argument("--gb-per-day", type=float, default=15.0)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--derive-to", default=None)
    ap.add_argument("--long-edge", type=int, default=1500)
    args = ap.parse_args()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    rows = []
    for line in Path(args.manifest).read_text(encoding="utf-8").splitlines():
        name, size, href = line.split("\t")
        rows.append((name, int(size), href))
    derive_dir = Path(args.derive_to) if args.derive_to else None
    if derive_dir:
        derive_dir.mkdir(parents=True, exist_ok=True)

    def derive(name: str) -> None:
        if not derive_dir:
            return
        out = derive_dir / (Path(name).stem + ".jpg")
        if out.exists() and out.stat().st_size > 0:
            return
        from PIL import Image
        with Image.open(dest / name) as im:
            im = im.convert("RGB")
            im.thumbnail((args.long_edge, args.long_edge), Image.LANCZOS)
            tmp_out = out.with_suffix(".part.jpg")
            im.save(tmp_out, "JPEG", quality=85, optimize=True)
        os.replace(tmp_out, out)

    for name, size, _ in rows:
        if (dest / name).exists() and (dest / name).stat().st_size == size:
            try:
                derive(name)
            except Exception as e:  # noqa: BLE001
                print(f"  derive {name}: {e}", flush=True)
    budget = args.gb_per_day * 1e9
    day_start, spent, done, failed = time.time(), 0.0, 0, 0
    todo = [r for r in rows if not ((dest / r[0]).exists() and (dest / r[0]).stat().st_size == r[1])]
    print(f"{len(rows)} listed, {len(rows) - len(todo)} present, {len(todo)} to fetch", flush=True)
    for name, size, href in todo:
        if time.time() - day_start >= 86400:
            day_start, spent = time.time(), 0.0
        if spent + size > budget:
            wait = 86400 - (time.time() - day_start)
            print(f"daily budget spent ({spent / 1e9:.1f} GB); sleeping {wait / 3600:.1f} h", flush=True)
            time.sleep(max(0, wait))
            day_start, spent = time.time(), 0.0
        url = href.replace("dl=0", "dl=1")
        tmp = dest / (name + ".part")
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "sg-missiven-readings/1.0"})
                with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as fh:
                    while chunk := r.read(1 << 20):
                        fh.write(chunk)
                if tmp.stat().st_size != size:
                    raise IOError(f"got {tmp.stat().st_size} bytes, listed {size}")
                os.replace(tmp, dest / name)
                spent += size
                try:
                    derive(name)
                except Exception as e:  # noqa: BLE001
                    print(f"  derive {name}: {e}", flush=True)
                done += 1
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {name}: {e}", flush=True)
                time.sleep(10 * (attempt + 1) ** 2)
        else:
            failed += 1
            tmp.unlink(missing_ok=True)
        if (done + failed) % 200 == 0:
            print(f"{done} fetched, {failed} failed, {spent / 1e9:.1f} GB today", flush=True)
        time.sleep(args.delay)
    print(f"finished: {done} fetched, {failed} failed", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
