// Draft C: the stream view (web/routes_obs.py). Every few seconds the frame is fetched
// again; a new picture is loaded first and only then swapped in, so OBS never shows a
// half-loaded image or a flash of black.
(() => {
  const body = document.body;
  const stage = document.getElementById("obs-stage");
  if (!body || !stage) return;
  const url = body.dataset.frame;
  const every = Math.max(5, Number(body.dataset.refresh) || 20) * 1000;
  const current = () => stage.querySelector(".obs-frame")?.dataset.t || "";

  async function tick() {
    try {
      const answer = await fetch(url, { cache: "no-store" });
      if (!answer.ok) return;
      const html = await answer.text();
      const next = new DOMParser().parseFromString(html, "text/html");
      const frame = next.querySelector(".obs-frame");
      if (frame && frame.dataset.t === current()) return;
      const img = next.querySelector(".obs-img");
      if (img) {
        const pre = new Image();
        pre.src = img.getAttribute("src");
        await pre.decode();
      }
      stage.replaceChildren(...next.body.childNodes);
    } catch {
      // the hub or the network is away: keep the last picture and try again
    }
  }
  setInterval(tick, every);
})();
