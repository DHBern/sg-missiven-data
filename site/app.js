/* St. Gallen Missives — readings viewer. No build step, no framework. */
(() => {
  "use strict";
  const IIIF = "https://media.sources-online.org/cantaloupe/iiif/2/sg-missiven!";
  const app = document.getElementById("app");
  const state = { index: null, readers: null, registers: null, letters: new Map(),
                  osd: null, ents: true, diff: false, reader: "edition", filter: "", year: "" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem("sgm:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("sgm:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  state.ents = store.get("ents", true);
  state.diff = store.get("diff", false);
  state.reader = store.get("reader", "edition");

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x) => (x == null ? "–" : (x * 100).toFixed(1).replace(".", ".") + " %");
  const fmtDate = (d) => {
    if (!d) return "undated";
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
    route();
  }

  function setNav(which) {
    if (!["places", "names", "map"].includes(which)) destroyMaps();
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === which));
  }

  function route() {
    hideTip();
    const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
    if (parts[0] === "letter" && parts[1]) return showLetter(parts[1], +(parts[2] || 1), parts[3]);
    if (parts[0] === "readers") return showReaders();
    if (parts[0] === "places") return showRegister("places");
    if (parts[0] === "names") return showRegister("names");
    if (parts[0] === "map") return showMap();
    if (parts[0] === "entry" && parts[1]) return showEntry(parts[1]);
    if (parts[0] === "about") return showAbout();
    return showList();
  }

  /* ---------- letters list ---------- */
  function showList() {
    setNav("letters");
    destroyOsd();
    const years = [...new Set(state.index.map((l) => (l.date || "").slice(0, 2) + "00"))].filter((y) => /^\d{4}$/.test(y)).sort();
    const best = state.readers.filter((r) => r.cer != null).sort((a, b) => a.cer - b.cer)[0];
    app.innerHTML = `
      <section class="intro">
        <h1>The letters of St. Gallen, read by machines</h1>
        <p>${state.index.filter((l) => l.edited !== false).length} letters to and from the city of St. Gallen, 1400–1650, from the edition of the
        <a href="https://missiven.stadtarchiv.ch/" target="_blank" rel="noopener">Stadtarchiv St. Gallen</a>${unedited() ? `, and ${unedited()} letters of the series the edition has not yet published` : ""}.
        Every page is shown with the edition's transcription, where there is one, and with ${machineReaders()}.
        Switch between them on the left, compare any of them with the edition, and see where they differ.</p>
        ${best ? `<p class="muted small">Closest to the edition over all scored pages: <b>${esc(best.label)}</b>, ${pct(best.cer)} character error rate. <a href="#/readers">All readers</a>.</p>` : ""}
      </section>
      <div class="filters">
        <input id="q" type="search" placeholder="Search sender, recipient, regest, number…" value="${esc(state.filter)}" aria-label="Search letters">
        <select id="ed" aria-label="Edited or not">
          <option value="">All letters</option>
          <option value="1" ${state.edited === "1" ? "selected" : ""}>Edited</option>
          <option value="0" ${state.edited === "0" ? "selected" : ""}>Not yet edited</option>
        </select>
        <select id="yr" aria-label="Century">
          <option value="">All years</option>
          ${years.map((y) => `<option value="${y}" ${state.year === y ? "selected" : ""}>${y.slice(0, 2)}th c. (${y}s)</option>`).join("")}
        </select>
        <span id="count" class="muted small"></span>
      </div>
      <div class="list" id="list"></div>`;
    const q = document.getElementById("q");
    const yr = document.getElementById("yr");
    const ed = document.getElementById("ed");
    const draw = () => {
      state.filter = q.value; state.year = yr.value; state.edited = ed.value;
      const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
      const rows = state.index.filter((l) => {
        if (ed.value && String(+(l.edited !== false)) !== ed.value) return false;
        if (yr.value && (l.date || "").slice(0, 2) !== yr.value.slice(0, 2)) return false;
        if (!words.length) return true;
        const hay = [l.title, l.idno, l.date, fmtDate(l.date), l.sender.join(" "), l.recipient.join(" "), l.regest].join(" ").toLowerCase();
        return words.every((w) => hay.includes(w));
      });
      document.getElementById("count").textContent = `${rows.length} letters`;
      document.getElementById("list").innerHTML = rows.slice(0, 400).map((l) => `
        <a class="row" href="#/letter/${l.id}/1">
          <span class="num">${esc(nrOf(l))}</span>
          <span class="date">${l.edited === false ? (l.year ? esc(l.year) + "?" : "") : esc(fmtDate(l.date))}</span>
          ${l.edited === false
            ? (l.draft
              ? `<span><span class="who">${esc(l.sender.join(", ") || "?")} → ${esc(l.recipient.join(", ") || "?")} <span class="muted small">· not yet edited · draft</span></span><span class="reg">${esc(l.regest)}</span></span>`
              : `<span><span class="who muted">Not yet edited</span><span class="reg">${l.pages} page${l.pages === 1 ? "" : "s"}, machine readings only</span></span>`)
            : `<span><span class="who">${esc(l.sender.join(", ") || "?")} → ${esc(l.recipient.join(", ") || "?")}</span>
          <span class="reg">${esc(l.regest)}</span></span>`}
        </a>`).join("") + (rows.length > 400 ? `<p class="muted small">Showing 400 of ${rows.length} — narrow the search.</p>` : "");
    };
    q.addEventListener("input", draw);
    yr.addEventListener("change", draw);
    ed.addEventListener("change", draw);
    draw();
  }

  function unedited() { return state.index.filter((l) => l.edited === false).length; }

  function machineReaders() {
    const rs = state.readers.filter((r) => r.id !== "edition");
    const names = rs.map((r) => esc(r.label));
    const list = names.length > 1 ? names.slice(0, -1).join(", ") + " and " + names[names.length - 1] : names[0] || "";
    return `the readings of ${list}`;
  }

  /* ---------- readers ---------- */
  function showReaders() {
    setNav("readers");
    destroyOsd();
    const scored = state.readers.filter((r) => r.cer != null);
    const max = Math.max(...scored.map((r) => r.cer), 0.01);
    app.innerHTML = `
      <section class="prose">
        <h1>Readers</h1>
        <p>Each automatic reading is compared with the edition's text of the same page: character error rate
        (CER) = edits needed to turn the reading into the edition ÷ characters of the edition, with whitespace
        collapsed. Corpus CER sums over all scored pages; the median is the typical page.</p>
        <p class="muted small">A distance to <em>this edition's</em> conventions, not a verdict on palaeography. A reader that writes
        <i>ů</i> where the edition writes <i>uo</i>, keeps <i>ſ</i>, or reads the archivist's later docket is counted as wrong.
        Pages without edition text (blank backs, address leaves the edition leaves out) are read but not scored.</p>
      </section>
      <div class="scroll"><table class="scores">
        <thead><tr><th>Reader</th><th>Kind</th><th>CER</th><th>Median page</th><th>Pages read / scored</th><th>About</th></tr></thead>
        <tbody>${state.readers.filter((r) => r.id !== "edition").map((r) => `
          <tr><td><b>${esc(r.label)}</b></td><td>${esc(r.kind)}</td>
          <td class="n">${pct(r.cer)}<div class="bar"><i style="width:${r.cer == null ? 0 : (100 * r.cer / max).toFixed(1)}%"></i></div></td>
          <td class="n">${pct(r.median_page_cer)}</td>
          <td class="n">${r.pages_read ?? 0} / ${r.pages_scored ?? 0}</td>
          <td class="small">${esc(r.about || "")}${r.caveat ? `<div class="caveat">${esc(r.caveat)}</div>` : ""}${r.note ? `<div class="muted">${esc(r.note)}</div>` : ""}</td></tr>`).join("")}
        </tbody></table></div>`;
  }

  function showAbout() {
    setNav("about");
    destroyOsd();
    app.innerHTML = `<section class="prose">
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
        <li><b>Entities</b>: on the edition the editors' tagging; on the joint reading the automatic tagging, learned from the edition's own tagging and linked to its registers.</li>
      </ul>
      <h2>Data and code</h2>
      <p><a href="https://github.com/DHBern/sg-missiven-data" target="_blank" rel="noopener">github.com/DHBern/sg-missiven-data</a> — the readings are in <code>readings/</code>, one JSON line per page and reader.</p>
    </section>`;
  }

  /* ---------- registers and map ---------- */
  /* The edition's registers with every edited letter that names an entry, in the
     text or in the header. Built from the TEI by scripts/build_site.py; the
     automatic tagging of letters not yet edited will add to it later. */
  const FTYPE = { P: "Settlement", A: "Region, state", S: "Building, site", H: "Water", L: "Area", T: "Mountain", R: "Road", V: "Forest", U: "Undersea" };
  const KIND = { place: "Place", person: "Person", org: "Organisation" };
  const ROLE = { sender: "sender", recipient: "recipient", place: "place of writing" };
  const lettersOf = (e) => new Set((e.occ || []).map((o) => o[0])).size;
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
      .bindTooltip(`${esc(e.name)} · ${n} letter${n === 1 ? "" : "s"}`)
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
        <h1>${which === "places" ? "Places" : "Names"}</h1>
        <p>${which === "places"
          ? `The edition's place register: ${all.length} places, ${all.length - noGeo} of them with coordinates (<a href="#/map">map</a>).`
          : `The edition's register of persons and organisations: ${all.filter(([, e]) => e.kind === "person").length} persons, ${all.filter(([, e]) => e.kind === "org").length} organisations.`}
        Each entry lists the edited letters that name it, in the text or as sender, recipient or place of writing.
        Letters not yet edited are not counted here yet: their names come from the automatic tagging, once it has been checked against the register.</p>
      </section>
      <div class="filters">
        <input id="rq" type="search" placeholder="Search names, description, country…" value="${esc(f[which] || "")}" aria-label="Search the register">
        ${which === "places"
          ? `<select id="rt" aria-label="Type"><option value="">All types</option>${Object.entries(FTYPE).filter(([k]) => all.some(([, e]) => e.ftype === k)).map(([k, v]) => `<option value="${k}" ${f.ftype === k ? "selected" : ""}>${v}</option>`).join("")}</select>
             <label class="small"><input type="checkbox" id="rg" ${f.nogeo ? "checked" : ""}> without coordinates (${noGeo})</label>`
          : `<select id="rt" aria-label="Kind"><option value="">Persons and organisations</option><option value="person" ${f.kind === "person" ? "selected" : ""}>Persons</option><option value="org" ${f.kind === "org" ? "selected" : ""}>Organisations</option></select>`}
        <span id="rcount" class="muted small"></span>
      </div>
      <div class="list reg" id="rlist"></div>`;
    const q = document.getElementById("rq"), t = document.getElementById("rt"), g = document.getElementById("rg");
    const draw = () => {
      f[which] = q.value;
      if (which === "places") { f.ftype = t.value; f.nogeo = g.checked; } else f.kind = t.value;
      const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
      const rows = all.filter(([, e]) => {
        if (which === "places" && t.value && e.ftype !== t.value) return false;
        if (which === "places" && g.checked && e.geo) return false;
        if (which === "names" && t.value && e.kind !== t.value) return false;
        if (!words.length) return true;
        const hay = [e.name, e.desc, e.country, ...(e.occ || []).map((o) => o[1])].join(" ").toLowerCase();
        return words.every((w) => hay.includes(w));
      });
      document.getElementById("rcount").textContent = `${rows.length} ${rows.length === 1 ? "entry" : "entries"}`;
      document.getElementById("rlist").innerHTML = rows.slice(0, 500).map(([id, e]) => {
        const n = lettersOf(e);
        return `<a class="row" href="#/entry/${encodeURIComponent(id)}">
          <span class="num">${n || "–"}<span class="muted small"> ${n === 1 ? "letter" : "letters"}</span></span>
          <span><span class="who">${esc(e.name)}${which === "places" && !e.geo ? ` <span class="badge">no coordinates</span>` : ""}</span>
          <span class="reg">${esc([which === "places" ? FTYPE[e.ftype] : KIND[e.kind], e.country, e.desc].filter(Boolean).join(" · "))}</span></span>
        </a>`;
      }).join("") + (rows.length > 500 ? `<p class="muted small">Showing 500 of ${rows.length} — narrow the search.</p>` : "");
    };
    q.addEventListener("input", draw);
    t.addEventListener("change", draw);
    g && g.addEventListener("change", draw);
    draw();
  }

  function showEntry(id) {
    destroyOsd(); destroyMaps();
    const e = state.registers[id];
    if (!e) { app.innerHTML = `<p>No register entry ${esc(id)}.</p>`; return; }
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
        <p class="muted small"><a href="#/${e.kind === "place" ? "places" : "names"}">← ${e.kind === "place" ? "Places" : "Names"}</a></p>
        <h1>${esc(e.name)}</h1>
        <p class="muted">${esc([e.kind === "place" ? FTYPE[e.ftype] || "Place" : KIND[e.kind], e.country, e.desc].filter(Boolean).join(" · "))}</p>
        ${links.length ? `<p class="links">${links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.type)}</a>`).join("")}</p>` : ""}
        <p class="small muted">Register <code>${esc(id)}</code>${e.kind === "place" && e.geo ? ` · ${e.geo[0].toFixed(4)}, ${e.geo[1].toFixed(4)}` : ""} ·
          <a href="https://missiven.stadtarchiv.ch/${e.kind === "place" ? "orte" : "namen"}/${encodeURIComponent(id)}" target="_blank" rel="noopener">in the edition ↗</a></p>
        ${e.kind === "place" ? (e.geo ? `<div id="emap" class="emap"></div>` : `<p class="note-box">The register has no coordinates for this place, so it is not on the map.</p>`) : ""}
        <h2>${rows.length} edited letter${rows.length === 1 ? "" : "s"}</h2>
        ${autoRows.length ? `<p class="muted small">Below: ${autoRows.length} letter${autoRows.length === 1 ? "" : "s"} not yet edited, where the automatic tagging found this ${e.kind === "place" ? "place" : "name"}.</p>` : ""}
        <div class="list">${rows.map((r) => `
          <a class="row" href="#/letter/${r.l.id}/1">
            <span class="num">${esc(nrOf(r.l))}</span>
            <span class="date">${esc(fmtDate(r.l.date))}</span>
            <span><span class="who">${esc(r.l.sender.join(", ") || "?")} → ${esc(r.l.recipient.join(", ") || "?")}</span>
            <span class="reg">${r.forms.size ? `<i>${[...r.forms].map(esc).join("</i>, <i>")}</i>` : ""}${r.roles.size ? `${r.forms.size ? " · " : ""}${[...r.roles].map((x) => ROLE[x] || x).join(", ")}` : ""}</span></span>
          </a>`).join("")}</div>
        ${autoRows.length ? `<h2>${autoRows.length} letter${autoRows.length === 1 ? "" : "s"} not yet edited <span class="badge">automatic</span></h2>
        <p class="muted small">Found by the automatic tagging of the machine reading and linked to this entry by rule; neither is checked.</p>
        <div class="list">${autoRows.map((r) => `
          <a class="row" href="#/letter/${r.l.id}/1">
            <span class="num">${esc(nrOf(r.l))}</span>
            <span class="date">${r.l.year ? esc(r.l.year) + "?" : ""}</span>
            <span><span class="who">${esc(r.l.sender.join(", ") || "Not yet edited")}${r.l.recipient.length ? " → " + esc(r.l.recipient.join(", ")) : ""}</span>
            <span class="reg"><i>${[...r.forms].map(esc).join("</i>, <i>")}</i>${r.roles.size ? " · " + [...r.roles].map((x) => ROLE[x] || x).join(", ") : ""}</span></span>
          </a>`).join("")}</div>` : ""}
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
        <h1>Map</h1>
        <p>${geo.length} of the ${places.length} places in the edition's register, each sized by the number of edited letters that name it.
        Click a place for its letters. <a href="#/places">All places</a>.</p>
      </section>
      <div id="bigmap" class="bigmap" role="region" aria-label="Map of the places named in the letters"></div>
      <section class="prose">
        <h2>Not on the map: ${missing.length} places without coordinates</h2>
        <p class="muted small">The register gives no coordinates for these. Most are regions, rivers or buildings; the list with links is in
        <a href="https://github.com/DHBern/sg-missiven-data/blob/main/docs/places_without_coordinates.md" target="_blank" rel="noopener">docs/places_without_coordinates.md</a>.</p>
        <p class="chips">${missing.map(([id, e]) => `<a href="#/entry/${encodeURIComponent(id)}">${esc(e.name)} <span class="muted">${lettersOf(e)}</span></a>`).join("")}</p>
      </section>`;
    if (!window.L) { document.getElementById("bigmap").innerHTML = `<p class="noimg">The map library did not load.</p>`; return; }
    const m = baseMap("bigmap", { scrollWheelZoom: true });
    geo.sort((a, b) => lettersOf(b[1]) - lettersOf(a[1])).forEach(([id, e]) => marker(id, e, m));
    /* open on the places named in several letters; the far ones are a zoom away */
    const core = geo.filter(([, e]) => lettersOf(e) >= 3);
    m.fitBounds(L.latLngBounds((core.length > 5 ? core : geo).map(([, e]) => e.geo)), { padding: [20, 20] });
  }

  /* ---------- letter ---------- */
  async function getLetter(id) {
    if (!state.letters.has(id)) state.letters.set(id, await json(`data/letters/${id}.json`));
    return state.letters.get(id);
  }

  async function showLetter(id, pageNo, readerId) {
    setNav("letters");
    let letter;
    try { letter = await getLetter(id); } catch { app.innerHTML = `<p>No letter ${esc(id)}. <a href="#/">All letters</a></p>`; return; }
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
            <a href="#/">All letters</a>
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
          <p class="caveat">Not yet edited: the Stadtarchiv has not published this letter. What follows are machine readings only, with no edition to compare them with.</p>
          ${draftBlock(letter.auto_regest, true)}`}
          ${edited && letter.auto_regest ? draftBlock(letter.auto_regest, false) : ""}
          <div class="pages"><span class="lbl">Page</span>${pages.map((p, i) =>
            `<a class="pg ${i + 1 === pageNo ? "on" : ""}" href="#/letter/${id}/${i + 1}" ${i + 1 === pageNo ? 'aria-current="page"' : ""}>${i + 1}</a>`).join("")}</div>
          <div class="readers" role="tablist" aria-label="Readings">${readerButtons(page, reader)}</div>
          <div class="toggles">
            <label><input type="checkbox" id="t-diff" ${state.diff ? "checked" : ""} ${reader === "edition" || !page.text ? "disabled" : ""}><span>Compare with edition</span></label>
            <label><input type="checkbox" id="t-ents" ${state.ents ? "checked" : ""} ${hasEnts(page, reader) ? "" : "disabled"}><span>Entities</span></label>
          </div>
          <div id="reading">${readingHtml(page, reader)}</div>
        </section>
        <section class="pane imgpane">
          <div id="osd" role="img" aria-label="Facsimile of ${esc(page.facs)}"></div>
          <div class="imgbar"><span class="muted">${esc(page.facs)}</span><span id="imglink"></span></div>
        </section>
      </div>
      <footer class="foot">Edition: Stadtarchiv St. Gallen (CC BY-SA 4.0) · Readings: DH Bern</footer>`;

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
    return `<details class="regest draft" ${open ? "open" : ""}><summary>Automatic regest (draft${open ? "" : ", for comparison"})</summary>
      ${who}${when ? `<p class="small muted">${when}${d.year ? ` · year read as ${esc(d.year)} (unchecked)` : ""}</p>` : ""}
      <p>${esc(d.regest)}</p>
      <p class="small muted">Drafted by ${esc(d.model || "a language model")} from the ${esc(readerLabel(d.reader))} reading, without correction. Names and dates may be wrong.</p>
    </details>`;
  }
  function readerLabel(id) { const r = state.readers.find((x) => x.id === id); return r ? r.label : (id || "machine"); }

  function readerButtons(page, current) {
    return state.readers.map((r) => {
      const has = r.id === "edition" ? !!page.html : !!page.readings[r.id];
      const rd = page.readings[r.id];
      const badge = r.id === "edition" ? "" : rd && rd.cer != null ? `<span class="cer" title="character error rate on this page">${pct(rd.cer)}</span>` : rd ? `<span class="cer">not scored</span>` : "";
      return `<button class="rd ${r.id === current ? "on" : ""}" role="tab" aria-selected="${r.id === current}" data-reader="${esc(r.id)}" ${has ? "" : "disabled title=\"not read on this page\""}>
        <span>${esc(r.label)}<span class="k">${esc(r.kind)}</span></span>${badge}</button>`;
    }).join("");
  }

  function hasEnts(page, reader) {
    if (reader === "edition") return /class="ent /.test(page.html || "");
    return !!(page.auto_entities && page.auto_entities[reader] && page.auto_entities[reader].length);
  }

  function legend() {
    return `<div class="legend"><span class="per"><i></i>person</span><span class="org"><i></i>organisation</span><span class="place"><i></i>place</span><span class="date"><i></i>date</span></div>`;
  }

  function readingHtml(page, readerId) {
    const r = state.readers.find((x) => x.id === readerId) || state.readers[0];
    const head = `${r.about ? `<p class="about-reader">${esc(r.about)}</p>` : ""}${r.caveat ? `<p class="caveat">${esc(r.caveat)}</p>` : ""}`;
    const showEnts = state.ents && hasEnts(page, readerId);
    if (readerId === "edition") {
      if (!page.html) return head + `<p class="empty">The edition has no text on this page.</p>`;
      return head + (showEnts ? legend() : "") + `<div class="text edition ${showEnts ? "ents" : ""}">${lineify(page.html)}</div>`;
    }
    const rd = page.readings[readerId];
    if (!rd) return head + `<p class="empty">Not read on this page.</p>`;
    if (!rd.text) return head + `<p class="empty">Empty reading${page.text ? "" : " — the edition has no text here either"}.</p>`;
    const trunc = rd.truncated ? `<p class="caveat">This reading hit the model's length limit and stops early.</p>` : "";
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
    const kindName = { per: "Person", place: "Place", org: "Organisation", date: "Date" }[kind] || "";
    const sub = { origPlace: "place of writing", origDate: "date line" }[el.dataset.el] || "";
    let body = `<b>${esc(kindName)}${sub ? " · " + sub : ""}${el.dataset.auto ? " · automatic" : ""}</b>`;
    if (kind === "date" && ref) body += esc(ref);
    else if (reg) {
      body += `<a href="#/entry/${encodeURIComponent(ref)}">${esc(reg.name)}</a>`;
      if (reg.occ) body += ` <span class="muted">· ${lettersOf(reg)} letters</span>`;
      const links = reg.links.filter((l) => /^https?:/.test(l.url)).slice(0, 4);
      if (links.length) body += `<div>${links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.type)}</a>`).join("")}</div>`;
    } else if (el.dataset.auto) body += `<span class="muted">not linked to the register</span>`;
    else if (ref) body += esc(ref);
    if (el.dataset.role) body += `<div class="muted">role: ${esc(el.dataset.role)}</div>`;
    if (el.dataset.auto && el.dataset.cert) body += `<div class="muted">${kind === "date" ? "year inferred from the neighbouring letters" : "uncertain link (close spelling)"}</div>`;
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
      `<p class="noimg">The image of this page is not shown here: the letter has not yet been published by the Stadtarchiv St. Gallen.</p>`;
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
      setImgLink(`${base}/full/max/0/default.jpg`, edited ? "Full image" : `Full image (shown until ${fmtDate(w.until)})`);
      return;
    }
    if (w) {
      const url = w.base.replace(/\/?$/, "/") + encodeURIComponent(facs.replace(/\.[^.]+$/, "")) + ".jpg";
      osd({ type: "image", url }, false);
      setImgLink(url, `Image (available until ${fmtDate(w.until)})`);
      return;
    }
    notPublic();
  }

  boot().catch((err) => { app.innerHTML = `<p>Could not load the data: ${esc(err.message)}</p>`; });
})();
