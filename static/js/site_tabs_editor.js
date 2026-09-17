/* site_tabs_editor.js — 사업장 탭: 사진 1장 · 변경 · 같은 탭에서 안내도 전환 */
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
      var show =
        editing &&
        panel.classList.contains("is-active") &&
        panel.getAttribute("data-mode") !== "map";
      btn.hidden = !show;
    });
    if (btnEdit) {
      var active = root.querySelector(".sites-tab-panel.is-active");
      var onMap = active && active.getAttribute("data-mode") === "map";
      btnEdit.hidden = editing || onMap;
      if (btnDone) btnDone.hidden = !editing || onMap;
    }
  }

  function setPanelMode(panel, mode) {
    if (!panel) return;
    mode = mode === "map" ? "map" : "photo";
    panel.setAttribute("data-mode", mode);
    var photoView = panel.querySelector(".sites-photo-view");
    var mapView = panel.querySelector(".sites-inline-map-view");
    if (photoView) photoView.hidden = mode === "map";
    if (mapView) mapView.hidden = mode !== "map";
    if (mode === "map") {
      renderInlineHotspots(panel);
      if (editing) setEditMode(false);
    }
    syncUploadButtons();
  }

  function renderInlineHotspots(panel) {
    var layer = panel.querySelector(".sites-inline-map-hotspots");
    var dataEl = panel.querySelector(".sites-inline-map-data");
    if (!layer) return;
    layer.innerHTML = "";
    var data = {};
    try {
      data = JSON.parse(dataEl ? dataEl.textContent || "{}" : "{}");
    } catch (e) {
      data = {};
    }
    var hotspots = Array.isArray(data.hotspots) ? data.hotspots : [];
    hotspots.forEach(function (h) {
      if (!h || !h.building_id) return;
      var el = document.createElement("a");
      el.className = "site-map-hotspot is-linked is-view";
      el.href = "/admin/buildings/" + h.building_id;
      el.style.left = (h.x != null ? h.x : 50) + "%";
      el.style.top = (h.y != null ? h.y : 50) + "%";
      el.title = (h.building_name || h.label || "") + " 현황";
      var label = document.createElement("span");
      label.className = "site-map-pin-label site-map-view-label";
      label.textContent = h.building_name || h.label || "바로가기";
      el.appendChild(label);
      layer.appendChild(el);
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
      if (on) {
        panel.removeAttribute("hidden");
        setPanelMode(panel, "photo");
      } else {
        panel.setAttribute("hidden", "");
      }
    });
    syncUploadButtons();
  }

  function setImage(panel, url) {
    if (!panel) return;
    var frame = panel.querySelector(".sites-photo-view .sites-photo-frame");
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
    if (hint) hint.hidden = editing;
    if (editHint) editHint.hidden = !editing;
    syncUploadButtons();
    root.querySelectorAll(".sites-open-map-btn").forEach(function (btn) {
      btn.disabled = editing;
      btn.classList.toggle("is-edit-blocked", editing);
    });
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      activate(tab.getAttribute("data-site-id"));
    });
  });

  panels.forEach(function (panel) {
    var input = panel.querySelector(".sites-tab-upload-btn input[type=file]");
    if (input) {
      input.addEventListener("change", function () {
        if (!input.files || !input.files.length) return;
        uploadImage(panel, input.files[0]);
        input.value = "";
      });
    }
    var openBtn = panel.querySelector(".sites-open-map-btn");
    if (openBtn) {
      openBtn.addEventListener("click", function () {
        if (editing) return;
        setPanelMode(panel, "map");
      });
    }
    var backBtn = panel.querySelector(".sites-back-photo-btn");
    if (backBtn) {
      backBtn.addEventListener("click", function () {
        setPanelMode(panel, "photo");
      });
    }
    renderInlineHotspots(panel);
  });

  if (btnEdit && canEdit) {
    btnEdit.addEventListener("click", function () {
      var active = root.querySelector(".sites-tab-panel.is-active");
      if (active && active.getAttribute("data-mode") === "map") {
        setPanelMode(active, "photo");
      }
      setEditMode(true);
    });
  }
  if (btnDone && canEdit) {
    btnDone.addEventListener("click", function () {
      setEditMode(false);
    });
  }

  syncUploadButtons();
})();
