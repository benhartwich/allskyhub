// The camera map (roadmap C5, H4): Leaflet with tiles and place search through the hub
// (routes_public.py), a popup per camera before its page, and the light-pollution view.
(() => {
  const el = document.getElementById("map");
  const raw = document.getElementById("map-config");
  if (!el || !raw || !window.L) return;
  const config = JSON.parse(raw.textContent);
  const sqm = config.layer === "sqm";
  const KEY = "ash-map-base";

  const map = L.map(el, { maxZoom: config.maxZoom, minZoom: 2, worldCopyJump: true });
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
      img.alt = `Himmel über ${c.title}`;
      img.loading = "lazy";
      box.append(img);
    }
    box.append(node("strong", "", c.title));
    if (c.place) box.append(node("span", "muted", c.place));
    if (sqm || c.sqm !== null) box.append(node("span", "map-sqm", c.sqm_text));
    const link = node("a", "button small", "Zur Kamera →");
    link.href = `/sky/${encodeURIComponent(c.slug)}`;
    box.append(link);
    return box;
  };
  const markers = [];
  for (const c of config.cameras) {
    const marker = L.circleMarker([c.lat, c.lon], {
      radius: 9,
      weight: 2,
      className: `map-dot${sqm ? ` ${c.sqm_class}` : ""}`,
    });
    marker.bindPopup(() => popup(c), { maxWidth: 260, minWidth: 200 });
    marker.bindTooltip(c.title, { direction: "top", offset: [0, -8] });
    marker.addTo(map);
    markers.push(marker);
  }
  if (markers.length && !new URLSearchParams(location.search).has("region")) {
    map.fitBounds(L.featureGroup(markers).getBounds().pad(0.4), { maxZoom: 6 });
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
    results.replaceChildren(node("li", "muted", "Suche …"));
    results.hidden = false;
    let places = [];
    try {
      const r = await fetch(`/karte/suche?q=${encodeURIComponent(q)}`);
      places = r.ok ? await r.json() : [];
      if (!r.ok) {
        results.replaceChildren(node("li", "muted", r.status === 429 ? "Zu viele Suchen, bitte kurz warten." : "Die Suche geht gerade nicht."));
        return;
      }
    } catch {
      results.replaceChildren(node("li", "muted", "Die Suche geht gerade nicht."));
      return;
    }
    if (!places.length) {
      results.replaceChildren(node("li", "muted", "Nichts gefunden."));
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
