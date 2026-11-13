# Continuous collection and publication, with tei as orchestrator

Readings of the series arrive from several machines, over days. This plan
makes tei collect them as they come, combine and tag them, check the result and
publish it, without a laptop in the loop. Written 13.11.2026.

## Who does what

| Machine | Role | Today |
|---|---|---|
| **tei** | orchestrator: starts the runs, collects, combines, tags, builds, publishes | checkout `~/sg-missiven-data`, venv `~/sg-missiven-venv` (lxml, rapidfuzz), `gh` logged in |
| idhefix (ATR gateway) | Qwen3.5-4B fine-tuned; kraken and TrOCR on the edited pages | runs started from tei (`atr-batch`) |
| Gemini API | Argon and Flash | started from tei (`atr-batch --via external`) |
| UBELIX | olmOCR; kraken and TrOCR on the new pages (below) | output to the research storage, which tei mounts |
| GPUStack | LLM tagging and judge; regest drafts | called from tei over HTTP |

Everything the pipeline reads is on tei's disk or on the research share
(`/mnt/wbkolleg_dh_1`, on UBELIX `/storage/research/wbkolleg_dh_1`). UBELIX jobs
write their results to `tei/sgm-readings/ubelix/<model>/` there, so collecting
them needs no login to UBELIX.

## One cycle

`scripts/pipeline.sh`, run by a systemd **user** timer on tei (dh has linger) at
07:30 and 19:30, and on demand (`systemctl --user start sgm-pipeline`). Units in
`ops/systemd/`; status in `~/sgm-pipeline/status.jsonl`.

1. **Update:** `git pull --ff-only` on `main`.
2. **Collect:** `scripts/collect_series.py` over the sources in
   `readings/sources.json`. It merges, never removes; a reader that would lose
   pages stops the cycle. *(Done by hand on 13.11.2026: 8 readers, 10,138
   pages.)*
3. **Joint reading:** `scripts/joint_reading.py`, extended to the series
   (below).
4. **Tagging:** pages read since the last cycle are tagged.
   - With the LLM tagger and judge (`scripts/ner_llm.py`, GPUStack) if it is
     chosen over hmBERT. hmBERT would need its model on a machine with memory to
     spare, and tei has about 1.9 GB free.
   - Register links only of the kinds measured at precision ≥ 0.9.
5. **Not automatic:**
   - **Regest drafts** start only when asked (`docs/REGESTS.md`).
   - **The TEI export** stays gated (`docs/NER.md`).
6. **Check:**
   - `build_site.py` must run without error (13.11.: 5 s, 134 MB).
   - No reader's page count may drop.
   - Only `readings/`, `entities/` and `regests/` may change.
   - No image config may change.
7. **Publish:** commit the changed data, then merge (see the decision below).
   GitHub Pages deploys from `main` as now.
8. **Report:** append a line to `~/sgm-pipeline/status.jsonl` (time, pages per
   reader, what was published), plus one message when a run finishes or a
   check fails.

A cycle that finds nothing new commits nothing.

### How data reaches `main` (decided 13.11.2026: B)

| | How | For | Against |
|---|---|---|---|
| A | tei pushes data commits straight to `main` | fastest | no review trail per update |
| **B** | tei opens or updates one PR "Readings update (automatic)" and merges it itself when the checks pass | every update visible as a PR; code never comes this way | one more step |
| C | as B, but a person merges | full control | updates wait for someone |

**B** is implemented: branch `data/auto`, PR "Readings update (automatic, tei)", merged by the script when the checks pass. Code changes keep coming as reviewed PRs.

### Limits on tei

- **Disk:** `/` is at 93 % (6.6 GB free).
  - The checkout is 66 MB, the readings 40 MB, the site build 56 MB, the venv
    39 MB.
  - No images are kept on tei; they stay on the share.
  - `site/data` is rebuilt in place.
- **Memory:** 3.9 GB in all, about 1.9 GB available. The build takes 134 MB.
  hmBERT inference and the alignment for the joint reading need to be measured
  before they run here.
- **Git growth:** each collection rewrites whole jsonl files; hence two cycles a
  day, not one every two hours.

## kraken and TrOCR on UBELIX for the new pages

The gateway read only the 2,048 edited pages with the four line models. The
series has 14,385 more (8,088 downloaded, the rest by 14.11.).

- **Same method as the gateway runs**, so the readings stay comparable:
  - kraken `blla` baseline segmentation with its default model;
  - recognition with `kraken-bern-combined-v1`, `kraken-cursive_inzigkofen`,
    `trocr-kurrent-xvi-xvii` and `trocr-medieval-escriptmask` on the same lines.
