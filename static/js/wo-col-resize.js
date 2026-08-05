(function () {
  var STORAGE_PREFIX = 'fms-eq-col-width:';
  var key = STORAGE_PREFIX + (window.location.pathname || '/');
  var defaultWidth = 140;
  var minW = 72;
  var maxW = 560;

  function clamp(n) {
    return Math.max(minW, Math.min(maxW, n));
  }

  function getWidth() {
    var v = parseInt(localStorage.getItem(key) || '', 10);
    return isNaN(v) ? defaultWidth : clamp(v);
  }

  function applyWidth(px) {
    var w = clamp(px);
    document.querySelectorAll('.wo-list-table').forEach(function (table) {
      table.style.setProperty('--wo-eq-col-width', w + 'px');
    });
    localStorage.setItem(key, String(w));
  }

  function initResizers() {
    applyWidth(getWidth());

    document.querySelectorAll('.wo-list-table thead th.wo-eq-col-head').forEach(function (th) {
      if (th.querySelector('.wo-col-resizer')) return;
      var handle = document.createElement('span');
      handle.className = 'wo-col-resizer';
      handle.setAttribute('aria-hidden', 'true');
      handle.title = '열 너비 조절';
      th.appendChild(handle);

      handle.addEventListener('mousedown', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var startX = e.clientX;
        var startW = th.getBoundingClientRect().width;

        function onMove(ev) {
          applyWidth(startW + (ev.clientX - startX));
        }

        function onUp() {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          document.body.classList.remove('wo-col-resizing');
        }

        document.body.classList.add('wo-col-resizing');
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initResizers);
  } else {
    initResizers();
  }
})();
