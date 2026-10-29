/* The text-size control every page of ours carries in its header: smaller, the size now (press
   it to go back to 100%), larger.

   One custom property does the work. --ui-scale sits on the root element, and every type token
   and every spacing token in /static/style.css is a calc() that multiplies by it, so text,
   controls and the gaps between them grow together and the layout stays the layout. It is not
   CSS zoom and not a transform on the page: both move the mouse's coordinates away from what a
   canvas thinks they are, and the 3D viewers (3Dmol and Mol*) then pick the residue next to the
   one under the pointer. The viewers keep their own pixel geometry; only what is around them
   grows.

   Where the size comes from, in order: a ui_scale in the page's own address, which is how HOPE
   Labs hands its own choice to every tool tab it opens; then what this page remembered last time;
   then 100%. The choice is remembered per page, and written back into the address, so a reload
   keeps what the person chose rather than what the tab was opened with.

   A page loads this in its <head>, so the size is applied before anything is drawn, and puts
   <span data-hl-zoom></span> where the control goes. Nothing else is needed. */
(function () {
  "use strict";
  var STEPS = [0.8, 0.9, 1, 1.1, 1.25, 1.5];
  var script = document.currentScript;
  var page = (script && script.getAttribute("data-page")) || "page";
  var KEY = "hl-ui-scale:" + page;
  var root = document.documentElement;

  function nearest(value) {
    var want = Number(value);
    if (!isFinite(want) || want <= 0) return 1;
    // A percentage is taken as one: ui_scale=125 and ui_scale=1.25 are the same request.
    if (want > 5) want = want / 100;
    var best = STEPS[0];
    STEPS.forEach(function (s) { if (Math.abs(s - want) < Math.abs(best - want)) best = s; });
    return best;
  }

  function fromAddress() {
    try {
      var got = new URLSearchParams(location.search).get("ui_scale");
      return got == null || got === "" ? null : nearest(got);
    } catch (e) { return null; }
  }

  function remembered() {
    try {
      var got = localStorage.getItem(KEY);
      return got == null ? null : nearest(got);
    } catch (e) { return null; }       // a private window, or storage switched off
  }

  var scale = fromAddress();
  if (scale == null) scale = remembered();
  if (scale == null) scale = 1;

  function apply() {
    root.style.setProperty("--ui-scale", String(scale));
    root.setAttribute("data-ui-scale", String(Math.round(scale * 100)));
  }
  apply();

  function keep() {
    try { localStorage.setItem(KEY, String(scale)); } catch (e) { /* not kept: still applied */ }
    // The address too, so a reload keeps this choice and not the one the tab was opened with.
    try {
      var url = new URL(location.href);
      if (url.searchParams.has("ui_scale") || scale !== 1) {
        if (scale === 1) url.searchParams.delete("ui_scale");
        else url.searchParams.set("ui_scale", String(scale));
        history.replaceState(history.state, "", url.toString());
      }
    } catch (e) { /* an address that cannot be rewritten keeps the old one */ }
  }

  var mounted = [];
  function paint() {
    var at = STEPS.indexOf(scale);
    mounted.forEach(function (box) {
      var pct = Math.round(scale * 100) + "%";
      box.querySelector(".hlzoom-pct").textContent = pct;
      box.querySelector(".hlzoom-pct").setAttribute("aria-label", "Text size " + pct + ". Press to reset to 100%");
      box.querySelector(".hlzoom-out").disabled = at <= 0;
      box.querySelector(".hlzoom-in").disabled = at >= STEPS.length - 1;
    });
  }

  function set(value) {
    var next = nearest(value);
    if (next === scale) { paint(); return scale; }
    scale = next;
    apply();
    keep();
    paint();
    // Anything drawn to a measured size redraws: a viewer whose column grew narrower has to
    // resize its canvas, or its drawing and the mouse stop agreeing about where a residue is.
    requestAnimationFrame(function () {
      window.dispatchEvent(new CustomEvent("hl-zoom", { detail: { scale: scale } }));
      window.dispatchEvent(new Event("resize"));
    });
    return scale;
  }

  function step(way) {
    var at = STEPS.indexOf(scale);
    return set(STEPS[Math.max(0, Math.min(STEPS.length - 1, at + way))]);
  }

  function button(cls, text, label, tip) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = text;
    b.setAttribute("aria-label", label);
    b.title = tip;
    return b;
  }

  function mount(where) {
    if (!where || where.getAttribute("data-hl-zoom-ready")) return;
    where.setAttribute("data-hl-zoom-ready", "1");
    where.classList.add("hlzoom");
    where.setAttribute("role", "group");
    where.setAttribute("aria-label", "Text size");
    var out = button("hlzoom-out", "−", "Smaller text", "Smaller text");
    var pct = button("hlzoom-pct", "100%", "Text size", "Back to 100%");
    var inn = button("hlzoom-in", "+", "Larger text", "Larger text");
    out.onclick = function () { step(-1); };
    inn.onclick = function () { step(1); };
    pct.onclick = function () { set(1); };
    where.appendChild(out);
    where.appendChild(pct);
    where.appendChild(inn);
    mounted.push(where);
    paint();
  }

  function mountAll() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-hl-zoom]"), mount);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountAll);
  else mountAll();

  /* What another page needs: the hub reads get() and opens a tool with withScale(url), so the
     person's choice follows them from tab to tab. */
  window.HLZoom = {
    steps: STEPS.slice(),
    key: KEY,
    get: function () { return scale; },
    set: set,
    step: step,
    reset: function () { return set(1); },
    mount: mount,
    withScale: function (address) {
      try {
        var url = new URL(address, location.href);
        if (scale === 1) url.searchParams.delete("ui_scale");
        else url.searchParams.set("ui_scale", String(scale));
        return url.toString();
      } catch (e) { return address; }
    },
  };
})();
