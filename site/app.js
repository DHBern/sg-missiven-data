/* St. Gallen Missives — readings viewer. No build step, no framework. */
(() => {
  "use strict";
  const IIIF = "https://media.sources-online.org/cantaloupe/iiif/2/sg-missiven!";
  const app = document.getElementById("app");
  const state = { index: null, readers: null, registers: null, letters: new Map(),
                  osd: null, ents: true, diff: false, reader: "edition", filter: "", from: null, to: null };
  const store = {
    get(k, d) { try { const v = localStorage.getItem("sgm:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("sgm:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  // one way of showing a reading at a time: entities, the comparison with the edition or the
  // uncertain words do not combine, so a single choice replaces the three switches
  state.mode = store.get("mode", "ents");
  state.reader = store.get("reader", "edition");

  /* Language: ?lang=de|en, else the viewer's last choice, else the browser's. */
  const urlLang = new URLSearchParams(location.search).get("lang");
  let LANG = ["de", "en"].includes(urlLang) ? urlLang
    : store.get("lang", (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en");
  /* T("English", "Deutsch"): every visible string in both languages, side by side */
  const T = (en, de) => (LANG === "de" ? de : en);
  const P = (n, en1, enN, de1, deN) => (n === 1 ? T(en1, de1) : T(enN, deN));
  const loc = (r, k) => (LANG === "de" && r[k + "_de"]) || r[k] || "";
  function applyLang() {
    document.documentElement.lang = LANG;
    document.title = T("St. Gallen Missives Readings", "St. Galler Missiven: Lesungen");
    const nav = { letters: T("Letters", "Briefe"), search: T("Search", "Suche"), places: T("Places", "Orte"), names: T("Names", "Namen"),
                  map: T("Map", "Karte"), keywords: T("Keywords", "Schlagwörter"), readers: T("Readers", "Leser"), about: T("About", "Über") };
    document.querySelectorAll("[data-nav]").forEach((a) => { a.textContent = nav[a.dataset.nav]; });
    const brand = document.querySelector(".brand");
    if (brand) brand.innerHTML = `${T("St. Gallen Missives", "St. Galler Missiven")} <span>· ${T("readings", "Lesungen")}</span>`;
    document.querySelectorAll("[data-lang]").forEach((b) => {
      b.classList.toggle("on", b.dataset.lang === LANG);
      b.setAttribute("aria-pressed", String(b.dataset.lang === LANG));
    });
  }
  function setLang(l) {
    LANG = l;
    store.set("lang", l);
    const u = new URL(location.href);
    u.searchParams.set("lang", l);
    history.replaceState(null, "", u);
    applyLang();
    route();
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // a reader's footnote (readers.json "footnote"): a marker after its description, the note below, URLs linked
  const fnMark = (r) => (r.footnote ? `<sup class="fn-mark">1</sup>` : "");
  const fnHtml = (r) => (r.footnote ? `<div class="footnote"><sup>1</sup> ${esc(loc(r, "footnote")).replace(/https?:\/\/[^\s)]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`)}</div>` : "");
  const pct = (x) => (x == null ? "–" : (x * 100).toFixed(1).replace(".", LANG === "de" ? "," : ".") + " %");
  const fmtDate = (d) => {
    if (!d) return T("undated", "undatiert");
    const m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/.exec(d);
    if (!m) return d;
    return [m[3], m[2], m[1]].filter(Boolean).map((x) => String(+x)).join(".");
  };
  /* "Nr. 177 Beilage 1" from the title; the id's digits would read 17701 */
  const nrOf = (l) => { const m = /Nr\.\s*(.+)$/.exec(l.title || ""); return m ? "Nr. " + m[1] : l.id; };
  const byId = (id) => state.index.find((l) => l.id === id);
  const json = (p) => fetch(p).then((r) => { if (!r.ok) throw new Error(p + ": " + r.status); return r.json(); });

  async function boot() {
    [state.index, state.readers, state.registers, state.config] = await Promise.all([
      json("data/index.json"), json("data/readers.json"), json("data/registers.json"),
      fetch("config.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : {})).catch(() => ({}))]);
    window.addEventListener("hashchange", route);
    document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
    applyLang();
    route();
  }

  function setNav(which) {
    if (!["places", "names", "map"].includes(which)) destroyMaps();
    if (which !== "search") clearTimeout(searchTimer);
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === which));
  }

  function route() {
    hideTip();
    const [path, qs] = location.hash.replace(/^#\/?/, "").split("?");
    const parts = path.split("/").map(decodeURIComponent);
    if (parts[0] === "search") return showSearch(new URLSearchParams(qs || ""));
    if (parts[0] === "letter" && parts[1]) return showLetter(parts[1], +(parts[2] || 1), parts[3]);
    if (parts[0] === "readers") return showReaders();
    if (parts[0] === "places") return showRegister("places");
    if (parts[0] === "names") return showRegister("names");
    if (parts[0] === "map") return showMap();
    if (parts[0] === "keywords" && parts[1] === "network") return showKeywordNetwork(new URLSearchParams(qs || ""));
    if (parts[0] === "keywords") return showKeywords();
    if (parts[0] === "keyword" && parts[1]) return showKeyword(parts[1], new URLSearchParams(qs || ""));
    if (parts[0] === "entry" && parts[1]) return showEntry(parts[1]);
    if (parts[0] === "about") return showAbout();
    return showList(new URLSearchParams(qs || ""));
  }

  /* ---------- year slider (letters list and search) ---------- */
  const Y_BIN = 10;
  function sliderHtml(id, min, max, from, to) {
    return `<div class="yrange">
          <div class="yr-head"><span>${T("Years", "Jahre")}</span><b id="${id}-lab">${from}–${to}</b></div>
          <div class="yr-track" title="${T("Letters not yet edited are placed by their estimated year.", "Noch nicht edierte Briefe stehen bei ihrem geschätzten Jahr.")}">
            <div class="yr-hist" id="${id}-hist" aria-hidden="true"></div>
            <input type="range" id="${id}-from" min="${min}" max="${max}" step="1" value="${from}" aria-label="${T("From year", "Von Jahr")}">
            <input type="range" id="${id}-to" min="${min}" max="${max}" step="1" value="${to}" aria-label="${T("To year", "Bis Jahr")}">
          </div>
        </div>`;
  }
  // onInput(from, to) while dragging; onChange() when a handle is let go
  function bindSlider(id, onInput, onChange) {
    const a = document.getElementById(`${id}-from`), b = document.getElementById(`${id}-to`);
    const move = (ev) => {
      let x = +a.value, y = +b.value;
      if (x > y) { if (ev.target === a) x = y; else y = x; a.value = x; b.value = y; }
      // the handle last moved stays on top, so two handles at one end can still be pulled apart
      a.style.zIndex = ev.target === a ? 3 : 2; b.style.zIndex = ev.target === b ? 3 : 2;
      document.getElementById(`${id}-lab`).textContent = `${x}–${y}`;
      onInput(x, y);
    };
    a.addEventListener("input", move); b.addEventListener("input", move);
    if (onChange) { a.addEventListener("change", onChange); b.addEventListener("change", onChange); }
  }
  // years: the year of every letter that passes the other filters (null for none)
  function paintSlider(id, years, min, max, from, to) {
    const b0 = Math.floor(min / Y_BIN), bins = new Array(Math.floor(max / Y_BIN) - b0 + 1).fill(0);
    for (const y of years) if (y != null && y >= min && y <= max) bins[Math.floor(y / Y_BIN) - b0]++;
    const top = Math.max(1, ...bins);
    document.getElementById(`${id}-hist`).innerHTML = bins.map((n, i) => {
      const lo = (b0 + i) * Y_BIN;
      const lab = `${lo}–${lo + Y_BIN - 1}: ${n} ${P(n, "letter", "letters", "Brief", "Briefe")}`;
      return `<i class="${lo + Y_BIN - 1 >= from && lo <= to ? "in" : ""}" data-lo="${lo}" title="${lab}" style="height:${n ? Math.max(6, Math.round(100 * n / top)) : 0}%"></i>`;
    }).join("");
    document.getElementById(`${id}-lab`).textContent = `${from}–${to}`;
  }
  /* a click on a bar narrows the years to its decade; a second click on the same bar widens them again */
  function bindBars(id, min, max, get, set) {
    const hist = document.getElementById(`${id}-hist`);
    hist.removeAttribute("aria-hidden");
    hist.addEventListener("click", (ev) => {
      const bar = ev.target.closest("i[data-lo]");
      if (!bar) return;
      let lo = Math.max(min, +bar.dataset.lo), hi = Math.min(max, +bar.dataset.lo + Y_BIN - 1);
      const [from, to] = get();
      if (from === lo && to === hi) { lo = min; hi = max; }
      document.getElementById(`${id}-from`).value = lo;
      document.getElementById(`${id}-to`).value = hi;
      document.getElementById(`${id}-lab`).textContent = `${lo}–${hi}`;
      set(lo, hi);
    });
  }
  const yArg = (v, d, min, max) => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : d; };

  /* ---------- letters list ---------- */
  function showList(params) {
    setNav("letters");
    state.edited = params && ["0", "1"].includes(params.get("ed")) ? params.get("ed") : "";
    destroyOsd();
    // Edited letters carry a date, unedited ones only an estimated year.
    const yNum = (l) => { const y = parseInt((l.date || String(l.year || "")).slice(0, 4), 10); return Number.isFinite(y) ? y : null; };
    const ys = state.index.map(yNum).filter((y) => y != null);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    state.from = yArg(params && params.get("from"), yMin, yMin, yMax);
    state.to = yArg(params && params.get("to"), yMax, yMin, yMax);
    if (state.from > state.to) [state.from, state.to] = [state.to, state.from];
    const best = state.readers.filter((r) => r.cer != null).sort((a, b) => a.cer - b.cer)[0];
    app.innerHTML = `
      <section class="intro">
        <h1>${T("The letters of St. Gallen, read by machines", "Die St. Galler Missiven, von Maschinen gelesen")}</h1>
        <p>${T(`${state.index.filter((l) => l.edited !== false).length} letters to and from the city of St. Gallen, 1400–1650, from the edition of the
        <a href="https://missiven.stadtarchiv.ch/" target="_blank" rel="noopener">Stadtarchiv St. Gallen</a>${unedited() ? `, and <a href="#/?ed=0">${unedited()} letters of the series the edition has not yet published</a>` : ""}.
        Every page is shown with the edition's transcription, where there is one, and with ${machineReaders()}.
        Switch between them on the left, compare any of them with the edition, and see where they differ.`,
        `${state.index.filter((l) => l.edited !== false).length} Briefe an die und von der Stadt St. Gallen, 1400–1650, aus der Edition des
        <a href="https://missiven.stadtarchiv.ch/" target="_blank" rel="noopener">Stadtarchivs St. Gallen</a>${unedited() ? `, dazu <a href="#/?ed=0">${unedited()} Briefe der Serie, die die Edition noch nicht veröffentlicht hat</a>` : ""}.
        Jede Seite erscheint mit der Transkription der Edition, wo es eine gibt, und mit ${machineReaders()}.
        Links lässt sich zwischen ihnen wechseln; jede Lesung lässt sich mit der Edition vergleichen.`)}</p>
        ${best ? `<p class="muted small">${T("Closest to the edition over all scored pages:", "Am nächsten an der Edition über alle bewerteten Seiten:")} <b>${esc(loc(best, "label"))}</b>, ${pct(best.cer)} ${T("character error rate", "Zeichenfehlerrate")}. <a href="#/readers">${T("All readers", "Alle Leser")}</a>.</p>` : ""}
      </section>
      <div class="filters">
        <input id="q" type="search" placeholder="${T("Search sender, recipient, regest, number…", "Absender, Empfänger, Regest, Nummer suchen…")}" value="${esc(state.filter)}" aria-label="${T("Search letters", "Briefe durchsuchen")}">
        <div class="seg" id="ed" role="group" aria-label="${T("Edited or not", "Ediert oder nicht")}">
          ${[["", T("All", "Alle"), state.index.length],
             ["1", T("Edited", "Ediert"), state.index.length - unedited()],
             ["0", T("Not yet edited", "Noch nicht ediert"), unedited()]].map(([v, label, n]) =>
            `<button type="button" data-v="${v}" aria-pressed="${(state.edited || "") === v}" class="${(state.edited || "") === v ? "on" : ""}">${label} <span class="n">${n}</span></button>`).join("")}
        </div>
        ${sliderHtml("ly", yMin, yMax, state.from, state.to)}
        <span id="count" class="muted small"></span>
      </div>
      <div class="list" id="list"></div>`;
    const q = document.getElementById("q");
    const ed = document.getElementById("ed");
    const sync = () => {
      const p = new URLSearchParams();
      if (state.edited) p.set("ed", state.edited);
      if (state.from > yMin) p.set("from", state.from);
      if (state.to < yMax) p.set("to", state.to);
      const h = "#/" + (p.toString() ? "?" + p : "");
      if (location.hash !== h && (location.hash || h !== "#/")) history.replaceState(null, "", location.pathname + location.search + h);
    };
    const draw = () => {
      state.filter = q.value;
      const narrowed = state.from > yMin || state.to < yMax;
      const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
      const years = [];
      const rows = state.index.filter((l) => {
        if (state.edited && String(+(l.edited !== false)) !== state.edited) return false;
        if (words.length) {
          const hay = [l.title, l.idno, l.date, fmtDate(l.date), l.sender.join(" "), l.recipient.join(" "), l.regest].join(" ").toLowerCase();
          if (!words.every((w) => hay.includes(w))) return false;
        }
        const y = yNum(l);
        years.push(y);
        return !narrowed || (y != null && y >= state.from && y <= state.to);
      });
      paintSlider("ly", years, yMin, yMax, state.from, state.to);
      document.getElementById("count").textContent = `${rows.length} ${P(rows.length, "letter", "letters", "Brief", "Briefe")}`;
      document.getElementById("list").innerHTML = rows.slice(0, 400).map((l) => `
        <a class="row" href="#/letter/${l.id}/1">
          <span class="num">${esc(nrOf(l))}</span>
          <span class="date">${l.edited === false ? (l.year ? esc(l.year) + "?" : "") : esc(fmtDate(l.date))}</span>
          ${l.edited === false
            ? (l.draft
              ? `<span><span class="who">${esc(l.sender.join(", ") || "?")} → ${esc(l.recipient.join(", ") || "?")} <span class="muted small">· ${T("not yet edited · draft", "noch nicht ediert · Entwurf")}</span></span><span class="reg">${esc(l.regest)}</span></span>`
              : `<span><span class="who muted">${T("Not yet edited", "Noch nicht ediert")}</span><span class="reg">${l.pages} ${P(l.pages, "page", "pages", "Seite", "Seiten")}, ${T("machine readings only", "nur maschinelle Lesungen")}</span></span>`)
            : `<span><span class="who">${esc(l.sender.join(", ") || "?")} → ${esc(l.recipient.join(", ") || "?")}</span>
          <span class="reg">${esc(l.regest)}</span></span>`}
        </a>`).join("") + (rows.length > 400 ? `<p class="muted small">${T(`Showing 400 of ${rows.length} — narrow the search.`, `400 von ${rows.length} angezeigt – Suche eingrenzen.`)}</p>` : "");
    };
    q.addEventListener("input", draw);
    bindSlider("ly", (a, b) => { state.from = a; state.to = b; draw(); }, sync);
    bindBars("ly", yMin, yMax, () => [state.from, state.to], (a, b) => { state.from = a; state.to = b; sync(); draw(); });
    ed.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-v]");
      if (!b) return;
      state.edited = b.dataset.v;
      ed.querySelectorAll("button").forEach((x) => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", on); });
      sync();
      draw();
    });
    draw();
  }

  function unedited() { return state.index.filter((l) => l.edited === false).length; }

  function machineReaders() {
    const rs = state.readers.filter((r) => r.id !== "edition");
    const names = rs.map((r) => esc(loc(r, "label")));
    const list = names.length > 1 ? names.slice(0, -1).join(", ") + T(" and ", " und ") + names[names.length - 1] : names[0] || "";
    return T(`the readings of ${list}`, `den Lesungen von ${list}`);
  }

  /* ---------- readers ---------- */
  function showReaders() {
    setNav("readers");
    destroyOsd();
    const scored = state.readers.filter((r) => r.cer != null);
    const max = Math.max(...scored.map((r) => r.cer), 0.01);
    app.innerHTML = `
      <section class="prose">
        <h1>${T("Readers", "Leser")}</h1>
        <p>${T(`Each automatic reading is compared with the edition's text of the same page: character error rate
        (CER) = edits needed to turn the reading into the edition ÷ characters of the edition, with whitespace
        collapsed. Corpus CER sums over all scored pages; the median is the typical page.`,
        `Jede automatische Lesung wird mit dem Text der Edition auf derselben Seite verglichen: Zeichenfehlerrate
        (CER) = Änderungen, die aus der Lesung den Text der Edition machen ÷ Zeichen der Edition, Leerraum
        zusammengefasst. Die Korpus-CER summiert über alle bewerteten Seiten; der Median ist die typische Seite.`)}</p>
        <p class="muted small">${T(`A distance to <em>this edition's</em> conventions, not a verdict on palaeography. A reader that writes
        <i>ů</i> where the edition writes <i>uo</i>, keeps <i>ſ</i>, or reads the archivist's later docket is counted as wrong.
        Pages without edition text (blank backs, address leaves the edition leaves out) are read but not scored.`,
        `Gemessen wird der Abstand zu den Konventionen <em>dieser</em> Edition, kein Urteil über Paläographie. Wer
        <i>ů</i> schreibt, wo die Edition <i>uo</i> schreibt, <i>ſ</i> beibehält oder den späteren Dorsalvermerk des Archivars liest, wird als Fehler gezählt.
        Seiten ohne Text der Edition (leere Rückseiten, Adressblätter, die die Edition weglässt) werden gelesen, aber nicht bewertet.`)}</p>
      </section>
      <div class="scroll"><table class="scores">
        <thead><tr><th>${T("Reader", "Leser")}</th><th>${T("Kind", "Art")}</th><th>CER</th><th>${T("Median page", "Median pro Seite")}</th><th>${T("Pages read / scored", "Seiten gelesen / bewertet")}</th><th>${T("About", "Beschreibung")}</th></tr></thead>
        <tbody>${state.readers.filter((r) => r.id !== "edition").map((r) => `
          <tr><td><b>${esc(loc(r, "label"))}</b></td><td>${esc(loc(r, "kind"))}</td>
          <td class="n">${pct(r.cer)}<div class="bar"><i style="width:${r.cer == null ? 0 : (100 * r.cer / max).toFixed(1)}%"></i></div></td>
          <td class="n">${pct(r.median_page_cer)}</td>
          <td class="n">${r.pages_read ?? 0} / ${r.pages_scored ?? 0}</td>
          <td class="small">${esc(loc(r, "about"))}${fnMark(r)}${r.caveat ? `<div class="caveat">${esc(loc(r, "caveat"))}</div>` : ""}${r.note ? `<div class="muted">${esc(loc(r, "note"))}</div>` : ""}${fnHtml(r)}</td></tr>`).join("")}
        </tbody></table></div>`;
  }

  function showAbout() {
    setNav("about");
    destroyOsd();
    app.innerHTML = LANG === "de" ? `<section class="prose">
      <h1>Über diese Seite</h1>
      <p>Eine Demonstration der <a href="https://www.dh.unibe.ch/" target="_blank" rel="noopener">Digital Humanities der Universität Bern</a>
      an der Edition <i>Briefverkehr der Stadt St. Gallen 1400–1650</i> des Stadtarchivs und der Vadianischen Sammlung der Ortsbürgergemeinde St. Gallen
      (<a href="https://missiven.stadtarchiv.ch/" target="_blank" rel="noopener">missiven.stadtarchiv.ch</a>).
      Das TEI der Edition steht unter CC BY-SA 4.0; die Bilder kommen vom IIIF-Server der Edition.</p>
      <h2>Was zu sehen ist</h2>
      <ul>
        <li><b>Edition</b>: die edierte Transkription mit der Auszeichnung von Personen, Organisationen, Orten und Daten durch die Editorinnen und Editoren. Ein Name zeigt beim Darüberfahren seinen Registereintrag.</li>
        <li><b>Maschinelle Lesungen</b>: jedes Seitenbild ohne Hilfe der Edition gelesen, als ganze Seite (Vision-Language-Modelle) oder Zeile für Zeile nach automatischer Segmentierung (kraken, TrOCR).</li>
        <li><b>Gemeinsame Lesung</b>: die maschinellen Lesungen aneinander ausgerichtet und zu einer zusammengeführt; wie, steht auf der Seite <a href="#/readers">Leser</a>.</li>
        <li><b>Mit der Edition vergleichen</b>: Wörter, die die Lesung hat und die Edition nicht, sind <ins>grün</ins>, Wörter der Edition, die der Lesung fehlen, <del>rot</del> markiert.</li>
        <li><b>Entitäten</b>: in der Edition die Auszeichnung der Editorinnen und Editoren; in den Lesungen die automatische Auszeichnung, gelernt aus der Auszeichnung der Edition und mit ihren Registern verknüpft.</li>
        <li><b>Orte, Namen, Karte</b>: die Register der Edition mit allen edierten Briefen, die einen Eintrag nennen; Funde in noch nicht edierten Briefen stehen getrennt und sind automatisch.</li>
      </ul>
      <h2>Daten und Code</h2>
      <p><a href="https://github.com/DHBern/sg-missiven-data" target="_blank" rel="noopener">github.com/DHBern/sg-missiven-data</a> – die Lesungen liegen in <code>readings/</code>, eine JSON-Zeile pro Seite und Leser.</p>
    </section>` : `<section class="prose">
      <h1>About</h1>
      <p>A demonstration by <a href="https://www.dh.unibe.ch/" target="_blank" rel="noopener">Digital Humanities, University of Bern</a>,
      on the edition <i>Briefverkehr der Stadt St. Gallen 1400–1650</i> of the Stadtarchiv und Vadianische Sammlung der Ortsbürgergemeinde St. Gallen
      (<a href="https://missiven.stadtarchiv.ch/" target="_blank" rel="noopener">missiven.stadtarchiv.ch</a>).
      The edition's TEI is CC BY-SA 4.0; images are served by the edition's IIIF server.</p>
      <h2>What you see</h2>
      <ul>
        <li><b>Edition</b>: the edited transcription with the editors' tagging of persons, organisations, places and dates. Hover a name for the register entry.</li>
        <li><b>Machine readings</b>: every page image read without the edition's help, as whole pages (vision-language models) or line by line after automatic segmentation (kraken, TrOCR).</li>
        <li><b>Joint reading</b>: the machine readings aligned and combined into one; how it is built is on the <a href="#/readers">readers</a> page.</li>
        <li><b>Compare with edition</b>: words the reading has and the edition does not are marked <ins>green</ins>, words of the edition the reading misses <del>red</del>.</li>
        <li><b>Entities</b>: on the edition the editors' tagging; on the machine readings the automatic tagging, learned from the edition's own tagging and linked to its registers.</li>
        <li><b>Places, Names, Map</b>: the edition's registers with every edited letter that names an entry; finds in letters not yet edited are listed apart and are automatic.</li>
      </ul>
      <h2>Data and code</h2>
      <p><a href="https://github.com/DHBern/sg-missiven-data" target="_blank" rel="noopener">github.com/DHBern/sg-missiven-data</a> — the readings are in <code>readings/</code>, one JSON line per page and reader.</p>
    </section>`;
  }

  /* ---------- registers and map ---------- */
  /* The edition's registers with every edited letter that names an entry, in the
     text or in the header. Built from the TEI by scripts/build_site.py; the
     automatic tagging of letters not yet edited will add to it later. */
  const FTYPE_EN = { P: "Settlement", A: "Region, state", S: "Building, site", H: "Water", L: "Area", T: "Mountain", R: "Road", V: "Forest", U: "Undersea" };
  const FTYPE_DE = { P: "Siedlung", A: "Region, Staat", S: "Gebäude, Stätte", H: "Gewässer", L: "Gebiet", T: "Berg", R: "Strasse", V: "Wald", U: "Unterwasser" };
  const ftype = (k) => (LANG === "de" ? FTYPE_DE : FTYPE_EN)[k];
  const kindName = (k) => ({ place: T("Place", "Ort"), person: T("Person", "Person"), org: T("Organisation", "Organisation") }[k]);
  const roleName = (r) => ({ sender: T("sender", "Absender"), recipient: T("recipient", "Empfänger"), issuer: T("issuer", "Aussteller"),
                             place: T("place of writing", "Ausstellungsort") }[r] || r);
  const lettersOf = (e) => new Set((e.occ || []).map((o) => o[0])).size;
  const autoLettersOf = (e) => new Set((e.occ_auto || []).map((o) => o[0])).size;
  let leafletMaps = [];
  function destroyMaps() { leafletMaps.forEach((m) => m.remove()); leafletMaps = []; }

  function baseMap(el, opts = {}) {
    const m = L.map(el, { scrollWheelZoom: false, ...opts });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(m);
    leafletMaps.push(m);
    return m;
  }

  function marker(id, e, m) {
    const n = lettersOf(e);
    return L.circleMarker(e.geo, { radius: Math.min(22, 4 + Math.sqrt(n) * 1.6), className: "lf-place", weight: 1.5, fillOpacity: 0.45 })
      .bindTooltip(`${esc(e.name)} · ${n} ${P(n, "letter", "letters", "Brief", "Briefe")}`)
      .on("click", () => { location.hash = `#/entry/${id}`; })
      .addTo(m);
  }

  /* authority links of a register entry, deduplicated, best known first */
  const LINK_LABEL = [
    ["hls", "HLS"], ["hls-dhs-dss", "HLS"], ["metagrid", "Metagrid"], ["wikidata", "Wikidata"], ["gnd", "GND"],
    ["deutsche-biographie", "Deutsche Biographie"], ["ssrq", "SSRQ"], ["histhub", "histHub"], ["geonames", "GeoNames"],
    ["ortsnamen", "ortsnamen.ch"], ["wikipedia-de", "Wikipedia"], ["wikipedia-en", "Wikipedia (en)"], ["viaf", "VIAF"],
    ["rag", "RAG"], ["kalliope-verbund", "Kalliope"], ["ddb", "DDB"], ["lcnaf", "LoC"], ["bnf", "BnF"], ["idref", "IdRef"],
    ["encyclopaedia-britannica-online", "Britannica"],
  ];
  function authLinks(e) {
    const seen = new Set(), out = [];
    const rank = (t) => { const i = LINK_LABEL.findIndex(([k]) => k === t); return i < 0 ? LINK_LABEL.length : i; };
    for (const l of (e.links || []).filter((l) => /^https?:/.test(l.url)).sort((a, b) => rank(a.type) - rank(b.type))) {
      const hit = LINK_LABEL.find(([k]) => k === l.type);
      const label = hit ? hit[1] : l.type;
      if (seen.has(label)) continue;
      seen.add(label); out.push({ label, url: l.url });
    }
    return out;
  }
  const linkChip = (l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a>`;

  function showRegister(which) {
    setNav(which);
    destroyOsd(); destroyMaps();
    const kinds = which === "places" ? ["place"] : ["person", "org"];
    const all = Object.entries(state.registers).filter(([, e]) => kinds.includes(e.kind))
      .sort((a, b) => (a[1].sort || a[1].name).localeCompare(b[1].sort || b[1].name, "de"));
    const f = state.regFilter = state.regFilter || {};
    const noGeo = all.filter(([, e]) => !e.geo).length;
    app.innerHTML = `
      <section class="intro">
        <h1>${which === "places" ? T("Places", "Orte") : T("Names", "Namen")}</h1>
        <p>${which === "places"
          ? T(`The edition's place register: ${all.length} places, ${all.length - noGeo} of them with coordinates (<a href="#/map">map</a>).`,
              `Das Ortsregister der Edition: ${all.length} Orte, ${all.length - noGeo} davon mit Koordinaten (<a href="#/map">Karte</a>).`)
          : T(`The edition's register of persons and organisations: ${all.filter(([, e]) => e.kind === "person").length} persons, ${all.filter(([, e]) => e.kind === "org").length} organisations.`,
              `Das Personen- und Organisationsregister der Edition: ${all.filter(([, e]) => e.kind === "person").length} Personen, ${all.filter(([, e]) => e.kind === "org").length} Organisationen.`)}
        ${T(`Each entry lists the edited letters that name it, in the text or as sender, recipient or place of writing.
        Letters not yet edited are listed apart: their names come from the automatic tagging of the machine readings, linked to the register by rule and not checked.`,
        `Jeder Eintrag nennt die edierten Briefe, die ihn erwähnen, im Text oder als Absender, Empfänger oder Ausstellungsort.
        Noch nicht edierte Briefe stehen getrennt: ihre Namen stammen aus der automatischen Auszeichnung der maschinellen Lesungen, sind nach Regeln mit dem Register verknüpft und nicht geprüft.`)}</p>
      </section>
      <div class="filters">
        <input id="rq" type="search" placeholder="${T("Search names, description, country…", "Namen, Beschreibung, Land suchen…")}" value="${esc(f[which] || "")}" aria-label="${T("Search the register", "Register durchsuchen")}">
        ${which === "places"
          ? `<select id="rt" aria-label="${T("Type", "Typ")}"><option value="">${T("All types", "Alle Typen")}</option>${Object.keys(FTYPE_EN).filter((k) => all.some(([, e]) => e.ftype === k)).map((k) => `<option value="${k}" ${f.ftype === k ? "selected" : ""}>${ftype(k)}</option>`).join("")}</select>
             <label class="small"><input type="checkbox" id="rg" ${f.nogeo ? "checked" : ""}> ${T("without coordinates", "ohne Koordinaten")} (${noGeo})</label>`
          : `<select id="rt" aria-label="${T("Kind", "Art")}"><option value="">${T("Persons and organisations", "Personen und Organisationen")}</option><option value="person" ${f.kind === "person" ? "selected" : ""}>${T("Persons", "Personen")}</option><option value="org" ${f.kind === "org" ? "selected" : ""}>${T("Organisations", "Organisationen")}</option></select>`}
        <select id="ra" aria-label="${T("Authority links", "Normdaten-Links")}">
          <option value="">${T("Any links", "Alle Links")}</option>
          <option value="has" ${f.auth === "has" ? "selected" : ""}>${T("With links", "Mit Links")} (${all.filter(([, e]) => authLinks(e).length).length})</option>
          <option value="none" ${f.auth === "none" ? "selected" : ""}>${T("Without links", "Ohne Links")} (${all.filter(([, e]) => !authLinks(e).length).length})</option>
          ${[...new Set(LINK_LABEL.map(([, l]) => l))].filter((l) => all.some(([, e]) => authLinks(e).some((x) => x.label === l)))
            .map((l) => `<option value="${esc(l)}" ${f.auth === l ? "selected" : ""}>${T("With", "Mit")} ${esc(l)} (${all.filter(([, e]) => authLinks(e).some((x) => x.label === l)).length})</option>`).join("")}
        </select>
        <label class="small"><input type="checkbox" id="ru" ${f.auto ? "checked" : ""}> ${T("in letters not yet edited", "in noch nicht edierten Briefen")} (${all.filter(([, e]) => e.occ_auto).length})</label>
        <span id="rcount" class="muted small"></span>
      </div>
      <div class="list reg" id="rlist"></div>`;
    const q = document.getElementById("rq"), t = document.getElementById("rt"), g = document.getElementById("rg"), u = document.getElementById("ru"), a = document.getElementById("ra");
    const draw = () => {
      f[which] = q.value;
      if (which === "places") { f.ftype = t.value; f.nogeo = g.checked; } else f.kind = t.value;
      f.auto = u.checked; f.auth = a.value;
      const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
      const rows = all.filter(([, e]) => {
        if (which === "places" && t.value && e.ftype !== t.value) return false;
        if (which === "places" && g.checked && e.geo) return false;
        if (which === "names" && t.value && e.kind !== t.value) return false;
        if (u.checked && !e.occ_auto) return false;
        if (a.value) {
          const ls = authLinks(e);
          if (a.value === "has" ? !ls.length : a.value === "none" ? ls.length : !ls.some((l) => l.label === a.value)) return false;
        }
        if (!words.length) return true;
        const hay = [e.name, e.desc, e.country, ...(e.occ || []).map((o) => o[1]), ...(e.occ_auto || []).map((o) => o[1])].join(" ").toLowerCase();
        return words.every((w) => hay.includes(w));
      });
      document.getElementById("rcount").textContent = `${rows.length} ${P(rows.length, "entry", "entries", "Eintrag", "Einträge")}`;
      document.getElementById("rlist").innerHTML = rows.slice(0, 500).map(([id, e]) => {
        const n = lettersOf(e);
        const ext = authLinks(e), MAX = 6;
        return `<div class="row rrow">
          <span class="num">${n || "–"}<span class="muted small"> ${P(n, "letter", "letters", "Brief", "Briefe")}</span></span>
          <span><a class="who go" href="#/entry/${encodeURIComponent(id)}">${esc(e.name)}</a>${which === "places" && !e.geo ? ` <span class="badge">${T("no coordinates", "ohne Koordinaten")}</span>` : ""}${autoLettersOf(e) ? ` <span class="badge auto">+${autoLettersOf(e)} ${T("not yet edited", "noch nicht ediert")}</span>` : ""}
          <span class="reg">${esc([which === "places" ? ftype(e.ftype) : kindName(e.kind), e.country, e.desc].filter(Boolean).join(" · "))}</span></span>
          <span class="ext">${ext.slice(0, MAX).map(linkChip).join("")}${ext.length > MAX ? `<a href="#/entry/${encodeURIComponent(id)}" class="more">+${ext.length - MAX}</a>` : ""}</span>
        </div>`;
      }).join("") + (rows.length > 500 ? `<p class="muted small">${T(`Showing 500 of ${rows.length} — narrow the search.`, `500 von ${rows.length} angezeigt – Suche eingrenzen.`)}</p>` : "");
    };
    q.addEventListener("input", draw);
    t.addEventListener("change", draw);
    g && g.addEventListener("change", draw);
    u.addEventListener("change", draw);
    a.addEventListener("change", draw);
    draw();
  }

  function showEntry(id) {
    destroyOsd(); destroyMaps();
    const e = state.registers[id];
    if (!e) { app.innerHTML = `<p>${T("No register entry", "Kein Registereintrag")} ${esc(id)}.</p>`; return; }
    setNav(e.kind === "place" ? "places" : "names");
    const groups = new Map();
    for (const [lid, form, role] of e.occ || []) {
      if (!groups.has(lid)) groups.set(lid, { forms: new Set(), roles: new Set() });
      const g = groups.get(lid);
      if (form) g.forms.add(form);
      if (role) g.roles.add(role);
    }
    const rows = [...groups.entries()].map(([lid, g]) => ({ l: byId(lid), ...g })).filter((r) => r.l)
      .sort((a, b) => state.index.indexOf(a.l) - state.index.indexOf(b.l));
    const agroups = new Map();
    for (const [lid, form, role] of e.occ_auto || []) {
      if (!agroups.has(lid)) agroups.set(lid, { forms: new Set(), roles: new Set() });
      agroups.get(lid).forms.add(form);
      if (role) agroups.get(lid).roles.add(role);
    }
    const autoRows = [...agroups.entries()].map(([lid, g]) => ({ l: byId(lid), ...g })).filter((r) => r.l)
      .sort((a, b) => state.index.indexOf(a.l) - state.index.indexOf(b.l));
    const links = authLinks(e);
    app.innerHTML = `
      <section class="entry">
        <p class="muted small"><a href="#/${e.kind === "place" ? "places" : "names"}">← ${e.kind === "place" ? T("Places", "Orte") : T("Names", "Namen")}</a></p>
        <h1>${esc(e.name)}</h1>
        <p class="muted">${esc([e.kind === "place" ? ftype(e.ftype) || kindName("place") : kindName(e.kind), e.country, e.desc].filter(Boolean).join(" · "))}</p>
        ${links.length ? `<p class="links">${links.map(linkChip).join("")}</p>` : ""}
        <p class="small muted">Register <code>${esc(id)}</code>${e.kind === "place" && e.geo ? ` · ${e.geo[0].toFixed(4)}, ${e.geo[1].toFixed(4)}` : ""} ·
          <a href="https://missiven.stadtarchiv.ch/${e.kind === "place" ? "orte" : "namen"}/${encodeURIComponent(id)}" target="_blank" rel="noopener">${T("in the edition", "in der Edition")} ↗</a></p>
        ${e.kind === "place" ? (e.geo ? `<div id="emap" class="emap"></div>` : `<p class="note-box">${T("The register has no coordinates for this place, so it is not on the map.", "Das Register gibt für diesen Ort keine Koordinaten, darum fehlt er auf der Karte.")}</p>`) : ""}
        <h2>${rows.length} ${P(rows.length, "edited letter", "edited letters", "edierter Brief", "edierte Briefe")}</h2>

        <div class="list">${rows.map((r) => `
          <a class="row" href="#/letter/${r.l.id}/1">
            <span class="num">${esc(nrOf(r.l))}</span>
            <span class="date">${esc(fmtDate(r.l.date))}</span>
            <span><span class="who">${esc(r.l.sender.join(", ") || "?")} → ${esc(r.l.recipient.join(", ") || "?")}</span>
            <span class="reg">${r.forms.size ? `<i>${[...r.forms].map(esc).join("</i>, <i>")}</i>` : ""}${r.roles.size ? `${r.forms.size ? " · " : ""}${[...r.roles].map(roleName).join(", ")}` : ""}</span></span>
          </a>`).join("")}</div>
        <h2>${autoRows.length ? autoRows.length + " " : ""}${P(autoRows.length, "Letter not yet edited", "Letters not yet edited", "noch nicht edierter Brief", "Noch nicht edierte Briefe")} <span class="badge">${T("automatic", "automatisch")}</span></h2>
        ${autoRows.length
          ? `<p class="muted small">${T("Found by the automatic tagging of the machine reading and linked to this entry by rule; neither is checked.",
              "Gefunden von der automatischen Auszeichnung der maschinellen Lesung und nach Regeln mit diesem Eintrag verknüpft; beides ist nicht geprüft.")}</p>`
          : `<p class="muted small">${unedited() ? T(`None found so far in the ${unedited()} letters not yet edited.`, `Bisher in keinem der ${unedited()} noch nicht edierten Briefe gefunden.`) : T("No letters not yet edited are read yet.", "Noch kein nicht edierter Brief ist gelesen.")}
             ${T("Names are found by the automatic tagging of the machine readings and linked to the register only where the link is reliable enough",
                 "Namen findet die automatische Auszeichnung der maschinellen Lesungen; mit dem Register verknüpft werden sie nur, wo die Verknüpfung verlässlich genug ist")}${e.kind === "person" ? T(" (persons: only if the edition attests them within 40 years of the letter)", " (Personen: nur, wenn die Edition sie innerhalb von 40 Jahren um den Brief belegt)") : ""}.</p>`}
        <div class="list">${autoRows.map((r) => `
          <a class="row" href="#/letter/${r.l.id}/1">
            <span class="num">${esc(nrOf(r.l))}</span>
            <span class="date">${r.l.year ? esc(r.l.year) + "?" : ""}</span>
            <span><span class="who">${esc(r.l.sender.join(", ") || T("Not yet edited", "Noch nicht ediert"))}${r.l.recipient.length ? " → " + esc(r.l.recipient.join(", ")) : ""}</span>
            <span class="reg"><i>${[...r.forms].map(esc).join("</i>, <i>")}</i>${r.roles.size ? " · " + [...r.roles].map(roleName).join(", ") : ""}</span></span>
          </a>`).join("")}</div>
      </section>`;
    if (e.geo && window.L) {
      const m = baseMap("emap", { center: e.geo, zoom: e.ftype === "A" ? 7 : 10 });
      for (const [oid, o] of Object.entries(state.registers)) if (o.kind === "place" && o.geo && oid !== id) marker(oid, o, m).setStyle({ opacity: 0.35, fillOpacity: 0.15 });
      const me = marker(id, e, m).bringToFront(); me.getElement && me.getElement().classList.add("on");
    }
  }

  function showMap() {
    setNav("map");
    destroyOsd(); destroyMaps();
    const places = Object.entries(state.registers).filter(([, e]) => e.kind === "place");
    const geo = places.filter(([, e]) => e.geo);
    const missing = places.filter(([, e]) => !e.geo).sort((a, b) => lettersOf(b[1]) - lettersOf(a[1]));
    app.innerHTML = `
      <section class="intro">
        <h1>${T("Map", "Karte")}</h1>
        <p>${T(`${geo.length} of the ${places.length} places in the edition's register, each sized by the number of edited letters that name it.
        Click a place for its letters.`, `${geo.length} der ${places.length} Orte im Register der Edition, jeder so gross wie die Zahl der edierten Briefe, die ihn nennen.
        Ein Klick auf einen Ort zeigt seine Briefe.`)} <a href="#/places">${T("All places", "Alle Orte")}</a>.</p>
      </section>
      <div id="bigmap" class="bigmap" role="region" aria-label="${T("Map of the places named in the letters", "Karte der in den Briefen genannten Orte")}"></div>
      <section class="prose">
        <h2>${T(`Not on the map: ${missing.length} places without coordinates`, `Nicht auf der Karte: ${missing.length} Orte ohne Koordinaten`)}</h2>
        <p class="muted small">${T("The register gives no coordinates for these. Most are regions, rivers or buildings; the list with links is in",
          "Für diese gibt das Register keine Koordinaten. Meist sind es Regionen, Flüsse oder Gebäude; die Liste mit Links steht in")}
        <a href="https://github.com/DHBern/sg-missiven-data/blob/main/docs/places_without_coordinates.md" target="_blank" rel="noopener">docs/places_without_coordinates.md</a>.</p>
        <p class="chips">${missing.map(([id, e]) => `<a href="#/entry/${encodeURIComponent(id)}">${esc(e.name)} <span class="muted">${lettersOf(e)}</span></a>`).join("")}</p>
      </section>`;
    if (!window.L) { document.getElementById("bigmap").innerHTML = `<p class="noimg">${T("The map library did not load.", "Die Kartenbibliothek wurde nicht geladen.")}</p>`; return; }
    const m = baseMap("bigmap", { scrollWheelZoom: true });
    geo.sort((a, b) => lettersOf(b[1]) - lettersOf(a[1])).forEach(([id, e]) => marker(id, e, m));
    /* open on the places named in several letters; the far ones are a zoom away */
    const core = geo.filter(([, e]) => lettersOf(e) >= 3);
    m.fitBounds(L.latLngBounds((core.length > 5 ? core : geo).map(([, e]) => e.geo)), { padding: [20, 20] });
  }

  /* ---------- search ---------- */
  /* A dedicated search over every letter of the series: full text (the edition's
     text, else the best machine reading) and every facet the data holds. The
     facets come from site/data/search/meta.json; the text is loaded, in
     parallel shards, only once a text query is typed. The whole search lives in
     the URL (#/search?q=…&kw=…), so a search can be bookmarked and sent. */
  const FACETS = [
    { k: "st", en: "Status", de: "Status" },
    { k: "dec", en: "Decade", de: "Jahrzehnt" },
    { k: "snd", en: "Sender", de: "Absender" },
    { k: "rcp", en: "Recipient", de: "Empfänger" },
    { k: "op", en: "Place of writing", de: "Ausstellungsort" },
    { k: "kw", en: "Keyword", de: "Schlagwort" },
    { k: "kwa", en: "Keyword (automatic)", de: "Schlagwort (automatisch)" },
    { k: "per", en: "Person named", de: "Genannte Person" },
    { k: "org", en: "Organisation named", de: "Genannte Organisation" },
    { k: "pl", en: "Place named", de: "Genannter Ort" },
    { k: "dr", en: "Regest", de: "Regest" },
    { k: "src", en: "Text from", de: "Text aus" },
  ];
  const search = { meta: null, refs: null, texts: null, loading: null };
  let searchTimer = null;

  async function searchMeta() {
    if (!search.meta) {
      search.meta = await json("data/search/meta.json");
      /* letters -> register entries they name, from the registers (edited: the
         editors' tags; not yet edited: the automatic tagging) */
      const refs = new Map(search.meta.letters.map((m) => [m.id, { per: new Set(), org: new Set(), pl: new Set() }]));
      for (const [ref, e] of Object.entries(state.registers)) {
        const k = e.kind === "place" ? "pl" : e.kind === "person" ? "per" : "org";
        for (const [lid] of [...(e.occ || []), ...(e.occ_auto || [])]) if (refs.has(lid)) refs.get(lid)[k].add(ref);
      }
      search.refs = refs;
    }
    return search.meta;
  }

  function searchTexts() {
    if (!search.loading) {
      search.loading = Promise.all(Array.from({ length: search.meta.shards }, (_, i) => json(`data/search/text-${i}.json`)))
        .then((shards) => { search.texts = Object.assign({}, ...shards); return search.texts; });
    }
    return search.loading;
  }

  /* Normalise for matching, keeping for every output character the position it
     came from (for highlighting). Tolerant: the old spelling's variants count as
     one: u/v/w, i/y/j, ſ/s, doubled letters, ů = uo = u, ä = ae = a, and a word
     broken at the line end. */
  function normText(str, tol) {
    let out = "", map = [], last = " ";
    for (let i = 0; i < str.length; i++) {
      const c = str[i];
      if (tol && (c === "-" || c === "¬" || c === "=") && /^[ \t]*\n/.test(str.slice(i + 1, i + 4))) {
        while (i + 1 < str.length && str[i + 1] !== "\n") i++;
        i++; // the line break: the word continues on the next line
        continue;
      }
      let d = c.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace("ß", "ss").replace("ſ", "s");
      if (tol) d = d.replace(/[vw]/g, "u").replace(/[yj]/g, "i");
      for (let ch of d) {
        if (!/[a-z0-9]/.test(ch)) ch = " ";
        if (ch === " " && last === " ") continue;
        if (tol && ch !== " " && ch === last) continue;
        if (tol && ((ch === "e" && /[aou]/.test(last)) || (ch === "o" && last === "u"))) continue;
        out += ch; map.push(i); last = ch;
      }
    }
    return { s: out, map };
  }

  function parseQuery(q, tol) {
    const terms = [];
    q.replace(/"([^"]+)"|(\S+)/g, (_, ph, w) => { const t = normText(ph || w, tol).s.trim(); if (t) terms.push(t); });
    return terms;
  }

  function facetValues(m, k) {
    const r = search.refs.get(m.id);
    switch (k) {
      case "st": return [m.ed ? "ed" : "un"];
      case "dec": return m.y ? [String(Math.floor(m.y / 10) * 10)] : [];
      case "snd": return m.s.filter(Boolean);
      case "rcp": return m.r.filter(Boolean);
      case "op": return m.op;
      case "kw": return m.kw;
      case "kwa": return m.kwa || [];
      case "per": case "org": case "pl": return [...r[k]];
      case "dr": return [m.dr ? "yes" : "no"];
      case "src": return m.src;
    }
    return [];
  }
  function facetLabel(k, v) {
    if (k === "st") return v === "ed" ? T("Edited", "Ediert") : T("Not yet edited", "Noch nicht ediert");
    if (k === "dec") return T(`${v}s`, `${v}er`);
    if (k === "dr") return v === "yes" ? T("Drafted automatically", "automatisch entworfen") : T("None drafted", "kein Entwurf");
    if (k === "src") return v === "edition" ? T("Edition", "Edition") : T("Machine reading", "Maschinelle Lesung");
    if (["per", "org", "pl"].includes(k)) return (state.registers[v] || {}).name || v;
    return v;
  }

  async function showSearch(params) {
    setNav("search");
    destroyOsd();
    const meta = await searchMeta();
    const sel = Object.fromEntries(FACETS.map((f) => [f.k, new Set(params.getAll(f.k))]));
    const sy = meta.letters.map((m) => m.y).filter(Boolean);
    const yMin = Math.min(...sy), yMax = Math.max(...sy);
    const ui = { q: params.get("q") || "", tol: params.get("tol") !== "0", sort: params.get("sort") || "date",
                 from: yArg(params.get("from"), yMin, yMin, yMax), to: yArg(params.get("to"), yMax, yMin, yMax),
                 shown: 50, open: new Set(params.getAll("open")) };
    if (ui.from > ui.to) [ui.from, ui.to] = [ui.to, ui.from];
    app.innerHTML = `
      <section class="intro">
        <h1>${T("Search", "Suche")}</h1>
        <p>${T(`All ${meta.letters.length} letters: the text of the edition, and for letters and pages the edition does not have, the machine reading (${esc(readerLabel(meta.reader))}). Narrow the results with the facets; every count shows how many letters remain with it.`,
              `Alle ${meta.letters.length} Briefe: der Text der Edition, und für Briefe und Seiten, die die Edition nicht hat, die maschinelle Lesung (${esc(readerLabel(meta.reader))}). Die Facetten grenzen ein; jede Zahl sagt, wie viele Briefe damit bleiben.`)}</p>
      </section>
      <div class="sbar">
        <input id="sq" type="search" value="${esc(ui.q)}" placeholder="${T('Words or "a phrase" in the text…', 'Wörter oder «eine Wendung» im Text…')}" aria-label="${T("Search the text", "Text durchsuchen")}">
        <label class="small"><input type="checkbox" id="stol" ${ui.tol ? "checked" : ""}> ${T("tolerant spelling", "Schreibvarianten zulassen")}</label>
        ${sliderHtml("sy", yMin, yMax, ui.from, ui.to)}
        <select id="ssort" aria-label="${T("Sort", "Sortierung")}">
          <option value="date" ${ui.sort === "date" ? "selected" : ""}>${T("by date", "nach Datum")}</option>
          <option value="hits" ${ui.sort === "hits" ? "selected" : ""}>${T("most hits first", "meiste Treffer zuerst")}</option>
        </select>
        <button type="button" id="sreset" class="linkish">${T("Reset", "Zurücksetzen")}</button>
      </div>
      <div class="search">
        <div class="facetwrap">
          <button type="button" id="ftoggle" class="ftoggle" aria-expanded="false" aria-controls="facets">${T("Facets", "Facetten")} <span id="fsel"></span></button>
          <aside class="facets" id="facets" aria-label="${T("Facets", "Facetten")}"></aside>
        </div>
        <section class="results"><p id="scount" class="muted small"></p><div id="sres" class="list"></div></section>
      </div>`;

    const $ = (id) => document.getElementById(id);
    const toUrl = () => {
      const u = new URLSearchParams();
      if (ui.q) u.set("q", ui.q);
      if (!ui.tol) u.set("tol", "0");
      if (ui.sort !== "date") u.set("sort", ui.sort);
      if (ui.from > yMin) u.set("from", ui.from);
      if (ui.to < yMax) u.set("to", ui.to);
      for (const f of FACETS) for (const v of sel[f.k]) u.append(f.k, v);
      const h = "#/search" + (u.toString() ? "?" + u : "");
      if (location.hash !== h) history.replaceState(null, "", location.pathname + location.search + h);
    };

    const run = async () => {
      toUrl();
      const terms = parseQuery(ui.q, ui.tol);
      let texts = null;
      if (terms.length) {
        if (!search.texts) $("scount").textContent = T("Loading the texts…", "Die Texte werden geladen…");
        texts = await searchTexts();
      }
      /* text and years first: they hold for every facet count. The text match is kept
         while only the years or facets change, so dragging the slider stays quick. */
      const key = ui.q + "\u0000" + ui.tol;
      if (!run.cache || run.cache.key !== key) run.cache = { key, hits: new Map(), letters: null };
      const hits = run.cache.hits;
      const textOk = run.cache.letters || (run.cache.letters = meta.letters.filter((m) => {
        if (!terms.length) return true;
        const pages = texts[m.id] || [];
        const found = [];
        let all = new Set();
        for (const [n, text, src] of pages) {
          const t = normText(text, ui.tol).s;
          const here = terms.filter((x) => t.includes(x));
          if (here.length) { found.push({ n, text, src, here }); here.forEach((x) => all.add(x)); }
        }
        if (all.size < terms.length) return false;
        hits.set(m.id, found);
        return true;
      }));
      const narrowed = ui.from > yMin || ui.to < yMax;
      const base = narrowed ? textOk.filter((m) => m.y && m.y >= ui.from && m.y <= ui.to) : textOk;
      const passes = (m, except) => FACETS.every((f) => f.k === except || !sel[f.k].size || facetValues(m, f.k).some((v) => sel[f.k].has(v)));
      const rows = base.filter((m) => passes(m, null));
      paintSlider("sy", textOk.filter((m) => passes(m, null)).map((m) => m.y), yMin, yMax, ui.from, ui.to);
      renderFacets(base, passes);
      renderResults(rows, hits, terms);
    };

    function renderFacets(base, passes) {
      const nSel = FACETS.reduce((n, f) => n + sel[f.k].size, 0);
      $("fsel").textContent = nSel ? `(${nSel} ${T("selected", "gewählt")})` : "";
      $("facets").innerHTML = FACETS.map((f) => {
        const counts = new Map();
        for (const m of base) if (passes(m, f.k)) for (const v of new Set(facetValues(m, f.k))) counts.set(v, (counts.get(v) || 0) + 1);
        for (const v of sel[f.k]) if (!counts.has(v)) counts.set(v, 0);
        let vals = [...counts.entries()];
        if (f.k === "dec") vals.sort((a, b) => a[0].localeCompare(b[0]));
        else vals.sort((a, b) => b[1] - a[1] || facetLabel(f.k, a[0]).localeCompare(facetLabel(f.k, b[0]), "de"));
        if (!vals.length) return "";
        const long = vals.length > 12 && f.k !== "dec";
        const filt = (ui["ff_" + f.k] || "").toLowerCase();
        let show = filt ? vals.filter(([v]) => facetLabel(f.k, v).toLowerCase().includes(filt)) : vals;
        const full = ui.open.has(f.k) || f.k === "dec";
        const rest = full ? 0 : Math.max(0, show.length - 12);
        if (!full) show = show.slice(0, 12).concat(show.slice(12).filter(([v]) => sel[f.k].has(v)));
        /* on a phone the facets start closed, so the results are not pushed off-screen */
        const isOpen = sel[f.k].size || (!window.matchMedia("(max-width: 800px)").matches && ["st", "dec", "snd", "kw", "pl"].includes(f.k));
        return `<details class="facet" ${isOpen ? "open" : ""} data-k="${f.k}">
          <summary>${T(f.en, f.de)}${sel[f.k].size ? ` <span class="badge">${sel[f.k].size}</span>` : ""} <span class="muted small">${vals.length}</span></summary>
          ${long ? `<input class="ffilter" type="search" data-k="${f.k}" value="${esc(ui["ff_" + f.k] || "")}" placeholder="${T("Filter…", "Filtern…")}" aria-label="${T("Filter", "Filtern")} ${T(f.en, f.de)}">` : ""}
          <ul>${show.map(([v, n]) => `<li><label class="${n ? "" : "zero"}"><input type="checkbox" data-k="${f.k}" value="${esc(v)}" ${sel[f.k].has(v) ? "checked" : ""}>
            <span class="fl">${esc(facetLabel(f.k, v))}</span><span class="fc">${n}</span></label></li>`).join("")}</ul>
          ${rest ? `<button type="button" class="linkish more" data-k="${f.k}">${T(`show all ${vals.length}`, `alle ${vals.length} zeigen`)}</button>` : ""}
        </details>`;
      }).join("");
    }

    function snippet(text, terms, tol) {
      const { s: t, map } = normText(text, tol);
      const spans = [];
      for (const x of terms) { let i = t.indexOf(x); while (i >= 0 && spans.length < 40) { spans.push([map[i], map[i + x.length - 1] + 1]); i = t.indexOf(x, i + x.length); } }
      spans.sort((a, b) => a[0] - b[0]);
      if (!spans.length) return "";
      const a = Math.max(0, spans[0][0] - 70), b = Math.min(text.length, spans[0][1] + 110);
      let out = "", at = a;
      for (const [x, y] of spans) { if (x < at || y > b) continue; out += esc(text.slice(at, x)) + `<mark>${esc(text.slice(x, y))}</mark>`; at = y; }
      out += esc(text.slice(at, b));
      return (a > 0 ? "…" : "") + out.replace(/\n/g, " / ") + (b < text.length ? "…" : "") + (spans.length > 1 ? ` <span class="muted">(${spans.length}×)</span>` : "");
    }

    function renderResults(rows, hits, terms) {
      const byIndex = new Map(state.index.map((l, i) => [l.id, i]));
      const nHits = (m) => (hits.get(m.id) || []).reduce((s, h) => s + h.here.length, 0);
      rows = [...rows].sort(ui.sort === "hits" && terms.length ? (a, b) => nHits(b) - nHits(a) || byIndex.get(a.id) - byIndex.get(b.id)
                                                                 : (a, b) => byIndex.get(a.id) - byIndex.get(b.id));
      $("scount").innerHTML = `${rows.length} ${P(rows.length, "letter", "letters", "Brief", "Briefe")}` +
        (terms.length ? T(` with `, ` mit `) + (ui.q.match(/"[^"]+"|\S+/g) || []).map((t) => `<b>${esc(t)}</b>`).join(" + ") : "");
      $("sres").innerHTML = rows.slice(0, ui.shown).map((m) => {
        const l = state.index[byIndex.get(m.id)] || {};
        const hs = hits.get(m.id) || [];
        const date = m.ed ? fmtDate(l.date) : m.y ? m.y + "?" : "";
        return `<div class="row srow">
          <span class="num"><a href="#/letter/${m.id}/1">${esc(nrOf(l))}</a></span>
          <span class="date">${esc(date)}</span>
          <span><a class="who" href="#/letter/${m.id}/1">${esc((l.sender || []).join(", ") || "?")} → ${esc((l.recipient || []).join(", ") || "?")}</a>
          ${!m.ed ? `<span class="badge auto">${T("not yet edited", "noch nicht ediert")}</span>` : ""}
          ${hs.length ? hs.slice(0, 3).map((h) => `<a class="hit" href="#/letter/${m.id}/${h.n}/${h.src === "edition" ? "edition" : esc(h.src)}">
              <span class="muted small">${T("p.", "S.")} ${h.n} · ${h.src === "edition" ? T("edition", "Edition") : esc(readerLabel(h.src))}</span> ${snippet(h.text, terms, ui.tol)}</a>`).join("")
              + (hs.length > 3 ? `<span class="muted small">${T(`and ${hs.length - 3} more pages`, `und ${hs.length - 3} weitere Seiten`)}</span>` : "")
            : `<span class="reg">${esc(l.regest || "")}</span>`}</span>
        </div>`;
      }).join("") + (rows.length > ui.shown ? `<button type="button" class="linkish" id="smore">${T(`Show ${Math.min(50, rows.length - ui.shown)} more of ${rows.length - ui.shown}`, `${Math.min(50, rows.length - ui.shown)} weitere von ${rows.length - ui.shown} zeigen`)}</button>` : "")
        + (!rows.length ? `<p class="empty">${T("No letter matches. Remove a facet or a word.", "Kein Brief passt. Eine Facette oder ein Wort entfernen.")}</p>` : "");
      const more = $("smore");
      more && more.addEventListener("click", () => { ui.shown += 50; renderResults(rows, hits, terms); });
    }

    const later = (f, ms = 250) => { clearTimeout(searchTimer); searchTimer = setTimeout(f, ms); };
    $("sq").addEventListener("input", () => { ui.q = $("sq").value; ui.shown = 50; later(run); });
    $("stol").addEventListener("change", () => { ui.tol = $("stol").checked; run(); });
    $("ssort").addEventListener("change", () => { ui.sort = $("ssort").value; run(); });
    bindSlider("sy", (a, b) => { ui.from = a; ui.to = b; ui.shown = 50; later(run, 60); });
    bindBars("sy", yMin, yMax, () => [ui.from, ui.to], (a, b) => { ui.from = a; ui.to = b; ui.shown = 50; run(); });
    $("sreset").addEventListener("click", () => { location.hash = "#/search"; });
    $("ftoggle").addEventListener("click", () => {
      const open = $("facets").classList.toggle("shown");
      $("ftoggle").setAttribute("aria-expanded", String(open));
    });
    $("facets").addEventListener("change", (ev) => {
      const box = ev.target.closest("input[type=checkbox][data-k]");
      if (!box) return;
      const set = sel[box.dataset.k];
      box.checked ? set.add(box.value) : set.delete(box.value);
      ui.shown = 50;
      run();
    });
    $("facets").addEventListener("input", (ev) => {
      const f = ev.target.closest(".ffilter");
      if (!f) return;
      ui["ff_" + f.dataset.k] = f.value;
      later(() => { run().then(() => { const el = app.querySelector(`.ffilter[data-k="${f.dataset.k}"]`); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }); }, 150);
    });
    $("facets").addEventListener("click", (ev) => {
      const b = ev.target.closest("button.more");
      if (b) { ui.open.add(b.dataset.k); run(); }
    });
    await run();
  }

  /* ---------- keywords ----------
     The edition's keywords (TEI <keywords>, only on edited letters), from the
     search data: per letter its year and keywords. */
  const KW_BIN = 20, KW_THIN = 10;
  async function kwData(withAuto = false) {
    const meta = await searchMeta();
    // a copy per letter: empty keywords (the edition has a few) dropped, the search's own data untouched;
    // with withAuto, letters not yet edited bring their automatic keywords (scripts/keywords.py)
    // a drafted year outside the series (1400–1660) is a misreading: such a letter counts as undated
    const yOk = (m) => (m.ed || (m.y >= 1400 && m.y <= 1660) ? m.y : null);
    const letters = meta.letters.map((m) => ({ ...m, y: yOk(m), auto: !m.ed && withAuto,
      kw: [...new Set((m.ed ? m.kw || [] : withAuto ? m.kwa || [] : []).map((k) => k.trim()).filter(Boolean))] }))
      .filter((m) => m.kw.length);
    const count = new Map();
    for (const m of letters) for (const k of m.kw) count.set(k, (count.get(k) || 0) + 1);
    const ys = letters.map((m) => m.y).filter(Boolean);
    const b0 = Math.floor(Math.min(...ys) / KW_BIN) * KW_BIN, b1 = Math.floor(Math.max(...ys) / KW_BIN) * KW_BIN;
    const bins = [];
    for (let lo = b0; lo <= b1; lo += KW_BIN) bins.push(lo);
    const binOf = (y) => (y ? bins.indexOf(Math.floor(y / KW_BIN) * KW_BIN) : -1);
    const totals = bins.map(() => 0);
    for (const m of letters) { const b = binOf(m.y); if (b >= 0) totals[b]++; }
    return { letters, count, bins, binOf, totals };
  }
  const kwHref = (k) => `#/keyword/${encodeURIComponent(k)}`;
  const kwSearch = (k, from, to) => `#/search?kw=${encodeURIComponent(k)}${from ? `&from=${from}&to=${to}` : ""}`;
  // sequential: one hue (the brand red) from the panel colour to full strength
  const heat = (v) => (v > 0 ? `color-mix(in oklab, var(--brand) ${Math.round(12 + 88 * Math.sqrt(v))}%, var(--panel))` : "transparent");

  async function showKeywords() {
    setNav("keywords");
    destroyOsd();
    app.innerHTML = `<p class="muted">${T("Loading…", "Wird geladen…")}</p>`;
    const f = state.kwView = state.kwView || { top: 40, sort: "count", mode: "n", q: "", auto: false };
    const d = await kwData(f.auto);
    const nAuto = (await searchMeta()).letters.filter((m) => !m.ed && m.kwa && m.kwa.length).length;
    const all = [...d.count.entries()];
    // the introduction speaks of the edition's keywords, whatever the source switch says
    const edL = d.letters.filter((m) => m.ed), edKw = new Set(edL.flatMap((m) => m.kw));
    app.innerHTML = `
      <section class="intro">
        <h1>${T("Keywords", "Schlagwörter")}</h1>
        <p>${T(`The edition gives its letters keywords: ${edKw.size} of them on ${edL.length} edited letters, about ${Math.round(edL.reduce((a, m) => a + m.kw.length, 0) / edL.length)} per letter. Letters not yet edited have only automatic keywords (experimental), ${nAuto} of them; «+ automatic» adds them to the heatmap.
          The heatmap shows in how many letters of each twenty years a keyword stands; a click on a cell opens those letters in the search.`,
          `Die Edition verschlagwortet ihre Briefe: ${edKw.size} Schlagwörter auf ${edL.length} edierten Briefen, etwa ${Math.round(edL.reduce((a, m) => a + m.kw.length, 0) / edL.length)} pro Brief. Noch nicht edierte Briefe haben nur automatische Schlagwörter (experimentell), ${nAuto} von ihnen; «+ automatisch» nimmt sie in die Heatmap auf.
          Die Heatmap zeigt, in wie vielen Briefen von je zwanzig Jahren ein Schlagwort steht; ein Klick auf eine Zelle öffnet diese Briefe in der Suche.`)}</p>
        <p><a href="#/keywords/network">${T("Keyword network →", "Netzwerk der Schlagwörter →")}</a></p>
      </section>
      <div class="filters">
        <input id="kq" type="search" placeholder="${T("Find a keyword…", "Schlagwort finden…")}" value="${esc(f.q)}" aria-label="${T("Find a keyword", "Schlagwort finden")}">
        <select id="ktop" aria-label="${T("How many", "Wie viele")}">
          ${[20, 40, 80, 0].map((n) => `<option value="${n}" ${f.top === n ? "selected" : ""}>${n ? T(`${n} most frequent`, `${n} häufigste`) : T("all", "alle")}</option>`).join("")}
        </select>
        <select id="ksort" aria-label="${T("Order", "Reihenfolge")}">
          <option value="count" ${f.sort === "count" ? "selected" : ""}>${T("by frequency", "nach Häufigkeit")}</option>
          <option value="time" ${f.sort === "time" ? "selected" : ""}>${T("by period of most use", "nach Zeit des Schwerpunkts")}</option>
          <option value="alpha" ${f.sort === "alpha" ? "selected" : ""}>${T("alphabetically", "alphabetisch")}</option>
        </select>
        ${nAuto ? `<div class="seg" id="ksrc" role="group" aria-label="${T("Source", "Quelle")}">
          ${[[false, T("Edition", "Edition")], [true, T(`+ automatic (${nAuto})`, `+ automatisch (${nAuto})`)]].map(([v, l]) =>
            `<button type="button" data-v="${v}" aria-pressed="${f.auto === v}" class="${f.auto === v ? "on" : ""}">${l}</button>`).join("")}
        </div>` : ""}
        <div class="seg" id="kmode" role="group" aria-label="${T("Value", "Wert")}">
          ${[["n", T("Letters", "Briefe")], ["share", T("Share of the period", "Anteil am Zeitraum")]].map(([v, l]) =>
            `<button type="button" data-v="${v}" aria-pressed="${f.mode === v}" class="${f.mode === v ? "on" : ""}">${l}</button>`).join("")}
        </div>
      </div>
      <div class="heatwrap" id="heat"></div>
      <p class="muted small" id="hnote"></p>
      <h2>${T("All keywords", "Alle Schlagwörter")}</h2>
      <div class="kwindex" id="kwindex"></div>`;
    const centre = (k) => {  // mean year of a keyword's letters, for "by period"
      let s = 0, n = 0;
      for (const m of d.letters) if (m.y && m.kw.includes(k)) { s += m.y; n++; }
      return n ? s / n : 0;
    };
    const cells = new Map();
    for (const m of d.letters) {
      const b = d.binOf(m.y);
      if (b < 0) continue;
      for (const k of m.kw) { if (!cells.has(k)) cells.set(k, d.bins.map(() => 0)); cells.get(k)[b]++; }
    }
    const draw = () => {
      const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
      let rows = all.filter(([k]) => words.every((w) => k.toLowerCase().includes(w))).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de"));
      if (f.top && !words.length) rows = rows.slice(0, f.top);
      if (f.sort === "alpha") rows.sort((a, b) => a[0].localeCompare(b[0], "de"));
      if (f.sort === "time") rows.sort((a, b) => centre(a[0]) - centre(b[0]));
      const val = (k, b) => { const n = (cells.get(k) || [])[b] || 0; return f.mode === "n" ? n : (d.totals[b] ? n / d.totals[b] : 0); };
      // a share over a handful of letters says nothing: those periods stay out of the colour scale
      const thin = (b) => f.mode === "share" && d.totals[b] < KW_THIN;
      let top = 0;
      for (const [k] of rows) d.bins.forEach((_, b) => { if (!thin(b)) top = Math.max(top, val(k, b)); });
      const fmt = (v) => (f.mode === "n" ? String(v) : `${Math.round(v * 100)} %`);
      const cols = `grid-template-columns: minmax(120px, 13rem) repeat(${d.bins.length}, minmax(30px, 1fr)) 3.2rem;`;
      document.getElementById("heat").innerHTML = rows.length ? `
        <div class="heat" style="${cols}" role="table" aria-label="${T("Keywords by twenty years", "Schlagwörter nach zwanzig Jahren")}">
          <div class="hh" role="columnheader"></div>
          ${d.bins.map((lo) => `<div class="hh" role="columnheader">${lo}<span>–${String(lo + KW_BIN - 1).slice(2)}</span></div>`).join("")}
          <div class="hh hn" role="columnheader">${T("all", "alle")}</div>
          <div class="hk muted" role="rowheader">${T("letters with keywords", "Briefe mit Schlagwörtern")}</div>
          ${d.totals.map((n, b) => `<div class="htot${thin(b) ? " thin" : ""}">${n}</div>`).join("")}
          <div class="hn muted">${d.letters.length}</div>
          ${rows.map(([k, n]) => `
            <a class="hk" role="rowheader" href="${kwHref(k)}">${esc(k)}</a>
            ${d.bins.map((lo, b) => {
              const v = val(k, b), c = (cells.get(k) || [])[b] || 0;
              return `<a class="hc${thin(b) ? " thin" : ""}" role="cell" href="${c ? kwSearch(k, lo, lo + KW_BIN - 1) : kwHref(k)}" style="background:${thin(b) ? "" : heat(top ? v / top : 0)}"
                data-tip="${esc(k)} · ${lo}–${lo + KW_BIN - 1}: ${c} ${P(c, "letter", "letters", "Brief", "Briefe")} ${T("of", "von")} ${d.totals[b]}${d.totals[b] ? ` (${Math.round(100 * c / d.totals[b])} %)` : ""}"
                aria-label="${esc(k)} ${lo}–${lo + KW_BIN - 1}: ${c}">${c && rows.length <= 40 ? `<span>${c}</span>` : ""}</a>`;
            }).join("")}
            <div class="hn">${n}</div>`).join("")}
        </div>` : `<p class="muted">${T("No keyword matches.", "Kein Schlagwort passt.")}</p>`;
      document.getElementById("hnote").innerHTML = `<span class="hlegend"><span>${f.mode === "n" ? "0" : "0 %"}</span><i></i><span>${fmt(top)}</span></span>
        ${f.auto ? T(`With the automatic keywords of ${nAuto} letters not yet edited: picked by a language model from the edition's vocabulary, unchecked (experimental; on edited letters it agrees with the editors on about two of three keywords). `,
          `Mit den automatischen Schlagwörtern von ${nAuto} noch nicht edierten Briefen: von einem Sprachmodell aus dem Vokabular der Edition gewählt, ungeprüft (experimentell; auf edierten Briefen stimmt es bei etwa zwei von drei Schlagwörtern mit der Redaktion überein). `) : ""}
        ${f.mode === "n" ? T("Number of letters in twenty years.", "Anzahl Briefe in zwanzig Jahren.") : T(`Share of the period's letters with keywords: evens out that the edition holds far more letters from some periods than from others. Periods with fewer than ${KW_THIN} letters are hatched and left out of the colours: one letter there would count as a large share.`, `Anteil an den verschlagworteten Briefen des Zeitraums: gleicht aus, dass die Edition aus manchen Zeiten viel mehr Briefe hat als aus anderen. Zeiträume mit weniger als ${KW_THIN} Briefen sind schraffiert und nicht eingefärbt: ein einziger Brief wäre dort schon ein grosser Anteil.`)}
        ${T(`Years by the letter's date; ${d.letters.filter((m) => !m.y).length} undated letters are counted only in "all".`, `Jahre nach dem Datum des Briefs; ${d.letters.filter((m) => !m.y).length} undatierte Briefe zählen nur unter «alle».`)}`;
    };
    // the index below: every keyword, A–Z, with its count
    const byLetter = new Map();
    for (const [k, n] of [...all].sort((a, b) => a[0].localeCompare(b[0], "de"))) {
      const l = k[0].toUpperCase();
      if (!byLetter.has(l)) byLetter.set(l, []);
      byLetter.get(l).push([k, n]);
    }
    document.getElementById("kwindex").innerHTML = [...byLetter].map(([l, ks]) => `
      <section><h3>${esc(l)}</h3><div class="kw">${ks.map(([k, n]) => `<a href="${kwHref(k)}">${esc(k)} <span class="n">${n}</span></a>`).join("")}</div></section>`).join("");
    const heatEl = document.getElementById("heat");
    heatEl.addEventListener("mouseover", (ev) => {
      const c = ev.target.closest(".hc");
      if (!c) return;
      hideTip();
      tipEl = document.createElement("div");
      tipEl.className = "tip";
      tipEl.textContent = c.dataset.tip;
      document.body.appendChild(tipEl);
      const r = c.getBoundingClientRect();
      tipEl.style.left = Math.max(8, Math.min(window.innerWidth - tipEl.offsetWidth - 8, r.left)) + "px";
      tipEl.style.top = (r.bottom + 6 + tipEl.offsetHeight > window.innerHeight ? r.top - tipEl.offsetHeight - 6 : r.bottom + 6) + "px";
    });
    heatEl.addEventListener("mouseleave", hideTip);
    document.getElementById("kq").addEventListener("input", (ev) => { f.q = ev.target.value; draw(); });
    document.getElementById("ktop").addEventListener("change", (ev) => { f.top = +ev.target.value; draw(); });
    document.getElementById("ksort").addEventListener("change", (ev) => { f.sort = ev.target.value; draw(); });
    const src = document.getElementById("ksrc");
    src && src.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-v]");
      if (!b) return;
      f.auto = b.dataset.v === "true";
      showKeywords();
    });
    document.getElementById("kmode").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-v]");
      if (!b) return;
      f.mode = b.dataset.v;
      document.querySelectorAll("#kmode button").forEach((x) => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", on); });
      draw();
    });
    draw();
  }

  async function showKeyword(k, params) {
    setNav("keywords");
    destroyOsd();
    app.innerHTML = `<p class="muted">${T("Loading…", "Wird geladen…")}</p>`;
    const d = await kwData();
    const mine = d.letters.filter((m) => m.kw.includes(k));
    if (!mine.length && !(await searchMeta()).letters.some((m) => (m.kwa || []).includes(k))) { app.innerHTML = `<p>${T("No letter has the keyword", "Kein Brief hat das Schlagwort")} «${esc(k)}». <a href="#/keywords">${T("All keywords", "Alle Schlagwörter")}</a></p>`; return; }
    const per = d.bins.map(() => 0);
    for (const m of mine) { const b = d.binOf(m.y); if (b >= 0) per[b]++; }
    const top = Math.max(1, ...per);
    const co = new Map();
    for (const m of mine) for (const x of m.kw) if (x !== k) co.set(x, (co.get(x) || 0) + 1);
    const coTop = [...co.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de")).slice(0, 24);
    // the letters list follows the Sankey: ?with=<keyword>&from=&to= narrow it
    const withK = params.get("with") || "";
    const from = +params.get("from") || 0, to = +params.get("to") || 0, undated = params.get("undated") === "1";
    const keep = (m) => (!withK || m.kw.includes(withK)) && (undated ? !m.y : !from || (m.y && m.y >= from && m.y <= to));
    const shown = mine.filter(keep);
    const rows = shown.map((m) => byId(m.id)).filter(Boolean).sort((a, b) => (a.date || "").localeCompare(b.date || ""));
    const autoRows = (await searchMeta()).letters.filter((m) => !m.ed && (m.kwa || []).includes(k)).map((m) => byId(m.id)).filter(Boolean);
    const filtered = withK || from || undated;
    const what = [withK ? T(`also with «${esc(withK)}»`, `auch mit «${esc(withK)}»`) : "",
                  undated ? T("undated", "undatiert") : from ? `${from}–${to}` : ""].filter(Boolean).join(", ");
    app.innerHTML = `
      <section class="intro">
        <div class="muted small"><a href="#/keywords">${T("Keywords", "Schlagwörter")}</a> · <a href="#/keywords/network?focus=${encodeURIComponent(k)}">${T("in the network", "im Netzwerk")}</a></div>
        <h1>${esc(k)}</h1>
        <p>${T(`${mine.length} edited ${P(mine.length, "letter", "letters", "", "")} with this keyword.`, `${mine.length} edierte ${P(mine.length, "", "", "Brief", "Briefe")} mit diesem Schlagwort.`)}
          <a href="${kwSearch(k)}">${T("Open in the search", "In der Suche öffnen")}</a></p>
      </section>
      ${coTop.length ? `<section class="pane sankeypane">
        <h2>${T("Companions over time", "Begleiter über die Zeit")}</h2>
        <div id="sankey"></div>
        <p class="muted small">${T(`From «${esc(k)}» to the keywords most often given with it, and on to the twenty years of those letters. A band's width is the number of letters that carry both keywords; a letter with several of these companions counts once with each, so the bands add up to more than ${mine.length} letters. A click on a band or a box filters the letters below.`,
          `Von «${esc(k)}» zu den Schlagwörtern, die am häufigsten mit ihm vergeben sind, und weiter zu den zwanzig Jahren dieser Briefe. Die Breite eines Bands ist die Zahl der Briefe mit beiden Schlagwörtern; ein Brief mit mehreren dieser Begleiter zählt bei jedem, die Bänder ergeben deshalb mehr als ${mine.length} Briefe. Ein Klick auf ein Band oder ein Feld filtert die Briefe unten.`)}</p>
      </section>` : ""}
      <div class="kwcols">
        <section class="pane">
          <h2>${T("Over time", "Über die Zeit")}</h2>
          <div class="kwbars" role="table" aria-label="${T("Letters per twenty years", "Briefe pro zwanzig Jahre")}">
            ${d.bins.map((lo, b) => `<a href="${per[b] ? kwSearch(k, lo, lo + KW_BIN - 1) : "#/keyword/" + encodeURIComponent(k)}" role="row" title="${lo}–${lo + KW_BIN - 1}: ${per[b]} ${T("of", "von")} ${d.totals[b]}">
              <span class="yr">${lo}</span><span class="bar"><i style="width:${Math.round(100 * per[b] / top)}%"></i></span><span class="n">${per[b] || ""}</span></a>`).join("")}
          </div>
          <p class="muted small">${T("Letters with this keyword per twenty years; a click opens them in the search.", "Briefe mit diesem Schlagwort pro zwanzig Jahre; ein Klick öffnet sie in der Suche.")}</p>
        </section>
        <section class="pane">
          <h2>${T("Often together with", "Oft zusammen mit")}</h2>
          <div class="kw">${coTop.map(([x, n]) => `<a href="${kwHref(x)}">${esc(x)} <span class="n">${n}</span></a>`).join("")}</div>
          <p class="muted small">${T("The number: letters that carry both keywords.", "Die Zahl: Briefe, die beide Schlagwörter tragen.")}</p>
        </section>
      </div>
      <h2 id="kwletters">${T("Letters", "Briefe")}${filtered ? ` <span class="muted small">${what}: ${rows.length} · <a href="${kwHref(k)}">${T("show all", "alle zeigen")}</a></span>` : ""}</h2>
      <div class="list">${rows.map((l) => `
        <a class="row" href="#/letter/${l.id}/1">
          <span class="num">${esc(nrOf(l))}</span>
          <span class="date">${esc(fmtDate(l.date))}</span>
          <span><span class="who">${esc(l.sender.join(", ") || "?")} → ${esc(l.recipient.join(", ") || "?")}</span><span class="reg">${esc(l.regest)}</span></span>
        </a>`).join("")}</div>
      ${autoRows.length ? `<h2>${T("Letters not yet edited", "Noch nicht edierte Briefe")} <span class="muted small">${autoRows.length} · ${T("keyword given automatically", "Schlagwort automatisch vergeben")}</span></h2>
      <p class="caveat">${T("A language model picked these keywords from the edition's vocabulary, from the machine reading and the draft regest. Unchecked and experimental: on edited letters it agrees with the editors on about two of three keywords and finds about three in five of theirs.",
        "Diese Schlagwörter hat ein Sprachmodell aus dem Vokabular der Edition gewählt, nach der maschinellen Lesung und dem Regestentwurf. Ungeprüft und experimentell: Auf edierten Briefen stimmt es bei etwa zwei von drei Schlagwörtern mit der Redaktion überein und findet etwa drei von fünf ihrer Schlagwörter.")}</p>
      <div class="list">${autoRows.map((l) => `
        <a class="row" href="#/letter/${l.id}/1">
          <span class="num">${esc(nrOf(l))}</span>
          <span class="date">${l.year ? esc(l.year) + "?" : ""}</span>
          <span><span class="who">${esc(l.sender.join(", ") || "?")} → ${esc(l.recipient.join(", ") || "?")} <span class="muted small">· ${T("draft", "Entwurf")}</span></span><span class="reg">${esc(l.regest || "")}</span></span>
        </a>`).join("")}</div>` : ""}`;
    if (coTop.length) drawSankey(k, mine, d, coTop.slice(0, 10).map(([x]) => x), { withK, from, undated });
    if (filtered) document.getElementById("kwletters").scrollIntoView({ block: "start" });
  }

  function tipAt(rect, html) {
    hideTip();
    tipEl = document.createElement("div");
    tipEl.className = "tip";
    tipEl.innerHTML = html;
    document.body.appendChild(tipEl);
    tipEl.style.left = Math.max(8, Math.min(window.innerWidth - tipEl.offsetWidth - 8, rect.left)) + "px";
    tipEl.style.top = (rect.bottom + 6 + tipEl.offsetHeight > window.innerHeight ? rect.top - tipEl.offsetHeight - 6 : rect.bottom + 6) + "px";
  }

  /* Sankey in three columns: the keyword → its most frequent companions →
     the twenty years of the letters that carry both. Plain SVG, no library:
     columns of boxes, bands as filled cubic curves. Identity is by label, not
     by colour: bands are one neutral tone, the one under the pointer (and its
     path through the companion) turns red. */
  function drawSankey(k, mine, d, comps, cur) {
    const el = document.getElementById("sankey");
    const W = 760, GAP = 6, PADY = 4;
    const periods = [...d.bins.map((lo) => ({ id: "p" + lo, lo, label: `${lo}–${lo + KW_BIN - 1}` }))];
    const und = { id: "pu", lo: 0, label: T("undated", "undatiert") };
    const flow1 = comps.map((c) => ({ c, n: mine.filter((m) => m.kw.includes(c)).length }));
    const flow2 = [];
    for (const c of comps) {
      for (const m of mine) {
        if (!m.kw.includes(c)) continue;
        const b = d.binOf(m.y);
        const p = b >= 0 ? periods[b] : und;
        const f = flow2.find((x) => x.c === c && x.p === p);
        if (f) f.n++; else flow2.push({ c, p, n: 1 });
      }
    }
    const usedP = [...periods, und].filter((p) => flow2.some((f) => f.p === p));
    const total = flow1.reduce((a, f) => a + f.n, 0);
    const maxNodes = Math.max(comps.length, usedP.length);
    const H = Math.max(280, maxNodes * 26);
    const scale = (H - 2 * PADY - GAP * (maxNodes - 1)) / total;
    const X = [150, 400, 640], NW = 12;
    // nodes: y positions per column, centred
    const col = (items, size) => {
      const h = items.reduce((a, it) => a + size(it) * scale, 0) + GAP * (items.length - 1);
      let y = (H - h) / 2;
      return items.map((it) => { const r = { it, y, h: size(it) * scale }; y += r.h + GAP; return r; });
    };
    const nK = col([k], () => total)[0];
    const cIn = new Map(flow1.map((f) => [f.c, f.n]));
    const nC = col(comps, (c) => cIn.get(c));
    const pIn = new Map(usedP.map((p) => [p, flow2.filter((f) => f.p === p).reduce((a, f) => a + f.n, 0)]));
    const nP = col(usedP, (p) => pIn.get(p));
    const band = (x0, y0, x1, y1, h) => {
      const mx = (x0 + x1) / 2;
      return `M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}L${x1},${y1 + h}C${mx},${y1 + h} ${mx},${y0 + h} ${x0},${y0 + h}Z`;
    };
    const href = (q) => `${kwHref(k)}?${q}`;
    let svg = "";
    // K → companions
    let yK = nK.y;
    nC.forEach((nc) => {
      const h = nc.h;
      svg += `<a href="${href("with=" + encodeURIComponent(nc.it))}"><path class="sk-band" data-c="${esc(nc.it)}" data-tip="${esc(k)} + ${esc(nc.it)}: ${cIn.get(nc.it)} ${P(cIn.get(nc.it), "letter", "letters", "Brief", "Briefe")}"
        d="${band(X[0] + NW, yK, X[1], nc.y, h)}"/></a>`;
      yK += h;
    });
    // companions → periods (bands leave a companion top to bottom in period order, and enter a period in companion order)
    const outY = new Map(nC.map((nc) => [nc.it, nc.y]));
    const inY = new Map(nP.map((np) => [np.it, np.y]));
    for (const nc of nC) {
      for (const np of nP) {
        const f = flow2.find((x) => x.c === nc.it && x.p === np.it);
        if (!f) continue;
        const h = f.n * scale;
        const q = `with=${encodeURIComponent(nc.it)}&${np.it === und ? "undated=1" : `from=${np.it.lo}&to=${np.it.lo + KW_BIN - 1}`}`;
        svg += `<a href="${href(q)}"><path class="sk-band" data-c="${esc(nc.it)}" data-p="${np.it.id}" data-tip="${esc(k)} + ${esc(nc.it)}, ${np.it.label}: ${f.n} ${P(f.n, "letter", "letters", "Brief", "Briefe")}"
          d="${band(X[1] + NW, outY.get(nc.it), X[2], inY.get(np.it), h)}"/></a>`;
        outY.set(nc.it, outY.get(nc.it) + h);
        inY.set(np.it, inY.get(np.it) + h);
      }
    }
    const node = (x, n, label, hrefN, cls, data, anchor) => `<a href="${hrefN}"><rect class="sk-node ${cls}" ${data} x="${x}" y="${n.y}" width="${NW}" height="${Math.max(1, n.h)}" rx="2"/>
      <text class="sk-lab" x="${anchor === "end" ? x - 6 : x + NW + 6}" y="${n.y + n.h / 2}" text-anchor="${anchor}" dominant-baseline="middle">${label}</text></a>`;
    svg += node(X[0], nK, esc(k), kwHref(k), "key", "", "end");
    nC.forEach((nc) => { svg += node(X[1], nc, `${esc(nc.it)} <tspan class="sk-n">${cIn.get(nc.it)}</tspan>`, href("with=" + encodeURIComponent(nc.it)), cur.withK === nc.it ? "on" : "", `data-c="${esc(nc.it)}"`, "start"); });
    nP.forEach((np) => {
      const q = np.it === und ? "undated=1" : `from=${np.it.lo}&to=${np.it.lo + KW_BIN - 1}`;
      svg += node(X[2], np, `${np.it.label} <tspan class="sk-n">${pIn.get(np.it)}</tspan>`, href(q), (np.it === und ? cur.undated : cur.from === np.it.lo) ? "on" : "", `data-p="${np.it.id}"`, "start");
    });
    el.innerHTML = `<div class="sankeywrap"><svg class="sankey" viewBox="0 0 ${W} ${H}" role="img" aria-label="${T(`Sankey: ${esc(k)} to its companions and their periods`, `Sankey: ${esc(k)} zu seinen Begleitern und deren Zeiträumen`)}">${svg}</svg></div>`;
    const svgEl = el.querySelector("svg");
    const lit = (c, p) => svgEl.querySelectorAll(".sk-band").forEach((b) => {
      const on = (!c || b.dataset.c === c) && (!p || b.dataset.p === p || (!b.dataset.p && !c));
      b.classList.toggle("hl", on && (c || p));
      b.classList.toggle("dim", !on && !!(c || p));
    });
    svgEl.addEventListener("mouseover", (ev) => {
      const b = ev.target.closest(".sk-band"), n = ev.target.closest("a") && ev.target.closest("a").querySelector(".sk-node");
      if (b) { lit(b.dataset.c, b.dataset.p); tipAt(b.getBoundingClientRect(), esc(b.dataset.tip)); return; }
      if (n && (n.dataset.c || n.dataset.p)) {
        lit(n.dataset.c, n.dataset.p);
        if (n.dataset.p) svgEl.querySelectorAll(".sk-band").forEach((x) => { const on = x.dataset.p === n.dataset.p || (!x.dataset.p && svgEl.querySelector(`.sk-band[data-p="${n.dataset.p}"][data-c="${CSS.escape(x.dataset.c)}"]`)); x.classList.toggle("hl", !!on); x.classList.toggle("dim", !on); });
      }
    });
    svgEl.addEventListener("mouseleave", () => { lit(null, null); hideTip(); });
  }

  /* ---------- keyword network ----------
     Keywords as nodes, letters they share as edges. The weight is the cosine
     of the two keywords' letter sets (shared / √(a·b)), so that the most
     frequent keywords do not tie to everything; each keyword keeps its five
     strongest ties (shared in at least three letters). Groups come from
     modularity (louvain below). The layout is d3-force, computed once and
     then drawn; it starts from d3's fixed spiral, so it is the same on every visit. */
  let d3Loading = null;
  function loadD3() {
    if (window.d3) return Promise.resolve();
    d3Loading = d3Loading || new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js";
      s.onload = res; s.onerror = () => rej(new Error("d3"));
      document.head.appendChild(s);
    });
    return d3Loading;
  }

  function kwGraph(d, min) {
    const count = new Map([...d.count].filter(([, n]) => n >= min));
    const names = [...count.keys()].sort((a, b) => count.get(b) - count.get(a) || a.localeCompare(b, "de"));
    const idx = new Map(names.map((n, i) => [n, i]));
    const pair = new Map();
    for (const m of d.letters) {
      const ks = m.kw.filter((x) => idx.has(x)).map((x) => idx.get(x)).sort((a, b) => a - b);
      for (let i = 0; i < ks.length; i++) for (let j = i + 1; j < ks.length; j++) {
        const key = ks[i] * 10000 + ks[j];
        pair.set(key, (pair.get(key) || 0) + 1);
      }
    }
    const cand = [];
    for (const [key, c] of pair) {
      if (c < 3) continue;
      const a = Math.floor(key / 10000), b = key % 10000;
      cand.push({ a, b, c, w: c / Math.sqrt(count.get(names[a]) * count.get(names[b])) });
    }
    const best = names.map(() => []);
    for (const e of cand) { best[e.a].push(e); best[e.b].push(e); }
    const keep = new Set();
    best.forEach((es) => es.sort((x, y) => y.w - x.w).slice(0, 5).forEach((e) => keep.add(e)));
    const edges = [...keep];
    const adj = names.map(() => []);
    for (const e of edges) { adj[e.a].push([e.b, e.w]); adj[e.b].push([e.a, e.w]); }
    const lab = louvain(names.length, edges.map((e) => [e.a, e.b, e.w]), 1.0);
    const nodes = names.map((n, i) => ({ id: i, name: n, n: count.get(n), g: lab[i], deg: adj[i].length }));
    const groups = new Map();
    for (const nd of nodes) if (nd.deg) { if (!groups.has(nd.g)) groups.set(nd.g, []); groups.get(nd.g).push(nd); }
    const glist = [...groups.values()].filter((g) => g.length >= 3).sort((a, b) => b.reduce((s, x) => s + x.n, 0) - a.reduce((s, x) => s + x.n, 0));
    return { nodes: nodes.filter((nd) => nd.deg), isolated: nodes.filter((nd) => !nd.deg), edges, groups: glist, adj, names };
  }

  /* Groups by modularity (Louvain): move each node to the neighbouring group
     that gains most, until nothing moves; then treat each group as one node and
     repeat. Nodes are visited in a fixed order, so the groups are the same on
     every visit. Returns a group number per node. */
  function louvain(n, wedges, gamma) {
    let member = Array.from({ length: n }, (_, i) => i);   // original node -> current super node
    let size = n, E = wedges;
    for (let level = 0; level < 6; level++) {
      const adj = Array.from({ length: size }, () => new Map());
      const k = new Array(size).fill(0);
      let m2 = 0;
      for (const [a, b, w] of E) {
        if (a === b) { k[a] += 2 * w; m2 += 2 * w; adj[a].set(a, (adj[a].get(a) || 0) + 2 * w); continue; }
        adj[a].set(b, (adj[a].get(b) || 0) + w); adj[b].set(a, (adj[b].get(a) || 0) + w);
        k[a] += w; k[b] += w; m2 += 2 * w;
      }
      if (!m2) break;
      const com = Array.from({ length: size }, (_, i) => i), tot = k.slice();
      let moved = true, any = false;
      for (let pass = 0; moved && pass < 50; pass++) {
        moved = false;
        for (let i = 0; i < size; i++) {
          const ci = com[i];
          const kin = new Map();
          for (const [j, w] of adj[i]) if (j !== i) kin.set(com[j], (kin.get(com[j]) || 0) + w);
          tot[ci] -= k[i];
          let best = ci, gain = (kin.get(ci) || 0) - gamma * tot[ci] * k[i] / m2;
          for (const [c, w] of kin) {
            const g = w - gamma * tot[c] * k[i] / m2;
            if (g > gain + 1e-12 || (Math.abs(g - gain) <= 1e-12 && c < best)) { best = c; gain = g; }
          }
          tot[best] += k[i];
          if (best !== ci) { com[i] = best; moved = true; any = true; }
        }
      }
      if (!any) break;
      const ids = new Map();
      com.forEach((c) => { if (!ids.has(c)) ids.set(c, ids.size); });
      member = member.map((s0) => ids.get(com[s0]));
      const agg = new Map();
      for (const [a, b, w] of E) {
        const x = ids.get(com[a]), y = ids.get(com[b]);
        const key = Math.min(x, y) * 100000 + Math.max(x, y);
        agg.set(key, (agg.get(key) || 0) + w);
      }
      E = [...agg].map(([key, w]) => [Math.floor(key / 100000), key % 100000, w]);
      size = ids.size;
    }
    return member;
  }

  async function showKeywordNetwork(params) {
    setNav("keywords");
    destroyOsd();
    app.innerHTML = `<p class="muted">${T("Loading…", "Wird geladen…")}</p>`;
    const d = await kwData();
    try { await loadD3(); } catch { app.innerHTML = `<p>${T("The network needs the d3 library, which could not be loaded.", "Das Netzwerk braucht die Bibliothek d3, die nicht geladen werden konnte.")}</p>`; return; }
    const f = state.netView = state.netView || { min: 15 };
    const focus = params.get("focus") || "";
    if (focus && (d.count.get(focus) || 0) < f.min) f.min = Math.max(3, d.count.get(focus) || 3);
    const g = kwGraph(d, f.min);
    app.innerHTML = `
      <section class="intro">
        <div class="muted small"><a href="#/keywords">${T("Keywords", "Schlagwörter")}</a></div>
        <h1>${T("Keyword network", "Netzwerk der Schlagwörter")}</h1>
        <p>${T(`Keywords given to the same letters stand close together. A line joins two keywords that share at least three letters; each keeps its five strongest ties, measured against how frequent both are (so that «Bote» does not tie to everything). Circle size: number of letters. The groups on the right are found from the ties alone; point at one to see it.`,
          `Schlagwörter, die denselben Briefen gegeben sind, stehen nah beieinander. Eine Linie verbindet zwei Schlagwörter, die mindestens drei Briefe teilen; jedes behält seine fünf stärksten Verbindungen, gemessen an der Häufigkeit beider (damit «Bote» nicht mit allem verbunden ist). Kreisgrösse: Anzahl Briefe. Die Gruppen rechts sind allein aus den Verbindungen gefunden; Zeiger darauf zeigt sie.`)}</p>
      </section>
      <div class="filters">
        <select id="nmin" aria-label="${T("Keywords shown", "Gezeigte Schlagwörter")}">
          ${[5, 8, 15, 25].map((n) => `<option value="${n}" ${f.min === n ? "selected" : ""}>${T(`keywords in ${n}+ letters`, `Schlagwörter in ${n}+ Briefen`)}</option>`).join("")}
          ${[5, 8, 15, 25].includes(f.min) ? "" : `<option value="${f.min}" selected>${T(`keywords in ${f.min}+ letters`, `Schlagwörter in ${f.min}+ Briefen`)}</option>`}
        </select>
        <input id="nq" type="search" placeholder="${T("Find a keyword…", "Schlagwort finden…")}" value="${esc(focus)}" aria-label="${T("Find a keyword", "Schlagwort finden")}" list="nlist">
        <datalist id="nlist">${g.nodes.map((n) => `<option value="${esc(n.name)}">`).join("")}</datalist>
        <span class="muted small">${g.nodes.length} ${T("keywords", "Schlagwörter")}, ${g.edges.length} ${T("ties", "Verbindungen")}${g.isolated.length ? T(`; ${g.isolated.length} without a tie left out`, `; ${g.isolated.length} ohne Verbindung weggelassen`) : ""}</span>
      </div>
      <div class="netcols">
        <section class="pane netpane"><div id="net"></div><p class="muted small">${T("Drag to move, scroll or pinch to zoom; a click on a keyword opens it.", "Ziehen verschiebt, Scrollen oder Zwei-Finger-Geste zoomt; ein Klick auf ein Schlagwort öffnet es.")}</p></section>
        <aside class="pane netgroups"><h2>${T("Groups", "Gruppen")}</h2><ol id="ngroups">${g.groups.map((gr, i) => `
          <li data-g="${gr[0].g}"><b>${gr.slice(0, 3).map((x) => esc(x.name)).join(" · ")}</b> <span class="muted small">${gr.length} ${T("keywords", "Schlagwörter")}</span>
          <div class="small muted">${gr.slice(3, 12).map((x) => `<a href="${kwHref(x.name)}">${esc(x.name)}</a>`).join(", ")}${gr.length > 12 ? " …" : ""}</div></li>`).join("")}</ol></aside>
      </div>`;
    document.getElementById("nmin").addEventListener("change", (ev) => { f.min = +ev.target.value; showKeywordNetwork(new URLSearchParams()); });
    const W = 900, H = 640;
    const nodes = g.nodes.map((n) => ({ ...n, r: 3 + 1.15 * Math.sqrt(n.n) }));
    const byIdx = new Map(nodes.map((n) => [n.id, n]));
    const links = g.edges.map((e) => ({ source: e.a, target: e.b, w: e.w, c: e.c }));
    const sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).id((n) => n.id).distance((l) => 30 + 80 * (1 - l.w)).strength((l) => 0.15 + 0.6 * l.w))
      .force("charge", d3.forceManyBody().strength(-120))
      .force("collide", d3.forceCollide((n) => n.r + 2))
      .force("x", d3.forceX(0).strength(0.04)).force("y", d3.forceY(0).strength(0.06))
      .stop();
    for (let i = 0; i < 400; i++) sim.tick();
    const xs = d3.extent(nodes, (n) => n.x), ys = d3.extent(nodes, (n) => n.y);
    const pad = 60;
    const vb = [xs[0] - pad, ys[0] - pad, xs[1] - xs[0] + 2 * pad, ys[1] - ys[0] + 2 * pad];
    const labelled = new Set(nodes.slice().sort((a, b) => b.n - a.n).slice(0, 30).map((n) => n.id));
    const svg = d3.select("#net").append("svg").attr("class", "net").attr("viewBox", vb.join(" ")).attr("role", "img")
      .attr("aria-label", T("Network of keywords", "Netzwerk der Schlagwörter"));
    const root = svg.append("g");
    // labels stay 12 px on screen whatever the viewBox scale and the zoom
    let zk = 1;
    const fit = () => {
      const px = svg.node().getBoundingClientRect().width / vb[2] || 1;
      root.selectAll("text").attr("font-size", 12 / (px * zk)).attr("stroke-width", 3 / (px * zk));
      root.selectAll("text").attr("y", (n) => n.y - n.r - 3 / (px * zk));
    };
    svg.call(d3.zoom().scaleExtent([0.5, 8]).on("zoom", (ev) => { root.attr("transform", ev.transform); zk = ev.transform.k; fit(); }));
    const lk = root.append("g").attr("class", "n-links").selectAll("line").data(links).join("line")
      .attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y)
      .attr("stroke-width", (l) => 0.6 + 2.4 * l.w);
    const nd = root.append("g").attr("class", "n-nodes").selectAll("a").data(nodes).join("a").attr("href", (n) => kwHref(n.name));
    nd.append("circle").attr("cx", (n) => n.x).attr("cy", (n) => n.y).attr("r", (n) => n.r);
    nd.append("text").attr("x", (n) => n.x).attr("y", (n) => n.y - n.r - 3).attr("text-anchor", "middle")
      .attr("class", (n) => (labelled.has(n.id) ? "n-lab" : "n-lab n-hidden")).text((n) => n.name);
    fit();
    if (state.netFit) window.removeEventListener("resize", state.netFit);
    state.netFit = () => { if (document.body.contains(svg.node())) fit(); };
    window.addEventListener("resize", state.netFit);
    const nbr = new Map(nodes.map((n) => [n.id, new Set([n.id, ...g.adj[n.id].map(([j]) => j)])]));
    const show = (ids, hot) => {
      nd.classed("dim", (n) => ids && !ids.has(n.id)).classed("hot", (n) => hot && hot.has(n.id))
        .select("text").classed("n-hidden", (n) => !(labelled.has(n.id) || (ids && ids.has(n.id))));
      lk.classed("dim", (l) => ids && !(ids.has(l.source.id) && ids.has(l.target.id))).classed("hot", (l) => ids && ids.has(l.source.id) && ids.has(l.target.id));
    };
    nd.on("mouseenter", (ev, n) => {
      show(nbr.get(n.id), new Set([n.id]));
      const ties = g.adj[n.id].slice().sort((a, b) => b[1] - a[1]).slice(0, 5).map(([j]) => esc(byIdx.get(j)?.name || g.names[j]));
      tipAt(ev.currentTarget.getBoundingClientRect(), `<b>${esc(n.name)}</b>${n.n} ${P(n.n, "letter", "letters", "Brief", "Briefe")}<div class="muted">${T("closest", "am nächsten")}: ${ties.join(", ")}</div>`);
    }).on("mouseleave", () => { show(null, null); hideTip(); });
    const groupIds = (gid) => new Set(nodes.filter((n) => n.g === gid).map((n) => n.id));
    document.getElementById("ngroups").addEventListener("mouseover", (ev) => {
      const li = ev.target.closest("li[data-g]");
      if (li) { const ids = groupIds(+li.dataset.g); show(ids, ids); }
    });
    document.getElementById("ngroups").addEventListener("mouseleave", () => show(null, null));
    const q = document.getElementById("nq");
    const find = () => {
      const n = nodes.find((x) => x.name.toLowerCase() === q.value.trim().toLowerCase());
      if (n) show(nbr.get(n.id), new Set([n.id])); else show(null, null);
    };
    q.addEventListener("input", find);
    if (focus) find();
  }

  /* ---------- letter ---------- */
  async function getLetter(id) {
    if (!state.letters.has(id)) state.letters.set(id, await json(`data/letters/${id}.json`));
    return state.letters.get(id);
  }

  async function showLetter(id, pageNo, readerId) {
    setNav("letters");
    let letter;
    try { letter = await getLetter(id); } catch { app.innerHTML = `<p>${T("No letter", "Kein Brief")} ${esc(id)}. <a href="#/">${T("All letters", "Alle Briefe")}</a></p>`; return; }
    const pages = letter.pages;
    pageNo = Math.min(Math.max(pageNo || 1, 1), pages.length);
    const page = pages[pageNo - 1];
    if (readerId) { state.reader = readerId; store.set("reader", readerId); }
    let reader = state.reader;
    const edited = letter.edited !== false;
    if (reader !== "edition" && !page.readings[reader]) reader = "edition";
    if (!edited && reader === "edition") {
      const firstRead = state.readers.find((x) => x.id !== "edition" && page.readings[x.id]);
      reader = firstRead ? firstRead.id : "edition";
    }
    state.view = { page, reader };  // for the uncertainty tooltip

    const pos = state.index.findIndex((l) => l.id === id);
    const prev = state.index[pos - 1], next = state.index[pos + 1];
    const s = letter.sender.filter((a) => a.type !== "place").map((a) => a.text).join(", ");
    const r = letter.recipient.filter((a) => a.type !== "place").map((a) => a.text).join(", ");

    const keepImage = state.osd && app.querySelector("#osd") && app.dataset.facs === page.facs;
    const html = `
      <div class="letter">
        <section class="pane textpane">
          <div class="lnav">
            ${prev ? `<a href="#/letter/${prev.id}/1">← ${esc(nrOf(prev))}</a>` : "<span></span>"}
            <a href="#/">${T("All letters", "Alle Briefe")}</a>
            ${next ? `<a href="#/letter/${next.id}/1">${esc(nrOf(next))} →</a>` : "<span></span>"}
          </div>
          <div class="muted small">${esc(letter.title)} · ${esc(letter.idno)}</div>
          ${edited ? `
          <h1>${esc(fmtDate(letter.date))}</h1>
          <div class="meta"><span class="parties">${esc(s || "?")} → ${esc(r || "?")}</span></div>
          <details class="regest" ${store.get("regest", true) ? "open" : ""}><summary>Regest</summary>
            <p>${esc(letter.regest)}</p>
            ${letter.keywords.some((k) => k.trim()) ? `<div class="kw">${letter.keywords.filter((k) => k.trim()).map((k) => `<a href="#/keyword/${encodeURIComponent(k)}">${esc(k)}</a>`).join("")}</div>` : ""}
          </details>` : `
          <h1>${esc(nrOf(letter))}${letter.auto_regest && letter.auto_regest.year ? ` <span class="muted">· ${esc(letter.auto_regest.year)}?</span>` : ""}</h1>
          <p class="caveat">${T("Not yet edited: the Stadtarchiv has not published this letter. What follows are machine readings only, with no edition to compare them with.",
            "Noch nicht ediert: Das Stadtarchiv hat diesen Brief nicht veröffentlicht. Was folgt, sind nur maschinelle Lesungen, ohne Edition zum Vergleich.")}</p>
          ${draftBlock(letter.auto_regest, true)}
          ${(letter.auto_keywords || []).length ? `<div class="kw auto" title="${T("Picked automatically from the edition's vocabulary; unchecked (experimental)", "Automatisch aus dem Vokabular der Edition gewählt; ungeprüft (experimentell)")}">
            <span class="kwlab">${T("Keywords (automatic, experimental)", "Schlagwörter (automatisch, experimentell)")}</span>
            ${letter.auto_keywords.map((k) => `<a href="#/keyword/${encodeURIComponent(k)}">${esc(k)}</a>`).join("")}</div>` : ""}`}
          ${edited && letter.auto_regest ? draftBlock(letter.auto_regest, false) : ""}
          ${modeSelect(page, reader)}
          <div class="pages"><span class="lbl">${T("Page", "Seite")}</span>${pages.map((p, i) =>
            `<a class="pg ${i + 1 === pageNo ? "on" : ""}" href="#/letter/${id}/${i + 1}" ${i + 1 === pageNo ? 'aria-current="page"' : ""}>${i + 1}</a>`).join("")}</div>
          <div class="readers" role="tablist" aria-label="${T("Readings", "Lesungen")}">${readerButtons(page, reader)}</div>
          <div id="reading">${readingHtml(page, reader)}</div>
        </section>
        <section class="pane imgpane">
          <div id="osd" role="img" aria-label="${T("Facsimile of", "Faksimile von")} ${esc(page.facs)}"></div>
          <div class="imgbar"><span class="muted">${esc(page.facs)}</span><span id="imglink"></span></div>
        </section>
      </div>
      <footer class="foot">Edition: Stadtarchiv St. Gallen (CC BY-SA 4.0) · ${T("Readings", "Lesungen")}: DH Bern</footer>`;

    if (keepImage) {
      app.querySelector(".textpane").outerHTML = new DOMParser().parseFromString(html, "text/html").querySelector(".textpane").outerHTML;
    } else {
      destroyOsd();
      app.innerHTML = html;
      app.dataset.facs = page.facs;
      openImage(page.facs, edited);
    }
    wireLetter(letter, id, pageNo, page);
  }

  /* A regest drafted by a language model from a machine reading: always marked as such. */
  function draftBlock(d, open) {
    if (!d) return "";
    const who = d.sender || d.recipient ? `<div class="meta"><span class="parties">${esc(d.sender || "?")} → ${esc(d.recipient || "?")}</span></div>` : "";
    const when = [d.place, d.date_text].filter(Boolean).map(esc).join(", ");
    return `<details class="regest draft" ${open ? "open" : ""}><summary>${T(`Automatic regest (draft${open ? "" : ", for comparison"})`, `Automatisches Regest (Entwurf${open ? "" : ", zum Vergleich"})`)}</summary>
      ${who}${when ? `<p class="small muted">${when}${d.year ? T(` · year read as ${esc(d.year)} (unchecked)`, ` · Jahr gelesen als ${esc(d.year)} (ungeprüft)`) : ""}</p>` : ""}
      <p>${esc(d.regest)}</p>
      <p class="small muted">${T(`Drafted by ${esc(d.model || "a language model")} from the ${esc(readerLabel(d.reader))} reading, without correction. Names and dates may be wrong.`,
        `Entworfen von ${esc(d.model || "einem Sprachmodell")} aus der Lesung von ${esc(readerLabel(d.reader))}, ohne Korrektur. Namen und Daten können falsch sein.`)}</p>
    </details>`;
  }
  function readerLabel(id) { const r = state.readers.find((x) => x.id === id); return r ? loc(r, "label") : (id || T("machine", "Maschine")); }

  function readerButtons(page, current) {
    return state.readers.map((r) => {
      const has = r.id === "edition" ? !!page.html : !!page.readings[r.id];
      const rd = page.readings[r.id];
      const badge = r.id === "edition" ? "" : rd && rd.cer != null ? `<span class="cer" title="${T("character error rate on this page", "Zeichenfehlerrate auf dieser Seite")}">${pct(rd.cer)}</span>` : rd ? `<span class="cer">${T("not scored", "nicht bewertet")}</span>` : "";
      return `<button class="rd ${r.id === current ? "on" : ""}" role="tab" aria-selected="${r.id === current}" data-reader="${esc(r.id)}" ${has ? "" : `disabled title="${T("not read on this page", "auf dieser Seite nicht gelesen")}"`}>
        <span>${esc(loc(r, "label"))}<span class="k">${esc(loc(r, "kind"))}</span></span>${badge}</button>`;
    }).join("");
  }

  function hasEnts(page, reader) {
    if (reader === "edition") return /class="ent /.test(page.html || "");
    return !!(page.auto_entities && page.auto_entities[reader] && page.auto_entities[reader].length);
  }

  function legend() {
    return `<div class="legend"><span class="per"><i></i>${T("person", "Person")}</span><span class="org"><i></i>${T("organisation", "Organisation")}</span><span class="place"><i></i>${T("place", "Ort")}</span><span class="date"><i></i>${T("date", "Datum")}</span></div>`;
  }

  /* a fine-tuned reader's caveat belongs on the pages it trained on, not on held-out or unedited ones */
  function pageCaveat(r, page) {
    if (r.caveat_scope !== "training_pages") return r.caveat ? `<p class="caveat">${esc(loc(r, "caveat"))}</p>` : "";
    if (page.fit === "train") return `<p class="caveat">${esc(loc(r, "caveat_page"))}</p>`;
    if (page.fit === "heldout") return `<p class="about-reader">${esc(loc(r, "heldout_note"))}</p>`;
    return "";
  }

  /* the ways a reading can be shown, and whether this page and reader offer each */
  const MODES = [
    ["plain", () => T("Text only", "Nur Text"), () => true],
    ["ents", () => T("Entities", "Entitäten"), (page, reader) => hasEnts(page, reader)],
    ["diff", () => T("Compare with edition", "Mit Edition vergleichen"), (page, reader) => reader !== "edition" && !!page.text && !!page.readings[reader]],
    ["unc", () => T("Uncertain words", "Unsichere Wörter"), (page, reader) => uncFor(page, reader).length > 0],
  ];
  /* the chosen view if this page and reader offer it, else the text alone */
  function modeFor(page, reader) {
    const m = MODES.find(([id]) => id === state.mode);
    return m && m[2](page, reader) ? m[0] : "plain";
  }
  function modeSelect(page, reader) {
    const cur = modeFor(page, reader);
    return `<div class="viewmode"><label for="t-mode">${T("View", "Ansicht")}</label>
      <select id="t-mode">${MODES.map(([id, label, ok]) => `<option value="${id}" ${id === cur ? "selected" : ""} ${ok(page, reader) ? "" : "disabled"}>${esc(label())}</option>`).join("")}</select>
      ${cur !== state.mode ? `<span class="muted small">${T("(not available for this reading)", "(für diese Lesung nicht verfügbar)")}</span>` : ""}</div>`;
  }

  function readingHtml(page, readerId) {
    const r = state.readers.find((x) => x.id === readerId) || state.readers[0];
    const head = `${r.about ? `<p class="about-reader">${esc(loc(r, "about"))}${fnMark(r)}</p>` : ""}${pageCaveat(r, page)}${fnHtml(r)}`;
    const mode = modeFor(page, readerId);
    const showEnts = mode === "ents";
    if (readerId === "edition") {
      if (!page.html) return head + `<p class="empty">${T("The edition has no text on this page.", "Die Edition hat auf dieser Seite keinen Text.")}</p>`;
      return head + (showEnts ? legend() : "") + lineNote(page.ln, page) + `<div class="text edition ${showEnts ? "ents" : ""}">${lineify(page.html)}</div>`;
    }
    const rd = page.readings[readerId];
    if (!rd) return head + `<p class="empty">${T("Not read on this page.", "Auf dieser Seite nicht gelesen.")}</p>`;
    if (!rd.text) return head + `<p class="empty">${T("Empty reading", "Leere Lesung")}${page.text ? "" : T(" — the edition has no text here either", " – auch die Edition hat hier keinen Text")}.</p>`;
    const trunc = rd.truncated ? `<p class="caveat">${T("This reading hit the model's length limit and stops early.", "Diese Lesung hat die Längengrenze des Modells erreicht und bricht vorzeitig ab.")}</p>` : "";
    let body;
    const unc = uncFor(page, readerId);
    const showUnc = mode === "unc";
    if (mode === "diff") body = diffHtml(page.text, rd.text);
    else if (showEnts) body = entHtml(rd.text, page.auto_entities[readerId], null);
    else body = showUnc ? uncHtml(rd.text, unc) : esc(rd.text);
    return head + trunc + (showEnts ? legend() : "") + (showUnc ? uncLegend(unc) : "") + lineNote(rd.ln, page) +
      `<div class="text ${showEnts ? "ents" : ""}">${numberLines(body)}</div>`;
  }

  /* the edition's html carries <br> + line numbers; wrap each line in a block */
  function lineify(html) {
    return html.split("<br>").map((l, i) => `<span class="line" data-i="${i}">${l}</span>`).join("");
  }
  function numberLines(body) {
    return body.split("\n").map((l, i) => `<span class="line" data-i="${i}"><span class="ln">${i + 1}</span>${l}</span>`).join("");
  }

  /* ---------- lines on the image ----------
     build_site.py puts the line readers' boxes together into rows (page.rows:
     per row its boxes, in ten-thousandths of the image width) and gives every text, per line, the
     rows it is linked to (ln; empty where no link was good enough). Pointing at
     a line shows its row on the image; pointing at the image shows the line. */
  function lineNote(ln, page) {
    if (!ln || !page.rows) return "";
    const lines = ln.length, linked = ln.filter((x) => x.length).length;
    return `<p class="line-note small muted">${T(`${linked} of ${lines} lines linked to the image: point at a line, or at the image.`,
      `${linked} von ${lines} Zeilen mit dem Bild verknüpft: Zeiger auf eine Zeile oder auf das Bild.`)}</p>`;
  }
  function currentLn() {
    const v = state.view || {};
    if (!v.page || !v.page.rows) return null;
    if (v.reader === "edition") return v.page.ln || null;
    const rd = v.page.readings[v.reader];
    return (rd && rd.ln) || null;
  }
  let hlEls = [];
  function clearRows() {
    hlEls.forEach((el) => state.osd && state.osd.removeOverlay(el));
    hlEls = [];
    app.querySelectorAll("#reading .line.hl").forEach((el) => el.classList.remove("hl"));
  }
  function showRows(rows) {
    const v = state.view || {};
    if (!state.osd || !v.page || !v.page.rows || !window.OpenSeadragon) return;
    for (const j of rows) {
      for (const b of v.page.rows[j] || []) {
        const el = document.createElement("div");
        el.className = "row-hl";
        state.osd.addOverlay({ element: el, location: new OpenSeadragon.Rect(b[0] / 1e4, b[1] / 1e4, (b[2] - b[0]) / 1e4, (b[3] - b[1]) / 1e4) });
        hlEls.push(el);
      }
    }
  }
  function onLineHover(ev) {
    const line = ev.target.closest(".line[data-i]");
    const ln = currentLn();
    clearRows();
    if (!line || !ln) return;
    const rows = ln[+line.dataset.i] || [];
    if (!rows.length) return;
    line.classList.add("hl");
    showRows(rows);
  }
  function rowAt(pt) {
    const v = state.view || {};
    if (!v.page || !v.page.rows) return -1;
    const x = pt.x * 1e4, y = pt.y * 1e4;
    let hit = -1, area = Infinity;
    v.page.rows.forEach((rects, j) => rects.forEach((b) => {
      if (x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3]) {
        const a = (b[2] - b[0]) * (b[3] - b[1]);
        if (a < area) { hit = j; area = a; }
      }
    }));
    return hit;
  }
  function linesOfRow(j) {
    const ln = currentLn();
    if (!ln || j < 0) return [];
    return [...app.querySelectorAll("#reading .line[data-i]")].filter((el) => (ln[+el.dataset.i] || []).includes(j));
  }
  function trackImage() {
    if (!state.osd || !window.OpenSeadragon) return;
    let last = -2;
    const tracker = new OpenSeadragon.MouseTracker({
      element: state.osd.canvas,
      moveHandler: (e) => {
        const j = rowAt(state.osd.viewport.pointFromPixel(e.position));
        if (j === last) return;
        last = j;
        clearRows();
        const els = linesOfRow(j);
        if (!els.length) return;
        els.forEach((el) => el.classList.add("hl"));
        showRows([j]);
      },
      leaveHandler: () => { last = -2; clearRows(); },
    });
    state.osd.addHandler("canvas-click", (e) => {
      if (!e.quick) return;
      const els = linesOfRow(rowAt(state.osd.viewport.pointFromPixel(e.position)));
      if (els.length) els[0].scrollIntoView({ block: "center", behavior: "smooth" });
    });
    state.osd.addHandler("destroy", () => tracker.destroy());
  }

  function entHtml(text, ents, unc) {
    const sorted = [...ents].sort((a, b) => a.start - b.start);
    const piece = (a, b) => unc ? uncHtml(text, unc, a, b) : esc(text.slice(a, b));
    let out = "", at = 0;
    for (const e of sorted) {
      if (e.start < at) continue;
      out += piece(at, e.start);
      const attrs = `class="ent ${e.type}" data-ref="${esc(e.ref || e.when || "")}" data-auto="1" data-el="${esc(e.el || "")}"` +
        ` data-role="${esc(e.role || "")}" data-cert="${esc(e.cert || e.when_cert || "")}"`;
      const seg = piece(e.start, e.end).split("\n").join(`</span>\n<span ${attrs}>`);
      out += `<span ${attrs}>${seg}</span>`;
      at = e.end;
    }
    return out + piece(at, text.length);
  }

  /* words the readers disagree on (joint_reading.py). A page carries
     [Argon start, end, joint start, end, 1|2, % agreement, [[alternative, codes]]];
     uncFor() gives [start, end, "low"|"medium", share, alternatives, index] for one reader. */
  const UNC_READER = { A: "gemini-4-argon", F: "gemini-3.8-flash", Q: "qwen3.5-4b-german-kurrent-xvi-xix-page-v1",
    O: "olmocr2-7b-german-kurrent-xvi-xix-blb-page-px4m-native-v1", KB: "kraken-bern-combined-v1",
    KI: "kraken-cursive_inzigkofen", TK: "trocr-kurrent-xvi-xvii", TM: "trocr-medieval-escriptmask" };
  function uncFor(page, readerId) {
    if (!page || !page.unc || !page.readings || !page.readings[readerId]) return [];
    const at = readerId === "gemini-4-argon" ? 0 : readerId === "joint" ? 2 : -1;
    if (at < 0) return [];
    return page.unc.map((m, i) => [m[at], m[at + 1], m[4] === 1 ? "low" : "medium", m[5] / 100,
      m[6].map(([t, codes]) => [t, codes.split("+").map((c) => UNC_READER[c] || c)]), i]);
  }
  function uncHtml(text, unc, from = 0, to = text.length) {
    let out = "", at = from;
    unc.forEach((m) => {
      const [s, e, level, , , i] = m;
      if (s < at || e > to || s >= to) return;
      out += esc(text.slice(at, s)) + `<span class="unc ${level}" data-unc="${i}">${esc(text.slice(s, e))}</span>`;
      at = e;
    });
    return out + esc(text.slice(at, to));
  }
  function uncLegend(unc) {
    const low = unc.filter((m) => m[2] === "low").length, med = unc.length - low;
    return `<p class="unc-legend small muted"><span class="unc low">${T("uncertain", "unsicher")}</span> ${low} ·
      <span class="unc medium">${T("doubtful", "zweifelhaft")}</span> ${med} —
      ${T("the machine readings disagree here; hover for their alternatives", "hier gehen die maschinellen Lesungen auseinander; Zeiger darauf zeigt ihre Varianten")}</p>`;
  }

  /* word diff: tokens of the reading against tokens of the edition (LCS) */
  function diffHtml(ref, hyp) {
    const tok = (s) => s.split(/(\s+)/).filter((t) => t !== "");
    const a = tok(ref).filter((t) => !/^\s+$/.test(t));
    const bAll = tok(hyp);
    const b = bAll.filter((t) => !/^\s+$/.test(t));
    const n = a.length, m = b.length;
    if (n * m > 4e6) return esc(hyp);
    const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    // walk, emitting hypothesis tokens with their original whitespace (keeps line breaks)
    const seps = []; { let k = -1; for (const t of bAll) { if (/^\s+$/.test(t)) { seps[k] = (seps[k] || "") + t; } else { k++; } } }
    let i = 0, j = 0, out = "", pendingDel = [];
    const flushDel = () => { if (pendingDel.length) { out += `<del>${esc(pendingDel.join(" "))}</del> `; pendingDel = []; } };
    while (i < n || j < m) {
      if (i < n && j < m && a[i] === b[j]) { flushDel(); out += esc(b[j]) + (seps[j] ?? ""); i++; j++; }
      else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) { flushDel(); out += `<ins>${esc(b[j])}</ins>` + (seps[j] ?? ""); j++; }
      else { pendingDel.push(a[i]); i++; }
    }
    flushDel();
    return out.replace(/ \n/g, "\n");
  }

  function wireLetter(letter, id, pageNo, page) {
    app.querySelectorAll(".rd").forEach((b) => b.addEventListener("click", () => {
      if (b.disabled) return;
      location.hash = `#/letter/${id}/${pageNo}/${b.dataset.reader}`;
    }));
    const m = app.querySelector("#t-mode");
    m && m.addEventListener("change", () => { state.mode = m.value; store.set("mode", m.value); route(); });
    const det = app.querySelector("details.regest");
    det && det.addEventListener("toggle", () => store.set("regest", det.open));
    app.querySelector("#reading").addEventListener("mouseover", entTip);
    app.querySelector("#reading").addEventListener("mouseover", onLineHover);
    app.querySelector("#reading").addEventListener("mouseleave", clearRows);
    app.querySelector("#reading").addEventListener("click", entTip);
  }

  /* ---------- tooltips ---------- */
  let tipEl = null;
  function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
  document.addEventListener("click", (ev) => { if (tipEl && !tipEl.contains(ev.target) && !ev.target.closest(".ent,.note,.choice")) hideTip(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") hideTip(); });
  function entTip(ev) {
    const u = ev.target.closest(".unc[data-unc]");
    if (u && !ev.target.closest(".ent")) return uncTip(u);
    const el = ev.target.closest(".ent");
    if (!el) return;
    const ref = el.dataset.ref || "";
    const reg = state.registers[ref];
    const kind = [...el.classList].find((c) => ["per", "place", "org", "date"].includes(c));
    const kn = { per: T("Person", "Person"), place: T("Place", "Ort"), org: T("Organisation", "Organisation"), date: T("Date", "Datum") }[kind] || "";
    const sub = { origPlace: T("place of writing", "Ausstellungsort"), origDate: T("date line", "Datierung") }[el.dataset.el] || "";
    let body = `<b>${esc(kn)}${sub ? " · " + sub : ""}${el.dataset.auto ? T(" · automatic", " · automatisch") : ""}</b>`;
    if (kind === "date" && ref) body += esc(ref);
    else if (reg) {
      body += `<a href="#/entry/${encodeURIComponent(ref)}">${esc(reg.name)}</a>`;
      if (reg.occ) body += ` <span class="muted">· ${lettersOf(reg)} ${P(lettersOf(reg), "letter", "letters", "Brief", "Briefe")}</span>`;
      const links = reg.links.filter((l) => /^https?:/.test(l.url)).slice(0, 4);
      if (links.length) body += `<div>${links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.type)}</a>`).join("")}</div>`;
    } else if (el.dataset.auto) body += `<span class="muted">${T("not linked to the register", "nicht mit dem Register verknüpft")}</span>`;
    else if (ref) body += esc(ref);
    if (el.dataset.role) body += `<div class="muted">${T("role", "Rolle")}: ${esc(roleName(el.dataset.role))}</div>`;
    if (el.dataset.auto && el.dataset.cert) body += `<div class="muted">${kind === "date" ? T("year inferred from the neighbouring letters", "Jahr aus den benachbarten Briefen erschlossen") : T("uncertain link (close spelling)", "unsichere Verknüpfung (ähnliche Schreibung)")}</div>`;
    hideTip();
    tipEl = document.createElement("div");
    tipEl.className = "tip";
    tipEl.innerHTML = body;
    document.body.appendChild(tipEl);
    const r = el.getBoundingClientRect();
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left)) + "px";
    tipEl.style.top = (r.bottom + 6 + h > window.innerHeight ? r.top - h - 6 : r.bottom + 6) + "px";
  }

  function uncTip(el) {
    const v = state.view || {};
    const m = uncFor(v.page, v.reader).find((x) => x[5] === +el.dataset.unc);
    if (!m) return;
    const [, , level, share, alts] = m;
    const who = (ids) => ids.map((r) => readerLabel(r)).join(", ");
    let body = `<b>${level === "low" ? T("Uncertain reading", "Unsichere Lesung") : T("Doubtful reading", "Zweifelhafte Lesung")}</b>` +
      `<div class="muted">${T("readers behind this form", "Leser hinter dieser Form")}: ${Math.round(share * 100)} %</div>`;
    if (alts && alts.length) body += `<div>${T("other readings", "andere Lesungen")}:</div>` +
      alts.map(([t, by]) => `<div>«${esc(t)}» <span class="muted">${esc(who(by))}</span></div>`).join("");
    else body += `<div class="muted">${T("no other reader has this word", "kein anderer Leser hat dieses Wort")}</div>`;
    hideTip();
    tipEl = document.createElement("div");
    tipEl.className = "tip";
    tipEl.innerHTML = body;
    document.body.appendChild(tipEl);
    const r = el.getBoundingClientRect();
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left)) + "px";
    tipEl.style.top = (r.bottom + 6 + h > window.innerHeight ? r.top - h - 6 : r.bottom + 6) + "px";
  }

  /* ---------- image ---------- */
  function destroyOsd() { hlEls = []; if (state.osd) { state.osd.destroy(); state.osd = null; } delete app.dataset.facs; }
  /* The server's info.json lists `sizes` and an @id without the /cantaloupe
     prefix; with `sizes` OpenSeadragon 4.1 stops at the 94 px thumbnail.
     Order: the edition's IIIF server (all published pages and some others);
     else, while config.json's image window is open, our own copy; else a notice. */
  function windowOpen() {
    const w = state.config && state.config.image_window;
    if (!w || !w.base || !w.until) return null;
    const today = new Date().toISOString().slice(0, 10);
    return (!w.from || today >= w.from) && today <= w.until ? w : null;
  }

  function osd(tileSources, cors = "Anonymous") {
    state.osd = OpenSeadragon({
      id: "osd",
      prefixUrl: "https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/images/",
      tileSources,
      showNavigator: true, navigatorPosition: "BOTTOM_RIGHT",
      gestureSettingsMouse: { clickToZoom: false },
      crossOriginPolicy: cors,
    });
    trackImage();
  }

  function setImgLink(url, label) {
    const el = document.getElementById("imglink");
    if (el) el.innerHTML = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)} ↗</a>` : "";
  }

  /* Images of letters not yet edited are shown only inside the agreed window,
     even though the archive's IIIF server holds most of them: showing them is
     the Stadtarchiv's decision, not a technical question. */
  function notPublic(w) {
    document.getElementById("osd").innerHTML =
      `<p class="noimg">${T("The image of this page is not shown here: the letter has not yet been published by the Stadtarchiv St. Gallen.",
        "Das Bild dieser Seite wird hier nicht gezeigt: Das Stadtarchiv St. Gallen hat den Brief noch nicht veröffentlicht.")}</p>`;
    setImgLink(null);
  }

  async function openImage(facs, edited = true) {
    if (!window.OpenSeadragon) return;
    const w = edited ? null : windowOpen();
    if (!edited && !w) { notPublic(); return; }
    /* A page outside the edition is keyed by our working copy (<stem>.jpg);
       the archive's server may hold it as .JPG, so try both spellings. */
    const swapped = facs.replace(/\.(jpe?g)$/i, (m, ext) => "." + (ext === ext.toLowerCase() ? ext.toUpperCase() : ext.toLowerCase()));
    let info = null, base = null;
    for (const name of facs === swapped ? [facs] : [facs, swapped]) {
      const b = `${IIIF}${encodeURIComponent(name)}`;
      try { info = await json(`${b}/info.json`); base = b; break; } catch { info = null; }
    }
    if (app.dataset.facs !== facs || !document.getElementById("osd")) return;
    if (info) {
      info["@id"] = base;
      delete info.sizes;
      osd(info);
      setImgLink(`${base}/full/max/0/default.jpg`, edited ? T("Full image", "Ganzes Bild") : T(`Full image (shown until ${fmtDate(w.until)})`, `Ganzes Bild (sichtbar bis ${fmtDate(w.until)})`));
      return;
    }
    if (w) {
      const url = w.base.replace(/\/?$/, "/") + encodeURIComponent(facs.replace(/\.[^.]+$/, "")) + ".jpg";
      osd({ type: "image", url }, false);
      setImgLink(url, T(`Image (available until ${fmtDate(w.until)})`, `Bild (verfügbar bis ${fmtDate(w.until)})`));
      return;
    }
    notPublic();
  }

  boot().catch((err) => { app.innerHTML = `<p>${T("Could not load the data", "Die Daten konnten nicht geladen werden")}: ${esc(err.message)}</p>`; });
})();
