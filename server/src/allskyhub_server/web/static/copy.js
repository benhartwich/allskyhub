// Copy buttons (<button data-copy="…">): the value to the clipboard, else selected for
// copying by hand (older browsers, or a page that is not https).
(() => {
  document.addEventListener("click", async (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-copy]") : null;
    if (!button) return;
    const text = button.getAttribute("data-copy") || "";
    try {
      await navigator.clipboard.writeText(text);
      const before = button.textContent;
      button.textContent = document.documentElement.lang === "en" ? "copied" : "kopiert";
      setTimeout(() => {
        button.textContent = before;
      }, 1500);
    } catch {
      const code = button.previousElementSibling;
      if (code) window.getSelection()?.selectAllChildren(code);
    }
  });
})();
