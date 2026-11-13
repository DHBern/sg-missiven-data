"""Read the TEI edition into letters and pages for the readings viewer.

Each letter keeps its header (title, date, sender, recipient, regest, keywords)
and its pages, one per ``<pb facs>``. A page carries the edition text twice:

* ``html`` — the edited text with its line numbers, the entities the editors
  tagged (``persName``, ``placeName``, ``orgName``, dates), expansions, unclear
  and supplied text, and the editors' notes as markers. Spans may cross a line
  break, so a page is one HTML string with ``<br>`` per ``<lb>``.
* ``text`` — the words on the page, one line per ``<lb>``: entity tags dropped,
  notes skipped (their tails kept), expansions as expanded. This is what a
  machine reading is compared with.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

TEI = "{http://www.tei-c.org/ns/1.0}"
NS = {"t": TEI[1:-1]}

ENTITY = {"persName": "per", "placeName": "place", "orgName": "org", "origPlace": "place",
          "origDate": "date", "date": "date"}


def local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def norm(s: str) -> str:
    return " ".join(s.split())


@dataclass
class Page:
    facs: str
    n: str
    html: list[str] = field(default_factory=list)
    lines: list[list[str]] = field(default_factory=lambda: [[]])
    entities: list[dict] = field(default_factory=list)

    def add(self, text: str) -> None:
        if not text:
            return
        self.html.append(html.escape(text))
        self.lines[-1].append(text)

    def pos(self) -> tuple[int, int]:
        return len(self.lines) - 1, sum(len(p) for p in self.lines[-1])

    def text(self) -> str:
        return self.text_and_spans()[0]

    def text_and_spans(self) -> tuple[str, list[dict]]:
        """The page text, and the entities with ``start``/``end`` offsets into it.

        Each line's whitespace is collapsed and stripped and empty lines are
        dropped, so the raw positions recorded while walking are mapped through
        the same normalisation."""
        out_lines: list[str] = []
        maps: list[list[int] | None] = []
        offset = 0
        for parts in self.lines:
            raw = "".join(parts)
            normed, cmap, prev_space = [], [], True
            for ch in raw:
                if ch.isspace():
                    if not prev_space:
                        normed.append(" ")
                    prev_space = True
                    cmap.append(len(normed) - (1 if normed and normed[-1] == " " else 0))
                else:
                    cmap.append(len(normed))
                    normed.append(ch)
                    prev_space = False
            line = "".join(normed).rstrip()
            cmap.append(len(line))
            if not line:
                maps.append(None)
                continue
            if out_lines:
                offset += 1  # the newline
            maps.append([offset + min(i, len(line)) for i in cmap])
            out_lines.append(line)
            offset += len(line)
        text = "\n".join(out_lines)

        def at(p, end=False):
            li, ci = p
            m = maps[li] if 0 <= li < len(maps) else None
            if m is None:  # an empty line: next (start) or previous (end) real position
                rng = range(li - 1, -1, -1) if end else range(li + 1, len(maps))
                for k in rng:
                    if maps[k] is not None:
                        return maps[k][-1] if end else maps[k][0]
                return len(text) if not end else 0
            return m[min(ci, len(m) - 1)]

        spans = []
        for ent in self.entities:
            if "_start" not in ent or "_end" not in ent:
                continue
            s, e = at(ent["_start"]), at(ent["_end"], end=True)
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            if e > s:
                spans.append({k: v for k, v in ent.items() if not k.startswith("_")} | {"start": s, "end": e})
        return text, spans


class Walker:
    def __init__(self) -> None:
        self.pages: list[Page] = []
        self.lines_on_page = 0
        self.notes: list[str] = []

    @property
    def page(self) -> Page | None:
        return self.pages[-1] if self.pages else None

    def emit(self, text: str | None) -> None:
        if text and self.page is not None:
            self.page.add(text)

    def raw(self, markup: str) -> None:
        if self.page is not None:
            self.page.html.append(markup)

    def walk(self, el) -> None:
        name = local(el)
        if name == "pb":
            self.pages.append(Page(facs=el.get("facs", ""), n=el.get("n", "")))
            self.lines_on_page = 0
        elif name == "lb":
            if self.page is not None:
                if self.lines_on_page:
                    if el.get("break") == "no":
                        self.raw('<span class="hy">-</span>')
                    self.raw("<br>")
                    self.page.lines.append([])
                self.lines_on_page += 1
                self.raw(f'<span class="ln">{html.escape(el.get("n", ""))}</span>')
        elif name == "note":
            text = norm("".join(el.itertext()))
            self.notes.append(text)
            self.raw(f'<sup class="note" title="{html.escape(text, quote=True)}">'
                     f'{len(self.notes)}</sup>')
        elif name == "choice":
            expan = el.find("t:expan", NS)
            abbr = el.find("t:abbr", NS)
            shown = expan if expan is not None else abbr
            title = norm("".join(abbr.itertext())) if abbr is not None else ""
            self.raw(f'<span class="choice" title="abgekürzt: {html.escape(title, quote=True)}">')
            if shown is not None:
                self.inner(shown)
            self.raw("</span>")
        elif name in ENTITY:
            kind = ENTITY[name]
            ref = el.get("ref") or el.get("when") or ""
            role = el.get("role") or ""
            label = norm("".join(el.itertext()))
            page = self.page
            ent = {"type": kind, "el": name, "ref": ref, "role": role, "text": label}
            if page is not None:
                ent["_start"] = page.pos()
                page.entities.append(ent)
            self.raw(f'<span class="ent {kind}" data-ref="{html.escape(ref, quote=True)}"'
                     f' data-role="{html.escape(role, quote=True)}">')
            self.inner(el)
            self.raw("</span>")
            if page is not None and self.page is page:
                ent["_end"] = page.pos()
        elif name in ("unclear", "supplied", "ex", "gap", "sic", "abbr", "expan"):
            css = {"supplied": "supplied", "ex": "ex", "unclear": "unclear",
                   "gap": "gap", "sic": "sic"}.get(name, name)
            self.raw(f'<span class="{css}">')
            if name == "gap":
                self.raw("[…]")
            self.inner(el)
            self.raw("</span>")
        else:
            self.inner(el)
        self.emit(el.tail)

    def inner(self, el) -> None:
        self.emit(el.text)
        for child in el:
            if isinstance(child.tag, str):
                self.walk(child)
            else:  # comment or PI: keep its tail only
                self.emit(child.tail)


def first(root, path: str) -> str:
    hit = root.find(path, NS)
    return norm("".join(hit.itertext())) if hit is not None else ""


def actors(action) -> list[dict]:
    out = []
    if action is None:
        return out
    for child in action:
        name = local(child)
        if name in ("persName", "orgName", "placeName"):
            out.append({"type": ENTITY[name], "ref": child.get("ref", ""),
                        "text": norm("".join(child.itertext()))})
    return out


def read_letter(path: Path) -> dict:
    root = etree.parse(str(path)).getroot()
    xml_id = root.get("{http://www.w3.org/XML/1998/namespace}id") or path.stem
    sent = root.find(".//t:correspAction[@type='sent']", NS)
    received = root.find(".//t:correspAction[@type='received']", NS)
    date_el = sent.find("t:date", NS) if sent is not None else None
    date = ""
    if date_el is not None:
        date = (date_el.get("when") or date_el.get("from") or date_el.get("notBefore")
                or norm("".join(date_el.itertext())))
    walker = Walker()
    body = root.find(".//t:text/t:body", NS)
    if body is not None:
        walker.inner(body)
    pages = []
    for p in walker.pages:
        text, spans = p.text_and_spans()
        pages.append({"facs": p.facs, "n": p.n,
              "html": re.sub(r"\s+", " ", "".join(p.html)).replace(" <br>", "<br>").strip(),
              "text": text, "entities": spans})
    return {
        "id": xml_id,
        "title": first(root, ".//t:titleStmt/t:title"),
        "idno": first(root, ".//t:msIdentifier/t:idno"),
        "date": date,
        "sender": actors(sent),
        "recipient": actors(received),
        "regest": first(root, ".//t:abstract"),
        "keywords": [norm("".join(t.itertext())) for t in root.findall(".//t:keywords/t:term", NS)],
        "pages": pages,
    }


def read_edition(data_dir: Path) -> list[dict]:
    return [read_letter(p) for p in sorted(data_dir.glob("*.xml"))]


if __name__ == "__main__":
    import json
    import sys
    letters = read_edition(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
    pages = [p for letter in letters for p in letter["pages"]]
    empty = [p["facs"] for p in pages if not p["text"]]
    print(f"{len(letters)} letters, {len(pages)} pages, {len(empty)} without edition text")
    print(json.dumps(letters[0], ensure_ascii=False, indent=1)[:2500])
