/* The structure browser: one dialog, shared by every page of ours, for choosing a structure from
   wherever it is. It works the way the lab's other file browsers already do, so somebody who has
   used one has learned this one: the places on the left, the folder on the right, one click to
   choose.

   Left, the places: This computer (the native file chooser, sent up through the page), a PDB code
   where the page can fetch one, the person's own scratch and home, this tool's run folder, the
   other tools' run folders the server knows of, and the group folders they can read. Right, the
   folder on show: where it is, up one level, its folders, then its structure files with their
   sizes. One click on a file chooses it. Escape closes; Enter opens what has the focus, and the
   arrow keys move between rows.

   The dialog is only the choosing. What a choice does is the page's: it is handed a function per
   way in (onFile, onPath, onCode, onRun, onFolder), each returning a promise, and it reads the
   structure through its own routes. While one runs the dialog says so; if it fails the sentence
   it failed with is shown here and the dialog stays open where it was, so nobody loses the
   folder they had walked to.

       HLPicker.open({
         api: (path, body) => fetchJson(path, body),     // the page's own, carrying its token
         places: "/api/places",                           // {places: [{label, path, kind}]}
         browse: "/api/browse?want=structure",            // one folder, as hope_labs/folding/browse.py lists it
         accept: ".pdb,.ent,.cif,.mmcif",
         onFile, onPath, onCode, onRun,
       });
*/
(function () {
  "use strict";

  var ICON = {
    folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2h9A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 13c1.5-2 4.5-2 6 0M9 16c1.5 2 4.5 2 6 0"/>',
    up: '<path d="M12 19V6M6 11l6-6 6 6"/>',
    computer: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    code: '<path d="M4 7h16M4 12h16M4 17h10"/>',
    home: '<path d="M4 11l8-7 8 7v9H4z"/><path d="M10 20v-5h4v5"/>',
    scratch: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    runs: '<path d="M5 4h14v16H5z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    group: '<circle cx="9" cy="9" r="3"/><circle cx="16" cy="10" r="2.5"/><path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5M14.5 15c2.6 0 4.4 1.3 5 4"/>',
    run: '<path d="M7 4l11 8-11 8z"/>',
    chev: '<path d="M9 6l6 6-6 6"/>',
  };
  function svg(name) {
    return '<svg class="hlp-ic" viewBox="0 0 24 24" aria-hidden="true">' + (ICON[name] || ICON.file) + "</svg>";
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function size(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return Math.round(n / 1024) + " kB";
    if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
    return (n / 1073741824).toFixed(2) + " GB";
  }

  var dlg = null, opts = null, places = [], at = "", lit = "", settle = null, closing = false;
  /* True while the dialog is being opened and walked to the folder it was last left in. The
     places list is scrolled to its top then, whichever place that folder lights: at the larger
     text sizes the list is taller than the dialog, and scrolling it to a place far down it put
     This computer, PDB code, My scratch and Home out of sight the moment it opened. */
  var opening = false;

  function q(sel) { return dlg.querySelector(sel); }

  function build() {
    dlg = el("dialog", "hlp hlmodal");
    dlg.setAttribute("aria-labelledby", "hlp-title");
    dlg.innerHTML =
      '<div class="hlp-head"><h2 id="hlp-title">Choose a structure</h2>' +
      '<button type="button" class="hlp-x" aria-label="Close" title="Close (Esc)">\u00d7</button></div>' +
      '<div class="hlp-body">' +
      '<nav class="hlp-places" aria-label="Places"></nav>' +
      '<section class="hlp-main" aria-label="Folder">' +
      '<div class="hlp-bar"><div class="hlp-crumbs"></div>' +
      '<input class="hlp-filter" type="search" placeholder="Filter" aria-label="Filter this folder" autocomplete="off"></div>' +
      '<div class="hlp-list"></div></section></div>' +
      '<div class="hlp-foot"><span class="hlp-says" role="status"></span>' +
      '<button type="button" class="btn sm hlp-new" hidden>New folder</button>' +
      '<button type="button" class="btn sm pri hlp-use" hidden>Use this folder</button>' +
      '<button type="button" class="btn sm hlp-cancel">Cancel</button></div>' +
      '<input type="file" class="hlp-file" hidden>';
    document.body.appendChild(dlg);
    q(".hlp-x").onclick = function () { close(); };
    q(".hlp-cancel").onclick = function () { close(); };
    dlg.addEventListener("cancel", function (e) {
      // Escape closes, unless something is being read: closing then would leave the page
      // reading a structure nobody is waiting for.
      if (dlg.classList.contains("busy")) { e.preventDefault(); return; }
      e.preventDefault();
      close();
    });
    dlg.addEventListener("close", function () { finish(false); });
    q(".hlp-file").addEventListener("change", function () {
      var f = this.files && this.files[0];
      this.value = "";
      if (f) take(function () { return opts.onFile(f); }, "Sending " + f.name + "\u2026");
    });
    q(".hlp-filter").addEventListener("input", filter);
    q(".hlp-use").onclick = function () {
      if (opts && opts.onFolder && at) take(function () { return opts.onFolder(at); }, "\u2026");
    };
    q(".hlp-new").onclick = newFolder;
    dlg.addEventListener("keydown", keys);
    // A file dropped anywhere on the dialog is the same as choosing it on This computer.
    dlg.addEventListener("dragover", function (e) { if (opts && opts.onFile) { e.preventDefault(); dlg.classList.add("over"); } });
    dlg.addEventListener("dragleave", function (e) { if (e.target === dlg) dlg.classList.remove("over"); });
    dlg.addEventListener("drop", function (e) {
      dlg.classList.remove("over");
      if (!opts || !opts.onFile) return;
      e.preventDefault();
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) take(function () { return opts.onFile(f); }, "Sending " + f.name + "\u2026");
    });
  }

  function says(text, bad) {
    var s = q(".hlp-says");
    s.textContent = text || "";
    s.classList.toggle("bad", !!bad);
  }

  /* One way in, run with the dialog marked busy. A promise that resolves closes the dialog; one
     that rejects leaves it open with the reason. */
  function take(job, saying) {
    if (dlg.classList.contains("busy")) return;
    dlg.classList.add("busy");
    says(saying || "Reading\u2026");
    var done;
    try { done = Promise.resolve(job()); } catch (e) { done = Promise.reject(e); }
    done.then(function () {
      dlg.classList.remove("busy");
      finish(true);
      close();
    }, function (why) {
      dlg.classList.remove("busy");
      says((why && why.message) || String(why || "That could not be read."), true);
    });
  }

  function close() {
    if (closing) return;
    closing = true;
    try { if (dlg.open) dlg.close(); } catch (e) { /* already closed */ }
    closing = false;
    finish(false);
  }

  function finish(chosen) {
    if (!settle) return;
    var s = settle;
    settle = null;
    s({ chosen: !!chosen, at: at });
  }

  function remember(path) {
    try { localStorage.setItem("hl-picker-at:" + (opts.key || "page"), path); } catch (e) { /* not kept */ }
  }
  function recalled() {
    try { return localStorage.getItem("hl-picker-at:" + (opts.key || "page")) || ""; } catch (e) { return ""; }
  }

  function placeRow(kind, label, onclick, title) {
    var b = el("button", "hlp-place");
    b.type = "button";
    b.dataset.kind = kind;
    b.innerHTML = svg(kind);
    b.appendChild(el("span", "hlp-pl", label));
    if (title) b.title = title;
    b.onclick = onclick;
    return b;
  }

  function drawPlaces() {
    var nav = q(".hlp-places");
    nav.innerHTML = "";
    if (opts.onFile) {
      nav.appendChild(placeRow("computer", "This computer", function () {
        light(this);
        showComputer();
        q(".hlp-file").click();
      }, "Choose a file on this computer and send it up"));
    }
    if (opts.onCode) {
      nav.appendChild(placeRow("code", "PDB code", function () { light(this); showCode(); },
        "Fetch an entry from the RCSB"));
    }
    if (opts.onFile || opts.onCode) nav.appendChild(el("div", "hlp-sep"));
    /* The places in their sections. "Other tools" starts closed: somebody who has used every
       tool has a dozen run folders there, and the list is for finding a place, not for reading. */
    var section = "", group = nav;
    places.forEach(function (p) {
      if ((p.section || "") !== section) {
        section = p.section || "";
        if (section) {
          var count = places.filter(function (o) { return o.section === section; }).length;
          var head = el("button", "hlp-sec");
          head.type = "button";
          head.appendChild(el("span", null, section));
          if (count > 3) head.appendChild(el("span", "hlp-count", String(count)));
          group = el("div", "hlp-group");
          group.dataset.section = section;
          var closed = count > 3 && opened(section) !== "1";
          group.hidden = closed;
          head.setAttribute("aria-expanded", closed ? "false" : "true");
          (function (head, group, section) {
            head.onclick = function () {
              group.hidden = !group.hidden;
              head.setAttribute("aria-expanded", group.hidden ? "false" : "true");
              opened(section, group.hidden ? "0" : "1");
            };
          })(head, group, section);
          nav.appendChild(head);
          nav.appendChild(group);
        } else {
          group = nav;
        }
      }
      var row = placeRow(p.kind || "folder", p.label, function () { light(this); list(p.path); }, p.path);
      row.dataset.path = p.path;
      group.appendChild(row);
    });
    if (!places.length) nav.appendChild(el("p", "hlp-empty", "No folders to offer."));
  }

  function light(row) {
    Array.prototype.forEach.call(dlg.querySelectorAll(".hlp-place"), function (b) {
      b.classList.toggle("on", b === row);
      b.setAttribute("aria-current", b === row ? "true" : "false");
    });
    lit = row ? (row.dataset.path || row.dataset.kind) : "";
    // A place inside a closed section is opened to, so the lit place is always in view.
    var group = row && row.closest(".hlp-group");
    if (group && group.hidden) {
      group.hidden = false;
      var head = group.previousElementSibling;
      if (head) head.setAttribute("aria-expanded", "true");
    }
    // Pressed, it is scrolled to; lit because the dialog opened on a folder inside it, it is not:
    // the list stays at its top, where the first places are.
    if (row && row.scrollIntoView && !opening) row.scrollIntoView({ block: "nearest" });
  }

  function opened(section, value) {
    var key = "hl-picker-open:" + section;
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { /* not kept */ }
    return null;
  }

  function placeOf(path) {
    var best = null;
    places.forEach(function (p) {
      if (path === p.path || path.indexOf(p.path.replace(/\/$/, "") + "/") === 0) {
        if (!best || p.path.length > best.path.length) best = p;
      }
    });
    return best;
  }

  function crumbs(path) {
    var box = q(".hlp-crumbs");
    box.innerHTML = "";
    var place = placeOf(path);
    var base = place ? place.path.replace(/\/$/, "") : "";
    var parts = [];
    if (place) parts.push({ name: place.label, path: place.path });
    else parts.push({ name: "/", path: "/" });
    var rest = place ? path.slice(base.length) : path;
    var walk = place ? base : "";
    rest.split("/").filter(Boolean).forEach(function (name) {
      walk += "/" + name;
      parts.push({ name: name, path: walk });
    });
    parts.forEach(function (c, n) {
      if (n) box.appendChild(el("span", "hlp-cs", "\u203a"));
      if (n === parts.length - 1) {
        box.appendChild(el("span", "hlp-c here", c.name));
      } else {
        var b = el("button", "hlp-c", c.name);
        b.type = "button";
        b.onclick = function () { list(c.path); };
        box.appendChild(b);
      }
    });
    box.title = path;
    var place2 = placeOf(path);
    light(place2 ? dlg.querySelector('.hlp-place[data-path="' + cssEscape(place2.path) + '"]') : null);
  }

  function cssEscape(s) {
    return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&");
  }

  function row(icon, name, extra, cls) {
    var b = el("button", "hlp-row" + (cls ? " " + cls : ""));
    b.type = "button";
    b.innerHTML = svg(icon);
    b.appendChild(el("span", "hlp-name", name));
    if (extra != null) b.appendChild(el("span", "hlp-size", extra));
    return b;
  }

  function showComputer() {
    listing += 1;
    q(".hlp-bar").hidden = true;
    var box = q(".hlp-list");
    box.innerHTML = "";
    var pane = el("div", "hlp-pane");
    pane.appendChild(el("p", "hlp-lead", "Choose a structure file on this computer."));
    var b = el("button", "btn pri", "Choose a file\u2026");
    b.type = "button";
    b.onclick = function () { q(".hlp-file").click(); };
    pane.appendChild(b);
    pane.appendChild(el("p", "hlp-hint", "Or drop it here. " + (opts.acceptSays || "")));
    box.appendChild(pane);
    says("");
  }

  function showCode() {
    listing += 1;
    q(".hlp-bar").hidden = true;
    var box = q(".hlp-list");
    box.innerHTML = "";
    var pane = el("div", "hlp-pane");
    var label = el("label", "hlp-lead", "PDB code");
    label.htmlFor = "hlp-code";
    pane.appendChild(label);
    var line = el("div", "hlp-codeline");
    var input = el("input", "inp hlp-code");
    input.id = "hlp-code";
    input.placeholder = "1YCR";
    input.maxLength = 4;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.addEventListener("input", function () { input.value = input.value.toUpperCase(); });
    var go = el("button", "btn pri", "Fetch");
    go.type = "button";
    var fetchIt = function () {
      var code = input.value.trim();
      if (!/^[A-Za-z0-9]{4}$/.test(code)) { says("A PDB code is four characters, like 1YCR.", true); input.focus(); return; }
      take(function () { return opts.onCode(code.toUpperCase()); }, "Fetching " + code.toUpperCase() + " from the RCSB\u2026");
    };
    go.onclick = fetchIt;
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); fetchIt(); } });
    line.appendChild(input);
    line.appendChild(go);
    pane.appendChild(line);
    pane.appendChild(el("p", "hlp-hint", "Fetched from the RCSB."));
    box.appendChild(pane);
    says("");
    setTimeout(function () { input.focus(); }, 0);
  }

  var listing = 0;
  function list(path) {
    // Only the folder pressed last is drawn: two pressed in quick succession answer in either
    // order, and the first answer arriving second would put the wrong folder on show.
    var mine = ++listing;
    q(".hlp-bar").hidden = false;
    q(".hlp-filter").value = "";
    var box = q(".hlp-list");
    box.innerHTML = "";
    box.appendChild(el("p", "hlp-empty", "Reading\u2026"));
    says("");
    var url = opts.browse + (opts.browse.indexOf("?") >= 0 ? "&" : "?") + "path=" + encodeURIComponent(path);
    return Promise.resolve(opts.api(url)).then(function (got) {
      if (mine !== listing) return;
      box.innerHTML = "";
      if (got.error && !got.path) { box.appendChild(el("p", "hlp-empty bad", got.error)); crumbs(path); return; }
      at = got.path || path;
      remember(at);
      crumbs(at);
      var place = placeOf(at);
      var top = place && (at === place.path || at === place.path.replace(/\/$/, ""));
      if (!top && got.parent && got.parent !== at) {
        var up = row("up", "Up one level", null, "up");
        up.onclick = function () { list(got.parent); };
        box.appendChild(up);
      }
      if (got.error) box.appendChild(el("p", "hlp-empty bad", got.error));
      if (got.run && got.run.structure && (opts.onRun || opts.onPath) && !opts.folders) {
        var use = row("run", "Use this run\u2019s structure", got.run.file || got.run.name || "", "userun");
        use.title = got.run.structure;
        use.onclick = function () {
          take(function () { return opts.onRun ? opts.onRun(at, got.run) : opts.onPath(got.run.structure); },
               "Reading " + (got.run.name || "the run") + "\u2026");
        };
        box.appendChild(use);
      }
      (got.dirs || []).forEach(function (d) {
        var r = row("folder", d.name, null, "dir");
        r.appendChild(document.createRange().createContextualFragment(svg("chev")));
        r.dataset.name = d.name.toLowerCase();
        r.onclick = function () { list(d.path); };
        box.appendChild(r);
      });
      var kinds = (opts.kinds || []).map(function (k) { return k.toLowerCase(); });
      var shown = 0;
      (got.files || []).forEach(function (f) {
        if (kinds.length && !kinds.some(function (k) { return f.name.toLowerCase().slice(-k.length) === k; })) return;
        var r = row("file", f.name, size(f.size), opts.folders ? "file off" : "file");
        r.dataset.name = f.name.toLowerCase();
        r.title = f.path;
        if (opts.folders) r.disabled = true;
        else r.onclick = function () { take(function () { return opts.onPath(f.path); }, "Reading " + f.name + "\u2026"); };
        box.appendChild(r);
        shown += 1;
      });
      if (!(got.dirs || []).length && !shown && !(got.run && got.run.structure)) {
        box.appendChild(el("p", "hlp-empty", opts.folders ? "No folders here." : "No structure files here."));
      }
      if (got.truncated) says("Showing the first " + (got.most || "") + " entries. Filter to find one.");
      q(".hlp-use").hidden = !opts.folders;
      q(".hlp-new").hidden = !(opts.folders && opts.mkdir && got.writable !== false);
      // The row that was pressed has just been drawn over, which leaves the focus nowhere; it
      // goes to the first row, so the keyboard carries on from where the person is.
      var first = box.querySelector(".hlp-row");
      var now = document.activeElement;
      if (first && (!now || now === document.body || !dlg.contains(now))) first.focus();
    }, function (why) {
      if (mine !== listing) return;
      box.innerHTML = "";
      box.appendChild(el("p", "hlp-empty bad", (why && why.message) || "That folder could not be read."));
    });
  }

  function filter() {
    var want = q(".hlp-filter").value.trim().toLowerCase();
    Array.prototype.forEach.call(dlg.querySelectorAll(".hlp-row.dir,.hlp-row.file"), function (r) {
      r.hidden = !!want && (r.dataset.name || "").indexOf(want) < 0;
    });
  }

  function newFolder() {
    var name = prompt("Name for the new folder");
    if (!name) return;
    Promise.resolve(opts.api(opts.mkdir, { parent: at, name: name })).then(function (made) {
      if (made && made.error) { says(made.error, true); return; }
      list((made && made.path) || at);
    }, function (why) { says((why && why.message) || "The folder could not be made.", true); });
  }

  function keys(e) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    var inList = e.target.closest && (e.target.closest(".hlp-list") || e.target.closest(".hlp-places"));
    if (!inList) {
      if (e.key === "ArrowDown" && e.target.classList.contains("hlp-filter")) {
        var first = q(".hlp-list .hlp-row:not([hidden])");
        if (first) { e.preventDefault(); first.focus(); }
      }
      return;
    }
    var rows = Array.prototype.filter.call(inList.querySelectorAll(".hlp-row,.hlp-place,.hlp-sec"), function (r) {
      return !r.hidden && !r.disabled && !(r.closest(".hlp-group") && r.closest(".hlp-group").hidden);
    });
    var i = rows.indexOf(e.target.closest(".hlp-row,.hlp-place,.hlp-sec"));
    if (i < 0) return;
    e.preventDefault();
    var next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) next.focus();
  }

  function open(options) {
    if (!dlg) build();
    if (settle) finish(false);
    opts = Object.assign({ title: "Choose a structure", browse: "/api/browse?want=structure", places: "/api/places" }, options || {});
    q("#hlp-title").textContent = opts.title;
    q(".hlp-file").accept = opts.accept || "";
    q(".hlp-use").textContent = opts.useLabel || "Use this folder";
    q(".hlp-use").hidden = !opts.folders;
    q(".hlp-new").hidden = true;
    dlg.classList.toggle("folders", !!opts.folders);
    dlg.classList.remove("busy");
    says("");
    var done = new Promise(function (resolve) { settle = resolve; });
    if (!dlg.open) dlg.showModal();
    q(".hlp-list").innerHTML = "";
    q(".hlp-list").appendChild(el("p", "hlp-empty", "Reading\u2026"));
    Promise.resolve(opts.api(opts.places)).then(function (got) {
      places = (got && got.places) || [];
      opening = true;
      drawPlaces();
      q(".hlp-places").scrollTop = 0;
      var start = opts.start || recalled();
      var place = start && placeOf(start);
      var walked = null;
      var walked_done = function () { opening = false; };
      if (opts.startWith === "code" && opts.onCode) {
        light(dlg.querySelector('.hlp-place[data-kind="code"]'));
        showCode();
        opening = false;
        return;
      }
      /* The folder about to be read is named and lit BEFORE it is read. The breadcrumbs and the
         lit place used to be drawn by list() when its answer came back, so the first frame of
         the dialog was a blank pane reading "Reading..." with an empty bar above it and nothing
         lit down the side: for the length of a round trip it said nothing at all about where it
         had opened. Nothing here waits on the server - the places are already in hand, and which
         place a path belongs to is arithmetic on strings. */
      var where = start && place ? start : (places.length ? places[0].path : "");
      if (where) {
        q(".hlp-bar").hidden = false;
        crumbs(where);
      }
      if (start && place) walked = list(start);
      else if (places.length) walked = list(places[0].path);
      else if (opts.onFile) { light(dlg.querySelector('.hlp-place[data-kind="computer"]')); showComputer(); }
      Promise.resolve(walked).then(walked_done, walked_done);
    }, function (why) {
      places = [];
      drawPlaces();
      says((why && why.message) || "The folders could not be read.", true);
      if (opts.onFile) showComputer();
    });
    return done;
  }

  window.HLPicker = { open: open, close: close, size: size };
})();
