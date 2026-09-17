/* site_tabs_editor.js — 사업장 탭: 사진 1장 · 변경 · 클릭 시 안내도 */
(function () {
  var root = document.getElementById("sites-tabs-root");
  if (!root) return;

  var tabs = root.querySelectorAll(".sites-region-tab");
  var panels = root.querySelectorAll(".sites-tab-panel");
  var hint = document.getElementById("sites-tabs-hint");
  var editHint = document.getElementById("sites-tabs-edit-hint");
  var btnEdit = document.getElementById("sites-tab-edit-btn");
  var btnDone = document.getElementById("sites-tab-done-btn");
  var canEdit = root.getAttribute("data-can-edit") === "1";
  var editing = false;

  function syncUploadButtons() {
    panels.forEach(function (panel) {
      var btn = panel.querySelector(".sites-tab-upload-btn");
      if (!btn) return;
      btn.hidden = !(editing && panel.classList.contains("is-active"));
    });
  }

  function activate(siteId) {
    tabs.forEach(function (tab) {
      var on = tab.getAttribute("data-site-id") === siteId;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    panels.forEach(function (panel) {
      var on = panel.getAttribute("data-site-id") === siteId;
      panel.classList.toggle("is-active", on);
      if (on) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
    });
    syncUploadButtons();
  }

  function setImage(panel, url) {
    if (!panel) return;
    var frame = panel.querySelector(".sites-photo-frame");
    if (!frame) return;
    var placeholder = frame.querySelector(".sites-split-placeholder");
    if (placeholder) placeholder.remove();
    var img = frame.querySelector(".sites-tab-img");
    if (!img) {
      img = document.createElement("img");
      img.className = "sites-tab-img";
      img.decoding = "async";
      frame.appendChild(img);
    }
    img.src = url;
    img.alt = panel.getAttribute("data-site-name") || "";
    frame.classList.remove("is-placeholder");
    panel.classList.remove("is-no-image");
  }

  function uploadImage(panel, file) {
    var url = panel.getAttribute("data-image-url") || "";
    if (!url) return;
    var fd = new FormData();
    fd.append("file", file);
    fetch(url, { method: "POST", body: fd, credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("upload failed");
        return res.json();
      })
      .then(function (data) {
        if (data && data.image) setImage(panel, data.image);
      })
      .catch(function () {
        window.alert("사진 업로드에 실패했습니다.");
      });
  }

  function setEditMode(on) {
    editing = !!on;
    root.classList.toggle("is-editing", editing);
    if (btnEdit) btnEdit.hidden = editing;
    if (btnDone) btnDone.hidden = !editing;
    if (hint) hint.hidden = editing;
    if (editHint) editHint.hidden = !editing;
    syncUploadButtons();
    root.querySelectorAll(".sites-photo-card-single").forEach(function (a) {
      if (editing) {
        a.setAttribute("data-href", a.getAttribute("href") || "");
        a.removeAttribute("href");
        a.classList.add("is-edit-blocked");
      } else {
        var href = a.getAttribute("data-href");
        if (href) a.setAttribute("href", href);
        a.classList.remove("is-edit-blocked");
      }
    });
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      activate(tab.getAttribute("data-site-id"));
    });
  });

  panels.forEach(function (panel) {
    var input = panel.querySelector(".sites-tab-upload-btn input[type=file]");
    if (!input) return;
    input.addEventListener("change", function () {
      if (!input.files || !input.files.length) return;
      uploadImage(panel, input.files[0]);
      input.value = "";
    });
  });

  if (btnEdit && canEdit) {
    btnEdit.addEventListener("click", function () {
      setEditMode(true);
    });
  }
  if (btnDone && canEdit) {
    btnDone.addEventListener("click", function () {
      setEditMode(false);
    });
  }
})();
