// Draft 7: the aircraft around a camera on the hub's map tiles (web/routes_cameras.py
// flight_map_data), refreshed every 10 s. The rings show where an aircraft at 10 km stands
// 10° and 30° up; the ones in the picture are drawn bright.
(() => {
  const el = document.getElementById("flight-map");
  const raw = document.getElementById("map-config");
  if (!el || !raw || !window.L) return;
  const config = JSON.parse(raw.textContent);
  const [lat, lon] = config.camera;
  const map = L.map(el, { maxZoom: config.maxZoom, minZoom: 5 });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  const base = config.layers.karte;
  L.tileLayer("/karte/kacheln/karte/{z}/{x}/{y}", {
    maxZoom: config.maxZoom,
    maxNativeZoom: base.maxZoom,
    attribution: base.credit,
  }).addTo(map);
  const css = getComputedStyle(document.documentElement);
  const colour = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const accent = colour("--accent", "#7aa2f7");
  const bright = colour("--warn", "#f2c06b");
  const dim = colour("--text-2", "#a7b0cc");
  const outer = Math.max(...config.rings.map((r) => r.km));
  for (const ring of config.rings) {
    L.circle([lat, lon], {
      radius: ring.km * 1000, color: accent, weight: 1, dashArray: "5 6",
      fillOpacity: ring.km === outer ? 0.08 : 0, interactive: false,
    }).addTo(map);
  }
  L.circleMarker([lat, lon], { radius: 6, color: "#000", weight: 2, fillColor: accent, fillOpacity: 1 })
    .bindTooltip("Kamera").addTo(map);
  map.fitBounds(L.latLng(lat, lon).toBounds(outer * 2600));

  const layer = L.layerGroup().addTo(map);
  const list = document.querySelector("[data-flight-list]");
  const count = document.querySelector("[data-flight-count]");
  const credit = document.querySelector("[data-flight-credit]");
  const text = (value) => document.createTextNode(value);
  const km = (v) => String(v).replace(".", ",");

  const draw = (data) => {
    layer.clearLayers();
    list.replaceChildren();
    const shown = data.planes.filter((p) => p.distance_km <= outer * 1.6);
    for (const p of shown) {
      const c = p.in_picture ? bright : dim;
      if (p.trail.length > 1) L.polyline(p.trail, { color: c, weight: 2, opacity: 0.8, dashArray: "2 5", interactive: false }).addTo(layer);
      L.circleMarker([p.lat, p.lon], { radius: p.in_picture ? 6 : 4, color: "#000", weight: 1, fillColor: c, fillOpacity: 1 })
        .bindTooltip(`${p.name} · ${km(p.alt_km)} km`).addTo(layer);
    }
    for (const p of data.planes.slice(0, 40)) {
      const li = document.createElement("li");
      if (p.in_picture) li.className = "in";
      const name = document.createElement("b");
      name.append(text(p.name));
      const what = document.createElement("span");
      what.append(text([p.type, `${km(p.alt_km)} km hoch`, `${Math.round(p.distance_km)} km entfernt`].filter(Boolean).join(" · ")));
      const where = document.createElement("span");
      where.className = "where";
      where.append(text(p.in_picture ? `im Bild, ${Math.round(p.elevation)}°` : p.elevation > 0 ? `${Math.round(p.elevation)}°, zu tief` : "unter dem Horizont"));
      li.append(name, what, where);
      list.append(li);
    }
    if (!data.planes.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.append(text("Gerade kein Flugzeug in der Nähe, oder die Daten kommen noch."));
      list.append(li);
    }
    const inside = data.planes.filter((p) => p.in_picture).length;
    count.textContent = `${data.planes.length} in 150 km · ${inside} im Bild`;
    credit.textContent = data.credit;
  };

  const load = async () => {
    try {
      const r = await fetch(el.dataset.url, { headers: { Accept: "application/json" } });
      if (r.ok) draw(await r.json());
    } catch {
      // offline for a moment: the next round tries again
    }
  };
  load();
  setInterval(load, 10000);
})();
