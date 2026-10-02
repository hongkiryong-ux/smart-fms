/** 점검일지 공통 — 드래그로 선택한 여러 칸을 Ctrl+C 로 엑셀 형식(탭/줄바꿈) 복사 */
(function () {
  if (window.__ilog2GridCopy) return;
  window.__ilog2GridCopy = true;

  let lastTable = null;

  function isSelectedCell(el) {
    if (!el || !el.classList) return false;
    for (let i = 0; i < el.classList.length; i++) {
      if (/-cell-selected$/.test(el.classList[i])) return true;
    }
    return false;
  }

  function selectedCells(scope) {
    return Array.prototype.filter.call(
      (scope || document).querySelectorAll('[class*="-cell-selected"]'),
      function (el) {
        return isSelectedCell(el) && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
      }
    );
  }

  function cellText(el) {
    if (el.type === "checkbox" || el.type === "radio") return el.checked ? (el.value || "1") : "";
    return String(el.value == null ? "" : el.value).replace(/[\t\r\n]+/g, " ");
  }

  function buildGrid(cells) {
    const rows = [];
    const rowIndex = new Map();
    cells.forEach(function (el) {
      const tr = el.closest("tr") || el.parentElement;
      if (!rowIndex.has(tr)) {
        rowIndex.set(tr, rows.length);
        rows.push([]);
      }
      rows[rowIndex.get(tr)].push(cellText(el));
    });
    return rows.map(function (cols) { return cols.join("\t"); }).join("\r\n");
  }

  function toast(message) {
    let box = document.getElementById("ilog2-copy-toast");
    if (!box) {
      box = document.createElement("div");
      box.id = "ilog2-copy-toast";
      box.style.cssText =
        "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;" +
        "padding:.45rem .9rem;border-radius:6px;background:#003876;color:#fff;" +
        "font-size:.85rem;box-shadow:0 2px 8px rgba(0,0,0,.25);pointer-events:none;transition:opacity .2s";
      document.body.appendChild(box);
    }
    box.textContent = message;
    box.style.opacity = "1";
    clearTimeout(box._timer);
    box._timer = setTimeout(function () { box.style.opacity = "0"; }, 1400);
  }

  function writeClipboard(text) {
    const active = document.activeElement;
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_e) {
      ok = false;
    }
    document.body.removeChild(area);
    if (active && typeof active.focus === "function") {
      try { active.focus({ preventScroll: true }); } catch (_e) { active.focus(); }
    }
    if (!ok && navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
    }
    return Promise.resolve(ok);
  }

  function rememberTable(e) {
    const target = e.target;
    if (!target || !target.closest) return;
    const table = target.closest("table");
    if (table && /-cell\b/.test(target.className || "")) lastTable = table;
  }

  document.addEventListener("mousedown", rememberTable, true);
  document.addEventListener("focusin", rememberTable, true);

  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
    if (String(e.key).toLowerCase() !== "c" && e.code !== "KeyC") return;
    let cells = lastTable && document.contains(lastTable) ? selectedCells(lastTable) : [];
    if (cells.length < 2) cells = selectedCells(document);
    if (cells.length < 2) return;
    e.preventDefault();
    const text = buildGrid(cells);
    writeClipboard(text).then(function (ok) {
      toast(ok ? cells.length + "칸 복사됨 · 붙여넣을 첫 칸을 누르고 Ctrl+V" : "복사 실패 — 브라우저 권한을 확인하세요");
    });
  });
})();
