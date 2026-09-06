(function () {
  var canvas = document.getElementById("pad");
  if (!canvas || typeof SignaturePad === "undefined") return;
  var input = document.getElementById("id_drawn");
  var pad = new SignaturePad(canvas, { penColor: "#1d1a4b", minWidth: 0.8, maxWidth: 2.4 });

  function resize() {
    var ratio = Math.max(window.devicePixelRatio || 1, 1);
    var data = pad.toData();
    canvas.width = canvas.offsetWidth * ratio;
    canvas.height = canvas.offsetHeight * ratio;
    canvas.getContext("2d").scale(ratio, ratio);
    pad.clear();
    if (data.length) pad.fromData(data);
  }
  window.addEventListener("resize", resize);
  resize();

  document.getElementById("clear").addEventListener("click", function () {
    pad.clear();
    input.value = "";
  });
  canvas.closest("form").addEventListener("submit", function () {
    input.value = pad.isEmpty() ? "" : pad.toDataURL("image/png");
  });
})();
