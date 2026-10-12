// Loop of the last hour on public sky pages: thumbnails load on first play, then cycle.
document.addEventListener("DOMContentLoaded", () => {
  const box = document.querySelector("[data-loop]");
  const button = document.querySelector("[data-loop-toggle]");
  const label = document.querySelector("[data-loop-label]");
  if (!box || !button) return;
  const frames = Array.from(box.querySelectorAll("img"));
  let index = frames.length - 1;
  let timer = null;
  const show = (i) => {
    frames[index].classList.remove("on");
    index = i;
    frames[index].classList.add("on");
    if (label) label.textContent = `${frames[index].dataset.time} Uhr`;
  };
  button.addEventListener("click", () => {
    if (timer) {
      clearInterval(timer);
      timer = null;
      button.textContent = "▶ Abspielen";
      button.setAttribute("aria-pressed", "false");
      return;
    }
    for (const img of frames) {
      if (img.dataset.src) {
        img.src = img.dataset.src;
        delete img.dataset.src;
      }
    }
    button.textContent = "❚❚ Anhalten";
    button.setAttribute("aria-pressed", "true");
    timer = setInterval(() => show((index + 1) % frames.length), 250);
  });
});
