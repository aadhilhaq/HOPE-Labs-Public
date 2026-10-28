/* HOPE Labs: the catalog, the tool shown inside the shell, and the results across every tool.
   The page talks only to the launcher running on this computer; the launcher holds the one SSH
   connection and starts a tool on the login node when a card asks it to. */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

/* The text size this page is drawn at, written into the address of a tab it opens, so the one
   choice follows the person from page to page. Said at 100% as well: a tool's page remembers its
   own size, and a page last left at 150% would otherwise open there whatever this one says. */
function withScale(address) {
  if (!window.HLZoom) return address;
  try {
    const url = new URL(address, location.href);
    url.searchParams.set("ui_scale", String(window.HLZoom.get()));
    return url.toString();
  } catch (e) { return address; }
}

/* What a ? explains: a short sheet over the page, rather than a paragraph on it. */
function explain(title, text, more) {
  const box = bigSheet(title, false);
  box.sheet.classList.add("narrow");
  String(text || "").split(/\n\n+/).filter((p) => p.trim())
    .forEach((para) => box.body.appendChild(el("p", "infotext", para)));
  if (more) box.body.appendChild(more);
  return box;
}

/* A ? that opens it. `more`, when it is a function, is called as the sheet opens, so what it
   shows is the state at that moment. */
function qButton(title, text, more) {
  const q = el("button", "q", "?");
  q.type = "button";
  q.title = "About " + title;
  q.setAttribute("aria-label", "About " + title);
  q.onclick = (e) => {
    e.stopPropagation();
    explain(title, text, typeof more === "function" ? more() : more);
  };
  return q;
}

/* A ? written into the page carries its own words: data-title and data-explain. */
document.addEventListener("click", (e) => {
  const q = e.target && e.target.closest ? e.target.closest("[data-explain]") : null;
  if (!q) return;
  e.preventDefault();
  explain(q.getAttribute("data-title") || "", q.getAttribute("data-explain") || "");
});

async function api(path, body) {
  const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const answer = await fetch(url, init);
  let data = {};
  try { data = await answer.json(); } catch (e) { data = { error: "the launcher sent something unreadable (" + answer.status + ")" }; }
  if (!answer.ok) throw new Error(data.error || "the launcher answered " + answer.status);
  return data;
}

let flashTimer = 0;
/* What just happened, said where it can be seen and shut when it has been read.

   It used to be a bare white box with a sentence in it, laid across whatever was under it: over
   the flow drawing and over the top of the settings column, with no name, no X and nothing
   joining it to anything, so it could be read as a card, a caption or a message - and it took
   the clicks of the "?" it covered. Now it says which it is along its top and carries an X, and
   it is tied to the head of the view it belongs to. */
function flash(text, bad) {
  clearTimeout(flashTimer);
  document.querySelectorAll(".flash").forEach((n) => n.remove());
  const box = el("div", "flash" + (bad ? " bad" : ""));
  box.setAttribute("role", bad ? "alert" : "status");
  box.id = "flash";
  const head = el("div", "flashhead");
  head.appendChild(el("span", "flashwhat", bad ? "That did not work" : "Done"));
  const shut = el("button", "flashx", "\u00d7");
  shut.type = "button";
  shut.title = "Close this message";
  shut.setAttribute("aria-label", "Close this message");
  shut.onclick = () => { box.remove(); window.dispatchEvent(new Event("resize")); };
  head.appendChild(shut);
  box.appendChild(head);
  box.appendChild(el("div", "flashsaid", text));
  /* It goes into the view, under its head, as a row of the page: laid over the page it covered
     whatever was under it - the settings column, the "?" beside a card, the head's own buttons -
     and a box with no edge of its own that sits on top of a drawing is the box nobody could
     explain. A row cannot cover anything. The drawing is re-fitted after it, because the canvas
     is now a little shorter than it was. */
  const view = Array.prototype.find.call(document.querySelectorAll(".main > section"), (s) => !s.hidden);
  const top = view && view.querySelector(".top");
  if (top && top.parentNode) top.parentNode.insertBefore(box, top.nextSibling);
  else document.body.appendChild(box);
  window.dispatchEvent(new Event("resize"));
  flashTimer = setTimeout(() => {
    box.remove();
    window.dispatchEvent(new Event("resize"));
  }, bad ? 16000 : 7000);
}

/* ------------------------------------------------------------------ state */
let STATE = { tools: [], categories: [] };
let VIEW = "tools";          // tools | runs | a tool key
let CATEGORY = "";
let QUERY = "";
let RUNFILTER = "";

const byKey = (key) => STATE.tools.find((t) => t.key === key);

/* the mark on each card: one glyph per kind of work, drawn rather than fetched */
const GLYPH = {
  _plain: '<rect x="5" y="5" width="14" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>',
  // RFdiffusion3: the same diffusing cloud as RFdiffusion, but resolved to single atoms -
  // which is the whole difference between them.
  rfd3: '<circle cx="7" cy="8" r="1.5" fill="currentColor"/><circle cx="12" cy="5.5" r="1.5" fill="currentColor"/><circle cx="17" cy="8" r="1.5" fill="currentColor"/><circle cx="8.5" cy="13.5" r="1.5" fill="currentColor"/><circle cx="15.5" cy="13.5" r="1.5" fill="currentColor"/><circle cx="12" cy="18.5" r="1.5" fill="currentColor"/>',
  // RFD4-Proteina: the target's edge with three marked points on it, and a binder coming in to
  // meet them, which is what asking for a design by what has to be in the right place looks like.
  rfd4: '<path d="M5 3.5 C8.5 7.5, 8.5 16.5, 5 20.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="8.5" cy="7.5" r="1.15" fill="currentColor"/><circle cx="9.5" cy="12" r="1.15" fill="currentColor"/><circle cx="8.5" cy="16.5" r="1.15" fill="currentColor"/><path d="M13 6 L19 9 L19 15 L13 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>',
  hopemd: '<path d="M4 16 L8 7 L12 14 L16 9 L20 13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  // BioEmu: one chain drawn three times over, the same fold in three arrangements, which is an
  // ensemble and is nothing HOPE-MD's single trace could be mistaken for.
  bioemu: '<path d="M4 19 C7 13, 10 16, 12 10 C14 5, 17 7, 20 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.4"/><path d="M4 19 C8 15, 9 12, 12 11 C15 10, 16 6, 20 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.65"/><path d="M4 19 C6 16, 11 15, 12 12 C13 9, 18 8, 20 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  // A backbone coming out of noise: scattered points on the left settling into a helix on the
  // right, which is what diffusing a backbone onto a target looks like from a distance.
  rfdiffusion: '<circle cx="4.5" cy="6" r="1.1" fill="currentColor"/><circle cx="4" cy="12.5" r="1.1" fill="currentColor"/><circle cx="6" cy="18" r="1.1" fill="currentColor"/><path d="M10 18 C16 16, 10 13, 16 11 C10 9, 16 6, 13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  // A binder taking shape against a surface: the target as an arc down one side, the thing being
  // generated as a closed form beside it.
  boltzgen: '<path d="M6 3 C2.5 7, 2.5 17, 6 21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M12 7.5 L17.5 10.5 L17.5 16 L12 19 L10 13.5 Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
  // The two folding tools, and they need two marks rather than one between them: both fold, and
  // which of them a person wants depends on what they are folding, so a tile that cannot be told
  // from the tile beside it is the one place the catalog has to be clear. Each is the mark its
  // own page already wears, in hope_labs/folding/docs.py, in one color instead of two: the same
  // shape on the card, on the page and in the page's tab.
  //
  // Boltz-2's: a helix and a strand brought together with a small molecule between them, which
  // is the thing it does that nothing else here does.
  boltz2: '<path d="M4.5 4 C8 7, 8 13, 4.5 16 C8 18, 8 20, 6.5 21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M19 4 L19 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="12" r="2.6" fill="none" stroke="currentColor" stroke-width="2"/>',
  // ColabFold's: one chain settling into a fold, drawn as a single line because one sequence in
  // and one structure out is the whole of what it does.
  colabfold: '<path d="M4 20 C4 15, 9 16, 9 12 C9 8, 4 9, 5 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M13 4 C18 6, 18 10, 14 11 C19 13, 19 18, 13.5 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
};

function glyph(key, size) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size || 21);
  svg.setAttribute("height", size || 21);
  // A tool with no mark of its own gets a plain one rather than another tool's: borrowing made
  // two new tools look like the simulation, which reads as a relationship that is not there.
  svg.innerHTML = GLYPH[key] || GLYPH._plain;
  return svg;
}

/* ------------------------------------------------------------------ the rail */
/* The three views, then what is running. It used to list all fifteen tools under "Open a tool",
   which was the Tools page's own tiles again in the column beside them, and on Flows it was the
   card palette again as well: every name on the screen twice, with nothing to say what pressing
   one of them did that pressing the other did not. The column's job here is the one the tiles
   cannot do - what is up now, from wherever you are - and Tools is one press away above it. */
