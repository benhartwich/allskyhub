// Plus checkout (roadmap N7): opens Paddle's overlay for the transaction the hub created.
// On Paddle's default payment link (no transaction here) Paddle.js opens the one named in
// the address (_ptxn) by itself.
(function () {
  "use strict";
  var box = document.getElementById("plus-checkout");
  if (!box || typeof Paddle === "undefined") return;
  var config = JSON.parse(box.dataset.config);
  if (config.env === "sandbox") Paddle.Environment.set("sandbox");
  Paddle.Initialize({
    token: config.token,
    checkout: {
      settings: { displayMode: "overlay", locale: "de", successUrl: config.success },
    },
  });
  if (config.transaction) Paddle.Checkout.open({ transactionId: config.transaction });
})();
