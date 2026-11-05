// uPlot formats numbers with the browser's language when it loads
// (new Intl.NumberFormat(navigator.language)). An invalid tag, such as "en-US@posix" from a
// POSIX locale, throws there, uPlot never defines itself and the charts stay at "Lade
// Messdaten …". Load this before uPlot: such a browser then reports de-DE.
(() => {
  try {
    new Intl.NumberFormat(navigator.language);
  } catch {
    try {
      Object.defineProperty(navigator, "language", { value: "de-DE", configurable: true });
    } catch {
      // Not redefinable here: charts.js says that the charts could not load.
    }
  }
})();
