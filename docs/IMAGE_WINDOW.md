# Showing images of unpublished letters for a limited time

The viewer always takes a page image from the edition's IIIF server
(`media.sources-online.org`). That covers every published page and some of the
others. For letters the Stadtarchiv St. Gallen has **not** published (Dropbox
folder `12_in-process`), only the machine readings are public. The viewer says
so where the image would be.

If the Stadtarchiv agrees to show those images for a while (a workshop, a
review week), the viewer and tei.dh.unibe.ch can serve our working copies
inside a fixed window. **The window is closed by default.** It closes on its
own at its end on both sides:

- **Viewer:** `site/config.json` holds `image_window: {base, from, until}`. Outside
  those dates (the visitor's date) the viewer neither requests nor links the
  images.
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
   python3 scripts/image_window.py open --from 2026-11-20 --until 2026-11-26
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
