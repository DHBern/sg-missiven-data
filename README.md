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
| `scripts/regest.py` | Regestentwürfe für noch nicht edierte Briefe (Qwen3.8-27B auf GPUStack), Evaluation gegen die Regesten der Edition; Plan für den Lauf über die ganze Serie: [docs/REGESTS.md](docs/REGESTS.md) |
| `scripts/dates.py` | Jahr aus der Datumszeile, das Jahrhundert abgekürzter Jahre aus den benachbarten Briefen (die Serie ist chronologisch nummeriert) |
| `scripts/tei_export.py` | TEI für die noch nicht edierten Briefe in der Form der Edition → `tei_unedited/` (Lesung, Regestentwurf, Auszeichnung; alles mit `@resp`, nichts geprüft; nicht nach `data/` kopieren). Erst nach dem NER-Lauf: schreibt nur getaggte Briefe |
| `readings/series_pages.txt` | alle 16.434 Bilder der Serie (nur Dateinamen), für Vollständigkeit und Originalnamen |
| `scripts/ner.py` | lernt die Auszeichnung aus den TEI-Dateien und wendet sie auf eine Lesung an (5-fach kreuzvalidiert nach Brief) |
| `site/` | der Viewer (statisch, IIIF-Bilder direkt vom Server der Edition) |

```bash
uv run --with lxml --with rapidfuzz python scripts/build_site.py
python3 -m http.server -d site
```

Noch nicht edierte Briefe der Serie erscheinen mit ihren maschinellen Lesungen, ohne Bild; die Bilder können befristet freigegeben werden ([docs/IMAGE_WINDOW.md](docs/IMAGE_WINDOW.md)).

Register und Karte: Orte, Personen und Organisationen der Register (`place/`, `person/`) mit allen edierten Briefen, die sie nennen (im Text mit der Schreibung dort, oder als Absender, Empfänger, Ausstellungsort), und eine Karte der Orte mit Koordinaten. Die 57 Orte ohne Koordinaten stehen in [docs/places_without_coordinates.md](docs/places_without_coordinates.md) (vom Build geschrieben). Noch nicht edierte Briefe kommen hinzu, sobald ihre automatische Auszeichnung mit den Registern verknüpft ist.

Suche (`#/search`): Volltext über alle Briefe – Text der Edition, sonst die beste maschinelle Lesung – mit Facetten Status, Jahrzehnt und Jahresbereich, Absender, Empfänger, Ausstellungsort, Schlagwort, genannte Personen/Organisationen/Orte (Register; bei noch nicht edierten Briefen aus der automatischen Auszeichnung), Regest, Textquelle. Wörter oder `"eine Wendung"`; «Schreibvarianten zulassen» setzt u/v/w, i/y/j, ſ/s, Doppelbuchstaben, ů/uo/u, ä/ae/a und Worttrennung am Zeilenende gleich. Die ganze Suche steht in der URL. Der Index (`site/data/search/`) wird beim Build geschrieben; der Text liegt in Teilen, die erst bei einer Textsuche parallel geladen werden.

Der Viewer ist zweisprachig (Deutsch/Englisch, Umschalter oben rechts): Er öffnet in der Sprache des Browsers, `?lang=de` oder `?lang=en` erzwingt eine Sprache, z. B. <https://dhbern.github.io/sg-missiven-data/?lang=de>. Jeder sichtbare Text steht in `site/app.js` als `T("English", "Deutsch")` nebeneinander; die Beschreibungen der Leser haben in `readings/readers.json` die Felder `label_de`, `kind_de`, `about_de`, `caveat_de`.

Eine weitere Lesung (z. B. aus Transkribus, eScriptorium, kraken oder einem anderen Modell) hinzufügen: [docs/ADDING_READINGS.md](docs/ADDING_READINGS.md).

Die Fehlerquote (CER) misst den Abstand zu den Konventionen *dieser* Edition, nicht die
paläographische Leistung. Das feinjustierte Qwen3.5-4B hat 90 % der Seiten dieser Edition
im Training gesehen; seine Zahl ist kein fairer Vergleich.