function paintRail() {
  const box = $("railtools");
  box.innerHTML = "";
  const up = STATE.tools.filter((tool) => tool.running);
  const label = $("raillabel");
  if (label) label.textContent = up.length ? "Running now" : "Nothing running";
  up.forEach((tool) => {
    const row = el("button", "ritem");
    row.appendChild(el("span", "dot up"));
    row.appendChild(el("span", "nm", tool.name));
    row.title = "Open " + tool.name;
    row.onclick = () => open_tool(tool.key);
    box.appendChild(row);
  });
  if (!up.length) {
    box.appendChild(el("p", "ritem quiet railnone", "Start one from Tools."));
  }
  document.querySelectorAll(".ritem[data-view]").forEach((b) => {
    b.classList.toggle("on", VIEW === b.dataset.view);
    b.onclick = () => show(b.dataset.view);
  });
  // Reachable from wherever a person is, because the thing worth saying is rarely said on the
  // page that prompted it. A flow that failed offers its own, with that flow already in it.
  const say = $("railsay");
  if (say) say.onclick = () => openReport("feedback", {});
  // Set up, for a launcher that has it: what this cluster has and which tools are installed.
  const setup = $("railsetup");
  if (setup) {
    setup.hidden = !(STATE.setup && STATE.labs && STATE.labs.found);
    setup.onclick = () => open_setup();
  }
}

/* The Set up page opens in a tab like a tool's, because it is started like one, on the login
   node, where installing has a network. */
function open_setup(button) {
  if (!STATE.setup) { flash("This launcher has no Set up page: download the current release.", true); return; }
  openWindow("setup");
  if (button) { button.disabled = true; setTimeout(() => { button.disabled = false; }, 4000); }
}

/* ------------------------------------------------------------------ a cluster new to HOPE Labs */
let BOOTING = 0;
function paintNewCluster() {
  const labs = STATE.labs || {};
  const missing = labs.found === false;
  $("newcluster").hidden = !missing;
  $("grid").hidden = missing;
  if (!missing) return;
  $("nc-where").textContent = STATE.address || STATE.host || "this cluster";
  if (!$("nc-folder").value && labs.suggest) $("nc-folder").value = labs.suggest;
  // A launcher from before 0.9.5 does not list the spaces at all, which is not the same as still
  // listing them: it gets the two boxes below, as it always had.
  if ("spaces" in labs) paintSpaces(labs.spaces);
  paintBoot(labs.boot || {});
}

/* Every space the person has on the cluster, found for them: a HOPE Labs already in one is used
   with one click, and any other they can write to fills the folder to set up in. Their home is
   listed, so nobody wonders where it went, and never offered. */
let SPACES_ASKED = 0;
function paintSpaces(spaces) {
  const box = $("nc-spaces");
  if (!box) return;
  box.innerHTML = "";
  if (spaces === null || spaces === undefined) {
    box.appendChild(el("div", "why", "Looking through your spaces…"));
    clearTimeout(SPACES_ASKED);
    SPACES_ASKED = setTimeout(async () => {
      try { const labs = await api("/api/labs"); STATE.labs = labs; paintSpaces(labs.spaces); }
      catch (e) { paintSpaces(null); }
    }, 2000);
    return;
  }
  if (!spaces.length) return;
  box.appendChild(el("div", "why", "Your spaces"));
  for (const sp of spaces) {
    const has = (sp.labs || [])[0];
    const usable = sp.writable && !sp.home;
    const row = el("div", "ncspace" + (has ? " has" : "") + (usable || has ? "" : " off"));
    row.appendChild(el("span", "where", sp.path));
    const bits = [];
    if (sp.free_gb !== null && sp.free_gb !== undefined) bits.push(Math.round(sp.free_gb).toLocaleString("en-US") + " GB");
    if (sp.free_files !== null && sp.free_files !== undefined) bits.push(Number(sp.free_files).toLocaleString("en-US") + " files");
    // A tool takes tens of thousands of files, so a space with fewer than that left is said so.
    const low = sp.free_files !== null && sp.free_files !== undefined && sp.free_files < 100000;
    row.appendChild(el("span", "room" + (low ? " low" : ""),
      sp.home ? "Home: too few files allowed"
        : !sp.writable ? "Not yours to write in"
        : bits.length ? bits.join(", ") + " free" + (low ? ", too few files" : "") : ""));
    if (has) {
      const use = el("button", "btn pri sm", "Use the HOPE Labs here");
      use.title = has.path;
      use.onclick = async () => {
        try {
          await api("/api/labs/use", { path: has.path });
          await refresh();
          flash("Using HOPE Labs in " + (STATE.labs || {}).root + ". This computer remembers it for this cluster.");
        } catch (e) { flash(e.message, true); }
      };
      row.appendChild(use);
    } else if (usable) {
      const pick = el("button", "btn sm", "Set up here");
      pick.onclick = () => {
        $("nc-folder").value = sp.path.replace(/\/$/, "") + "/hope-labs";
        $("nc-setup").focus();
        flash("Now press Set HOPE Labs up here.");
      };
      row.appendChild(pick);
    }
    box.appendChild(row);
  }
}

function paintBoot(boot) {
  const log = $("nc-log");
  const running = boot.state === "running";
  $("nc-setup").disabled = running;
  $("nc-use").disabled = running;
  if (!boot.state) { log.hidden = true; return; }
  log.hidden = false;
  const lines = (boot.log || []).map((l) => l.replace(/^HOPE-LABS(-FAILED)? /, ""));
  if (boot.state === "failed") lines.push("Stopped: " + (boot.problem || "no reason given"));
  log.textContent = lines.join("\n") || "starting...";
  log.scrollTop = log.scrollHeight;
}

async function watchBoot() {
  clearTimeout(BOOTING);
  try {
    const labs = await api("/api/labs");
    STATE.labs = labs;
    paintBoot(labs.boot || {});
    if ((labs.boot || {}).state === "running") { BOOTING = setTimeout(watchBoot, 1500); return; }
    if ((labs.boot || {}).state === "done") {
      $("nc-next").hidden = false;
      $("nc-setup").disabled = true;
      // Said as done, with only the log and the way on left: the questions are answered.
      $("nc-title").textContent = "HOPE Labs is set up on " + (STATE.address || "this cluster");
      $("nc-ask").hidden = true;
      await refresh();
      flash("HOPE Labs is set up in " + labs.root + ". Now choose the tools to install.");
      $("newcluster").hidden = false;
      $("grid").hidden = true;
    }
  } catch (e) { BOOTING = setTimeout(watchBoot, 3000); }
}

if ($("nc-setup")) $("nc-setup").onclick = async () => {
  const folder = $("nc-folder").value.trim();
  if (!folder) { flash("Name a folder first.", true); return; }
  try {
    const labs = await api("/api/labs/setup", { folder: folder, proxy: ($("nc-proxy") || {}).value || "" });
    STATE.labs = labs;
    paintBoot(labs.boot || {});
    watchBoot();
  } catch (e) { flash(e.message, true); }
};
if ($("nc-use")) $("nc-use").onclick = async () => {
  const path = $("nc-path").value.trim();
  if (!path) { flash("Name the folder HOPE Labs is in.", true); return; }
  try {
    await api("/api/labs/use", { path: path });
    await refresh();
    flash("Using HOPE Labs in " + (STATE.labs || {}).root + ". This computer remembers it for this cluster.");
  } catch (e) { flash(e.message, true); }
};
if ($("nc-open")) $("nc-open").onclick = () => { open_setup($("nc-open")); $("newcluster").hidden = true; $("grid").hidden = false; };

/* ------------------------------------------------------------------ the catalog */
/* What a tool takes and leaves, for the sheet its ? opens: the tile itself carries its name, one
   line on what it is for, and the buttons, and nothing a person has to read past to reach them. */
function toolFacts(tool) {
  const list = el("dl", "infolist");
  [["Takes", tool.takes], ["Leaves", tool.gives], ["Category", tool.category]].forEach(([word, what]) => {
    if (!what) return;
    list.appendChild(el("dt", null, word));
    list.appendChild(el("dd", null, what));
  });
  return list;
}

/* The buttons that hand a tool's work to the one that usually comes next. Named by that tool,
   with the step it stands for in the tooltip. */
function nextButtons(tool, acts) {
  tool.next_steps.forEach((step) => {
    const to = byKey(step.to);
    if (!to) return;
    const next = el("button", "btn sm next", "→ " + to.name);
    next.title = step.label;
    next.onclick = (e) => { e.stopPropagation(); open_tool(step.to); };
    acts.appendChild(next);
  });
}

