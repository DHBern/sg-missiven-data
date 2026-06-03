#!/usr/bin/env python3
"""
Extract per-page text from TEI XML files.
For each <pb facs="..."> element, create a .txt file named after the facs value
containing the plain text of that page (all XML tags stripped).
"""

import re
import sys
from pathlib import Path

TEI_NS = "http://www.tei-c.org/ns/1.0"

def strip_tags(xml_fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", xml_fragment)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def process_file(xml_path: Path, output_dir: Path) -> int:
    raw = xml_path.read_text(encoding="utf-8")

    # Find all <pb> positions with their facs attribute
    pb_pattern = re.compile(r"<pb\b[^>]*\bfacs=[\"']([^\"']+)[\"'][^>]*/?>", re.IGNORECASE)
    matches = list(pb_pattern.finditer(raw))

    if not matches:
        return 0

    pages_written = 0
    for i, match in enumerate(matches):
        facs = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)

        text = strip_tags(raw[start:end])
        if not text:
            continue

        out_file = output_dir / (Path(facs).name + ".txt")
        out_file.write_text(text + "\n", encoding="utf-8")
        pages_written += 1

    return pages_written


def main():
    repo_root = Path(__file__).parent
    data_dir = repo_root / "data"
    output_dir = repo_root / "txt"

    if len(sys.argv) > 1:
        data_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:
        output_dir = Path(sys.argv[2])

    output_dir.mkdir(parents=True, exist_ok=True)

    xml_files = sorted(data_dir.glob("*.xml"))
    if not xml_files:
        print(f"No XML files found in {data_dir}")
        sys.exit(1)

    total = 0
    for xml_path in xml_files:
        n = process_file(xml_path, output_dir)
        print(f"{xml_path.name}: {n} page(s)")
        total += n

    print(f"\nDone — {total} txt files written to {output_dir}")


if __name__ == "__main__":
    main()
