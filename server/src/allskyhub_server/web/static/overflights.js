// The night's overflights on the sky map (roadmap L29): play the night like a timelapse,
// filter by kind, pick a track or a row to see its picture. Lists longer than LONG rows get
// a search field and show the first LONG rows until "Alle zeigen" (Benjamin, 2026-10-19).
// Without JavaScript the map shows every track and the lists show every row.
(function () {
  "use strict";
  const root = document.querySelector("[data-ovf]");
  const rows = Array.from(document.querySelectorAll(".ovf-row"));
  const detail = document.getElementById("ovf-detail");
  const tracks = root ? Array.from(root.querySelectorAll(".ovf-track")) : [];
  const skies = root ? Array.from(root.querySelectorAll(".ovf-stars")) : [];
  const RECENT = 20 * 60; // seconds shown bright while playing
  const STEP = 60; // seconds of the night per frame
  const FRAME_MS = 80;
  let now = null; // null: the whole night
  let timer = null;
  let selected = null;

  const hidden = new Set();
  const clockFmt = root
    ? new Intl.DateTimeFormat("de-AT", {
        hour: "2-digit", minute: "2-digit", timeZone: root.dataset.zone || undefined,
      })
    : null;

  function show() {
    for (const g of tracks) {
      const start = Number(g.dataset.start);
      // The picked track always shows, also ahead of the player's time.
      const later = now !== null && start > now && !g.classList.contains("on");
      const off = hidden.has(g.dataset.group) || later;
      g.toggleAttribute("hidden", off);
      g.classList.toggle("old", now !== null && !off && Number(g.dataset.end) < now - RECENT);
    }
    for (const r of rows) r.toggleAttribute("hidden", hidden.has(r.dataset.group));
    document.dispatchEvent(new Event("ovf:rows"));
    if (skies.length) {
      const t = now ?? Number(skies[Math.floor(skies.length / 2)].dataset.at);
      let best = skies[0];
      for (const s of skies) if (Math.abs(s.dataset.at - t) < Math.abs(best.dataset.at - t)) best = s;
      for (const s of skies) s.hidden = s !== best;
    }
    const out = root && root.querySelector("[data-ovf-clock]");
    if (out) out.textContent = now === null ? "alle" : clockFmt.format(new Date(now * 1000));
  }

  function select(i) {
    for (const g of tracks) g.classList.toggle("on", g.dataset.i === i);
    for (const r of rows) r.classList.toggle("on", r.dataset.i === i && i !== undefined);
  }

  function open(row) {
    selected = row;
    row.classList.remove("cut");
    for (const r of rows) r.classList.toggle("on", r === row);
    select(row.dataset.i);
    if (root) show();
    if (!detail) return;
    detail.querySelector("[data-ovf-title]").textContent = row.dataset.title;
    detail.querySelector("[data-ovf-text]").textContent = row.dataset.text;
    detail.querySelector("[data-ovf-link]").href = row.dataset.link;
    const img = detail.querySelector("[data-ovf-img]");
    if (row.dataset.img) {
      img.src = row.dataset.img;
      img.hidden = false;
    } else {
      img.hidden = true;
      img.removeAttribute("src");
    }
    detail.hidden = false;
    detail.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  for (const r of rows) {
    r.addEventListener("click", (e) => {
      if (e.target.closest("a")) return;
      open(r);
    });
  }
  for (const g of tracks) {
    const row = rows.find((r) => r.dataset.i === g.dataset.i);
    const pick = () => row && open(row);
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
  }
  if (detail) {
    detail.querySelector("[data-ovf-close]").addEventListener("click", () => {
      detail.hidden = true;
      selected = null;
      select(undefined);
      for (const r of rows) r.classList.remove("on");
      if (root) show();
    });
  }
  const LONG = 10;
  for (const table of document.querySelectorAll("[data-long-list]")) {
    const all = Array.from(table.tBodies[0].rows);
    if (all.length <= LONG) continue;
    const box = table.closest(".table-scroll") || table;
    const search = document.createElement("input");
    search.type = "search";
    search.className = "list-search";
    search.placeholder = "Suchen: Name, Rufzeichen, Typ, Richtung";
    search.setAttribute("aria-label", "Liste durchsuchen");
    search.autocomplete = "off";
    const none = document.createElement("p");
    none.className = "muted small";
    none.textContent = "Nichts gefunden.";
    const more = document.createElement("button");
    more.type = "button";
    more.className = "secondary small";
    box.before(search);
    box.after(none, more);
    let full = false;
    const apply = () => {
      const q = search.value.trim().toLowerCase();
      let shown = 0;
      for (const r of all) {
        const text = (r.textContent + " " + (r.dataset.search || "")).toLowerCase();
        const hit = !q || text.includes(q);
        r.classList.toggle("miss", !hit);
        if (hit && !r.hidden) shown += 1;
        r.classList.toggle("cut", !q && !full && shown > LONG && r !== selected);
      }
      none.hidden = !q || shown > 0;
      more.hidden = Boolean(q) || shown <= LONG;
      more.textContent = full ? "Weniger zeigen" : `Alle ${shown} zeigen`;
    };
    search.addEventListener("input", apply);
    more.addEventListener("click", () => {
      full = !full;
      apply();
    });
    document.addEventListener("ovf:rows", apply);
    apply();
  }

  if (!root) return;

  for (const box of root.querySelectorAll("[data-ovf-group]")) {
    box.addEventListener("change", () => {
      if (box.checked) hidden.delete(box.dataset.ovfGroup);
      else hidden.add(box.dataset.ovfGroup);
      show();
    });
  }

  const player = root.querySelector(".ovf-player");
  const range = root.querySelector("[data-ovf-time]");
  const play = root.querySelector("[data-ovf-play]");
  player.hidden = false;
  range.value = range.min;

  function stop() {
    clearInterval(timer);
    timer = null;
    play.textContent = "▶";
    play.setAttribute("aria-label", "Abspielen");
  }
  play.addEventListener("click", () => {
    if (timer) return stop();
    if (now === null || now >= Number(range.max)) now = Number(range.min);
    play.textContent = "❚❚";
    play.setAttribute("aria-label", "Anhalten");
    timer = setInterval(() => {
      now = Math.min(Number(range.max), now + STEP);
      range.value = now;
      show();
      if (now >= Number(range.max)) stop();
    }, FRAME_MS);
  });
  range.addEventListener("input", () => {
    stop();
    now = Number(range.value);
    show();
  });
  root.querySelector("[data-ovf-all]").addEventListener("click", () => {
    stop();
    now = null;
    range.value = range.min;
    show();
  });
  show();
})();
