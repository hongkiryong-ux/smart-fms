(function () {
  var STORAGE_KEY = 'fms-wo-cols:' + (window.location.pathname || '/');
  var LEGACY_EQ_KEY = 'fms-eq-col-width:' + (window.location.pathname || '/');
  var MIN_W = 40;
  var MAX_W = 800;
  var EQ_INDEX = 1; /* 체크박스 다음 = 설비 */

  function clamp(n) {
    return Math.max(MIN_W, Math.min(MAX_W, Math.round(n)));
  }

  function loadSaved() {
    try {
      var raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      if (Array.isArray(raw)) return raw;
    } catch (e) { /* ignore */ }
    return null;
  }

  function saveWidths(widths) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
  }

  function ensureColgroup(table, count) {
    var cg = table.querySelector('colgroup.wo-colgroup');
    if (!cg) {
      cg = document.createElement('colgroup');
      cg.className = 'wo-colgroup';
      table.insertBefore(cg, table.firstChild);
    }
    while (cg.children.length < count) {
      cg.appendChild(document.createElement('col'));
    }
    while (cg.children.length > count) {
      cg.removeChild(cg.lastChild);
    }
    return cg;
  }

  function applyWidths(table, widths) {
    var cg = ensureColgroup(table, widths.length);
    var total = 0;
    for (var i = 0; i < widths.length; i++) {
      var w = clamp(widths[i]);
      widths[i] = w;
      cg.children[i].style.width = w + 'px';
      total += w;
    }
    table.style.width = total + 'px';
    table.style.minWidth = total + 'px';
  }

  function measureWidths(ths) {
    var widths = [];
    for (var i = 0; i < ths.length; i++) {
      widths.push(clamp(ths[i].getBoundingClientRect().width || 80));
    }
    return widths;
  }

  function initTable(table) {
    var ths = table.querySelectorAll('thead th');
    if (!ths.length) return;

    var saved = loadSaved();
    var widths;
    if (saved && saved.length === ths.length) {
      widths = saved.map(clamp);
    } else {
      widths = measureWidths(ths);
      /* 예전 설비 열 너비 설정 이어받기 */
      var legacyEq = parseInt(localStorage.getItem(LEGACY_EQ_KEY) || '', 10);
      if (!isNaN(legacyEq) && widths.length > EQ_INDEX) {
        widths[EQ_INDEX] = clamp(legacyEq);
      }
      if (saved && saved.length !== ths.length) {
        /* 열 개수가 바뀌면 겹치는 구간만 복원 */
        for (var i = 0; i < Math.min(saved.length, widths.length); i++) {
          if (saved[i]) widths[i] = clamp(saved[i]);
        }
      }
    }

    applyWidths(table, widths);
    saveWidths(widths);

    Array.prototype.forEach.call(ths, function (th, index) {
      th.classList.add('wo-col-head');
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
        var startW = widths[index];

        function onMove(ev) {
          widths[index] = clamp(startW + (ev.clientX - startX));
          applyWidths(table, widths);
        }

        function onUp() {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          document.body.classList.remove('wo-col-resizing');
          saveWidths(widths);
          if (index === EQ_INDEX) {
            localStorage.setItem(LEGACY_EQ_KEY, String(widths[index]));
          }
        }

        document.body.classList.add('wo-col-resizing');
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
    });
  }

  function initResizers() {
    document.querySelectorAll('.wo-list-table').forEach(initTable);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initResizers);
  } else {
    initResizers();
  }
})();
