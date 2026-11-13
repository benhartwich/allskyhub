// Draft 6: a night's saved frames with the sky over them (routes_gallery.sky_history_frame),
// or, step 2, the sky alone from sunset to sunrise (planetarium, sky_history_time). The
// slider picks a frame or a time; the drawing comes from the hub as JSON and uses the same
// classes as the live image, so the layer switches of overlay.js work here too.
(() => {
  const root = document.querySelector("[data-sky-history]");
  const raw = document.getElementById("sky-frames");
  for (const select of document.querySelectorAll("[data-autosubmit]")) {
    select.addEventListener("change", () => select.form.submit());
  }
  if (!root || !raw) return;
  const frames = JSON.parse(raw.textContent);
  const times = JSON.parse(document.getElementById("sky-times")?.textContent || "[]");
  const disc = root.querySelector("[data-sky-disc]");
  const modeButton = root.querySelector("[data-sky-mode]");
  let planetarium = frames.length === 0;
  const image = root.querySelector("[data-sky-image]");
  const drawing = root.querySelector("[data-sky-drawing]");
  const slider = root.querySelector("[data-sky-time]");
  const clock = root.querySelector("[data-sky-clock]");
  const play = root.querySelector("[data-sky-play]");
  const base = `/cameras/${root.dataset.device}`;
  const night = root.dataset.night;
  const NS = "http://www.w3.org/2000/svg";
  const cache = new Map();

  const el = (name, attrs, text) => {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = (cls, d) => el("svg", { class: cls, viewBox: `0 0 ${d.width} ${d.height}`, preserveAspectRatio: "xMidYMid meet", "aria-hidden": "true" });
  const label = (g, m, font, dx, dy, cls) => g.append(el("text", { x: Math.round(m.x + dx), y: Math.round(m.y + dy), "font-size": font, ...(cls ? { class: cls } : {}) }, m.text));

  const draw = (d) => {
    drawing.replaceChildren();
    if (!d || !d.width) return;
    // Without a picture the disc keeps the frame's shape and the lens's sky circle.
    disc.style.aspectRatio = `${d.width} / ${d.height}`;
    const f = d.font;
    const lines = svg("sky-overlay", d);
    lines.append(el("path", { class: "sky-lines", d: d.lines }));
    for (const n of d.names) lines.append(el("text", { class: "sky-name", x: n.x, y: n.y, "font-size": f }, n.text));
    for (const c of d.cardinals) lines.append(el("text", { class: "sky-cardinal", x: c.x, y: c.y, "font-size": f * 1.6 }, c.text));

    const layers = svg("layer-overlay", d);
    const lf = Math.round(d.width / 80 * 10) / 10;
    const grid = el("g", { class: "lay-grid" });
    for (const p of d.grid) grid.append(el("path", { d: p }));
    for (const c of d.grid_cardinals) label(grid, c, lf * 1.5, 0, 0, "lay-cardinal");
    for (const m of d.grid_labels) label(grid, m, lf, lf * 0.4, -lf * 0.3);
    const stars = el("g", { class: "lay-stars" });
    for (const m of d.stars) { stars.append(el("circle", { cx: Math.round(m.x), cy: Math.round(m.y), r: lf * 0.45 })); label(stars, m, lf, lf * 0.8, lf * 0.35); }
    const planets = el("g", { class: "lay-planets" });
    for (const m of d.planets) { planets.append(el("circle", { cx: Math.round(m.x), cy: Math.round(m.y), r: lf * 0.6 })); label(planets, m, lf, lf, lf * 0.35); }
    const sats = el("g", { class: "lay-sats" });
    for (const m of d.satellites) { if (m.trail) sats.append(el("path", { d: m.trail })); sats.append(el("circle", { cx: Math.round(m.x), cy: Math.round(m.y), r: lf * 0.4 })); label(sats, m, lf, lf * 0.8, -lf * 0.4); }
    const events = el("g", { class: "lay-events" });
    for (const t of d.tracks) {
      events.append(el("line", { x1: t.x1, y1: t.y1, x2: t.x2, y2: t.y2, class: t.kind }));
      events.append(el("text", { x: t.x2 + lf * 0.6, y: t.y2, "font-size": lf, class: t.kind }, t.label));
    }
    layers.append(grid, stars, planets, sats, events);
    drawing.append(lines, layers);
  };

  const url = (item) => item.name
    ? `${base}/himmel/${night}/${encodeURIComponent(item.name)}.json`
    : `${base}/himmel/${night}/zeit?at=${encodeURIComponent(item.at)}`;
  const sky = (item) => {
    const key = url(item);
    if (!cache.has(key)) {
      cache.set(key, fetch(key, { headers: { Accept: "application/json" } })
        .then((r) => (r.ok ? r.json() : null)).catch(() => null));
    }
    return cache.get(key);
  };
  const src = (frame) => `${base}/frames/${night}/${encodeURIComponent(frame.name)}/${frame.full ? "full" : "thumb"}.jpg`;
  const items = () => (planetarium ? times : frames);

  let shown = -1;
  const show = async (i) => {
    const list = items();
    const item = list[i];
    if (!item) return;
    shown = i;
    clock.textContent = item.clock;
    if (!planetarium) image.src = src(item);
    const d = await sky(item);
    if (shown === i) draw(d);
    const next = list[i + 1];
    if (next) { if (!planetarium) new Image().src = src(next); sky(next); }
  };
  // The slider's range follows the mode; switching keeps about the same time of night.
  const nearest = (list, t) => list.reduce((best, item, k) => (Math.abs(Date.parse(item.at) - t) < Math.abs(Date.parse(list[best].at) - t) ? k : best), 0);
  // keep: switching keeps the time of night; else the newest frame, or in the planetarium
  // the time nearest to now (the first one for a past night).
  const setMode = (on, keep = true) => {
    const at = keep ? items()[Number(slider.value)]?.at : undefined;
    planetarium = on;
    root.classList.toggle("planetarium", on);
    modeButton?.setAttribute("aria-pressed", String(on));
    const list = items();
    slider.max = String(Math.max(0, list.length - 1));
    let i = list.length - 1;
    if (at) i = nearest(list, Date.parse(at));
    else if (on && list.length) i = nearest(list, Date.now());
    slider.value = String(i);
    show(i);
  };
  modeButton?.addEventListener("click", () => { stop(); setMode(!planetarium); });
  slider.addEventListener("input", () => show(Number(slider.value)));

  let timer = null;
  const stop = () => { clearInterval(timer); timer = null; play.textContent = "▶"; play.setAttribute("aria-label", "Abspielen"); };
  play.addEventListener("click", () => {
    if (timer) return stop();
    if (Number(slider.value) >= items().length - 1) slider.value = "0";
    play.textContent = "❚❚";
    play.setAttribute("aria-label", "Anhalten");
    show(Number(slider.value));
    timer = setInterval(() => {
      const next = Number(slider.value) + 1;
      if (next >= items().length) return stop();
      slider.value = String(next);
      show(next);
    }, 600);
  });
  setMode(planetarium, false);
})();
