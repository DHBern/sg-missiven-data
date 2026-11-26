# Similar letters

The page «Ähnlichkeit» (`#/similarity`) shows the letters of the series on a map: letters with a similar regest, or a
similar text, lie close together. Each letter page has the box «Ähnliche Missiven finden» with its ten most similar
letters. The method follows `thodel/ssrq_print` (`ssrq_print/embed.py`, `embmap.py`, page
[Ähnlichkeit der Stücke](https://thodel.github.io/ssrq_print_pages/s/aehnlichkeit.html)).

## How it is built

`scripts/similarity.py`, run by hand after the regests or readings change (it needs GPUStack, so the CI build only
copies its output):

```bash
set -a; . ~/.gpustack.env; set +a
python scripts/build_site.py        # the letters as the viewer has them
python scripts/similarity.py all    # embed (cached in .cache-sim/) and map -> similarity/
python scripts/build_site.py        # copies similarity/ into site/data/
```

- **Two sets, one vector per letter each:**
  - *regest*: the edition's regest, or the draft regest of a letter not yet edited (4,089 letters on 26.11.2026);
  - *text*: the edition's transcription, or the joint reading, in sections of about 1,500 characters cut at sentence
    ends, the sections' vectors averaged (4,080 letters; letters with under 300 characters, an address leaf alone,
    get none).
- **Model:** `qwen3-embedding-4b` on the University of Bern's GPUStack (2,560 dimensions), with an instruction
  before the text. Vectors are cached by model, instruction and text, so a rerun only sends new texts.
- **Map:** PCA to 50 dimensions, UMAP (cosine, 30 neighbours, seed 42) in 2D and 3D; HDBSCAN groups the 2D map, and
  each group is named by the three regest words it has far more often than the series (TF-IDF). On 26.11.2026:
  38 groups on the regest map, 21 on the text map.
- **Neighbours:** per letter the ten letters with the highest mean of the two cosine similarities (where a letter
  has only one vector, that one).

Output, committed: `similarity/map_regest.json`, `map_source.json` (rows: id, year, number, edited, direction,
partner place and region from the page «Korrespondenz», sender → recipient, start of the regest; 2D and 3D
coordinates; group), `neighbours.json`.

## Limits

- Edited regests (by the editors) and draft regests (by a language model) are written differently. Colour the map by
  «Regest: Edition oder Entwurf»: on 26.11.2026 25 of the 38 regest groups had more than 10 % of the minority kind,
  so the map does not simply sort by who wrote the regest.
- The text of a letter not yet edited is a machine reading; misreadings carry into its vector.
- The axes mean nothing; only nearness counts. The similarity values are cosines of this model, comparable with each
  other, not a probability.
