/** 주택변전소 1일 일지 — 입력 시 자동 저장 */
(function () {
  const form = document.querySelector(".hs-daily-form");
  const statusEl = document.getElementById("hs-save-status");
  if (!form || !statusEl) return;

  let debounceTimer = null;
  let saving = false;
  let pending = false;
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

  form.querySelectorAll(".hs-cell").forEach(function (el) {
    el.addEventListener("input", function () {
      setStatus("입력됨…", "dirty");
      scheduleSave();
    });
    el.addEventListener("blur", scheduleSave);
  });
})();
