# Draft regests for the letters not yet edited

A regest draft is written by an open language model (Qwen3.8-27B on the
University of Bern's GPUStack, no thinking) from the best machine reading
(Gemini 4 Argon, CER 11.1 % on the edition's pages), with four of the edition's
own regests as examples, one per quarter of the date range
(`scripts/regest.py`). It is a draft: shown as such in the viewer, marked
`@resp="#regest"` in the TEI (`scripts/tei_export.py`), never in place of an
editor's regest.

## Where it stands (13.11.2026)

- 85 letters drafted from the first 1,878 downloaded pages (of 86 letters read
  so far; one is not complete).
- Evaluated on 30 edited letters against the editors' regests:
  - Sender and recipient match: median similarity 100. At least 80 for 25 and
    26 of the 30.
  - Regest length: 38–88 words, median 59, as the edition's.
  - Year: the model got it right in 13 of 30. It closed abbreviated years in
    the wrong century ("anno etc. lxv°" as 1515, "im lij jar" as 1551).
- The year is now read by rule (`scripts/dates.py`). The century comes from
  the neighbouring letters, because the series is numbered chronologically.
  - Result: 17 of 30 right, 20 within a year. Of the 10 misses, 6 are
    misreadings of the year in the reading itself, 3 have no year and 1 is a
    garbled numeral.
  - Applied to the 85 drafts, this changed 20 years, all by a century
    ("anno etc. 73" next to letters of 1573–1575 had been 1473).
- The series has **3,283 letters** that are not in the edition (14,385 pages,
  median 4 pages, Nr. 152–3999). A letter is drafted only when every one of
  its pages has been read (`--complete-against readings/series_pages.txt`).

## The rerun

**When:** once Gemini 4 Argon has read the whole series.
- The download of the remaining 6,297 images finishes in the night to
  Saturday 14.11.
- A scheduled task starts the Argon/Flash run on them on Saturday at 08:00.
- At about 680 pages an hour the run ends on Saturday evening.
- So the rerun is due **Sunday 15.11**, once `job_status` shows the run
  `sg-missiven-new-gemini-v4` as done.

**Steps:**

1. **Collect the readings** from tei into `readings/`:
   ```bash
   rsync -a --include='*/' --include='*.json' --include='*.jsonl' --exclude='*' dh@130.92.252.85:agentic_historian/agentic_historian/data/vlm_test/sg-missiven-full-v4 dh@130.92.252.85:agentic_historian/agentic_historian/data/vlm_test/sg-missiven-new-gemini-v4 dh@130.92.252.85:agentic_historian/agentic_historian/data/vlm_test/sg-missiven-new-vlm-v1 RAW/
   python3 scripts/collect_readings.py RAW
   ```
   Check that `readings/gemini-4-argon.jsonl` has about 16,000 pages: the
   edition's 1,667 plus about 14,385 new.
2. **Widen the evaluation** to 100 edited letters, spread over the series:
   `regest.py eval --n 100`, then `regest.py years`.
   - **Gate:** sender and recipient at least 80 similar in at least 80 % of
     letters, and the year (rule) right in at least half.
   - Below that, stop: the prompt needs work before 3,000 drafts repeat the
     fault.
   - This step also measures how long a call takes on GPUStack, which sets the
     time for step 3.
3. **Draft every complete letter:**
   `regest.py draft --complete-against readings/series_pages.txt --n 4000 --workers 4`.
   - The run resumes. Letters already drafted are kept, so an interrupted
     run is started again as it was, and the 85 existing drafts are not
     written a second time.
   - `--redo` drafts them again, for a new prompt.
   - Four workers keep the load on the shared GPUStack moderate.
4. **Site:** `build_site.py` rebuilds the viewer data.
5. **Pull request** with the readings and regests. The merge is the
   project's call.

No TEI in this run. The TEI for the unedited letters (`scripts/tei_export.py`)
is produced only after the automatic tagging (`scripts/ner.py`) has run over
their readings; until then the script refuses (see below).

**After the rerun:**
- The Missiven team sees the drafts beside the images from 18.11. to 01.12.
- Their corrections are the measure for a second prompt (`--redo`).
- Two faults are known and not yet fixed:
  - The year-start style: the edition normalises to a year beginning on
    1 January ("Sant Sebastians tag … lxxxxxiiii" is 1495 there).
  - Misread years in the reading.

## Not yet

- **TEI for the unedited letters** waits for the tagging. `tei_export.py`
  writes a letter only when the tagger has tagged its pages, and stops
  without writing anything when no page is tagged. `--allow-untagged`
  overrides this, for testing only.

- Places and names in the drafts are plain text (`<name>`, not linked).
  Linking them waits for the automatic tagging of the readings. The tagger
  (`scripts/ner.py`) has so far only been evaluated on the edition's pages
  (F1 0.73) and has not tagged an unedited page.
- Keywords (`textClass/keywords`) are not drafted.
