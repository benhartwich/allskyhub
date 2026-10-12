// Measurement viewer (charts.html) on uPlot: sky values and the camera's state over one
// range, with twilight bands, sun and moon altitude, a shared cursor, drag-to-zoom across
// all panels, CSV and PNG export. Data: GET <data-src>?night=YYYYMMDD | ?hours=N
// (devices/series.py). Colours come from CSS custom properties, so the red theme works.
(() => {
  const root = document.querySelector("[data-charts]");
  if (!root || !window.uPlot) return;
  const uPlot = window.uPlot;
  const withStatus = root.hasAttribute("data-status");
  // Embedded on another page: smaller, and the address bar stays that page's.
  const compact = root.hasAttribute("data-compact");
  const panelsBox = root.querySelector("[data-panels]");
  const meta = root.querySelector("[data-chart-meta]");
  const resetButton = root.querySelector("[data-reset-zoom]");
  const nightPick = root.querySelector("[data-night-pick]");
  const DE = "de-DE";
  const WEEKDAYS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

  // What to draw. `source` is the table in the answer; scales with `log` are logarithmic.
  const PANELS = [
    {
      id: "sqm", title: "Himmelshelligkeit", source: "sky", height: 260, ephemeris: true,
      series: [{ key: "sqm", label: "Himmelshelligkeit", unit: "mag/″²", color: "--chart-sqm", scale: "sqm", digits: 2 }],
      axes: [{ scale: "sqm", label: "mag/″² (↑ dunkler)" }],
    },
    {
      id: "clouds", title: "Bewölkung und Sterne", source: "sky", height: 200,
      series: [
        { key: "cloud", label: "Bewölkung", unit: "%", color: "--chart-clouds", scale: "pct", digits: 0, fill: true },
        { key: "stars", label: "Sterne", unit: "", color: "--chart-stars", scale: "n", digits: 0 },
      ],
      axes: [{ scale: "pct", label: "Bewölkung %" }, { scale: "n", label: "Sterne", side: 1 }],
    },
    {
      id: "exposure", title: "Belichtung und Gain", source: "status", height: 200, status: true,
      series: [
        { key: "exposure", label: "Belichtung", unit: "s", color: "--chart-exposure", scale: "s", digits: 3 },
        { key: "gain", label: "Gain", unit: "", color: "--chart-gain", scale: "gain", digits: 0, step: true },
      ],
      axes: [{ scale: "s", label: "Belichtung s (log)", log: true }, { scale: "gain", label: "Gain", side: 1 }],
    },
    {
      id: "temps", title: "Temperaturen und Speicher", source: "status", height: 200, status: true,
      series: [
        { key: "sensor", label: "Sensor", unit: "°C", color: "--chart-temp", scale: "c", digits: 1 },
        { key: "cpu", label: "Prozessor", unit: "°C", color: "--chart-cpu", scale: "c", digits: 0 },
        { key: "disk", label: "Speicher frei", unit: "%", color: "--chart-disk", scale: "pct", digits: 0 },
      ],
      axes: [{ scale: "c", label: "°C" }, { scale: "pct", label: "Speicher frei %", side: 1 }],
    },
  ];
  const SCALES = {
    sqm: { auto: true, range: (u, min, max) => padRange(min, max, 0.25, 0.5) },
    pct: { auto: false, range: [0, 100] },
    n: { auto: true, range: (u, min, max) => [0, Math.max(10, max * 1.08)] },
    s: { distr: 3, log: 10 },
    gain: { auto: true, range: (u, min, max) => padRange(min, max, 0.08, 1) },
    c: { auto: true, range: (u, min, max) => padRange(min, max, 0.1, 2) },
    deg: { auto: false, range: [-30, 90] },
  };
  const BANDS = [
    [0, "--chart-day"],
    [-6, "--chart-civil"],
    [-12, "--chart-nautical"],
    [-18, "--chart-astro"],
  ];

  let data = null;
  let plots = [];
  let syncing = false;
  let request = null;
  const overlays = { twilight: true, moon: true, sun: false };
  const sync = uPlot.sync("ash-charts");

  const padRange = (min, max, part, least) => {
    if (min == null || max == null) return [0, 1];
    const pad = Math.max((max - min) * part, least / 2);
    return [min - pad, max + pad];
  };
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  const num = (v, digits) =>
    v == null || Number.isNaN(v) ? "–" : v.toLocaleString(DE, { maximumFractionDigits: digits, minimumFractionDigits: digits > 2 ? 0 : digits });

  const decades = (min, max) => {
    const out = [];
    for (let e = Math.floor(Math.log10(min)); e <= Math.ceil(Math.log10(max)); e++) out.push(10 ** e);
    return out;
  };
  const axisNum = (v) =>
    v == null ? "" : Math.abs(v) >= 100 ? num(v, 0) : v.toLocaleString(DE, { maximumSignificantDigits: 3 });

  // Time formatting in the camera's zone, independent of the browser's.
  const parts = (ts, opts) => {
    const f = new Intl.DateTimeFormat(DE, { timeZone: data.tz, ...opts });
    return Object.fromEntries(f.formatToParts(new Date(ts * 1000)).map((p) => [p.type, p.value]));
  };
  const clock = (ts) => {
    const p = parts(ts, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    return `${p.hour}:${p.minute}`;
  };
  const day = (ts) => {
    const p = parts(ts, { day: "numeric", month: "numeric", weekday: "short" });
    return `${p.weekday.replace(".", "")} ${p.day}.${p.month}.`;
  };
  const stamp = (ts) => `${day(ts)} ${clock(ts)}`;
  const midnight = (ts) => {
    const p = parts(ts, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    return p.hour === "00" && p.minute === "00";
  };
  const xValues = (u, splits, axisIdx, space, incr) =>
    splits.map((ts) => (incr >= 86400 || midnight(ts) ? day(ts) : clock(ts)));

  // Night ids (YYYYMMDD) as calendar dates without time zone trouble.
  const nightDate = (id) => new Date(Date.UTC(+id.slice(0, 4), +id.slice(4, 6) - 1, +id.slice(6, 8)));
  const nightId = (d) => d.toISOString().slice(0, 10).replaceAll("-", "");
  const nightOf = (ts) => {
    // The night a moment belongs to: before local noon it is the evening before.
    const p = parts(ts, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" });
    const d = new Date(Date.UTC(+p.year, +p.month - 1, +p.day));
    if (+p.hour < 12) d.setUTCDate(d.getUTCDate() - 1);
    return nightId(d);
  };
  const nightTitle = (id) => {
    const a = nightDate(id);
    const b = new Date(a);
    b.setUTCDate(b.getUTCDate() + 1);
    const m = (d) => d.toLocaleDateString(DE, { month: "long", timeZone: "UTC" });
    return a.getUTCMonth() === b.getUTCMonth()
      ? `Nacht ${a.getUTCDate()}./${b.getUTCDate()}. ${m(b)} ${b.getUTCFullYear()}`
      : `Nacht ${a.getUTCDate()}. ${m(a)}/${b.getUTCDate()}. ${m(b)} ${b.getUTCFullYear()}`;
  };

  const tableFor = (panel) => {
    const src = data[panel.source];
    if (!src) return null;
    const cols = panel.series.map((s) => src[s.key] || []);
    if (!cols.some((c) => c.some((v) => v != null))) return null;
    const own = [src.t, ...cols];
    const eph = data.ephemeris;
    if (!panel.ephemeris || !eph) return { table: own, extra: 0 };
    // Sun and moon on their own time grid; uPlot.join aligns both. NULL_EXPAND (2) keeps a
    // pause a gap across the sun/moon points inside it (modes are indexed from 1, like series).
    const joined = uPlot.join(
      [own, [eph.t, eph.sun, eph.moon]],
      [[0, ...cols.map(() => 2)], [0, 0, 0]],
    );
    return { table: joined, extra: 2 };
  };

  const drawBands = (u) => {
    const eph = data.ephemeris;
    if (!overlays.twilight || !eph) return;
    const { ctx } = u;
    const { left, top, width, height } = u.bbox;
    const colors = BANDS.map(([, v]) => css(v));
    const band = (alt) => BANDS.findIndex(([limit]) => alt > limit);
    ctx.save();
    ctx.beginPath();
    ctx.rect(left, top, width, height);
    ctx.clip();
    let from = 0;
    for (let i = 1; i <= eph.t.length; i++) {
      if (i < eph.t.length && band(eph.sun[i]) === band(eph.sun[from])) continue;
      const b = band(eph.sun[from]);
      if (b >= 0) {
        const x0 = u.valToPos(eph.t[from], "x", true);
        const x1 = u.valToPos(eph.t[Math.min(i, eph.t.length - 1)], "x", true);
        ctx.fillStyle = colors[b];
        ctx.fillRect(x0, top, x1 - x0, height);
      }
      from = i;
    }
    ctx.restore();
  };

  const build = (panel, width) => {
    const t = tableFor(panel);
    const box = document.createElement("figure");
    box.className = "chart-panel";
    box.innerHTML = `<figcaption><strong>${panel.title}</strong></figcaption>`;
    panelsBox.append(box);
    if (!t) {
      box.insertAdjacentHTML("beforeend", '<p class="muted small chart-empty">Keine Messwerte in diesem Zeitraum.</p>');
      return null;
    }
    const grid = { stroke: css("--chart-grid"), width: 1 };
    const ticks = { stroke: css("--chart-grid"), width: 1, size: 4 };
    const axisFont = `12px ${getComputedStyle(document.body).fontFamily}`;
    const axisColor = css("--chart-axis");
    const series = [
      { value: (u, v) => (v == null ? "–" : stamp(v)), label: "Zeit" },
      ...panel.series.map((s) => ({
        label: s.unit ? `${s.label} (${s.unit})` : s.label,
        scale: s.scale,
        stroke: css(s.color),
        width: 1.6,
        fill: s.fill ? `${css(s.color)}22` : undefined,
        spanGaps: false,
        paths: s.step ? uPlot.paths.stepped({ align: 1 }) : undefined,
        points: { show: (u, si, i0, i1) => i1 - i0 < 120, size: 4 },
        value: (u, v) => num(v, s.digits),
      })),
    ];
    const axes = [
      { stroke: axisColor, grid, ticks, font: axisFont, values: xValues, space: 70 },
      ...panel.axes.map((a, i) => ({
        scale: a.scale, side: a.side ?? 3, label: a.label, labelSize: 18, labelFont: axisFont,
        stroke: axisColor, font: axisFont, size: 52,
        grid: i === 0 ? grid : { show: false }, ticks,
        values: (u, vals) => vals.map(axisNum),
        // Log scale: grid and labels at whole decades only.
        ...(a.log ? { splits: (u, ai, min, max) => decades(min, max) } : {}),
      })),
    ];
    if (t.extra) {
      series.push(
        { label: "Sonnenhöhe (°)", scale: "deg", stroke: css("--chart-sun"), width: 1.2, dash: [2, 4], show: overlays.sun, value: (u, v) => num(v, 1), points: { show: false } },
        { label: "Mondhöhe (°)", scale: "deg", stroke: css("--chart-moon"), width: 1.2, dash: [6, 4], show: overlays.moon, value: (u, v) => num(v, 1), points: { show: false } },
      );
      axes.push({ scale: "deg", side: 1, label: "Höhe °", labelSize: 18, labelFont: axisFont, stroke: axisColor, font: axisFont, size: 44, grid: { show: false }, ticks, values: (u, vals) => vals.map((v) => `${v}°`), splits: () => [-18, 0, 30, 60, 90] });
    }
    const opts = {
      width,
      height: compact ? Math.round(panel.height * 0.8) : panel.height,
      series,
      axes,
      // The whole asked range on first draw (not just where data is); afterwards zooms.
      scales: { x: { time: true, range: (u, min, max) => (u.ashReady ? [min, max] : [data.from, data.to]) }, ...SCALES },
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), data.tz),
      cursor: { sync: { key: sync.key, setSeries: false }, drag: { x: true, y: false, setScale: true }, points: { size: 7 } },
      legend: { live: true },
      hooks: {
        drawClear: [drawBands],
        setScale: [(u, key) => key === "x" && follow(u)],
      },
    };
    const plot = new uPlot(opts, t.table, box);
    plot.ashReady = true;
    plot.panel = panel;
    return plot;
  };

  // Zoom one panel → zoom all; show the reset button while zoomed.
  const follow = (source) => {
    if (syncing) return;
    syncing = true;
    const { min, max } = source.scales.x;
    for (const p of plots) if (p !== source) p.setScale("x", { min, max });
    syncing = false;
    resetButton.hidden = !(min > data.from + 1 || max < data.to - 1);
  };

  const render = () => {
    for (const p of plots) p.destroy();
    plots = [];
    panelsBox.replaceChildren();
    if (!data) return;
    const width = Math.max(280, panelsBox.clientWidth);
    for (const panel of PANELS) {
      if (panel.status && !withStatus) continue;
      const plot = build(panel, width);
      if (plot) plots.push(plot);
    }
    resetButton.hidden = true;
  };

  const describe = () => {
    const bits = [data.night ? nightTitle(data.night) : `${stamp(data.from)} – ${stamp(data.to)}`];
    bits.push(`Ortszeit ${data.tz}`);
    if (data.ephemeris) bits.push(`Mond ${num(data.ephemeris.moon_illumination[0] * 100, 0)} % beleuchtet`);
    if (data.bucket_s) bits.push(`Mittelwerte über ${Math.round(data.bucket_s / 60)} min`);
    meta.textContent = bits.join(" · ");
    document.querySelectorAll("[data-chart-tz]").forEach((n) => (n.textContent = data.tz));
    root.querySelectorAll("[data-range]").forEach((b) => {
      const on = b.dataset.range === "night" ? !!data.night : +b.dataset.range === data.hours;
      b.classList.toggle("secondary", !on);
      b.setAttribute("aria-pressed", String(on));
    });
    if (nightPick) {
      const id = data.night || nightOf(data.to);
      nightPick.value = `${id.slice(0, 4)}-${id.slice(4, 6)}-${id.slice(6, 8)}`;
    }
  };

  const load = async (query) => {
    request?.abort();
    request = new AbortController();
    meta.textContent = "Lade Messdaten …";
    try {
      const res = await fetch(`${root.dataset.src}?${new URLSearchParams(query)}`, { signal: request.signal, credentials: "same-origin" });
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
    } catch (e) {
      if (e.name === "AbortError") return;
      meta.textContent = "Die Messdaten konnten nicht geladen werden.";
      return;
    }
    if (!compact) {
      const url = new URL(location.href);
      url.searchParams.delete("night");
      url.searchParams.delete("hours");
      if (data.night) url.searchParams.set("night", data.night);
      else url.searchParams.set("hours", data.hours);
      history.replaceState(null, "", url);
    }
    describe();
    render();
  };

  const currentNight = () => data?.night || (data ? nightOf(data.to) : null);

  root.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.range === "night") load(currentNight() ? { night: currentNight() } : {});
    else if (b.dataset.range) load({ hours: b.dataset.range });
    else if (b.dataset.nightStep && currentNight()) {
      const d = nightDate(currentNight());
      d.setUTCDate(d.getUTCDate() + +b.dataset.nightStep);
      load({ night: nightId(d) });
    } else if (b.hasAttribute("data-reset-zoom")) {
      for (const p of plots) p.setScale("x", { min: data.from, max: data.to });
    } else if (b.dataset.export === "csv") exportCsv();
    else if (b.dataset.export === "png") exportPng();
  });
  nightPick?.addEventListener("change", () => {
    if (nightPick.value) load({ night: nightPick.value.replaceAll("-", "") });
  });
  root.querySelectorAll("[data-overlay]").forEach((box) => {
    overlays[box.dataset.overlay] = box.checked;
    box.addEventListener("change", () => {
      overlays[box.dataset.overlay] = box.checked;
      for (const p of plots) {
        p.series.forEach((s, i) => {
          if (s.label === "Sonnenhöhe (°)") p.setSeries(i, { show: overlays.sun });
          if (s.label === "Mondhöhe (°)") p.setSeries(i, { show: overlays.moon });
        });
        p.redraw(false);
      }
    });
  });

  // Exports cover what is on screen (the zoomed range, if zoomed).
  const visible = () => {
    const x = plots[0]?.scales.x;
    return [x?.min ?? data.from, x?.max ?? data.to];
  };
  const download = (blob, name) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const fileStem = () => `allskyhub-${data.night || nightOf(data.to)}`;

  const exportCsv = () => {
    const [min, max] = visible();
    const cols = [];
    for (const panel of PANELS) {
      if (panel.status && !withStatus) continue;
      for (const s of panel.series) {
        if (!cols.some((c) => c.key === s.key)) cols.push({ ...s, source: panel.source });
      }
    }
    const tables = ["sky", "status"].filter((k) => data[k]).map((k) => [data[k].t, ...cols.filter((c) => c.source === k).map((c) => data[k][c.key])]);
    const order = ["sky", "status"].filter((k) => data[k]).flatMap((k) => cols.filter((c) => c.source === k));
    const joined = tables.length > 1 ? uPlot.join(tables) : tables[0];
    const head = ["utc", `ortszeit (${data.tz})`, ...order.map((c) => (c.unit ? `${c.key} [${c.unit}]` : c.key))];
    const lines = [head.join(",")];
    for (let i = 0; i < joined[0].length; i++) {
      const ts = joined[0][i];
      if (ts < min || ts > max) continue;
      const row = joined.slice(1).map((col) => (col[i] == null ? "" : String(col[i])));
      if (row.every((v) => v === "")) continue;
      lines.push([new Date(ts * 1000).toISOString(), `${day(ts)} ${clock(ts)}`, ...row].join(","));
    }
    if (data.bucket_s) lines.unshift(`# Mittelwerte über ${data.bucket_s} s`);
    download(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }), `${fileStem()}.csv`);
  };

  const exportPng = () => {
    if (!plots.length) return;
    const ratio = window.devicePixelRatio || 1;
    const head = 34 * ratio;
    const w = plots[0].ctx.canvas.width;
    const h = plots.reduce((a, p) => a + p.ctx.canvas.height + head, 0) + head;
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d");
    ctx.fillStyle = css("--bg");
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = css("--text");
    ctx.font = `${14 * ratio}px ${getComputedStyle(document.body).fontFamily}`;
    ctx.fillText(`${document.title} · ${meta.textContent}`, 8 * ratio, 22 * ratio);
    let y = head;
    for (const p of plots) {
      ctx.fillStyle = css("--text");
      ctx.fillText(p.panel.title, 8 * ratio, y + 22 * ratio);
      y += head;
      ctx.drawImage(p.ctx.canvas, 0, y);
      y += p.ctx.canvas.height;
    }
    c.toBlob((blob) => blob && download(blob, `${fileStem()}.png`), "image/png");
  };

  // Keep width with the page; rebuild for the red theme (colours are read once per build).
  let lastWidth = 0;
  new ResizeObserver(() => {
    const w = Math.max(280, panelsBox.clientWidth);
    if (Math.abs(w - lastWidth) < 2) return;
    lastWidth = w;
    for (const p of plots) p.setSize({ width: w, height: p.height });
  }).observe(panelsBox);
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  // First range: the address (full page), else data-default ("24", a night id or "night").
  const params = compact ? new URLSearchParams() : new URLSearchParams(location.search);
  const first = params.get("hours") || params.get("night") || root.dataset.default || "";
  load(/^\d{8}$/.test(first) ? { night: first } : /^\d+$/.test(first) ? { hours: first } : {});

  // Nights of a year (owner page): darkest sky per night, clear hours, moon phase.
  const yearBox = document.querySelector("[data-year-chart]");
  if (yearBox) {
    const panel = yearBox.querySelector("[data-year-panel]");
    let year = new Date().getFullYear();
    let yearPlot = null;
    const nav = document.createElement("div");
    nav.className = "actions";
    nav.innerHTML = '<button type="button" class="secondary small" data-year="-1" aria-label="Jahr davor">‹</button><strong data-year-label></strong><button type="button" class="secondary small" data-year="1" aria-label="Jahr danach">›</button>';
    yearBox.insertBefore(nav, panel);
    const draw = async () => {
      nav.querySelector("[data-year-label]").textContent = year;
      const res = await fetch(`${yearBox.dataset.src}?year=${year}`, { credentials: "same-origin" });
      if (!res.ok) return;
      const d = await res.json();
      yearPlot?.destroy();
      yearPlot = null;
      panel.replaceChildren();
      const n = d.nights;
      if (!n.t.length) {
        panel.innerHTML = '<p class="muted small chart-empty">Für dieses Jahr gibt es noch keine Nächte.</p>';
        return;
      }
      const axisFont = `12px ${getComputedStyle(document.body).fontFamily}`;
      const grid = { stroke: css("--chart-grid"), width: 1 };
      const tz = d.tz;
      yearPlot = new uPlot({
        width: Math.max(280, panel.clientWidth),
        height: 240,
        tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), tz),
        scales: {
          x: { time: true },
          sqm: { auto: true, range: (u, min, max) => padRange(min, max, 0.1, 0.5) },
          h: { auto: true, range: (u, min, max) => [0, Math.max(4, max)] },
          pct: { auto: false, range: [0, 100] },
        },
        series: [
          { value: (u, v) => (v == null ? "–" : new Date(v * 1000).toLocaleDateString(DE, { timeZone: tz })), label: "Nacht" },
          { label: "Klare Stunden", scale: "h", stroke: css("--chart-clouds"), fill: `${css("--chart-clouds")}55`, paths: uPlot.paths.bars({ size: [0.7, 12] }), points: { show: false }, value: (u, v) => num(v, 1) },
          { label: "Dunkelster Himmel (mag/″²)", scale: "sqm", stroke: css("--chart-sqm"), paths: () => null, points: { show: true, size: 6, fill: css("--chart-sqm") }, value: (u, v) => num(v, 2) },
          { label: "Mond beleuchtet (%)", scale: "pct", stroke: css("--chart-moon"), width: 1, dash: [6, 4], points: { show: false }, value: (u, v) => num(v, 0) },
        ],
        axes: [
          { stroke: css("--chart-axis"), grid, font: axisFont, values: (u, splits) => splits.map((ts) => new Date(ts * 1000).toLocaleDateString(DE, { month: "short", timeZone: tz })) },
          { scale: "sqm", label: "mag/″² (↑ dunkler)", labelSize: 18, labelFont: axisFont, stroke: css("--chart-axis"), font: axisFont, grid, size: 52, values: (u, vals) => vals.map(axisNum) },
          { scale: "h", side: 1, label: "klare Stunden", labelSize: 18, labelFont: axisFont, stroke: css("--chart-axis"), font: axisFont, grid: { show: false }, size: 44 },
        ],
        legend: { live: true },
      }, [n.t, n.clear_hours, n.best_sqm, n.moon], panel);
    };
    nav.addEventListener("click", (e) => {
      const step = e.target.closest("[data-year]")?.dataset.year;
      if (!step) return;
      year += +step;
      draw();
    });
    draw();
  }
})();
