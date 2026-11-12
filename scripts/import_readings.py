"""Import one engine's page transcriptions as a reader of the viewer.

    python scripts/import_readings.py <reader id> <folder> [--format auto|txt|pagexml|alto|jsonl]

``<folder>`` holds one file per page image. A file is matched to the edition's
page by the **stem of its name**: ``StadtASG_Missive_1_1.txt``,
``StadtASG_Missive_1_1.xml`` or ``StadtASG_Missive_1_1.jpg.txt`` all belong to
``<pb facs="StadtASG_Missive_1_1.JPG"/>`` — case and image extension do not
matter. A ``jsonl`` input instead carries ``{"key": ..., "text": ...}`` per line
and is matched the same way by ``key``.

* ``txt``: the file is the page text, one line per line.
* ``pagexml``: PAGE XML (Transkribus, eScriptorium, kraken, Loghi …); the text
  of every ``TextLine`` in reading order (``ReadingOrder`` if present, else
  document order), from its ``TextEquiv`` with the lowest ``@index``.
* ``alto``: ALTO XML; ``String/@CONTENT`` per ``TextLine``.

Writes ``readings/<reader id>.jsonl`` in the edition's page order and reports
which files matched no page and how many pages have no reading. Then add the
reader to ``readings/readers.json`` (see docs/ADDING_READINGS.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
PB = re.compile(r'<pb\b[^>]*\bfacs="([^"]+)"')
IMAGE_EXT = re.compile(r"\.(jpe?g|png|tiff?|jp2|webp)$", re.I)
ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def page_index() -> dict[str, str]:
    """normalised stem -> facs, in edition order."""
    out: dict[str, str] = {}
    for xml in sorted((ROOT / "data").glob("*.xml")):
        for facs in PB.findall(xml.read_text(encoding="utf-8")):
            out.setdefault(stem(facs), facs)
    return out


def stem(name: str) -> str:
    name = Path(name).name
    for _ in range(2):  # "x.jpg.txt" -> "x"
        name = re.sub(r"\.(txt|xml|json)$", "", name, flags=re.I)
        name = IMAGE_EXT.sub("", name)
    return name.lower()


def local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def from_pagexml(path: Path) -> str:
    root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
    regions = {el.get("id"): el for el in root.iter() if local(el).endswith("Region")}
    order = [el.get("regionRef") for el in root.iter() if local(el) == "RegionRefIndexed"]
    order_idx = [el.get("index") for el in root.iter() if local(el) == "RegionRefIndexed"]
    if order:
        ordered = [regions[r] for _, r in sorted(zip(map(int, order_idx), order)) if r in regions]
        rest = [r for k, r in regions.items() if k not in set(order)]
        containers = ordered + rest
    else:
        containers = [root]
    lines, seen = [], set()
    tree = root.getroottree()
    for c in containers:
        for tl in c.iter():
            if local(tl) != "TextLine":
                continue
            path = tree.getpath(tl)  # a nested region must not give a line twice
            if path in seen:
                continue
            seen.add(path)
            equivs = [e for e in tl if local(e) == "TextEquiv"]
            if not equivs:
                continue
            best = min(equivs, key=lambda e: int(e.get("index") or 0))
            uni = next((u for u in best if local(u) == "Unicode"), None)
            lines.append("".join(uni.itertext()) if uni is not None else "")
    return "\n".join(lines)


def from_alto(path: Path) -> str:
    root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
    lines = []
    for tl in root.iter():
        if local(tl) == "TextLine":
            words = [s.get("CONTENT", "") for s in tl if local(s) == "String"]
            lines.append(" ".join(words))
    return "\n".join(lines)


def sniff(path: Path) -> str:
    if path.suffix.lower() == ".jsonl":
        return "jsonl"
    if path.suffix.lower() == ".txt":
        return "txt"
    head = path.read_bytes()[:2000].decode("utf-8", "ignore")
    if "PcGts" in head:
        return "pagexml"
    if "<alto" in head.lower():
        return "alto"
    return "txt"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reader", help="reader id, lower case: becomes readings/<id>.jsonl")
    ap.add_argument("source", help="folder with one file per page, or a .jsonl file")
    ap.add_argument("--format", default="auto", choices=["auto", "txt", "pagexml", "alto", "jsonl"])
    args = ap.parse_args()
    if not ID.match(args.reader):
        sys.exit(f"reader id {args.reader!r}: use lower case letters, digits, '.', '-', '_'")

    pages = page_index()
    src = Path(args.source)
    found: dict[str, str] = {}
    unmatched: list[str] = []
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    for path in files:
        fmt = args.format if args.format != "auto" else sniff(path)
        if fmt == "jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    facs = pages.get(stem(rec["key"]))
                    if facs:
                        found[facs] = rec.get("text") or ""
                    else:
                        unmatched.append(rec["key"])
            continue
        if path.suffix.lower() not in (".txt", ".xml"):
            continue
        facs = pages.get(stem(path.name))
        if not facs:
            unmatched.append(path.name)
            continue
        if fmt == "pagexml":
            text = from_pagexml(path)
        elif fmt == "alto":
            text = from_alto(path)
        else:
            text = path.read_text(encoding="utf-8")
        found[facs] = text.strip("\n")

    out = ROOT / "readings" / f"{args.reader}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for facs in pages.values():
            if facs in found:
                fh.write(json.dumps({"key": facs, "text": found[facs]}, ensure_ascii=False) + "\n")
    print(f"{out.relative_to(ROOT)}: {len(found)} of {len(pages)} pages")
    if unmatched:
        print(f"{len(unmatched)} file(s) match no page of the edition, e.g. {unmatched[:3]}")
    known = {r["id"] for r in json.loads((ROOT / "readings" / "readers.json").read_text(encoding="utf-8"))}
    if args.reader not in known:
        print(f"next: add {{\"id\": \"{args.reader}\", \"label\": ..., \"kind\": ..., \"about\": ...}} "
              "to readings/readers.json — a reader that is not listed there is not shown")


if __name__ == "__main__":
    main()
