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

  function placeNear(anchor) {
    var el = ensureTip();
    var rect = anchor.getBoundingClientRect();
    var tipRect = el.getBoundingClientRect();
    var gap = 10;
    var left = rect.left + rect.width / 2 - tipRect.width / 2;
    var top = rect.top - tipRect.height - gap;
    if (top < 8) {
      top = rect.bottom + gap;
    }
    left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - tipRect.height - 8));
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
