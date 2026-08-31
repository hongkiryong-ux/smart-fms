/** 주택변전소 1일 일지 — 자동 저장 · 드래그 선택 · 삭제 · 엑셀 붙여넣기 */
(function () {
  const form = document.querySelector(".hs-daily-form");
  const statusEl = document.getElementById("hs-save-status");
  if (!form || !statusEl) return;

  let debounceTimer = null;
  let saving = false;
  let pending = false;
  let activeSelector = null;
  const DEBOUNCE_MS = 600;

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = "hs-save-status " + (cls || "");
  }

  async function doSave() {
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
        headers: { "X-HS-Autosave": "1" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error("save failed");
      await res.json();
      setStatus("저장됨", "saved");
    } catch (_err) {
      setStatus("저장 실패 — 다시 입력해 주세요", "error");
    } finally {
      saving = false;
      if (pending) {
        pending = false;
        doSave();
      }
    }
  }

  function scheduleSave() {
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
    while (lines.length && lines[lines.length - 1] === "") {
      lines.pop();
    }
    return lines.map(function (line) {
      return line.split("\t");
    });
  }

  form.querySelectorAll(".hs-cell").forEach(function (el) {
    el.addEventListener("input", notifyDirty);
    el.addEventListener("blur", scheduleSave);
  });

  function setupDragSelect(table) {
    const grid = [];
    const cellMap = new Map();
    const rows = table.querySelectorAll("tbody tr");

    rows.forEach(function (tr, rowIdx) {
      const inputs = tr.querySelectorAll(".hs-cell");
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
        el.classList.remove("hs-cell-selected");
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
          el.classList.add("hs-cell-selected");
        }
      }
      activate();
    }

    function getPasteAnchor() {
      if (selectionBounds) {
        return { row: selectionBounds.minR, col: selectionBounds.minC };
      }
      const focused = table.querySelector(".hs-cell:focus");
      if (focused && cellMap.has(focused)) {
        return cellMap.get(focused);
      }
      if (selected.size === 1) {
        return cellMap.get(selected.values().next().value);
      }
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
          if (!el || el.readOnly) return;
          const val = String(raw).trim();
          if (el.value !== val) {
            el.value = val;
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
        if (el.value !== "") {
          el.value = "";
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
        activate();
        if (!dragged && selected.size <= 1 && !selected.has(input)) {
          applySelection(pos.row, pos.col, pos.row, pos.col);
        }
      });

      input.addEventListener("click", function () {
        if (dragged) {
          input.blur();
        }
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
          notifyDirty();
        }
      });

      input.addEventListener("paste", function (e) {
        const text = e.clipboardData && e.clipboardData.getData("text/plain");
        if (!isGridPasteText(text)) return;
        if (pasteFromText(text)) {
          e.preventDefault();
        }
      });
    });

    document.addEventListener("mouseup", function () {
      if (selecting) {
        selecting = false;
        form.classList.remove("hs-drag-selecting");
      }
    });

    table.addEventListener("mousedown", function () {
      form.classList.add("hs-drag-selecting");
    });

    return selector;
  }

  const selectors = [];
  form.querySelectorAll(".hs-excel").forEach(function (table) {
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
    if (sel && sel.pasteFromText(text)) {
      e.preventDefault();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Delete" || e.key === "Backspace") {
      if (e.target && e.target.matches && e.target.matches("input:not(.hs-cell)")) return;

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
    if (e.target.closest(".hs-cell")) return;
    selectors.forEach(function (sel) {
      sel.clearSelection();
    });
  });
})();
