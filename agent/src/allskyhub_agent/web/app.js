// The camera's local UI (SPEC §7, roadmap L2–L7): one page, the menu switches views by
// the URL fragment. Everything comes from the camera itself, so it works without the hub.
"use strict";

const $ = (id) => document.getElementById(id);
const page = $("page");
// The page itself is German for now; schema texts follow it so nothing is mixed (SPEC §6.9).
const LANG = document.documentElement.lang === "en" ? "en" : "de";
const text = (t) => (t == null ? "" : typeof t === "string" ? t : (LANG === "en" && t.en) || t.de);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KINDS = { meteor: "Meteor", lightning: "Blitz", aurora: "Polarlicht", nlc: "Leuchtende Nachtwolken",
  satellite: "Satellit", clouds: "Wolken", sky_quality: "Himmelsqualität" };
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit" });
const fmtDateTime = (iso) => new Date(iso).toLocaleString("de-AT",
  { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const fmtNight = (id) => `${id.slice(6, 8)}.${id.slice(4, 6)}.${id.slice(0, 4)}`;
const fmtExposure = (us) => (us >= 1e6 ? (us / 1e6).toFixed(1) + " s" : (us / 1000).toFixed(2) + " ms");
const fileUrl = (night, name, thumb) => `files/${night}/${encodeURIComponent(name)}${thumb ? "?thumb=1" : ""}`;

let timers = [];
const every = (fn, ms) => { fn(); timers.push(setInterval(fn, ms)); };
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (r.status === 401) { location.href = "local-login"; throw new Error("login"); }
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

// --- lightbox ----------------------------------------------------------------------
let lbItems = [];
let lbIndex = 0;
function lightbox(items, index) {
  lbItems = items;
  lbIndex = index;
  showLightbox();
  $("lightbox").showModal();
}
function showLightbox() {
  const it = lbItems[lbIndex];
  $("lightboxImg").src = it.src;
  $("lbDownload").href = it.src;
  $("lbCaption").textContent = it.caption || "";
  $("lbPrev").disabled = lbIndex === 0;
  $("lbNext").disabled = lbIndex === lbItems.length - 1;
}
$("lbPrev").onclick = () => { if (lbIndex > 0) { lbIndex--; showLightbox(); } };
$("lbNext").onclick = () => { if (lbIndex < lbItems.length - 1) { lbIndex++; showLightbox(); } };
$("lbClose").onclick = () => $("lightbox").close();
document.addEventListener("keydown", (e) => {
  if (!$("lightbox").open) return;
  if (e.key === "ArrowLeft") $("lbPrev").click();
  if (e.key === "ArrowRight") $("lbNext").click();
});

// --- Live (L3) ---------------------------------------------------------------------
function viewLive() {
  page.innerHTML = `
    <section class="card stack">
      <img class="live-img" id="live" alt="Aktuelles Bild">
      <dl class="facts" id="facts"><dt>Bild</dt><dd>Warte auf das erste Bild …</dd></dl>
    </section>
    <section class="card stack">
      <h2>Die letzte Stunde</h2>
      <img class="live-img" id="loopImg" alt="Loop der letzten Stunde">
      <div class="loop-controls">
        <button type="button" class="secondary small" id="loopPlay">Abspielen</button>
        <input type="range" id="loopPos" min="0" max="0" value="0" aria-label="Position">
        <span class="muted small" id="loopTime"></span>
      </div>
    </section>
    <section class="card stack">
      <h2>Fokus</h2>
      <div class="row">
        <button id="focusBtn">Fokusmodus starten</button>
        <button class="secondary" id="resetBtn" hidden>Bestwert zurücksetzen</button>
      </div>
      <div id="focusPanel" hidden>
        <div class="row"><span class="score" id="score">–</span><div class="bar"><div id="bar"></div></div></div>
        <p class="muted">Bestwert <b id="best">–</b>. Den Fokusring in kleinen Schritten drehen: höher ist
          schärfer. Sinkt die Zahl, bist du zu weit, also zurück. Dann die Feststellschraube anziehen
          und die Zahl noch einmal prüfen.</p>
        <img class="live-img" id="crop" alt="Bildmitte in voller Auflösung">
      </div>
    </section>`;
  let lastFrames = -1;
  let focus = false;
  $("live").onclick = () => lightbox([{ src: $("live").src, caption: "Aktuelles Bild" }], 0);
  const render = (s) => {
    $("version").textContent = s.version;
    focus = s.focus_mode;
    $("focusBtn").textContent = focus ? "Fokusmodus beenden" : "Fokusmodus starten";
    $("focusPanel").hidden = !focus;
    $("resetBtn").hidden = !focus;
    if (s.frame) {
      const f = s.frame;
      $("facts").innerHTML = `
        <dt>Aufgenommen</dt><dd>${fmtTime(f.captured_at)}</dd>
        <dt>Modus</dt><dd>${f.mode === "night" ? "Nacht" : "Tag"}</dd>
        <dt>Belichtung</dt><dd>${fmtExposure(f.exposure_us)}</dd>
        <dt>Gain</dt><dd>${f.gain}</dd>
        <dt>Helligkeit</dt><dd>${f.mean.toFixed(2)}</dd>
        <dt>Sonne</dt><dd>${f.sun_elevation.toFixed(1)}°</dd>
        ${f.sensor_temp_c != null ? `<dt>Sensor</dt><dd>${f.sensor_temp_c.toFixed(1)} °C</dd>` : ""}`;
    }
    if (focus && s.sharpness != null) {
      $("score").textContent = Math.round(s.sharpness);
      $("best").textContent = s.sharpness_max != null ? Math.round(s.sharpness_max) : "–";
      $("bar").style.width = (s.sharpness_max ? Math.min(100, 100 * s.sharpness / s.sharpness_max) : 0) + "%";
    }
    if (s.frames !== lastFrames) {
      lastFrames = s.frames;
      $("live").src = "api/live.jpg?n=" + s.frames;
      if (focus) $("crop").src = "api/crop.jpg?n=" + s.frames;
    }
  };
  const post = async (action) => {
    const r = await fetch("api/focus", { method: "POST", body: action });
    if (r.ok) render(await r.json());
  };
  $("focusBtn").onclick = () => post(focus ? "off" : "on");
  $("resetBtn").onclick = () => post("reset");
  every(() => getJSON("api/status").then(render).catch(() => {}), 2000);

  // The loop: thumbnails of the last hour, refreshed every minute.
  let frames = [];
  let playing = null;
  const show = (i) => {
    const f = frames[i];
    if (!f) return;
    $("loopImg").src = fileUrl(f.night_id, f.name, true);
    $("loopTime").textContent = fmtTime(f.captured_at);
    $("loopPos").value = i;
  };
  $("loopPos").oninput = (e) => show(Number(e.target.value));
  $("loopPlay").onclick = () => {
    if (playing) { clearInterval(playing); playing = null; $("loopPlay").textContent = "Abspielen"; return; }
    $("loopPlay").textContent = "Anhalten";
    playing = setInterval(() => show((Number($("loopPos").value) + 1) % Math.max(1, frames.length)), 250);
    timers.push(playing);
  };
  every(() => getJSON("api/loop").then((l) => {
    const atEnd = Number($("loopPos").value) >= frames.length - 1;
    frames = l;
    $("loopPos").max = Math.max(0, frames.length - 1);
    if (atEnd && !playing) show(frames.length - 1);
  }).catch(() => {}), 60000);
}

// --- Archive (L4) --------------------------------------------------------------------
async function viewArchive(night) {
  if (night) return viewNight(night);
  page.innerHTML = `<h1>Archiv</h1><div class="nights" id="nights"><p class="muted">Lade …</p></div>`;
  const nights = await getJSON("api/nights");
  if (!nights.length) { $("nights").innerHTML = `<p class="empty">Noch keine Nächte gespeichert.</p>`; return; }
  $("nights").innerHTML = nights.map((n) => `
    <a class="night" href="#archiv/${n.night_id}">
      <div class="card stack">
        ${n.cover ? `<img class="night-cover" loading="lazy" alt="" src="${fileUrl(n.night_id, n.cover, true)}">` : ""}
        <h2>${fmtNight(n.night_id)}</h2>
        <p class="muted small">${n.frames} Bilder${n.first ? ` · ${fmtTime(n.first)}–${fmtTime(n.last)}` : ""}</p>
        <div class="badges">
          ${n.products.map((p) => `<span class="badge ok">${esc({ keogram: "Keogramm", startrails: "Sternspuren", timelapse: "Zeitraffer" }[p.kind] || p.kind)}</span>`).join("")}
          ${n.events ? `<span class="badge">${n.events} Ereignisse</span>` : ""}
        </div>
      </div>
    </a>`).join("");
}

async function viewNight(night) {
  page.innerHTML = `<p><a class="back" href="#archiv">‹ Alle Nächte</a></p><h1>Nacht vom ${fmtNight(night)}</h1>
    <div class="products" id="products"></div>
    <section class="card stack"><h2>Bilder</h2><div class="thumbs" id="thumbs"></div>
      <div class="actions"><button type="button" class="secondary" id="more" hidden>Mehr laden</button></div></section>`;
  const nights = await getJSON("api/nights");
  const n = nights.find((x) => x.night_id === night);
  const names = { keogram: "Keogramm", startrails: "Sternspuren", timelapse: "Zeitraffer" };
  $("products").innerHTML = (n ? n.products : []).map((p) => {
    const src = `files/${night}/products/${p.name}`;
    const media = p.kind === "timelapse"
      ? `<video controls preload="metadata" src="${src}" poster="${src}?thumb=1"></video>`
      : `<a href="${src}" target="_blank" rel="noopener"><img loading="lazy" src="${src}?thumb=1" alt=""></a>`;
    return `<section class="card stack product"><h2>${names[p.kind] || esc(p.kind)}</h2>${media}
      <a class="button secondary small" href="${src}" download>Herunterladen</a></section>`;
  }).join("");
  let offset = 0;
  const all = [];
  const load = async () => {
    const data = await getJSON(`api/nights/${night}/frames?offset=${offset}&limit=200`);
    const start = all.length;
    all.push(...data.frames);
    offset += data.frames.length;
    const box = $("thumbs");
    data.frames.forEach((f, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.innerHTML = `<img loading="lazy" alt="${fmtTime(f.captured_at)}" src="${fileUrl(night, f.name, true)}">`;
      b.onclick = () => lightbox(all.map((x) => ({ src: fileUrl(night, x.name, false),
        caption: `${fmtDateTime(x.captured_at)} · ${fmtExposure(x.exposure_us)} · Gain ${x.gain}` })), start + i);
      box.appendChild(b);
    });
    $("more").hidden = offset >= data.total;
  };
  $("more").onclick = load;
  await load();
}

// --- night picker shared by events and sky ----------------------------------------------
async function nightPicker(current, route) {
  const nights = await getJSON("api/nights");
  const night = current || (nights[0] && nights[0].night_id);
  const options = nights.map((n) => `<option value="${n.night_id}"${n.night_id === night ? " selected" : ""}>${fmtNight(n.night_id)}</option>`).join("");
  return { night, html: `<label>Nacht <select class="night-select" id="nightSel">${options}</select></label>`,
    bind: () => { const s = $("nightSel"); if (s) s.onchange = () => { location.hash = `${route}/${s.value}`; }; } };
}

// --- Events (L5) ---------------------------------------------------------------------
function eventFacts(e) {
  const d = e.data || {};
  const out = [];
  if (d.azimuth_deg != null) out.push(`Richtung ${Math.round(d.azimuth_deg)}°, Höhe ${Math.round(d.altitude_deg)}°`);
  if (e.kind === "meteor" && d.shower) out.push(esc(d.shower));
  if (e.kind === "meteor" && d.length_px) out.push(`${d.length_px} px`);
  if (e.kind === "lightning" && d.storm_flashes) out.push(`${d.storm_flashes} Blitze in 30 min`);
  if ((e.kind === "aurora" || e.kind === "nlc") && d.peak_index != null) out.push(`Index ${d.peak_index}`);
  out.push(`Sicherheit ${Math.round(e.confidence * 100)} %`);
  return out.join(" · ");
}

async function viewEvents(night) {
  const picker = await nightPicker(night, "#ereignisse");
  page.innerHTML = `<h1>Ereignisse</h1><div class="card stack">${picker.html}<ul class="events" id="events"><li class="muted">Lade …</li></ul></div>`;
  picker.bind();
  if (!picker.night) { $("events").innerHTML = `<li class="empty">Noch nichts erkannt.</li>`; return; }
  const events = await getJSON(`api/nights/${picker.night}/events`);
  if (!events.length) { $("events").innerHTML = `<li class="empty">In dieser Nacht nichts erkannt.</li>`; return; }
  const pictures = events.filter((e) => e.has_image);
  $("events").innerHTML = events.map((e) => `
    <li class="event">
      ${e.has_image ? `<img loading="lazy" alt="" data-i="${pictures.indexOf(e)}" src="files/${e.night_id}/events/${e.id}.jpg?thumb=1">` : "<span></span>"}
      <div><b>${KINDS[e.kind] || esc(e.kind)}</b> <span class="muted">${fmtTime(e.start)}</span>
        <p class="muted small">${eventFacts(e)}</p></div>
    </li>`).join("");
  const items = pictures.map((e) => ({ src: `files/${e.night_id}/events/${e.id}.jpg`,
    caption: `${KINDS[e.kind] || e.kind} · ${fmtDateTime(e.start)}` }));
  for (const img of $("events").querySelectorAll("img[data-i]")) {
    img.onclick = () => lightbox(items, Number(img.dataset.i));
  }
}

// --- Sky charts (L6) -------------------------------------------------------------------
function chart(points, opts) {
  // points: [{t: ms, v: number}], one or two series as opts.series
  const W = 640, H = 200, L = 44, R = 12, T = 12, B = 26;
  const all = opts.series.flatMap((s) => s.points);
  if (!all.length) return `<p class="empty">Keine Messwerte in dieser Nacht.</p>`;
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t)) || t0 + 1;
  const lo = opts.min ?? Math.min(...all.map((p) => p.v)), hi = opts.max ?? Math.max(...all.map((p) => p.v));
  const span = hi - lo || 1;
  const x = (t) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - R);
  const y = (v) => {
    const f = (v - lo) / span;
    return T + (opts.invert ? f : 1 - f) * (H - T - B);
  };
  const lines = opts.series.map((s, i) => {
    const d = s.points.map((p, j) => `${j ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    return `<path class="line${i ? " alt" : ""}" d="${d}"/>`;
  }).join("");
  const ticks = [lo, lo + span / 2, hi].map((v) => `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${opts.fmt(v)}</text>`).join("");
  const hours = [];
  for (let t = Math.ceil(t0 / 3.6e6) * 3.6e6; t <= t1; t += 3.6e6 * Math.max(1, Math.round((t1 - t0) / 3.6e6 / 8))) {
    hours.push(`<text x="${x(t)}" y="${H - 8}" text-anchor="middle">${new Date(t).getHours()}h</text>`);
  }
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label)}">
    <line class="axis" x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}"/>
    <line class="axis" x1="${L}" y1="${T}" x2="${L}" y2="${H - B}"/>${ticks}${hours.join("")}${lines}</svg>`;
}

async function viewSky(night) {
  const picker = await nightPicker(night, "#himmel");
  page.innerHTML = `<h1>Himmel</h1><div class="card stack">${picker.html}</div><div class="stack" id="charts"></div>`;
  picker.bind();
  if (!picker.night) { $("charts").innerHTML = `<p class="empty">Noch keine Messwerte.</p>`; return; }
  const [sky, frames] = await Promise.all([
    getJSON(`api/nights/${picker.night}/sky`),
    getJSON(`api/nights/${picker.night}/frames?limit=2000`),
  ]);
  const pts = (rows, key, scale = 1, at = "at") => rows.filter((r) => r[key] != null)
    .map((r) => ({ t: Date.parse(r[at]), v: r[key] * scale }));
  const nightFrames = frames.frames.filter((f) => f.mode === "night");
  $("charts").innerHTML = `
    <section class="card stack"><h2>Bewölkung</h2>
      ${chart(null, { series: [{ points: pts(sky, "cloud_cover", 100) }], min: 0, max: 100, fmt: (v) => Math.round(v) + " %", label: "Bewölkung" })}</section>
    <section class="card stack"><h2>Himmelshelligkeit</h2>
      <p class="muted small">mag/″², höher ist dunkler. Nur bei astronomischer Nacht gemessen.</p>
      ${chart(null, { series: [{ points: pts(sky, "sqm_mag") }], fmt: (v) => v.toFixed(2), label: "Himmelshelligkeit" })}</section>
    <section class="card stack"><h2>Sterne</h2>
      ${chart(null, { series: [{ points: pts(sky, "stars") }], min: 0, fmt: (v) => Math.round(v), label: "Sterne" })}</section>
    <section class="card stack"><h2>Belichtung und Gain</h2>
      <p class="muted small">Blau: Belichtung in Sekunden, grün: Gain.</p>
      ${chart(null, { series: [{ points: pts(nightFrames, "exposure_us", 1e-6, "captured_at") }], min: 0, fmt: (v) => v.toFixed(0) + " s", label: "Belichtung" })}
      ${chart(null, { series: [{ points: [] }, { points: pts(nightFrames, "gain", 1, "captured_at") }], min: 0, fmt: (v) => Math.round(v), label: "Gain" })}</section>
    <section class="card stack"><h2>Sensortemperatur</h2>
      ${chart(null, { series: [{ points: pts(frames.frames, "sensor_temp_c", 1, "captured_at") }], fmt: (v) => v.toFixed(0) + " °C", label: "Sensortemperatur" })}</section>`;
}

// --- Settings (L7, SPEC §6.9) -------------------------------------------------------------
let schema = [];
let original = {};

function input(spec, value) {
  const el = document.createElement(spec.type === "choice" ? "select" : "input");
  el.name = spec.key;
  if (spec.type === "choice") {
    for (const c of spec.choices) {
      const o = document.createElement("option");
      o.value = c.value;
      o.textContent = text(c.label);
      el.appendChild(o);
    }
    el.value = value ?? "";
  } else if (spec.type === "bool") {
    el.type = "checkbox";
    el.checked = !!value;
  } else if (spec.type === "number" || spec.type === "int") {
    el.type = "number";
    el.inputMode = spec.type === "int" ? "numeric" : "decimal";
    for (const a of ["min", "max", "step"]) if (spec[a] != null) el[a] = spec[a];
    el.value = value ?? "";
    if (spec.nullable) el.placeholder = "leer";
  } else {
    el.type = spec.write_only ? "password" : "text";
    if (spec.max_length) el.maxLength = spec.max_length;
    el.value = spec.write_only ? "" : value ?? "";
    if (spec.write_only) el.placeholder = original[spec.key + "_set"] ? "gesetzt" : "nicht gesetzt";
  }
  return el;
}

function field(spec) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  wrap.dataset.key = spec.key;
  const label = document.createElement("label");
  label.textContent = text(spec.label) + (spec.unit ? ` (${spec.unit})` : "");
  wrap.appendChild(label);
  if (spec.type === "location") {
    const pair = document.createElement("div");
    pair.className = "pair";
    for (const [k, ph] of [["latitude", "Breite"], ["longitude", "Länge"]]) {
      const el = input({ key: k, type: "number", step: 0.0001, nullable: true }, original[k]);
      el.placeholder = ph;
      el.setAttribute("aria-label", ph);
      pair.appendChild(el);
    }
    wrap.appendChild(pair);
    if (navigator.geolocation) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "secondary small";
      b.textContent = "Standort dieses Geräts";
      b.onclick = () => navigator.geolocation.getCurrentPosition((p) => {
        pair.children[0].value = p.coords.latitude.toFixed(4);
        pair.children[1].value = p.coords.longitude.toFixed(4);
      });
      wrap.appendChild(b);
    }
  } else {
    const el = input(spec, original[spec.key]);
    el.id = "s_" + spec.key;
    label.htmlFor = el.id;
    wrap.appendChild(el);
  }
  if (spec.help) {
    const h = document.createElement("p");
    h.className = "muted small";
    h.textContent = text(spec.help);
    wrap.appendChild(h);
  }
  return wrap;
}

