/** 점검일지2 — 모바일/태블릿에서 숫자 키패드(inputmode=decimal) 표시 */
(function () {
  var CELL_SELECTOR = [
    "input.sw-cell",
    "input.sh-cell",
    "input.hc-cell",
    "input.eg-cell",
    "input.bd-cell",
    "input.gt-cell",
    "input.hs-cell",
    "input.cf-cell",
    "input.bah-cell",
    "input.p8-cell",
  ].join(",");

  function shouldSkip(el) {
    if (!el || el.readOnly || el.disabled) return true;
    if (el.type === "hidden" || el.type === "checkbox" || el.type === "radio") return true;
    var cls = String(el.className || "");
    var name = String(el.name || "");
    // 비고·특이사항 등 문자 입력칸은 일반 키패드 유지
    if (/\b[\w-]*(note|remark|notes)\b/i.test(cls)) return true;
    if (/(^|__)(remark|note|notes)(__|$)/i.test(name)) return true;
    return false;
  }

  function apply(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(CELL_SELECTOR).forEach(function (el) {
      if (shouldSkip(el)) return;
      el.setAttribute("inputmode", "decimal");
    });
  }

  function boot() {
    apply(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.ilog2ApplyNumericKeypad = apply;
})();
