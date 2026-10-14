// A fireball's path over the ground and its possible fall area (routes_fireballs.py), on
// the hub's map tiles like the camera map.
(() => {
  const el = document.getElementById("map");
  const raw = document.getElementById("map-config");
  if (!el || !raw || !window.L) return;
  const config = JSON.parse(raw.textContent);
  const map = L.map(el, { maxZoom: config.maxZoom, minZoom: 2 });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  const base = config.layers.karte;
  L.tileLayer("/karte/kacheln/karte/{z}/{x}/{y}", {
    maxZoom: config.maxZoom,
    maxNativeZoom: base.maxZoom,
    attribution: base.credit,
  }).addTo(map);

  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--accent").trim() || "#7aa2f7";
  const warn = css.getPropertyValue("--warn").trim() || "#f2c06b";
  const path = L.polyline(config.path, { color: accent, weight: 4 }).addTo(map);
  const [high, low] = config.heights;
  L.circleMarker(config.path[0], { radius: 5, color: accent })
    .bindTooltip(`${high} km`)
    .addTo(map);
  L.circleMarker(config.path[1], { radius: 5, color: accent, fillOpacity: 1 })
    .bindTooltip(`${low} km`)
    .addTo(map);
  let bounds = path.getBounds();
  if (config.area) {
    const area = L.polygon(config.area, { color: warn, weight: 2, fillOpacity: 0.25 }).addTo(map);
    bounds = bounds.extend(area.getBounds());
  }
  map.fitBounds(bounds, { padding: [30, 30], maxZoom: 11 });
})();