function card(tool) {
  const box = el("div", "tool");
  const head = el("div", "head");
  const badge = el("div", "badge");
  badge.appendChild(glyph(tool.key));
  head.appendChild(badge);
  const titles = el("div", "titles");
  const h = el("h3");
  h.appendChild(document.createTextNode(tool.name));
  // Whether it is up, beside its name. Its category is the heading the tile sits under.
  if (tool.running) h.appendChild(el("span", "state up", "Running"));
  titles.appendChild(h);
  titles.appendChild(el("div", "tag", tool.tagline));
  head.appendChild(titles);
  head.appendChild(qButton(tool.name, tool.blurb, () => toolFacts(tool)));
  box.appendChild(head);

  const acts = el("div", "acts");
  // A tool with no page of its own is listed because somebody looking for it looks here, but
  // Launch would have nothing to open. Its tile offers the canvas, where it is actually run.
  if (tool.flow_only) {
    const draw = el("button", "btn pri", "Add to a flow");
    draw.title = tool.name + " runs from a flow: it has no page of its own";
    draw.onclick = () => {
      if (window.addFlowCard) window.addFlowCard(tool.key);
      else flash(tool.name + " is run from the Flows screen.", true);
    };
    acts.appendChild(draw);
    nextButtons(tool, acts);
    box.appendChild(acts);
    return box;
  }
  // Not on this cluster: said on the tile, with the way to install it, rather than found out from
  // a start that could only fail. Only a launcher that asks says so; one that does not, says
  // nothing, and the tile is as it always was.
  if (tool.installed === false) {
    h.appendChild(el("span", "state off", "Not installed"));
    const setup = el("button", "btn pri", "Set up…");
    setup.title = "Install " + tool.name + " on this cluster, from the Set up page";
    setup.onclick = () => open_setup(setup);
    acts.appendChild(setup);
    box.classList.add("absent");
    box.appendChild(acts);
    return box;
  }
  const go = el("button", "btn pri", tool.running ? "Open" : "Launch");
  go.onclick = () => open_tool(tool.key, go);
  acts.appendChild(go);
  if (tool.running) {
    const stop = el("button", "btn sm", "Stop");
    stop.onclick = async (e) => { e.stopPropagation(); await stop_tool(tool.key); };
    acts.appendChild(stop);
  }
  nextButtons(tool, acts);
  box.appendChild(acts);
  return box;
}

/* The tiles under their category, in the order the categories are listed, each with how many it
   holds; the chips above narrow them to one, and the search to what matches. */
function paintTools() {
  const grid = $("grid");
  grid.innerHTML = "";
  const q = QUERY.trim().toLowerCase();
  const shown = STATE.tools.filter((t) =>
    (!CATEGORY || t.category === CATEGORY) &&
    (!q || (t.name + " " + t.tagline + " " + t.blurb + " " + t.category).toLowerCase().includes(q)));
  const order = (STATE.categories || []).slice();
  shown.forEach((t) => { if (order.indexOf(t.category) < 0) order.push(t.category); });
  order.forEach((name) => {
    const mine = shown.filter((t) => t.category === name);
    if (!mine.length) return;
    const section = el("section", "toolsec");
    const title = el("h2", null, name);
    title.appendChild(el("span", "n", String(mine.length)));
    section.appendChild(title);
    const tiles = el("div", "grid");
    mine.forEach((tool) => tiles.appendChild(card(tool)));
    section.appendChild(tiles);
    grid.appendChild(section);
  });
  if (!shown.length) grid.appendChild(el("p", "empty", "No tool matches that."));
  const running = STATE.tools.filter((t) => t.running).length;
  $("toolcount").textContent = (shown.length === STATE.tools.length
    ? STATE.tools.length + " tools" : shown.length + " of " + STATE.tools.length)
    + (running ? " · " + running + " running" : "");

  const cats = $("cats");
  cats.innerHTML = "";
  const all = el("button", "chip" + (CATEGORY ? "" : " on"), "All");
  all.onclick = () => { CATEGORY = ""; paintTools(); };
  cats.appendChild(all);
  STATE.categories.forEach((name) => {
    const chip = el("button", "chip" + (CATEGORY === name ? " on" : ""), name);
    chip.onclick = () => { CATEGORY = CATEGORY === name ? "" : name; paintTools(); };
    cats.appendChild(chip);
  });
}

/* ------------------------------------------------------------------ views */
/* The rail's buttons name the view they show; the sections are flex columns, so each is hidden by
   display as well, which [hidden] alone would not do against it. */
const VIEWS = { tools: "view-tools", flow: "view-flow", runs: "view-runs" };

function show(view) {
  VIEW = view;
  Object.keys(VIEWS).forEach((name) => {
    const box = $(VIEWS[name]);
    if (!box) return;
    box.hidden = view !== name;
    box.style.display = box.hidden ? "none" : "flex";
  });
  if (view === "runs") loadRuns();
  if (view === "flow" && window.openFlows) window.openFlows();
  paintRail();
}

/* Each tool opens in a tab of its own, on the tool's own page, as its own launcher opens it. The tab
   is named after the tool, so picking a tool that is open already goes back to its tab instead of
   loading the page again over whatever is filled in there. The tab comes to the launcher first,
   which starts the tool and shows the wait, then goes on to the tool's page. */
function openWindow(key, from, view) {
  const tool = byKey(key);
  // With this page's text size, which the tab hands on to the tool's own page.
  const url = withScale("/tool?key=" + encodeURIComponent(key) + "&t=" + encodeURIComponent(TOKEN)
            + (from ? "&from=" + encodeURIComponent(from) : "")
            + (view ? "&view=" + encodeURIComponent(view) : ""));
  // A name and no address finds the tab already open under it, or opens an empty one; a plain
  // tab either way, with the address bar and the other tabs beside it.
  const win = window.open("", "hopelabs-" + key);
  if (!win) { flash("Chrome blocked the tab. Click the blocked pop-up mark in the address bar, choose Always allow, then try again.", true); return; }
  let empty = false, ours = false;
  try { empty = win.location.href === "about:blank"; ours = true; } catch (e) { /* the tool's own page, another address */ }
  // Load it when the tab is new, when a run is handed over, or when the tool has stopped since the
  // tab last showed it; a tab still starting the tool, or showing it, is only brought forward.
  if (empty || from || view || !(ours || (tool && tool.running))) win.location.href = url;
  win.focus();
}

/* Open the tool's tab inside the click, before anything is awaited. Chrome lets a page open a tab
   only while the click that asked for it is fresh (about five seconds), and starting a tool on the
   cluster takes far longer than that. So the tab opens at once and starts the tool itself, showing
   its progress where the person is looking. `from` is a run folder to hand over: the tab shows it
   with a Copy button before it goes on to the tool. */
function open_tool(key, button, from) {
  const tool = byKey(key);
  if (!tool) return;
  openWindow(key, from);
  if (!tool.running && button) {
    const was = button.textContent;
    button.disabled = true;
    button.textContent = "starting…";
    setTimeout(() => { button.disabled = false; button.textContent = was; }, 4000);
  }
  // the tab starts the tool; read the state back so this block says "running" when it is
  setTimeout(() => { refresh().catch(() => {}); }, 3000);
  setTimeout(() => { refresh().catch(() => {}); }, 20000);
}

async function stop_tool(key) {
  try {
    const got = await api("/api/stop", { tool: key });
    Object.assign(byKey(key) || {}, got);
    flash((got.name || "the tool") + " stopped.");
    paintTools(); paintRail();
  } catch (failure) { flash(failure.message, true); }
}


/* ------------------------------------------------------------------ results */
/* The runs whose results are open, kept across a refresh of the list so a person reading one
   does not have it shut under them every time the list is read again. */
const OPEN_RUNS = new Set();
/* A run's folder as it is shown under its name: the last three folders, with the whole path in
   the tooltip. In full it is a line of slashes that says little the run's name does not. */
