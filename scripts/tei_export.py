"""TEI for the letters the edition has not yet published, in the edition's own form.

    python scripts/tei_export.py                       # -> tei_unedited/*.xml
    python scripts/tei_export.py --reader gemini-4-argon --reader joint

One file per letter of the series that has a machine reading but no TEI in
``data/``, shaped like the edition's files (``data/stasg_missiv_00272.xml`` is a
typical one) so that the same tools read both:

* ``teiHeader``: title "StadtASG Missive, Nr. N"; ``msIdentifier/idno``
  "StadtASG Missive_<Nr>_<first page>/<last page>" as the edition writes it;
  ``abstract``, ``correspDesc`` (sender, recipient, place, date) from the
  drafted regest (scripts/regest.py) where there is one; ``revisionDesc`` with
  the edition's own vocabulary, every step ``status="open"``.
* ``text/body``: one ``<p>``; ``<pb facs n>`` per image (the original file
  name, as in the edition), ``<lb n>`` per line, numbered through the letter.
  The text is the first of ``--reader`` that has read the page.
* Entities, where ``entities/<reader>.jsonl`` has tagged the page:
  ``persName``/``orgName``/``placeName`` with ``@ref`` into the registers when
  the tagger linked them, ``origDate`` for dates.

Only after the automatic tagging: a letter is written only when
``entities/<reader>.jsonl`` has tagged its pages, and the script stops without
writing anything when no page of an unedited letter is tagged
(``--allow-untagged`` overrides this, for testing only).

Nothing here is edited. Everything a machine produced carries ``@resp``
pointing to a ``respStmt`` that names the machine: the transcription, the
regest, the sender and recipient taken from it, and every tag. Do not merge
these files into ``data/``: that folder is the published edition, and its
app indexes everything in it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_site import load_drafts, registers  # noqa: E402

TEI_NS = "http://www.tei-c.org/ns/1.0"
FACS = re.compile(r"^StadtASG_Missive_(\d+)_([^.]+)\.", re.I)
ELEMENT = {"per": "persName", "org": "orgName", "place": "placeName", "date": "origDate"}
LABEL = {"gemini-4-argon": "Gemini 4 Argon (Google), ganze Seite, Prompt sg_missiven_page_v4",
         "gemini-3.8-flash": "Gemini 3.8 Flash (Google), ganze Seite, Prompt sg_missiven_page_v4",
         "joint": "gemeinsame Lesung mehrerer Leser (scripts/joint_reading.py)"}


def edition_pages() -> set[str]:
    pb = re.compile(r'<pb\b[^>]*\bfacs="([^"]+)"')
    return {Path(f).stem.lower() for x in (ROOT / "data").glob("*.xml") for f in pb.findall(x.read_text(encoding="utf-8"))}


def page_order(part: str) -> tuple:
    """'3059' < '3060'; '333-a' < '333-b' < '334-a'."""
    return tuple(int(t) if t.isdigit() else t for t in re.findall(r"\d+|[a-z]+", part.lower()))


def load_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["key"]] = rec
    return out


def original_names() -> dict[str, str]:
    """stem (lower case) -> the original image name, as the archive's files spell it."""
    path = ROOT / "readings" / "series_pages.txt"
    if not path.exists():
        return {}
    return {Path(n).stem.lower(): n for n in path.read_text(encoding="utf-8").split()}


def outer(spans: list[dict], length: int) -> list[dict]:
    keep, end = [], -1
    for s in sorted(spans, key=lambda s: (s["start"], -s["end"])):
        if s["start"] >= end and 0 <= s["start"] < s["end"] <= length:
            keep.append(s)
            end = s["end"]
    return keep


def page_xml(text: str, spans: list[dict], line_no: int, reg: dict) -> tuple[str, int]:
    """The page's lines as TEI: '<lb n="k"/>' before each line, entities wrapped.
    An entity may run across a line break, as in the edition."""
    text = "\n".join(line.rstrip() for line in text.strip("\n").splitlines())
    if not text.strip():
        return "", line_no
    opens: dict[int, list[str]] = {}
    closes: dict[int, list[str]] = {}
    for s in outer(spans, len(text)):
        name = ELEMENT.get(s["type"])
        if not name:
            continue
        attrs = ""
        if s.get("ref") and s["ref"] in reg:
            attrs += f" ref={quoteattr(s['ref'])}"
            if s.get("link_score") is not None and s["link_score"] < 90:
                attrs += ' cert="low"'
        attrs += ' resp="#ner"'
        opens.setdefault(s["start"], []).append(f"<{name}{attrs}>")
        closes.setdefault(s["end"], []).insert(0, f"</{name}>")
    line_no += 1
    out = [f'\n<lb n="{line_no}"/>']
    for i, ch in enumerate(text):
        out += closes.get(i, [])
        out += opens.get(i, [])
        if ch == "\n":
            line_no += 1
            out.append(f'\n<lb n="{line_no}"/>')
        else:
            out.append(escape(ch))
    out += closes.get(len(text), [])
    return "".join(out), line_no


