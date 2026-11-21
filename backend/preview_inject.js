// Injected into every previewed page. Keeps scroll position across live
// reloads and lets the host app pick an element ("make *this* bigger").
(function () {
  // The page is sandboxed (opaque origin, no sessionStorage), so the host app
  // remembers the scroll position and sends it back after each reload.
  var scrollTimer = null;
  window.addEventListener("scroll", function () {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function () {
      parent.postMessage({ source: "vox-build", type: "scroll", y: window.scrollY }, "*");
    }, 150);
  }, { passive: true });

  var picking = false;
  var selected = null;
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #7c5cff;" +
    "background:rgba(124,92,255,.10);border-radius:6px;display:none;transition:all .06s ease-out";

  function mount() { if (!box.parentNode && document.body) document.body.appendChild(box); }
  document.addEventListener("DOMContentLoaded", mount);
  mount();

  function cssPath(el) {
    var parts = [];
    while (el && el.nodeType === 1 && el !== document.body) {
      var part = el.tagName.toLowerCase();
      if (el.id) { parts.unshift(part + "#" + el.id); break; }
      var parent = el.parentElement;
      if (parent) {
        var same = Array.prototype.filter.call(parent.children, function (c) { return c.tagName === el.tagName; });
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(el) + 1) + ")";
      }
      parts.unshift(part);
      el = parent;
    }
    return parts.join(" > ");
  }

  function label(el) {
    var text = (el.innerText || el.getAttribute("alt") || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return el.tagName.toLowerCase() + (text ? ' "' + text + '"' : "");
  }

  function show(el) {
    if (!el) { box.style.display = "none"; return; }
    var r = el.getBoundingClientRect();
    box.style.display = "block";
    box.style.left = r.left - 2 + "px";
    box.style.top = r.top - 2 + "px";
    box.style.width = r.width + 4 + "px";
    box.style.height = r.height + 4 + "px";
  }

  document.addEventListener("mouseover", function (e) { if (picking) show(e.target); }, true);
  window.addEventListener("scroll", function () { if (!picking) show(selected); }, true);

  document.addEventListener("click", function (e) {
    if (!picking) return;
    e.preventDefault();
    e.stopPropagation();
    selected = e.target;
    show(selected);
    // The React component that rendered it, so the editor knows which file to open.
    var owner = selected.closest ? selected.closest("[data-vox-id]") : null;
    var voxId = owner ? owner.getAttribute("data-vox-id") : null;
    parent.postMessage({
      source: "vox-build",
      type: "select",
      selection: {
        path: cssPath(selected),
        html: selected.outerHTML.slice(0, 4000),
        label: label(selected) + (voxId ? " in " + voxId : ""),
        vox_id: voxId,
      },
    }, "*");
  }, true);

  // The page's photos as the user sees them, so "the image on the left" or "the background" can be
  // resolved: section, left/right, top/bottom, size, and whether text sits on top of it.
  function srcOf(raw) {
    if (!raw) return "";
    try {
      var abs = new URL(raw, document.baseURI).href;
      var base = document.baseURI.replace(/[^/]*$/, "");
      return abs.indexOf(base) === 0 ? abs.slice(base.length) : abs;
    } catch (_) { return raw; }
  }

  function layout() {
    var found = [];
    var all = document.body ? document.body.getElementsByTagName("*") : [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i], src = "", alt = "";
      if (el.tagName === "IMG") { src = el.getAttribute("src"); alt = el.getAttribute("alt") || ""; }
      else {
        var bg = getComputedStyle(el).backgroundImage;
        var m = bg && bg.match(/url\(["']?([^"')]+)["']?\)/);
        if (m) src = m[1];
      }
      if (!src) continue;
      var r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 40) continue;
      var sec = el.closest("section,header,footer,[data-section]") || document.body;
      var s = sec.getBoundingClientRect();
      var cx = (r.left + r.width / 2 - s.left) / (s.width || 1);
      var cy = (r.top + r.height / 2 - s.top) / (s.height || 1);
      var coverage = (r.width * r.height) / ((s.width * s.height) || 1);
      var textOnTop = Array.prototype.some.call(sec.querySelectorAll("h1,h2,h3,p,a,button"), function (t) {
        if (el.contains(t)) return el.tagName !== "IMG";
        var tr = t.getBoundingClientRect();
        return tr.left < r.right && tr.right > r.left && tr.top < r.bottom && tr.bottom > r.top;
      });
      found.push({
        src: srcOf(src),
        alt: alt,
        section: sec.id || sec.getAttribute("data-section") || sec.tagName.toLowerCase(),
        background: coverage > 0.6 && textOnTop,
        h: cx < 0.38 ? "left" : cx > 0.62 ? "right" : "centre",
        v: cy < 0.33 ? "top" : cy > 0.66 ? "bottom" : "middle",
        width: Math.round((r.width / (s.width || 1)) * 100),
        y: Math.round(r.top + window.scrollY),
        // What the user is looking at right now ("the image on the left" means one of these).
        onScreen: r.bottom > 60 && r.top < window.innerHeight - 60,
      });
    }
    parent.postMessage({ source: "vox-build", type: "layout", images: found.slice(0, 60) }, "*");
  }
  var layoutTimer = null;
  function layoutSoon() { clearTimeout(layoutTimer); layoutTimer = setTimeout(layout, 900); }
  window.addEventListener("load", layoutSoon);
  window.addEventListener("resize", layoutSoon);
  window.addEventListener("scroll", layoutSoon, { passive: true });

  window.addEventListener("message", function (e) {
    var d = e.data || {};
    if (d.source !== "vox-build-host") return;
    if (d.type === "scroll-to") {
      var y = +d.y || 0;
      if (document.readyState === "complete") window.scrollTo(0, y);
      else window.addEventListener("load", function () { window.scrollTo(0, y); });
    } else if (d.type === "pick") {
      picking = !!d.on;
      document.documentElement.style.cursor = picking ? "crosshair" : "";
      if (!picking) show(selected);
    } else if (d.type === "highlight") {
      try { selected = d.path ? document.querySelector(d.path) : null; } catch (_) { selected = null; }
      show(selected);
    }
  });
})();
