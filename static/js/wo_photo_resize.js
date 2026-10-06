/** 정비의뢰 사진 첨부 — 500KB 초과 사진을 브라우저에서 자동 축소 · 미리보기 */
(function () {
  const MAX_BYTES = 500 * 1024;
  const MAX_EDGE = 1920;
  const MIN_EDGE = 320;
  const pending = new WeakMap();

  function fmtKB(n) {
    return Math.max(1, Math.round(n / 1024)) + "KB";
  }

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = function () {
        resolve({ img: img, url: url });
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error("decode"));
      };
      img.src = url;
    });
  }

  function toBlob(canvas, quality) {
    return new Promise(function (resolve) {
      canvas.toBlob(resolve, "image/jpeg", quality);
    });
  }

  async function shrink(file) {
    if (file.size <= MAX_BYTES && /^image\/(jpeg|png|webp)$/.test(file.type)) return file;
    const loaded = await loadImage(file);
    try {
      const img = loaded.img;
      let scale = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      let blob = null;
      for (;;) {
        canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        for (const q of [0.85, 0.75, 0.65, 0.55, 0.45]) {
          blob = await toBlob(canvas, q);
          if (blob && blob.size <= MAX_BYTES) break;
        }
        if ((blob && blob.size <= MAX_BYTES) || Math.max(canvas.width, canvas.height) <= MIN_EDGE) break;
        scale *= 0.75;
      }
      if (!blob) return file;
      const name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
      return new File([blob], name, { type: "image/jpeg", lastModified: Date.now() });
    } finally {
      URL.revokeObjectURL(loaded.url);
    }
  }

  function previewBox(input) {
    let box = input.parentElement.querySelector(".wo-photo-preview");
    if (!box) {
      box = document.createElement("div");
      box.className = "wo-photo-preview";
      input.insertAdjacentElement("afterend", box);
    }
    return box;
  }

  async function processInput(input) {
    const files = Array.from(input.files || []);
    const box = previewBox(input);
    box.innerHTML = "";
    if (!files.length) return;
    const status = document.createElement("p");
    status.className = "wo-photo-status";
    status.textContent = "사진 " + files.length + "장 확인 중…";
    box.appendChild(status);

    const out = [];
    let shrunk = 0;
    let failed = 0;
    for (const f of files) {
      if (!/^image\//.test(f.type) && !/\.(jpe?g|png|gif|webp|bmp|heic|heif)$/i.test(f.name)) {
        failed++;
        continue;
      }
      try {
        const r = await shrink(f);
        if (r !== f) shrunk++;
        out.push({ file: r, before: f.size });
      } catch (_e) {
        out.push({ file: f, before: f.size });
      }
    }

    if (typeof DataTransfer !== "undefined") {
      try {
        const dt = new DataTransfer();
        out.forEach(function (o) {
          dt.items.add(o.file);
        });
        input.files = dt.files;
      } catch (_e) {
        /* 일부 브라우저는 files 교체 불가 → 서버에서 축소 */
      }
    }

    status.textContent =
      "사진 " + out.length + "장 첨부" +
      (shrunk ? " · " + shrunk + "장 500KB 이하로 자동 축소" : "") +
      (failed ? " · 이미지가 아닌 파일 " + failed + "개 제외" : "");
    const list = document.createElement("div");
    list.className = "wo-photo-preview-list";
    out.forEach(function (o) {
      const fig = document.createElement("figure");
      const img = document.createElement("img");
      img.src = URL.createObjectURL(o.file);
      img.onload = function () {
        URL.revokeObjectURL(img.src);
      };
      const cap = document.createElement("figcaption");
      cap.textContent = o.file.size < o.before ? fmtKB(o.before) + " → " + fmtKB(o.file.size) : fmtKB(o.file.size);
      fig.appendChild(img);
      fig.appendChild(cap);
      list.appendChild(fig);
    });
    box.appendChild(list);
  }

  document.addEventListener("change", function (e) {
    const input = e.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("input[type=file][data-wo-photo]")) return;
    const job = processInput(input).finally(function () {
      if (pending.get(input) === job) pending.delete(input);
    });
    pending.set(input, job);
  });

  document.addEventListener(
    "submit",
    function (e) {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      const jobs = Array.from(form.querySelectorAll("input[type=file][data-wo-photo]"))
        .map(function (i) {
          return pending.get(i);
        })
        .filter(Boolean);
      if (!jobs.length) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      const btn = form.querySelector("[type=submit]");
      if (btn) btn.disabled = true;
      Promise.allSettled(jobs).then(function () {
        if (btn) btn.disabled = false;
        if (typeof form.requestSubmit === "function") form.requestSubmit(btn || undefined);
        else form.submit();
      });
    },
    true
  );
})();
