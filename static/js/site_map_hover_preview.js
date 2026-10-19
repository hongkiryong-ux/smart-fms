/* site_map_hover_preview.js — 안내도 바로가기 호버 시 건물 사진 미리보기 */
(function (global) {
  var tip = null;
  var hideTimer = null;
  var activeAnchor = null;

  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement("div");
    tip.className = "site-map-photo-preview";
    tip.hidden = true;
    tip.setAttribute("role", "tooltip");
    tip.innerHTML =
      '<div class="site-map-photo-preview-inner">' +
      '<img class="site-map-photo-preview-img" alt="" />' +
      '<div class="site-map-photo-preview-name"></div>' +
      "</div>";
    document.body.appendChild(tip);
    tip.addEventListener("mouseenter", function () {
      clearHide();
    });
    tip.addEventListener("mouseleave", function () {
      scheduleHide();
    });
    return tip;
  }

  function clearHide() {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function scheduleHide() {
    clearHide();
    hideTimer = setTimeout(hide, 80);
  }

  function hide() {
    clearHide();
    activeAnchor = null;
    var el = ensureTip();
    el.hidden = true;
    el.classList.remove("is-visible");
  }

  function clampRange(v, lo, hi) {
    return Math.max(lo, Math.min(v, Math.max(lo, hi)));
  }

  /* 팝업이 바로가기(anchor)를 가리지 않도록 위·아래·오른쪽·왼쪽 중 들어가는 쪽에 두고,
     어느 쪽에도 원래 크기로 안 들어가면 가장 넓은 쪽에 맞춰 축소한다. */
  function placeNear(anchor) {
    var el = ensureTip();
    var img = el.querySelector(".site-map-photo-preview-img");
    el.style.width = "";
    if (img) img.style.height = "";

    var rect = anchor.getBoundingClientRect();
    var tipRect = el.getBoundingClientRect();
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var gap = 10;
    var edge = 8;
    var w = tipRect.width;
    var h = tipRect.height;
    var imgH = img ? img.getBoundingClientRect().height : h;
    var nameH = Math.max(0, h - imgH);

    var space = {
      top: rect.top - gap - edge,
      bottom: vh - rect.bottom - gap - edge,
      right: vw - rect.right - gap - edge,
      left: rect.left - gap - edge,
    };
    var fullW = vw - edge * 2;
    var fullH = vh - edge * 2;

    function scaleFor(side) {
      if (!imgH || !w) return 0;
      if (side === "top" || side === "bottom") {
        return Math.min(1, (space[side] - nameH) / imgH, fullW / w);
      }
      return Math.min(1, space[side] / w, (fullH - nameH) / imgH);
    }

    var order = ["top", "bottom", "right", "left"];
    var side = null;
    var scale = 1;
    for (var i = 0; i < order.length; i++) {
      if (scaleFor(order[i]) >= 1) {
        side = order[i];
        break;
      }
    }
    if (!side) {
      side = order[0];
      scale = scaleFor(side);
      for (var j = 1; j < order.length; j++) {
        var s = scaleFor(order[j]);
        if (s > scale) {
          scale = s;
          side = order[j];
        }
      }
      scale = Math.max(0.25, scale);
      el.style.width = Math.floor(w * scale) + "px";
      if (img) img.style.height = Math.floor(imgH * scale) + "px";
      tipRect = el.getBoundingClientRect();
      w = tipRect.width;
      h = tipRect.height;
    }

    var left;
    var top;
    if (side === "top" || side === "bottom") {
      left = clampRange(rect.left + rect.width / 2 - w / 2, edge, vw - w - edge);
      top = side === "top" ? rect.top - gap - h : rect.bottom + gap;
    } else {
      top = clampRange(rect.top + rect.height / 2 - h / 2, edge, vh - h - edge);
      left = side === "right" ? rect.right + gap : rect.left - gap - w;
    }
    el.style.left = Math.round(left) + "px";
    el.style.top = Math.round(top) + "px";
  }

  function show(anchor, photoUrl, name) {
    if (!anchor || !photoUrl) return;
    clearHide();
    activeAnchor = anchor;
    var el = ensureTip();
    var img = el.querySelector(".site-map-photo-preview-img");
    var nameEl = el.querySelector(".site-map-photo-preview-name");
    if (nameEl) nameEl.textContent = name || "";
    if (img) {
      img.alt = name || "건물 사진";
      if (img.getAttribute("src") !== photoUrl) {
        img.src = photoUrl;
      }
    }
    el.hidden = false;
    el.classList.add("is-visible");
    placeNear(anchor);
    if (img && !img.complete) {
      img.onload = function () {
        if (activeAnchor === anchor) placeNear(anchor);
      };
    }
  }

  function bind(anchor, photoUrl, name) {
    if (!anchor || !photoUrl) return;
    anchor.setAttribute("data-photo-url", photoUrl);
    anchor.addEventListener("mouseenter", function () {
      show(anchor, photoUrl, name);
    });
    anchor.addEventListener("focus", function () {
      show(anchor, photoUrl, name);
    });
    anchor.addEventListener("mouseleave", scheduleHide);
    anchor.addEventListener("blur", scheduleHide);
  }

  global.SiteMapHoverPreview = {
    bind: bind,
    hide: hide,
  };
})(window);
