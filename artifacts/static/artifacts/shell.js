// Story Kit shell.
// Sheets: any [data-open="id"] opens that dialog as a sheet; Done, the backdrop, Esc, or a swipe down on the grabber closes it.
(function () {
  document.addEventListener('click', function (e) {
    var o = e.target.closest('[data-open]');
    if (o) { var d = document.getElementById(o.dataset.open); if (d && d.showModal) d.showModal(); return; }
    if (e.target.closest('[data-close]')) { var dlg = e.target.closest('dialog'); if (dlg) dlg.close(); return; }
    if (e.target.tagName === 'DIALOG') {
      var r = e.target.getBoundingClientRect();
      if (e.clientY < r.top || e.clientY > r.bottom || e.clientX < r.left || e.clientX > r.right) e.target.close();
    }
  });
  document.querySelectorAll('dialog.sheet').forEach(function (d) {
    var top = d.querySelector('.sheet-top'); if (!top) return;
    var y0 = null, dy = 0, t0 = 0;
    top.addEventListener('pointerdown', function (e) { if (e.target.closest('button')) return; y0 = e.clientY; t0 = e.timeStamp; dy = 0; top.setPointerCapture(e.pointerId); d.style.transition = 'none'; d.style.animation = 'none'; });
    top.addEventListener('pointermove', function (e) { if (y0 === null) return; dy = Math.max(0, e.clientY - y0); d.style.transform = 'translateY(' + dy + 'px)'; });
    function end(e) {
      if (y0 === null) return;
      var fast = dy / Math.max(1, e.timeStamp - t0) > 0.6; y0 = null;
      d.style.transition = 'transform .3s cubic-bezier(.32, .72, 0, 1)';
      if (dy > 90 || fast) { d.style.transform = 'translateY(100%)'; setTimeout(function () { d.close(); d.style.transform = ''; d.style.transition = ''; d.style.animation = ''; }, 200); }
      else { d.style.transform = ''; setTimeout(function () { d.style.transition = ''; d.style.animation = ''; }, 320); }
    }
    top.addEventListener('pointerup', end); top.addEventListener('pointercancel', end);
  });
})();
