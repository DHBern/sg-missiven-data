// Similar letters (page «Ähnlichkeit»): 2D (canvas, d3 zoom) and 3D (three.js points), from
// data/similarity/map_<set>.json and neighbours.json (scripts/similarity.py). After the map of
// thodel/ssrq_print (site_assets/simmap.js).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const PAL = ["#2a78d6", "#eb6834", "#1baf7a", "#c8102e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22",
  "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173", "#3182bd", "#e6550d", "#31a354", "#756bb1", "#636363",
  "#9c9ede", "#cedb9c", "#e7cb94", "#e7969c", "#de9ed6", "#6baed6", "#fd8d3c"];
const NONE = "#c9c9c9";

export function mount(host, { T, sel, neighbours }) {
  const d3 = window.d3;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const S = { set: "regest", view: "2d", color: "group", y0: null, y1: null, q: "", sel: null };
  let D = null, colorOf = null, visible = null, rel = [], idx = {};
  host.innerHTML = `
  <div class="smctl">
    <label>${T("Basis", "Grundlage")} <select data-k="set"><option value="regest">${T("Regest", "Regest")}</option><option value="source">${T("Letter text", "Brieftext")}</option></select></label>
    <label>${T("View", "Ansicht")} <select data-k="view"><option value="2d">2D</option><option value="3d">3D</option><option value="time">${T("3D with time axis", "3D mit Zeitachse")}</option></select></label>
    <label>${T("Colour", "Farbe")} <select data-k="color">
      <option value="group">${T("Group", "Gruppe")}</option><option value="period">${T("Period", "Zeitraum")}</option>
      <option value="direction">${T("Direction", "Richtung")}</option><option value="region">${T("Partner region", "Region des Partners")}</option>
      <option value="edited">${T("Regest: edition or draft", "Regest: Edition oder Entwurf")}</option></select></label>
    <label>${T("Years", "Jahre")} <input type="number" data-k="y0" min="1400" max="1700" step="10" placeholder="${T("from", "von")}"> – <input type="number" data-k="y1" min="1400" max="1700" step="10" placeholder="${T("to", "bis")}"></label>
    <label>${T("Search", "Suche")} <input type="search" data-k="q" placeholder="${T("regest, name, place, no.", "Regest, Name, Ort, Nr.")}"></label>
    <span class="smcount muted small"></span>
  </div>
  <div class="smstage"><canvas class="sm2d"></canvas><div class="sm3d" hidden></div><div class="smtip" hidden></div></div>
  <div class="smlegend"></div><div class="sminfo"></div>`;
  const stage = host.querySelector(".smstage"), cv = host.querySelector(".sm2d"), box3 = host.querySelector(".sm3d");
  const tip = host.querySelector(".smtip"), info = host.querySelector(".sminfo");
  const F = (r, k) => r[D.fields.indexOf(k)];

  const period = (y) => (y ? `${Math.floor(y / 50) * 50}–${Math.floor(y / 50) * 50 + 49}` : T("no year", "ohne Jahr"));
  const keyOf = {
    group: (i) => (D.group[i] < 0 ? T("no group", "ohne Gruppe") : `${D.group[i] + 1}: ${D.groups[D.group[i]] || ""}`),
    period: (i) => period(F(D.rows[i], "year")),
    direction: (i) => ({ "an St. Gallen": T("to St. Gallen", "an St. Gallen"), "von St. Gallen": T("from St. Gallen", "von St. Gallen") }[F(D.rows[i], "direction")] || T("other", "andere")),
    region: (i) => F(D.rows[i], "region") || T("no place", "ohne Ort"),
    edited: (i) => (F(D.rows[i], "edited") ? T("edition", "Edition") : T("draft (machine)", "Entwurf (maschinell)")),
  };
  const isNone = (k) => /^(no |ohne |other$|andere$)/.test(k);
  function recolor() {
    const counts = d3.rollup(d3.range(D.rows.length), (v) => v.length, keyOf[S.color]);
    let keys = [...counts.keys()];
    keys = S.color === "period" ? keys.sort((a, b) => (parseInt(a) || 9999) - (parseInt(b) || 9999))
      : S.color === "group" ? keys.sort((a, b) => (parseInt(a) || 9999) - (parseInt(b) || 9999))
      : keys.sort((a, b) => counts.get(b) - counts.get(a));
    let n = 0;
    const map = new Map(keys.map((k) => [k, isNone(k) ? NONE : PAL[n++ % PAL.length]]));
    colorOf = (i) => map.get(keyOf[S.color](i));
    host.querySelector(".smlegend").innerHTML = keys.slice(0, 60).map((k) =>
      `<span><i style="background:${map.get(k)}"></i>${esc(k)} <small>${counts.get(k)}</small></span>`).join("");
  }
  function filter() {
    const q = S.q.trim().toLowerCase();
    visible = new Uint8Array(D.rows.length);
    let n = 0;
    D.rows.forEach((r, i) => {
      const y = F(r, "year");
      const ok = (S.y0 == null || (y && y >= S.y0)) && (S.y1 == null || (y && y <= S.y1)) &&
        (!q || `${F(r, "regest")} ${F(r, "who")} ${F(r, "partner")} ${F(r, "no")}`.toLowerCase().includes(q));
      visible[i] = ok ? 1 : 0; n += ok;
    });
    host.querySelector(".smcount").textContent = `${n.toLocaleString("de-CH")} ${T("of", "von")} ${D.rows.length.toLocaleString("de-CH")} ${T("letters", "Briefen")}`;
  }
  const label = (i) => { const r = D.rows[i]; return `${F(r, "no")}${F(r, "year") ? " · " + F(r, "year") : ""}`; };
  const tipHtml = (i) => { const r = D.rows[i]; return `<b>${esc(label(i))}</b> ${esc(F(r, "who"))}<br>${esc(F(r, "regest"))}${F(r, "regest").length >= 160 ? " …" : ""}`; };
  function select(i) {
    S.sel = i; rel = [];
    if (i == null) { info.innerHTML = ""; history.replaceState(null, "", "#/similarity"); draw(); return; }
    const r = D.rows[i], id = F(r, "id");
    history.replaceState(null, "", "#/similarity?sel=" + encodeURIComponent(id));
    const nb = (neighbours[id] || []);
    rel = nb.map((x) => idx[x[0]]).filter((k) => k != null);
    info.innerHTML = `<p><b>${esc(label(i))}</b> ${esc(F(r, "who"))} <a href="#/letter/${encodeURIComponent(id)}">${T("open letter", "Brief öffnen")}</a></p>
      <p class="small">${esc(F(r, "regest"))}${F(r, "regest").length >= 160 ? " …" : ""}</p>
      ${nb.length ? `<p class="muted small">${T("Most similar letters (lines on the map):", "Ähnlichste Briefe (Linien auf der Karte):")}</p>
      <ol class="smnb">${nb.map(([y, a, b]) => { const k = idx[y]; const rr = k != null ? D.rows[k] : null;
        return `<li><a href="#/letter/${encodeURIComponent(y)}">${esc(rr ? label(k) : "Nr. " + (+(/(\d+)$/.exec(y) || [0, y])[1]))}</a> ${rr ? esc(F(rr, "who")) : ""}
          <span class="muted small">${a != null ? `${T("regest", "Regest")} ${a.toFixed(2)}` : ""}${a != null && b != null ? " · " : ""}${b != null ? `${T("text", "Text")} ${b.toFixed(2)}` : ""}</span></li>`; }).join("")}</ol>` : ""}`;
    draw();
  }

  // ------------------------------------------------------------ 2D
  let Tz = d3.zoomIdentity, W = 0, H = 0, qt = null;
  function size() {
    W = stage.clientWidth; H = Math.max(380, Math.min(720, Math.round(W * 0.7)));
    const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
    cv.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    stage.style.height = H + "px";
  }
  const px = (i) => [Tz.applyX(20 + D.xy[i][0] * (W - 40)), Tz.applyY(20 + (1 - D.xy[i][1]) * (H - 40))];
  const ink = () => getComputedStyle(host).color || "#111";
  function draw2d() {
    const g = cv.getContext("2d");
    g.clearRect(0, 0, W, H);
    const r = Math.max(1.6, Math.min(5, 2 * Math.sqrt(Tz.k)));
    g.globalAlpha = 0.15;
    for (let i = 0; i < D.rows.length; i++) if (!visible[i]) { const [x, y] = px(i); g.fillStyle = "#999"; g.fillRect(x - 0.8, y - 0.8, 1.6, 1.6); }
    g.globalAlpha = 0.85;
    for (let i = 0; i < D.rows.length; i++) if (visible[i]) { const [x, y] = px(i); g.fillStyle = colorOf(i); g.beginPath(); g.arc(x, y, r, 0, 6.2832); g.fill(); }
    if (S.sel != null) {
      const [sx, sy] = px(S.sel);
      g.globalAlpha = 0.9; g.strokeStyle = ink(); g.lineWidth = 1;
      for (const k of rel) { const [x, y] = px(k); g.beginPath(); g.moveTo(sx, sy); g.lineTo(x, y); g.stroke(); g.beginPath(); g.arc(x, y, r + 2, 0, 6.2832); g.stroke(); }
      g.lineWidth = 2.5; g.beginPath(); g.arc(sx, sy, r + 4, 0, 6.2832); g.stroke();
    }
    g.globalAlpha = 1;
    qt = d3.quadtree().x((i) => px(i)[0]).y((i) => px(i)[1]).addAll(d3.range(D.rows.length).filter((i) => visible[i]));
  }
  const zoom = d3.zoom().scaleExtent([0.5, 60]).on("zoom", (ev) => { Tz = ev.transform; draw2d(); });
  d3.select(cv).call(zoom);
  function fit() {
    const q = (a, f) => a[Math.floor((a.length - 1) * f)];
    const pts = D.xy.filter((p, i) => visible[i]);
    const use = pts.length ? pts : D.xy;
    const xs = use.map((p) => 20 + p[0] * (W - 40)).sort((a, b) => a - b), ys = use.map((p) => 20 + (1 - p[1]) * (H - 40)).sort((a, b) => a - b);
    const x0 = q(xs, 0.005), x1 = q(xs, 0.995), y0 = q(ys, 0.005), y1 = q(ys, 0.995);
    const k = Math.min(use.length === D.xy.length ? 8 : 40, 0.9 * Math.min(W / (x1 - x0 || 1), H / (y1 - y0 || 1)));
    Tz = d3.zoomIdentity.translate(W / 2 - k * (x0 + x1) / 2, H / 2 - k * (y0 + y1) / 2).scale(k);
    d3.select(cv).call(zoom.transform, Tz);
  }
  const showTip = (ev, i) => { tip.hidden = false; tip.style.left = (ev.offsetX + 12) + "px"; tip.style.top = (ev.offsetY + 12) + "px"; tip.innerHTML = tipHtml(i); };
  cv.addEventListener("mousemove", (ev) => { const i = qt && qt.find(ev.offsetX, ev.offsetY, 12); if (i == null) tip.hidden = true; else showTip(ev, i); });
  cv.addEventListener("mouseleave", () => { tip.hidden = true; });
  cv.addEventListener("click", (ev) => { const i = qt && qt.find(ev.offsetX, ev.offsetY, 12); select(i ?? null); });

  // ------------------------------------------------------------ 3D
  let R3 = null;
  function stop3d() { if (R3) { R3.renderer.dispose(); R3 = null; box3.innerHTML = ""; } }
  function build3d() {
    stop3d();
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1); renderer.setSize(W, H);
    renderer.setClearColor(new THREE.Color(getComputedStyle(document.body).backgroundColor || "#fff"));
    box3.appendChild(renderer.domElement);
    const scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(50, W / H, 0.01, 100);
    cam.position.set(1.05, 0.8, 1.05);
    const ctl = new OrbitControls(cam, renderer.domElement); ctl.target.set(0, 0, 0);
    const n = D.rows.length, pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
    const yrs = D.rows.map((r) => F(r, "year")).filter(Boolean), ymin = d3.min(yrs), ymax = d3.max(yrs);
    for (let i = 0; i < n; i++) {
      const y = F(D.rows[i], "year");
      const p = S.view === "time" ? [D.xy[i][0], y ? (y - ymin) / (ymax - ymin) : -0.08, D.xy[i][1]] : D.xyz[i];
      pos.set([p[0] - 0.5, p[1] - 0.5, p[2] - 0.5], i * 3);
      const c = new THREE.Color(visible[i] ? colorOf(i) : "#8a8a8a");
      col.set([c.r, c.g, c.b], i * 3);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3)); geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    const pts = new THREE.Points(geo, new THREE.PointsMaterial({ size: 0.014, vertexColors: true }));
    scene.add(pts);
    if (S.sel != null && rel.length) {
      const P = (i) => new THREE.Vector3(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
      const lines = new THREE.BufferGeometry().setFromPoints(rel.flatMap((k) => [P(S.sel), P(k)]));
      scene.add(new THREE.LineSegments(lines, new THREE.LineBasicMaterial({ color: new THREE.Color(ink()) })));
    }
    const ray = new THREE.Raycaster(); ray.params.Points.threshold = 0.012;
    const mouse = new THREE.Vector2();
    renderer.domElement.addEventListener("mousemove", (ev) => {
      mouse.set(ev.offsetX / W * 2 - 1, -ev.offsetY / H * 2 + 1); ray.setFromCamera(mouse, cam);
      const hit = ray.intersectObject(pts).find((h) => visible[h.index]);
      if (!hit) { tip.hidden = true; R3.hover = null; return; }
      showTip(ev, hit.index); R3.hover = hit.index;
    });
    renderer.domElement.addEventListener("mouseleave", () => { tip.hidden = true; });
    renderer.domElement.addEventListener("dblclick", () => { if (R3 && R3.hover != null) select(R3.hover); });
    R3 = { renderer, hover: null };
    (function loop() { if (!R3 || R3.renderer !== renderer || !host.isConnected) { if (R3 && !host.isConnected) stop3d(); return; } ctl.update(); renderer.render(scene, cam); requestAnimationFrame(loop); })();
  }

  function draw() {
    const two = S.view === "2d";
    cv.hidden = !two; box3.hidden = two;
    if (two) { stop3d(); draw2d(); } else build3d();
  }
  async function load() {
    info.innerHTML = `<p class="muted">${T("Loading the map …", "Karte wird geladen …")}</p>`;
    D = await fetch(`data/similarity/map_${S.set}.json`).then((r) => r.json());
    idx = {}; D.rows.forEach((r, i) => { idx[F(r, "id")] = i; });
    info.innerHTML = "";
    recolor(); filter(); size(); fit(); lastW = stage.clientWidth;
    const k = sel != null ? idx[sel] : null;
    if (k != null) select(k); else draw();
  }
  host.querySelectorAll(".smctl [data-k]").forEach((el) => el.addEventListener(el.tagName === "INPUT" ? "input" : "change", () => {
    const k = el.dataset.k;
    S[k] = k === "y0" || k === "y1" ? (el.value ? +el.value : null) : el.value;
    if (k === "set") { sel = S.sel != null ? F(D.rows[S.sel], "id") : sel; S.sel = null; rel = []; load(); return; }
    if (k === "color") recolor();
    if (k === "q" || k === "y0" || k === "y1") filter();
    draw();
  }));
  // size from the stage's own width: it can be 0 while the page is still being laid out or hidden
  let lastW = -1;
  const ro = new ResizeObserver(() => {
    if (!host.isConnected) { ro.disconnect(); return; }
    if (!D || stage.clientWidth === lastW) return;
    const first = lastW <= 0;
    lastW = stage.clientWidth; size(); if (first) fit(); draw();
  });
  ro.observe(stage);
  load();
  return { stop: () => { ro.disconnect(); stop3d(); } };
}
