// Foldable cards and settings groups (<details data-fold>): each browser remembers what
// was open; a link to a card (#teilen, #discord) opens it. data-fold-keep: the server's
// choice wins (after an error every settings group is open).
(() => {
  const PREFIX = "ash-fold:";
  const read = (key) => {
    try {
      return localStorage.getItem(PREFIX + key);
    } catch {
      return null;
    }
  };
  const write = (key, open) => {
    try {
      localStorage.setItem(PREFIX + key, open ? "1" : "0");
    } catch {
      // Private window or blocked storage: the page works, it just forgets.
    }
  };
  for (const details of document.querySelectorAll("details[data-fold]")) {
    const key = details.dataset.fold;
    const stored = read(key);
    if (stored !== null && !details.hasAttribute("data-fold-keep")) {
      details.open = stored === "1";
    }
    details.addEventListener("toggle", () => write(key, details.open));
  }
  const openTarget = () => {
    const id = decodeURIComponent(location.hash.slice(1));
    const target = id && document.getElementById(id);
    if (target instanceof HTMLDetailsElement) {
      target.open = true;
      target.scrollIntoView({ block: "start" });
    }
  };
  openTarget();
  addEventListener("hashchange", openTarget);
})();
