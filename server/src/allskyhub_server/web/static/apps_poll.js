// An app card polls itself every 10 s while something of it is on its way (_app_card.html).
// While someone types into one of its forms the poll waits, so the form is not swapped
// away under their fingers (Benjamin, 2026-11-05).
(() => {
  const typing = (card) => {
    const active = document.activeElement;
    if (active && card.contains(active) && active.matches("input, textarea, select")) {
      return true;
    }
    return [...card.querySelectorAll("input:not([type=hidden]), textarea")].some(
      (field) => field.value !== field.defaultValue,
    );
  };
  document.addEventListener("htmx:beforeRequest", (event) => {
    const card = event.detail.elt;
    if (card instanceof Element && card.matches("section[id^='app-']") && typing(card)) {
      event.preventDefault();
    }
  });
})();
