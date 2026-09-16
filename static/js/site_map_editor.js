/* site_map_editor.js — 안내도 핫스팟 보기/드래그 수정/추가 */
(function () {
  var root = document.getElementById("site-map-root");
  if (!root) return;

  var dataEl = document.getElementById("site-map-data");
  var frame = document.getElementById("site-map-frame");
  var layer = document.getElementById("site-map-hotspots");
  var img = document.getElementById("site-map-img");
  var dialog = document.getElementById("site-map-link-dialog");
  var selectEl = document.getElementById("site-map-building-select");
  var form = document.getElementById("site-map-link-form");
  var hint = document.getElementById("site-map-hint");
  var editHint = document.getElementById("site-map-edit-hint");

  var btnEdit = document.getElementById("site-map-edit-btn");
  var btnAdd = document.getElementById("site-map-add-btn");
  var btnSave = document.getElementById("site-map-save-btn");
  var btnCancel = document.getElementById("site-map-cancel-btn");

  var canEdit = root.getAttribute("data-can-edit") === "1";
  var saveUrl = root.getAttribute("data-save-url") || "";
  var imageUploadUrl = root.getAttribute("data-image-url") || "";
  var uploadInput = document.getElementById("site-map-upload-input");
  var mapData = {};
  try {
    mapData = JSON.parse(dataEl ? dataEl.textContent || "{}" : "{}");
  } catch (e) {
    mapData = {};
  }

  var buildings = Array.isArray(mapData.buildings) ? mapData.buildings : [];
  var hotspots = (Array.isArray(mapData.hotspots) ? mapData.hotspots : []).map(cloneSpot);
  var snapshot = "";
  var editing = false;
  var placing = false;
  var pendingSpot = null;
  var drag = null;

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

  function fillBuildingSelect(selectedId) {
    if (!selectEl) return;
    selectEl.innerHTML = '<option value="">선택</option>';
    buildings.forEach(function (b) {
      var opt = document.createElement("option");
      opt.value = String(b.id);
      opt.textContent = b.name || ("건물#" + b.id);
      if (selectedId != null && String(selectedId) === String(b.id)) opt.selected = true;
      selectEl.appendChild(opt);
    });
  }

  function setEditMode(on) {
    editing = !!on;
    placing = false;
    root.classList.toggle("is-editing", editing);
    root.classList.toggle("is-placing", false);
    if (btnEdit) btnEdit.hidden = editing;
    if (btnAdd) btnAdd.hidden = !editing;
    if (btnSave) btnSave.hidden = !editing;
    if (btnCancel) btnCancel.hidden = !editing;
    if (hint) hint.hidden = editing;
    if (editHint) editHint.hidden = !editing;
    if (btnAdd) btnAdd.textContent = "추가";
    render();
  }

  function setPlacing(on) {
    placing = !!on;
    root.classList.toggle("is-placing", placing);
    if (btnAdd) btnAdd.textContent = placing ? "추가 취소" : "추가";
  }

  function render() {
    if (!layer) return;
    layer.innerHTML = "";
    hotspots.forEach(function (h) {
      if (!editing && !h.building_id) return;
      var el;
      if (editing) {
        el = document.createElement("div");
        el.className = "site-map-hotspot is-edit" + (h.building_id ? " is-linked" : " is-unlinked");
        el.setAttribute("data-id", h.id);
        el.style.left = h.x + "%";
        el.style.top = h.y + "%";
        el.style.width = h.w + "%";
        el.style.height = h.h + "%";
        el.title = (h.building_name || h.label || "") + " (드래그로 이동)";

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
          hotspots = hotspots.filter(function (x) {
            return x.id !== h.id;
          });
          render();
        });
        el.appendChild(del);

        var linkBtn = document.createElement("button");
        linkBtn.type = "button";
        linkBtn.className = "site-map-pin-link";
        linkBtn.textContent = h.building_id ? "연동변경" : "건물선택";
        linkBtn.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          openLinkDialog(h);
        });
        el.appendChild(linkBtn);

        el.addEventListener("pointerdown", onPinPointerDown);
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
  }

  function pctFromEvent(ev) {
    var rect = frame.getBoundingClientRect();
    var x = ((ev.clientX - rect.left) / rect.width) * 100;
    var y = ((ev.clientY - rect.top) / rect.height) * 100;
    return { x: clamp(x, 0, 100), y: clamp(y, 0, 100) };
  }

  function onPinPointerDown(ev) {
    if (!editing || placing) return;
    if (ev.button != null && ev.button !== 0) return;
    var pin = ev.currentTarget;
    if (!pin || !pin.getAttribute) return;
    if (ev.target && ev.target.closest && ev.target.closest("button")) return;
    var id = pin.getAttribute("data-id");
    var spot = hotspots.find(function (h) {
      return h.id === id;
    });
    if (!spot) return;
    ev.preventDefault();
    pin.setPointerCapture(ev.pointerId);
    drag = {
      id: id,
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      origX: spot.x,
      origY: spot.y,
      moved: false,
    };
    pin.classList.add("is-dragging");
  }

  function onPointerMove(ev) {
    if (!drag) return;
    var pin = layer.querySelector('.site-map-hotspot[data-id="' + drag.id + '"]');
    var spot = hotspots.find(function (h) {
      return h.id === drag.id;
    });
    if (!pin || !spot) return;
    var rect = frame.getBoundingClientRect();
    var dx = ((ev.clientX - drag.startX) / rect.width) * 100;
    var dy = ((ev.clientY - drag.startY) / rect.height) * 100;
    if (Math.abs(dx) + Math.abs(dy) > 0.3) drag.moved = true;
    spot.x = clamp(drag.origX + dx, 0, 100);
    spot.y = clamp(drag.origY + dy, 0, 100);
    pin.style.left = spot.x + "%";
    pin.style.top = spot.y + "%";
  }

  function onPointerUp(ev) {
    if (!drag) return;
    var pin = layer.querySelector('.site-map-hotspot[data-id="' + drag.id + '"]');
    if (pin) {
      try {
        pin.releasePointerCapture(drag.pointerId);
      } catch (e) {}
      pin.classList.remove("is-dragging");
    }
    drag = null;
  }

  function openLinkDialog(spot) {
    pendingSpot = spot;
    fillBuildingSelect(spot.building_id);
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    else {
      var name = window.prompt("연동할 건물 ID를 입력하세요");
      if (!name) {
        pendingSpot = null;
        return;
      }
    }
  }

  function applyBuildingLink(spot, buildingId) {
    var b = buildings.find(function (x) {
      return String(x.id) === String(buildingId);
    });
    if (!b) return false;
    spot.building_id = b.id;
    spot.building_name = b.name;
    spot.label = b.name;
    spot.matched = true;
    return true;
  }

  if (form) {
    form.addEventListener("submit", function (ev) {
      // dialog form method=dialog: submitter value
    });
  }

  if (dialog) {
    dialog.addEventListener("close", function () {
      var val = dialog.returnValue;
      if (val === "ok" && pendingSpot) {
        var bid = selectEl ? selectEl.value : "";
        if (!bid) {
          if (pendingSpot && !pendingSpot.building_id) {
            hotspots = hotspots.filter(function (h) {
              return h.id !== pendingSpot.id;
            });
          }
          pendingSpot = null;
          render();
          return;
        }
        applyBuildingLink(pendingSpot, bid);
      } else if (pendingSpot && !pendingSpot.building_id) {
        hotspots = hotspots.filter(function (h) {
          return h.id !== pendingSpot.id;
        });
      }
      pendingSpot = null;
      render();
    });
  }

  if (frame) {
    frame.addEventListener("click", function (ev) {
      if (!editing || !placing) return;
      if (ev.target && ev.target.closest && ev.target.closest(".site-map-hotspot")) return;
      var p = pctFromEvent(ev);
      var spot = {
        id: uid(),
        label: "바로가기",
        building_id: null,
        building_name: null,
        matched: false,
        x: p.x,
        y: p.y,
        w: 8,
        h: 3.5,
      };
      hotspots.push(spot);
      setPlacing(false);
      render();
      openLinkDialog(spot);
    });
  }

  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);

  if (btnEdit && canEdit) {
    btnEdit.addEventListener("click", function () {
      snapshot = JSON.stringify(hotspots);
      setEditMode(true);
    });
  }
  if (btnAdd && canEdit) {
    btnAdd.addEventListener("click", function () {
      setPlacing(!placing);
    });
  }
  if (btnCancel && canEdit) {
    btnCancel.addEventListener("click", function () {
      try {
        hotspots = JSON.parse(snapshot || "[]").map(cloneSpot);
      } catch (e) {}
      setEditMode(false);
    });
  }
  if (btnSave && canEdit) {
    btnSave.addEventListener("click", function () {
      var missing = hotspots.filter(function (h) {
        return !h.building_id;
      });
      if (missing.length) {
        window.alert("건물이 연결되지 않은 바로가기가 있습니다. 연동하거나 삭제해 주세요.");
        return;
      }
      btnSave.disabled = true;
      fetch(saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          hotspots: hotspots.map(function (h) {
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
      })
        .then(function (res) {
          if (!res.ok) throw new Error("save failed");
          return res.json();
        })
        .then(function () {
          snapshot = JSON.stringify(hotspots);
          setEditMode(false);
          window.alert("저장되었습니다.");
        })
        .catch(function () {
          window.alert("저장에 실패했습니다. 권한 또는 네트워크를 확인해 주세요.");
        })
        .finally(function () {
          btnSave.disabled = false;
        });
    });
  }

  if (img) {
    img.addEventListener("dragstart", function (ev) {
      ev.preventDefault();
    });
  }

  function applyUploadedImage(url) {
    var placeholder = document.getElementById("site-map-placeholder");
    if (placeholder) placeholder.remove();
    if (!img) {
      img = document.createElement("img");
      img.id = "site-map-img";
      img.className = "site-map-img";
      img.draggable = false;
      img.decoding = "async";
      if (frame) frame.insertBefore(img, layer);
    }
    img.src = url;
    img.alt = mapData.title || "안내도";
    img.addEventListener("dragstart", function (ev) {
      ev.preventDefault();
    });
  }

  if (uploadInput && canEdit && imageUploadUrl) {
    uploadInput.addEventListener("change", function () {
      if (!uploadInput.files || !uploadInput.files.length) return;
      var file = uploadInput.files[0];
      uploadInput.value = "";
      var fd = new FormData();
      fd.append("file", file);
      fetch(imageUploadUrl, { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (res) {
          if (!res.ok) throw new Error("upload failed");
          return res.json();
        })
        .then(function (data) {
          applyUploadedImage(data.image);
        })
        .catch(function () {
          window.alert("사진 업로드에 실패했습니다.");
        });
    });
  }

  render();
})();
