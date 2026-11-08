// Fireballs sent later (SPEC §10.8): the time from an Allsky file name, and the track
// marked on the frame (points in the frame's own pixels, kept in the hidden field).
(function () {
  "use strict";

  const file = document.getElementById("late-image");
  const taken = document.getElementById("late-taken");
  if (file && taken) {
    file.addEventListener("change", function () {
      const name = file.files && file.files[0] ? file.files[0].name : "";
      const m = /(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/.exec(name);
      if (m && !taken.value) {
        taken.value = m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":" + m[6];
      }
    });
  }

  const box = document.querySelector(".late-track");
  const img = document.getElementById("late-frame");
  const svg = document.getElementById("late-marks");
  const field = document.getElementById("late-track");
  const undo = document.getElementById("late-undo");
  const count = document.getElementById("late-count");
  if (!box || !img || !svg || !field) return;
  const width = Number(box.dataset.width);
  const height = Number(box.dataset.height);
  const ns = "http://www.w3.org/2000/svg";
  const size = Math.max(6, width / 250);
  let scale = 1, tx = 0, ty = 0;
  let points = [];
  try {
    const kept = JSON.parse(field.value || "[]");
    if (Array.isArray(kept)) points = kept;
  } catch (e) {
    points = [];
  }

  function draw() {
    // The marks keep their size on screen at any zoom.
    const r = size / scale;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (points.length > 1) {
      const line = document.createElementNS(ns, "polyline");
      line.setAttribute("points", points.map(function (p) { return p[0] + "," + p[1]; }).join(" "));
      line.setAttribute("class", "late-line");
      line.setAttribute("stroke-width", String(r / 2));
      svg.appendChild(line);
    }
    points.forEach(function (p) {
      const c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", String(p[0]));
      c.setAttribute("cy", String(p[1]));
      c.setAttribute("r", String(r));
      c.setAttribute("stroke-width", String(r / 3));
      c.setAttribute("class", "late-point");
      svg.appendChild(c);
    });
    field.value = JSON.stringify(points);
    if (count) count.textContent = points.length + " / 32";
  }

  // Zoom and pan (Benjamin, 2026-11-08: the track is a few pixels wide on a phone): the
  // wheel or two fingers zoom, dragging moves, a tap without moving sets a point. The stage
  // moves by a CSS transform; getBoundingClientRect follows it, so a tap maps as before.
  const stage = box.querySelector(".late-stage") || box;

  function apply() {
    const w = box.clientWidth, h = box.clientHeight;
    tx = Math.min(0, Math.max(w - w * scale, tx));
    ty = Math.min(0, Math.max(h - h * scale, ty));
    stage.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    draw();
  }

  function zoomAt(clientX, clientY, factor) {
    const rect = box.getBoundingClientRect();
    const px = clientX - rect.left, py = clientY - rect.top;
    const next = Math.min(12, Math.max(1, scale * factor));
    tx = px - (px - tx) * (next / scale);
    ty = py - (py - ty) * (next / scale);
    scale = next;
    apply();
  }

  function place(clientX, clientY) {
    if (points.length >= 32) return;
    const rect = svg.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * width;
    const y = ((clientY - rect.top) / rect.height) * height;
    if (x < 0 || y < 0 || x > width || y > height) return;
    points.push([Math.round(x * 10) / 10, Math.round(y * 10) / 10]);
    draw();
  }

  box.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    zoomAt(ev.clientX, ev.clientY, Math.exp(-ev.deltaY * 0.0015));
  }, { passive: false });

  const down = new Map();
  let moved = false, pinch = 0, last = null;
  box.addEventListener("pointerdown", function (ev) {
    box.setPointerCapture(ev.pointerId);
    down.set(ev.pointerId, { x: ev.clientX, y: ev.clientY, x0: ev.clientX, y0: ev.clientY });
    moved = down.size > 1;
    pinch = 0;
    last = null;
  });
  box.addEventListener("pointermove", function (ev) {
    const p = down.get(ev.pointerId);
    if (!p) return;
    p.x = ev.clientX;
    p.y = ev.clientY;
    const all = Array.from(down.values());
    if (all.length === 1) {
      if (Math.hypot(p.x - p.x0, p.y - p.y0) > 6) moved = true;
      if (moved && last) {
        tx += p.x - last.x;
        ty += p.y - last.y;
        apply();
      }
      last = { x: p.x, y: p.y };
    } else if (all.length === 2) {
      const d = Math.hypot(all[0].x - all[1].x, all[0].y - all[1].y);
      const mx = (all[0].x + all[1].x) / 2, my = (all[0].y + all[1].y) / 2;
      if (pinch) zoomAt(mx, my, d / pinch);
      pinch = d;
    }
  });
  function up(ev) {
    const p = down.get(ev.pointerId);
    down.delete(ev.pointerId);
    if (p && !moved && down.size === 0) place(ev.clientX, ev.clientY);
    if (down.size === 0) last = null;
    pinch = 0;
  }
  box.addEventListener("pointerup", up);
  box.addEventListener("pointercancel", function (ev) {
    down.delete(ev.pointerId);
    moved = true;
  });

  const buttons = { "late-zoom-in": 1.6, "late-zoom-out": 1 / 1.6 };
  Object.keys(buttons).forEach(function (id) {
    const b = document.getElementById(id);
    if (b) b.addEventListener("click", function () {
      const rect = box.getBoundingClientRect();
      zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, buttons[id]);
    });
  });
  const whole = document.getElementById("late-zoom-reset");
  if (whole) whole.addEventListener("click", function () {
    scale = 1;
    tx = ty = 0;
    apply();
  });

  if (undo) {
    undo.addEventListener("click", function () {
      points.pop();
      draw();
    });
  }
  draw();
})();
