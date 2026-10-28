/* ===== DRAGGABLE LABELS ====================================
   3Dmol labels carry a per-label screenOffset that the render
   loop folds into the sprite's alignment every frame, so a
   dragged label stays INSIDE the canvas and appears in a saved
   PNG with nothing composited. The leader line is the one part
   that does not - it is SVG, so the capture draws it itself
   from leaderSegments().
   ============================================================ */
/* ---------------------------------------------------------------------------
 * drag.js -- drag molecular labels around a 3Dmol.js v2.x viewer, the way a
 * figure author nudges labels clear of the structure in PyMOL before saving.
 *
 * PYTHON-FORMAT SAFETY
 *   This file is included verbatim in two Python page templates, one of them
 *   a percent-formatted string. Percent signs used here: NONE. Zero, in code,
 *   in strings, in CSS and in comments. Nothing needs doubling.
 *   CSS sizing is px and flex only; the overlay is sized in px, never in
 *   fractions-of-a-hundred. There is no modulo anywhere.
 *   If you edit this file, keep it free of that character.
 *
 * API
 *   var drag = makeLabelDrag(viewer, hostEl, opts);
 *     viewer : object returned by $3Dmol.createViewer(hostEl, {...})
 *     hostEl : the element the viewer was created in
 *     opts   : { leaderColor: "#777777",   // leader line stroke
 *                leaderWidth: 1,           // leader line width in px
 *                leaderThreshold: 4,       // px of offset before a leader
 *                                          // appears, so undragged labels
 *                                          // stay clean
 *                onChange: function(label){} }  // fired after each drop and
 *                                               // after reset
 *   Returns {
 *     enable(), disable(), isEnabled(), destroy(),
 *     reset(),                  // every label back to zero offset
 *     resetOne(label),          // one label back to zero offset
 *     leaderSegments(),         // [{x1,y1,x2,y2,color}] in CSS px relative
 *                               // to hostEl -- see THE CAPTURE PROBLEM
 *     scaleOffsets(factor),     // -> single-shot restore closure
 *     offsetOf(label),          // -> {x, y}
 *     rectOf(label),            // -> on-screen box, PAGE px (or null)
 *     pickAt(pageX, pageY),     // -> {label, rect} or null
 *     redraw()                  // force a leader repaint
 *   }
 *
 * HOW THE OFFSET WORKS (all line numbers from the v2.5.5 unminified build)
 *   Every label owns a Sprite whose material carries an optional
 *   `screenOffset`. SpritePlugin.render reads it on EVERY frame, at line
 *   31096, and folds it into the `alignment` uniform:
 *       alignx = (alignx || 0) + (2.0 * material.screenOffset.x) / w;
 *       aligny = (aligny || 0) + (2.0 * material.screenOffset.y) / h;
 *   The sprite vertex shader (line 36010) then does
 *       vec2 alignedPosition = position + alignment;
 *       ... finalPosition = projectionMatrix * modelViewMatrix * origin;
 *           finalPosition /= finalPosition.w;
 *           finalPosition.xy += rotatedPosition;
 *   so the nudge lands AFTER projection and the perspective divide: it is
 *   pure screen space, and it costs nothing -- no texture rebuild, no
 *   geometry change. Mutating the material and calling viewer.render() is
 *   the whole of a drag frame.
 *
 *   UNITS. The quad's `position` attribute spans -1..+1 (line 30945-30959),
 *   so one alignment unit is HALF a label box. scale[0] is
 *   (w * devicePixelRatio) / viewportWidth (line 31078) and viewportWidth is
 *   _currentWidth -> _viewportWidth -> _canvas.width = cssWidth * dpr
 *   (line 29508, 29823, 30895). The ratio cancels: a screenOffset.x of N
 *   moves the label exactly N CSS px, at any device pixel ratio, including
 *   the antialiasing path that forces dpr to 2.0 (line 29506).
 *
 *   SIGN. finalPosition is clip space, where +y is UP. A positive
 *   screenOffset.y therefore lifts the label. A mouse dy is positive DOWN.
 *   Hence the single most important line in this file: oy = start - dy.
 *
 * WHY WE HIT-TEST BY HAND
 *   3Dmol has no label picking. Sprites never enter the raycaster, and there
 *   is no getLabels(): `viewer.labels` (line 22137) is the only way to
 *   enumerate them. So we project each label ourselves and do the rectangle
 *   test here.
 *
 * THE CAPTURE PROBLEM (read this before touching the screenshot code)
 *   The leader lines are an SVG overlay on hostEl. SVG is not part of the
 *   WebGL drawing buffer, so leaders DO NOT appear in viewer.pngURI() or in
 *   any readback of viewer.getCanvas(). That is why leaderSegments() exists:
 *   it hands the capture code the same lines as plain numbers, in CSS px
 *   relative to the viewer, so the capture can stroke them onto the 2d
 *   context of the canvas it copied the frame into. Multiply each coordinate
 *   by the capture factor when drawing into a supersampled canvas.
 *
 * 3Dmol methods and fields used (all read from the v2.5.5 unminified build):
 *   viewer.labels                        22137  the array; there is no
 *                                               getLabels() in this build
 *   viewer.modelToScreen(pos)            22285  -> PAGE coords, y DOWN, CSS
 *                                               px; it adds canvasOffset()
 *   viewer.canvasOffset()                21932  rect + pageOffset, so the
 *                                               result is document-relative
 *   viewer.render()                      full re-render, synchronous
 *   viewer.setLabelStyle(label, spec)    24068  REPLACES stylespec wholesale
 *   viewer.getCanvas()                   -> glDOM, the WebGL canvas
 *   label.canvas.width / .height         26831 / 26917  the rasterised box,
 *                                               and its CSS px size on screen
 *   label.stylespec                      26829  the live style object
 *   label.sprite.position                26994  the MODEL position
 *   label.sprite.visible                 26844  false after label.hide()
 *   label.sprite.material.screenOffset   26991 / 31096
 *   label.sprite.material.alignment      26989  already resolved to a vector
 *   3Dmol binds mousedown/mousemove on glDOM, the canvas INSIDE hostEl
 *   (line 21780-21784), bubble phase; mouseup on document.body (22226).
 *   A capture-phase listener on hostEl therefore runs first and can keep the
 *   event away from the camera by calling stopPropagation.
 * ------------------------------------------------------------------------ */

