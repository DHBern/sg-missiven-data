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
  state.ents = store.get("ents", true);
  state.diff = store.get("diff", false);
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
                  map: T("Map", "Karte"), readers: T("Readers", "Leser"), about: T("About", "Über") };
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
    if (parts[0] === "entry" && parts[1]) return showEntry(parts[1]);
    if (parts[0] === "about") return showAbout();
    return showList(new URLSearchParams(qs || ""));
  }

  /* ---------- letters list ---------- */
  function showList(params) {
    setNav("letters");
    state.edited = params && ["0", "1"].includes(params.get("ed")) ? params.get("ed") : "";
    destroyOsd();
    // Edited letters carry a date, unedited ones only an estimated year.
    const yNum = (l) => { const y = parseInt((l.date || String(l.year || "")).slice(0, 4), 10); return Number.isFinite(y) ? y : null; };
    const ys = state.index.map(yNum).filter((y) => y != null);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const yArg = (v, d) => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(yMax, Math.max(yMin, n)) : d; };
    state.from = yArg(params && params.get("from"), yMin);
    state.to = yArg(params && params.get("to"), yMax);
    if (state.from > state.to) [state.from, state.to] = [state.to, state.from];
    const BIN = 10, b0 = Math.floor(yMin / BIN) * BIN, nBins = Math.floor(yMax / BIN) - b0 / BIN + 1;
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
        <div class="yrange">
          <div class="yr-head"><span>${T("Years", "Jahre")}</span><b id="yrlab"></b></div>
          <div class="yr-track" title="${T("Letters not yet edited are placed by their estimated year.", "Noch nicht edierte Briefe stehen bei ihrem geschätzten Jahr.")}">
            <div class="yr-hist" id="yrhist" aria-hidden="true"></div>
            <input type="range" id="yfrom" min="${yMin}" max="${yMax}" step="1" value="${state.from}" aria-label="${T("From year", "Von Jahr")}">
            <input type="range" id="yto" min="${yMin}" max="${yMax}" step="1" value="${state.to}" aria-label="${T("To year", "Bis Jahr")}">
          </div>
        </div>
        <span id="count" class="muted small"></span>
      </div>
      <div class="list" id="list"></div>`;
    const q = document.getElementById("q");
    const ed = document.getElementById("ed");
    const yf = document.getElementById("yfrom"), yt = document.getElementById("yto");
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
      const bins = new Array(nBins).fill(0);
      const rows = state.index.filter((l) => {
        if (state.edited && String(+(l.edited !== false)) !== state.edited) return false;
        if (words.length) {
          const hay = [l.title, l.idno, l.date, fmtDate(l.date), l.sender.join(" "), l.recipient.join(" "), l.regest].join(" ").toLowerCase();
          if (!words.every((w) => hay.includes(w))) return false;
        }
        const y = yNum(l);
        if (y != null) bins[Math.floor(y / BIN) - b0 / BIN]++;
        return !narrowed || (y != null && y >= state.from && y <= state.to);
      });
      const top = Math.max(1, ...bins);
      document.getElementById("yrhist").innerHTML = bins.map((n, i) => {
        const lo = b0 + i * BIN;
        return `<i class="${lo + BIN - 1 >= state.from && lo <= state.to ? "in" : ""}" style="height:${n ? Math.max(6, Math.round(100 * n / top)) : 0}%"></i>`;
      }).join("");
      document.getElementById("yrlab").textContent = `${state.from}–${state.to}`;
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
    const slide = (ev) => {
      let a = +yf.value, b = +yt.value;
      if (a > b) { if (ev.target === yf) a = b; else b = a; yf.value = a; yt.value = b; }
      state.from = a; state.to = b;
      // the handle last moved stays on top, so two handles at one end can still be pulled apart
      yf.style.zIndex = ev.target === yf ? 3 : 2; yt.style.zIndex = ev.target === yt ? 3 : 2;
      draw();
    };
    yf.addEventListener("input", slide); yt.addEventListener("input", slide);
    yf.addEventListener("change", sync); yt.addEventListener("change", sync);
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
          <td class="small">${esc(loc(r, "about"))}${r.caveat ? `<div class="caveat">${esc(loc(r, "caveat"))}</div>` : ""}${r.note ? `<div class="muted">${esc(loc(r, "note"))}</div>` : ""}</td></tr>`).join("")}
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
        <label class="small"><input type="checkbox" id="ru" ${f.auto ? "checked" : ""}> ${T("in letters not yet edited", "in noch nicht edierten Briefen")} (${all.filter(([, e]) => e.occ_auto).length})</label>
        <span id="rcount" class="muted small"></span>
      </div>
      <div class="list reg" id="rlist"></div>`;
    const q = document.getElementById("rq"), t = document.getElementById("rt"), g = document.getElementById("rg"), u = document.getElementById("ru");
    const draw = () => {
      f[which] = q.value;
      if (which === "places") { f.ftype = t.value; f.nogeo = g.checked; } else f.kind = t.value;
      f.auto = u.checked;
      const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
      const rows = all.filter(([, e]) => {
        if (which === "places" && t.value && e.ftype !== t.value) return false;
        if (which === "places" && g.checked && e.geo) return false;
        if (which === "names" && t.value && e.kind !== t.value) return false;
        if (u.checked && !e.occ_auto) return false;
        if (!words.length) return true;
        const hay = [e.name, e.desc, e.country, ...(e.occ || []).map((o) => o[1]), ...(e.occ_auto || []).map((o) => o[1])].join(" ").toLowerCase();
        return words.every((w) => hay.includes(w));
      });
      document.getElementById("rcount").textContent = `${rows.length} ${P(rows.length, "entry", "entries", "Eintrag", "Einträge")}`;
      document.getElementById("rlist").innerHTML = rows.slice(0, 500).map(([id, e]) => {
        const n = lettersOf(e);
        return `<a class="row" href="#/entry/${encodeURIComponent(id)}">
          <span class="num">${n || "–"}<span class="muted small"> ${P(n, "letter", "letters", "Brief", "Briefe")}</span></span>
          <span><span class="who">${esc(e.name)}${which === "places" && !e.geo ? ` <span class="badge">${T("no coordinates", "ohne Koordinaten")}</span>` : ""}${autoLettersOf(e) ? ` <span class="badge auto">+${autoLettersOf(e)} ${T("not yet edited", "noch nicht ediert")}</span>` : ""}</span>
          <span class="reg">${esc([which === "places" ? ftype(e.ftype) : kindName(e.kind), e.country, e.desc].filter(Boolean).join(" · "))}</span></span>
        </a>`;
      }).join("") + (rows.length > 500 ? `<p class="muted small">${T(`Showing 500 of ${rows.length} — narrow the search.`, `500 von ${rows.length} angezeigt – Suche eingrenzen.`)}</p>` : "");
    };
    q.addEventListener("input", draw);
    t.addEventListener("change", draw);
    g && g.addEventListener("change", draw);
    u.addEventListener("change", draw);
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
    const links = e.links.filter((l) => /^https?:/.test(l.url));
    app.innerHTML = `
      <section class="entry">
        <p class="muted small"><a href="#/${e.kind === "place" ? "places" : "names"}">← ${e.kind === "place" ? T("Places", "Orte") : T("Names", "Namen")}</a></p>
        <h1>${esc(e.name)}</h1>
        <p class="muted">${esc([e.kind === "place" ? ftype(e.ftype) || kindName("place") : kindName(e.kind), e.country, e.desc].filter(Boolean).join(" · "))}</p>
        ${links.length ? `<p class="links">${links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.type)}</a>`).join("")}</p>` : ""}
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
    const ui = { q: params.get("q") || "", tol: params.get("tol") !== "0", sort: params.get("sort") || "date",
                 from: params.get("from") || "", to: params.get("to") || "", shown: 50, open: new Set(params.getAll("open")) };
    app.innerHTML = `
      <section class="intro">
        <h1>${T("Search", "Suche")}</h1>
        <p>${T(`All ${meta.letters.length} letters: the text of the edition, and for letters and pages the edition does not have, the machine reading (${esc(readerLabel(meta.reader))}). Narrow the results with the facets; every count shows how many letters remain with it.`,
              `Alle ${meta.letters.length} Briefe: der Text der Edition, und für Briefe und Seiten, die die Edition nicht hat, die maschinelle Lesung (${esc(readerLabel(meta.reader))}). Die Facetten grenzen ein; jede Zahl sagt, wie viele Briefe damit bleiben.`)}</p>
      </section>
      <div class="sbar">
        <input id="sq" type="search" value="${esc(ui.q)}" placeholder="${T('Words or "a phrase" in the text…', 'Wörter oder «eine Wendung» im Text…')}" aria-label="${T("Search the text", "Text durchsuchen")}">
        <label class="small"><input type="checkbox" id="stol" ${ui.tol ? "checked" : ""}> ${T("tolerant spelling", "Schreibvarianten zulassen")}</label>
        <span class="yr">${T("Years", "Jahre")} <input id="sfrom" type="number" min="1300" max="1700" value="${esc(ui.from)}" aria-label="${T("from year", "ab Jahr")}"> – <input id="sto" type="number" min="1300" max="1700" value="${esc(ui.to)}" aria-label="${T("to year", "bis Jahr")}"></span>
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
      if (ui.from) u.set("from", ui.from);
      if (ui.to) u.set("to", ui.to);
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
      /* text and years first: they hold for every facet count */
      const hits = new Map();
      const base = meta.letters.filter((m) => {
        if (ui.from && (!m.y || m.y < +ui.from)) return false;
        if (ui.to && (!m.y || m.y > +ui.to)) return false;
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
      });
      const passes = (m, except) => FACETS.every((f) => f.k === except || !sel[f.k].size || facetValues(m, f.k).some((v) => sel[f.k].has(v)));
      const rows = base.filter((m) => passes(m, null));
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
    $("sfrom").addEventListener("input", () => { ui.from = $("sfrom").value; later(run, 400); });
    $("sto").addEventListener("input", () => { ui.to = $("sto").value; later(run, 400); });
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
            ${letter.keywords.length ? `<div class="kw">${letter.keywords.map((k) => `<span>${esc(k)}</span>`).join("")}</div>` : ""}
          </details>` : `
          <h1>${esc(nrOf(letter))}${letter.auto_regest && letter.auto_regest.year ? ` <span class="muted">· ${esc(letter.auto_regest.year)}?</span>` : ""}</h1>
          <p class="caveat">${T("Not yet edited: the Stadtarchiv has not published this letter. What follows are machine readings only, with no edition to compare them with.",
            "Noch nicht ediert: Das Stadtarchiv hat diesen Brief nicht veröffentlicht. Was folgt, sind nur maschinelle Lesungen, ohne Edition zum Vergleich.")}</p>
          ${draftBlock(letter.auto_regest, true)}`}
          ${edited && letter.auto_regest ? draftBlock(letter.auto_regest, false) : ""}
          <div class="pages"><span class="lbl">${T("Page", "Seite")}</span>${pages.map((p, i) =>
            `<a class="pg ${i + 1 === pageNo ? "on" : ""}" href="#/letter/${id}/${i + 1}" ${i + 1 === pageNo ? 'aria-current="page"' : ""}>${i + 1}</a>`).join("")}</div>
          <div class="readers" role="tablist" aria-label="${T("Readings", "Lesungen")}">${readerButtons(page, reader)}</div>
          <div class="toggles">
            <label><input type="checkbox" id="t-diff" ${state.diff ? "checked" : ""} ${reader === "edition" || !page.text ? "disabled" : ""}><span>${T("Compare with edition", "Mit Edition vergleichen")}</span></label>
            <label><input type="checkbox" id="t-ents" ${state.ents ? "checked" : ""} ${hasEnts(page, reader) ? "" : "disabled"}><span>${T("Entities", "Entitäten")}</span></label>
          </div>
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

  function readingHtml(page, readerId) {
    const r = state.readers.find((x) => x.id === readerId) || state.readers[0];
    const head = `${r.about ? `<p class="about-reader">${esc(loc(r, "about"))}</p>` : ""}${r.caveat ? `<p class="caveat">${esc(loc(r, "caveat"))}</p>` : ""}`;
    const showEnts = state.ents && hasEnts(page, readerId);
    if (readerId === "edition") {
      if (!page.html) return head + `<p class="empty">${T("The edition has no text on this page.", "Die Edition hat auf dieser Seite keinen Text.")}</p>`;
      return head + (showEnts ? legend() : "") + `<div class="text edition ${showEnts ? "ents" : ""}">${lineify(page.html)}</div>`;
    }
    const rd = page.readings[readerId];
    if (!rd) return head + `<p class="empty">${T("Not read on this page.", "Auf dieser Seite nicht gelesen.")}</p>`;
    if (!rd.text) return head + `<p class="empty">${T("Empty reading", "Leere Lesung")}${page.text ? "" : T(" — the edition has no text here either", " – auch die Edition hat hier keinen Text")}.</p>`;
    const trunc = rd.truncated ? `<p class="caveat">${T("This reading hit the model's length limit and stops early.", "Diese Lesung hat die Längengrenze des Modells erreicht und bricht vorzeitig ab.")}</p>` : "";
    let body;
    if (state.diff && page.text) body = diffHtml(page.text, rd.text);
    else if (showEnts) body = entHtml(rd.text, page.auto_entities[readerId]);
    else body = esc(rd.text);
    return head + trunc + (showEnts && !(state.diff && page.text) ? legend() : "") +
      `<div class="text ${showEnts ? "ents" : ""}">${numberLines(body)}</div>`;
  }

  /* the edition's html carries <br> + line numbers; wrap each line in a block */
  function lineify(html) {
    return html.split("<br>").map((l) => `<span class="line">${l}</span>`).join("");
  }
  function numberLines(body) {
    return body.split("\n").map((l, i) => `<span class="line"><span class="ln">${i + 1}</span>${l}</span>`).join("");
  }

  function entHtml(text, ents) {
    const sorted = [...ents].sort((a, b) => a.start - b.start);
    let out = "", at = 0;
    for (const e of sorted) {
      if (e.start < at) continue;
      out += esc(text.slice(at, e.start));
      const attrs = `class="ent ${e.type}" data-ref="${esc(e.ref || e.when || "")}" data-auto="1" data-el="${esc(e.el || "")}"` +
        ` data-role="${esc(e.role || "")}" data-cert="${esc(e.cert || e.when_cert || "")}"`;
      const seg = text.slice(e.start, e.end).split("\n").map(esc).join(`</span>\n<span ${attrs}>`);
      out += `<span ${attrs}>${seg}</span>`;
      at = e.end;
    }
    return out + esc(text.slice(at));
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
    const d = app.querySelector("#t-diff"), e = app.querySelector("#t-ents");
    d && d.addEventListener("change", () => { state.diff = d.checked; store.set("diff", d.checked); route(); });
    e && e.addEventListener("change", () => { state.ents = e.checked; store.set("ents", e.checked); route(); });
    const det = app.querySelector("details.regest");
    det && det.addEventListener("toggle", () => store.set("regest", det.open));
    app.querySelector("#reading").addEventListener("mouseover", entTip);
    app.querySelector("#reading").addEventListener("click", entTip);
  }

  /* ---------- tooltips ---------- */
  let tipEl = null;
  function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
  document.addEventListener("click", (ev) => { if (tipEl && !tipEl.contains(ev.target) && !ev.target.closest(".ent,.note,.choice")) hideTip(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") hideTip(); });
  function entTip(ev) {
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

  /* ---------- image ---------- */
  function destroyOsd() { if (state.osd) { state.osd.destroy(); state.osd = null; } delete app.dataset.facs; }
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
