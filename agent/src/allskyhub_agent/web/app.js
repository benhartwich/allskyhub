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

// --- Overlay (L11) ------------------------------------------------------------------
// Drawn over the image when it is shown; the stored frames stay as the camera took them.
// On unless this browser switched it off.
const loadPref = (k) => { try { return localStorage.getItem("ash-ov-" + k) !== "0"; } catch { return true; } };
const savePref = (k, on) => { try { localStorage.setItem("ash-ov-" + k, on ? "1" : "0"); } catch { /* private mode */ } };

function overlaySvg(f, orientation, ov, w, h, fixedColours = false, sky = null) {
  const H = w ? 1000 * h / w : 562.5;
  const fs = 22;  // in viewBox units: about 2 % of the width
  const cls = fixedColours ? ' fill="#fff" stroke="rgba(0,0,0,.75)" stroke-width="4" paint-order="stroke"' : "";
  let out = "";
  if (ov.stars && sky) {
    // Pixels of the solved frame, scaled to the drawing's 1000 units wide.
    const k = 1000 / sky.width;
    const line = fixedColours ? ' stroke="rgba(160,190,255,.7)"' : "";
    const label = fixedColours ? ' fill="rgba(200,215,255,.9)"' : "";
    out += `<g class="ov-sky" transform="scale(${k.toFixed(5)})">
      ${sky.paths.map((d) => `<path d="${d}" fill="none" stroke-width="${(1.6 / k).toFixed(2)}"${line}/>`).join("")}
      ${sky.names.map((n) => `<text x="${n.x}" y="${n.y}" font-size="${sky.font}" text-anchor="middle"${label}>${esc(n.text)}</text>`).join("")}
    </g>`;
  }
  if (ov.data && f) {
    const at = new Date(f.captured_at);
    const lines = [
      at.toLocaleDateString("de-AT", { day: "2-digit", month: "2-digit", year: "numeric" }) + " " +
        at.toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
      `${fmtExposure(f.exposure_us)} · Gain ${f.gain}${f.sensor_temp_c != null ? ` · ${f.sensor_temp_c.toFixed(1)} °C` : ""}`,
      `Sonne ${f.sun_elevation.toFixed(1)}° · ${f.mode === "night" ? "Nacht" : "Tag"}`,
    ];
    out += lines.map((t, i) => `<text class="ov-text" x="16" y="${16 + fs * (i + 1) * 1.25}" font-size="${fs}"${cls}>${esc(t)}</text>`).join("");
  }
  const o = orientation && orientation.overlay;
  if (ov.compass && o) {
    out += o.points.map((p) => `<text class="ov-compass" x="${(p.x * 1000).toFixed(1)}" y="${(p.y * H).toFixed(1)}" font-size="${fs * 1.6}"
      text-anchor="middle" dominant-baseline="middle"${cls}>${p.label}</text>`).join("");
  }
  return out;
}

