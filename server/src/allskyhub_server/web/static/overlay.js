// Overlays on the live image: constellations (roadmap H1) and aircraft (H3), each with its
// own switch for the whole page, remembered per browser; survives the live image's htmx
// refresh (state on <html>).
(() => {
  const KINDS = { "": ["ash-sky-overlay", "sky-on"], air: ["ash-air-overlay", "air-on"] };
  const root = document.documentElement;
  const sync = () => {
    document.querySelectorAll("[data-overlay-toggle]").forEach((b) => {
      const [, cls] = KINDS[b.dataset.overlayToggle] || KINDS[""];
      b.setAttribute("aria-pressed", String(root.classList.contains(cls)));
    });
  };
  for (const [key, cls] of Object.values(KINDS)) {
    try {
      if (localStorage.getItem(key) === "1") root.classList.add(cls);
    } catch {
      // no storage (private window): the switch still works for this page
    }
  }
  document.addEventListener("click", (e) => {
    const button = e.target.closest("[data-overlay-toggle]");
    if (!button) return;
    const [key, cls] = KINDS[button.dataset.overlayToggle] || KINDS[""];
    const on = root.classList.toggle(cls);
    try {
      localStorage.setItem(key, on ? "1" : "0");
    } catch {
      // see above
    }
    sync();
  });
  document.addEventListener("DOMContentLoaded", sync);
  document.addEventListener("htmx:afterSwap", sync);
})();
