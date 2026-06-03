#!/usr/bin/env python3
"""
Convert TEI XML files to stripped semantic XML:
- Removes <teiHeader> and all metadata
- Keeps <persName>, <orgName>, <placeName>, <origPlace>, <origDate>, <date>,
  <pb>, <lb>, <p> and the outer <TEI>/<text>/<body> wrapper
- Drops index-pointer attributes (ref=) but keeps semantic ones
  (when=, role=, facs=, n=, break=)
- One output file per input file, written to xml-semantic/
"""

import re
import sys
from pathlib import Path

# Attributes to keep per tag (others are dropped)
KEEP_ATTRS: dict[str, set[str]] = {
    "persname":  {"role"},
    "orgname":   {"role"},
    "placename": set(),
    "origplace": set(),
    "origdate":  {"when"},
    "date":      {"when"},
    "pb":        {"facs", "n"},
    "lb":        {"n", "break"},
    "p":         set(),
    "body":      set(),
    "text":      set(),
    "tei":       {"xml:id"},
}

SEMANTIC_TAGS = set(KEEP_ATTRS.keys())

# Matches an opening or self-closing tag: <tagname attr...> or <tagname .../>
TAG_RE = re.compile(
    r"(<(/?)([a-zA-Z][a-zA-Z0-9_:.-]*)(\s[^>]*)?(/?))>",
    re.DOTALL,
)

ATTR_RE = re.compile(r'\s([a-zA-Z][a-zA-Z0-9_:.-]*)=["\'][^"\']*["\']')


def filter_attrs(tag_lower: str, attrs_str: str) -> str:
    """Keep only allowed attributes for this tag."""
    allowed = KEEP_ATTRS.get(tag_lower, set())
    if not attrs_str:
        return ""
    result = []
    for m in ATTR_RE.finditer(attrs_str):
        if m.group(1).lower() in allowed:
            result.append(m.group(0))
    return "".join(result)


def transform(raw: str) -> str:
    # 1. Remove teiHeader block entirely (including content)
    raw = re.sub(
        r"<teiHeader\b[^>]*>.*?</teiHeader>",
        "",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 2. Remove XML declaration and re-add a clean one
    raw = re.sub(r"<\?xml[^?]*\?>", "", raw).strip()

    # 3. Process tag by tag
    out = []
    pos = 0
    for m in TAG_RE.finditer(raw):
        # Emit text between last match and this tag
        out.append(raw[pos : m.start()])
        pos = m.end()

        closing  = m.group(2)   # "/" if closing tag
        tag_name = m.group(3)
        attrs    = m.group(4) or ""
        selfclose = m.group(5)  # "/" if self-closing

        tag_lower = tag_name.lower()

        if tag_lower not in SEMANTIC_TAGS:
            # Drop the tag but keep its text content (closing tags vanish silently)
            continue

        filtered = filter_attrs(tag_lower, attrs)
        if selfclose == "/":
            out.append(f"<{tag_name}{filtered}/>")
        elif closing:
            out.append(f"</{tag_name}>")
        else:
            out.append(f"<{tag_name}{filtered}>")

    out.append(raw[pos:])
    result = "".join(out)

    # 4. Collapse runs of whitespace inside text nodes (not inside tags)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + result.strip() + "\n"


def process_file(xml_path: Path, output_dir: Path) -> None:
    raw = xml_path.read_text(encoding="utf-8")
    transformed = transform(raw)
    out_file = output_dir / xml_path.name
    out_file.write_text(transformed, encoding="utf-8")


def main():
    repo_root = Path(__file__).parent
    data_dir = repo_root / "data"
    output_dir = repo_root / "xml-semantic"

    if len(sys.argv) > 1:
        data_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:
        output_dir = Path(sys.argv[2])

    output_dir.mkdir(parents=True, exist_ok=True)

    xml_files = sorted(data_dir.glob("*.xml"))
    if not xml_files:
        print(f"No XML files found in {data_dir}")
        sys.exit(1)

    for xml_path in xml_files:
        process_file(xml_path, output_dir)
        print(xml_path.name)

    print(f"\nDone — {len(xml_files)} files written to {output_dir}")


if __name__ == "__main__":
    main()
