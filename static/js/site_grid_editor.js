/* site_grid_editor.js — 사업장 분할 화면: 사진 변경 · 클릭 시 안내도 이동 */
(function () {
  var root = document.getElementById("sites-split-root");
  if (!root) return;

  var grid = document.getElementById("sites-split-grid");
  var hint = document.getElementById("sites-split-hint");
  var editHint = document.getElementById("sites-split-edit-hint");
  var btnEdit = document.getElementById("sites-split-edit-btn");
  var btnDone = document.getElementById("sites-split-done-btn");

  var canEdit = root.getAttribute("data-can-edit") === "1";
  var editing = false;
  var panels = [];

  function Panel(el) {
    this.el = el;
    this.siteId = el.getAttribute("data-site-id");
    this.siteUrl = el.getAttribute("data-site-url") || "";
    this.imageUrl = el.getAttribute("data-image-url") || "";
    this.frame = el.querySelector(".sites-split-frame");
    this.uploadInput = el.querySelector(".sites-split-upload-btn input[type=file]");
    this.siteName = el.getAttribute("data-site-name") || "";
  }

  Panel.prototype.select = function () {
    panels.forEach(function (p) {
      p.el.classList.toggle("is-selected", p === this);
    }, this);
  };

  Panel.prototype.setImage = function (url) {
    var frame = this.frame;
    if (!frame) return;
    var placeholder = frame.querySelector(".sites-split-placeholder");
    if (placeholder) placeholder.remove();
    var img = frame.querySelector(".site-map-img");
    if (!img) {
      img = document.createElement("img");
      img.className = "site-map-img";
      img.draggable = false;
      img.decoding = "async";
      frame.appendChild(img);
    }
    img.src = url;
    img.alt = this.siteName;
    this.el.classList.remove("is-no-image");
  };

  function initPanels() {
    panels = [];
    root.querySelectorAll(".sites-split-panel").forEach(function (el) {
      var p = new Panel(el);
      panels.push(p);

      el.addEventListener("click", function (ev) {
        if (!editing) return;
        if (ev.target.closest(".sites-split-panel-actions")) return;
        if (ev.target.closest("a.sites-split-link, a.sites-split-title-link")) {
          ev.preventDefault();
        }
        p.select();
      });

      if (p.uploadInput) {
        p.uploadInput.addEventListener("change", function () {
          if (!p.uploadInput.files || !p.uploadInput.files.length) return;
          uploadImage(p, p.uploadInput.files[0]);
          p.uploadInput.value = "";
        });
      }
    });
    if (panels.length) panels[0].select();
  }

  function uploadImage(panel, file) {
    var fd = new FormData();
    fd.append("file", file);
    fetch(panel.imageUrl, { method: "POST", body: fd, credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("upload failed");
        return res.json();
      })
      .then(function (data) {
        panel.setImage(data.image);
        panel.select();
      })
      .catch(function () {
        window.alert("사진 업로드에 실패했습니다.");
      });
  }

  function setEditMode(on) {
    editing = !!on;
    root.classList.toggle("is-editing", editing);
    if (grid) grid.classList.toggle("is-edit", editing);
    if (btnEdit) btnEdit.hidden = editing;
    if (btnDone) btnDone.hidden = !editing;
    if (hint) hint.hidden = editing;
    if (editHint) editHint.hidden = !editing;
    root.querySelectorAll(".sites-split-upload-btn").forEach(function (el) {
      el.hidden = !editing;
    });
    panels.forEach(function (p) {
      p.el.classList.toggle("is-editing", editing);
    });
  }

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

  initPanels();
})();
