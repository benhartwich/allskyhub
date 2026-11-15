// Draft C: the address of the stream view from the boxes on /cameras/<id>/stream.
(() => {
  const builder = document.querySelector("[data-obs-builder]");
  if (!builder) return;
  const field = builder.querySelector("[data-obs-address]");
  const copy = builder.querySelector("[data-obs-copy]");
  const update = () => {
    const layers = [...builder.querySelectorAll('input[name="ebene"]:checked')].map((b) => b.value);
    const params = [`ebenen=${layers.join(",")}`];
    if (builder.querySelector('input[name="text"]').checked) params.push("text=aus");
    if (builder.querySelector('input[name="rotlicht"]').checked) params.push("rotlicht=1");
    const address = `${builder.dataset.base}?${params.join("&")}`;
    field.value = address;
    copy.setAttribute("data-copy", address);
  };
  builder.addEventListener("change", update);
  update();
})();
