/** 제철소본부 일지 — 자동저장 · 설비 utility 계산 · 드래그 · 엑셀 붙여넣기 · 삭제 */
(function () {
  const form = document.querySelector(".sw-daily-form");
  const statusEl = document.getElementById("sw-save-status");
  if (!form) return;

  let debounceTimer = null;
  let saving = false;
  let pending = false;
  let activeSelector = null;
  const DEBOUNCE_MS = 600;

  function setStatus(text, cls) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.className = "sw-save-status " + (cls || "");
  }

  function parseNum(v) {
    if (v == null || v === "") return null;
    const n = parseFloat(String(v).replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  }

  function fmtNum(n) {
    if (n == null) return "";
    return Math.abs(n - Math.round(n)) < 0.05 ? String(Math.round(n)) : String(Math.round(n * 10) / 10);
  }

  function calcUtility() {
    form.querySelectorAll(".sw-utility-row").forEach(function (row) {
      const uid = row.dataset.utility;
      const scale = parseFloat(row.dataset.multiplier || "1") || 1;
      const prev = parseNum(form.querySelector('[name="f__utility__' + uid + '__prev"]')?.value);
      const today = parseNum(form.querySelector('[name="f__utility__' + uid + '__today"]')?.value);
      let daily = null;
      if (prev != null && today != null) daily = (today - prev) * scale;
      const dailyIn = row.querySelector('[name="f__utility__' + uid + '__daily"]');
      const monthlyIn = row.querySelector('[name="f__utility__' + uid + '__monthly"]');
      if (dailyIn && daily != null) dailyIn.value = fmtNum(daily);
      if (monthlyIn && daily != null) {
        const monthBase = parseNum(row.dataset.monthBase);
        monthlyIn.value = fmtNum((monthBase != null ? monthBase : 0) + daily);
      }
    });
  }

  function syncMonthBase(row) {
    const uid = row.dataset.utility;
    const daily = parseNum(row.querySelector('[name="f__utility__' + uid + '__daily"]')?.value);
    const monthly = parseNum(row.querySelector('[name="f__utility__' + uid + '__monthly"]')?.value);
    if (daily != null && monthly != null) row.dataset.monthBase = String(monthly - daily);
    else if (monthly != null) row.dataset.monthBase = String(monthly);
    else row.dataset.monthBase = "0";
  }

  function applyServerData(data) {
    if (!data) return;
    const util = (data.facility || {}).utility || {};
    Object.keys(util).forEach(function (uid) {
      const row = form.querySelector('.sw-utility-row[data-utility="' + uid + '"]');
      if (!row) return;
      const u = util[uid];
      const dIn = row.querySelector('[name="f__utility__' + uid + '__daily"]');
      const mIn = row.querySelector('[name="f__utility__' + uid + '__monthly"]');
      if (dIn) dIn.value = u.daily || "";
      if (mIn) mIn.value = u.monthly || "";
      syncMonthBase(row);
    });
    calcUtility();
  }

  form.querySelectorAll(".sw-utility-row").forEach(syncMonthBase);

  async function doSave() {
    if (!statusEl) return;
    if (saving) {
      pending = true;
      return;
    }
    saving = true;
    setStatus("저장 중…", "saving");
    try {
      const res = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-SWHQ-Autosave": "1" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error("fail");
      const data = await res.json();
      if (data.data) applyServerData(data.data);
      setStatus("저장됨", "saved");
    } catch (_e) {
      setStatus("저장 실패", "error");
    } finally {
      saving = false;
      if (pending) {
        pending = false;
        doSave();
      }
    }
  }

  function scheduleSave() {
    calcUtility();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(doSave, DEBOUNCE_MS);
  }

  function notifyDirty() {
    setStatus("입력됨…", "dirty");
    scheduleSave();
  }

  function isGridPasteText(text) {
    return /[\t\n\r]/.test(text || "");
  }

  function parseClipboardGrid(text) {
    const normalized = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const lines = normalized.split("\n");
    while (lines.length && lines[lines.length - 1] === "") lines.pop();
    return lines.map(function (line) {
      return line.split("\t");
    });
  }

  function markPrevManual(el) {
    if (!el.classList.contains("sw-prev")) return;
    const flag = el.parentElement && el.parentElement.querySelector(".sw-prev-flag");
    const isEmpty = !String(el.value || "").trim();
    if (flag) flag.value = isEmpty ? "" : "1";
  }

  function isEditableCell(el) {
    return el && el.classList.contains("sw-cell") && !el.readOnly;
  }

  form.querySelectorAll(".sw-cell").forEach(function (el) {
    if (el.readOnly) return;
    el.addEventListener("input", function () {
      markPrevManual(el);
      notifyDirty();
    });
    el.addEventListener("blur", scheduleSave);
  });

  function setupDragSelect(table) {
    const grid = [];
    const cellMap = new Map();
    table.querySelectorAll("tbody tr").forEach(function (tr, rowIdx) {
      tr.querySelectorAll(".sw-cell:not([readonly])").forEach(function (input, colIdx) {
        if (!grid[rowIdx]) grid[rowIdx] = [];
        grid[rowIdx][colIdx] = input;
        cellMap.set(input, { row: rowIdx, col: colIdx });
      });
    });
    if (!cellMap.size) return null;

    let anchor = null;
    let selecting = false;
    let dragged = false;
    const selected = new Set();
    let selectionBounds = null;
    const selector = {
      selected: selected,
      table: table,
      clearSelection: clearSelection,
      deleteSelected: deleteSelected,
      pasteFromText: pasteFromText,
      containsElement: function (el) {
        return table.contains(el);
      },
      activate: function () {
        activeSelector = selector;
      },
    };

    function clearSelection() {
      selected.forEach(function (el) {
        el.classList.remove("sw-cell-selected");
      });
      selected.clear();
      selectionBounds = null;
    }

    function applySelection(r1, c1, r2, c2) {
      clearSelection();
      const minR = Math.min(r1, r2);
      const maxR = Math.max(r1, r2);
      const minC = Math.min(c1, c2);
      const maxC = Math.max(c1, c2);
      selectionBounds = { minR: minR, maxR: maxR, minC: minC, maxC: maxC };
      for (let r = minR; r <= maxR; r++) {
        for (let c = minC; c <= maxC; c++) {
          const el = grid[r] && grid[r][c];
          if (!el) continue;
          selected.add(el);
          el.classList.add("sw-cell-selected");
        }
      }
      selector.activate();
    }

    function getPasteAnchor() {
      if (selectionBounds) return { row: selectionBounds.minR, col: selectionBounds.minC };
      const focused = table.querySelector(".sw-cell:focus");
      if (focused && cellMap.has(focused)) return cellMap.get(focused);
      if (selected.size === 1) return cellMap.get(selected.values().next().value);
      return null;
    }

    function pasteFromText(text) {
      const matrix = parseClipboardGrid(text);
      if (!matrix.length) return false;
      const anchorPos = getPasteAnchor();
      if (!anchorPos) return false;
      let changed = false;
      let endR = anchorPos.row;
      let endC = anchorPos.col;
      matrix.forEach(function (cols, dr) {
        cols.forEach(function (raw, dc) {
          const el = grid[anchorPos.row + dr] && grid[anchorPos.row + dr][anchorPos.col + dc];
          if (!isEditableCell(el)) return;
          const val = String(raw).trim();
          if (el.value !== val) {
            el.value = val;
            markPrevManual(el);
            changed = true;
          }
          endR = Math.max(endR, anchorPos.row + dr);
          endC = Math.max(endC, anchorPos.col + dc);
        });
      });
      if (!changed) return false;
      applySelection(anchorPos.row, anchorPos.col, endR, endC);
      notifyDirty();
      return true;
    }

    function deleteSelected() {
      if (!selected.size) return false;
      let changed = false;
      selected.forEach(function (el) {
        if (!isEditableCell(el)) return;
        if (el.value !== "") {
          el.value = "";
          markPrevManual(el);
          changed = true;
        }
      });
      if (changed) notifyDirty();
      return changed;
    }

    cellMap.forEach(function (pos, input) {
      input.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        selecting = true;
        dragged = false;
        anchor = pos;
        applySelection(pos.row, pos.col, pos.row, pos.col);
      });
      input.addEventListener("mouseenter", function () {
        if (!selecting || !anchor) return;
        dragged = true;
        applySelection(anchor.row, anchor.col, pos.row, pos.col);
      });
      input.addEventListener("focus", function () {
        selector.activate();
        if (!dragged && selected.size <= 1 && !selected.has(input)) {
          applySelection(pos.row, pos.col, pos.row, pos.col);
        }
      });
      input.addEventListener("click", function () {
        if (dragged) input.blur();
      });
      input.addEventListener("paste", function (e) {
        const text = e.clipboardData && e.clipboardData.getData("text/plain");
        if (!isGridPasteText(text)) return;
        if (pasteFromText(text)) e.preventDefault();
      });
    });

    document.addEventListener("mouseup", function () {
      if (selecting) {
        selecting = false;
        form.classList.remove("sw-drag-selecting");
      }
    });
    table.addEventListener("mousedown", function () {
      form.classList.add("sw-drag-selecting");
    });
    return selector;
  }

  const selectors = [];
  form.querySelectorAll(".sw-excel").forEach(function (table) {
    const sel = setupDragSelect(table);
    if (sel) selectors.push(sel);
  });

  function findSelectorForTarget(target) {
    if (!target) return activeSelector;
    for (let i = 0; i < selectors.length; i++) {
      if (selectors[i].containsElement(target)) return selectors[i];
    }
    return activeSelector;
  }

  form.addEventListener("paste", function (e) {
    const text = e.clipboardData && e.clipboardData.getData("text/plain");
    if (!isGridPasteText(text)) return;
    const sel = findSelectorForTarget(e.target);
    if (sel && sel.pasteFromText(text)) e.preventDefault();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Delete" || e.key === "Backspace") {
      if (e.target && e.target.matches && e.target.matches("input:not(.sw-cell)")) return;
      let total = 0;
      selectors.forEach(function (sel) {
        total += sel.selected.size;
      });
      if (total <= 1) return;
      e.preventDefault();
      selectors.forEach(function (sel) {
        sel.deleteSelected();
      });
    }
  });

  document.addEventListener("mousedown", function (e) {
    if (e.target.closest(".sw-cell")) return;
    selectors.forEach(function (sel) {
      sel.clearSelection();
    });
  });

  calcUtility();
})();
