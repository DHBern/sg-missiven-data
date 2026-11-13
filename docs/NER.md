# Tagging the letters not yet edited

The automatic tagging of the unedited letters should look like the editors'
tagging of the edited ones: the same elements, the same roles, the same
registers, the same restraint. This plan takes each of those from the edition
itself (808 letters, counted 13.11.2026) and says how the tagger
(`scripts/ner.py`) reproduces it and how that is measured.

## What the editors tag

| Element | Count | Attributes |
|---|---:|---|
| `persName` | 2,713 | `@ref` always (stadtasg-actors); `@role` 414 (issuer 339, recipient 58, testis 17); `@cert="medium"` 35 |
| `orgName` | 1,604 | `@ref` always; `@role` 1,200 (recipient 720, issuer 478) |
| `placeName` | 2,177 | `@ref` always (stadtasg-places) |
| `origPlace` | 194 | `@ref` to the place: the place of writing in the date line |
| `origDate` | 753 | `@when`, to the day in 743 |
| `date` | 417 | `@when`: other dates in the text |

There is no nesting. `@cert` occurs only as "medium". Every name is linked to a
register, and roles mark the signature (issuer) and the address (recipient).

## What the tagger did until now, and what changes

| | Until now | From now on |
|---|---|---|
| Types learned | 4 (per, org, place, date) | **10**: `persName`, `persName@issuer`, `persName@recipient`, `orgName`, `orgName@issuer`, `orgName@recipient`, `placeName`, `origPlace`, `origDate`, `date` |
| `origPlace` | not read from the TEI, so its 194 words were taught as *no entity* | learned as its own type |
| `origDate` / `date` | merged | separate, as in the edition |
| Roles | not learned | learned with the type (the address and signature are visible in the text) |
| `testis` (18) | — | tagged as plain `persName`; too rare to learn |

## Linking, set by measured precision

How often a link was right in the 5-fold run, for each kind of match:

| | Spelling seen in training letters | Fuzzy 95–99 | Fuzzy 90–94 |
|---|---:|---:|---:|
| Organisations | 1.00 | 0.98 | 0.69 |
| Places | 0.99 | 1.00 | 0.69 |
| Persons | **0.77** | 0.88 | **0.33** |

Organisations and places rarely change their name, so a known spelling or a
very close one is enough. Persons share names across generations. A known
spelling is wrong in a quarter of the cases, and a fuzzy one in two thirds.

The policy follows from this:

- **Organisations and places:** `@ref` for a known spelling, or for a fuzzy
  match ≥ 95. A fuzzy match gets `@cert="medium"`, the edition's own word for
  doubt. Below 95, no `@ref`.
- **Persons:**
  - A register person is a candidate only if the edition attests them within
    **±40 years** of this letter. The letter's year comes from its date line
    (`scripts/dates.py`) or from the neighbouring letters.
  - `@ref` only for a known spelling within that window, or a fuzzy match
    ≥ 95 within it (`@cert="medium"`). Otherwise no `@ref`.
- **Re-measure:** after the change the thresholds are measured again on the
  cross-validation. A link type whose precision stays below 0.9 is not
  written at all.
- **Unlinked names** stay tagged without a `@ref`. They are collected in
  `entities/unlinked_<reader>.tsv` (type, normalised form, spellings, count,
  letters) as candidates for new register entries.
  - Most persons of 1573–1650 are not in the register: 700 of the 808 edited
    letters are earlier. So for the unedited letters, this list matters more
    than the links.

## Dates

- **`origDate/@when`:**
  - The year comes from the date line, as for the regests: written in full,
    or with the century from the neighbouring letters (`@cert="medium"`).
  - The day is added only when the line gives day and month in figures or
    with a month name ("den 15 tag Novembris anno etc. 73" → 1573-11-15).
  - A feast-day dating ("mentag nach Galli") is left at the year. Converting
    it needs a calendar of feasts, which is not part of this step.
- **`date`** in the text: tagged, no `@when`.

## Measuring

- **5-fold cross-validation by letter.** Every edited page is tagged by a model
  that never saw its letter.
- **Reported:**
  - per type, with and without roles
  - linking precision per type and per kind of match
  - all of that separately for the **late letters** (Nr. ≥ 1000, 109 letters,
    1573–1650). They are the nearest the edition comes to the unedited
    letters, which are mostly from that period.
- **Gate before the TEI:**
  - persons, organisations and places at F1 ≥ 0.70 in the late letters;
  - every `@ref` that is written comes from a link type with precision ≥ 0.9.

## Running

1. **Cross-validation:** 5 folds, about 4–5 hours on this Mac (MPS). This
   gives the numbers above and the tags of the edited pages.
2. **Final model:** trained on all edited pages (about 1 hour) and kept in
   `models/ner-<reader>/`, which is not committed.
3. **Tag the unedited pages** read so far (392 pages), and again, with the
   saved model, once Argon has read the whole series (about 14,385 pages,
   from 15.11.): `ner.py tag`.
4. **Then:**
   - the viewer shows the tags on the unedited letters;
   - the register pages list them separately, as automatic;
   - `scripts/tei_export.py` may write the TEI.