def letter_xml(nr: int, keys: list[str], readings: list[tuple[str, dict]], ents: dict,
               draft: dict | None, names: dict, reg: dict, today: str) -> tuple[str, dict]:
    lid = f"stasg_missiv_unedited_{nr:05d}"
    parts = [FACS.match(k).group(2) for k in keys]
    idno = f"StadtASG Missive_{nr}_{parts[0]}" + (f"/{parts[-1]}" if len(parts) > 1 else "")
    used, tagged = set(), False
    body, line_no = [], 0
    for i, key in enumerate(keys):
        facs = names.get(Path(key).stem.lower(), key)
        body.append(f'\n<pb facs={quoteattr(facs)} n="{i + 1}"/>')
        for reader, pages in readings:
            if key in pages:
                used.add(reader)
                spans = ents.get(reader, {}).get(key, {}).get("entities", [])
                tagged |= bool(spans)
                chunk, line_no = page_xml(pages[key].get("text") or "", spans, line_no, reg)
                body.append(chunk)
                break
    resp = []
    for reader in [r for r, _ in readings if r in used]:
        resp.append(f'<respStmt xml:id="atr-{reader}"><resp>Maschinelle Lesung (Transkription), nicht korrigiert</resp>'
                    f"<name>{escape(LABEL.get(reader, reader))}</name></respStmt>")
    if draft:
        resp.append('<respStmt xml:id="regest"><resp>Regestentwurf, Absender, Empfänger, Ort und Datum aus der maschinellen Lesung, nicht geprüft</resp>'
                    f"<name>{escape(draft.get('model') or 'Sprachmodell')}, GPUStack der Universität Bern (scripts/regest.py)</name></respStmt>")
    if tagged:
        resp.append('<respStmt xml:id="ner"><resp>Automatische Auszeichnung und Verknüpfung mit den Registern, nicht geprüft</resp>'
                    "<name>hmBERT, trainiert auf der Auszeichnung der Edition (scripts/ner.py)</name></respStmt>")

    corresp = abstract = ""
    if draft:
        abstract = f'<abstract><p resp="#regest">{escape(draft["regest"])}</p></abstract>'
        sent = []
        if draft.get("sender"):
            sent.append(f'<name resp="#regest">{escape(draft["sender"])}</name>')
        if draft.get("place"):
            sent.append(f'<placeName resp="#regest">{escape(draft["place"])}</placeName>')
        # the year: as the date line writes it ("written"), its century from the
        # neighbouring letters ("century"), or only the model's guess ("model");
        # see scripts/dates.py
        year = str(draft.get("year") or "")
        when = draft.get("date") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(draft.get("date") or "")) else (
            year if re.fullmatch(r"\d{4}", year) else "")
        if when and len(when) == 10 and when[:4] != year and re.fullmatch(r"\d{4}", year):
            when = year  # the model's full date disagrees with the year read by rule: keep the year only
        cert = {"written": "", "century": "medium"}.get(draft.get("year_how") or "", "low")
        if when or draft.get("date_text"):
            att = (f' when="{when}"' if when else "") + (f' cert="{cert}"' if when and cert else "")
            sent.append(f'<date{att} resp="#regest">{escape(draft.get("date_text") or when)}</date>')
        recv = f'<name resp="#regest">{escape(draft["recipient"])}</name>' if draft.get("recipient") else ""
        corresp = ("<correspDesc>"
                   + (f'<correspAction type="sent">{"".join(sent)}</correspAction>' if sent else "")
                   + (f'<correspAction type="received">{recv}</correspAction>' if recv else "")
                   + "</correspDesc>")

    changes = [f'<change type="transcription" status="open" who="{" ".join("#atr-" + r for r in sorted(used))}">'
               f"<name>Maschinelle Lesung</name><date>{today}</date></change>"]
    if tagged:
        changes.append(f'<change type="tagging" status="open" who="#ner"><name>Automatische Auszeichnung</name><date>{today}</date></change>')
    if draft:
        changes.append(f'<change type="regest" status="open" who="#regest"><name>Regestentwurf</name><date>{today}</date></change>')

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{TEI_NS}" xml:id="{lid}">
    <teiHeader>
        <fileDesc>
            <titleStmt>
                <title>StadtASG Missive, Nr. {nr}</title>
                {"".join(resp)}
            </titleStmt>
            <publicationStmt>
                <publisher>Universität Bern, Digital Humanities</publisher>
                <date>{today[:4]}</date>
                <availability><p>Maschinelle Vorstufe zu einem noch nicht edierten Brief der Serie: weder Transkription noch Regest noch Auszeichnung sind geprüft. Nicht Teil der Edition des Stadtarchivs St. Gallen.</p></availability>
            </publicationStmt>
            <sourceDesc>
                <msDesc>
                    <msIdentifier>
                        <idno>{escape(idno)}</idno>
                    </msIdentifier>
                </msDesc>
            </sourceDesc>
        </fileDesc>
        <profileDesc>
            {abstract}
            <langUsage>
                <language ident="deu">Deutsch</language>
            </langUsage>
            {corresp}
        </profileDesc>
        <revisionDesc>
            {"".join(changes)}
        </revisionDesc>
    </teiHeader>
    <text>
        <body>
