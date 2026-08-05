(function () {
  var STORAGE_PREFIX = 'fms-wo-col:';
  var LEGACY_EQ_KEY = 'fms-eq-col-width:';
  var path = window.location.pathname || '/';
  var minByCol = {
    eq: 72,
    date: 72,
    sched: 96,
    priority: 48,
    partner: 72,
    action: 10 * 8, /* ~10ch */
    approver: 56
  };
  var maxW = 560;
  var defaultEq = 140;

  function clamp(col, n) {
    var minW = minByCol[col] != null ? minByCol[col] : 48;
    return Math.max(minW, Math.min(maxW, n));
  }

  function storageKey(col) {
    return STORAGE_PREFIX + path + ':' + col;
  }

  function legacyEqWidth() {
    var v = parseInt(localStorage.getItem(LEGACY_EQ_KEY + path) || '', 10);
    return isNaN(v) ? null : v;
  }

  function getSavedWidth(col) {
    var v = parseInt(localStorage.getItem(storageKey(col)) || '', 10);
    if (!isNaN(v)) return clamp(col, v);
    if (col === 'eq') {
      var legacy = legacyEqWidth();
      if (legacy != null) return clamp(col, legacy);
      return defaultEq;
    }
    return null;
  }

  function applyWidth(col, px) {
    var w = clamp(col, px);
    document.querySelectorAll('.wo-list-table').forEach(function (table) {
      table.style.setProperty('--wo-col-' + col, w + 'px');
      /* 이전 설비 변수 호환 */
      if (col === 'eq') {
        table.style.setProperty('--wo-eq-col-width', w + 'px');
      }
    });
    localStorage.setItem(storageKey(col), String(w));
    if (col === 'eq') {
      localStorage.setItem(LEGACY_EQ_KEY + path, String(w));
    }
  }

  function applySaved() {
    document.querySelectorAll('.wo-list-table thead th.wo-col-resizable').forEach(function (th) {
      var col = th.getAttribute('data-col');
      if (!col) return;
      var saved = getSavedWidth(col);
      if (saved == null) return;
      document.querySelectorAll('.wo-list-table').forEach(function (table) {
        table.style.setProperty('--wo-col-' + col, saved + 'px');
        if (col === 'eq') {
          table.style.setProperty('--wo-eq-col-width', saved + 'px');
        }
      });
    });
  }

  function initResizers() {
    applySaved();

    document.querySelectorAll('.wo-list-table thead th.wo-col-resizable').forEach(function (th) {
      var col = th.getAttribute('data-col');
      if (!col || th.querySelector('.wo-col-resizer')) return;

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
          applyWidth(col, startW + (ev.clientX - startX));
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
