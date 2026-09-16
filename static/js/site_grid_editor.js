/* site_grid_editor.js — 사업장 분할 화면: 사진·바로가기 편집 */
(function () {
  var root = document.getElementById("sites-split-root");
  if (!root) return;

  var grid = document.getElementById("sites-split-grid");
  var dialog = document.getElementById("site-map-link-dialog");
  var selectEl = document.getElementById("site-map-building-select");
  var hint = document.getElementById("sites-split-hint");
  var editHint = document.getElementById("sites-split-edit-hint");

  var btnEdit = document.getElementById("sites-split-edit-btn");
  var btnAdd = document.getElementById("sites-split-add-btn");
  var btnSave = document.getElementById("sites-split-save-btn");
  var btnCancel = document.getElementById("sites-split-cancel-btn");

  var canEdit = root.getAttribute("data-can-edit") === "1";
  var editing = false;
  var placing = false;
  var activePanel = null;
  var pendingSpot = null;
  var drag = null;
  var panels = [];

  function uid() {
    return Math.random().toString(16).slice(2, 10) + Date.now().toString(16).slice(-4);
  }

  function num(v, d) {
    var n = parseFloat(v);
    return isFinite(n) ? n : d;
  }

  function clamp(v, a, b) {
    return Math.max(a, Math.min(b, v));
  }

  function cloneSpot(h) {
    return {
      id: h.id || uid(),
      label: h.label || h.building_name || "바로가기",
      building_id: h.building_id != null ? h.building_id : null,
      building_name: h.building_name || null,
      matched: !!h.matched || h.building_id != null,
      x: num(h.x, 50),
      y: num(h.y, 50),
      w: Math.max(2, num(h.w, 7)),
      h: Math.max(2, num(h.h, 3)),
    };
  }

  function Panel(el) {
    this.el = el;
    this.siteId = el.getAttribute("data-site-id");
    this.saveUrl = el.getAttribute("data-save-url") || "";
    this.imageUrl = el.getAttribute("data-image-url") || "";
    this.frame = el.querySelector(".sites-split-frame");
    this.layer = el.querySelector(".site-map-hotspots");
    this.img = el.querySelector(".site-map-img");
    this.uploadInput = el.querySelector(".sites-split-upload-btn input[type=file]");
    this.dataEl = el.querySelector(".sites-panel-data");
    this.mapData = {};
    try {
      this.mapData = JSON.parse(this.dataEl ? this.dataEl.textContent || "{}" : "{}");
    } catch (e) {
      this.mapData = {};
    }
    this.buildings = Array.isArray(this.mapData.buildings) ? this.mapData.buildings : [];
    this.hotspots = (Array.isArray(this.mapData.hotspots) ? this.mapData.hotspots : []).map(cloneSpot);
    this.snapshot = JSON.stringify(this.hotspots);
  }

  Panel.prototype.render = function (isEditing) {
    var layer = this.layer;
    if (!layer) return;
    layer.innerHTML = "";
    var self = this;
    this.hotspots.forEach(function (h) {
      if (!isEditing && !h.building_id) return;
      var el;
      if (isEditing) {
        el = document.createElement("div");
        el.className = "site-map-hotspot is-edit" + (h.building_id ? " is-linked" : " is-unlinked");
        el.setAttribute("data-id", h.id);
        el.style.left = h.x + "%";
        el.style.top = h.y + "%";
        el.style.width = h.w + "%";
        el.style.height = h.h + "%";

        var label = document.createElement("span");
        label.className = "site-map-pin-label";
        label.textContent = h.building_name || h.label || "바로가기";
        el.appendChild(label);

        var del = document.createElement("button");
        del.type = "button";
        del.className = "site-map-pin-del";
        del.setAttribute("aria-label", "삭제");
        del.textContent = "×";
        del.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          self.hotspots = self.hotspots.filter(function (x) {
            return x.id !== h.id;
          });
          self.render(true);
        });
        el.appendChild(del);

        var linkBtn = document.createElement("button");
        linkBtn.type = "button";
        linkBtn.className = "site-map-pin-link";
        linkBtn.textContent = h.building_id ? "연동변경" : "건물선택";
        linkBtn.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          openLinkDialog(self, h);
        });
        el.appendChild(linkBtn);

        el.addEventListener("pointerdown", function (ev) {
          onPinPointerDown(self, ev);
        });
      } else {
        el = document.createElement("a");
        el.className = "site-map-hotspot is-linked";
        el.href = "/admin/buildings/" + h.building_id;
        el.style.left = h.x + "%";
        el.style.top = h.y + "%";
        el.style.width = h.w + "%";
        el.style.height = h.h + "%";
        el.title = (h.building_name || h.label || "") + " 현황";
        var sr = document.createElement("span");
        sr.className = "sr-only";
        sr.textContent = h.building_name || h.label || "";
        el.appendChild(sr);
      }
      layer.appendChild(el);
    });
  };

  Panel.prototype.pctFromEvent = function (ev) {
    var rect = this.frame.getBoundingClientRect();
    return {
      x: clamp(((ev.clientX - rect.left) / rect.width) * 100, 0, 100),
      y: clamp(((ev.clientY - rect.top) / rect.height) * 100, 0, 100),
    };
  };

  Panel.prototype.select = function () {
    panels.forEach(function (p) {
      p.el.classList.toggle("is-selected", p === this);
    }, this);
    activePanel = this;
  };

  function initPanels() {
    panels = [];
    root.querySelectorAll(".sites-split-panel").forEach(function (el) {
      var p = new Panel(el);
      panels.push(p);
      p.render(false);

      el.addEventListener("click", function (ev) {
        if (!editing) return;
        if (ev.target.closest(".sites-split-panel-actions")) return;
        p.select();
      });

      if (p.uploadInput) {
        p.uploadInput.addEventListener("change", function () {
          if (!p.uploadInput.files || !p.uploadInput.files.length) return;
          uploadImage(p, p.uploadInput.files[0]);
          p.uploadInput.value = "";
        });
      }

      if (p.frame) {
        p.frame.addEventListener("click", function (ev) {
          if (!editing || !placing || activePanel !== p) return;
          if (ev.target.closest(".site-map-hotspot")) return;
          if (!p.img) {
            window.alert("먼저 사진을 업로드해 주세요.");
            return;
          }
          var pt = p.pctFromEvent(ev);
          var spot = {
            id: uid(),
            label: "바로가기",
            building_id: null,
            building_name: null,
            matched: false,
            x: pt.x,
            y: pt.y,
            w: 8,
            h: 3.5,
          };
          p.hotspots.push(spot);
          setPlacing(false);
          p.render(true);
          openLinkDialog(p, spot);
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
        var url = data.image;
        var frame = panel.frame;
        var placeholder = frame.querySelector(".sites-split-placeholder");
        if (placeholder) placeholder.remove();
        if (!panel.img) {
          panel.img = document.createElement("img");
          panel.img.className = "site-map-img";
          panel.img.draggable = false;
          panel.img.decoding = "async";
          frame.insertBefore(panel.img, panel.layer);
        }
        panel.img.src = url;
        panel.img.alt = panel.mapData.title || panel.mapData.site_name || "";
        panel.el.classList.remove("is-no-image");
        panel.select();
      })
      .catch(function () {
        window.alert("사진 업로드에 실패했습니다.");
      });
  }

  function fillBuildingSelect(panel, selectedId) {
    if (!selectEl) return;
    selectEl.innerHTML = '<option value="">선택</option>';
    panel.buildings.forEach(function (b) {
      var opt = document.createElement("option");
      opt.value = String(b.id);
      opt.textContent = b.name || ("건물#" + b.id);
      if (selectedId != null && String(selectedId) === String(b.id)) opt.selected = true;
      selectEl.appendChild(opt);
    });
  }

  function openLinkDialog(panel, spot) {
    pendingSpot = { panel: panel, spot: spot };
    fillBuildingSelect(panel, spot.building_id);
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
  }

  function onPinPointerDown(panel, ev) {
    if (!editing || placing) return;
    if (ev.button != null && ev.button !== 0) return;
    var pin = ev.currentTarget;
    if (ev.target && ev.target.closest && ev.target.closest("button")) return;
    var id = pin.getAttribute("data-id");
    var spot = panel.hotspots.find(function (h) {
      return h.id === id;
    });
    if (!spot) return;
    ev.preventDefault();
    pin.setPointerCapture(ev.pointerId);
    drag = {
      panel: panel,
      id: id,
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      origX: spot.x,
      origY: spot.y,
    };
    pin.classList.add("is-dragging");
    panel.select();
  }

  function onPointerMove(ev) {
    if (!drag) return;
    var panel = drag.panel;
    var pin = panel.layer.querySelector('.site-map-hotspot[data-id="' + drag.id + '"]');
    var spot = panel.hotspots.find(function (h) {
      return h.id === drag.id;
    });
    if (!pin || !spot) return;
    var rect = panel.frame.getBoundingClientRect();
    var dx = ((ev.clientX - drag.startX) / rect.width) * 100;
    var dy = ((ev.clientY - drag.startY) / rect.height) * 100;
    spot.x = clamp(drag.origX + dx, 0, 100);
    spot.y = clamp(drag.origY + dy, 0, 100);
    pin.style.left = spot.x + "%";
    pin.style.top = spot.y + "%";
  }

  function onPointerUp() {
    if (!drag) return;
    var panel = drag.panel;
    var pin = panel.layer.querySelector('.site-map-hotspot[data-id="' + drag.id + '"]');
    if (pin) {
      try {
        pin.releasePointerCapture(drag.pointerId);
      } catch (e) {}
      pin.classList.remove("is-dragging");
    }
    drag = null;
  }

  if (dialog) {
    dialog.addEventListener("close", function () {
      if (!pendingSpot) return;
      var panel = pendingSpot.panel;
      var spot = pendingSpot.spot;
      if (dialog.returnValue === "ok") {
        var bid = selectEl ? selectEl.value : "";
        if (!bid) {
          if (!spot.building_id) {
            panel.hotspots = panel.hotspots.filter(function (h) {
              return h.id !== spot.id;
            });
          }
        } else {
          var b = panel.buildings.find(function (x) {
            return String(x.id) === String(bid);
          });
          if (b) {
            spot.building_id = b.id;
            spot.building_name = b.name;
            spot.label = b.name;
            spot.matched = true;
          }
        }
      } else if (!spot.building_id) {
        panel.hotspots = panel.hotspots.filter(function (h) {
          return h.id !== spot.id;
        });
      }
      pendingSpot = null;
      panel.render(true);
    });
  }

  function setEditMode(on) {
    editing = !!on;
    placing = false;
    root.classList.toggle("is-editing", editing);
    root.classList.toggle("is-placing", false);
    if (grid) grid.classList.toggle("is-edit", editing);
    if (btnEdit) btnEdit.hidden = editing;
    if (btnAdd) btnAdd.hidden = !editing;
    if (btnSave) btnSave.hidden = !editing;
    if (btnCancel) btnCancel.hidden = !editing;
    if (hint) hint.hidden = editing;
    if (editHint) editHint.hidden = !editing;
    root.querySelectorAll(".sites-split-upload-btn").forEach(function (el) {
      el.hidden = !editing;
    });
    if (btnAdd) btnAdd.textContent = "바로가기 추가";
    panels.forEach(function (p) {
      p.el.classList.toggle("is-editing", editing);
      if (editing) {
        p.snapshot = JSON.stringify(p.hotspots);
      }
      p.render(editing);
    });
  }

  function setPlacing(on) {
    placing = !!on;
    root.classList.toggle("is-placing", placing);
    if (btnAdd) btnAdd.textContent = placing ? "추가 취소" : "바로가기 추가";
    if (placing && activePanel) activePanel.select();
  }

  if (btnEdit && canEdit) {
    btnEdit.addEventListener("click", function () {
      setEditMode(true);
    });
  }
  if (btnAdd && canEdit) {
    btnAdd.addEventListener("click", function () {
      if (!activePanel) {
        window.alert("먼저 수정할 사업장 패널을 클릭해 선택하세요.");
        return;
      }
      if (!activePanel.img) {
        window.alert("바로가기를 추가하려면 먼저 사진을 업로드해 주세요.");
        return;
      }
      setPlacing(!placing);
    });
  }
  if (btnCancel && canEdit) {
    btnCancel.addEventListener("click", function () {
      panels.forEach(function (p) {
        try {
          p.hotspots = JSON.parse(p.snapshot || "[]").map(cloneSpot);
        } catch (e) {}
      });
      setEditMode(false);
    });
  }
  if (btnSave && canEdit) {
    btnSave.addEventListener("click", function () {
      var invalid = [];
      panels.forEach(function (p) {
        p.hotspots.forEach(function (h) {
          if (!h.building_id) invalid.push(p.mapData.site_name);
        });
      });
      if (invalid.length) {
        window.alert("건물이 연결되지 않은 바로가기가 있습니다. 연동하거나 삭제해 주세요.");
        return;
      }
      btnSave.disabled = true;
      var chain = Promise.resolve();
      panels.forEach(function (p) {
        chain = chain.then(function () {
          return fetch(p.saveUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({
              hotspots: p.hotspots.map(function (h) {
                return {
                  id: h.id,
                  label: h.label,
                  building_id: h.building_id,
                  x: h.x,
                  y: h.y,
                  w: h.w,
                  h: h.h,
                };
              }),
            }),
          }).then(function (res) {
            if (!res.ok) throw new Error("save failed");
            p.snapshot = JSON.stringify(p.hotspots);
          });
        });
      });
      chain
        .then(function () {
          setEditMode(false);
          window.alert("저장되었습니다.");
        })
        .catch(function () {
          window.alert("저장에 실패했습니다.");
        })
        .finally(function () {
          btnSave.disabled = false;
        });
    });
  }

  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);

  initPanels();
})();
