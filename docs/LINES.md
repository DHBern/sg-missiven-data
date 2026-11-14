# Lines on the image

Pointing at a line of a transcription shows where that line stands on the page
image, and pointing at the image shows the line in the text; a click on the
image scrolls the text to it. This works for every text of a page: the edition
and each machine reading.

## Where the boxes come from

Only the line readers find lines: kraken (`sg-missiven-kraken-v1`) and TrOCR
(`sg-missiven-trocr-v1`) segmented each page with kraken's `blla` and read each
line box. The page readers (Gemini, Qwen, olmOCR) read the whole page and give
no positions. So the links exist where the line readers ran: the 2,047 edited
pages. The pages outside the edition have none until a line reader runs on them.

All four line readers of a page share one segmentation: the same boxes, each
reader in an order of its own. `scripts/collect_series.py` (on tei) writes them
to `readings/lines.jsonl`, one page per line:

```json
{"key": "<facs>", "size": [w, h], "segmented_by": "...",
 "boxes": [[x0, y0, x1, y1], ...],        // fractions of the image
 "baselines": [[x0, y0, x1, y1], ...],    // each box's baseline, first and last point
 "readers": {"<line reader>": null | [box index of each text line]}}
```

`null` means the reader's lines are in the boxes' order. The image size comes
from the images the line readers read (`line_images` in `readings/sources.json`);
the boxes are fractions of it, so they fit the archive's IIIF image of any size.

## From boxes to rows

The segmentation cuts a written line into fragments (median 31 boxes on a page,
61 % narrower than a tenth of the image). `scripts/line_links.py` puts them
together into rows by their **baselines**: a box joins the row whose baseline,
carried on to the box, passes within a third of the line spacing of the box's
own baseline, and two boxes standing over each other are never one row.
Grouping by the boxes' height does not work: a box reaches up to the tallest
ascender, so the boxes of two neighbouring lines overlap by half, and the first
version of this merged them (every link one line off).

What the viewer shows of a row is each of its boxes cut to the band of its line
(from just under the baseline to about one line spacing above it), so a flourish
does not light up half the page.

## From rows to the lines of a text

Each row has a text: the line readers' readings of its boxes, left to right.
A transcription is aligned with the rows in order, as two texts are in a
sequence alignment (`align`): a line may match one row, two neighbouring rows
(the segmenter split it), or two lines one row (it merged them); lines and rows
may stay unmatched (the colour chart, the ruler, a line the segmenter missed).
Similarity is `rapidfuzz`'s ratio on letters only: no case, no diacritics,
u/v, i/j/y and ß/ss folded. A link needs a similarity of 0.5 (`MIN_SIM`); below
it the line stays unlinked rather than guessed.

The line readers' own lines need no alignment: line *i* is box *i*.

## In the viewer

`build_site.py` writes per page `rows` (per row its boxes, in ten-thousandths of
the image width, the unit of OpenSeadragon's overlays) and beside every text
`ln`: per line, the rows it is linked to, `[]` where none. The reading's head
says how many of its lines are linked.

## How well it works

On a fifth of the edited pages with text (333 pages):

| text | lines linked |
| --- | --- |
| edition | 96.6 % |
| Gemini 4 Argon | 96.8 % |
| Qwen3.5-4B | 96.4 % |

Where the edition and Argon both link a line they agree on (aligned to each
other), they point to the same row in 4,457 of 4,462 cases. Checked by eye on
pages from 1421 and the 1450s, rows drawn on the IIIF image: every linked line
on its written line, signatures included.

What it cannot do: a line the segmenter did not find has no row; marginal notes
and additions between the lines are rows of their own, and a text that puts
them elsewhere than the image does may leave them unlinked; a page whose lines
the reader ordered differently from top to bottom (two columns, an address on
the side) is linked only as far as the order agrees.