function shortPath(path) {
  const parts = String(path || "").replace(/\/+$/, "").split("/");
  const shown = parts.length > 4 ? "\u2026/" + parts.slice(-3).join("/") : String(path || "");
  // Free to wrap after each slash, so a narrow column breaks it between folders, not inside a name.
  return shown.replace(/\//g, "/\u200b");
}

function when(seconds) {
  if (!seconds) return "";
  const gap = Date.now() / 1000 - seconds;
  if (gap < 3600) return Math.max(1, Math.round(gap / 60)) + " min ago";
  if (gap < 86400) return Math.round(gap / 3600) + " h ago";
  if (gap < 86400 * 14) { const days = Math.round(gap / 86400); return days + (days === 1 ? " day ago" : " days ago"); }
  return new Date(seconds * 1000).toISOString().slice(0, 10);
}

async function loadRuns(refresh) {
  const body = $("runbody");
  body.innerHTML = "";
  body.appendChild(el("p", "empty", "Reading the run folders…"));
  let runs = [];
  try { runs = (await api("/api/runs" + (refresh ? "?refresh=1" : ""))).runs; }
  catch (failure) { body.innerHTML = ""; body.appendChild(el("p", "empty", failure.message)); return; }

  const filter = $("runfilter");
  filter.innerHTML = "";
  const all = el("button", "chip" + (RUNFILTER ? "" : " on"), "All tools");
  all.onclick = () => { RUNFILTER = ""; loadRuns(); };
  filter.appendChild(all);
  STATE.tools.forEach((tool) => {
    const n = runs.filter((r) => r.tool === tool.key).length;
    if (!n) return;
    const chip = el("button", "chip" + (RUNFILTER === tool.key ? " on" : ""), tool.name);
    chip.appendChild(el("span", "n", String(n)));
    chip.onclick = () => { RUNFILTER = RUNFILTER === tool.key ? "" : tool.key; loadRuns(); };
    filter.appendChild(chip);
  });

  /* The same filter as one box. Fifteen chips are four rows of them at the larger text sizes on
     a laptop, and the runs they filter were pushed off the bottom of the screen; the sheet draws
     the chips or this, by the room the column has. Both set the same filter. */
  const pick = $("runfilterbox");
  if (pick) {
    pick.innerHTML = "";
    const box = el("select", "pick");
    box.setAttribute("aria-label", "Show one tool's runs");
    box.title = "Show one tool's runs";
    const every = el("option", null, "All tools");
    every.value = "";
    box.appendChild(every);
    STATE.tools.forEach((tool) => {
      const n = runs.filter((r) => r.tool === tool.key).length;
      if (!n) return;
      const one = el("option", null, tool.name + " " + n);
      one.value = tool.key;
      box.appendChild(one);
    });
    box.value = RUNFILTER && box.querySelector('option[value="' + RUNFILTER + '"]') ? RUNFILTER : "";
    box.onchange = () => { RUNFILTER = box.value; loadRuns(); };
    pick.appendChild(box);
  }

  const shown = runs.filter((r) => !RUNFILTER || r.tool === RUNFILTER);
  if ($("runcount")) {
    $("runcount").textContent = runs.length
      ? runs.length + (runs.length === 1 ? " run" : " runs") + ", newest first" : "";
  }
  body.innerHTML = "";
  if (!shown.length) {
    body.appendChild(el("p", "empty", "No runs yet. A tool's runs appear here once it has made one."));
    return;
  }
  const table = el("table", "runs");
  const head = el("tr");
  ["Run", "Tool", "When", "Next step"].forEach((h) => head.appendChild(el("th", null, h)));
  table.appendChild(el("thead")).appendChild(head);
  const tbody = el("tbody");
  shown.forEach((run) => {
    const tr = el("tr", "runrow");
    const first = el("td");
    const name = el("div", "runname");
    const chev = el("span", "chev", OPEN_RUNS.has(run.path) ? "\u25BE" : "\u25B8");
    name.appendChild(chev);
    name.appendChild(document.createTextNode(" " + run.name));
    first.appendChild(name);
    const folder = el("div", "runpath", shortPath(run.path));
    folder.title = run.path;
    first.appendChild(folder);
    tr.appendChild(first);
    const second = el("td");
    second.appendChild(el("span", "toolchip", run.tool_name));
    tr.appendChild(second);
    tr.appendChild(el("td", "runwhen", when(run.when)));
    // Looking at it comes first: a run nobody can open is a path and a date. Files reads the
    // folder itself, which works for every tool whatever its own page does, and the hand-offs
    // beside it start the next tool on this run.
    const last = el("td");
    const acts = el("div", "acts");
    const look = el("button", "btn sm", "What it made");
    look.title = "This run's structures, pictures, videos, reports and tables";
    look.onclick = () => tr.onclick({ target: tr });
    acts.appendChild(look);
    run.next_steps.forEach((step) => {
      const to = byKey(step.to);
      if (!to) return;
      // Named by the tool it goes to, as on the Tools page; the step itself is the tooltip.
      const b = el("button", "btn sm next", "\u2192 " + to.name);
      b.title = step.label;
      b.onclick = () => open_tool(step.to, b, run.path);
      acts.appendChild(b);
    });
    last.appendChild(acts);
    tr.appendChild(last);
    tbody.appendChild(tr);
    /* The run's results, under its row. Opened by clicking anywhere on the row that is not a
       button, because the row is the run and looking at it is the thing most often wanted. */
    const more = el("tr", "resrowwrap");
    const cell = el("td");
    cell.colSpan = 4;
    const panel = el("div", "respanel");
    cell.appendChild(panel);
    more.appendChild(cell);
    more.hidden = !OPEN_RUNS.has(run.path);
    if (!more.hidden) resultsInto(panel, run);
    tr.onclick = (e) => {
      if (e.target.closest("button, a")) return;
      const opening = more.hidden;
      more.hidden = !opening;
      chev.textContent = opening ? "\u25BE" : "\u25B8";
      if (opening) { OPEN_RUNS.add(run.path); resultsInto(panel, run); }
      else OPEN_RUNS.delete(run.path);
    };
    tbody.appendChild(more);
  });
  table.appendChild(tbody);
  body.appendChild(table);
}

/* ------------------------------------------------------------------ an older launcher */
/* This page is taken from HOPE Labs on the cluster, so it can be served by a launcher built before
   it. That launcher still works; the page says once, at the top, what it lacks and where the
   current one is. PAGE_API_NEWEST is the newest server this page uses (PAGE_API in
   hope_labs/__init__.py, which the state reports as "api"), and PAGE_API_LACKS is what a launcher
   below it is without, in one line. Raising PAGE_API means changing these two lines. */
const PAGE_API_NEWEST = 7;
const PAGE_API_LACKS = "the structure browser, which reaches this computer, your scratch and every tool's results from one dialog";
/* Where the current launcher is: the releases page of the repository. */
const LAUNCHER_DOWNLOAD = "https://github.com/aadhilhaq/HOPE-Labs-Public/releases/latest";
const NOTICE_SEEN = "hlOlderLauncher";
let noticeDismissed = false;

function paintLauncherNotice(link) {
  const box = $("oldlauncher");
  if (!box) return;
  const where = link === undefined ? LAUNCHER_DOWNLOAD : link;
  let dismissed = noticeDismissed;
  try { dismissed = dismissed || sessionStorage.getItem(NOTICE_SEEN) === String(PAGE_API_NEWEST); }
  catch (e) { /* no storage: dismissing lasts as long as the page */ }
  box.hidden = (STATE.api || 0) >= PAGE_API_NEWEST || dismissed;
  if (box.hidden) return;
  const text = $("oldlauncher-text");
  text.textContent = "This launcher is older than HOPE Labs on the cluster. It still works, but "
    + "without " + PAGE_API_LACKS + ". Download the current launcher from ";
  const folder = el(where ? "a" : "span", null, "the releases page");
  if (where) { folder.href = where; folder.target = "_blank"; folder.rel = "noopener"; }
  text.appendChild(folder);
  text.appendChild(document.createTextNode("."));
}

if ($("oldlauncher-hide")) $("oldlauncher-hide").onclick = () => {
  noticeDismissed = true;
  try { sessionStorage.setItem(NOTICE_SEEN, String(PAGE_API_NEWEST)); }
  catch (e) { /* no storage: it stays away until the page is opened again */ }
  $("oldlauncher").hidden = true;
};

/* ------------------------------------------------------------------ the bar along the bottom */
/* The launcher writes the bar, and rolls its credits across the window when they do not fit. The
   stylesheet holds it to one still line instead; this adds Credits beside it, which opens the
   whole statement above the line: the version, the copyright, and each credit on a line of its
   own. Its links open in a tab of their own, because this page holds the session. */
function stillCredits() {
  const bar = document.querySelector(".creditbar");
  const text = bar && bar.querySelector(".credittext");
  if (!text || bar.querySelector(".hlc-more")) return;
  const sep = " \u00b7 ";
  let said = text.innerHTML;
  // Rolled anyway, by a bar that measured itself before this page's styles: the statement twice.
  const half = (said.length - sep.length) / 2;
  if (text.closest(".rolling") && Number.isInteger(half)
      && said.slice(0, half) === said.slice(half + sep.length)) said = said.slice(0, half);
  const parts = said.split(sep).map((p) => p.trim()).filter(Boolean);
  const all = el("div", "hlc-all");
  all.id = "hlc-all";
  all.hidden = true;
  all.innerHTML = "<p><b>" + (parts[0] || "") + "</b></p>" + (parts[1] ? "<p>" + parts[1] + "</p>" : "")
    + (parts.length > 2 ? "<ul>" + parts.slice(2).map((p) => "<li>" + p + "</li>").join("") + "</ul>" : "");
  all.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  /* The visible line is the version and the copyright, which end; the whole statement names
     every tool and every engine and is longer than any window, so it waits behind Credits. Held
     to one line it was cut mid-word - "...All righ..." - which reads as broken
     text rather than as a line that stops on purpose.

     Read back out of an element rather than written straight in: these parts are the markup the
     launcher wrote, so "Texas A&amp;M" in them is an ampersand, and put into a text node as it
     stands it was drawn as the five characters &amp;. */
  const decode = (html) => { const n = document.createElement("span"); n.innerHTML = html; return n.textContent; };
  const short = decode(parts[0] + (parts[1] ? sep + parts[1].split(/(?<=\.)\s/)[0] : ""));
  text.textContent = short;
  text.title = decode(parts.join(sep));
  const more = el("button", "hlc-more", "Credits");
  more.type = "button";
  more.setAttribute("aria-expanded", "false");
  more.setAttribute("aria-controls", "hlc-all");
  more.onclick = () => {
    const opening = all.hidden;
    all.hidden = !opening;
    more.setAttribute("aria-expanded", String(opening));
    more.textContent = opening ? "Hide credits" : "Credits";
  };
  bar.insertBefore(all, bar.firstChild);
  bar.insertBefore(more, bar.querySelector(".creditlinks"));
}
stillCredits();

/* ------------------------------------------------------------------ start */
async function refresh() {
  const got = await api("/api/state");
  STATE = got;
  $("who").textContent = (got.user || "") + " · " + (got.host || "grace");
  if ($("railcluster") && got.address) $("railcluster").textContent = got.address;
  paintTools();
  paintRail();
  paintLauncherNotice();
  if ($("runfiles")) $("runfiles").hidden = (got.api || 0) < 5;
  if ($("newcluster") && $("nc-next").hidden) paintNewCluster();
}

// How to cite, in the bar along the bottom, is a page of the docs. It opens in a tab of its own,
// like Docs beside it, so this page, which holds the session, is not navigated away from.
if ($("citebar")) { $("citebar").target = "_blank"; $("citebar").rel = "noopener"; }

$("q").addEventListener("input", (e) => { QUERY = e.target.value; paintTools(); });
$("runrefresh").onclick = () => loadRuns(true);
/* Looking through the folders on the cluster. It opens the one browser this page has - the
   shared structure browser, with its places down the left - and what is chosen there is shown in
   the folder sheet. Before this, pressing it opened a second browser of its own, with a path box
   and an Up button, on the person's home folder: two browsers that looked nothing alike, for one
   job. A launcher too old for the shared one keeps the sheet it always had. */
if ($("runfiles")) {
  $("runfiles").onclick = () => {
    if (!canPick()) { openFiles(); return; }
    const look = (path) => openFiles({ name: path.split("/").filter(Boolean).pop() || path, path: path });
    hubPicker({
      title: "Look through the folders",
      folders: true,
      useLabel: "Show what is in it",
      onPath: (file) => look(file.replace(/\/[^/]*$/, "")),
      onFolder: (folder) => look(folder),
    });
  };
}

(async function start() {
  if (!TOKEN) { flash("Open this page from the launcher window: it carries the token that gates it.", true); return; }
  try { await refresh(); } catch (failure) { flash(failure.message, true); }
  // The tools are started and stopped from here, but a tool can also die on its own; this keeps
  // the rail honest without getting in the way.
  setInterval(() => { refresh().catch(() => {}); }, 15000);
})();

/* ------------------------------------------------------------------ telling the author */
/* A run that failed is the moment somebody has something to say and the least to say it with: the
   job id, the folder and the log are all on the cluster. So the form gathers them there, shows
   the whole report, and only then offers to file it. Two presses rather than one, on purpose -
   the report quotes the person's own run, and they read it before it is written where the group
   can see it. */
const REPORT_KINDS = {
  failure: ["Report a run that failed", "What were you expecting, and what happened instead?"],
  feedback: ["Send feedback", "What is awkward, missing, or wrong? Anything at all."],
  idea: ["Suggest something", "What would help?"],
};

function openReport(kind, context) {
  const [title, prompt] = REPORT_KINDS[kind] || REPORT_KINDS.feedback;
  const back = el("div", "sheetback");
  const sheet = el("div", "dsheet");
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-label", title);
  sheet.appendChild(el("h2", "sheettitle", title));
  sheet.appendChild(el("p", "sheetlead", prompt));

  const said = el("textarea", "sheettext");
  said.rows = 5;
  said.placeholder = prompt;
  sheet.appendChild(said);

  const who = el("input", "sheetline");
  who.type = "text";
  who.placeholder = "Your email, for an answer (optional)";
  sheet.appendChild(who);

  const note = el("p", "sheetnote", "You see the whole report before anything is sent. ");
  note.appendChild(qButton("the report", "It goes to a folder the lab reads, on the cluster, and "
    + "nothing leaves it. It carries what you type, the state of each card, what Slurm says about "
    + "its jobs, and the last 80 lines of the log."));
  sheet.appendChild(note);

  const shown = el("pre", "sheetpre");
  shown.hidden = true;
  sheet.appendChild(shown);

  const row = el("div", "sheetacts");
  const cancel = el("button", "btn sm", "Cancel");
  const see = el("button", "btn sm go", "See what will be sent");
  const send = el("button", "btn sm go", "Send it");
  const copy = el("button", "btn sm", "Copy");
  send.hidden = true;
  copy.hidden = true;
  [cancel, copy, see, send].forEach((b) => { b.type = "button"; row.appendChild(b); });
  sheet.appendChild(row);
  const says = el("p", "sheetsays", "");
  sheet.appendChild(says);

  const shut = () => { document.removeEventListener("keydown", onKey); back.remove(); };
  const onKey = (e) => { if (e.key === "Escape") shut(); };
  document.addEventListener("keydown", onKey);
  back.onclick = (e) => { if (e.target === back) shut(); };
  cancel.onclick = shut;

  const ask = async (preview) => {
    says.textContent = preview ? "gathering it on the cluster…" : "writing it…";
    [see, send, copy].forEach((b) => { b.disabled = true; });
    try {
      const got = await api("/api/report", {
        kind: kind, said: said.value, contact: who.value,
        context: context || {}, preview: preview,
      });
      shown.textContent = got.text || "";
      shown.hidden = false;
      see.hidden = true;
      send.hidden = false;
      copy.hidden = false;
      says.textContent = got.path
        ? "Sent. It is at " + got.path
        : (got.problem || "This is what will be sent. Nothing has been written yet.");
      if (got.path) { send.hidden = true; said.disabled = true; who.disabled = true; }
    } catch (failure) {
      says.textContent = failure.message;
    }
    [see, send, copy].forEach((b) => { b.disabled = false; });
  };
  see.onclick = () => ask(true);
  send.onclick = () => ask(false);
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(shown.textContent || "");
      says.textContent = "Copied. Paste it wherever you like.";
    } catch (e) {
      says.textContent = "This browser would not let the page copy; select it and copy by hand.";
    }
  };

  back.appendChild(sheet);
  document.body.appendChild(back);
  said.focus();
}

