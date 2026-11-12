# Adding a reading

The viewer shows every reader listed in `readings/readers.json` that has a file
`readings/<id>.jsonl`. Adding an engine's transcriptions therefore takes two
files and a pull request. Once it is merged into `main`, the Pages workflow
rebuilds the site, scores the new reading against the edition, and publishes it
at <https://dhbern.github.io/sg-missiven-data/>.

## 1. Get the page images

The images are not in this repository; they come from the edition's IIIF server.
To run an engine yourself, fetch them once (about 640 MB, 2,049 pages,
throttled):

```bash
python3 scripts/fetch_images.py --out ~/sg-missiven-images
```

Each file is named after its page, `<facs stem>.jpg` (for example
`StadtASG_Missive_1_1.jpg` for `<pb facs="StadtASG_Missive_1_1.JPG"/>`). The
size is 1500 px on the long edge. Pass `--size max` for the originals, which are
3000 px.

## 2. Run the engine

Whatever the engine, keep **one output per page image** and keep the image's
name as the file name. That name is how a reading finds its page.

| Engine | What to export |
|---|---|
| Transkribus, eScriptorium, Loghi | PAGE XML per page, or ALTO |
| kraken CLI (`kraken -a` / `-x`) | ALTO or PAGE XML per page |
| An LLM or VLM behind an OpenAI-compatible API | `scripts/read_pages.py` (below) |
| The University of Bern ATR gateway (`start_batch`) | the run folder; `scripts/collect_readings.py` |
| Anything else | plain text, one `.txt` per page, one line per line |

For an OpenAI-compatible endpoint (GPUStack, vLLM, Gemini's OpenAI layer …),
`read_pages.py` sends each image with the prompt in
`prompts/sg_missiven_page_v4.md`. That is the prompt the Gemini readings used,
so the readings differ by model and not by instruction. It writes one JSON line
per page and resumes where it stopped:

```bash
set -a; . ~/.gpustack.env; set +a
uv run --with httpx python scripts/read_pages.py --images ~/sg-missiven-images \
    --model qwen3.8-27b --out raw/readings_qwen3.8-27b.jsonl --limit 5
```

`--base-url` and `--key-env` point it at another provider. Read the end of a few
pages before a full run: a page that hits `--max-tokens` comes back as an
ordinary answer that stops mid-sentence, and is marked `"truncated": true`.

## 3. Import

```bash
uv run --with lxml python scripts/import_readings.py <id> <folder or .jsonl>
```

`<id>` becomes the file name and the viewer's key. Use lower case, digits, `.`,
`-` and `_`; name it after the model, e.g. `transkribus-german-giant-ii`. The
input format is detected per file and can be forced with `--format`:

- **txt**: the page text, one line per line;
- **pagexml**: every `TextLine` in reading order (`ReadingOrder` if present,
  otherwise document order), from its `TextEquiv` with the lowest `@index`;
- **alto**: the `String/@CONTENT` of each `TextLine`;
- **jsonl**: `{"key": "<image name>", "text": "..."}` per line.

A file belongs to a page by the stem of its name. Case and the image extension
don't matter, so `StadtASG_Missive_1_1.xml`, `stadtasg_missive_1_1.jpg.txt` and
`StadtASG_Missive_1_1.JPG.xml` all match `StadtASG_Missive_1_1.JPG`. The script
lists the files that matched no page. A page without a reading simply shows that
reader greyed out.

The result is `readings/<id>.jsonl`, one line per page:

```json
{"key": "StadtASG_Missive_1_1.JPG", "text": "Min undertaenig willig dienst …\nwissenlich ist, …"}
```

`key` is exactly the edition's `pb/@facs`, and `text` uses `\n` between lines. An
optional `"truncated": true` shows a warning on that page. Empty lines are
dropped when the site is built: line engines leave one for every segment they
could not read, such as the colour chart or the ruler.

## 4. Describe the reader

Add an entry to `readings/readers.json`. The order of that list is the order of
the buttons in the viewer.

```json
{"id": "transkribus-german-giant-ii",
 "label": "Transkribus · German Giant II",
 "kind": "Transkribus HTR",
 "about": "Transkribus public model German Giant II, layout analysis by Transkribus, exported as PAGE XML on 2026-11-20.",
 "caveat": "Optional: anything a reader of the scores must know, e.g. that the model was trained on these pages."}
```

`id`, `label` and `kind` are required. `about` appears above the reading and on
the readers page. Only use `caveat` where it applies; the fine-tuned Qwen3.5-4B
needs one, because it saw most of these pages in training. The ids `edition`
and `joint` are reserved.

## 5. Check locally, then open a pull request

```bash
uv run -p 3.12 --with lxml --with rapidfuzz python scripts/build_site.py
python3 -m http.server -d site 8000      # http://localhost:8000
```

`build_site.py` prints every reader's corpus CER and median page CER against
the edition. Look at a few pages with **Compare with edition** on. Before
trusting the number, make sure the engine's conventions match the edition's
(u/v, superscript letters written into the line, ß, abbreviations): a reading
that writes `ů` where the edition has `uo` is scored as wrong on every word.

Commit `readings/<id>.jsonl` and `readings/readers.json`, then open a pull
request. The site updates when it is merged.

## Optional: joint reading and tagging

- **Joint reading.** Name the new reader in `--readers` to let it vote:
  `python scripts/joint_reading.py --readers gemini-3.8-flash,<id>,…`. Its weight
  is learned from its CER on the other half of the letters, so a weak reader
  barely counts. Leave out a reader trained on these pages, or the joint reading
  inherits its advantage.
- **Tagging.** `python scripts/ner.py run --reader <id>` learns the edition's
  tagging on that reading (5 folds by letter) and writes
  `entities/<id>.jsonl` plus a report. The viewer shows the tags under
  **Entities** for that reader. It needs a GPU or a few hours on Apple MPS.
