// Lightbox for photos: links marked data-lightbox open in a full-screen viewer instead of
// the bare file. Links with the same data-lightbox value form a group (← → to browse).
// Zoom with wheel, pinch, double click/tap or + −; drag to pan; swipe to browse or close.
// Delegated from the document, so links swapped in by htmx work without rebinding.
(() => {
  const MAX_SCALE = 8;
  const icon = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  let box = null;
  let img = null;
  let caption = null;
  let counter = null;
  let original = null;
  let status = null;
  let links = [];
  let index = 0;
  let scale = 1;
  let tx = 0;
  let ty = 0;
  let pushed = false;

  const build = () => {
    box = document.createElement("dialog");
    box.className = "lightbox";
    box.setAttribute("aria-label", "Bildansicht");
    box.innerHTML = `
      <div class="lb-stage"><img alt=""><p class="lb-status" role="status"></p></div>
      <div class="lb-bar">
        <p class="lb-caption"><span class="lb-counter"></span><span class="lb-text"></span></p>
        <div class="lb-tools">
          <button type="button" data-lb="out" aria-label="Verkleinern" title="Verkleinern (−)">${icon("M5 12h14")}</button>
          <button type="button" data-lb="in" aria-label="Vergrößern" title="Vergrößern (+)">${icon("M12 5v14M5 12h14")}</button>
          <a class="lb-original" target="_blank" rel="noopener" aria-label="Original öffnen" title="Original öffnen">${icon("M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5")}</a>
          <button type="button" data-lb="close" autofocus aria-label="Schließen" title="Schließen (Esc)">${icon("M6 6l12 12M18 6L6 18")}</button>
        </div>
      </div>
      <button type="button" class="lb-nav lb-prev" data-lb="prev" aria-label="Vorheriges Bild">${icon("M15 5l-7 7 7 7")}</button>
      <button type="button" class="lb-nav lb-next" data-lb="next" aria-label="Nächstes Bild">${icon("M9 5l7 7-7 7")}</button>`;
    document.body.append(box);
    img = box.querySelector(".lb-stage img");
    caption = box.querySelector(".lb-text");
    counter = box.querySelector(".lb-counter");
    original = box.querySelector(".lb-original");
    status = box.querySelector(".lb-status");
    const stage = box.querySelector(".lb-stage");

    box.addEventListener("click", (e) => {
      const action = e.target.closest("[data-lb]")?.dataset.lb;
      if (action === "close") close();
      else if (action === "prev") go(-1);
      else if (action === "next") go(1);
      else if (action === "in") zoomBy(1.6);
      else if (action === "out") zoomBy(1 / 1.6);
    });
    box.addEventListener("cancel", (e) => {
      e.preventDefault();
      close();
    });
    box.addEventListener("close", finish);
    box.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "+" || e.key === "=") zoomBy(1.6);
      else if (e.key === "-") zoomBy(1 / 1.6);
      else if (e.key === "0") reset();
      else return;
      e.preventDefault();
    });
    img.addEventListener("load", () => {
      box.classList.remove("lb-loading");
      status.textContent = "";
    });
    img.addEventListener("error", () => {
      box.classList.remove("lb-loading");
      status.textContent = "Das Bild konnte nicht geladen werden.";
    });

    stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(scale * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
    }, { passive: false });
    stage.addEventListener("dblclick", (e) => {
      if (scale > 1.01) reset();
      else zoomAt(2.5, e.clientX, e.clientY);
    });
    stage.addEventListener("click", (e) => {
      // A click on the dark area beside the picture closes, like most viewers.
      if (scale <= 1.01 && !moved && outside(e)) close();
    });

    // Pointers: one finger/mouse pans (or swipes when not zoomed), two pinch.
    const pointers = new Map();
    let start = null;
    let moved = false;
    let lastTap = 0;
    stage.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      try {
        stage.setPointerCapture(e.pointerId);
      } catch {
        // Synthetic or already released pointer: panning still works without capture.
      }
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      moved = false;
      start = snapshot(pointers);
    });
    stage.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId) || !start) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const now = snapshot(pointers);
      if (now.count !== start.count) {
        start = now;
        return;
      }
      const dx = now.x - start.x;
      const dy = now.y - start.y;
      if (Math.abs(dx) + Math.abs(dy) > 6) moved = true;
      if (now.count === 2) {
        const s = Math.min(MAX_SCALE, Math.max(1, start.scale * (now.dist / start.dist)));
        const r = s / start.scale;
        const c = centre();
        tx = now.x - c.x - (start.x - c.x - start.tx) * r;
        ty = now.y - c.y - (start.y - c.y - start.ty) * r;
        scale = s;
        apply();
      } else if (scale > 1.01) {
        tx = start.tx + dx;
        ty = start.ty + dy;
        apply();
      } else {
        // Not zoomed: the picture follows the finger a little, as a hint for the swipe.
        img.style.transform = `translate(${dx * 0.4}px, ${Math.max(0, dy) * 0.4}px)`;
      }
    });
    const up = (e) => {
      if (!pointers.has(e.pointerId) || !start) return;
      const now = snapshot(pointers);
      pointers.delete(e.pointerId);
      if (pointers.size > 0) {
        start = snapshot(pointers);
        return;
      }
      const dx = now.x - start.x;
      const dy = now.y - start.y;
      start = null;
      if (scale <= 1.01 && now.count === 1) {
        if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) {
          go(dx < 0 ? 1 : -1);
          return;
        }
        if (dy > 100 && dy > Math.abs(dx)) {
          close();
          return;
        }
      }
      if (e.pointerType !== "mouse" && !moved) {
        // Double tap on touch screens (dblclick does not fire reliably there).
        const t = Date.now();
        if (t - lastTap < 300) {
          if (scale > 1.01) reset();
          else zoomAt(2.5, now.x, now.y);
          lastTap = 0;
          return;
        }
        lastTap = t;
      }
      apply();
    };
    stage.addEventListener("pointerup", up);
    stage.addEventListener("pointercancel", up);
    window.addEventListener("resize", () => box.open && apply());
    window.addEventListener("popstate", () => {
      if (box.open) {
        pushed = false;
        close();
      }
    });
  };

  const snapshot = (pointers) => {
    const p = [...pointers.values()];
    const x = p.reduce((a, q) => a + q.x, 0) / p.length;
    const y = p.reduce((a, q) => a + q.y, 0) / p.length;
    const dist = p.length > 1 ? Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) || 1 : 1;
    return { count: p.length, x, y, dist, scale, tx, ty };
  };

  // Whether a click hit the dark margin around the picture (the element fills the stage).
  const outside = (e) => {
    const r = img.getBoundingClientRect();
    const fit = img.naturalWidth ? Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight) : 1;
    const w = img.naturalWidth * fit;
    const h = img.naturalHeight * fit;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    return Math.abs(e.clientX - cx) > w / 2 || Math.abs(e.clientY - cy) > h / 2;
  };

  const centre = () => {
    const r = box.querySelector(".lb-stage").getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  };

  const apply = () => {
    // Keep the picture on screen: no panning past its edges.
    const stage = box.querySelector(".lb-stage").getBoundingClientRect();
    // The element fills the stage; the picture inside is "contain"-fitted.
    const fit = img.naturalWidth && img.naturalHeight
      ? Math.min(stage.width / img.naturalWidth, stage.height / img.naturalHeight)
      : 0;
    const w = (fit ? img.naturalWidth * fit : stage.width) * scale;
    const h = (fit ? img.naturalHeight * fit : stage.height) * scale;
    const mx = Math.max(0, (w - stage.width) / 2);
    const my = Math.max(0, (h - stage.height) / 2);
    tx = Math.min(mx, Math.max(-mx, tx));
    ty = Math.min(my, Math.max(-my, ty));
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    box.classList.toggle("lb-zoomed", scale > 1.01);
  };

  const zoomAt = (s, x, y) => {
    s = Math.min(MAX_SCALE, Math.max(1, s));
    const c = centre();
    const r = s / scale;
    tx = x - c.x - (x - c.x - tx) * r;
    ty = y - c.y - (y - c.y - ty) * r;
    scale = s;
    apply();
  };

  const zoomBy = (f) => {
    const c = centre();
    zoomAt(scale * f, c.x, c.y);
  };

  const reset = () => {
    scale = 1;
    tx = 0;
    ty = 0;
    apply();
  };

  const textOf = (a) => {
    const thumb = a.querySelector("img");
    return a.dataset.caption || a.title || thumb?.alt || "";
  };

  const show = (i) => {
    index = (i + links.length) % links.length;
    const a = links[index];
    scale = 1;
    tx = 0;
    ty = 0;
    img.style.transform = "";
    box.classList.remove("lb-zoomed");
    box.classList.add("lb-loading");
    status.textContent = "";
    img.alt = textOf(a);
    img.src = a.href;
    if (img.complete && img.naturalWidth) box.classList.remove("lb-loading");
    caption.textContent = textOf(a);
    counter.textContent = links.length > 1 ? `${index + 1} / ${links.length}` : "";
    original.href = a.href;
    box.classList.toggle("lb-single", links.length < 2);
    // Fetch the neighbours ahead, so browsing feels instant.
    for (const n of [index + 1, index - 1]) {
      if (links.length > 1) new Image().src = links[(n + links.length) % links.length].href;
    }
  };

  const go = (step) => {
    if (links.length > 1) show(index + step);
  };

  const open = (a) => {
    if (!box) build();
    const group = a.dataset.lightbox;
    links = group
      ? [...document.querySelectorAll("a[data-lightbox]")].filter((l) => l.dataset.lightbox === group)
      : [a];
    show(Math.max(0, links.indexOf(a)));
    document.documentElement.classList.add("lb-open");
    box.showModal();
    // The phone's back button closes the viewer instead of leaving the page.
    history.pushState({ lightbox: true }, "");
    pushed = true;
  };

  const close = () => {
    if (box.open) box.close();
  };

  const finish = () => {
    document.documentElement.classList.remove("lb-open");
    img.removeAttribute("src");
    if (pushed) {
      pushed = false;
      history.back();
    }
  };

  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-lightbox]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    open(a);
  });
})();
