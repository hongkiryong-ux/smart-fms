(function () {
  var STORAGE_KEY = 'fms-wo-cols:' + (window.location.pathname || '/');
  var LEGACY_EQ_KEY = 'fms-eq-col-width:' + (window.location.pathname || '/');
  /* 정비접수(source)·D-1(follow) 목록이 같은 이름의 열 너비를 함께 쓴다 */
  var SHARED_KEY = 'fms-wo-cols-shared';
  /* 정비접수 화면을 아직 열지 않았을 때 D-1이 쓰는 정비접수 기본 너비 */
  var FOLLOW_DEFAULTS = {
    _sel: 40, '상세': 40, '설비': 249, '정비의뢰내용': 125, '정비의뢰자': 140,
    '등록일': 93, '예정일': 156, '우선순위': 156, '협력사': 187, '진행': 109,
    '조치내용': 62, '작업 승인자': 62, '최종승인자': 62, '저장': 62, '삭제': 40,
    '작업허가 승인요청': 130, '잠재위험·안전대책': 200, '위험등급': 70
  };
  var MIN_W = 40;
  var MAX_W = 800;
  var EQ_INDEX = 1; /* 체크박스 다음 = 설비 */

  function clamp(n) {
    return Math.max(MIN_W, Math.min(MAX_W, Math.round(n)));
  }

  function readJSON(key) {
    try {
      return JSON.parse(localStorage.getItem(key) || 'null');
    } catch (e) {
      return null;
    }
  }

  function loadSaved(key) {
    var raw = readJSON(key);
    return Array.isArray(raw) ? raw : null;
  }

  function loadShared() {
    var raw = readJSON(SHARED_KEY);
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  }

  function colKey(th) {
    var k = th.getAttribute('data-col');
    if (k) return k;
    var text = (th.textContent || '').replace(/\s+/g, ' ').trim();
    return text || '_sel';
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

  function applyWidths(table, widths, isHidden) {
    var cg = ensureColgroup(table, widths.length);
    var total = 0;
    for (var i = 0; i < widths.length; i++) {
      var w = clamp(widths[i]);
      widths[i] = w;
      if (isHidden && isHidden(i)) w = 0;
      cg.children[i].style.width = w + 'px';
      total += w;
    }
    if (table.hasAttribute('data-col-version')) {
      /* 화면보다 좁으면 비율대로 늘려 꽉 채우고, 넓으면 가로 스크롤 */
      table.style.width = '';
      table.style.minWidth = total + 'px';
    } else {
      table.style.width = total + 'px';
      table.style.minWidth = total + 'px';
    }
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

    var share = table.getAttribute('data-col-share') || '';
    var version = table.getAttribute('data-col-version') || '';
    var storeKey = share === 'follow' ? STORAGE_KEY + ':v2' : STORAGE_KEY;
    if (version) storeKey += ':' + version;
    var keys = Array.prototype.map.call(ths, colKey);

    /* 열 숨김 (data-col-version 표에서 열 설정 메뉴로 사용) */
    var hideKey = version ? storeKey + ':hidden' : '';
    var hidden = {};
    if (hideKey) {
      var savedHidden = readJSON(hideKey);
      if (Array.isArray(savedHidden)) {
        savedHidden.forEach(function (k) { hidden[k] = true; });
      } else {
        Array.prototype.forEach.call(ths, function (th, i) {
          if (th.getAttribute('data-col-hidden') === '1') hidden[keys[i]] = true;
        });
      }
    }
    function isHidden(i) { return !!hidden[keys[i]]; }
    function paintHidden() {
      Array.prototype.forEach.call(table.rows, function (row) {
        if (row.cells.length !== ths.length) return;
        for (var i = 0; i < row.cells.length; i++) {
          row.cells[i].classList.toggle('wo-col-off', isHidden(i));
        }
      });
    }
    paintHidden();

    var saved = loadSaved(storeKey);
    var widths;
    if (saved && saved.length === ths.length) {
      widths = saved.map(clamp);
    } else {
      widths = measureWidths(ths);
      Array.prototype.forEach.call(ths, function (th, i) {
        if (isHidden(i)) widths[i] = clamp(parseInt(th.getAttribute('data-col-w') || '', 10) || 120);
      });
      if (share === 'follow' && !version) {
        keys.forEach(function (k, i) {
          if (FOLLOW_DEFAULTS[k]) widths[i] = FOLLOW_DEFAULTS[k];
        });
      }
      if (!version) {
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
    }

    /* 새 버전 표는 첫 진입 때 기본 너비를 쓰고, 이후부터 공유 너비를 따른다 */
    if (share && (!version || saved)) {
      var shared = loadShared();
      keys.forEach(function (k, i) {
        if (shared[k]) widths[i] = clamp(shared[k]);
      });
    }

    function saveAll() {
      localStorage.setItem(storeKey, JSON.stringify(widths));
    }

    function saveShared(onlyIndex) {
      if (!share) return;
      var shared = loadShared();
      keys.forEach(function (k, i) {
        if (onlyIndex == null || onlyIndex === i) shared[k] = widths[i];
      });
      localStorage.setItem(SHARED_KEY, JSON.stringify(shared));
    }

    applyWidths(table, widths, isHidden);
    paintHidden();
    saveAll();
    if (share === 'source') saveShared(null);

    if (hideKey) {
      table.woCols = {
        columns: function () {
          return Array.prototype.map.call(ths, function (th, i) {
            return {
              index: i,
              key: keys[i],
              label: th.getAttribute('data-col-label') || keys[i],
              fixed: th.getAttribute('data-col-fixed') === '1',
              hidden: isHidden(i)
            };
          });
        },
        setHidden: function (i, on) {
          if (on) hidden[keys[i]] = true; else delete hidden[keys[i]];
          localStorage.setItem(hideKey, JSON.stringify(Object.keys(hidden)));
          applyWidths(table, widths, isHidden);
          paintHidden();
        },
        reset: function () {
          localStorage.removeItem(hideKey);
          localStorage.removeItem(storeKey);
          window.location.reload();
        }
      };
    }

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
          applyWidths(table, widths, isHidden);
        }

        function onUp() {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          document.body.classList.remove('wo-col-resizing');
          saveAll();
          saveShared(index);
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
