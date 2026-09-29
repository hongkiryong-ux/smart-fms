/** 주택변전소 1일 일지 — 자동 저장 · 드래그 선택 · 삭제 · 엑셀 붙여넣기 */
(function () {
  const form = document.querySelector(".hs-daily-form");
  const statusEl = document.getElementById("hs-save-status");
  if (!form || !statusEl) return;

  let debounceTimer = null;
  let retryTimer = null;
  let saving = false;
  let pending = false;
  let dirty = false;
  let activeSelector = null;
  let savePromise = null;
  const DEBOUNCE_MS = 600;
  const RETRY_MS = 5000;

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = "hs-save-status " + (cls || "");
  }

  function failMessage(kind, detail) {
    if (kind === "login") {
      return "로그인이 만료되어 저장되지 않았습니다 — 새 탭에서 로그인하면 자동으로 다시 저장합니다 (이 창은 닫지 마세요)";
    }
    const suffix = detail ? " (" + detail + ")" : "";
    return "저장 실패 — 입력 내용은 화면에 남아 있고 5초 후 자동으로 다시 저장합니다" + suffix;
  }

  async function postOnce() {
    let res;
    try {
      res = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-HS-Autosave": "1", "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
    } catch (_err) {
      return { ok: false, kind: "network", detail: "네트워크 연결 확인" };
    }
    const type = res.headers.get("content-type") || "";
    if ((res.redirected && res.url.indexOf("/admin/login") >= 0) || res.status === 401) {
      return { ok: false, kind: "login" };
    }
    if (type.indexOf("application/json") < 0) {
      return { ok: false, kind: res.ok ? "login" : "server", detail: res.ok ? "" : "HTTP " + res.status };
    }
    let body = {};
    try {
      body = await res.json();
    } catch (_err) {
      body = {};
    }
    if (!res.ok || body.ok === false) {
      return { ok: false, kind: "server", detail: body.message || "HTTP " + res.status };
    }
    return { ok: true };
  }

  async function doSave() {
    if (saving) {
      pending = true;
      return savePromise;
    }
    clearTimeout(retryTimer);
    saving = true;
    dirty = false;
    setStatus("저장 중…", "saving");
    savePromise = (async function () {
      let result;
      try {
        result = await postOnce();
        if (result.ok) {
          setStatus("저장됨", "saved");
        } else {
          dirty = true;
          setStatus(failMessage(result.kind, result.detail), "error");
          retryTimer = setTimeout(doSave, RETRY_MS);
        }
      } finally {
        saving = false;
      }
      if (pending) {
        pending = false;
        return doSave();
      }
      return result;
    })();
    return savePromise;
  }

  function scheduleSave() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(doSave, DEBOUNCE_MS);
  }

  function notifyDirty() {
    dirty = true;
    setStatus("입력됨…", "dirty");
    scheduleSave();
  }

  window.addEventListener("beforeunload", function (e) {
    if (!dirty && !saving) return;
    e.preventDefault();
    e.returnValue = "";
  });

  const closeBtn = form.querySelector(".hs-close-day-btn");
  let closing = false;
  if (closeBtn) {
    closeBtn.addEventListener("click", async function (e) {
      if (closing) return;
      e.preventDefault();
      if (!window.confirm(closeBtn.dataset.confirm || "이 날짜를 마감할까요?")) return;
      clearTimeout(debounceTimer);
      closeBtn.disabled = true;
      let result = await doSave();
      if (saving && savePromise) result = await savePromise;
      if (!result || !result.ok) {
        closeBtn.disabled = false;
        window.alert(
          "입력 내용이 저장되지 않아 마감하지 않았습니다.\n" +
          failMessage(result ? result.kind : "server", result ? result.detail : "")
        );
        return;
      }
      closing = true;
      dirty = false;
      closeBtn.disabled = false;
      form.requestSubmit ? form.requestSubmit(closeBtn) : closeBtn.click();
    });
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

  form.querySelectorAll(".hs-prev-input").forEach(function (el) {
    el.addEventListener("input", function () {
      const hidden = el.parentElement && el.parentElement.querySelector(".hs-prev-manual-flag");
      const isEmpty = !String(el.value || "").trim();
      if (hidden) hidden.value = isEmpty ? "" : "1";
      el.dataset.prevManual = isEmpty ? "0" : "1";
      notifyDirty();
    });
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