- **Where the models come from:**
  - kraken Bern: on the research share
    (`Textrecognition_Training/training_folder/trained/`);
  - kraken Inzigkofen: Zenodo 10.5281/zenodo.18207767, downloaded once to the
    research storage;
  - TrOCR: Hugging Face `dh-unibe/…`, cached on the research storage.
- **Job:** `ubelix/lines.sbatch`, an array over shards like
  `recognize.sbatch`, resumable.
  - Step 1, segmentation on the GPU: line polygons per page, saved as JSON next
    to the output, so each line model reads the same lines.
  - Step 2, the four models: TrOCR batched on line crops on the GPU, kraken on
    the GPU as well.
  - Output: `tei/sgm-readings/ubelix/<model>/shard-*.jsonl`, with `{key, text,
    lines}`.
- **Smoke test first:** 50 edited pages, compared with the gateway's readings of
  the same pages. They should be nearly identical, since models and
  segmentation are the same. This also measures the speed.
- **Container:** one Apptainer image with kraken 5 and transformers, built
  once.
- **Not on 16.11.** (UBELIX maintenance 06:30–17:00).
- **Submitting from tei** needs an SSH key for UBELIX that the pipeline may use.
  Decision needed: a key of its own for tei → UBELIX, or submission stays a
  manual step from the desktop. Collection works either way.

## The joint reading over the series, and whether to tag it again

### Measured (13.11.2026, 1,666 edited pages; tagging on the 218 late-letter pages)

| | CER | |
|---|---:|---|
| Argon alone | 10.97 % | the backbone |
| Joint reading (`joint_reading.py`: Flash, kraken × 2, TrOCR × 2 voting) | 11.04 % | slightly **worse**; 725 words changed in all |
| Oracle: the best reader *per page* (Argon, Flash, kraken, TrOCR) | 10.90 % | the most any page-level choice could reach |
| Same oracle, with the fine-tuned Qwen3.5 | 10.39 % | optimistic: it was trained on these pages |

- **The independent readers don't complement Argon.**
  - Even knowing the answer, picking the best reader per page gains 0.07 points.
  - Word-level voting gains nothing.
  - Two line readers get negative weights (Inzigkofen, TrOCR medieval).

| Tagging (qwen3.8 + judge), F1 on the late letters | on Argon | on the edition text |
|---|---:|---:|
| Persons | 0.677 | 0.842 |
| Organisations | 0.752 | 0.811 |
| Places | 0.718 | 0.853 |
| All | 0.690 | **0.822** |
| Links found (recall) | 0.62 | 0.58 |

- **The reading limits the tagging.** A perfect reading would add 13 points of F1, 16 for persons.
- **The reading does not limit the linking:** recall is the same on the edition text. Links are limited by the register: the persons of 1573–1650 are mostly not in it.

### What follows

1. **No re-tagging on the joint reading as it is.** It is no better than Argon, so tagging it again would only change tags at random.
2. **Tag again only when a reading is measurably better, by at least one point of CER on edited pages the reader never saw.** Three ways could get there:
   - **The fine-tuned VLMs (Qwen3.5-4B, olmOCR).**
     - First reconstruct which edited pages they did *not* train on. Training drew a seeded page split (seed 42, about 90 % train) over all its datasets; the run records in training-atr-models hold or rebuild the lists.
     - Then measure them and the joint reading with them on those pages only.
     - On the new pages they are fair readers, since they never saw those.
   - **Corrections by the Missiven team** (window 18.11.–01.12.): every corrected page is a better reading of that page and is tagged again at once.
   - **kraken and TrOCR on the new pages (UBELIX)** are unlikely to lift the joint reading; they did not on the edited pages. Run them for comparison and as readers in the viewer, not for the joint reading.
3. **When a reader qualifies,** the pipeline re-tags only the pages whose text changed. The tags are then exported to TEI and checked against TEI P5, as now.
4. **Linking improves on the register side, not the reading side:**
   - authority links for the registers (issue #12);
   - unlinked names as candidates for new register entries (`entities/unlinked_*.tsv`, 10,509 groups);
   - person links, which stay unwritten until at least 20 measured links reach precision ≥ 0.9.
5. **Not pursued:** a "merged reading" written by a language model from several readings. It would read better and be harder to check: a model that normalises spelling erases what the edition keeps. Only with its own measurement on held-out pages, and never as the edition's text.

## Order of work

1. ✅ `collect_series.py`, first collection on tei (13.11.).
2. ✅ Publication: B. Open: the UBELIX key.
3. `pipeline.sh` with the systemd user timer, steps 1, 2, 6, 7, 8 (collect and
   publish).
4. ✅ Joint reading measured (13.11.): no gain over Argon; the VLMs' held-out pages come first.
5. Tagging step, after hmBERT vs LLM is decided (`ner_llm.py` pilot,
   13.11.).
6. `ubelix/lines.sbatch`, with the smoke test first, then the full run (not
   16.11.).
