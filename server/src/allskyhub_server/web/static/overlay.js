// Constellations over the live image (roadmap H1): one switch for the whole page,
// remembered per browser; survives the live image's htmx refresh (state on <html>).
(() => {
  const KEY = "ash-sky-overlay";
  const root = document.documentElement;
  const sync = () => {
    const on = root.classList.contains("sky-on");
    document.querySelectorAll("[data-overlay-toggle]").forEach((b) => b.setAttribute("aria-pressed", String(on)));
  };
  try {
    if (localStorage.getItem(KEY) === "1") root.classList.add("sky-on");
  } catch {
    // no storage (private window): the switch still works for this page
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-overlay-toggle]")) return;
    const on = root.classList.toggle("sky-on");
    try {
      localStorage.setItem(KEY, on ? "1" : "0");
    } catch {
      // see above
    }
    sync();
  });
  document.addEventListener("DOMContentLoaded", sync);
  document.addEventListener("htmx:afterSwap", sync);
})();
