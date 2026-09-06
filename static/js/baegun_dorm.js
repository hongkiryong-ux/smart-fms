/** 백운생활관 운영일보 — UTILITY 계산 · 드래그 선택 · 엑셀 붙여넣기 · 삭제 · 자동 저장 */
(function () {
  const form = document.getElementById("bd-daily-form");
  const status = document.getElementById("bd-save-status");
  if (!form) return;

  let timer = null;
  let saving = false;
  let pending = false;
  let activeSelector = null;
  const DEBOUNCE_MS = 600;

  function numberOf(value) {
    if (value == null || String(value).trim() === "") return null;
    const number = Number(String(value).replace(/,/g, ""));
    return Number.isFinite(number) ? number : null;
  }

  function formatNumber(value) {
    if (value == null) return "";
    if (Math.abs(value - Math.round(value)) < 1e-9) return String(Math.round(value));
    return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  }

  function setStatus(text, className) {
    if (!status) return;
    status.textContent = text;
    status.className = className || "";
  }

  function syncMonthBase(row) {
    const daily = numberOf(row.querySelector('[name$="__daily"]')?.value);
    const monthly = numberOf(row.querySelector('[name$="__monthly"]')?.value);
    row.dataset.monthBase = String(monthly != null ? monthly - (daily || 0) : 0);
  }

  function rowMultiplier(row) {
    const key = row.dataset.multiplierKey;
    if (key) {
      const input = form.querySelector('[name="mul__' + key + '"]');
      const fromInput = numberOf(input && input.value);
      if (fromInput != null) return fromInput;
    }
    return numberOf(row.dataset.multiplier) || 1;
  }

  function calcUtility() {
    form.querySelectorAll(".bd-utility-row").forEach(function (row) {
      const id = row.dataset.utility;
      const multiplier = rowMultiplier(row);
      const prev = numberOf(row.querySelector('[name="u__' + id + '__prev"]')?.value);
      const today = numberOf(row.querySelector('[name="u__' + id + '__today"]')?.value);
      const dailyInput = row.querySelector('[name="u__' + id + '__daily"]');
      const monthlyInput = row.querySelector('[name="u__' + id + '__monthly"]');
      const daily = prev != null && today != null ? (today - prev) * multiplier : null;
      const monthly = daily != null ? (numberOf(row.dataset.monthBase) || 0) + daily : null;
      if (dailyInput) dailyInput.value = formatNumber(daily);
      if (monthlyInput) monthlyInput.value = formatNumber(monthly);
    });
  }

  function applyServerData(data) {
    if (!data || !data.utility) return;
    Object.keys(data.utility).forEach(function (id) {
      const row = form.querySelector('.bd-utility-row[data-utility="' + id + '"]');
      if (!row) return;
      const values = data.utility[id] || {};
      const daily = row.querySelector('[name="u__' + id + '__daily"]');
      const monthly = row.querySelector('[name="u__' + id + '__monthly"]');
      if (daily) daily.value = values.daily || "";
      if (monthly) monthly.value = values.monthly || "";
      syncMonthBase(row);
    });
    if (data.multipliers) {
      Object.keys(data.multipliers).forEach(function (key) {
        const input = form.querySelector('[name="mul__' + key + '"]');
        if (input) input.value = data.multipliers[key] || "";
      });
    }
    calcUtility();
    calcHeatingRun();
  }

  async function save() {
    if (saving) {
      pending = true;
      return;
    }
    saving = true;
    setStatus("저장 중…", "saving");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { "X-BDORM-Autosave": "1" },
      });
      if (!response.ok) throw new Error("save failed");
      const result = await response.json();
      applyServerData(result.data);
      setStatus("저장됨", "saved");
    } catch (_error) {
      setStatus("저장 실패", "error");
    } finally {
      saving = false;
      if (pending) {
        pending = false;
        save();
      }
    }
  }

  function scheduleSave() {
    calcUtility();
    calcHeatingRun();
    clearTimeout(timer);
    timer = setTimeout(save, DEBOUNCE_MS);
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
    if (!el.classList.contains("bd-prev")) return;
    const flag = el.parentElement && el.parentElement.querySelector(".bd-prev-flag");
    const isEmpty = !String(el.value || "").trim();
    if (flag) flag.value = isEmpty ? "" : "1";
  }


  function markHrPrevManual(el) {
    if (!el.classList.contains("bd-hr-prev")) return;
    const flag = el.parentElement && el.parentElement.querySelector(".bd-hr-prev-flag");
    const isEmpty = !String(el.value || "").trim();
    if (flag) flag.value = isEmpty ? "" : "1";
  }

  function calcHeatingRun() {
    form.querySelectorAll(".bd-hr-row").forEach(function (row) {
      const id = row.dataset.hr;
      const today = numberOf(row.querySelector('[name="hr__' + id + '__daily"]')?.value);
      const prev = numberOf(row.querySelector('[name="hr__' + id + '__prev_cum"]')?.value);
      const monthlyInput = row.querySelector('[name="hr__' + id + '__monthly_cum"]');
      const monthly = today != null ? (prev || 0) + today : null;
      if (monthlyInput) monthlyInput.value = formatNumber(monthly);
    });
  }

  function isEditableCell(el) {
    return el && el.classList.contains("bd-cell") && !el.readOnly;
  }

  form.querySelectorAll(".bd-utility-row").forEach(syncMonthBase);

  form.querySelectorAll(".bd-cell").forEach(function (el) {
    if (el.readOnly) return;
    el.addEventListener("input", function () {
      markPrevManual(el);
      markHrPrevManual(el);
      notifyDirty();
    });
    el.addEventListener("blur", function () {
      clearTimeout(timer);
      timer = setTimeout(save, 100);
    });
  });

  const notes = form.querySelector(".bd-notes");
  if (notes) {
    notes.addEventListener("input", notifyDirty);
    notes.addEventListener("blur", function () {
      clearTimeout(timer);
      timer = setTimeout(save, 100);
    });
  }

  function setupDragSelect(table) {
    const grid = [];
    const cellMap = new Map();
    table.querySelectorAll("tbody tr").forEach(function (tr, rowIdx) {
      tr.querySelectorAll(".bd-cell:not([readonly])").forEach(function (input, colIdx) {
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
        el.classList.remove("bd-cell-selected");
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
          el.classList.add("bd-cell-selected");
        }
      }
      selector.activate();
    }

    function getPasteAnchor() {
      if (selectionBounds) return { row: selectionBounds.minR, col: selectionBounds.minC };
      const focused = table.querySelector(".bd-cell:focus");
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
        form.classList.remove("bd-drag-selecting");
      }
    });
    table.addEventListener("mousedown", function () {
      form.classList.add("bd-drag-selecting");
    });
    return selector;
  }

  const selectors = [];
  form.querySelectorAll(".bd-excel").forEach(function (table) {
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
      if (e.target && e.target.matches && e.target.matches("input:not(.bd-cell), textarea")) return;
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
    if (e.target.closest(".bd-cell")) return;
    selectors.forEach(function (sel) {
      sel.clearSelection();
    });
  });

  calcUtility();
  calcHeatingRun();
})();
