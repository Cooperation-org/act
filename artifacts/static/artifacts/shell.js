// Story Kit shell: the More menu in the tab bar closes the way an iOS menu does.
(function () {
  var more = document.querySelector('.tabs .more');
  if (!more) return;
  function close() { more.removeAttribute('open'); }
  document.addEventListener('click', function (e) { if (more.open && !more.contains(e.target)) close(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && more.open) { close(); more.querySelector('summary').focus(); } });
  more.addEventListener('click', function (e) { if (e.target.closest('.more-menu a')) close(); });
})();