/* ------------------------------------------------------------------ looking inside a run */
/* The floor under the tools' own pages. Whatever a tool shows of its results, this shows what is
   actually in the folder, lets a text file be read without leaving the launcher, and hands
   anything over to the browser to save. It works the same for every tool, including the ones
   whose pages are in other repositories, because it reads the folder rather than the tool. */

const SIZES = [[1 << 30, "GB"], [1 << 20, "MB"], [1 << 10, "kB"]];
function asSize(n) {
  for (const [at, name] of SIZES) if (n >= at) return (n / at).toFixed(n >= 10 * at ? 0 : 1) + " " + name;
  return n + " B";
}

/* What can be read in the sheet rather than downloaded. Everything else is offered as a file:
   a trajectory or a checkpoint shown as text is a wall of bytes nobody wanted. */
const READABLE = /\.(txt|log|out|err|tsv|csv|json|yaml|yml|md|cfg|conf|ini|sbatch|sh|fasta|fa|a3m|pdb|cif|pdbqt|dat|xvg)$/i;

function fileUrl(path, how) {
  return "/api/files/" + how + "?t=" + encodeURIComponent(TOKEN)
    + "&path=" + encodeURIComponent(path);
}

/* A page fetched from the cluster and shown as one. It is handed to the tab as a blob rather
   than linked, because the file is on the cluster and the browser cannot reach it: the launcher
   is the only thing that can, and this is it reading the file and handing over what it read. A
   report written by one of the tools is self-contained, so it needs nothing else. */
