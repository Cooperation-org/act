// Story Kit tiles: what you see you can change, saved as you go.
// ✕ archives a tile (Undo brings it back), ↩ brings an archived one back,
// and the title is typed straight onto the tile.
(function () {
  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
  function quick(tile, data) {
    return fetch(tile.dataset.quick, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
      body: JSON.stringify(data)
    }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }
  var toastEl = null, toastTimer = null;
  function toast(text, undo) {
    if (toastEl) toastEl.remove();
    toastEl = document.createElement('div'); toastEl.className = 'toast'; toastEl.setAttribute('role', 'status');
    var span = document.createElement('span'); span.textContent = text; toastEl.appendChild(span);
    if (undo) { var b = document.createElement('button'); b.type = 'button'; b.textContent = 'Undo'; b.onclick = function () { toastEl.remove(); undo(); }; toastEl.appendChild(b); }
    document.body.appendChild(toastEl);
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { if (toastEl) toastEl.remove(); }, 6000);
  }
  window.storyKitToast = toast;
  function removeTile(tile, status, message, back) {
    var parent = tile.parentNode, next = tile.nextSibling;
    tile.classList.add('gone');
    quick(tile, {status: status}).then(function () {
      setTimeout(function () { tile.remove(); }, 200);
      toast(message, function () {
        quick(tile, {status: back}).then(function () { tile.classList.remove('gone'); parent.insertBefore(tile, next); });
      });
    }).catch(function () { tile.classList.remove('gone'); toast('Could not save. Check your connection.'); });
  }
  document.addEventListener('click', function (e) {
    var x = e.target.closest('.tile .x'); if (!x) return;
    var tile = x.closest('.tile');
    if (x.classList.contains('archive')) removeTile(tile, 'hidden', 'Archived', 'kept');
    else removeTile(tile, 'kept', 'Brought back', 'hidden');
  });
  var timers = {};
  function saveTitle(h) {
    var tile = h.closest('.tile');
    quick(tile, {title: h.textContent}).catch(function () { toast('Could not save the title.'); });
  }
  document.addEventListener('input', function (e) {
    var h = e.target.closest('.tile .edit'); if (!h) return;
    var pk = h.closest('.tile').dataset.pk;
    clearTimeout(timers[pk]); timers[pk] = setTimeout(function () { saveTitle(h); }, 600);
  });
  document.addEventListener('keydown', function (e) {
    var h = e.target.closest('.tile .edit'); if (h && e.key === 'Enter') { e.preventDefault(); h.blur(); }
  });
  document.addEventListener('focusout', function (e) {
    var h = e.target.closest('.tile .edit'); if (!h) return;
    var pk = h.closest('.tile').dataset.pk; clearTimeout(timers[pk]); saveTitle(h);
  });
})();
