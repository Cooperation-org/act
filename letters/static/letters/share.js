// Progressive enhancement for the share icons (see letters/templatetags/share.py).
// Mastodon: ask once for the home server and open its /share page directly.
// Instagram: no share intent exists, so copy the message before opening Instagram.
(function () {
  var boxes = document.querySelectorAll(".share");
  if (!boxes.length) return;

  function note(box, msg) {
    var el = box.querySelector(".note");
    if (!el) return;
    el.textContent = msg;
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.textContent = ""; }, 6000);
  }

  function mastodonHost() {
    var saved = "";
    try { saved = localStorage.getItem("mastodonHost") || ""; } catch (e) {}
    var host = window.prompt("Your Mastodon server (for example mastodon.social)", saved);
    if (host === null) return null;
    host = host.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (!host) return "";
    try { localStorage.setItem("mastodonHost", host); } catch (e) {}
    return host;
  }

  Array.prototype.forEach.call(boxes, function (box) {
    var text = box.getAttribute("data-text") || "";

    var mastodon = box.querySelector("a.mastodon");
    if (mastodon) mastodon.addEventListener("click", function (ev) {
      ev.preventDefault();
      var host = mastodonHost();
      if (host === null) return;
      var target = host ? "https://" + host + "/share?text=" + encodeURIComponent(text) : mastodon.href;
      window.open(target, "_blank", "noopener");
    });

    var instagram = box.querySelector("a.instagram");
    if (instagram) instagram.addEventListener("click", function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          note(box, "Copied the message. Paste it into your Instagram post.");
        }, function () {});
      }
    });
  });
})();
