/** 점검일지 1일 「엑셀 다운로드」 — 현재 화면 양식(병합·색·입력값) 그대로 엑셀로 저장 */
(function () {
  if (window.__ilog2PageExcel) return;
  window.__ilog2PageExcel = true;

  const ENDPOINT = "/admin/ilog2-page-excel";
  const TEMPLATE_EXPORTS = /\/(housing|ccr-facility|central-control-room|steelworks-hq)\/export\/daily/;
  const SKIP_CLASS = /-(form-actions|hint|qr-box|date-nav)\b|\balert\b/;

  function hidden(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.tagName === "INPUT" && el.type === "hidden") return true;
    if (/^(SCRIPT|STYLE|TEMPLATE|BUTTON|DATALIST|NOSCRIPT)$/.test(el.tagName)) return true;
    return window.getComputedStyle(el).display === "none";
  }

  function toHex(color) {
    const m = String(color || "").match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)/);
    if (!m) return null;
    if (m[4] !== undefined && Number(m[4]) < 0.05) return null;
    return "#" + [m[1], m[2], m[3]].map(function (v) {
      return ("0" + Number(v).toString(16)).slice(-2);
    }).join("");
  }

  function inlineText(node) {
    let out = "";
    node.childNodes.forEach(function (child) {
      if (child.nodeType === 3) {
        const ws = window.getComputedStyle(child.parentElement).whiteSpace;
        const keep = /^pre/.test(ws);
        out += keep ? child.textContent : child.textContent.replace(/\s+/g, " ");
        return;
      }
      if (child.nodeType !== 1 || hidden(child)) return;
      const tag = child.tagName;
      if (tag === "BR") {
        out += "\n";
      } else if (tag === "INPUT") {
        if (child.type === "checkbox" || child.type === "radio") out += child.checked ? "☑" : "☐";
        else out += " " + (child.value || "") + " ";
      } else if (tag === "SELECT") {
        const opt = child.options[child.selectedIndex];
        out += " " + (opt ? opt.text : "") + " ";
      } else if (tag === "TEXTAREA") {
        out += child.value || "";
      } else {
        const block = /^(DIV|P|LI|UL|OL)$/.test(tag);
        if (block && out && !/\n$/.test(out)) out += "\n";
        out += inlineText(child);
        if (block) out += "\n";
      }
    });
    return out;
  }

  function clean(text) {
    return String(text || "")
      .split("\n")
      .map(function (line) { return line.replace(/[ \t\u00a0]+/g, " ").trim(); })
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function tableBlock(table) {
    const rows = [];
    Array.prototype.forEach.call(table.rows, function (tr) {
      if (hidden(tr)) return;
      const cells = [];
      Array.prototype.forEach.call(tr.cells, function (td) {
        if (hidden(td)) return;
        const style = window.getComputedStyle(td);
        const align = style.textAlign === "start" ? "left" : style.textAlign === "end" ? "right" : style.textAlign;
        cells.push({
          t: clean(inlineText(td)),
          rs: td.rowSpan || 1,
          cs: td.colSpan || 1,
          h: td.tagName === "TH",
          bg: toHex(style.backgroundColor),
          fg: toHex(style.color),
          b: Number(style.fontWeight) >= 600,
          al: align,
          w: Math.round(td.getBoundingClientRect().width),
        });
      });
      rows.push(cells);
    });
    return { type: "table", rows: rows };
  }

  function collect(node, blocks) {
    if (hidden(node)) return;
    const cls = typeof node.className === "string" ? node.className : "";
    if (SKIP_CLASS.test(cls)) return;
    if (node.tagName === "TABLE") {
      blocks.push(tableBlock(node));
      return;
    }
    if (node.tagName === "TEXTAREA") {
      blocks.push({ type: "text", text: node.value || "" });
      return;
    }
    if (/-form-title\b/.test(cls)) {
      blocks.push({ type: "title", text: clean(inlineText(node)) });
      return;
    }
    if (/-sheet-(title|subtitle)\b|-part-title\b|-subtitle\b/.test(cls)) {
      blocks.push({ type: "section", text: clean(inlineText(node)) });
      return;
    }
    if (node.querySelector("table, textarea")) {
      Array.prototype.forEach.call(node.children, function (child) { collect(child, blocks); });
      return;
    }
    const text = clean(inlineText(node));
    if (text) blocks.push({ type: "line", text: text });
  }

  function buildPayload(form) {
    const blocks = [];
    Array.prototype.forEach.call(form.children, function (child) { collect(child, blocks); });
    const h1 = document.querySelector(".page-header h1");
    const pageTitle = clean(h1 ? h1.textContent : document.title);
    const dateInput = form.querySelector('input[name="log_date"]');
    const logDate = dateInput ? dateInput.value : "";
    const hasTitle = blocks.some(function (b) { return b.type === "title"; });
    return {
      title: hasTitle ? "" : pageTitle,
      subtitle: logDate ? "일자 : " + logDate : "",
      sheet: logDate || "1일",
      filename: pageTitle + "_1일_" + logDate,
      blocks: blocks,
    };
  }

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(url);
      a.remove();
    }, 1000);
  }

  document.addEventListener("click", function (e) {
    const link = e.target.closest && e.target.closest('a[href*="/export/daily"]');
    if (!link || TEMPLATE_EXPORTS.test(link.getAttribute("href") || "")) return;
    const form = document.querySelector('form[id$="-daily-form"]');
    if (!form || !form.querySelector("table")) return;
    e.preventDefault();
    const label = link.textContent;
    link.textContent = "엑셀 생성 중…";
    const payload = buildPayload(form);
    fetch(ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (response) {
        if (!response.ok) throw new Error("export failed");
        return response.blob();
      })
      .then(function (blob) {
        saveBlob(blob, payload.filename.replace(/[\\/:*?"<>|]+/g, "_") + ".xlsx");
      })
      .catch(function () {
        window.location.href = link.href;
      })
      .finally(function () {
        link.textContent = label;
      });
  });
})();