function changes() {
  const form = $("settingsForm");
  const out = {};
  for (const spec of schema) {
    if (spec.type === "location") {
      const lat = form.elements.latitude.value, lon = form.elements.longitude.value;
      const a = lat === "" ? null : parseFloat(lat), b = lon === "" ? null : parseFloat(lon);
      if (a !== original.latitude || b !== original.longitude) { out.latitude = a; out.longitude = b; }
      continue;
    }
    const el = form.elements[spec.key];
    let v;
    if (spec.type === "bool") v = el.checked;
    else if (spec.type === "number" || spec.type === "int") {
      v = el.value === "" ? (spec.nullable ? null : undefined) : spec.type === "int" ? parseInt(el.value, 10) : parseFloat(el.value);
    } else if (spec.write_only) v = el.value === "" ? undefined : el.value;  // empty keeps the secret
    else v = el.value;
    if (v !== undefined && v !== original[spec.key]) out[spec.key] = v;
  }
  return out;
}

function message(t, good) {
  const m = $("settingsMsg");
  m.textContent = t;
  m.className = "msg " + (good ? "ok" : "error");
  m.hidden = false;
}

async function viewSettings() {
  page.innerHTML = `<h1>Einstellungen</h1><div class="card stack"><form class="form" id="settingsForm" novalidate></form>
    <div class="actions"><button type="button" id="saveBtn">Speichern</button></div>
    <p class="msg" id="settingsMsg" hidden></p></div>`;
  let data;
  try { data = await getJSON("api/settings"); } catch {
    $("settingsForm").innerHTML = `<p class="empty">Diese Kamera speichert keine Einstellungen.</p>`;
    $("saveBtn").hidden = true;
    return;
  }
  schema = data.schema;
  original = data.values;
  const form = $("settingsForm");
  const groups = new Map();
  for (const spec of schema) {
    const g = text(spec.group);
    if (!groups.has(g)) {
      const fs = document.createElement("fieldset");
      const lg = document.createElement("legend");
      lg.textContent = g;
      fs.appendChild(lg);
      groups.set(g, fs);
      form.appendChild(fs);
    }
    groups.get(g).appendChild(field(spec));
  }
  $("saveBtn").onclick = async () => {
    for (const el of document.querySelectorAll(".field.bad")) el.classList.remove("bad");
    const body = changes();
    if (!Object.keys(body).length) { message("Nichts geändert.", true); return; }
    const r = await fetch("api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.ok) { message("Gespeichert. Die Kamera startet die Aufnahme neu …", true); setTimeout(viewSettings, 8000); return; }
    const err = await r.json().catch(() => ({ fields: [] }));
    const names = [];
    for (const k of err.fields || []) {
      const key = k === "latitude" || k === "longitude" ? "location" : k;
      const wrap = document.querySelector(`.field[data-key="${key}"]`);
      if (wrap) { wrap.classList.add("bad"); names.push(wrap.querySelector("label").textContent); }
    }
    message("Nicht gespeichert: " + (names.join(", ") || "ungültige Eingabe") + ".", false);
  };
}

// --- System -------------------------------------------------------------------------
async function viewSystem() {
  page.innerHTML = `<h1>System</h1><section class="card stack"><dl class="facts" id="sys"></dl></section>`;
  every(async () => {
    const st = await getJSON("api/status").catch(() => null);
    if (!st) return;
    // Without a hub (e.g. --sim on a laptop) there is no setup API.
    const setup = await getJSON("api/setup").catch(() => null);
    const hub = setup ? `
      <dt>Gerät</dt><dd><code>${esc(setup.device_id)}</code></dd>
      <dt>Profil</dt><dd>${esc(setup.profile)}</dd>
      <dt>Hub</dt><dd>${esc(setup.hub_url)}</dd>
      <dt>Gekoppelt</dt><dd>${setup.paired ? "ja" : "nein"}</dd>
      <dt>Verbindung</dt><dd>${setup.connected ? '<span class="badge ok">verbunden</span>' : '<span class="badge">getrennt</span>'}</dd>
      <dt>Zeitzone</dt><dd>${esc(setup.timezone || "–")}</dd>` : "<dt>Hub</dt><dd>keiner</dd>";
    $("sys").innerHTML = `
      <dt>Agent</dt><dd>${esc(st.version)}</dd>${hub}
      <dt>Bilder seit Start</dt><dd>${st.frames}</dd>
      ${st.frame && st.frame.sensor_temp_c != null ? `<dt>Sensor</dt><dd>${st.frame.sensor_temp_c.toFixed(1)} °C</dd>` : ""}`;
  }, 5000);
}

// --- router -------------------------------------------------------------------------
const VIEWS = { live: viewLive, archiv: viewArchive, ereignisse: viewEvents, himmel: viewSky,
  einstellungen: viewSettings, system: viewSystem };

function route() {
  clearTimers();
  const [name, arg] = (location.hash.slice(1) || "live").split("/");
  const view = VIEWS[name] || viewLive;
  for (const a of document.querySelectorAll("#nav [data-page]")) {
    const on = a.dataset.page === (VIEWS[name] ? name : "live");
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  Promise.resolve(view(arg)).catch((e) => {
    if (e.message !== "login") page.innerHTML = `<p class="msg error">Konnte nicht geladen werden (${esc(e.message)}).</p>`;
  });
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
route();

// Pairing code while unpaired, logout button once paired (SPEC §7, §7.4).
(async function pollSetup() {
  try {
    const s = await getJSON("api/setup");
    $("logout").hidden = !s.paired;
    const show = !s.paired && s.pairing_code;
    $("pairPanel").hidden = !show;
    if (show) $("pairCode").textContent = s.pairing_code.slice(0, 3) + "-" + s.pairing_code.slice(3);
    if (s.paired) return;
  } catch { /* camera restarting */ }
  setTimeout(pollSetup, 3000);
})();
getJSON("api/status").then((s) => { $("version").textContent = s.version; }).catch(() => {});
