# Showing images of unpublished letters for a limited time

For letters the Stadtarchiv St. Gallen has **not** published (Dropbox folder
`12_in-process`), the machine readings are public, but the viewer shows **no
image**. It says so where the image would be.

The edition's IIIF server (`media.sources-online.org`) does hold most of these
images: all 80 of a random sample of 80 unedited pages, on 12.11.2026, mostly
with a lower-case `.jpg`. The edition simply doesn't link them. Whether they are
shown is the Stadtarchiv's decision, not a technical question, so the viewer
does not show them outside the window either.

When images of unpublished letters are to be shown for a while (a workshop, a
review by the edition team), the viewer opens a fixed window. Inside it, the
viewer takes the archive's own IIIF image and falls back to our working copy on
tei.dh.unibe.ch for pages the IIIF server lacks. Edited letters are not affected.
**The window is closed by default.** It closes on its own at its end on both
sides:

- **Viewer:** `site/config.json` holds `image_window: {base, from, until}`. Outside
  those dates (the visitor's date) the viewer neither requests nor links any
  image of an unedited letter. It revalidates `config.json` on every load, so a
  change reaches visitors as soon as Pages has deployed it.
- **Server:** the nginx snippet allows only the window's calendar days (server
  time). On every other day it answers `410 Gone`, even if nobody takes the
  snippet out.

## Where the images are

| What | Where |
|---|---|
| Originals from Dropbox | `/mnt/wbkolleg_dh_1/Data_SG-Missiven/12_in-process/` (research share) |
| 1500 px working copies (`<stem>.jpg`) | `/mnt/wbkolleg_dh_1/tei/datasets/sg-missiven-images/pages/` |

The working copies are also what the readers read: the gateway runs take them as
`datasets:sg-missiven-images/pages`. The share is mounted world-readable on tei
(dirs 0775, files 0664), so nginx serves them from there, and nothing is copied
onto tei's own disk.

## Opening a window

1. Agree the dates with the Stadtarchiv St. Gallen.
2. In this repository:

   ```bash
   python3 scripts/image_window.py open --from 2026-11-18 --until 2026-12-01
   ```

   This writes `site/config.json` and `deploy/nginx/sg-missiven-images.conf`.
   Commit both and merge to `main`; the Pages workflow publishes the viewer
   change.
3. On tei, as root (`dh` has no passwordless sudo for nginx):

   ```bash
   curl -fsS https://raw.githubusercontent.com/DHBern/sg-missiven-data/main/deploy/nginx/sg-missiven-images.conf | sudo tee /etc/nginx/snippets/sg-missiven-images.conf >/dev/null
   sudo grep -q 'snippets/sg-missiven-images.conf' /etc/nginx/sites-enabled/tei.dh.unibe.ch || echo 'add: include snippets/sg-missiven-images.conf;  to the TLS server block'
   sudo nginx -t && sudo systemctl reload nginx
   ```

   The `include` line goes into the TLS `server` block of
   `/etc/nginx/sites-enabled/tei.dh.unibe.ch`, next to
   `include snippets/live-translate.conf;`. It only needs adding once; later
   windows just replace the snippet file.
4. Check:

   ```bash
   curl -sI https://tei.dh.unibe.ch/sg-missiven-images/StadtASG_Missive_5_9.jpg | head -1
   ```

   Inside the window this answers `200`, outside it `410`.

## Closing early

```bash
python3 scripts/image_window.py close      # config.json: image_window null; snippet: always 410
```

Commit and merge, fetch the snippet onto tei and reload nginx as above. Even
without these steps, the window ends on its last day on both sides.

## What the snippet does

- `alias` to the working copies, with no directory listing.
- `X-Robots-Tag: noindex, nofollow, noarchive`: the images are meant for the
  window, not for search engines.
- `Access-Control-Allow-Origin: https://dhbern.github.io`.
- `Cache-Control: private, max-age=3600`, so a browser does not keep an image
  much beyond the window.

The tests behind the snippet: on nginx 1.27, `nginx -t` passes. Inside the
window a present file answers 200 and a missing one 404; on a day outside the
window, and with the closed snippet, both answer 410.
