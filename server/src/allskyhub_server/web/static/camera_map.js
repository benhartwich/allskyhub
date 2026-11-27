// The camera map (roadmap C5, H4): Leaflet with tiles and place search through the hub
// (routes_public.py), a popup per camera before its page, and the light-pollution view.
(() => {
  const el = document.getElementById("map");
  const raw = document.getElementById("map-config");
  if (!el || !raw || !window.L) return;
  const config = JSON.parse(raw.textContent);
  const sqm = config.layer === "sqm";
  const KEY = "ash-map-base";
  // German or English like the page (<html lang>, web/lang.py).
  const en = document.documentElement.lang === "en";
  const t = (de, english) => (en ? english : de);

  // The zoom at which the whole world fits the map's width (2026-11-24: cameras in
  // Germany, Nevada and New Zealand did not fit at the old fixed minimum of 2).
  const worldZoom = () => Math.max(0, Math.floor(Math.log2(Math.max(el.clientWidth, 256) / 256)));
  const map = L.map(el, { maxZoom: config.maxZoom, minZoom: worldZoom(), worldCopyJump: true });
  window.addEventListener("resize", () => map.setMinZoom(worldZoom()));
  map.setView([config.view[0], config.view[1]], config.view[2]);
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');

  const bases = {};
  for (const [key, layer] of Object.entries(config.layers)) {
    bases[key] = L.tileLayer(`/karte/kacheln/${key}/{z}/{x}/{y}`, {
      maxZoom: config.maxZoom,
      maxNativeZoom: layer.maxZoom,
      attribution: layer.credit,
    });
  }
  let current = null;
  const show = (key) => {
    if (!bases[key]) key = "karte";
    if (current) map.removeLayer(bases[current]);
    bases[key].addTo(map);
    current = key;
    document.querySelectorAll("[data-map-base]").forEach((b) => {
      const on = b.dataset.mapBase === key;
      b.classList.toggle("secondary", !on);
      b.setAttribute("aria-pressed", String(on));
    });
    el.classList.toggle("map-dark", key !== "karte");
  };
  let start = sqm ? "nacht" : "karte";
  if (!sqm) {
    try {
      start = localStorage.getItem(KEY) || start;
    } catch {
      // no storage: the default
    }
  }
  show(start);
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-map-base]");
    if (!b) return;
    show(b.dataset.mapBase);
    if (!sqm) {
      try {
        localStorage.setItem(KEY, current);
      } catch {
        // see above
      }
    }
  });

  // A popup first, the camera's page from there. Built from nodes: titles are owners' text.
  const node = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  };
  const popup = (c) => {
    const box = node("div", "map-popup");
    if (c.image) {
      const img = node("img");
      img.src = `/sky/${encodeURIComponent(c.slug)}/latest.jpg`;
      img.alt = `${t("Himmel über", "Sky over")} ${c.title}`;
      img.loading = "lazy";
      box.append(img);
    }
    box.append(node("strong", "", c.title));
    if (c.place) box.append(node("span", "muted", c.place));
    if (sqm || c.sqm !== null) box.append(node("span", "map-sqm", c.sqm_text));
    // On /netz a station can be on the map without a page of its own.
    if (c.slug) {
      const link = node("a", "button small", c.kind === "station" ? t("Zur Station →", "To the station →") : t("Zur Kamera →", "To the camera →"));
      link.href = `/sky/${encodeURIComponent(c.slug)}`;
      box.append(link);
    }
    return box;
  };
  const markers = [];
  for (const c of config.cameras) {
    const marker = L.circleMarker([c.lat, c.lon], {
      radius: 9,
      weight: 2,
      className: `map-dot${sqm ? ` ${c.sqm_class}` : ""}${c.kind ? ` kind-${c.kind}` : ""}${c.active === false ? " idle" : ""}`,
    });
    marker.bindPopup(() => popup(c), { maxWidth: 260, minWidth: 200 });
    marker.bindTooltip(c.title, { direction: "top", offset: [0, -8] });
    marker.addTo(map);
    markers.push(marker);
  }
  // Reports by eye from the app: small squares, coloured like the cameras.
  for (const r of config.reports || []) {
    const box = node("div", "map-popup");
    box.append(node("strong", "", t("Gemeldet mit bloßem Auge", "Reported by naked eye")));
    box.append(node("span", "map-sqm", r.sqm_text));
    L.circleMarker([r.lat, r.lon], { radius: 5, weight: 1, className: `map-report ${r.sqm_class}` })
      .bindPopup(box, { maxWidth: 240 })
      .addTo(map);
  }
  if (markers.length && !new URLSearchParams(location.search).has("region")) {
    const bounds = L.featureGroup(markers).getBounds();
    // Cameras on several continents: the whole world, not a padded slice of it.
    if (bounds.getEast() - bounds.getWest() > 120) map.setView([20, 10], worldZoom());
    else map.fitBounds(bounds.pad(0.4), { maxZoom: 6 });
  }

  // Place search (Nominatim through the hub).
  const form = document.querySelector("[data-map-search]");
  const results = document.querySelector("[data-map-results]");
  const go = (p) => {
    results.hidden = true;
    if (p.bbox) {
      map.fitBounds([[p.bbox[0], p.bbox[2]], [p.bbox[1], p.bbox[3]]], { maxZoom: 9 });
    } else {
      map.setView([p.lat, p.lon], 9);
    }
  };
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = form.q.value.trim();
    if (q.length < 2) return;
    results.replaceChildren(node("li", "muted", t("Suche …", "Searching …")));
    results.hidden = false;
    let places = [];
    try {
      const r = await fetch(`/karte/suche?q=${encodeURIComponent(q)}`);
      places = r.ok ? await r.json() : [];
      if (!r.ok) {
        results.replaceChildren(node("li", "muted", r.status === 429 ? t("Zu viele Suchen, bitte kurz warten.", "Too many searches, please wait a moment.") : t("Die Suche geht gerade nicht.", "Search is not working right now.")));
        return;
      }
    } catch {
      results.replaceChildren(node("li", "muted", t("Die Suche geht gerade nicht.", "Search is not working right now.")));
      return;
    }
    if (!places.length) {
      results.replaceChildren(node("li", "muted", t("Nichts gefunden.", "Nothing found.")));
      return;
    }
    if (places.length === 1) {
      go(places[0]);
      return;
    }
    results.replaceChildren(
      ...places.map((p) => {
        const li = node("li");
        const b = node("button", "link", p.name);
        b.type = "button";
        b.addEventListener("click", () => go(p));
        li.append(b);
        return li;
      }),
    );
  });
})();
