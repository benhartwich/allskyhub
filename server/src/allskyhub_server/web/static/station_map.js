// A station's place on the hub's map tiles (web/routes_stations.py): shown to the admin,
// or set by a click when the owner asks to connect a camera by its image address.
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
  const lat = document.getElementById("latitude");
  const lon = document.getElementById("longitude");
  let marker = null;
  const place = (point) => {
    // A dot, not L.marker: the hub ships Leaflet without its marker images.
    if (marker) marker.setLatLng(point);
    else marker = L.circleMarker(point, { radius: 8, className: "map-dot", weight: 2 }).addTo(map);
  };
  const typed = () => {
    const a = parseFloat((lat?.value || "").replace(",", "."));
    const b = parseFloat((lon?.value || "").replace(",", "."));
    return Number.isFinite(a) && Number.isFinite(b) ? [a, b] : null;
  };
  const start = config.point || typed();
  if (start) {
    place(start);
    map.setView(start, 9);
  } else {
    map.setView([50, 10], 4);
  }
  if (!config.editable || !lat || !lon) return;
  map.on("click", (event) => {
    lat.value = event.latlng.lat.toFixed(5);
    lon.value = event.latlng.lng.toFixed(5);
    place(event.latlng);
  });
  for (const input of [lat, lon]) {
    input.addEventListener("change", () => {
      const point = typed();
      if (point) {
        place(point);
        map.setView(point, Math.max(map.getZoom(), 9));
      }
    });
  }
})();
