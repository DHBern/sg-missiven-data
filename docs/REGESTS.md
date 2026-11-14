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

## The run of 14.11.2026

**Gate (step 2 below), on 100 edited letters: missed.**

| measure | gate | result |
| --- | --- | --- |
| sender at least 80 similar | 80 % | 70 % |
| recipient at least 80 similar | 80 % | 89 % |
| both | 80 % | 63 % |
| year right (rule) | 50 % | 43 % (57 % within a year) |

What the misses are:
- **Names.** Most sender misses are not wrong. The draft names the office
  the letter gives ("Heinrich, Abt des Klosters St. Gallen"); the edition
  names the person it identified from its register ("Heinrich von
  Mansdorf"). A few are wrong (the Confederates for Bern, Wil for Chur).
- **Years.** Most are one year off: the edition starts the year on 1 January,
  and letters dated by feasts around the turn of the year ("an der kindlin tag
  anno xxxviiii") land a year early or late. Others are misreadings in the
  reading itself ("Mcccc tricesimo" read as 1930).

The run went ahead on the project's request, with the gate missed and said so.
The drafts carry the same marks as before: draft, unchecked, names and dates
may be wrong. The corrections the Missiven team makes from 18.11. are the
measure for a second prompt.

**Drafted:** every letter not yet edited that Gemini 4 Argon had read completely
on 14.11. (the readings published that day, PR #33). Letters whose reading
completes later are drafted by running step 3 again: it keeps the drafts it has.

## Keywords (experimental)

`scripts/keywords.py` gives the letters not yet edited keywords from the
edition's own vocabulary (602 keywords, about 8 per edited letter). Qwen3.8-27B
gets the vocabulary, the reading and the draft regest, with four edited letters
as examples, and picks 4–12 keywords; a word not in the vocabulary is dropped.

On 40 edited letters (with the editors' regest, so a best case): **precision
0.67, recall 0.60, F1 0.63**; 7 keywords outside the vocabulary dropped. Two of
three keywords it gives the editors gave too, and it finds three in five of
theirs.

The viewer shows them only as automatic and experimental: dashed chips with a
label on the letter, a section of their own on each keyword page, a facet of
their own in the search ("Schlagwort (automatisch)"), and in the heatmap only
when "+ automatisch" is chosen. The network and the edition's counts use the
edition's keywords alone.

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
- Keywords are drafted (experimental, above) but not written to TEI.

## Corrections (14.11.2026)

- **French date lines.** `scripts/dates.py` read «mil cinq cens huitante trois» as the Roman figures M, I, L (1049). It now reads French number words (`french_year`, 1583 here). A year read out of words that falls outside 1300–1700 is dropped rather than kept. «jm» counts as «im» («jm liij» had been read as J, M, LIIJ = 1052).
  - Changed: 4 drafts. Nr. 953 → 1553, 999 → 1523, 1161 → 1583, 1321 → 1595. The last two moved because the two false anchors had bent the timeline.
  - The evaluation is unchanged: the rule gets 43 of 100 years right, the model 40.
- **Years set by hand:** `regests/year_corrections.json`. `regest.py` applies them whenever it settles the years, with `year_how: "corrected"` and a note. The viewer shows «Jahr von Hand gesetzt», and the TEI writes `cert="medium"`.
  - First entry: Nr. 953, 1553 confirmed (1453 considered). The letter is filed among letters of 1571.
- **Regests in German only.** The model had written the regests of the two French Geneva letters (Nr. 1161, 1469) in English.
  - The prompt now asks for German whatever the letter's language.
  - `ask()` checks the answer (`german()`). If it is not German, it asks again in the same conversation. For Nr. 1161, only that follow-up gave German.
  - Both letters were drafted again, and their keywords as well.

## English translations (14.11.2026)

`scripts/regest_en.py` translates every regest into English. That is 808 regests of the edition, written by its editors, and 2,933 drafts for the letters not yet edited. For a draft, sender, recipient and place are translated too.

- **Model:** Qwen3.8-27B on the University of Bern's GPUStack, no thinking.
- **Glossary:** a fixed list of office titles and phrases, e.g. «Bürgermeister und Rat» → «Burgomaster and Council», «Eidgenossen» → «the Confederates», «Vogt» → «bailiff».
- **Names:** persons and places stay as the German writes them (Zürich, Konstanz, St. Gallen).

**Output:** `regests/en/edition.jsonl` and `regests/en/draft_<reader>.jsonl`. Each row keeps a hash of the German it was made from. A rerun translates only what is new or has changed. The build leaves out a translation whose German has changed since.

### Checks, over all 3,741

| check | edition | drafts |
|---|---:|---:|
| every number of the German in the English | 808 / 808 | 2,933 / 2,933 |
| register names of the German kept | 4,171 / 4,173 | 9,903 / 9,903 |
| in English | 808 / 808 | 2,933 / 2,933 |
| asked a second time (a check failed at first) | 101 | 308 |

The names that still "fail" are false alarms: «Sechs», «Geldern», «Mengen», «Name» and a genitive «Gersters» are not names there.

One real omission remained in every attempt. The model left out the two butchers of Nr. 1414 and turned the sentence round. That translation was corrected by hand against the German and is marked `fixed` in its row.

A sample of 40 (20 edition, 20 drafts), translated back into German beside the original, is in `regests/en/review.md`. In it the back-translations carry the German content.

### On the site and in the TEI

- **English interface:** it shows the English regest, marked "Machine translation of the editors' German regest; not by the editors" (or "of the German draft"), with the German original one click away. The German interface is unchanged.
- **Separate file:** the English texts are in `site/data/index_en.json`, which only the English interface loads, so `index.json` does not grow. The letter list's filter in the English interface also finds English words (the full-text search does not).
- **TEI:** `tei_unedited/` has a second `<abstract xml:lang="en" resp="#mt">`. The edition's TEI (`data/`) is untouched.
