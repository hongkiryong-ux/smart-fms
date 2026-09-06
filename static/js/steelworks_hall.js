/** 제철회관 운영일보 — UTILITY 계산, 전일지침 수기 표시, 자동 저장. */
(function () {
  const form = document.getElementById("sh-daily-form");
  const status = document.getElementById("sh-save-status");
  if (!form) return;

  let timer = null;
  let saving = false;
  let pending = false;

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

  function calcUtility() {
    let powerDaily = 0;
    let powerMonthly = 0;
    let hasPower = false;
    form.querySelectorAll(".sh-utility-row").forEach(function (row) {
      const id = row.dataset.utility;
      const multiplier = numberOf(row.dataset.multiplier) || 1;
      const prev = numberOf(row.querySelector('[name="u__' + id + '__prev"]')?.value);
      const today = numberOf(row.querySelector('[name="u__' + id + '__today"]')?.value);
      const dailyInput = row.querySelector('[name="u__' + id + '__daily"]');
      const monthlyInput = row.querySelector('[name="u__' + id + '__monthly"]');
      const daily = prev != null && today != null ? (today - prev) * multiplier : null;
      const monthly = daily != null ? (numberOf(row.dataset.monthBase) || 0) + daily : null;
      if (dailyInput) dailyInput.value = formatNumber(daily);
      if (monthlyInput) monthlyInput.value = formatNumber(monthly);
      if (id === "power_mid" || id === "power_peak" || id === "power_off") {
        if (daily != null) {
          hasPower = true;
          powerDaily += daily;
          powerMonthly += monthly || 0;
        }
      }
    });
    const dailySum = document.getElementById("sh-power-sum-daily");
    const monthlySum = document.getElementById("sh-power-sum-monthly");
    if (dailySum) dailySum.textContent = hasPower ? formatNumber(powerDaily) : "";
    if (monthlySum) monthlySum.textContent = hasPower ? formatNumber(powerMonthly) : "";
  }

  function applyServerData(data) {
    if (!data || !data.utility) return;
    Object.keys(data.utility).forEach(function (id) {
      const row = form.querySelector('.sh-utility-row[data-utility="' + id + '"]');
      if (!row) return;
      const values = data.utility[id] || {};
      const daily = row.querySelector('[name="u__' + id + '__daily"]');
      const monthly = row.querySelector('[name="u__' + id + '__monthly"]');
      if (daily) daily.value = values.daily || "";
      if (monthly) monthly.value = values.monthly || "";
      syncMonthBase(row);
    });
    calcUtility();
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
        headers: { "X-SWHALL-Autosave": "1" },
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
    setStatus("입력됨…", "dirty");
    clearTimeout(timer);
    timer = setTimeout(save, 600);
  }

  form.querySelectorAll(".sh-utility-row").forEach(syncMonthBase);
  form.querySelectorAll("input:not([readonly]), textarea").forEach(function (input) {
    input.addEventListener("input", function () {
      if (input.classList.contains("sh-prev")) {
        const flag = input.parentElement?.querySelector(".sh-prev-flag");
        if (flag) flag.value = "1";
      }
      scheduleSave();
    });
    input.addEventListener("blur", function () {
      clearTimeout(timer);
      timer = setTimeout(save, 100);
    });
  });
  calcUtility();
})();
