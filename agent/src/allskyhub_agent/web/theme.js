// Red night theme (packages/design): set before the page paints, switched by the button with
// data-theme-toggle, remembered in this browser only. Same key as the hub ("ash-theme").
(() => {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem("ash-theme"); } catch { /* private mode */ }
  if (saved === "red") root.dataset.theme = "red";
  document.addEventListener("DOMContentLoaded", () => {
    for (const button of document.querySelectorAll("[data-theme-toggle]")) {
      button.setAttribute("aria-pressed", String(root.dataset.theme === "red"));
      button.addEventListener("click", () => {
        const red = root.dataset.theme !== "red";
        if (red) root.dataset.theme = "red"; else delete root.dataset.theme;
        button.setAttribute("aria-pressed", String(red));
        try { localStorage.setItem("ash-theme", red ? "red" : "dark"); } catch { /* private mode */ }
      });
    }
  });
})();
