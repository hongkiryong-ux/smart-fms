/** 중앙관제실(설비) 운영일보 — 자동저장 · HR 계산 · 드래그 선택 · 엑셀 붙여넣기 · 삭제 */
(function () {
  const form = document.querySelector(".cf-daily-form");
  const statusEl = document.getElementById("cf-save-status");
  if (!form) return;

  let debounceTimer = null;
  let saving = false;
  let pending = false;
  let activeSelector = null;
  const DEBOUNCE_MS = 600;

  function setStatus(text, cls) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.className = "cf-save-status " + (cls || "");
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

  function parseTimeRange(s) {
    const m = String(s || "").trim().match(/^(\d{1,2}):(\d{2})\s*[~\-–—]\s*(\d{1,2}):(\d{2})$/);
    if (!m) return parseNum(s);
    let start = parseInt(m[1], 10) + parseInt(m[2], 10) / 60;
    let end = parseInt(m[3], 10) + parseInt(m[4], 10) / 60;
    if (end < start) end += 24;
    return Math.round((end - start) * 10) / 10;
  }

  function calcS1() {
    ["heat", "flow"].forEach(function (mid) {
      const prev = parseNum(form.querySelector('[name="f__s1__' + mid + '__prev"]')?.value);
      const today = parseNum(form.querySelector('[name="f__s1__' + mid + '__today"]')?.value);
      let daily = null;
      if (prev != null && today != null) daily = today - prev;
      const outD = document.querySelector('[data-out="' + mid + '-daily"]');
      if (outD) outD.textContent = daily != null ? fmtNum(daily) : "";
    });
    const prev = parseNum(form.querySelector('[name="f__s1__power__prev"]')?.value);
    const today = parseNum(form.querySelector('[name="f__s1__power__today"]')?.value);
    let daily = null;
    if (prev != null && today != null) daily = today - prev;
    const outD = document.querySelector('[data-out="power-daily"]');
    if (outD) outD.textContent = daily != null ? fmtNum(daily) : "";
  }

  function calcShiftRow(tr) {
    const shifts = parseInt(tr.dataset.shifts || "3", 10);
    let sum = 0;
    let has = false;
    for (let i = 1; i <= shifts; i++) {
      const timeIn = tr.querySelector('[name$="__s' + i + '_time"]');
      const hrIn = tr.querySelector('[name$="__s' + i + '_hr"]');
      if (timeIn && hrIn && timeIn.value && timeIn.value.includes(":")) {
        const hr = parseTimeRange(timeIn.value);
        if (hr != null) hrIn.value = fmtNum(hr);
      }
      const h = parseNum(hrIn?.value);
      if (h != null) {
        sum += h;
        has = true;
      }
    }
    const dailyIn = tr.querySelector('[name$="__daily"]');
    const monthlyIn = tr.querySelector('[name$="__monthly"]');
    const prevIn = tr.querySelector('[name$="__prev_day"]');
    if (dailyIn && has) dailyIn.value = fmtNum(sum);
    const pd = parseNum(prevIn?.value);
    const d = parseNum(dailyIn?.value);
    if (monthlyIn && pd != null && d != null) monthlyIn.value = fmtNum(pd + d);
    else if (monthlyIn && d != null) monthlyIn.value = fmtNum(d);
  }

  function calcS4() {
    document.querySelectorAll(".cf-s4-daily").forEach(function (dailyIn) {
      const u = dailyIn.dataset.unit;
      let sum = 0;
      let has = false;
      [1, 2, 3].forEach(function (i) {
        const v = parseNum(form.querySelector('[name="f__s4__' + u + '__s' + i + '"]')?.value);
        if (v != null) {
          sum += v;
          has = true;
        }
      });
      if (has) dailyIn.value = fmtNum(sum);
      const prev = parseNum(form.querySelector('[name="f__s4__' + u + '__prev_day"]')?.value);
      const d = parseNum(dailyIn.value);
      const mon = form.querySelector('[name="f__s4__' + u + '__monthly"]');
      if (mon && prev != null && d != null) mon.value = fmtNum(prev + d);
      else if (mon && d != null) mon.value = fmtNum(d);
    });
  }

  function recalcAll() {
    calcS1();
    document.querySelectorAll(".cf-shift-row").forEach(calcShiftRow);
    calcS4();
  }

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
        headers: { "X-CCRF-Autosave": "1" },
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

  function applyServerData(data) {
    if (!data) return;
    const s1 = data.s1 || {};
    ["heat", "flow", "power"].forEach(function (mid) {
      const m = s1[mid] || {};
      const outD = document.querySelector('[data-out="' + mid + '-daily"]');
      const outM = document.querySelector('[data-out="' + mid + '-monthly"]');
      if (outD) outD.textContent = m.daily || "";
      if (outM) outM.textContent = m.monthly || "";
    });
    recalcAll();
  }

  function scheduleSave() {
    recalcAll();
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
    const normalized = String(text || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n");
    const lines = normalized.split("\n");
    while (lines.length && lines[lines.length - 1] === "") lines.pop();
    return lines.map(function (line) {
      return line.split("\t");
    });
  }

  function markPrevManual(el) {
    if (!el.classList.contains("cf-prev")) return;
    const flag = el.parentElement && el.parentElement.querySelector(".cf-prev-flag");
    const isEmpty = !String(el.value || "").trim();
    if (flag) flag.value = isEmpty ? "" : "1";
    el.dataset.prevManual = isEmpty ? "0" : "1";
  }

  function isEditableCell(el) {
    return el && el.classList.contains("cf-cell") && !el.readOnly;
  }

  form.querySelectorAll(".cf-cell").forEach(function (el) {
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
    const rows = table.querySelectorAll("tbody tr");

    rows.forEach(function (tr, rowIdx) {
      const inputs = tr.querySelectorAll(".cf-cell:not([readonly])");
      inputs.forEach(function (input, colIdx) {
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
      containsElement: containsElement,
      activate: activate,
    };

    function activate() {
      activeSelector = selector;
    }

    function containsElement(el) {
      return table.contains(el);
    }

    function clearSelection() {
      selected.forEach(function (el) {
        el.classList.remove("cf-cell-selected");
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
          el.classList.add("cf-cell-selected");
        }
      }
      activate();
    }

    function getPasteAnchor() {
      if (selectionBounds) {
        return { row: selectionBounds.minR, col: selectionBounds.minC };
      }
      const focused = table.querySelector(".cf-cell:focus");
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
      recalcAll();
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
      if (changed) {
        recalcAll();
        notifyDirty();
      }
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
        activate();
        if (!dragged && selected.size <= 1 && !selected.has(input)) {
          applySelection(pos.row, pos.col, pos.row, pos.col);
        }
      });

      input.addEventListener("click", function () {
        if (dragged) input.blur();
      });

      input.addEventListener("keydown", function (e) {
        if (e.key !== "Delete" && e.key !== "Backspace") return;
        if (!selected.has(input) || selected.size !== 1) return;
        const v = input.value;
        if (!v) return;
        const start = input.selectionStart;
        const end = input.selectionEnd;
        const allSelected = start === 0 && end === v.length;
        if (allSelected) {
          e.preventDefault();
          input.value = "";
          markPrevManual(input);
          recalcAll();
          notifyDirty();
        }
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
        form.classList.remove("cf-drag-selecting");
      }
    });

    table.addEventListener("mousedown", function () {
      form.classList.add("cf-drag-selecting");
    });

    return selector;
  }

  const selectors = [];
  form.querySelectorAll(".cf-excel").forEach(function (table) {
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
      if (e.target && e.target.matches && e.target.matches("input:not(.cf-cell)")) return;

      let totalSelected = 0;
      selectors.forEach(function (sel) {
        totalSelected += sel.selected.size;
      });
      if (totalSelected <= 1) return;

      e.preventDefault();
      selectors.forEach(function (sel) {
        sel.deleteSelected();
      });
      return;
    }

    if ((e.ctrlKey || e.metaKey) && e.key === "v") {
      const sel = findSelectorForTarget(document.activeElement);
      if (!sel) return;
      if (navigator.clipboard && navigator.clipboard.readText) {
        navigator.clipboard.readText().then(function (text) {
          if (!isGridPasteText(text)) return;
          sel.pasteFromText(text);
        }).catch(function () {
          /* paste 이벤트에 위임 */
        });
      }
    }
  });

  document.addEventListener("mousedown", function (e) {
    if (e.target.closest(".cf-cell")) return;
    selectors.forEach(function (sel) {
      sel.clearSelection();
    });
  });

  recalcAll();
})();
