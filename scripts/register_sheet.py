"""A matching sheet for the generated register entries that could not be resolved (issue #12).

    python scripts/register_sheet.py            # -> registers/unresolved_register.xlsx

One table per category (Personen, Orte, Organisationen) with every generated
entry (scripts/auto_register.py) whose best candidate is neither «sicher» nor
«wahrscheinlich», most often named first. Columns, for a person to fill in:

  A  Name (as the letters spell it most often)
  B  «kein Name»: the tagger was wrong, this is not a person/place name
  C  the same as an entry of the edition's register (drop-down, same category)
  D  normalised name
  E  authority link (full URL: Wikidata, GND, HLS, …)

and, to help: mentions, letters, other spellings, the best «möglich» proposal,
the entry id (for reading the answers back) and its page in the viewer.

The file is uploaded to Google Drive and converted there; the drop-downs come
from Excel data validation and survive the conversion.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_site import registers  # noqa: E402

VIEWER = "https://dhbern.github.io/sg-missiven-data/?lang=de#/entry/"
SHEETS = [("Personen", "person", "Personen"), ("Orte", "place", "Orts"), ("Organisationen", "org", "Organisations")]
HEAD = ["Name", "kein {what}name", "Eintrag im Register der Edition (dieselbe Person bzw. derselbe Ort)", "normalisierter Name", "Normdaten-Link (ganze URL)",
        "Belege", "Briefe (Nr.)", "weitere Schreibungen", "bester Vorschlag (möglich, ungeprüft)", "Eintrag-ID", "im Viewer"]


def resolved(e: dict) -> bool:
    return any(c.get("grade") in ("sicher", "wahrscheinlich") for c in e.get("candidates", []))


def main() -> None:
    data = json.loads((ROOT / "registers" / "auto_register.json").read_text(encoding="utf-8"))
    reg = registers()
    wb = Workbook()
    wb.remove(wb.active)
    counts = {}
    for title, kind, what in SHEETS:
        # the edition's names of this category, for the drop-down (on a hidden sheet)
        ed = sorted({e["name"] for e in reg.values() if e.get("kind") == kind}, key=str.casefold)
        lst = wb.create_sheet(f"Liste {title}")
        for i, n in enumerate(ed, 1):
            lst.cell(i, 1, n)
        lst.sheet_state = "hidden"

        ws = wb.create_sheet(title, index=len(wb.sheetnames) - 1)
        ws.append([h.format(what=what) for h in HEAD])
        rows = [(eid, e) for eid, e in data["entries"].items() if e["kind"] == kind and not resolved(e)]
        rows.sort(key=lambda x: (-x[1].get("mentions", 0), x[1]["name"].casefold()))
        for eid, e in rows:
            nrs = [str(int(re.search(r"(\d+)$", lid).group(1))) for lid in e.get("letters", [])]
            top = next((c for c in e.get("candidates", []) if c.get("grade") == "möglich"), None)
            hint = ""
            if top:
                url = next((l["url"] for l in top.get("links", [])), "")
                hint = f"{top.get('label', '')} ({url})" if url else top.get("label", "")
            ws.append([e["name"], None, None, None, None, e.get("mentions", 0),
                       ", ".join(nrs[:8]) + (f" … (+{len(nrs) - 8})" if len(nrs) > 8 else ""),
                       ", ".join(f for f in e.get("forms", []) if f != e["name"])[:200], hint, eid, VIEWER + eid])
        n = len(rows) + 1
        counts[title] = len(rows)
        # a name beginning with "=" (a word broken at the line end) must stay text, not become a formula
        for row in ws.iter_rows(min_row=2, max_col=1):
            for c in row:
                if isinstance(c.value, str) and c.value[:1] in "=+-@":
                    c.data_type = "s"
        dv_b = DataValidation(type="list", formula1='"kein Name"', allow_blank=True)
        dv_c = DataValidation(type="list", formula1=f"='Liste {title}'!$A$1:$A${len(ed)}", allow_blank=True)
        dv_e = DataValidation(type="custom", formula1='=OR(ISBLANK(E2),LEFT(E2,4)="http")', allow_blank=True,
                              error="Bitte die ganze URL eingeben (https://…)", errorTitle="Normdaten-Link",
                              showErrorMessage=True)
        for dv, col in ((dv_b, "B"), (dv_c, "C"), (dv_e, "E")):
            ws.add_data_validation(dv)
            dv.add(f"{col}2:{col}{max(n, 2)}")
        ws.freeze_panes = "B2"
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="EEEEEE")
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for col, w in zip("ABCDEFGHIJK", (28, 14, 40, 28, 40, 8, 22, 30, 40, 20, 30)):
            ws.column_dimensions[col].width = w
        ws.auto_filter.ref = f"A1:K{n}"
    # a short note first
    info = wb.create_sheet("Anleitung", 0)
    for line in [
        "Ungeklärte automatisch generierte Registereinträge der St. Galler Missiven (DH Bern, Issue #12).",
        "Je Blatt eine Kategorie; die Namen stammen aus der automatischen Auszeichnung der noch nicht edierten Briefe.",
        "Spalte B: «kein Name» wählen, wenn das kein Personen-, Orts- bzw. Organisationsname ist (Fehler der Auszeichnung).",
        "Spalte C: ist es dieselbe Person / derselbe Ort wie ein Eintrag im Register der Edition? Dann diesen wählen.",
        "Spalte D: sonst den normalisierten Namen eintragen; Spalte E: einen Normdaten-Link (ganze URL: Wikidata, GND, HLS …).",
        "Spalten F–K nur zur Hilfe: Belege, Briefnummern, Schreibungen, ein ungeprüfter Vorschlag, die ID (für den Rückimport), der Eintrag im Viewer.",
        "Bitte Spalten und Reihenfolge nicht ändern: die Antworten werden über die Eintrag-ID (Spalte J) zurückgelesen.",
    ]:
        info.append([line])
    info.column_dimensions["A"].width = 140
    out = ROOT / "registers" / "unresolved_register.xlsx"
    wb.save(out)
    print(f"wrote {out.relative_to(ROOT)}: {counts}")


if __name__ == "__main__":
    main()
