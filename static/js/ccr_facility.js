/** 중앙관제실(설비) 운영일보 — 자동저장 · HR 계산 · 1항 집계 */
(function () {
  const form = document.querySelector(".cf-daily-form");
  const statusEl = document.getElementById("cf-save-status");
  if (!form) return;

  let debounceTimer = null;
  let saving = false;
  let pending = false;
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
      const outM = document.querySelector('[data-out="' + mid + '-monthly"]');
      if (outD) outD.textContent = daily != null ? fmtNum(daily) : "";
      /* monthly from server on save */
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
        if (hr != null) {
          hrIn.value = fmtNum(hr);
        }
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

  form.addEventListener("input", function (e) {
    const t = e.target;
    if (t.classList.contains("cf-prev")) {
      const flag = t.parentElement?.querySelector(".cf-prev-flag");
      if (flag) flag.value = "1";
    }
    scheduleSave();
  });

  recalcAll();
})();
