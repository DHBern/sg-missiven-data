# Korrespondenz: mit wem St. Gallen wann Briefe wechselte

Die Seite `#/correspondence` des Viewers zeigt die Partner der Stadt über die Zeit.
Ihre Daten schreibt `scripts/correspondence.py`, vom Build aufgerufen, nach
`site/data/correspondence.json`.

## Wie ein Brief einem Partner zugeordnet wird

- **Richtung:** Bei einem Brief an St. Gallen ist der Partner der Absender, bei
  einem Brief von St. Gallen der Empfänger. Briefe ohne St. Gallen auf einer Seite
  zählen nicht.
- **Ort:** Der Partner ist der Ort, den sein Name nennt: «Bürgermeister und Rat zu
  Lindau» → Lindau, «Landammann und Rat der Äusseren Rhoden» → Appenzell
  Ausserrhoden. Rat, Bischof und Bürger eines Ortes zählen also zusammen.
- **Koordinaten:** aus dem Ortsregister der Edition; für zehn häufige Orte ohne
  Koordinaten im Register (Ravensburg, Wangen, Leutkirch …) aus einer kurzen Liste
  im Skript (`EXTRA`).
- **Jahr:** das Datum der Edition, bei noch nicht edierten Briefen das Jahr des
  Regestentwurfs. Diese Jahre sind unsicher, darum zeigt die Seite Jahrzehnte und
  Zeitfenster, keine Einzeljahre.
- **Regionen:** Eidgenossenschaft und Zugewandte (Orte in der heutigen Schweiz),
  Bodensee und Oberschwaben, Reich und weiter. Drei Regionen, weil mehr Farben
  auf Karte und Fläche nicht mehr sicher zu unterscheiden sind.

Stand 17.11.2026: 4'047 datierte Briefe, 2'823 einem von 117 Orten zugeordnet.
969 Briefe nennen einen Partner ohne Ort (Einzelpersonen, «Eidgenössische Räte»)
und fehlen in den Zahlen; `unplaced` in der JSON-Datei nennt die häufigsten.

## Was die Zahlen zeigen

Was das Stadtarchiv überliefert, nicht den ganzen Austausch. Ausgehende Briefe
sind nur als Entwürfe und Kopien erhalten. Die Balken über der Matrix zeigen die
Überlieferungsdichte je Jahrzehnt; «Anteil am Jahrzehnt» gleicht sie aus. Die
edierten Briefe liegen vor allem 1417–1500, in den 1520er- und 1530er-Jahren und
um 1650; für das späte 16. und das 17. Jahrhundert tragen die Entwürfe fast alles.

## Noch offen

- Einzelpersonen als Partner, über die Registerverweise der edierten Briefe und
  eine Normalisierungsliste für die Entwürfe.
- Ein Netz der Genannten: wer in denselben Briefen vorkommt (Register und
  automatische Auszeichnung). Ein Netz nur der Partner wäre ein Stern um St. Gallen.