(function (root) {
  "use strict";

  /* SpriteAlignment, line 31236-31246. The vector is in half-box units:
   * topLeft is (1,-1), which puts the box one half-width right and one
   * half-height down of the anchor, i.e. the anchor sits on the box's
   * top-left corner. y is the clip-space y, so it points UP. */
  var ALIGN = {
    topLeft:      { x:  1, y: -1 },
    topCenter:    { x:  0, y: -1 },
    topRight:     { x: -1, y: -1 },
    centerLeft:   { x:  1, y:  0 },
    center:       { x:  0, y:  0 },
    centerRight:  { x: -1, y:  0 },
    bottomLeft:   { x:  1, y:  1 },
    bottomCenter: { x:  0, y:  1 },
    bottomRight:  { x: -1, y:  1 }
  };

  /* Label.setContext, line 26892: `style.alignment || SpriteAlignment.topLeft`.
   * Note this is NOT the SpriteMaterial default (center, line 33224) -- that
   * one is overwritten by the constructor parameter on every label. */
  var DEFAULT_ALIGN = ALIGN.topLeft;

  var DEFAULT_THRESHOLD = 4;                 // px
  var SVG_NS = "http://www.w3.org/2000/svg";

  function makeLabelDrag(viewer, hostEl, opts) {
    opts = opts || {};

    /* LEADERS OFF BY DEFAULT. A line back to where the label used to sit is
     * the convention in a printed figure, but on screen it reads as a stray
     * mark pointing at nothing - the label has MOVED, and the line documents
     * the move rather than the molecule. Callers that want one pass
     * {leaders: true}. */
    var leadersOn = opts.leaders === true;
    var leaderColor = opts.leaderColor || "#777777";
    var leaderWidth = (opts.leaderWidth > 0) ? opts.leaderWidth : 1;
    var threshold = (typeof opts.leaderThreshold === "number" && opts.leaderThreshold >= 0)
      ? opts.leaderThreshold : DEFAULT_THRESHOLD;
    var onChange = (typeof opts.onChange === "function") ? opts.onChange : null;

    var enabled = false;
    var svg = null;
    var dragging = null;          // {label, pageX, pageY, ox, oy}
    var rafId = 0;
    var baseCursor = null;        // hostEl's cursor before we touched it
    var hostPositioned = false;   // did we have to set position:relative
    var savedHostPosition = "";
    var origRender = null;
    var renderWasOwn = false;

    /* --------------------------------------------------------------- misc */

    function noop() {}

    function win() {
      try {
        return (hostEl && hostEl.ownerDocument && hostEl.ownerDocument.defaultView) || root;
      } catch (e) { return root; }
    }

    function doc() {
      try { return hostEl.ownerDocument; } catch (e) { return null; }
    }

    function clamp(v, lo, hi) {
      if (v < lo) { return lo; }
      if (v > hi) { return hi; }
      return v;
    }

    /* The labels array, or null. Every read is guarded: a viewer that has
     * been destroyed can throw on property access. */
    function labelList() {
      var ls = null;
      try { ls = viewer && viewer.labels; } catch (e) { return null; }
      return (ls && typeof ls.length === "number") ? ls : null;
    }

    /* ----------------------------------------------------------- geometry */

    /* Page coords of an element's padding box -- the origin an absolutely
     * positioned child uses. Same shape as 3Dmol canvasOffset() (line 21932)
     * plus the border, so the two agree on a bordered container. */
    function pageOrigin(el) {
      var rect = el.getBoundingClientRect();
      var d = el.ownerDocument;
      var de = d.documentElement;
      var w = d.defaultView || root;
      return {
        left: rect.left + w.pageXOffset - de.clientLeft + (el.clientLeft || 0),
        top: rect.top + w.pageYOffset - de.clientTop + (el.clientTop || 0)
      };
    }

    function hostOrigin() { return pageOrigin(hostEl); }

    /* The gl canvas origin, for the useScreen label case below. Falls back to
     * the host when getCanvas() is unavailable. */
    function canvasOrigin() {
      try {
        var cv = viewer && viewer.getCanvas && viewer.getCanvas();
        if (cv) { return pageOrigin(cv); }
      } catch (e) {}
      return hostOrigin();
    }

    /* The label's alignment vector. The material is authoritative: setContext
     * has already turned a string name into a vector (line 26893-26896), so
     * reading the material means we never have to guess. */
    function alignmentOf(label) {
      var a = null;
      try {
        a = label && label.sprite && label.sprite.material && label.sprite.material.alignment;
      } catch (e) { a = null; }
      if (a && typeof a.x === "number" && typeof a.y === "number") { return a; }
      var s = null;
      try { s = label && label.stylespec && label.stylespec.alignment; } catch (e) { s = null; }
      if (typeof s === "string" && Object.prototype.hasOwnProperty.call(ALIGN, s)) {
        return ALIGN[s];
      }
      if (s && typeof s.x === "number" && typeof s.y === "number") { return s; }
      return DEFAULT_ALIGN;
    }

    /* The live offset. Never null: a label that was never dragged reads as
     * zero, which is exactly how the renderer treats a null screenOffset. */
    function offsetOf(label) {
      var o = null;
      try {
        o = label && label.sprite && label.sprite.material && label.sprite.material.screenOffset;
      } catch (e) { o = null; }
      if (o && typeof o.x === "number" && typeof o.y === "number" &&
          isFinite(o.x) && isFinite(o.y)) {
        return { x: o.x, y: o.y };
      }
      return { x: 0, y: 0 };
    }

    /* Mutate the material in place. This is the drag-frame write: no dispose,
     * no re-rasterise, no new texture. Compare setLabelStyle (line 24068),
     * which does all three and must never be called on a mousemove. */
    function setOffset(label, x, y) {
      try {
        var m = label && label.sprite && label.sprite.material;
        if (!m) { return false; }
        m.screenOffset = { x: x, y: y };
        return true;
      } catch (e) { return false; }
    }

    /* The anchor -- where the label would sit with no offset -- in PAGE px. */
    function anchorPage(label) {
      var sp = null;
      try { sp = label && label.sprite; } catch (e) { return null; }
      if (!sp || !sp.position) { return null; }
      var m = null;
      try { m = sp.material; } catch (e) { m = null; }
      if (m && m.useScreenCoordinates === true) {
        /* A useScreen label (addLabel with useScreen:true) already carries a
         * canvas-relative pixel position; SpritePlugin uses it raw at line
         * 31080-31086. Lift it into page space instead of projecting it. */
        var co = canvasOrigin();
        return { x: sp.position.x + co.left, y: sp.position.y + co.top };
      }
      if (!viewer || typeof viewer.modelToScreen !== "function") { return null; }
      var p = viewer.modelToScreen(sp.position);    // PAGE coords, line 22285
      if (!p || typeof p.x !== "number" || typeof p.y !== "number") { return null; }
      if (!isFinite(p.x) || !isFinite(p.y)) { return null; }
      return { x: p.x, y: p.y };
    }

    /* The label's on-screen box in PAGE px, y DOWN, or null if the label
     * cannot be placed. Derivation, from the render path quoted in the header:
     *
     *   box size    = canvas.width x canvas.height, in CSS px
     *   box centre  = anchor
     *                 + alignment.x * width  / 2      (alignment is half-box)
     *                 - alignment.y * height / 2      (alignment y is UP)
     *                 + screenOffset.x                (CSS px, right)
     *                 - screenOffset.y                (CSS px, UP)
     *
     * Sanity check with the default alignment topLeft (1,-1) and no offset:
     * centre = anchor + (w/2, h/2), so the box spans [anchor, anchor+w] and
     * [anchor, anchor+h] -- the anchor is the top-left corner, as the name
     * promises. With `center` (0,0) the box straddles the anchor. */
    function rectOf(label) {
      if (!label) { return null; }
      var cv = null;
      try { cv = label.canvas; } catch (e) { return null; }
      if (!cv) { return null; }
      var w = cv.width, h = cv.height;
      if (!(w > 0 && h > 0)) { return null; }
      var p;
      try { p = anchorPage(label); } catch (e) { return null; }
      if (!p) { return null; }
      var a = alignmentOf(label);
      var off = offsetOf(label);
      var cx = p.x + (a.x * w) / 2 + off.x;
      var cy = p.y - (a.y * h) / 2 - off.y;
      return {
        left: cx - w / 2,
        top: cy - h / 2,
        width: w,
        height: h,
        anchorX: p.x,
        anchorY: p.y,
        ox: off.x,
        oy: off.y
      };
    }

    /* Topmost label under a PAGE point, or null.
     *
     * Later entries in viewer.labels were added later and are drawn later, so
     * walking backwards yields topmost-first. (The renderer does sort sprites
     * by depth before drawing -- painterSortStable, line 31066 -- but array
     * order is the only ordering we can see from outside, and it is the right
     * answer for the flat, inFront labels a figure uses.) */
    function pickAt(pageX, pageY) {
      var ls = labelList();
      if (!ls) { return null; }
      for (var i = ls.length - 1; i >= 0; i--) {
        var label = ls[i];
        if (!label) { continue; }                 // a null hole is not a label
        try {
          if (label.sprite && label.sprite.visible === false) { continue; }
          var r = rectOf(label);
          if (!r) { continue; }
          if (pageX >= r.left && pageX <= r.left + r.width &&
              pageY >= r.top && pageY <= r.top + r.height) {
            return { label: label, rect: r, index: i };
          }
        } catch (e) { /* one bad label must not stop the search */ }
      }
      return null;
    }

    /* ------------------------------------------------------------ leaders */

    /* The lines that keep a dragged label tied to its atom, in CSS px
     * relative to hostEl. A label still sitting on its anchor gets none.
     *
     * The far end is the anchor; the near end is the point of the label box
     * closest to it, so the line stops on the border instead of running under
     * the text. If the anchor is inside the box there is nothing to draw. */
    function leaderSegments() {
      var out = [];
      if (!leadersOn) { return out; }   // also keeps them out of the capture
      var ls = labelList();
      if (!ls) { return out; }
      var o;
      try { o = hostOrigin(); } catch (e) { return out; }
      for (var i = 0; i < ls.length; i++) {
        var label = ls[i];
        if (!label) { continue; }
        try {
          if (label.sprite && label.sprite.visible === false) { continue; }
          var r = rectOf(label);
          if (!r) { continue; }
          var dist = Math.sqrt(r.ox * r.ox + r.oy * r.oy);
          if (!(dist > threshold)) { continue; }
          var x1 = r.anchorX - o.left;
          var y1 = r.anchorY - o.top;
          var left = r.left - o.left;
          var top = r.top - o.top;
          var x2 = clamp(x1, left, left + r.width);
          var y2 = clamp(y1, top, top + r.height);
          if (x2 === x1 && y2 === y1) { continue; }   // anchor inside the box
          out.push({ x1: x1, y1: y1, x2: x2, y2: y2, color: leaderColor });
        } catch (e) { /* skip this label, keep going */ }
      }
      return out;
    }

    function ensureSvg() {
      if (svg && svg.parentNode === hostEl) { return svg; }
      var d = doc();
      if (!d) { return null; }
      svg = d.createElementNS(SVG_NS, "svg");
      svg.setAttribute("aria-hidden", "true");
      var s = svg.style;
      s.position = "absolute";
      s.left = "0px";
      s.top = "0px";
      s.pointerEvents = "none";     // the overlay must never eat a click
      s.overflow = "visible";
      s.zIndex = "4";
      hostEl.appendChild(svg);
      return svg;
    }

    function clearSvg() {
      if (!svg) { return; }
      try { while (svg.firstChild) { svg.removeChild(svg.firstChild); } } catch (e) {}
    }

    function drawLeaders() {
      if (!enabled) { return; }
      /* Nothing to draw and no overlay to create: an empty <svg> over the
       * viewer is a element no one needs and one more thing to size. */
      if (!leadersOn) { clearSvg(); return; }
      var segs;
      try { segs = leaderSegments(); } catch (e) { segs = []; }
      try {
        var el = ensureSvg();
        if (!el) { return; }
        /* Sized in px from the live box, never in fractions-of-a-hundred:
         * this file has to survive Python percent formatting. */
        el.style.width = (hostEl.clientWidth || 0) + "px";
        el.style.height = (hostEl.clientHeight || 0) + "px";
        clearSvg();
        var d = doc();
        if (!d) { return; }
        for (var i = 0; i < segs.length; i++) {
          var ln = d.createElementNS(SVG_NS, "line");
          ln.setAttribute("x1", segs[i].x1);
          ln.setAttribute("y1", segs[i].y1);
          ln.setAttribute("x2", segs[i].x2);
          ln.setAttribute("y2", segs[i].y2);
          ln.setAttribute("stroke", segs[i].color);
          ln.setAttribute("stroke-width", leaderWidth);
          ln.setAttribute("stroke-linecap", "round");
          el.appendChild(ln);
        }
      } catch (e) {}
    }

    function scheduleDraw() {
      if (!enabled) { return; }
      if (rafId) { return; }
      var w = win();
      if (!w || typeof w.requestAnimationFrame !== "function") { drawLeaders(); return; }
      try {
        rafId = w.requestAnimationFrame(function () { rafId = 0; drawLeaders(); });
      } catch (e) { drawLeaders(); }
    }

    function cancelDraw() {
      if (!rafId) { return; }
      var w = win();
      try { if (w && w.cancelAnimationFrame) { w.cancelAnimationFrame(rafId); } } catch (e) {}
      rafId = 0;
    }

    /* Leaders have to follow the camera, not just the drag. Every camera
     * change in 3Dmol ends in viewer.render(), so wrapping render is the one
     * hook that catches rotate, zoom, translate, zoomTo, setView and spin
     * alike. The wrapper only schedules a repaint for the next frame, so a
     * hundred renders cost one redraw. */
    function hookRender() {
      if (origRender) { return; }
      try {
        if (!viewer || typeof viewer.render !== "function") { return; }
        renderWasOwn = Object.prototype.hasOwnProperty.call(viewer, "render");
        origRender = viewer.render;
        var wrapped = function () {
          var r = origRender.apply(this, arguments);
          scheduleDraw();
          return r;
        };
        wrapped._labelDragWrapped = true;
        viewer.render = wrapped;
      } catch (e) { origRender = null; }
    }

    function unhookRender() {
      if (!origRender) { return; }
      try {
        /* Only unwrap our own wrapper. If something else wrapped render after
         * us, putting the original back would silently drop their hook. */
        if (viewer.render && viewer.render._labelDragWrapped) {
          if (renderWasOwn) { viewer.render = origRender; }
          else { delete viewer.render; }
        }
      } catch (e) {}
      origRender = null;
      renderWasOwn = false;
    }

    function render() {
      try { if (viewer && typeof viewer.render === "function") { viewer.render(); } } catch (e) {}
    }

    /* ------------------------------------------------------------- cursor */

    function applyCursor(v) {
      try {
        if (baseCursor === null) { baseCursor = hostEl.style.cursor || ""; }
        hostEl.style.cursor = (v === null) ? baseCursor : v;
      } catch (e) {}
    }

    function restoreCursor() {
      try { if (baseCursor !== null) { hostEl.style.cursor = baseCursor; } } catch (e) {}
      baseCursor = null;
    }

    /* ------------------------------------------------------------ persist */

    /* Write the live offset into the stylespec, so it survives anything that
     * later rebuilds the material from the spec (setLabelStyle, setLabelText,
     * a restyle from the page).
     *
     * The shallow copy is essential. setLabelStyle REPLACES stylespec
     * outright (line 24068-24075) and setContext falls back to a position of
     * {x:-10, y:1, z:1} when the spec has none (line 26880-26886) -- hand it
     * a bare {screenOffset} and the label teleports off the structure. */
    function persist(label) {
      try {
        if (!label) { return false; }
        var off = offsetOf(label);
        var spec = label.stylespec ||
                   (typeof label.getStyle === "function" ? label.getStyle() : null);
        if (!spec) { return false; }
        var copy = {}, k;
        for (k in spec) {
          if (Object.prototype.hasOwnProperty.call(spec, k)) { copy[k] = spec[k]; }
        }
        copy.screenOffset = { x: off.x, y: off.y };
        if (viewer && typeof viewer.setLabelStyle === "function") {
          viewer.setLabelStyle(label, copy);
        } else {
          label.stylespec = copy;
          if (typeof label.setContext === "function") { label.setContext(); }
        }
        return true;
      } catch (e) { return false; }
    }

    /* -------------------------------------------------------------- reset */

    function resetOne(label) {
      if (!label) { return false; }
      var ok = false;
      try {
        var off = offsetOf(label);
        setOffset(label, 0, 0);
        persist(label);
        ok = true;
        if (onChange && (off.x !== 0 || off.y !== 0)) {
          try { onChange(label); } catch (e1) {}
        }
      } catch (e) { ok = false; }
      render();
      scheduleDraw();
      return ok;
    }

    /* Put every label back where 3Dmol wanted it. A user will drag a label
     * into the next county and want out, and hunting it down by hand is not
     * a thing a figure author should have to do. */
    function reset() {
      var ls = labelList();
      var n = 0;
      if (ls) {
        for (var i = 0; i < ls.length; i++) {
          var label = ls[i];
          if (!label) { continue; }
          try {
            var off = offsetOf(label);
            if (setOffset(label, 0, 0)) {
              persist(label);
              n++;
              if (onChange && (off.x !== 0 || off.y !== 0)) {
                try { onChange(label); } catch (e1) {}
              }
            }
          } catch (e) {}
        }
      }
      render();
      scheduleDraw();
      return n;
    }

    /* ------------------------------------------------------- capture glue */

    /* THE SUPERSAMPLING TRAP.
     *
     * The screenshot module captures at Nx by resizing the viewer and
     * multiplying every label's fontSize by N, so a label covers the same
     * fraction of the frame at 1x and at 4x. But screenOffset is in CSS px of
     * the CURRENT viewport: a label dragged 60px clear of its atom keeps a
     * 60px offset while its text grows four times as large, so at 4x it
     * collapses back onto the atom it was dragged away from. Every dragged
     * label in the PNG lands somewhere the user never put it.
     *
     * So the offsets scale with the fonts. Call this alongside scaleLabels():
     *
     *     restoreFonts   = shot.scaleLabels(factor);
     *     restoreOffsets = drag.scaleOffsets(factor);
     *     ... capture ...
     *     restoreOffsets();
     *     restoreFonts();
     *
     * Same contract as scaleLabels: it ALWAYS returns a function, it never
     * throws, and the restore is single-shot. The original offset OBJECT is
     * stashed and assigned back, not a copy of its numbers, so the restore is
     * exact -- no multiply-then-divide drift.
     *
     * It deliberately does not render. The capture renders immediately after,
     * and the restore is followed by the capture's own restore render. */
    function scaleOffsets(factor) {
      if (!(factor > 0)) { return noop; }
      var ls = labelList();
      if (!ls || !ls.length) { return noop; }
      var saved = [];
      for (var i = 0; i < ls.length; i++) {
        var label = ls[i];
        if (!label) { continue; }
        try {
          var m = label.sprite && label.sprite.material;
          if (!m) { continue; }
          var o = m.screenOffset;
          if (!o || typeof o.x !== "number" || typeof o.y !== "number") { continue; }
          if (o.x === 0 && o.y === 0) { continue; }   // nothing to scale
          saved.push({ label: label, offset: o });    // the ORIGINAL object
          m.screenOffset = { x: o.x * factor, y: o.y * factor };
        } catch (e) { /* skip this label, keep going */ }
      }
      if (!saved.length) { return noop; }
      var done = false;
      return function () {
        if (done) { return; }
        done = true;
        for (var j = 0; j < saved.length; j++) {
          try {
            var m2 = saved[j].label.sprite && saved[j].label.sprite.material;
            if (m2) { m2.screenOffset = saved[j].offset; }
          } catch (e) {}
        }
      };
    }

    /* -------------------------------------------------------------- input */

    function onDown(ev) {
      if (!enabled || dragging) { return; }
      if (ev.button !== undefined && ev.button !== 0) { return; }   // left only
      var hit = null;
      try { hit = pickAt(ev.pageX, ev.pageY); } catch (e) { hit = null; }
      /* A miss falls straight through to 3Dmol: no preventDefault, no
       * stopPropagation, nothing. Rotating the structure by dragging empty
       * space has to keep working exactly as it did. */
      if (!hit) { return; }
      dragging = {
        label: hit.label,
        pageX: ev.pageX,
        pageY: ev.pageY,
        ox: hit.rect.ox,
        oy: hit.rect.oy
      };
      applyCursor("grabbing");
      /* We are in the CAPTURE phase on hostEl and 3Dmol listens on the canvas
       * inside it (line 21780), so stopping here means the camera never sees
       * the press and the structure does not rotate under the label. */
      ev.preventDefault();
      ev.stopPropagation();
      attachDragListeners();
    }

    function onDragMove(ev) {
      if (!dragging) { return; }
      var dx = ev.pageX - dragging.pageX;
      var dy = ev.pageY - dragging.pageY;
      /* NEGATE dy. screenOffset.y is positive UP (clip space, vertex shader
       * at line 36010); a mouse dy is positive DOWN. Get this wrong and the
       * label runs away from the pointer. */
      setOffset(dragging.label, dragging.ox + dx, dragging.oy - dy);
      ev.preventDefault();
      ev.stopPropagation();
      render();          // the only work a drag frame does
      scheduleDraw();
    }

    function onDragUp(ev) {
      if (!dragging) { return; }
      var label = dragging.label;
      dragging = null;
      detachDragListeners();
      if (ev) {
        try { ev.preventDefault(); ev.stopPropagation(); } catch (e) {}
      }
      /* Only now do we pay for a texture rebuild: once, on release. */
      persist(label);
      var over = null;
      if (ev) {
        try { over = pickAt(ev.pageX, ev.pageY); } catch (e) { over = null; }
      }
      applyCursor(over ? "grab" : null);
      render();
      scheduleDraw();
      if (onChange) { try { onChange(label); } catch (e) {} }
    }

    /* Hover feedback. Passive in every sense: it never stops the event, so
     * 3Dmol keeps its own hover and rotation behaviour. */
    function onHoverMove(ev) {
      if (!enabled || dragging) { return; }
      var hit = null;
      try { hit = pickAt(ev.pageX, ev.pageY); } catch (e) { hit = null; }
      applyCursor(hit ? "grab" : null);
    }

    function onHoverLeave() {
      if (!enabled || dragging) { return; }
      applyCursor(null);
    }

    /* A drag that leaves the viewer, or ends over another element, still has
     * to finish. Window listeners, capture phase, and only while dragging --
     * when idle we add nothing to the page's event load. */
    function attachDragListeners() {
      var w = win();
      if (!w || !w.addEventListener) { return; }
      w.addEventListener("mousemove", onDragMove, true);
      w.addEventListener("mouseup", onDragUp, true);
      w.addEventListener("blur", onDragUp, true);
    }

    function detachDragListeners() {
      var w = win();
      if (!w || !w.removeEventListener) { return; }
      try {
        w.removeEventListener("mousemove", onDragMove, true);
        w.removeEventListener("mouseup", onDragUp, true);
        w.removeEventListener("blur", onDragUp, true);
      } catch (e) {}
    }

    /* ------------------------------------------------------ enable/disable */

    function positionHost() {
      try {
        var w = win();
        var cs = (w && w.getComputedStyle) ? w.getComputedStyle(hostEl) : null;
        if (cs && cs.position === "static") {
          savedHostPosition = hostEl.style.position;
          hostEl.style.position = "relative";
          hostPositioned = true;
        }
      } catch (e) {}
    }

    function unpositionHost() {
      if (!hostPositioned) { return; }
      try { hostEl.style.position = savedHostPosition; } catch (e) {}
      hostPositioned = false;
      savedHostPosition = "";
    }

    function enable() {
      if (enabled) { return; }
      if (!hostEl || !hostEl.addEventListener) { return; }
      enabled = true;
      positionHost();
      /* Capture phase, so we run before the canvas handlers 3Dmol installed
       * on its own element inside this one. */
      hostEl.addEventListener("mousedown", onDown, true);
      hostEl.addEventListener("mousemove", onHoverMove, false);
      hostEl.addEventListener("mouseleave", onHoverLeave, false);
      hookRender();
      drawLeaders();
    }

    function disable() {
      if (!enabled) { return; }
      if (dragging) { dragging = null; detachDragListeners(); }
      enabled = false;
      try {
        hostEl.removeEventListener("mousedown", onDown, true);
        hostEl.removeEventListener("mousemove", onHoverMove, false);
        hostEl.removeEventListener("mouseleave", onHoverLeave, false);
      } catch (e) {}
      unhookRender();
      cancelDraw();
      clearSvg();
      restoreCursor();
    }

    function destroy() {
      disable();
      try {
        if (svg && svg.parentNode) { svg.parentNode.removeChild(svg); }
      } catch (e) {}
      svg = null;
      unpositionHost();
      restoreCursor();
    }

    return {
      enable: enable,
      disable: disable,
      isEnabled: function () { return enabled; },
      destroy: destroy,
      reset: reset,
      resetOne: resetOne,
      leaderSegments: leaderSegments,
      scaleOffsets: scaleOffsets,
      offsetOf: offsetOf,
      rectOf: rectOf,
      pickAt: pickAt,
      redraw: function () { drawLeaders(); }
    };
  }

  makeLabelDrag._ALIGN = ALIGN;                  // exposed for testing
  makeLabelDrag._DEFAULT_ALIGN = DEFAULT_ALIGN;
  root.makeLabelDrag = makeLabelDrag;
})(typeof window !== "undefined" ? window : this);