// The shown image plus the overlay as one JPEG, made in the browser.
function saveWithOverlay(img, svgInner, f) {
  if (!img.naturalWidth) return;
  const w = img.naturalWidth, h = img.naturalHeight;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 1000 ${(1000 * h / w).toFixed(1)}"
    font-family="system-ui, sans-serif" font-weight="600">${svgInner}</svg>`;
  const layer = new Image();
  layer.onload = () => {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    g.drawImage(layer, 0, 0, w, h);
    c.toBlob((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      const stamp = f ? f.name.replace(/\.jpg$/, "") : "allsky";
      a.download = `${stamp}-overlay.jpg`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    }, "image/jpeg", 0.92);
  };
  layer.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

// --- Live (L3) ---------------------------------------------------------------------
function viewLive() {
  page.innerHTML = `
    <section class="card stack">
      <div class="live-stage">
        <img class="live-img" id="live" alt="Aktuelles Bild">
        <svg id="liveOverlay" class="overlay" viewBox="0 0 1000 562.5" aria-hidden="true"></svg>
      </div>
      <div class="row">
        <button type="button" class="secondary small" data-ov="data" aria-pressed="false">Daten</button>
        <button type="button" class="secondary small" data-ov="compass" aria-pressed="false">Himmelsrichtungen</button>
        <button type="button" class="secondary small" data-ov="stars" aria-pressed="false" hidden>Sternbilder</button>
        <button type="button" class="secondary small" id="ovSave">Mit Overlay speichern</button>
      </div>
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
  let lastFrame = null;
  let orientation = null;
  getJSON("api/calibration").then((c) => { orientation = c.orientation; drawOverlay(); }).catch(() => {});
  const ov = { data: loadPref("data"), compass: loadPref("compass"), stars: loadPref("stars") };
  let sky = null;  // constellations for the shown frame (packages/sky, as on the hub)
  let skyFor = null;
  const loadSky = () => {
    if (!lastFrame || lastFrame.captured_at === skyFor) return;
    skyFor = lastFrame.captured_at;
    getJSON("api/sky-overlay?at=" + encodeURIComponent(lastFrame.captured_at)).then((s) => {
      sky = s;
      document.querySelector('[data-ov="stars"]').hidden = false;
      drawOverlay();
    }).catch(() => {});
  };
  for (const b of document.querySelectorAll("[data-ov]")) {
    const key = b.dataset.ov;
    const sync = () => { b.classList.toggle("active", ov[key]); b.setAttribute("aria-pressed", String(ov[key])); };
    sync();
    b.onclick = () => { ov[key] = !ov[key]; savePref(key, ov[key]); sync(); drawOverlay(); };
  }
  // The overlay is drawn on top, never into the stored image (L11): Benjamin, 2026-10-12.
  const drawOverlay = () => {
    const img = $("live"), svg = $("liveOverlay");
    if (!img || !svg) return;
    svg.innerHTML = overlaySvg(lastFrame, orientation, ov, img.naturalWidth, img.naturalHeight, false, sky);
    if (img.naturalWidth) svg.setAttribute("viewBox", `0 0 1000 ${(1000 * img.naturalHeight / img.naturalWidth).toFixed(1)}`);
  };
  $("live").addEventListener("load", drawOverlay);
  $("ovSave").onclick = () => saveWithOverlay($("live"), overlaySvg(lastFrame, orientation,
    { data: true, compass: ov.compass, stars: ov.stars }, $("live").naturalWidth, $("live").naturalHeight, true, sky), lastFrame);
  $("live").onclick = () => lightbox([{ src: $("live").src, caption: "Aktuelles Bild" }], 0);
  const render = (s) => {
    $("version").textContent = s.version;
    focus = s.focus_mode;
    $("focusBtn").textContent = focus ? "Fokusmodus beenden" : "Fokusmodus starten";
    $("focusPanel").hidden = !focus;
    $("resetBtn").hidden = !focus;
    if (s.frame) {
      const f = s.frame;
      lastFrame = f;
      loadSky();
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
  } else if (spec.type === "bool") {
    label.className = "check";
    label.textContent = "";
    label.append(input(spec, original[spec.key]), " " + text(spec.label));
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
    <p class="msg" id="settingsMsg" hidden></p></div>
    <section class="card stack" id="calibration" hidden></section>
    <section class="card stack" id="public" hidden></section>`;
  viewCalibration().catch(() => {});
  viewPublic().catch(() => {});
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

// --- Sky mask and orientation (L8) ----------------------------------------------------
// The live image with everything that is not sky shaded red. Tap points to draw an area,
// tap the first point again (or "Fläche fertig") to close it. Coordinates are fractions
// of the image, so they fit every frame size.
async function viewCalibration() {
  const box = $("calibration");
  let info = await getJSON("api/calibration");
  let areas = { exclude: info.edits.exclude.slice(), include: info.edits.include.slice() };
  let drawing = null;  // {kind, points}
  let showGrid = true;
  box.hidden = false;
  box.innerHTML = `
    <h2>Himmelsmaske und Ausrichtung</h2>
    <p class="muted small" id="maskState"></p>
    <div class="mask-stage" id="maskStage">
      <img id="maskImg" alt="Aktuelles Bild">
      <img id="maskShade" alt="" hidden>
      <svg id="maskSvg" viewBox="0 0 1000 562.5" aria-hidden="true"></svg>
    </div>
    <div class="row">
      <button type="button" class="secondary small" data-draw="exclude">Bereich ausschließen</button>
      <button type="button" class="secondary small" data-draw="include">Bereich einschließen</button>
      <button type="button" class="secondary small" id="maskDone" hidden>Fläche fertig</button>
      <button type="button" class="secondary small" id="maskUndo" hidden>Punkt zurück</button>
      <button type="button" class="secondary small active" id="maskGrid" aria-pressed="true">Horizont und Himmelsrichtungen</button>
    </div>
    <ul class="areas" id="maskAreas"></ul>
    <div class="actions">
      <button type="button" id="maskSave">Maske speichern</button>
      <button type="button" class="secondary" id="maskRelearn">Maske neu lernen</button>
    </div>
    <p class="msg" id="maskMsg" hidden></p>
    <dl class="facts" id="orientFacts"></dl>`;
  const img = $("maskImg"), shade = $("maskShade"), svg = $("maskSvg");
  const say = (t, good) => { const m = $("maskMsg"); m.textContent = t; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
  // The drawing has the image's aspect, so letters and circles are not stretched.
  let VH = 1000 * 9 / 16;
  const X = (x) => (x * 1000).toFixed(1), Y = (y) => (y * VH).toFixed(1);
  const pts = (list) => list.map(([x, y]) => `${X(x)},${Y(y)}`).join(" ");
  const refreshShade = () => {
    if (!img.naturalWidth) return;
    VH = 1000 * img.naturalHeight / img.naturalWidth;
    svg.setAttribute("viewBox", `0 0 1000 ${VH.toFixed(1)}`);
    draw();
    shade.onerror = () => { shade.hidden = true; };
    shade.onload = () => { shade.hidden = false; };
    shade.src = `api/skymask.png?w=${img.naturalWidth}&h=${img.naturalHeight}&t=${Date.now()}`;
  };
  const draw = () => {
    const o = info.orientation && info.orientation.overlay;
    let out = "";
    if (o && showGrid) {
      out += `<polyline class="ring horizon" points="${pts(o.horizon)}"/>`;
      out += o.rings.map((r) => `<polyline class="ring" points="${pts(r)}"/>`).join("");
      out += `<circle class="zenith" cx="${X(o.zenith[0])}" cy="${Y(o.zenith[1])}" r="6"/>`;
      out += o.points.map((p) => `<text class="compass" x="${X(p.x)}" y="${Y(p.y)}">${p.label}</text>`).join("");
    }
    for (const kind of ["exclude", "include"]) {
      out += areas[kind].map((a) => `<polygon class="area ${kind}" points="${pts(a)}"/>`).join("");
    }
    if (drawing) {
      out += `<polyline class="area ${drawing.kind} open" points="${pts(drawing.points)}"/>`;
      out += drawing.points.map(([x, y], i) => `<circle class="vertex${i ? "" : " first"}" cx="${X(x)}" cy="${Y(y)}" r="${i ? 7 : 11}"/>`).join("");
    }
    svg.innerHTML = out;
    $("maskDone").hidden = $("maskUndo").hidden = !drawing;
    for (const b of box.querySelectorAll("[data-draw]")) b.classList.toggle("active", !!drawing && drawing.kind === b.dataset.draw);
    $("maskAreas").innerHTML = ["exclude", "include"].flatMap((kind) => areas[kind].map((a, i) => `
      <li><span class="badge ${kind === "exclude" ? "" : "ok"}">${kind === "exclude" ? "Ausgeschlossen" : "Eingeschlossen"} ${i + 1}</span>
        <button type="button" class="secondary small" data-remove="${kind}:${i}" aria-label="Bereich entfernen">Entfernen</button></li>`)).join("");
    for (const b of $("maskAreas").querySelectorAll("[data-remove]")) {
      b.onclick = () => { const [k, i] = b.dataset.remove.split(":"); areas[k].splice(Number(i), 1); draw(); };
    }
  };
  const describe = () => {
    $("maskState").textContent = info.mask_learned_at
      ? `Gelernt am ${fmtDateTime(info.mask_learned_at)}. Rot: zählt nicht als Himmel (Bäume, Dächer, Rand). Die Kamera lernt die Maske jede klare Nacht neu, gezeichnete Bereiche bleiben.`
      : "Noch keine Maske gelernt, das passiert in der ersten Nacht. Bis dahin gilt der Bildkreis des Objektivs; gezeichnete Bereiche gelten sofort.";
    const o = info.orientation;
    $("orientFacts").innerHTML = o ? `
      <dt>Norden</dt><dd>${o.north_deg}° im Bild (im Uhrzeigersinn von oben)${o.mirrored ? ", gespiegelt" : ""}</dd>
      <dt>Ausgerichtet</dt><dd>${fmtDateTime(o.solved_at)} mit ${o.stars} Sternen, Abweichung ${o.rms_deg.toFixed(2)}°</dd>
      <dt>Prüfen</dt><dd class="muted small">Stimmt N mit der Wirklichkeit überein und liegt die gelbe Linie auf dem Horizont? Sonst ist die Ausrichtung falsch; sie wird nach 7 Tagen neu bestimmt.</dd>`
      : `<dt>Ausrichtung</dt><dd>Noch nicht bestimmt. Die Kamera findet sie selbst in einer klaren, mondlosen Nacht anhand der Sterne.</dd>`;
  };
  const at = (e) => {
    const r = img.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const finish = () => {
    if (drawing && drawing.points.length >= 3) areas[drawing.kind].push(drawing.points);
    drawing = null;
    draw();
  };
  $("maskStage").onclick = (e) => {
    if (!drawing) return;
    const p = at(e);
    const first = drawing.points[0];
    const r = img.getBoundingClientRect();
    if (first && drawing.points.length >= 3 && Math.hypot((p[0] - first[0]) * r.width, (p[1] - first[1]) * r.height) < 18) { finish(); return; }
    drawing.points.push(p.map((v) => Math.round(v * 1e4) / 1e4));
    draw();
  };
  for (const b of box.querySelectorAll("[data-draw]")) {
    b.onclick = () => { drawing = drawing && drawing.kind === b.dataset.draw ? null : { kind: b.dataset.draw, points: [] }; draw(); };
  }
  $("maskDone").onclick = finish;
  $("maskUndo").onclick = () => { if (drawing) { drawing.points.pop(); draw(); } };
  $("maskGrid").onclick = (e) => {
    showGrid = !showGrid;
    e.target.classList.toggle("active", showGrid);
    e.target.setAttribute("aria-pressed", String(showGrid));
    draw();
  };
  const post = async (url, body) => {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "local-login"; return null; }
    return r.ok ? r.json() : null;
  };
  $("maskSave").onclick = async () => {
    if (drawing) finish();
    const next = await post("api/calibration/mask", areas);
    if (!next) { say("Nicht gespeichert.", false); return; }
    info = next;
    say("Gespeichert. Gilt ab dem nächsten Bild für Belichtung, Erkennung und Himmelswerte.", true);
    describe(); draw(); refreshShade();
  };
  $("maskRelearn").onclick = async () => {
    if (!confirm("Gelernte Maske verwerfen? Die Kamera lernt in der nächsten klaren Nacht eine neue. Gezeichnete Bereiche bleiben.")) return;
    const next = await post("api/calibration/relearn", {});
    if (!next) { say("Hat nicht geklappt.", false); return; }
    info = next;
    say("Verworfen. Bis zur nächsten Nacht gilt der Bildkreis.", true);
    describe(); draw(); refreshShade();
  };
  img.onload = refreshShade;
  img.src = "api/live.jpg?t=" + Date.now();
  describe();
  draw();
}

// --- Public page on the hub (L10, SPEC §6.8) --------------------------------------------
const SECTIONS = { live: "Livebild", loop: "Loop der letzten Stunde", archive: "Archiv", events: "Ereignisse",
  sky: "Himmel (Charts)", aurora: "Polarlicht-Lage", observatory: "Sternwarte (N.I.N.A.)" };
const TEXTS = [["title", "Titel", 100], ["location_label", "Ort", 200], ["camera", "Kamera", 200],
  ["lens", "Objektiv", 200], ["computer", "Rechner", 200], ["owner", "Betrieben von", 200]];

async function viewPublic() {
  const box = $("public");
  const r = await fetch("api/public", { cache: "no-store" });
  if (r.status === 404) return;  // no hub configured
  box.hidden = false;
  const say = (t, good) => { const m = $("pubMsg"); m.textContent = t; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
  if (!r.ok) {
    box.innerHTML = `<h2>Öffentliche Seite</h2><p class="muted">Der Hub ist gerade nicht erreichbar. Die öffentliche Seite liegt auf dem Hub und lässt sich nur ändern, wenn die Kamera verbunden ist.</p>`;
    return;
  }
  const render = (st) => {
    const p = st.profile;
    const off = !st.online;
    box.innerHTML = `
      <h2>Öffentliche Seite</h2>
      <p class="muted small">Eine Seite auf allskyhub.org, die jeder ohne Anmeldung sehen kann. Sie liegt auf dem Hub; Änderungen hier und dort sind dieselben.</p>
      ${off ? `<p class="msg error">Der Hub ist gerade nicht erreichbar: zuletzt bekannter Stand, nur lesbar.</p>` : ""}
      <div class="row">
        ${st.enabled ? `<span class="badge ok">eingeschaltet</span> <a href="${esc(st.url)}" target="_blank" rel="noopener">${esc(st.url)}</a>`
          : `<span class="badge">ausgeschaltet</span>`}
      </div>
      <div class="actions"><button type="button" class="${st.enabled ? "secondary" : ""}" id="pubToggle"${off ? " disabled" : ""}>
        ${st.enabled ? "Ausschalten" : "Einschalten"}</button></div>
      ${st.enabled ? `<p class="muted small">Ausschalten macht die Adresse sofort ungültig; beim erneuten Einschalten gibt es eine neue.</p>` : ""}
      <form class="form stack" id="pubForm" novalidate>
        <fieldset${off ? " disabled" : ""}><legend>Texte</legend>
          ${TEXTS.map(([k, l, n]) => `<div class="field"><label for="pub_${k}">${l}</label>
            <input id="pub_${k}" name="${k}" maxlength="${n}" value="${esc(p[k])}"></div>`).join("")}
          <label class="check"><input type="checkbox" name="show_coordinates"${p.show_coordinates ? " checked" : ""}> Koordinaten zeigen (auf 0,1° gerundet, etwa 10 km)</label>
          <div class="field"><label for="pub_notes">Notizen</label>
            <textarea id="pub_notes" name="notes" maxlength="2000" rows="4">${esc(p.notes)}</textarea></div>
        </fieldset>
        <fieldset${off ? " disabled" : ""}><legend>Abschnitte</legend>
          ${Object.entries(SECTIONS).map(([k, l]) => `<label class="check"><input type="checkbox" name="section_${k}"${p.sections[k] ? " checked" : ""}> ${l}</label>`).join("")}
        </fieldset>
      </form>
      <div class="actions"><button type="button" id="pubSave"${off ? " disabled" : ""}>Seite speichern</button></div>
      <p class="msg" id="pubMsg" hidden></p>`;
    const send = async (body, done) => {
      const res = await fetch("api/public", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (res.status === 401) { location.href = "local-login"; return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { say(data.detail || "Nicht gespeichert.", false); return; }
      render(data);
      say(done, true);
    };
    $("pubToggle").onclick = () => {
      if (st.enabled && !confirm("Öffentliche Seite ausschalten? Die Adresse wird sofort ungültig.")) return;
      send({ enabled: !st.enabled }, st.enabled ? "Ausgeschaltet." : "Eingeschaltet.");
    };
    $("pubSave").onclick = () => {
      const f = $("pubForm").elements;
      const profile = { show_coordinates: f.show_coordinates.checked, notes: f.notes.value, sections: {} };
      for (const [k] of TEXTS) profile[k] = f[k].value;
      for (const k of Object.keys(SECTIONS)) profile.sections[k] = f["section_" + k].checked;
      send({ profile }, "Gespeichert.");
    };
  };
  render(await r.json());
}

// --- System (L12) ----------------------------------------------------------------------
const POWER = { under_voltage: "Unterspannung jetzt", freq_capped: "Takt begrenzt", throttled: "gedrosselt",
  temp_limit: "Temperaturgrenze erreicht", under_voltage_seen: "Unterspannung seit dem Start",
  freq_capped_seen: "Takt war begrenzt", throttled_seen: "war gedrosselt", temp_limit_seen: "Temperaturgrenze war erreicht" };
const fmtBytes = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round(b / 1e6) + " MB");
const fmtUptime = (s) => {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (d ? `${d} d ` : "") + `${h} h ${m} min`;
};
const meter = (used, total) => `<div class="row"><div class="bar"><div style="width:${Math.min(100, 100 * used / total).toFixed(0)}%"></div></div>
  <span class="small muted">${Math.round(100 * used / total)} %</span></div>`;

async function viewSystem() {
  page.innerHTML = `<h1>System</h1>
    <section class="card stack"><h2>Kamera und Hub</h2><dl class="facts" id="sys"></dl></section>
    <section class="card stack" id="hw" hidden><h2>Rechner</h2><dl class="facts" id="hwFacts"></dl></section>
    <section class="card stack" id="net" hidden></section>
    <section class="card stack" id="upd" hidden></section>
    <section class="card stack" id="support" hidden></section>
    <section class="card stack" id="actions" hidden><h2>Neu starten</h2>
      <p class="muted small">Die Aufnahme läuft danach von selbst weiter. Gespeicherte Bilder bleiben erhalten.</p>
      <div class="actions">
        <button type="button" class="secondary" data-act="restart">Kamera-Dienst neu starten</button>
        <button type="button" class="secondary" data-act="reboot">Rechner neu starten</button>
        <button type="button" class="danger" data-act="shutdown">Ausschalten</button>
      </div>
      <p class="msg" id="sysMsg" hidden></p></section>`;
  const ASK = { restart: "Kamera-Dienst neu starten? Die Seite ist ein paar Sekunden nicht erreichbar.",
    reboot: "Rechner neu starten? Die Kamera ist etwa eine Minute nicht erreichbar.",
    shutdown: "Kamera ausschalten? Sie bleibt aus, bis jemand den Strom kurz trennt." };
  const DONE = { restart: "Der Dienst startet neu …", reboot: "Der Rechner startet neu …",
    shutdown: "Die Kamera fährt herunter. Warte 20 Sekunden, bevor du den Strom trennst." };
  for (const b of document.querySelectorAll("[data-act]")) {
    b.onclick = async () => {
      const act = b.dataset.act;
      if (!confirm(ASK[act])) return;
      const r = await fetch("api/system/" + act, { method: "POST" });
      if (r.status === 401) { location.href = "local-login"; return; }
      const data = await r.json().catch(() => ({}));
      const m = $("sysMsg");
      m.textContent = r.ok ? DONE[act] : data.detail || "Hat nicht geklappt.";
      m.className = "msg " + (r.ok ? "ok" : "error");
      m.hidden = false;
    };
  }
  viewNetwork().catch(() => {});
  viewUpdates().catch(() => {});
  viewSupport().catch(() => {});
  every(async () => {
    const [st, setup, hw] = await Promise.all([getJSON("api/status").catch(() => null),
      getJSON("api/setup").catch(() => null), getJSON("api/system").catch(() => null)]);
    if (!st) return;
    // Without a hub (e.g. --sim on a laptop) there is no setup API.
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
    if (!hw) return;
    $("hw").hidden = false;
    $("actions").hidden = false;
    const power = hw.power == null ? "" : hw.power.length
      ? `<dt>Stromversorgung</dt><dd>${hw.power.map((f) => `<span class="badge">${esc(POWER[f] || f)}</span>`).join(" ")}
          <p class="muted small">Unterspannung macht Bilder und SD-Karte kaputt: ein Netzteil mit 5,1 V / 3 A nehmen.</p></dd>`
      : `<dt>Stromversorgung</dt><dd><span class="badge ok">in Ordnung</span></dd>`;
    $("hwFacts").innerHTML = `
      ${hw.model ? `<dt>Modell</dt><dd>${esc(hw.model)}</dd>` : ""}
      ${hw.os ? `<dt>System</dt><dd>${esc(hw.os)}</dd>` : ""}
      <dt>Läuft seit</dt><dd>${fmtUptime(hw.uptime_s)}</dd>
      ${hw.cpu_temp_c != null ? `<dt>CPU</dt><dd>${hw.cpu_temp_c.toFixed(1)} °C${hw.load ? ` · Last ${hw.load[0].toFixed(2)} (${hw.cpus || "?"} Kerne)` : ""}</dd>` : ""}
      ${hw.memory ? `<dt>Arbeitsspeicher</dt><dd>${fmtBytes(hw.memory.total - hw.memory.available)} von ${fmtBytes(hw.memory.total)}
          ${meter(hw.memory.total - hw.memory.available, hw.memory.total)}</dd>` : ""}
      ${hw.disk ? `<dt>Speicher</dt><dd>${fmtBytes(hw.disk.free)} frei von ${fmtBytes(hw.disk.total)}
          ${meter(hw.disk.total - hw.disk.free, hw.disk.total)}</dd>` : ""}
      ${power}
      ${hw.alpaca ? `<dt>ASCOM Alpaca</dt><dd>${!hw.alpaca.enabled ? '<span class="muted">aus</span>'
        : hw.alpaca.error ? `<span class="badge">Port belegt</span> <span class="muted small">Port ${hw.alpaca.port} oder 32227 nutzt schon ein anderes Programm (z. B. ASCOM Remote).</span>`
        : `<span class="badge ok">an</span> Port ${hw.alpaca.port}`}</dd>` : ""}
      ${hw.observatory ? `<dt>N.I.N.A.</dt><dd>${hw.observatory.connected ? '<span class="badge ok">verbunden</span>' : '<span class="badge">nicht erreichbar</span>'}
        ${hw.observatory.target ? ` · ${esc(hw.observatory.target)}` : ""}${hw.observatory.sequence ? ` · ${Math.round(hw.observatory.sequence.progress * 100)} %` : ""}
        ${hw.observatory.safe != null ? ` · ${hw.observatory.safe ? "sicher" : "unsicher"}` : ""}</dd>` : ""}
      <dt>Uhrzeit</dt><dd>${hw.time_trusted ? '<span class="badge ok">genau</span>' : '<span class="badge">ungenau</span>'}</dd>`;
  }, 5000);
}

// --- Log, support, backup (L15) ------------------------------------------------------------
async function viewSupport() {
  const box = $("support");
  const log = await getJSON("api/logs?lines=300");
  box.hidden = false;
  box.innerHTML = `<h2>Protokoll und Support</h2>
    <details><summary>Protokoll der Kamera (letzte ${log.lines.length} ${log.lines.length === 1 ? "Zeile" : "Zeilen"})</summary>
      <pre class="log" id="logBox"></pre>
      <div class="actions"><button type="button" class="secondary small" id="logRefresh">Aktualisieren</button></div>
    </details>
    <p class="muted small">Das Support-Paket enthält Protokoll, Versionen, System- und Netzwerkdaten, Einstellungen und
      die Kalibrierung, aber keine Schlüssel, Passwörter oder Bilder. Hänge es an deine Frage an.</p>
    <div class="actions"><a class="button secondary" href="api/support.zip" download>Support-Paket herunterladen</a></div>
    <h3>Sicherung</h3>
    <p class="muted small">Einstellungen und gezeichnete Maskenbereiche, zum Beispiel für eine neue SD-Karte.</p>
    <div class="actions">
      <a class="button secondary" href="api/backup.json" download>Sicherung herunterladen</a>
      <label class="button secondary" for="restoreFile">Sicherung einspielen</label>
      <input type="file" id="restoreFile" accept="application/json,.json" hidden>
    </div>
    <p class="msg" id="restoreMsg" hidden></p>`;
  const show = (lines) => {
    const pre = $("logBox");
    pre.textContent = lines.join("\n");
    pre.scrollTop = pre.scrollHeight;
  };
  show(log.lines);
  $("logRefresh").onclick = async () => show((await getJSON("api/logs?lines=300")).lines);
  $("restoreFile").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const m = $("restoreMsg");
    const say = (t, good) => { m.textContent = t; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
    if (!confirm(`Sicherung „${file.name}“ einspielen? Die jetzigen Einstellungen werden ersetzt.`)) { e.target.value = ""; return; }
    const r = await fetch("api/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: await file.text() });
    e.target.value = "";
    if (r.status === 401) { location.href = "local-login"; return; }
    if (r.ok) { say("Eingespielt. Die Kamera startet die Aufnahme neu …", true); return; }
    const err = await r.json().catch(() => ({}));
    say((err.fields || []).includes("backup") ? "Das ist keine Sicherung dieser Kamera-Software."
      : "Nicht eingespielt, ungültig: " + (err.fields || []).join(", ") + ".", false);
  };
}

// --- Updates (L14, SPEC §8) -----------------------------------------------------------------
const UPDATE_STATES = { up_to_date: "aktuell", downloading: "lädt herunter", waiting: "wartet auf den Tag",
  installing: "wird installiert", installed: "installiert", rolled_back: "zurückgenommen", failed: "fehlgeschlagen" };
const UPDATE_CODES = { unhealthy: "lief nicht sauber", asked: "auf Wunsch", no_space: "zu wenig Speicher",
  no_previous: "keine ältere Version vorhanden", bad_signature: "Signatur ungültig", checksum: "Prüfsumme falsch",
  bad_bundle: "Paket beschädigt" };

async function viewUpdates() {
  const box = $("upd");
  const u = await getJSON("api/update");
  box.hidden = false;
  const st = u.state;
  const others = u.releases.filter((r) => r !== u.release);
  box.innerHTML = `<h2>Updates</h2>
    <dl class="facts">
      <dt>Version</dt><dd>${esc(u.release || u.version)}</dd>
      ${st ? `<dt>Zuletzt</dt><dd>${esc(UPDATE_STATES[st.state] || st.state)}${st.version ? ` · ${esc(st.version)}` : ""}${st.code ? ` (${esc(UPDATE_CODES[st.code] || st.code)})` : ""}
        <span class="muted small">${st.at ? fmtDateTime(st.at) : ""}</span></dd>` : ""}
      ${others.length ? `<dt>Ältere Versionen</dt><dd>${others.map((r) => `<code>${esc(r)}</code>`).join(" ")}</dd>` : ""}
    </dl>
    ${u.updater ? `
      <p class="muted small">Die Kamera sucht stündlich nach Updates und installiert sie nur am Tag. Läuft eine neue Version
        nicht sauber, geht sie von selbst zurück.</p>
      <div class="actions">
        <button type="button" class="secondary" data-upd="now"${u.pending ? " disabled" : ""}>Jetzt suchen und installieren</button>
        ${others.length ? `<button type="button" class="secondary" data-upd="rollback"${u.pending ? " disabled" : ""}>Vorige Version</button>` : ""}
      </div>
      ${u.pending ? `<p class="msg ok">Angefragt, der Updater ist dran …</p>` : `<p class="msg" id="updMsg" hidden></p>`}`
      : `<p class="muted small">Auf dieser Installation werden Updates von Hand eingespielt.</p>`}`;
  for (const b of box.querySelectorAll("[data-upd]")) {
    b.onclick = async () => {
      const word = b.dataset.upd;
      const ask = word === "now" ? "Jetzt nach einem Update suchen und es gleich installieren, auch nachts? Die Aufnahme pausiert dabei kurz."
        : "Zur vorigen Version zurückgehen? Die jetzige wird dann nicht von selbst wieder installiert.";
      if (!confirm(ask)) return;
      const r = await fetch("api/update/" + word, { method: "POST" });
      if (r.status === 401) { location.href = "local-login"; return; }
      if (r.ok) { viewUpdates(); return; }
      const m = $("updMsg");
      m.textContent = (await r.json().catch(() => ({}))).detail || "Hat nicht geklappt.";
      m.className = "msg error";
      m.hidden = false;
    };
  }
}

// --- Network (L13) ----------------------------------------------------------------------
async function viewNetwork() {
  const box = $("net");
  const n = await getJSON("api/network");
  box.hidden = false;
  const iface = (i) => `<dt>${i.kind === "wifi" ? "WLAN" : "LAN"} <span class="muted small">${esc(i.name)}</span></dt>
    <dd>${i.up ? (i.addresses.length ? i.addresses.map((a) => `<code>${esc(a.split("/")[0])}</code>`).join(" ") : "verbunden, keine Adresse")
      : '<span class="muted">nicht verbunden</span>'}${i.default ? ' <span class="badge ok">Internet</span>' : ""}</dd>`;
  box.innerHTML = `<h2>Netzwerk</h2>
    <dl class="facts">
      <dt>Name im Netz</dt><dd><code>${esc(n.hostname)}.local</code></dd>
      ${n.wifi ? `<dt>WLAN</dt><dd>${esc(n.wifi.ssid)}${n.wifi.signal != null ? ` · Signal ${n.wifi.signal} %` : ""}</dd>` : ""}
      ${n.interfaces.map(iface).join("")}
    </dl>
    ${n.managed ? `
      <form class="form stack" id="wifiForm" novalidate>
        <fieldset><legend>Anderes WLAN</legend>
          <div class="field"><label for="wifiSsid">Netz</label>
            <select id="wifiSsid" name="ssid"><option value="">Suche …</option></select></div>
          <div class="field"><label for="wifiPw">Passwort</label>
            <input id="wifiPw" name="password" type="password" autocomplete="off" placeholder="leer bei offenem Netz"></div>
          <div class="field"><label for="wifiCountry">Land (Funkregeln)</label>
            <input id="wifiCountry" name="country" maxlength="2" value="${esc(n.country || "AT")}"></div>
        </fieldset>
      </form>
      <p class="muted small">Klappt die Verbindung nicht, öffnet die Kamera nach 2 Minuten ihr eigenes WLAN
        <code>allskyhub-…</code>, über das du sie mit der App wieder einrichtest.</p>
      <div class="actions"><button type="button" id="wifiJoin">Verbinden</button></div>
      <p class="msg" id="wifiMsg" hidden></p>`
      : `<p class="muted small">Das Netzwerk verwaltet auf dieser Installation das Betriebssystem, nicht die Kamera.</p>`}`;
  if (!n.managed) return;
  const say = (t, good) => { const m = $("wifiMsg"); m.textContent = t; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
  getJSON("api/network/wifi").then((nets) => {
    $("wifiSsid").innerHTML = nets.length
      ? nets.map((w) => `<option value="${esc(w.ssid)}"${n.wifi && w.ssid === n.wifi.ssid ? " selected" : ""}>${esc(w.ssid)} (${w.signal} %${w.secure ? ", gesichert" : ""})</option>`).join("")
      : `<option value="">Keine Netze gefunden</option>`;
  }).catch(() => { $("wifiSsid").innerHTML = `<option value="">Suche fehlgeschlagen</option>`; });
  $("wifiJoin").onclick = async () => {
    const f = $("wifiForm").elements;
    if (!f.ssid.value) { say("Bitte ein Netz wählen.", false); return; }
    if (!confirm(`Mit „${f.ssid.value}“ verbinden? Die Kamera ist danach nur noch in diesem Netz erreichbar.`)) return;
    const r = await fetch("api/network/wifi", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid: f.ssid.value, password: f.password.value || null, country: f.country.value }) });
    if (r.status === 401) { location.href = "local-login"; return; }
    if (r.ok) { say(`Die Kamera wechselt zu „${f.ssid.value}“. Diese Seite ist jetzt nicht mehr erreichbar.`, true); return; }
    const err = await r.json().catch(() => ({}));
    const names = { password: "Passwort (8 bis 63 Zeichen)", country: "Land (zwei Buchstaben)", ssid: "Netz" };
    say("Nicht übernommen: " + ((err.fields || []).map((k) => names[k] || k).join(", ") || err.detail || "ungültig") + ".", false);
  };
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