async function openAsPage(path, name) {
  const answer = await fetch(fileUrl(path, "get"));
  if (!answer.ok) throw new Error("that page could not be read from the cluster");
  const text = await answer.text();
  const url = URL.createObjectURL(new Blob([text], { type: "text/html" }));
  const win = window.open(url, "_blank", "noopener");
  if (!win) {
    URL.revokeObjectURL(url);
    throw new Error("the browser blocked the tab. Allow pop-ups for this page and try again.");
  }
  // Long enough for the tab to have taken it; holding it for ever leaks the whole page.
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

/* The sheet opens on a run's folder, or with no run on the person's own scratch (Browse files,
   on Results). The path box takes any path, Up goes to the folder above, and the launcher says
   how far either may go: "up" is the folder above when it may be read, and "top" is the one line
   shown where Up stops. The cluster's own permissions still apply below that, and a folder the
   account cannot read is answered with the cluster's own reason. */
function openFiles(run) {
  run = run || { name: "Files", path: "" };
  const back = el("div", "sheetback");
  const sheet = el("div", "dsheet wide");
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-label", run.path ? "What is in " + run.name : "Files on the cluster");
  const title = el("h2", "sheettitle", run.path ? run.name : "Files on the cluster");
  sheet.appendChild(title);

  const crumbs = el("form", "crumbs pathbar");
  crumbs.setAttribute("role", "search");
  const up = el("button", "btn sm", "\u2191 Up");
  up.type = "button";
  up.id = "filesup";
  const box = el("input", "pathbox");
  box.id = "filespath";
  box.type = "text";
  box.spellcheck = false;
  box.autocomplete = "off";
  box.placeholder = "a folder on the cluster, as /scratch/\u2026";
  box.setAttribute("aria-label", "Path on the cluster");
  const go = el("button", "btn sm go", "Go");
  go.type = "submit";
  const again = el("button", "btn sm", "The run");
  again.type = "button";
  again.hidden = true;
  [up, box, go, again].forEach((b) => crumbs.appendChild(b));
  sheet.appendChild(crumbs);
  // The one line worth reading: where Up stops, or why a path was not opened.
  const note = el("p", "pathnote");
  note.id = "filesnote";
  note.hidden = true;
  sheet.appendChild(note);
  const list = el("div", "filelist");
  sheet.appendChild(list);
  const peek = el("pre", "sheetpre");
  peek.hidden = true;
  sheet.appendChild(peek);
  const says = el("p", "sheetsays", "");
  sheet.appendChild(says);

  const row = el("div", "sheetacts");
  const whole = el("button", "btn sm go", "Download the whole run");
  whole.type = "button";
  whole.title = "Zipped on the cluster first, then sent here";
  whole.hidden = !run.path;
  whole.onclick = () => {
    says.textContent = "Zipping it on the cluster: a large run takes a minute to start.";
    window.location.href = fileUrl(run.path, "zip");
  };
  const shut = el("button", "btn sm", "Close");
  shut.type = "button";
  [shut, whole].forEach((b) => row.appendChild(b));
  sheet.appendChild(row);
  const tell = (text, bad) => {
    note.textContent = text || "";
    note.hidden = !text;
    note.classList.toggle("bad", !!bad);
  };

  const close = () => { document.removeEventListener("keydown", onKey); back.remove(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  back.onclick = (e) => { if (e.target === back) close(); };
  shut.onclick = close;

  let here = "";
  const show = async (path) => {
    says.textContent = "reading…";
    let got;
    try { got = await api("/api/files/ls", { path: path }); }
    catch (failure) {
      // What was on the sheet stays there; the box goes back to it, and the reason is the line.
      says.textContent = "";
      box.value = here;
      tell(failure.message, true);
      if (!here) list.innerHTML = "";
      return;
    }
    list.innerHTML = "";
    peek.hidden = true;
    here = got.path;
    says.textContent = got.more ? got.more + " more not shown" : "";
    box.value = got.path;
    up.disabled = !got.up;
    up.onclick = () => { if (got.up) show(got.up); };
    up.title = got.up ? "Open " + got.up : (got.top || "This is the top of the cluster's files");
    tell(got.top || "", false);
    again.hidden = !run.path || got.path === run.path;
    again.onclick = () => show(run.path);

    if (!got.entries.length) list.appendChild(el("p", "empty", "This folder is empty."));
    got.entries.forEach((entry) => {
      const full = got.path.replace(/\/$/, "") + "/" + entry.name;
      const line = el("div", "filerow");
      const name = el("button", "filename" + (entry.dir ? " isdir" : ""),
                      (entry.dir ? "▸ " : "") + entry.name);
      name.type = "button";
      if (entry.dir) {
        name.onclick = () => show(full);
      } else if (/\.html?$/i.test(entry.name)) {
        name.title = "Open it as a page";
        name.onclick = async () => {
          says.textContent = "opening " + entry.name + "…";
          try { await openAsPage(full, entry.name); says.textContent = ""; }
          catch (failure) { says.textContent = failure.message; }
        };
      } else if (READABLE.test(entry.name)) {
        name.title = "Read it here";
        name.onclick = async () => {
          says.textContent = "reading " + entry.name + "…";
          try {
            const answer = await fetch(fileUrl(full, "get"));
            const text = await answer.text();
            // Enough to read a table or a traceback without pulling a 400 MB log into the page.
            peek.textContent = text.length > 200000
              ? text.slice(0, 200000) + "\n\n... the rest is longer than this sheet shows; "
                + "download it to read the whole of it"
              : text;
            peek.hidden = false;
            says.textContent = entry.name + ", " + asSize(entry.size);
          } catch (e) { says.textContent = "that file could not be read"; }
        };
      } else {
        name.disabled = true;
        name.title = "Not a kind this sheet shows; download it to open it";
      }
      line.appendChild(name);
      line.appendChild(el("span", "filesize", entry.dir ? "" : asSize(entry.size)));
      line.appendChild(el("span", "filewhen", when(entry.when)));
      if (!entry.dir) {
        const get = el("a", "btn sm", "Download");
        get.href = fileUrl(full, "get");
        get.setAttribute("download", entry.name);
        line.appendChild(get);
      } else {
        const get = el("a", "btn sm", "Zip");
        get.href = fileUrl(full, "zip");
        get.title = "Zipped on the cluster, then sent here";
        line.appendChild(get);
      }
      list.appendChild(line);
    });
  };

  crumbs.onsubmit = (e) => {
    e.preventDefault();
    const asked = box.value.trim();
    if (!asked) { tell("Type the whole path, starting with /.", true); return; }
    show(asked);
  };

  back.appendChild(sheet);
  document.body.appendChild(back);
  show(run.path);
}

/* ------------------------------------------------------------------ seeing a run's results */
/* What a run made, shown rather than listed: structures turn in a viewer, pictures are pictures,
   a video plays, a report opens as a page and a table reads as a table. It reads the run's folder
   rather than asking the tool, so it is the same for every tool, and it goes two folders down,
   because that is where the tools put what they make. */

/* In the order a person looks: a report a tool wrote up says the most, a video shows the most,
   pictures are quickest to compare, and structures and tables are what they go to next. */
const VISUAL = [
  ["reports", /\.html?$/i, "Reports"],
  ["videos", /\.(mp4|webm|m4v)$/i, "Videos"],
  ["pictures", /\.(png|jpe?g|gif|svg|webp)$/i, "Pictures"],
  ["structures", /\.(pdb|cif|mmcif|pdbqt)$/i, "Structures"],
  ["tables", /\.(tsv|csv)$/i, "Tables"],
];

/* Folders a run keeps its working files in, not its results: walked into, they are thousands of
   intermediates and nothing anybody came to look at. */
const NOT_RESULTS = /^(\.|__pycache__$|tmp$|cache$|scratch$|logs?$|work$|msas?$|frames?$)/i;

/* Two folders down is where the tools put what they make, and further than that only through
   folders that hold nothing to look at, as far as DEEPEST: Boltz-2 writes its model four down, in
   out/boltz_results_<name>/predictions/<name>/, and a finished fold's gallery said there was
   nothing in it. */
const DEEPEST = 4;

async function scanResults(root) {
  const found = { structures: [], pictures: [], videos: [], reports: [], tables: [] };
  const queue = [[root, 0]];
  let looked = 0;
  while (queue.length && looked < 16) {
    const [path, depth] = queue.shift();
    looked += 1;
    let got;
    try { got = await api("/api/files/ls", { path: path }); } catch (e) {
      // The run's own folder unreadable is the answer, and not an empty gallery: "nothing to
      // look at" over a folder the person may not open sent them looking for a run that failed.
      if (path === root) throw e;
      continue;
    }
    const inside = [];
    let shown = 0;
    got.entries.forEach((entry) => {
      const full = got.path.replace(/\/$/, "") + "/" + entry.name;
      if (entry.dir) {
        if (!NOT_RESULTS.test(entry.name)) inside.push(full);
        return;
      }
      const kind = VISUAL.find(([, pattern]) => pattern.test(entry.name));
      if (kind) {
        found[kind[0]].push({ name: entry.name, path: full, size: entry.size,
                              where: full.slice(root.length + 1) });
        shown += 1;
      }
    });
    if (depth < 2 || (depth < DEEPEST && !shown)) inside.forEach((dir) => queue.push([dir, depth + 1]));
  }
  // The largest structure first is rarely the one wanted; the shortest path is - a run's own
  // top-level model sits above its per-replica copies. Pictures likewise.
  Object.values(found).forEach((list) => list.sort((a, b) =>
    a.where.split("/").length - b.where.split("/").length || a.name.localeCompare(b.name)));
  return found;
}

/* Where HOPE Labs is on the cluster signed in to, as the launcher says (a launcher before
   PAGE_API 3 does not), or the folder its page has always been kept in. */
const LABS_KEPT = "";
function labsFolder() {
  return (((STATE.labs || {}).path) || LABS_KEPT).replace(/\/$/, "");
}

/* The viewer, once per page. It is the lab's own copy, the one the folding pages serve, read
   over the connection like any other file: the launcher's page is on this computer and the
   viewer is on the cluster, and fetching it this way needs no new launcher and no outside site. */
let VIEWER = null;
function loadViewer() {
  if (window.$3Dmol) return Promise.resolve();
  if (VIEWER) return VIEWER;
  // The copies to try, in order: HOPE Labs' own on the cluster, then HOPE-MD's. A tool's own
  // `install` is not one of them: for the folding tools it is their environment, which holds no
  // viewer at all.
  const md = (byKey("hopemd") || {}).install || "";
  const labs = labsFolder();
  const where = (labs ? [labs + "/hope_labs/folding/web/vendor/3Dmol-min.js"] : [])
    .concat(md ? [md.replace(/\/$/, "") + "/hopemd/web/vendor/3Dmol-min.js"] : []);
  VIEWER = (async () => {
    let text = "";
    for (const path of where) {
      try {
        const answer = await fetch(fileUrl(path, "get"));
        if (answer.ok) { text = await answer.text(); break; }
      } catch (e) { /* the next copy */ }
    }
    if (!text) throw new Error("the structure viewer could not be read from the cluster");
    const url = URL.createObjectURL(new Blob([text], { type: "text/javascript" }));
    await new Promise((done, fail) => {
      const tag = document.createElement("script");
      tag.src = url;
      tag.onload = done;
      tag.onerror = () => fail(new Error("the structure viewer would not start"));
      document.head.appendChild(tag);
    });
  })();
  VIEWER.catch(() => { VIEWER = null; });
  return VIEWER;
}

/* A sheet over the page, for anything shown large. Closed by the button, Escape, or a click
   outside it. */
function bigSheet(title, wide) {
  const back = el("div", "sheetback");
  const sheet = el("div", "dsheet " + (wide ? "wide" : ""));
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-label", title);
  const head = el("div", "bighead");
  head.appendChild(el("h2", "sheettitle", title));
  const shut = el("button", "btn sm", "Close");
  shut.type = "button";
  head.appendChild(shut);
  sheet.appendChild(head);
  const body = el("div", "bigbody");
  sheet.appendChild(body);
  const close = () => { document.removeEventListener("keydown", onKey); back.remove(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  back.onclick = (e) => { if (e.target === back) close(); };
  shut.onclick = close;
  back.appendChild(sheet);
  document.body.appendChild(back);
  return { body: body, close: close, sheet: sheet };
}

/* How a structure is drawn first, from the atoms 3Dmol read rather than from the text, whose
   columns a CIF lays out differently: protein as a cartoon, colored by chain where there are
   several; and anything small - a docked peptide on its own, a pose from the design run - as
   sticks, because a cartoon of two residues is nothing at all, and the viewer looked empty. */
const SMALL = 30;
function firstLook(atoms) {
  const polymer = atoms.filter((a) => !a.hetflag);
  const chains = new Set(polymer.map((a) => a.chain));
  const residues = new Set(polymer.map((a) => a.chain + ":" + a.resi));
  return residues.size < SMALL ? "sticks" : chains.size > 1 ? "chains" : "cartoon";
}

/* A docked pose as 3Dmol can read it. Vina writes a pose's torsion tree into the file - ROOT,
   ENDROOT, BRANCH, ENDBRANCH - and 3Dmol takes any line starting END as the end of the molecule,
   so every pose from the design run was drawn as the three atoms before its first ENDROOT. The tree
   is taken out, and of several poses the first is kept, which is the best one. */
function asPose(text) {
  const out = [];
  for (const line of text.split(/\r?\n/)) {
    if (/^ENDMDL/.test(line)) break;
    if (/^(ROOT|ENDROOT|BRANCH|ENDBRANCH|TORSDOF|MODEL)\b/.test(line)) continue;
    out.push(line);
  }
  return out.join("\n");
}

/* 3Dmol's own color for a chain, as its "chain" scheme paints one, so the surface MSMS computed
   reads as the viewer's own did. A residue key is "A:96": its chain is the part before the colon,
   and "_" is a blank chain, which the scheme has no color for, so the surface's own stands in. */
function chainColour(key) {
  const chain = String(key).split(":")[0];
  const scheme = ((window.$3Dmol || {}).builtinColorSchemes || {}).chain || {};
  const color = chain && chain !== "_" && scheme.map ? scheme.map[chain] : undefined;
  return color === undefined ? null : color;
}

/* The launcher's server that answers for a surface: PAGE_API 4, which its state says. A launcher
   built before it has no such route, so the page does not ask it and draws the viewer's own. */
const SURFACE_API = 4;

async function viewStructure(item) {
  const box = bigSheet(item.where || item.name, true);
  const stage = el("div", "molstage");
  box.body.appendChild(stage);
  const bar = el("div", "molbar");
  box.body.appendChild(bar);
  const says = el("p", "sheetsays", "Reading the structure and the viewer…");
  box.body.appendChild(says);
  try {
    const [text] = await Promise.all([
      fetch(fileUrl(item.path, "get")).then((r) => {
        if (!r.ok) throw new Error("that structure could not be read");
        return r.text();
      }),
      loadViewer(),
    ]);
    const format = /\.(cif|mmcif)$/i.test(item.name) ? "cif"
      : /\.pdbqt$/i.test(item.name) ? "pdbqt" : "pdb";
    const viewer = window.$3Dmol.createViewer(stage, { backgroundColor: "white" });
    viewer.addModel(format === "pdbqt" ? asPose(text) : text, format);
    const idle = item.size ? asSize(item.size) + ". Drag to turn it, scroll to zoom." : "";
    /* The surface is MSMS's, computed on the cluster where the file is and drawn by msms.js as
       custom shapes. They are kept so that another style takes out exactly those:
       removeAllSurfaces takes out the viewer's own surface and leaves custom shapes alone. */
    let shapes = [], showing = "", waiting = 0;
    const ownSurface = (why) => {
      viewer.addSurface(window.$3Dmol.SurfaceType.VDW, { opacity: 0.7, colorscheme: "chain" });
      says.textContent = "Surface drawn by the viewer: " + String(why).replace(/\.+$/, "") + ".";
    };
    const again = () => { if (showing === "surface" && stage.isConnected) paint("surface"); };
    const surface = () => {
      viewer.setStyle({}, { cartoon: { colorscheme: "chain" } });
      if ((STATE.api || 0) < SURFACE_API || typeof msmsSurface !== "function") {
        ownSurface("this launcher was built before MSMS surfaces; the current release has them");
        return;
      }
      // Asked for once per file in a session. The answer comes back to the sheet that asked,
      // and is drawn then if the surface is still what it shows; a sheet opened on the same file
      // while that question is out is not called back, so it looks again for itself.
      const d = msmsSurface(item.path, fileUrl(item.path, "surface"), again);
      if (d === null) {
        says.textContent = "Computing the surface with MSMS…";
        waiting = setTimeout(again, 1500);
        return;
      }
      if (d.source !== "msms") { ownSurface(d.why || "MSMS gave none"); return; }
      try { shapes = msmsAdd(viewer, d, { opacity: 0.7 }, chainColour); }
      catch (e) { ownSurface("the surface MSMS gave could not be drawn (" + e.message + ")"); }
    };
    const styles = {
      cartoon: () => viewer.setStyle({}, { cartoon: { color: "spectrum" } }),
      chains: () => viewer.setStyle({}, { cartoon: { colorscheme: "chain" } }),
      sticks: () => viewer.setStyle({}, { stick: {} }),
      surface: surface,
    };
    const paint = (how) => {
      clearTimeout(waiting);
      showing = how;
      viewer.removeAllSurfaces();
      shapes.forEach((shape) => { try { viewer.removeShape(shape); } catch (e) { /* gone already */ } });
      shapes = [];
      says.textContent = idle;
      styles[how]();
      // Anything that is not a protein or a nucleic acid - a ligand, a peptide built as a
      // hetero group - stays as sticks whatever the rest is drawn as, or it vanishes.
      viewer.addStyle({ hetflag: true }, { stick: { colorscheme: "greenCarbon" } });
      viewer.render();
    };
    [["cartoon", "Rainbow"], ["chains", "By chain"], ["sticks", "Sticks"],
     ["surface", "Surface"]].forEach(([how, label]) => {
      const b = el("button", "btn sm", label);
      b.type = "button";
      b.onclick = () => paint(how);
      bar.appendChild(b);
    });
    const get = el("a", "btn sm", "Download");
    get.href = fileUrl(item.path, "get");
    get.setAttribute("download", item.name);
    bar.appendChild(get);
    paint(firstLook(viewer.getModel().selectedAtoms({})));
    viewer.zoomTo();
    viewer.render();
  } catch (failure) {
    says.textContent = failure.message;
  }
}

async function blobOf(item, type) {
  const answer = await fetch(fileUrl(item.path, "get"));
  if (!answer.ok) throw new Error(item.name + " could not be read");
  const data = await answer.blob();
  return URL.createObjectURL(type ? new Blob([data], { type: type }) : data);
}

async function viewPicture(item) {
  const box = bigSheet(item.where || item.name, true);
  const says = el("p", "sheetsays", "Reading it…");
  box.body.appendChild(says);
  try {
    const type = /\.svg$/i.test(item.name) ? "image/svg+xml" : "";
    const url = await blobOf(item, type);
    const img = el("img", "bigpic");
    img.src = url;
    img.alt = item.name;
    box.body.insertBefore(img, says);
    says.textContent = "";
  } catch (failure) { says.textContent = failure.message; }
}

async function viewVideo(item) {
  const box = bigSheet(item.where || item.name, true);
  const says = el("p", "sheetsays", item.size > 200 * 1048576
    ? "This one is " + asSize(item.size) + ": it plays once it has all arrived." : "Reading it…");
  box.body.appendChild(says);
  try {
    const url = await blobOf(item, "video/mp4");
    const video = el("video", "bigvideo");
    video.src = url;
    video.controls = true;
    video.autoplay = true;
    box.body.insertBefore(video, says);
    says.textContent = "";
  } catch (failure) { says.textContent = failure.message; }
}

/* The tables in a file, which is not always one header and its rows. MMPBSA's frames file, which
   HOPE-MD leaves in every run, is four tables one after another, each under a line naming it, and
   read as one it was a single column headed "GENERALIZED BORN:". A line of one cell in a file of
   several columns names the table after it, whose first line is its header; a file with no such
   lines is one table, as before, blank lines and all. */
function tablesOf(text, sep) {
  const out = [];
  let cur = null, title = "";
  text.split(/\r?\n/).forEach((line) => {
    if (!line.trim()) return;
    const cells = line.split(sep);
    if (cells.length < 2 && (!cur || cur.head.length > 1)) { title = line.trim(); cur = null; return; }
    if (!cur) { cur = { title: title, head: cells, rows: [] }; out.push(cur); title = ""; return; }
    cur.rows.push(cells);
  });
  const filled = out.filter((t) => t.rows.length);
  if (filled.length) return filled;
  // nothing shaped like a table: the whole file, its first line the header, as it always was
  const rows = text.split(/\r?\n/).filter((l) => l.trim()).map((l) => l.split(sep));
  return rows.length > 1 ? [{ title: "", head: rows[0], rows: rows.slice(1) }] : [];
}

async function viewTable(item) {
  const box = bigSheet(item.where || item.name, true);
  const says = el("p", "sheetsays", "Reading it…");
  box.body.appendChild(says);
  try {
    const text = await (await fetch(fileUrl(item.path, "get"))).text();
    const sep = /\.tsv$/i.test(item.name) ? "\t" : ",";
    const tables = tablesOf(text, sep);
    if (!tables.length) { says.textContent = "This table is empty."; return; }
    const wrap = el("div", "tablewrap");
    const table = el("table", "runs datatable");
    wrap.appendChild(table);
    const show = (t) => {
      const head = t.head;
      const body = t.rows.slice(0, 2000);
      let order = { col: -1, up: true };
      const draw = () => {
        table.innerHTML = "";
        const tr = el("tr");
        head.forEach((name, i) => {
          const th = el("th", "sortable", name + (order.col === i ? (order.up ? " ▲" : " ▼") : ""));
          th.title = "Sort by " + name;
          th.onclick = () => {
            order = { col: i, up: order.col === i ? !order.up : true };
            const num = body.every((r) => r[i] === undefined || r[i] === "" || !isNaN(Number(r[i])));
            body.sort((a, b) => {
              const x = num ? Number(a[i]) : String(a[i] || ""), y = num ? Number(b[i]) : String(b[i] || "");
              return (x < y ? -1 : x > y ? 1 : 0) * (order.up ? 1 : -1);
            });
            draw();
          };
          tr.appendChild(th);
        });
        table.appendChild(el("thead")).appendChild(tr);
        const tb = el("tbody");
        body.forEach((r) => {
          const row = el("tr");
          head.forEach((_, i) => row.appendChild(el("td", null, r[i] == null ? "" : r[i])));
          tb.appendChild(row);
        });
        table.appendChild(tb);
      };
      draw();
      wrap.scrollTop = 0;
      says.textContent = (t.title ? t.title + ": " : "") + t.rows.length + " rows"
        + (t.rows.length > 2000 ? ", the first 2,000 shown" : "") + ". Click a heading to sort by it.";
    };
    // One button for each table in the file, where there is more than one.
    if (tables.length > 1) {
      const pick = el("div", "molbar");
      tables.forEach((t, i) => {
        const b = el("button", "btn sm" + (i ? "" : " go"), t.title || "Table " + (i + 1));
        b.type = "button";
        b.onclick = () => {
          pick.querySelectorAll(".btn").forEach((x) => x.classList.toggle("go", x === b));
          show(t);
        };
        pick.appendChild(b);
      });
      box.body.insertBefore(pick, says);
    }
    box.body.insertBefore(wrap, says);
    show(tables[0]);
  } catch (failure) { says.textContent = failure.message; }
}

/* The gallery: what a run made, by kind, each with the way to look at it. Pictures are drawn
   small straight away, because a picture is the fastest way to tell two runs apart; everything
   else waits to be asked for. */
async function resultsInto(box, run) {
  box.innerHTML = "";
  const acts = el("div", "acts resacts");
  const tool = byKey(run.tool);
  if (tool) {
    const open = el("button", "btn sm go", "Open in " + tool.name);
    open.title = "Open " + tool.name + " on this run, on its own results page";
    open.onclick = () => { openWindow(run.tool, "", run.path); };
    acts.appendChild(open);
  }
  const files = el("button", "btn sm", "All files");
  files.onclick = () => openFiles(run);
  acts.appendChild(files);
  const whole = el("a", "btn sm", "Download the run");
  whole.href = fileUrl(run.path, "zip");
  whole.title = "Zipped on the cluster first, then sent here";
  acts.appendChild(whole);
  box.appendChild(acts);

  const says = el("p", "why", "Looking for what this run made…");
  box.appendChild(says);
  let found;
  try { found = await scanResults(run.path); }
  catch (failure) { says.textContent = failure.message; return; }
  const any = Object.values(found).some((l) => l.length);
  says.textContent = any ? "" : "Nothing to look at yet. All files lists what is there.";

  VISUAL.forEach(([kind, , title]) => {
    const list = found[kind];
    if (!list.length) return;
    const part = el("div", "respart");
    const heading = el("div", "reshead", title);
    heading.appendChild(el("span", "n", " " + list.length));
    part.appendChild(heading);
    if (kind === "pictures") {
      const strip = el("div", "thumbs");
      list.slice(0, 12).forEach((item) => {
        const tile = el("button", "thumb");
        tile.type = "button";
        tile.title = item.where;
        tile.onclick = () => viewPicture(item);
        const img = el("img");
        img.alt = item.name;
        // A picture still being written, or one the browser cannot draw, says so, rather than
        // showing the browser's broken-image mark with half its name beside it.
        img.onerror = () => { img.replaceWith(el("span", "thumbnone", "not ready to show yet")); };
        blobOf(item, /\.svg$/i.test(item.name) ? "image/svg+xml" : "")
          .then((url) => { img.src = url; }).catch(() => { tile.textContent = item.name; });
        tile.appendChild(img);
        tile.appendChild(el("span", "thumbname", item.name));
        strip.appendChild(tile);
      });
      part.appendChild(strip);
    } else {
      const rows = el("div", "reslist");
      list.slice(0, 20).forEach((item) => {
        const line = el("div", "resrow");
        const label = el("span", "resname");
        const cut = item.where.lastIndexOf("/");
        if (cut > 0) label.appendChild(el("span", "resdir", item.where.slice(0, cut + 1)));
        label.appendChild(document.createTextNode(item.where.slice(cut + 1)));
        label.title = item.where;
        line.appendChild(label);
        line.appendChild(el("span", "filesize", asSize(item.size)));
        const act = { structures: ["View in 3D", viewStructure], videos: ["Play", viewVideo],
                      reports: ["Open", (i) => openAsPage(i.path, i.name).catch((e) => flash(e.message, true))],
                      tables: ["View", viewTable] }[kind];
        const b = el("button", "btn sm", act[0]);
        b.type = "button";
        b.onclick = () => act[1](item);
        line.appendChild(b);
        const get = el("a", "btn sm", "Download");
        get.href = fileUrl(item.path, "get");
        get.setAttribute("download", item.name);
        line.appendChild(get);
        rows.appendChild(line);
      });
      if (list.length > 20) rows.appendChild(el("p", "why", (list.length - 20) + " more in All files"));
      part.appendChild(rows);
    }
    box.appendChild(part);
  });
}

/* The same gallery over the page, for a flow card: a flow's results are in as many folders as
   it has cards, and each card's are one press away. */
function openResults(run) {
  const box = bigSheet(run.name, true);
  resultsInto(box.body, run);
}

/* ============================== the structure browser ==============================
   The dialog every page of ours opens to choose a structure, so somebody who has used one has
   learned this one: the places on the left, the folder on the right, one click to choose.

   It is one module, hope_labs/pages/web/picker.js, and this is that file as it stands, between
   the two markers below. The launcher serves six files and no others (hub.PAGE_FILES), so a file
   of its own would reach nobody until they downloaded a new launcher; tests/test_page_source.py
   holds this copy to the original, character for character, the way index.html holds the copy of
   the text-size control. Everything it needs of a server is handed in (hubPicker below): the
   launcher is not on the cluster, so its places and its folders come down the connection.
   ============================================================================== */
/*__PICKER_JS__*/
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
/*__PICKER_JS_END__*/

/* The browser, as the launcher opens it: the places and the folders come from the cluster over
   the connection this launcher holds, and a file chosen on this computer is written into the
   person's own scratch first, so everything after it works on a path on the cluster.

   `onPath` is the one thing a caller must give: what to do with the structure that was chosen.
   A caller that also takes a PDB code gives `onCode`. */
function hubPicker(options) {
  const opts = options || {};
  const took = (where) => Promise.resolve(opts.onPath(where));
  // Choosing a structure lists structures; looking through the folders lists everything, and
  // offers the folder itself. One dialog, two jobs, so the hub has one browser and not two.
  const folders = !!opts.folders;
  return window.HLPicker.open({
    title: opts.title || "Choose a structure",
    api: (path, body) => api(path, body),
    places: "/api/places",
    browse: folders ? "/api/browse?want=any" : "/api/browse?want=structure",
    accept: ".pdb,.ent,.cif,.mmcif,.pdbqt",
    acceptSays: "A .pdb, .cif or .pdbqt file.",
    kinds: folders ? [] : [".pdb", ".ent", ".cif", ".mmcif", ".pdbqt"],
    start: opts.start || "",
    folders: folders,
    useLabel: opts.useLabel || "",
    onFolder: opts.onFolder ? (where) => Promise.resolve(opts.onFolder(where)) : null,
    onPath: took,
    onRun: (folder, run) => took(run.structure),
    onCode: opts.onCode ? (code) => Promise.resolve(opts.onCode(code)) : null,
    // The file is read here and written into the person's scratch on the cluster: the launcher
    // is the only thing that can reach both ends, and everything downstream wants a path.
    onFile: (file) => new Promise((ok, no) => {
      const reader = new FileReader();
      reader.onerror = () => no(new Error("that file could not be read from this computer"));
      reader.onload = () => {
        const bytes = new Uint8Array(reader.result);
        let binary = "";
        for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
        api("/api/files/put", { name: file.name, data: btoa(binary) })
          .then((got) => ok(took(got.path)), no);
      };
      reader.readAsArrayBuffer(file);
    }),
  });
}

/* Whether this launcher can open the browser at all. One built before the routes it needs keeps
   the box it always had, with the same path typed into it. */
function canPick() {
  return (STATE.api || 0) >= 7 && !!window.HLPicker;
}
window.hubPicker = hubPicker;
window.canPick = canPick;
