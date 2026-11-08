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
  const r = Math.max(6, width / 250);
  let points = [];
  try {
    const kept = JSON.parse(field.value || "[]");
    if (Array.isArray(kept)) points = kept;
  } catch (e) {
    points = [];
  }

  function draw() {
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

  svg.addEventListener("click", function (ev) {
    if (points.length >= 32) return;
    const rect = svg.getBoundingClientRect();
    const x = ((ev.clientX - rect.left) / rect.width) * width;
    const y = ((ev.clientY - rect.top) / rect.height) * height;
    points.push([Math.round(x * 10) / 10, Math.round(y * 10) / 10]);
    draw();
  });
  if (undo) {
    undo.addEventListener("click", function () {
      points.pop();
      draw();
    });
  }
  draw();
})();
