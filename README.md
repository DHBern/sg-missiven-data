# Briefverkehr der Stadt St. Gallen 1400–1650 (Daten)

## Daten
Es werden die Missiven aus dem Ordner `34_finalized` mit dem TEI Publisher publiziert.

## Lesungen (Readings-Viewer)

Eine Demonstration der Universität Bern (Digital Humanities): jede Seite der Edition mit
der Transkription der Edition und mit den Lesungen automatischer Leser (Gemini 4 Argon,
Gemini 3.8 Flash, ein feinjustiertes Qwen3.5-4B, zwei kraken- und zwei TrOCR-Modelle) und
einer gemeinsamen Lesung, umschaltbar Seite für Seite. Veröffentlicht als GitHub Page,
eingebettet unter <https://dhbern.github.io/content/projects/missiven-sg/>.

| Pfad | Inhalt |
|---|---|
| `readings/<leser>.jsonl` | eine Zeile pro Seite: `key` (= `pb/@facs`), `text` |
| `readings/readers.json` | wer jeder Leser ist, mit Vorbehalten |
| `entities/<leser>.jsonl` | automatische Auszeichnung einer Lesung (Personen, Organisationen, Orte, Daten) mit Verknüpfung zu den Registern |
| `prompts/` | die Anweisung an die Vision-Language-Modelle |
| `scripts/tei_pages.py` | TEI → Seiten (Text, Zeilen, Entitäten mit Offsets) |
| `scripts/build_site.py` | baut `site/data/` aus TEI und Lesungen; misst jede Lesung gegen die Edition (CER) |
| `scripts/import_readings.py` | Lesungen anderer Engines übernehmen (Text, PAGE XML, ALTO, JSONL) |
| `scripts/dropbox_fetch.py` | lädt Bilder der ganzen Missiven-Serie aus der Dropbox-Freigabe (Tageslimit, Arbeitskopien 1500 px) |
| `scripts/image_window.py` | öffnet/schliesst ein befristetes Fenster für Bilder noch nicht edierter Briefe ([docs/IMAGE_WINDOW.md](docs/IMAGE_WINDOW.md)) |
| `scripts/ner.py` | lernt die Auszeichnung aus den TEI-Dateien und wendet sie auf eine Lesung an (5-fach kreuzvalidiert nach Brief) |
| `site/` | der Viewer (statisch, IIIF-Bilder direkt vom Server der Edition) |

```bash
uv run --with lxml --with rapidfuzz python scripts/build_site.py
python3 -m http.server -d site
```

Noch nicht edierte Briefe der Serie erscheinen mit ihren maschinellen Lesungen, ohne Bild; die Bilder können befristet freigegeben werden ([docs/IMAGE_WINDOW.md](docs/IMAGE_WINDOW.md)).

Eine weitere Lesung (z. B. aus Transkribus, eScriptorium, kraken oder einem anderen Modell) hinzufügen: [docs/ADDING_READINGS.md](docs/ADDING_READINGS.md).

Die Fehlerquote (CER) misst den Abstand zu den Konventionen *dieser* Edition, nicht die
paläographische Leistung. Das feinjustierte Qwen3.5-4B hat 90 % der Seiten dieser Edition
im Training gesehen; seine Zahl ist kein fairer Vergleich.
