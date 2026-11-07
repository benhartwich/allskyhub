// The app on Android phones (Benjamin, 2026-11-07): a slim bar above the page, as Safari
// shows on the iPhone by itself (apple-itunes-app in base.html). Closed, it stays away for
// 30 days; without storage it simply comes back.
(() => {
  const KEY = "allskyhub-app-banner";
  const DAYS = 30;
  if (!/Android/i.test(navigator.userAgent)) return;
  try {
    const closed = Number(localStorage.getItem(KEY) || 0);
    if (Date.now() - closed < DAYS * 86400000) return;
  } catch {
    // no storage: show it
  }
  const show = () => {
    const bar = document.getElementById("app-banner");
    if (!bar) return;
    bar.hidden = false;
    bar.querySelector("[data-app-banner-close]")?.addEventListener("click", () => {
      bar.hidden = true;
      try {
        localStorage.setItem(KEY, String(Date.now()));
      } catch {
        // nothing to remember it in
      }
    });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", show);
  else show();
})();