<p>{"".join(body)}
</p>
        </body>
    </text>
</TEI>
"""
    root = etree.fromstring(xml.encode())  # well-formed or fail here, not in someone's tools
    return etree.tostring(root, encoding="unicode", xml_declaration=False), {
        "id": lid, "pages": len(keys), "lines": line_no, "readers": sorted(used),
        "regest": bool(draft), "tagged": tagged}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader", action="append", default=None,
                    help="reading for the text, in order of preference (default: gemini-4-argon)")
    ap.add_argument("--out", default=str(ROOT / "tei_unedited"))
    ap.add_argument("--allow-untagged", action="store_true",
                    help="write letters the tagger has not tagged (testing only; the TEI waits for the NER run)")
    args = ap.parse_args()
    order = args.reader or ["gemini-4-argon"]
    readings = [(r, load_jsonl(ROOT / "readings" / f"{r}.jsonl")) for r in order]
    readings = [(r, p) for r, p in readings if p]
    if not readings:
        sys.exit(f"no readings for {order}")
    ents = {r: load_jsonl(ROOT / "entities" / f"{r}.jsonl") for r, _ in readings}
    known = edition_pages()
    groups: dict[int, set[str]] = {}
    for _, pages in readings:
        for key in pages:
            m = FACS.match(key)
            if m and Path(key).stem.lower() not in known:
                groups.setdefault(int(m.group(1)), set()).add(key)
    tagged_keys = {k for pages in ents.values() for k, rec in pages.items() if rec.get("entities") is not None}
    if not args.allow_untagged:
        untagged = [nr for nr, keys in groups.items() if not keys & tagged_keys]
        for nr in untagged:
            del groups[nr]
        if not groups:
            sys.exit(f"no unedited letter has been tagged yet ({len(untagged)} letters read): "
                     "run scripts/ner.py over their readings first; nothing written")
        if untagged:
            print(f"{len(untagged)} letters skipped: not tagged yet", flush=True)
    drafts = load_drafts()
    names = original_names()
    reg = registers()
    today = dt.date.today().isoformat()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for nr in sorted(groups):
        keys = sorted(groups[nr], key=lambda k: page_order(FACS.match(k).group(2)))
        draft = drafts.get(f"stasg_missiv_unedited_{nr:05d}")
        xml, row = letter_xml(nr, keys, readings, ents, draft, names, reg, today)
        (out / f"{row['id']}.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n", encoding="utf-8")
        rows.append(row)
    print(f"{len(rows)} letters, {sum(r['pages'] for r in rows)} pages, {sum(r['lines'] for r in rows)} lines -> {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/; "
          f"with regest {sum(r['regest'] for r in rows)}, tagged {sum(r['tagged'] for r in rows)}; "
          f"original image names {'from readings/series_pages.txt' if names else 'MISSING (readings/series_pages.txt)'}")


if __name__ == "__main__":
    main()
