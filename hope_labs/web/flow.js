/* HOPE Labs: the flow canvas. Cards are dropped from the palette, dragged into place and linked
   socket to socket; the target is filled in on the right; what is wrong with the flow is listed
   under it, and Launch is held shut until nothing is.

   The canvas decides nothing. Which links are legal, and what is wrong with a flow, come from the
   launcher, which reads the card catalog the runner reads on the cluster. The refusals arrive as
   a table of every pair of sockets, worked out for the target as it is filled in at that moment,
   so a drag can color the sockets without a round trip for each one, and a refusal reads here in
   the words the catalog gives it rather than in a second set of our own.

   The view is one of three stages at a time. The canvas is where a flow is drawn; the plan is what
   the cluster says the flow would do, read before anything is queued; the run view is the same
   cards standing where they were drawn, colored by what has become of each. They are stages
   rather than tabs because they follow one another: a plan is read on the way to queueing a flow,
   and once the flow is queued there is nothing to go back to a plan for.

   It shares api(), flash(), el(), $(), when(), byKey() and open_tool() with app.js, which is
   loaded before it. */
"use strict";

// A stored value whose spelling is not ours to change, shown the way everything else is written.
// The value goes on to the tool that defined it; only the word in the menu changes.
const SHOWN_AS = { "centre": "center" };

const CANVAS_MIN = [1180, 660];     // the drawing is this big at least, and the box scrolls

/* The three states a card does not come back from without somebody asking it to. The runner's own
   words are used for all seven, here and on the page: somebody reading squeue beside this should
   find one vocabulary rather than two. */
const ENDED = ["done", "failed", "stopped"];

/* How often a flow that is still going is read again. A flow is hours to days long, so this costs
   the login node next to nothing, and it is quick enough that a card going from queued to running
   is seen rather than waited for. */
const RUN_EVERY = 20000;

let CAT = null;                     // the catalog, read once
let FLOW = blankFlow();             // exactly the document flow.py reads and writes
/* Set while the canvas is showing a flow that has already been queued, so that Launch becomes
   Save and carry on and the cards that have run are shown rather than offered. Null the rest of
   the time, which is every ordinary use of the canvas. */
let EDITING = null;
let REFUSALS = {};                  // "card:port>card:port" -> why not, or ""
let PROBLEMS = [];                  // check()'s sentences, as they came
/* The same problems with where each one is: {text, node, key}. A person is taken to one at a time
   rather than handed the list and left to find each box from its sentence, which is what the
   list alone asked of them. FIXING is the one they are on, or null before they have started. */
let ISSUES = [];
let FIXING = null;
/* What the cluster refused at Save and carry on, placed on its card and its box. The canvas checks
   a flow with the launcher's own copy of the rules, which can know less than the cluster's: a
   launcher of 0.6.0 or earlier that takes its cards from the cluster keeps their settings without
   what each number may be, and any launcher built before a rule has not got it. So the runner
   could refuse a flow the canvas had just called fixed, and its reason was a message that faded,
   with nothing on the list and no Fix to go to the box. It stays listed until something is
   changed and the flow is checked again; Save and carry on then asks the cluster afresh. */
let REFUSED = [];
let WARNINGS = [];                  // things worth knowing that do not stop the flow running
let CHECKED = false;                // whether the launcher has answered about this flow yet
let PICKED = "";                    // the node whose settings the panel shows
let WIRE = null;                    // the link being drawn, while one is
let SAVED = [];                     // the flows kept on this computer
let QUEUED = [];                    // the flows queued from this computer, newest first
let STAGE = "draw";                 // draw | plan | run
let PLAN = null;                    // what the cluster says this flow would do, while it is shown
let WATCHING = null;                // {folder, flow, status, read, why} of the flow being watched
let checkSeq = 0;
let checkTimer = 0;
let planSeq = 0;
let runTimer = 0;

function blankFlow() { return { version: 1, name: "", runs_root: "", nodes: [], edges: [] }; }

const cardOf = (key) => (CAT ? CAT.cards.find((c) => c.key === key) : null);
const nodeOf = (id) => FLOW.nodes.find((n) => n.id === id);
const portOf = (card, side, key) => (card ? (card[side] || []).find((p) => p.key === key) : null);

/* The cards that are not tools, so they have no glyph in app.js: the target and the structure
   file, which are a person's own input rather than something the lab installed, and the two
   preparations, which belong to the links they stand on. A mark of its own for each, because two
   cards drawn alike on a canvas is the one mistake a glyph exists to prevent - and these two fall
   back to the target's mark when they have none, so a card without one here is not undecorated,
   it is wearing another card's face. */
const CARD_GLYPH = {
  target: '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/>'
        + '<path d="M12 2v5 M12 17v5 M2 12h5 M17 12h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  /* A file with a structure already in it: the page, its folded corner, three bonded atoms. Not
     the target's crosshairs: this card carries a structure and aims at nothing. */
  structurefile: '<path d="M6 2h7l5 5v15H6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>'
               + '<path d="M13 2v5h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>'
               + '<path d="M9.6 16.6l2.4-3 2.5 1.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
               + '<circle cx="9.6" cy="16.6" r="1.25" fill="currentColor"/>'
               + '<circle cx="12" cy="13.6" r="1.25" fill="currentColor"/>'
               + '<circle cx="14.5" cy="15.1" r="1.25" fill="currentColor"/>',
  /* Two molecules, split apart and each given its hydrogens. */
  prepare: '<path d="M12 2v20" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2 2"/>'
         + '<circle cx="6.5" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/>'
         + '<circle cx="17.5" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/>'
         + '<path d="M6.5 5.5v2 M6.5 16.5v2 M17.5 5.5v2 M17.5 16.5v2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  /* The receptor's pocket, the site a binder docks into, with its hydrogens given. */
  prepare_receptor: '<path d="M4 5v6.5a8 8 0 0 0 16 0V5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
                  + '<path d="M9 5v5.5a3 3 0 0 0 6 0V5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2 2"/>'
                  + '<path d="M4 2v1 M20 2v1 M12 21.5v1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
};

function cardGlyph(card, size) {
  if (card.tool) return glyph(card.tool, size);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size || 18);
  svg.setAttribute("height", size || 18);
  svg.innerHTML = CARD_GLYPH[card.key] || CARD_GLYPH.target;
  return svg;
}

/* ------------------------------------------------------------------ the palette */
function paintPalette() {
  const box = $("palette");
  box.innerHTML = "";
  /* A card the canvas puts in by itself is not one to drag in: the preparation belongs to the
     link it stands on, and a spare one dropped on the canvas would be a card joined to nothing. */
  /* Grouped by the kind of tool, in the order the catalog lists them, which is the order work is
     done in. The one card with no tool, the target, heads
     the list on its own. Each card is its name; what it does is its tooltip. */
  const groups = [];
  CAT.cards.filter((card) => !card.fixed).forEach((card) => {
    const tool = card.tool ? byKey(card.tool) : null;
    const name = card.tool ? (tool ? tool.category : "Other") : "";
    let group = groups.find((g) => g.name === name);
    if (!group) { group = { name: name, cards: [] }; groups.push(group); }
    group.cards.push(card);
  });
  groups.forEach((group) => {
    if (group.name) box.appendChild(el("div", "palhead", group.name));
    group.cards.forEach((card) => {
      const item = el("button", "pcard" + (card.tool ? "" : " start"));
      const badge = el("span", "pbadge");
      badge.appendChild(cardGlyph(card));
      item.appendChild(badge);
      const names = el("span", "pnames");
      names.appendChild(el("span", "pname", card.name));
      item.appendChild(names);
      const what = String(card.tagline || "");
      item.title = (what ? what.charAt(0).toUpperCase() + what.slice(1) + ". " : "")
        + "Click to add it, or drag it onto the canvas.";
      item.draggable = true;
      item.ondragstart = (e) => e.dataTransfer.setData("text/plain", card.key);
      item.onclick = () => addCard(card.key);
      box.appendChild(item);
    });
  });
}

/* ------------------------------------------------------------------ the flow */
function freeId(key) {
  if (!nodeOf(key)) return key;
  for (let n = 2; ; n++) {
    if (!nodeOf(key + "-" + n)) return key + "-" + n;
  }
}

function filledIn(card) {
  const out = {};
  (card.settings || []).forEach((s) => { out[s.key] = s.default === null ? "" : s.default; });
  return out;
}

function addCard(key, at) {
  const card = cardOf(key);
  if (!card) return "";
  const n = FLOW.nodes.length;
  const id = freeId(key);
  FLOW.nodes.push({ id: id, card: key, settings: filledIn(card),
                    at: at || [24 + (n % 4) * 252, 24 + Math.floor(n / 4) * 176] });
  PICKED = id;
  paint();
  check();
  return id;
}

function dropCard(id) {
  // A card that has run stays where it is: its results are on disk, and the runner would refuse
  // the flow at Save and carry on for taking it out. Nothing on the page offers this for one; the
  // guard is for anything that ever does.
  const going = nodeOf(id);
  if (alreadyRan(going)) {
    flash((cardOf(going.card) || {}).name + " has already run, so it stays in the flow: its "
          + "results are on disk.", true);
    return;
  }
  if ((cardOf((going || {}).card) || {}).fixed) {
    flash((cardOf(going.card) || {}).name + " goes with its link: take the link off instead.", true);
    return;
  }
  FLOW.nodes = FLOW.nodes.filter((n) => n.id !== id);
  FLOW.edges = FLOW.edges.filter((e) => e.from !== id && e.to !== id);
  if (PICKED === id) PICKED = (FLOW.nodes[0] || {}).id || "";
  paint();
  check();
}

/* Where a card sits. A flow this page drew says so; one written by hand, or queued from a file on
   the cluster, need not, and a card with nowhere to be would stop the drawing dead. Those are laid
   out as the palette lays out a card it has just been given. */
function spot(at, i) {
  if (!at || at.length < 2) return [24 + (i % 4) * 252, 24 + Math.floor(i / 4) * 176];
  return [Math.max(0, Math.round(at[0] || 0)), Math.max(0, Math.round(at[1] || 0))];
}

/* A flow document with nothing missing from it that the drawing needs. Both the canvas and the run
   view take flows out of the settings file, so both come through here rather than one of them
   trusting what it was given. */
function asDrawn(doc) {
  return {
    version: 1, name: (doc || {}).name || "", runs_root: (doc || {}).runs_root || "",
    nodes: ((doc || {}).nodes || []).map((n, i) => ({ id: n.id, card: n.card,
                                                      settings: Object.assign({}, n.settings),
                                                      at: spot(n.at, i) })),
    edges: ((doc || {}).edges || []).map((e) => ({ from: e.from, fromPort: e.fromPort,
                                                   to: e.to, toPort: e.toPort })),
  };
}

function openFlow(doc) {
  FIT_PENDING = true;
  FLOW = asDrawn(doc);
  // Every card, not only the one that gets picked: what is checked and what is saved should be
  // the flow as the canvas means it, and that includes a 0 that meant the tool's own. Except a
  // card that has already run, which is left exactly as it ran: respelling its settings, even to
  // say the same thing, made the runner refuse Save and carry on for a change nobody had made.
  FLOW.nodes.forEach((n) => { if (!alreadyRan(n)) fillDefaults(n, cardOf(n.card)); });
  $("flowname").value = FLOW.name;
  /* The target first: it is the card a person reopening a flow is coming back to. Except where
     the settings are a drawer over the drawing rather than a column beside it - on a laptop, and
     at the larger text sizes - because then opening a flow would open it behind a panel and the
     first thing anybody saw of their own drawing would be a sliver of it. */
  PICKED = panelIsDrawer() ? ""
    : ((FLOW.nodes.find((n) => n.card === "target") || FLOW.nodes[0] || {}).id) || "";
  paint();
  check();
}

/* Whether the settings slide over the drawing rather than standing in a column beside it. Read
   off the panel itself: the stylesheet decides it, from the room the column has at the text size
   in use, and one answer to that question is better than two. */
function panelIsDrawer() {
  const box = $("panel");
  return !!box && getComputedStyle(box).position === "absolute";
}

/* The dimmed layer under the settings, kept in step with whether they are a drawer at all. The
   panel itself is not redrawn: somebody may be typing in it. */
function syncPanelLayer() {
  const back = $("paneback");
  if (!back) return;
  back.hidden = !(PICKED && nodeOf(PICKED) && panelIsDrawer());
  /* How tall the bar under the drawing is, written where the sheet can read it. At this width
     the settings are a drawer over the drawing, and a drawer that ran to the foot of the column
     slid under that bar: a box drawn in its last hundred pixels could not be read or filled in,
     and the one button that launches the flow was behind it. The drawer and the dimming stop
     above the bar instead. Measured rather than guessed: the bar holds a line about what to do
     next, or a list of what is wrong, and is as tall as that needs. */
  const wrap = $("flowwrap");
  const bar = document.querySelector("#view-flow .probbar");
  if (wrap && bar) {
    // Only a height that was really measured. The text size can be changed from another view,
    // where this view is not drawn and every box is nothing tall, and writing that would leave
    // the drawer running to the foot of the column when the person came back to it.
    const tall = Math.round(bar.getBoundingClientRect().height);
    if (tall > 0) wrap.style.setProperty("--probh", tall + "px");
  }
}

/* Put the settings away. Only where they are a drawer: in a column there is nothing to put away,
   and emptying it would take the person's place in the flow from them for nothing. */
function closePanel() {
  if (!panelIsDrawer()) return;
  PICKED = "";
  paint();
}

/* ------------------------------------------------------------------ what may be linked */
/* The catalog's answer, out of the table, with the three things the canvas itself knows: a card
   cannot feed itself, the same link cannot be drawn twice, and a socket not marked `many` holds
   one link. Those are about the drawing rather than about the tools, which is why they are worded
   here and everything else is not. */
function refusedLink(fromId, fromPort, toId, toPort) {
  const a = nodeOf(fromId), b = nodeOf(toId);
  if (!a || !b) return "that card is not on the canvas";
  if (a.id === b.id) return "a card cannot be linked to itself";
  // What arrived at a card that has run is what it ran with. A new link into it would start a card
  // whose work goes nowhere, since this one is not run again, and the runner refuses it.
  if (alreadyRan(b)) {
    return (cardOf(b.card) || {}).name + " has already run, so nothing new can be linked into it";
  }
  if (FLOW.edges.some((e) => e.from === fromId && e.fromPort === fromPort
                          && e.to === toId && e.toPort === toPort)) return "that link is drawn already";
  const into = cardOf(b.card), port = portOf(into, "inputs", toPort);
  if (into && port && !port.many && FLOW.edges.some((e) => e.to === toId && e.toPort === toPort)) {
    return into.name + "'s " + port.label + " takes one link, and it has one already";
  }
  const why = REFUSALS[a.card + ":" + fromPort + ">" + b.card + ":" + toPort];
  return why === undefined ? "" : why;        // a card the table has never heard of: check() will say
}

function refusedEdge(e) {
  const a = nodeOf(e.from), b = nodeOf(e.to);
  if (!a || !b) return "";
  const why = REFUSALS[a.card + ":" + e.fromPort + ">" + b.card + ":" + e.toPort];
  return why === undefined ? "" : why;
}

/* ------------------------------------------------------------------ the drawing */
/* Drawn twice: on the canvas, where a flow is put together, and in the run view, where the same
   cards stand where they were put and say what has become of them. One builder rather than two,
   because two would part company the first time a card grew a socket, and the whole point of the
   run view is that it is the drawing a person recognizes.

   `rows` is the status answer's cards while a flow is being watched and null while one is being
   drawn, and everything that edits the drawing hangs off its being null. */
function drawFlow(box, svg, doc, rows) {
  Array.prototype.slice.call(box.querySelectorAll(".node")).forEach((n) => n.remove());
  (doc.nodes || []).forEach((node) => {
    box.appendChild(nodeBox(node, rows ? rowFor(rows, node.id) : null));
  });
  sizeBox(box, doc);
  wiresInto(svg, box, doc, rows);
}

function rowFor(rows, id) {
  return (rows || []).find((r) => r.id === id) || { id: id, state: "waiting", jobs: [] };
}

function paint() {
  drawFlow($("canvas"), $("wires"), FLOW, null);
  applyZoom();                       // the drawing's size may have changed, and its margins with it
  paintPanel();
  paintProblems();
  syncPanelLayer();                  // the bar under the drawing may be a line taller or shorter
  // An empty canvas says where to start, and has nothing yet to zoom; a drawing needs neither.
  if ($("canvasempty")) $("canvasempty").hidden = FLOW.nodes.length > 0;
  const zoombar = document.querySelector(".zoombar");
  if (zoombar) zoombar.hidden = !FLOW.nodes.length;
  if (FIT_PENDING && $("canvasgrid").clientWidth) {
    FIT_PENDING = false;
    fitZoom(false);
  }
}

/* A flow just opened is fitted to the view once the canvas can be measured, which is not until it
   is on the screen: a flow opened while another view is showing is fitted when it is shown. */
let FIT_PENDING = false;

function socket(node, port, side, live) {
  const s = el("button", "sock");
  s.setAttribute("data-" + side, port.key);
  // the dot on the card's edge, then the label; the output column turns the pair round, so the
  // label stays beside what it carries and the dot stays on the outside
  s.appendChild(el("span", "dot" + (port.many ? " many" : "")));
  const text = el("span", "st");
  text.appendChild(el("span", "sl", port.label));
  s.appendChild(text);
  s.dataset.tip = "carries " + (port.kinds || []).map((k) => CAT.kinds[k]).join("; or ")
                + (port.many ? ". Several links may arrive here." : "");
  s.title = s.dataset.tip;
  if (!live) return s;                // a watched card's sockets are read, not rewired
  if (side === "out") {
    s.addEventListener("pointerdown", (e) => startWire(e, node, port));
  } else {
    // while a link is being drawn the socket takes the pointer, so the card does not move with it
    s.addEventListener("pointerdown", (e) => { if (WIRE) e.stopPropagation(); });
  }
  return s;
}

function nodeBox(node, run) {
  const card = cardOf(node.card);
  const state = run ? (run.state || "waiting") : "";
  const box = el("div", "node" + (run ? " watched " + state : (PICKED === node.id ? " on" : "")));
  box.dataset.node = node.id;
  box.style.left = Math.round(node.at[0] || 0) + "px";
  box.style.top = Math.round(node.at[1] || 0) + "px";
  const head = el("div", "nhead");
  const badge = el("span", "nbadge");
  if (card) badge.appendChild(cardGlyph(card));
  head.appendChild(badge);
  const names = el("span", "nnames");
  names.appendChild(el("span", "nname", card ? card.name : node.card));
  // What the card does is the tooltip on its head; only a card this launcher cannot draw says
  // something on its face.
  if (!card) names.appendChild(el("span", "ntag", "not a card this launcher knows"));
  if (card) head.title = card.name + ": " + card.tagline;
  head.appendChild(names);
  if (!run && alreadyRan(node)) {
    // Said on the card itself, so which cards are locked is seen before one is tried.
    const ran = el("span", "opt", "has run");
    ran.title = "Its results are on disk and Carry on does not make them again, so it cannot be "
      + "changed or taken off";
    head.appendChild(ran);
  } else if (!run && !(card && card.fixed)) {
    // Not on a card the canvas puts in by itself: it belongs to its link and would only refuse.
    // Its panel says how it goes, which is with the link.
    const off = el("button", "nx", "×");
    off.title = "Take " + (card ? card.name : node.card) + " off the canvas";
    off.onclick = (e) => { e.stopPropagation(); dropCard(node.id); };
    head.appendChild(off);
  }
  box.appendChild(head);
  if (run) {
    // On its own line rather than beside the name: every card on the canvas is the same width,
    // and a state put next to "RFD4-Proteina" leaves the name an ellipsis. The state and the job
    // ids are what is worth reading off the drawing - the ids because squeue is where a person
    // goes next - and the folder is in the list beside it, where there is room for it.
    const strip = el("div", "nrun");
    strip.appendChild(el("span", "sp " + state, state));
    if ((run.jobs || []).length) {
      strip.appendChild(el("span", "njobs", (run.jobs.length === 1 ? "job " : "jobs ")
                                            + run.jobs.join(" ")));
    }
    box.appendChild(strip);
  }
  if (card) {
    const socks = el("div", "socks");
    const left = el("div", "col in"), right = el("div", "col out");
    (card.inputs || []).forEach((p) => left.appendChild(socket(node, p, "in", !run)));
    (card.outputs || []).forEach((p) => right.appendChild(socket(node, p, "out", !run)));
    socks.appendChild(left);
    socks.appendChild(right);
    box.appendChild(socks);
  }
  if (run) {
    if (run.note) {
      // Kept to a few lines on the drawing, which is shrunk to fit; the whole is in the tooltip
      // and in the stage beside it.
      const note = el("div", "nnote", run.note);
      note.title = run.note;
      box.appendChild(note);
    }
  } else {
    box.addEventListener("pointerdown", (e) => startMove(e, node, box));
  }
  return box;
}

function sizeBox(box, doc) {
  let w = CANVAS_MIN[0], h = CANVAS_MIN[1];
  (doc.nodes || []).forEach((n) => {
    w = Math.max(w, Math.round(n.at[0] || 0) + 300);
    h = Math.max(h, Math.round(n.at[1] || 0) + 250);
  });
  box.style.width = w + "px";
  box.style.height = h + "px";
}

/* How much a drawing is scaled on the screen, read off the element rather than remembered. Both
   drawings can be scaled - the canvas by its zoom, the run view's to fit its space - and every
   conversion between the screen and the drawing goes through this, so a card dropped, dragged or
   wired lands where the pointer is at any zoom. */
function scaleOf(box) {
  const shown = box.getBoundingClientRect().width;
  return box.offsetWidth && shown ? shown / box.offsetWidth : 1;
}

function pointIn(e) {
  const box = $("canvas"), c = box.getBoundingClientRect(), k = scaleOf(box);
  return { x: (e.clientX - c.left) / k, y: (e.clientY - c.top) / k };
}

/* ------------------------------------------------------------------ zoom */
/* The canvas can be shrunk to see a whole flow, or enlarged to read one card. A transform scales
   what is drawn and not the space it takes, so the margins are set to make the scrollable area
   match what is on the screen: without them, zoomed in, the far edge of the flow cannot be
   scrolled to, and zoomed out there is a field of empty scrolling after it. */
let ZOOM = 1;
const ZOOM_MIN = 0.35, ZOOM_MAX = 1.6;

/* The text size chosen in the header. The drawing follows it as a whole, through the same scale
   as its own zoom: its cards are drawn at fixed sizes, so they grow together with the gaps between
   them and none lands on another, and every conversion between the screen and the drawing measures
   the scale off the element (scaleOf), so a card still goes where the pointer puts it. */
function uiScale() {
  const k = window.HLZoom ? Number(window.HLZoom.get()) : 1;
  return k > 0 ? k : 1;
}
addEventListener("hl-zoom", () => {
  applyZoom();
  syncPanelLayer();
  if (STAGE === "run") fitRunDrawing();
});
// The window itself can cross the same line, by being dragged narrower.
addEventListener("resize", () => { syncPanelLayer(); });

/* Under this much of full size a card's own text would be drawn below 13px, so it is not drawn
   small: the card drops to its name and its dots, and the drawing reads as the shape of the flow.
   The name itself is held readable by --nd-name, which divides by the scale below. */
const DRAW_READABLE = 0.85;
/* And this is as small as the drawing goes. Half size is where a name at 13px on the screen
   still fits a card 214 of the drawing's pixels wide; past it the names, not just the detail,
   stop being readable, and a drawing nobody can read is not a smaller drawing. */
const EFF_MIN = 0.5;

function applyZoom() {
  const box = $("canvas");
  if (!box) return;
  const k = ZOOM * uiScale();
  box.style.transformOrigin = "0 0";
  box.style.transform = k === 1 ? "" : "scale(" + k + ")";
  box.style.marginRight = Math.round(box.offsetWidth * (k - 1)) + "px";
  box.style.marginBottom = Math.round(box.offsetHeight * (k - 1)) + "px";
  /* The scale the drawing is actually drawn at, for the stylesheet. --nd-name divides by it, so
     a card's name is the same number of screen pixels whatever the drawing is zoomed to. It was
     declared in the sheet and set by nobody, which left every name frozen at 14 of the drawing's
     own pixels: at the 38% a fitted example opened at, "Target" was drawn at 5.3px. */
  box.style.setProperty("--flow-eff", String(Math.round(k * 1000) / 1000));
  const small = k < DRAW_READABLE;
  if (box.classList.contains("overview") !== small) {
    box.classList.toggle("overview", small);
    // A card changes height when its detail goes, and the wires are drawn from where the
    // sockets actually are, so they are measured again rather than left where they were.
    paintWires();
  }
  const area = box.closest(".canvasbox");
  if (area) {
    let says = area.querySelector(".overviewsays");
    if (small && !says) {
      says = el("p", "overviewsays", "The shape of the flow. Zoom in to read a card.");
      area.querySelector(".canvasarea").appendChild(says);
    } else if (!small && says) {
      says.remove();
    }
    if (says) says.hidden = !$("canvas").querySelector(".node");
  }
  const label = $("zoomreset");
  if (label) label.textContent = Math.round(ZOOM * 100) + "%";
}

/* Zoom to `z`, keeping the point under `about` (screen coordinates) where it is, so zooming with
   the wheel zooms into what the pointer is on rather than into the top-left corner. */
/* The least this zoom may be at the text size in use: the drawing's own zoom times the text
   size is what is on the screen, so at 150% text the drawing stops zooming out sooner. */
function zoomFloor() {
  return Math.max(ZOOM_MIN, Math.round((EFF_MIN / uiScale()) * 100) / 100);
}

function setZoom(z, about) {
  const grid = $("canvasgrid");
  const was = ZOOM;
  ZOOM = Math.min(ZOOM_MAX, Math.max(zoomFloor(), Math.round(z * 100) / 100));
  if (ZOOM === was) return;
  const k = uiScale();
  const r = grid.getBoundingClientRect();
  const px = about ? about.x - r.left : grid.clientWidth / 2;
  const py = about ? about.y - r.top : grid.clientHeight / 2;
  const cx = (grid.scrollLeft + px) / (was * k), cy = (grid.scrollTop + py) / (was * k);
  applyZoom();
  grid.scrollLeft = cx * ZOOM * k - px;
  grid.scrollTop = cy * ZOOM * k - py;
}

/* The whole flow in view. Only ever shrinks to do it on its own: a flow that already fits is
   left at full size, because a card drawn larger than it needs to be is no easier to read. */
function fitZoom(enlarge) {
  const grid = $("canvasgrid"), box = $("canvas");
  let wide = 0, tall = 0;
  box.querySelectorAll(".node").forEach((n) => {
    wide = Math.max(wide, n.offsetLeft + n.offsetWidth);
    tall = Math.max(tall, n.offsetTop + n.offsetHeight);
  });
  if (!wide) { setZoom(1); return; }
  // The scale the drawing needs on the screen, as the zoom that gives it at this text size. It
  // is not followed past the floor: fitting a seven-card flow into a laptop's canvas wanted 38%,
  // and at 38% every word in it was drawn at about 5px. Past the floor the flow is larger than
  // the view and is scrolled to, which is what a drawing larger than its paper does.
  const z = Math.min((grid.clientWidth - 30) / wide, (grid.clientHeight - 30) / tall) / uiScale();
  setZoom(enlarge ? z : Math.min(1, z));
  grid.scrollLeft = 0;
  grid.scrollTop = 0;
}

function socketAt(box, id, side, port) {
  const node = box.querySelector('.node[data-node="' + id + '"]');
  return node ? node.querySelector('.sock[data-' + side + '="' + port + '"] .dot') : null;
}

/* Where a socket's dot has ended up, measured rather than worked out: a card is as tall as its own
   text made it, so the only thing that knows where its third socket is is the card. The box has to
   be on the screen for this to answer, which is why a stage is shown before it is drawn. */
function where(box, id, side, port) {
  const dot = socketAt(box, id, side, port);
  if (!dot) return null;
  const c = box.getBoundingClientRect(), r = dot.getBoundingClientRect(), k = scaleOf(box);
  return { x: (r.left + r.width / 2 - c.left) / k, y: (r.top + r.height / 2 - c.top) / k };
}

function curve(a, b) {
  const reach = Math.max(42, Math.abs(b.x - a.x) / 2);
  return "M" + a.x + " " + a.y + " C" + (a.x + reach) + " " + a.y
       + " " + (b.x - reach) + " " + b.y + " " + b.x + " " + b.y;
}

function wirePath(d, cls) {
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", d);
  p.setAttribute("class", cls);
  return p;
}

/* The links into a given box. On the canvas each also carries a wide invisible path so it can be
   clicked off without aiming, and says why it is refused. A watched flow has neither, since there
   is nothing there to change; instead a link goes solid once the card it leaves has finished, so
   the drawing fills in as the flow runs. */
function wiresInto(svg, box, doc, rows) {
  svg.innerHTML = "";
  (doc.edges || []).forEach((edge, i) => {
    const a = where(box, edge.from, "out", edge.fromPort);
    const b = where(box, edge.to, "in", edge.toPort);
    if (!a || !b) return;
    const d = curve(a, b);
    if (rows) {
      svg.appendChild(wirePath(d, "wire"
        + (rowFor(rows, edge.from).state === "done" ? " ran" : " pending")));
      return;
    }
    const why = refusedEdge(edge);
    // A link into a card that has run is what that card ran with, so it stays. The links leaving
    // one are the inputs of cards that have not run, and those can go.
    const into = nodeOf(edge.to);
    const kept = alreadyRan(into);
    const hit = wirePath(d, "hit" + (kept ? " lockedin" : ""));
    if (!kept) hit.addEventListener("click", () => { FLOW.edges.splice(i, 1); paint(); check(); });
    const tip = document.createElementNS("http://www.w3.org/2000/svg", "title");
    tip.textContent = kept
      ? (cardOf(into.card) || {}).name + " has already run with what arrived along this link, "
        + "so it stays"
      : why ? why + " - click to take this link out" : "click to take this link out";
    hit.appendChild(tip);
    svg.appendChild(hit);
    svg.appendChild(wirePath(d, "wire" + (why ? " bad" : "")));
  });
  if (!rows && WIRE) {
    const a = where(box, WIRE.from, "out", WIRE.port);
    if (a) svg.appendChild(wirePath(curve(a, { x: WIRE.x, y: WIRE.y }), "wire live"));
  }
}

function paintWires() { wiresInto($("wires"), $("canvas"), FLOW, null); }

/* ------------------------------------------------------------------ moving a card */
function pick(id) {
  PICKED = id;
  Array.prototype.slice.call($("canvas").querySelectorAll(".node"))
       .forEach((n) => n.classList.toggle("on", n.dataset.node === id));
  paintPanel();
}

function startMove(e, node, box) {
  if (WIRE || e.target.closest(".sock") || e.target.closest(".nx")) return;
  pick(node.id);
  const start = pointIn(e), at = [Math.round(node.at[0] || 0), Math.round(node.at[1] || 0)];
  const move = (ev) => {
    const now = pointIn(ev);
    node.at = [Math.max(0, Math.round(at[0] + now.x - start.x)),
               Math.max(0, Math.round(at[1] + now.y - start.y))];
    box.style.left = node.at[0] + "px";
    box.style.top = node.at[1] + "px";
    paintWires();
  };
  const up = () => {
    box.removeEventListener("pointermove", move);
    box.removeEventListener("pointerup", up);
    sizeBox($("canvas"), FLOW);
    paintWires();
  };
  try { box.setPointerCapture(e.pointerId); } catch (ignore) { /* a mouse without capture */ }
  box.addEventListener("pointermove", move);
  box.addEventListener("pointerup", up);
  e.preventDefault();
}

/* ------------------------------------------------------------------ drawing a link */
/* Two ways round, because both are natural and one of them works with a trackpad on a knee: drag
   from an output socket to an input socket, or click the output socket and then the input one. */
function markSockets(hot) {
  Array.prototype.slice.call($("canvas").querySelectorAll(".sock[data-in]")).forEach((s) => {
    const id = s.closest(".node").dataset.node;
    s.classList.remove("ok", "no", "hot");
    s.title = s.dataset.tip;
    if (!WIRE) return;
    const why = refusedLink(WIRE.from, WIRE.port, id, s.getAttribute("data-in"));
    s.classList.add(why ? "no" : "ok");
    if (why) s.title = why;
    if (s === hot) s.classList.add("hot");
  });
  Array.prototype.slice.call($("canvas").querySelectorAll(".sock[data-out]")).forEach((s) => {
    const id = s.closest(".node").dataset.node;
    s.classList.toggle("from", !!WIRE && WIRE.from === id
                                     && WIRE.port === s.getAttribute("data-out"));
  });
}

function socketUnder(e) {
  const at = document.elementFromPoint(e.clientX, e.clientY);
  return at && at.closest ? at.closest(".sock[data-in]") : null;
}

function startWire(e, node, port) {
  e.preventDefault();
  e.stopPropagation();
  if (WIRE) return endWire();
  const a = where($("canvas"), node.id, "out", port.key) || { x: 0, y: 0 };
  WIRE = { from: node.id, port: port.key, x: a.x, y: a.y, moved: false, armed: false };
  document.addEventListener("pointermove", onWireMove);
  document.addEventListener("pointerup", onWireUp);
  markSockets();
  paintWires();
}

function onWireMove(e) {
  if (!WIRE) return;
  const at = pointIn(e);
  if (Math.abs(at.x - WIRE.x) + Math.abs(at.y - WIRE.y) > 3) WIRE.moved = true;
  WIRE.x = at.x;
  WIRE.y = at.y;
  markSockets(socketUnder(e));
  paintWires();
}

function onWireUp(e) {
  if (!WIRE) return;
  const sock = socketUnder(e);
  if (sock) return finishWire(sock);
  // a click rather than a drag: the link stays on the pointer until an input socket is clicked
  if (!WIRE.armed && !WIRE.moved) { WIRE.armed = true; return; }
  endWire();
}

function finishWire(sock) {
  const id = sock.closest(".node").dataset.node, port = sock.getAttribute("data-in");
  const why = refusedLink(WIRE.from, WIRE.port, id, port);
  if (why) {
    flash(why, true);
    endWire();
    return;
  }
  FLOW.edges.push({ from: WIRE.from, fromPort: WIRE.port, to: id, toPort: port });
  endWire();
  paint();
  check();
}

function endWire() {
  WIRE = null;
  document.removeEventListener("pointermove", onWireMove);
  document.removeEventListener("pointerup", onWireUp);
  markSockets();
  paintWires();
}

/* ------------------------------------------------------------------ the settings panel */
/* A field is drawn from what the catalog says about it, and the catalog says more than it
   used to: what a number may be, what an empty box would mean, and what the field becomes when
   another one is answered. That last is why these are not independent boxes - an engine that
   fixes its own timestep has none to set, and structures picked by rank need no names. Rather
   than let somebody fill those in and find out from the log, the field says so where they are
   looking. */

/* The setting as it stands, once every rule in `varies` has been applied against this card's
   other answers. Returns a copy: the catalog is shared and is never written to. */
function effective(node, setting) {
  const now = Object.assign({}, setting);
  (setting.varies || []).forEach((rule) => {
    const said = node.settings[rule.on];
    let got = rule.cases[String(said)];
    // A tick is "true" or "false" here. A catalog from a cluster that spelt one the way Python
    // prints a bool is still understood, rather than leaving the field it governs unchanged.
    if (got === undefined && typeof said === "boolean") got = rule.cases[said ? "True" : "False"];
    if (got) Object.assign(now, got);
  });
  /* One flow, one pH, and Flow.check() refuses a tool whose own pH box says another: the receptor
     it is given was prepared at the flow's. So the box is the flow's, set for you, rather than a
     second answer to keep in step by hand. */
  const ph = setting.key === "ph" && !(cardOf(node.card) || {}).fixed ? flowPh() : null;
  if (ph) {
    Object.assign(now, { value: ph.value,
                         why: "the flow's pH, from the " + ph.from + " card: one flow has one pH" });
  }
  return now;
}

/* The pH the flow is prepared at, and the card that says so, or null where no preparation does:
   the first preparation card's, as Flow.check() reads it (flow._flow_ph). */
function flowPh() {
  const prep = (FLOW.nodes || []).find((n) => (cardOf(n.card) || {}).fixed && n.settings
    && n.settings.ph !== undefined && n.settings.ph !== null && n.settings.ph !== "");
  if (!prep) return null;
  const value = Number(prep.settings.ph);
  return Number.isFinite(value) ? { value: value, from: (cardOf(prep.card) || {}).name } : null;
}

/* A bound as a person reads it. 100000 to 50000000 under a box cannot be told from 10000000 at
   a glance, and the bounds are there to be read rather than counted. */
function asCount(n) {
  const x = Number(n);
  return Number.isFinite(x) ? x.toLocaleString("en-US") : String(n);
}

/* hh:mm:ss, or d-hh:mm:ss past a day, which is what Slurm takes. */
function asWalltime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const days = Math.floor(s / 86400);
  const pad = (n) => String(n).padStart(2, "0");
  const clock = pad(Math.floor((s % 86400) / 3600)) + ":" + pad(Math.floor((s % 3600) / 60))
    + ":" + pad(s % 60);
  return days ? days + "-" + clock : clock;
}

/* The other way: a walltime a person typed, in seconds, or NaN if it is not one. */
function walltimeSeconds(text) {
  const m = /^(?:(\d+)-)?(\d+):([0-5]\d):([0-5]\d)$/.exec(String(text || "").trim());
  if (!m) return NaN;
  return (+(m[1] || 0)) * 86400 + (+m[2]) * 3600 + (+m[3]) * 60 + (+m[4]);
}

/* Two arrows on a box, because the browser's own are a few pixels of target clipped against the
   border, and on a touchpad they are unhittable. Out of the tab order on purpose: the box is
   already a number input, so the keyboard has up and down without them. */
function arrows(onStep, upSays, downSays) {
  const box = el("div", "arrows");
  [["\u25B4", +1, upSays], ["\u25BE", -1, downSays]].forEach(([glyph, by, says]) => {
    const button = el("button", null, glyph);
    button.type = "button";
    button.tabIndex = -1;
    button.title = says;
    button.setAttribute("aria-label", says);
    button.onclick = (e) => { e.preventDefault(); onStep(by); };
    box.appendChild(button);
  });
  return box;
}

/* How long a fold is likely to want, as a generous allowance rather than a measurement. It is
   worked out from what the card says - the sequence, the copies, and how many models - because
   those are what the time actually follows. It rounds up to the half hour and never goes below
   an hour: a job that ends early is charged for what it used, and one that runs out has to be
   queued again from the start. Only offered where there is something real to work it out from. */
function suggestWalltime(node, card) {
  const sequence = String(node.settings.sequence || "").replace(/\s+/g, "");
  if (!sequence) return 0;
  const residues = sequence.length * Math.max(1, Number(node.settings.copies) || 1);
  const models = Number(node.settings.num_models) || 1;
  const recycles = Number(node.settings.num_recycle || node.settings.recycling_steps) || 3;
  const perModel = 6 * Math.max(1, recycles / 3) * Math.max(1, residues / 100);
  const minutes = 25 + perModel * models * (card && card.key === "boltz2" ? 2 : 1);
  return Math.max(3600, Math.ceil((minutes * 60) / 1800) * 1800);
}

/* A card whose run is on disk. Its settings are shown because they are what produced what is in
   that folder, and they are not offered because changing them would make the flow's own record a
   lie about it: Carry on does not run it again. */
function alreadyRan(node) {
  return !!(EDITING && node && EDITING.done.indexOf(node.id) >= 0);
}

/* The canvas is being given another flow, so the one being corrected is let go, unsaved. Left
   set, the button under the canvas still read Save and carry on, and pressed, it wrote whatever
   was on the canvas by then - the example, a saved flow, a blank one drawn afresh - into the
   stopped flow's folder, and carried that on instead, beside the record of the flow that had
   stopped. */
function stopCorrecting() {
  EDITING = null;
  FIXING = null;
  REFUSED = [];
}


/* ------------------------------------------------------------- where a structure comes from */
/* The target card holds four settings for one question: a menu saying which of three places the
   structure comes from, a box for a PDB id, a box for a path on the cluster, and which copy of
   an entry to fetch - and whichever two do not apply are drawn as gray boxes holding half a
   sentence about not being read. That is four things to understand to answer "which molecule".

   Here it is one button. Choose a structure opens the browser every page of ours opens - this
   computer, your scratch, your home, every tool's results, the group's folders, and a PDB code -
   and whatever is chosen there sets the three settings between them. Once one is chosen the card
   shows what it is and a Change beside it. Nothing about the flow's own record changes: the same
   three keys are written, so a flow drawn today and one drawn last week are the same flow.

   Which copy to fetch stays a setting of its own, under More settings, and only for an id. */

const STRUCTURE_KEYS = ["source", "pdb_id", "path", "assembly"];

/* Whether a card asks the structure question, which is the target card and anything else that
   grows one of these three settings. Read off the settings rather than off the card's name, so a
   catalog from the cluster with another such card is answered correctly too. */
function asksForStructure(card) {
  const keys = ((card && card.settings) || []).map((s) => s.key);
  return keys.indexOf("source") >= 0 && keys.indexOf("pdb_id") >= 0 && keys.indexOf("path") >= 0;
}

/* A structure handed in by a folding card linked to this one. */
function foldedInto(node) {
  return (FLOW.edges || []).some((e) => e.to === node.id && e.toPort === "structure");
}

/* The file's own name, with the folder it is in under it: a path is read from its end. */
function fileName(path) {
  return String(path || "").replace(/\/+$/, "").split("/").pop() || String(path || "");
}

function structureField(node) {
  const ran = alreadyRan(node);
  const wrap = el("label", "field structure" + (ran ? " fixed" : ""));
  const head = el("span", "flab", "Structure");
  wrap.appendChild(head);
  const say = (text) => wrap.appendChild(el("p", "why2", text));

  if (foldedInto(node)) {
    // The link answers it, so there is nothing to ask and nothing to show but what it is.
    if (node.settings.source !== "folded" && !ran) { node.settings.source = "folded"; touch(); }
    head.appendChild(el("span", "opt set", "set for you"));
    say("The folding card linked to this one hands it in.");
    return wrap;
  }

  const source = node.settings.source || "rcsb";
  const id = String(node.settings.pdb_id || "").trim();
  const path = String(node.settings.path || "").trim();
  const chosen = source === "rcsb" ? (id ? "PDB " + id.toUpperCase() : "")
               : source === "file" ? (path ? fileName(path) : "") : "";

  if (chosen) {
    const got = el("div", "chosen");
    got.appendChild(el("span", "chosenname", chosen));
    if (source === "file" && path) {
      // The folder as a person reads it, not as the cluster spells it: the last few names with
      // the rest put away, which is how a run's folder is written everywhere else on this page.
      // The whole of it is the tooltip, for anybody who wants to copy it.
      const folder = path.replace(/\/[^/]*$/, "") || "/";
      const where = el("span", "chosenwhere",
                       typeof shortPath === "function" ? shortPath(folder) : folder);
      where.title = path;
      got.appendChild(where);
    }
    wrap.appendChild(got);
  }

  const acts = el("div", "tickacts");
  const pick = el("button", "btn" + (chosen ? " sm" : " pri"), chosen ? "Change" : "Choose a structure");
  pick.type = "button";
  pick.id = "set-path";
  pick.disabled = !!ran;
  pick.title = "Your computer, your scratch, a tool's results, or a PDB code";
  pick.onclick = () => {
    if (!window.canPick || !window.canPick()) {
      flash("This launcher is too old for the structure browser. Download the current one.", true);
      return;
    }
    window.hubPicker({
      start: source === "file" && path ? path.replace(/\/[^/]*$/, "") : "",
      onPath: (where) => {
        node.settings.source = "file";
        node.settings.path = where;
        touch();
        paintPanel();
      },
      onCode: (code) => {
        node.settings.source = "rcsb";
        node.settings.pdb_id = String(code || "").trim().toUpperCase();
        touch();
        paintPanel();
      },
    });
  };
  acts.appendChild(pick);
  wrap.appendChild(acts);
  if (!chosen) say("Your computer, your scratch, a tool's results, or a PDB code.");
  return wrap;
}

function field(node, setting) {
  const live = effective(node, setting);
  const ran = alreadyRan(node);
  const forced = Object.prototype.hasOwnProperty.call(live, "value");
  const fixed = forced || ran;
  // Only a value the card itself supplies is written back. A card that has already run is shown
  // locked and left exactly as it is: writing here would change what it ran with, and the runner
  // refuses a finished card that has changed, so Save and carry on would be refused for a change
  // the person never made.
  if (forced && !ran && node.settings[setting.key] !== live.value) {
    // The answer is not the person's to give here, so it is made the stored one as well: what
    // the card is launched with and what the panel shows are never allowed to differ.
    node.settings[setting.key] = live.value;
  }
  /* A setting the card answers for itself, with nothing in it and nothing to say but that it
     is not read, is not drawn. It was: a gray box with half of "not read: the structure is
     fetched by its id" showing in it, which is a box nobody can explain and nobody can use.
     Where the card has actually put a value in, it is still shown, marked set for you. */
  if (forced && !ran && (live.value === "" || live.value == null)
      && String(live.placeholder || "").indexOf("not read") === 0) {
    return null;
  }
  const wrap = el("label", "field" + (fixed ? " fixed" : ""));
  const head = el("span", "flab", live.label);
  /* Hotspots are optional until something in the flow aims at a site, and then the flow is
     refused without them. A required box labeled optional is the worst of both: it is the one
     the person skipped, and the panel told them to. */
  const needed = setting.key === "hotspots" && siteCards().length > 0;
  if (needed && !fixed) {
    head.appendChild(el("span", "opt needed", "needed by " + siteCards()[0]));
  } else if (live.optional && !fixed) head.appendChild(el("span", "opt", "optional"));
  if (fixed && !ran) head.appendChild(el("span", "opt set", "set for you"));
  if (ran) head.appendChild(el("span", "opt", "already run"));
  wrap.appendChild(head);
  const put = (value) => { node.settings[setting.key] = value; touch(); };
  // A card that has run is shown as the canvas would show it, with nothing written back: a setting
  // its drawing never had is shown as the default the panel fills in, so an absent MM-GBSA is not
  // drawn as an unticked box on a card that computed it.
  const held = ran ? meant(node.settings[setting.key], setting) : node.settings[setting.key];
  let input;

  if (live.kind === "choice") {
    input = el("select", "pick");
    const choices = (live.choices || []).slice();
    // what a flow was saved with, even where the catalog no longer offers it: the box has to
    // show what is stored, and check() is the one that says it is no longer a choice
    if (held != null && String(held) !== "" && choices.indexOf(String(held)) < 0) {
      choices.push(String(held));
    }
    choices.forEach((c) => {
      const note = (live.notes || {})[c];
      // The short name in the box; what it means is its tooltip, and the list behind the ?.
      const o = el("option", null, choiceName(c));
      if (note) o.title = note;
      // The stored value, not what is shown: site_mode's "centre" is the name of the form field
      // the tool itself reads, so it is spelled its way in every record and sent as it is.
      o.value = c;
      input.appendChild(o);
    });
    input.value = held == null ? "" : String(held);
    // Every kind of box is shut on a card that has run. Only the ones typed into used to be: a menu
    // or a tick on a finished card could be changed, and Save and carry on was then refused for it.
    input.disabled = !!fixed;
    input.onchange = () => { put(input.value); paintPanel(); };
  } else if (live.kind === "yesno") {
    input = el("input", "tick");
    input.type = "checkbox";
    input.checked = !!held;
    input.disabled = !!fixed;
    input.onchange = () => { put(input.checked); paintPanel(); };
  } else if (live.kind === "ticks") {
    /* Names nobody can be expected to remember, spelt the way the tool spells them.
       Typed, one of them misspelt is a run that refuses at the far end; ticked, it cannot be. */
    const list = el("div", "ticklist");
    // The one field the fixer could not take anybody to: a problem names the setting, and the
    // panel had no box under that name to scroll to, focus and mark. The list is the box.
    list.id = "set-" + setting.key;
    list.tabIndex = -1;
    const chosen = new Set(String(held == null ? "" : held).split(",")
      .map((x) => x.trim()).filter(Boolean));
    // The list is taller than it is allowed to be, so how many names there are is said rather
    // than left to be found by scrolling, and how many are ticked with it.
    const tally = el("span", "why2", "");
    const count = () => {
      tally.textContent = chosen.size
        ? chosen.size + " of " + (live.choices || []).length + " ticked"
        : (live.placeholder || "");
    };
    const save = () => {
      put((live.choices || []).filter((c) => chosen.has(c)).join(","));
      count();
    };
    (live.choices || []).forEach((choice) => {
      const row = el("label", "tickrow");
      const box = el("input", "tick");
      box.type = "checkbox";
      box.checked = chosen.has(choice);
      box.disabled = !!fixed;
      box.onchange = () => { box.checked ? chosen.add(choice) : chosen.delete(choice); save(); };
      row.appendChild(box);
      row.appendChild(el("span", "tickname", choice));
      const note = (live.notes || {})[choice];
      if (note) row.title = note;
      list.appendChild(row);
    });
    wrap.appendChild(list);
    const how = el("div", "tickacts");
    [["All", () => (live.choices || []).forEach((c) => chosen.add(c))],
     ["None", () => chosen.clear()]].forEach(([name, doIt]) => {
      const b = el("button", "btn sm", name);
      b.type = "button";
      b.disabled = !!fixed;
      b.onclick = () => { doIt(); save(); paintPanel(); };
      how.appendChild(b);
    });
    wrap.appendChild(how);
    count();
    wrap.appendChild(tally);
    explainField(wrap, head, live);
    return wrap;
  } else if (live.kind === "number") {
    const spin = el("div", "numbox");
    input = el("input", "box num");
    input.type = "number";
    if (live.least != null) input.min = String(live.least);
    if (live.most != null) input.max = String(live.most);
    if (live.step != null) input.step = String(live.step);
    if (live.placeholder) input.placeholder = live.placeholder;
    input.value = held == null || held === "" ? "" : held;
    input.disabled = !!fixed;
    input.oninput = () => put(input.value === "" ? "" : Number(input.value));
    const by = Number(live.step) || 1;
    const clamp = (n) => {
      if (live.least != null) n = Math.max(Number(live.least), n);
      if (live.most != null) n = Math.min(Number(live.most), n);
      // Steps of 0.1 on a float would otherwise walk off into 7.400000000000001.
      return Math.round(n * 1e6) / 1e6;
    };
    const step = (way) => {
      // From an empty box the first press lands on the least it may be, rather than a step past
      // it. An empty library size stepped to 11 and an empty count of sampling steps to 600,000,
      // neither of which is a number anybody was reaching for.
      const from = input.value === "" ? null : Number(input.value);
      const want = from === null ? clamp(live.least != null ? Number(live.least) : 0)
        : clamp((Number.isFinite(from) ? from : 0) + way * by);
      input.value = String(want);
      put(want);
    };
    spin.appendChild(input);
    if (!fixed) spin.appendChild(arrows(step, "more", "less"));
    wrap.appendChild(spin);
    explainField(wrap, head, live);
    // What it may be, at the end of its name rather than on a line of its own.
    if (live.least != null && live.most != null && !fixed) {
      head.appendChild(el("span", "fbounds", asCount(live.least) + " to " + asCount(live.most)));
    }
    input.id = "set-" + setting.key;
    return wrap;
  } else if (live.kind === "duration") {
    /* Typing 04:00:00 to ask for four hours is eight keystrokes and two chances to get it wrong.
       The arrows move it by the half hour, and where the card knows enough to work one out, the
       button fills in an allowance rather than leaving somebody to guess. */
    const spin = el("div", "numbox");
    input = el("input", "box");
    input.type = "text";
    input.spellcheck = false;
    input.placeholder = live.placeholder || "04:00:00";
    input.value = held == null ? "" : String(held);
    input.disabled = !!fixed;
    input.oninput = () => put(input.value);
    const by = Number(live.step) || 1800;
    const step = (way) => {
      // An empty box, or one holding something that is not a time, steps to the shortest the
      // card allows rather than to that plus half an hour: the arrows are also the way out of a
      // box somebody has typed four hours into in words.
      const now = walltimeSeconds(input.value);
      let want = Number.isFinite(now) ? now + way * by : (Number(live.least) || 3600);
      if (live.least != null) want = Math.max(Number(live.least), want);
      if (live.most != null) want = Math.min(Number(live.most), want);
      input.value = asWalltime(want);
      put(input.value);
    };
    spin.appendChild(input);
    if (!fixed) spin.appendChild(arrows(step, "half an hour more", "half an hour less"));
    wrap.appendChild(spin);
    const guess = suggestWalltime(node, cardOf(node.card));
    if (guess && !fixed) {
      const acts = el("div", "tickacts");
      const b = el("button", "btn sm", "Estimate: " + asWalltime(guess));
      b.type = "button";
      b.title = "A generous allowance worked out from the sequence, the copies and the models "
        + "asked for. It is a guess, not a measurement; a job that ends early is charged for "
        + "what it used.";
      b.onclick = () => { input.value = asWalltime(guess); put(input.value); };
      acts.appendChild(b);
      wrap.appendChild(acts);
    }
    explainField(wrap, head, live);
    input.id = "set-" + setting.key;
    return wrap;
  } else if (live.kind === "range") {
    // A slider, between the two numbers the catalog gives as its choices. No card asks for one
    // yet; it is drawn rather than left out so that the first one that does needs nothing here.
    const bounds = (live.choices || []).map(Number);
    input = el("input", "slide");
    input.type = "range";
    input.min = bounds.length ? bounds[0] : 0;
    input.max = bounds.length > 1 ? bounds[1] : 100;
    input.value = held == null ? input.min : held;
    input.disabled = !!fixed;
    const read = el("span", "readout", String(input.value));
    input.oninput = () => { read.textContent = input.value; put(Number(input.value)); };
    wrap.appendChild(input);
    wrap.appendChild(read);
    explainField(wrap, head, live);
    input.id = "set-" + setting.key;
    return wrap;
  } else {
    input = el("input", "box");
    input.type = "text";
    input.spellcheck = false;
    if (live.kind === "path") input.placeholder = "a path on the cluster";
    if (live.kind === "residues") input.placeholder = "54,56,66-70";
    if (live.placeholder) input.placeholder = live.placeholder;
    input.value = held == null ? "" : String(held);
    input.disabled = !!fixed;
    input.oninput = () => put(input.value);
  }
  input.id = "set-" + setting.key;
  wrap.appendChild(input);
  explainField(wrap, head, live);
  return wrap;
}

/* A choice by its short name; the empty one is the tool's own default. */
function choiceName(c) {
  return c === "" ? "Default" : (SHOWN_AS[c] || c);
}

/* What a box is for, behind a ? beside its name rather than in a line under it: the reason the
   catalog gives, and what each of its choices means. A span and not a button: the field is a
   label, and a button inside one would take the label's clicks from the box it names. */
function explainField(wrap, head, live) {
  const notes = Object.keys(live.notes || {}).filter((k) => live.notes[k]);
  if (!live.why && !notes.length) return;
  const help = el("div", "fhelp");
  help.hidden = true;
  if (live.why) help.appendChild(el("p", null, live.why));
  if (notes.length) {
    const list = el("dl");
    notes.forEach((k) => {
      list.appendChild(el("dt", null, choiceName(k)));
      list.appendChild(el("dd", null, live.notes[k]));
    });
    help.appendChild(list);
  }
  const q = el("span", "q", "?");
  q.setAttribute("role", "button");
  q.tabIndex = 0;
  q.setAttribute("aria-expanded", "false");
  q.setAttribute("aria-label", "What " + live.label + " means");
  q.title = "What this means";
  const toggle = (e) => {
    e.preventDefault();
    e.stopPropagation();
    help.hidden = !help.hidden;
    q.setAttribute("aria-expanded", String(!help.hidden));
  };
  q.addEventListener("click", toggle);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") toggle(e); });
  head.appendChild(q);
  wrap.appendChild(help);
}

/* The cards on this canvas that are aimed at a site, by name. The same question the cluster
   asks before it refuses a flow for its hotspots (cards.aims_at_site): always for a design
   tool, and for a card that may or may not be aimed at one only while it is. Asked of the
   card and of what is filled in on it, so a card with no site does not make hotspots
   required. */
function siteCards() {
  return (FLOW.nodes || []).map((node) => {
    const card = cardOf(node.card);
    if (!card || !card.site) return null;
    if (card.site === "always") return card.name;
    const field = ((card.settings || []).find((x) => x.key === card.site) || {});
    let value = node.settings[card.site];
    if (value === undefined || value === null) value = field.default;
    if (value === undefined || value === null) value = true;
    if (typeof value === "string") {
      return ["", "0", "false", "no", "off"].indexOf(value.trim().toLowerCase()) < 0 ? card.name : null;
    }
    return value ? card.name : null;
  }).filter(Boolean);
}

/* A setting as the canvas spells it. One the drawing does not have is the card's default. A flow
   saved before these fields were empty-by-default says 0 where it meant the tool's own. Shown as
   0, that reads as a number somebody chose and the box hides what it means; emptied, it shows the
   gray words saying what the tool will do, which is what 0 always did. */
function meant(value, s) {
  if (value === undefined) return s.default === null ? "" : s.default;
  if (s.default === null && s.kind === "number" && (value === 0 || value === "0")) return "";
  return value;
}

function fillDefaults(node, card) {
  (card && card.settings ? card.settings : []).forEach((s) => {
    const now = meant(node.settings[s.key], s);
    if (now !== node.settings[s.key]) node.settings[s.key] = now;
  });
}

/* The cards whose further settings are open. Per card, not one switch for all of them: a person
   deep in the simulation's settings is not asking to see the fold's as well. */
const MORE = new Set();

/* The target's own ?, which the catalog gives no note: why it is the one card to fill in. */
const TARGET_ABOUT = "Everything linked to the target takes this receptor and these hotspots, "
  + "so the design and the simulation are both aimed at the same site. The hotspots are needed "
  + "by the cards that aim at a site; a simulation on its own needs none.";

/* What a card is for, behind a ? beside its name: a few lines that open under the name and close
   again, rather than a paragraph every time the card is picked. */
function explainCard(box, title, text) {
  if (!text) return;
  const help = el("div", "fhelp");
  help.hidden = true;
  help.appendChild(el("p", null, text));
  const q = el("button", "q", "?");
  q.type = "button";
  q.setAttribute("aria-expanded", "false");
  q.setAttribute("aria-label", "What this card does");
  q.title = "What this card does";
  q.onclick = () => {
    help.hidden = !help.hidden;
    q.setAttribute("aria-expanded", String(!help.hidden));
  };
  title.appendChild(q);
  box.appendChild(help);
}

function paintPanel() {
  const box = $("panel");
  box.innerHTML = "";
  const node = nodeOf(PICKED);
  // On a laptop, and at the larger text sizes, the settings slide over the drawing rather than
  // taking a column beside it; with nothing picked there is nothing to slide, and the class is
  // what the stylesheet reads to keep it shut.
  box.classList.toggle("empty", !node);
  // What is under the drawer is dimmed while it is open, so it reads as a layer on top.
  if ($("paneback")) $("paneback").hidden = !(node && panelIsDrawer());
  if (!node) {
    box.appendChild(el("p", "phint", "Pick a card on the canvas to fill it in."));
    return;
  }
  const shut = el("button", "pclose", "\u00d7");
  shut.type = "button";
  shut.title = "Close the settings (Esc)";
  shut.setAttribute("aria-label", "Close the settings");
  shut.onclick = closePanel;
  box.appendChild(shut);
  const card = cardOf(node.card);
  const title = el("div", "ptitle", card ? card.name : node.card);
  box.appendChild(title);
  box.appendChild(el("div", "ptag", card ? card.tagline : "not a card this launcher knows"));
  explainCard(box, title, card && card.note ? card.note : node.card === "target" ? TARGET_ABOUT : "");
  /* At most one highlighted line, and only when it says what to do. A card that has run says why
     its boxes are shut; the others are reasons, and wait behind the ? beside the name. */
  if (alreadyRan(node)) {
    box.appendChild(el("p", "pnote ran", "Already run: its settings are locked."));
  } else if (node.card === "target") {
    box.appendChild(el("p", "pnote", "Fill this in: every card linked to it aims here."));
  }
  if (card && card.fixed && !alreadyRan(node)) {
    box.appendChild(el("p", "pnote fixedcard", "Part of its link: take the link off to remove it."));
  }
  const settings = (card && card.settings) || [];
  if (card && !settings.length) {
    box.appendChild(el("p", "phint", "Nothing to fill in: it works from its links."));
  }
  /* Anything the drawing does not say is filled in from the catalog before the fields are
     drawn, so a box never shows one thing while the run does another. A tick box was the way this
     showed: a setting the drawing had no value for was drawn unticked, while the tool it goes to
     treats absent as yes - so MM-GBSA read as off on a run that would compute it. */
  if (!alreadyRan(node)) fillDefaults(node, card);

  /* The few that change what a run is, then the rest behind a fold. A tool's own defaults are
     the lab's considered answer to most of these, and making somebody scroll past twenty of them
     to reach the one they came for is how a panel stops being read at all. The fold stays open
     per card once it has been opened, so comparing two cards' settings does not mean opening it
     again each time. */
  /* The structure question is one button rather than four boxes, where the card asks it and
     this launcher can open the browser. An older launcher keeps the four boxes it always had. */
  const asOne = asksForStructure(card) && window.canPick && window.canPick();
  const keep = (s) => !asOne || STRUCTURE_KEYS.indexOf(s.key) < 0;
  if (asOne) box.appendChild(structureField(node));
  settings.filter((s) => !s.advanced && keep(s))
    .forEach((s) => { const f = field(node, s); if (f) box.appendChild(f); });
  // Which copy of an entry to fetch is a question only an id raises, and only then.
  if (asOne && (node.settings.source || "rcsb") === "rcsb" && !foldedInto(node)) {
    const which = (settings || []).find((x) => x.key === "assembly");
    if (which) { const f = field(node, which); if (f) box.appendChild(f); }
  }
  const deeper = settings.filter((s) => s.advanced && keep(s));
  if (deeper.length) {
    const open = MORE.has(node.id);
    const toggle = el("button", "btn sm more",
                      (open ? "Fewer settings" : "More settings") + " · " + deeper.length);
    toggle.onclick = () => { if (open) MORE.delete(node.id); else MORE.add(node.id); paintPanel(); };
    box.appendChild(toggle);
    if (open) {
      box.appendChild(el("p", "phint", "Left blank, each takes "
        + (card ? card.name : "the tool") + "'s own default."));
      deeper.forEach((s) => { const f = field(node, s); if (f) box.appendChild(f); });
    }
  }
  if (!alreadyRan(node) && !(card || {}).fixed) {
    const off = el("button", "btn sm", "Take this card off");
    off.onclick = () => dropCard(node.id);
    box.appendChild(off);
  }
}

/* ------------------------------------------------------------------ what is wrong with it */
function paintProblems() {
  const box = $("problems");
  box.innerHTML = "";
  // An empty canvas has one thing to do, and its list of problems would only say that there is
  // nothing on it. Launch stays shut all the same: the check still says so.
  /* Next, the instruction, and a button that takes the person to it: a line that says what to
     do next and leaves them to find it is half a signpost. */
  const nextline = (text, show) => {
    const next = el("p", "pgood");
    next.appendChild(el("span", "nextlab", "Next"));
    next.appendChild(document.createTextNode(text));
    if (show) {
      const b = el("button", "btn sm showme", "Show me");
      b.type = "button";
      b.onclick = show;
      next.appendChild(b);
    }
    box.appendChild(next);
  };
  const blank = !FLOW.nodes.length && !EDITING;
  if (blank) {
    nextline("Add a card from the left, or open the example.", () => {
      const first = document.querySelector(".palette .pcard");
      if (first) { first.scrollIntoView({ block: "center", behavior: "smooth" }); mark(first); }
    });
  } else if (!CHECKED) {
    box.appendChild(el("p", "pgood", "Checking…"));
  } else if (!liveIssues().length && !WARNINGS.length && !EDITING) {
    // Nothing to fix, so the next thing to do is read the plan, which queues nothing.
    nextline("Press Launch to read the plan. Nothing is queued yet.", () => {
      const go = $("flowlaunch");
      if (go) { go.scrollIntoView({ block: "center", behavior: "smooth" }); mark(go); }
    });
  }
  const todo = blank ? [] : fixList();
  // Why it stopped, kept on screen for the whole of the correcting: it is what the person came to
  // deal with, and it is not a problem the canvas can check, so it is said rather than listed.
  if (EDITING && EDITING.why) {
    const why = el("p", "fixwhy");
    why.appendChild(el("b", null, "Why it stopped: "));
    why.appendChild(document.createTextNode(EDITING.why.card + ", " + EDITING.why.note + ". "));
    const look = el("button", "btn sm", "Show me");
    look.onclick = () => goToIssue({ text: "", node: EDITING.why.node, key: "", failure: true });
    why.appendChild(look);
    if (!todo.length) {
      why.appendChild(document.createTextNode(" Nothing needs changing: Save and carry on tries "
        + "it again."));
    }
    box.appendChild(why);
  }
  if (CHECKED && todo.length) {
    /* One at a time. The header says how many are left and offers the next; each row is the
       problem in a sentence and a Fix that goes to the card and the box. When the last one is
       fixed the header says so and the button beside it is the one that carries on. */
    const head = el("div", "fixhead");
    const left = todo.length;
    const said = FIXING && !FIXING.failure && !todo.some((t) => sameIssue(t, FIXING))
      ? "Fixed. " + left + (left === 1 ? " thing" : " things") + " left."
      : left + (left === 1 ? " thing" : " things") + " to fix before this can run.";
    head.appendChild(el("span", "nextlab", "Next"));
    head.appendChild(el("span", "fixcount", said));
    const next = el("button", "btn sm go", FIXING ? "Next" : "Fix the first");
    next.onclick = () => goToIssue(todo[0]);
    head.appendChild(next);
    box.appendChild(head);
    const list = el("ol", "fixlist");
    todo.forEach((issue) => {
      const row = el("li", "fixrow" + (FIXING && sameIssue(issue, FIXING) ? " on" : "")
                     + (issue.failure ? " failure" : ""));
      row.appendChild(el("span", "fixtext", issue.text));
      if (issue.node || issue.key) {
        const go = el("button", "btn sm", issue.failure ? "Show me" : "Fix");
        go.onclick = () => goToIssue(issue);
        row.appendChild(go);
      }
      list.appendChild(row);
    });
    box.appendChild(list);
  } else if (CHECKED && EDITING) {
    box.appendChild(el("p", "pgood fixdone", (FIXING ? "Everything is fixed. " : "Nothing needs "
      + "fixing. ") + "Save and carry on queues what has not run."));
  }
  /* A warning is not a refusal, and must not read as one. The flow can be queued with these
     standing; what they say is what will happen to it, which somebody should know before
     pressing Launch rather than find out from the record afterwards. */
  if (CHECKED && WARNINGS.length && !blank) {
    const list = el("ul", "problist warn");
    WARNINGS.forEach((why) => list.appendChild(el("li", null, why)));
    box.appendChild(list);
  }
  $("flowlaunch").disabled = !CHECKED || liveIssues().length > 0;
  $("flowlaunch").textContent = EDITING ? "Save and carry on" : "Launch the flow\u2026";
  // A flow being corrected keeps its name: its folder, and the runs its finished cards made, are
  // named after it, and the cards still to run would otherwise be named after another.
  $("flowname").disabled = !!EDITING;
  $("flowname").title = EDITING ? "A flow keeps its name while it is corrected: its folder and "
    + "its runs are named after it" : "";
  // While a stopped flow is being corrected, the button that carries on is offered once there is
  // nothing left to fix: before that it is the next issue that is wanted, not the button.
  $("flowlaunch").hidden = !!EDITING && fixList().length > 0;
}

/* What is left to fix: the flow's own problems, and while a stopped flow is being corrected, why
   it stopped. That reason comes first because it is what the person came to deal with; it is not
   a problem the canvas can check, so it leaves the list once they have looked at it. */
/* The problems that count. While a stopped flow is being corrected, a problem on a card that has
   already run is not one: the card is locked, so it cannot be fixed, and its results are on disk,
   so it does not need to be. Listing it would be a dead end, and letting it shut Save and carry
   on would be a worse one. */
function liveIssues() {
  if (!EDITING) return ISSUES;
  return ISSUES.filter((i) => !i.node || EDITING.done.indexOf(i.node) < 0);
}

function fixList() {
  const live = liveIssues();
  return live.concat(REFUSED.filter((r) => !live.some((i) => i.text === r.text)));
}

/* Where a problem is, worked out from its sentence, for a launcher whose checker gives sentences
   alone. Every sentence about a setting names the card and the setting's own label, so the card
   is found by name and the box by label. It is a fallback and knows it: a sentence that matches
   nothing is still listed, with no Fix beside it, rather than sent somewhere wrong. */
function placeOf(text) {
  const out = { text: text, node: "", key: "" };
  const lower = String(text).toLowerCase();
  if (lower.indexOf("the flow needs a name") === 0) { out.key = "flowname"; return out; }
  let best = null;
  FLOW.nodes.forEach((n) => {
    const card = cardOf(n.card);
    if (!card) return;
    const named = lower.indexOf(card.name.toLowerCase()) === 0
      || (n.card === "target" && lower.indexOf("the target") >= 0);
    if (!named) return;
    let key = "";
    (card.settings || []).forEach((f) => {
      const label = f.label.toLowerCase();
      if (lower.indexOf(label) >= 0 && (!key || label.length > best.len)) {
        key = f.key;
        best = { len: label.length };
      }
    });
    if (!out.node || key) { out.node = n.id; out.key = key; }
  });
  return out;
}

function sameIssue(a, b) {
  return !!a && !!b && a.text === b.text && a.node === b.node && a.key === b.key;
}

/* Take the person to the box. The card is picked and brought into view on the canvas, its
   further settings are opened if the box is one of them, and the box itself is scrolled to,
   focused and marked, so the one thing to do next is type. */
function goToIssue(issue) {
  if (!issue) return;
  // Looking at the card that stopped is not fixing anything. Counted as the problem being fixed,
  // one look had the list say "Fixed." and "Everything is fixed ... with what you changed" over a
  // flow nobody had changed.
  if (!issue.failure) FIXING = issue;
  if (!issue.node && issue.key === "flowname") {
    paintProblems();
    const box = $("flowname");
    box.scrollIntoView({ block: "center", behavior: "smooth" });
    box.focus();
    mark(box);
    return;
  }
  const node = nodeOf(issue.node);
  if (!node) { paintProblems(); return; }
  PICKED = node.id;
  const card = cardOf(node.card);
  const field = card && issue.key ? (card.settings || []).find((f) => f.key === issue.key) : null;
  if (field && field.advanced) MORE.add(node.id);
  paint();
  paintProblems();
  const drawn = document.querySelector('.node[data-node="' + node.id + '"]');
  if (drawn) {
    drawn.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    mark(drawn);
  }
  if (!field) return;
  // The three settings behind the structure question are one button now, and a problem about any
  // of them has to land on it. It carries set-path, so that is where the other two are sent.
  const key = asksForStructure(card) && STRUCTURE_KEYS.indexOf(field.key) >= 0
              && field.key !== "assembly" ? "path" : field.key;
  // After the panel has been drawn: the box does not exist until it has.
  setTimeout(() => {
    const box = document.getElementById("set-" + key);
    if (!box) return;
    const at = box.closest(".field") || box;
    at.scrollIntoView({ block: "center", behavior: "smooth" });
    if (!box.disabled) box.focus({ preventScroll: true });
    mark(at);
  }, 60);
}

/* A ring that fades, so the eye lands on the right place without it staying there. */
function mark(node) {
  node.classList.remove("fixme");
  void node.offsetWidth;                         // restart the animation if it is already on
  node.classList.add("fixme");
  setTimeout(() => node.classList.remove("fixme"), 2600);
}

function touch() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(() => { check(); }, 250);
}

/* The launcher is asked about the flow as it stands, and answers with both the sentences and the
   table of what may reach what. So changing the binder length on the target re-reads every link
   already drawn: the ones that have become impossible are marked where they are, and said in the
   list under the canvas. */
/* Every tool's own pH box held to the flow's before the flow is checked, not only the one whose
   panel is open: changing the pH on a preparation would otherwise leave another card's box at
   the old one, and the flow refused for a box nobody had looked at. */
function holdToFlowPh() {
  const ph = flowPh();
  if (!ph) return;
  FLOW.nodes.forEach((n) => {
    const card = cardOf(n.card);
    if (!card || card.fixed || alreadyRan(n)) return;
    if ((card.settings || []).some((s) => s.key === "ph")) n.settings.ph = ph.value;
  });
}

async function check() {
  clearTimeout(checkTimer);
  REFUSED = [];                                 // something changed: the cluster is asked again
  FLOW.name = $("flowname").value.trim();
  holdToFlowPh();
  const mine = ++checkSeq;
  const sent = JSON.stringify(FLOW);
  let got;
  try {
    got = await api("/api/flow/check", { flow: FLOW, done: EDITING ? EDITING.done : [] });
  } catch (failure) {
    if (mine !== checkSeq) return;
    CHECKED = true;
    PROBLEMS = [failure.message];
    ISSUES = [{ text: failure.message, node: "", key: "" }];
    WARNINGS = [];
    paintProblems();
    return;
  }
  if (mine !== checkSeq) return;                // a later check has already answered
  /* The launcher may hand back the flow with something put right in it - a preparation on a link
     that carries a structure into the simulation, which is not optional and not
     a card anybody has to remember. Adopted only when nothing has been touched since it was asked,
     so a keystroke in a box during the round trip is never thrown away. */
  if (got.flow && JSON.stringify(FLOW) === sent) {
    FLOW = asDrawn(got.flow);
    FLOW.nodes.forEach((n) => { if (!alreadyRan(n)) fillDefaults(n, cardOf(n.card)); });
    /* Said by which card went in: the receptor's preparation and the other one do different
       things, and one line says what happened to the link just drawn. */
    const added = (got.added || []).map((id) => (nodeOf(id) || {}).card);
    const receptor = added.filter((k) => k === "prepare_receptor").length;
    const others = added.length - receptor;
    if (receptor) {
      flash("Prepare receptor was put in front of the docking, at the flow's pH."
            + (others ? " A preparation went on a link that carries a structure, too." : ""));
    } else if (others) {
      flash((others === 1 ? "A preparation was" : others + " preparations were")
            + " put on the link: a structure is prepared before it is simulated.");
    }
    paint();
  }
  REFUSALS = got.refusals || {};
  PROBLEMS = (got.problems || []).filter((p) => !staleJoin(String(p)));
  ISSUES = (got.issues || PROBLEMS.map(placeOf)).filter((i) => !staleJoin(i.text, i.node));
  WARNINGS = got.warnings || [];
  CHECKED = true;
  paintProblems();
  paintWires();
  markSockets();
}

/* A launcher whose own check calls a card "not joined to the target" when the drawing shows it
   reaching a card the target feeds is set aside here: the cluster's check, which queues the
   flow, is the one that counts. */
function staleJoin(text, nodeId) {
  const found = /^(.*) is not joined to the target$/.exec(String(text || ""));
  if (!found) return false;
  const nodes = FLOW.nodes || [];
  /* The card the sentence names, not the card it was pinned on: a launcher that sends the bare
     sentence leaves the page to place it, and "... to the target" names the target too. */
  const named = nodes.filter((n) => cardOf(n.card) && cardOf(n.card).name === found[1]).map((n) => n.id);
  const ids = nodeId && named.includes(nodeId) ? [nodeId] : named;
  const upstreamTarget = (id) => {
    const seen = new Set(), todo = [id];
    while (todo.length) {
      const at = todo.shift();
      if (seen.has(at)) continue;
      seen.add(at);
      const node = nodes.find((n) => n.id === at);
      if (node && node.card === "target" && at !== id) return true;
      (FLOW.edges || []).filter((e) => e.to === at).forEach((e) => todo.push(e.from));
    }
    return false;
  };
  return ids.length > 0 && ids.every((id) => (FLOW.edges || []).some((e) => e.from === id && upstreamTarget(e.to)));
}

/* ------------------------------------------------------------------ the flows kept here */
function paintSaved() {
  const pick = $("flowsaved");
  pick.innerHTML = "";
  const first = el("option", null, SAVED.length ? "Saved flows…" : "No saved flows");
  first.value = "";
  pick.appendChild(first);
  SAVED.forEach((f) => {
    const o = el("option", null, f.name);
    o.value = f.name;
    pick.appendChild(o);
  });
  pick.value = "";
  $("flowforget").disabled = !SAVED.some((f) => f.name === FLOW.name);
}

async function loadSaved() {
  try { SAVED = (await api("/api/flow/saved")).flows || []; } catch (failure) { SAVED = []; }
  paintSaved();
}

/* ------------------------------------------------------------------ the flows queued from here */
/* A queued flow runs on the cluster whether this window is open or not, so where each one landed
   is kept in the settings file with the drawing it came from. This is how somebody who signs in
   the next morning gets back to the flow they started: one choice from the list, and the run view
   reads the record the flow's own jobs have been keeping meanwhile. */
function paintQueued() {
  const pick = $("flowruns");
  pick.innerHTML = "";
  const first = el("option", null, QUEUED.length ? "Queued flows…" : "No queued flows");
  first.value = "";
  pick.appendChild(first);
  QUEUED.forEach((r) => {
    const o = el("option", null, (r.name || "a flow") + (r.started ? " · " + when(r.started) : ""));
    o.value = r.folder;
    o.title = r.folder;
    pick.appendChild(o);
  });
  pick.value = "";
  pick.disabled = !QUEUED.length;
}

async function loadQueued() {
  try { QUEUED = (await api("/api/flow/queued")).runs || []; } catch (failure) { QUEUED = []; }
  paintQueued();
}

/* ------------------------------------------------------------------ the stages */
/* Which of the three the view is showing. The editing controls go with the canvas: on the other
   two stages they would act on a drawing nobody is looking at, and Clear on top of a plan a person
   is about to queue would be a trap. The two lists stay, because picking another flow or another
   run is a way out of both sheets. */
function stage(which) {
  STAGE = which;
  if (which === "draw" && FIT_PENDING) {
    requestAnimationFrame(() => {
      if (FIT_PENDING && $("canvasgrid").clientWidth) { FIT_PENDING = false; fitZoom(false); }
    });
  }
  $("flowwrap").hidden = which !== "draw";
  $("flowplan").hidden = which !== "plan";
  $("flowrun").hidden = which !== "run";
  ["namebox", "flowsave", "flowforget", "flowexample", "flowclear"].forEach((id) => {
    $(id).hidden = which !== "draw";
  });
  if (which !== "run") {
    clearTimeout(runTimer);
    runTimer = 0;
  }
}

/* The path with a button that puts it on the clipboard, as a handed-over run has in a tool's tab.
   A page may write to the clipboard only inside a click, and on some machines Chrome refuses it
   outright, so the fallback selects the text for Ctrl+C rather than leaving nothing to copy. */
function pathLine(path) {
  const row = el("div", "pathrow");
  const code = el("code", null, path);
  row.appendChild(code);
  const copy = el("button", "btn sm", "Copy");
  copy.title = "Put this folder on the clipboard";
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(path);
      flash("Copied.");
    } catch (refused) {
      const range = document.createRange();
      range.selectNodeContents(code);
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
      flash("Selected. Press Ctrl+C to copy it.", true);
    }
  };
  row.appendChild(copy);
  return row;
}

/* ------------------------------------------------------------------ the plan */
/* Launch queues nothing. It asks the cluster what the flow would do and shows that, because the
   press commits an allocation that can run for days and until it has been read there is nothing
   to decide on. Queue it, in the bar underneath, is the only thing on this page that queues. */
async function showPlan() {
  FLOW.name = $("flowname").value.trim();
  const mine = ++planSeq;
  PLAN = null;
  stage("plan");
  paintPlan();
  let got;
  try {
    got = await api("/api/flow/plan", { flow: FLOW });
  } catch (failure) {
    if (mine !== planSeq || STAGE !== "plan") return;
    PLAN = { failed: failure.message };
    paintPlan();
    return;
  }
  // A later plan has already been asked for, or Back was pressed while the cluster worked this one
  // out: this answer is about a flow nobody is looking at, and a plan that is about the wrong flow
  // is worse than no plan, because it is the thing Queue it is pressed on the strength of.
  if (mine !== planSeq || STAGE !== "plan") return;
  PLAN = got;
  paintPlan();
}

function planStep(row, n) {
  const li = el("li", "planstep");
  const card = cardOf(row.card);
  const head = el("div", "psthead");
  head.appendChild(el("span", "pstn", String(n)));
  const badge = el("span", "pbadge");
  if (card) badge.appendChild(cardGlyph(card));
  head.appendChild(badge);
  head.appendChild(el("span", "pstname", row.name || row.card));
  head.appendChild(el("span", "pstafter", (row.waits_for || []).length
    ? "after " + row.waits_for.join(" and ")
    : "starts first"));
  li.appendChild(head);
  const facts = el("dl", "pstfacts");
  // The numbers behind the cost are the whole reason for reading this, so they are a row of their
  // own rather than a parenthesis after what the card queues.
  [["Jobs", row.queues], ["Settings", row.scale]].forEach((pair) => {
    if (!pair[1]) return;
    facts.appendChild(el("dt", null, pair[0]));
    facts.appendChild(el("dd", null, pair[1]));
  });
  if (row.lands_in) {
    facts.appendChild(el("dt", null, "Folder"));
    const dd = el("dd");
    dd.appendChild(pathLine(row.lands_in));
    facts.appendChild(dd);
  }
  li.appendChild(facts);
  return li;
}

function planBlock(cls, title, lines) {
  const box = el("div", cls);
  box.appendChild(el("div", "plab", title));
  const list = el("ul", "notelist");
  lines.forEach((line) => list.appendChild(el("li", null, line)));
  box.appendChild(list);
  return box;
}

function paintPlan() {
  const body = $("planbody");
  const why = $("planwhy");
  body.innerHTML = "";
  why.innerHTML = "";
  const wide = el("div", "sheetwide");
  body.appendChild(wide);
  $("planqueue").hidden = true;
  $("planqueue").disabled = false;
  $("planqueue").textContent = "Queue it";

  if (!PLAN) {
    wide.appendChild(el("p", "empty", "Working out the plan on the cluster…"));
    why.appendChild(el("p", "pgood", "Nothing has been queued."));
    return;
  }
  if (PLAN.failed) {
    wide.appendChild(planBlock("planbad", "The plan could not be worked out", [PLAN.failed]));
    why.appendChild(el("p", "runbad", "Nothing has been queued."));
    return;
  }

  const head = el("div", "sheethead");
  head.appendChild(el("h2", null, PLAN.name || "this flow"));
  head.appendChild(el("p", "sheetwhy", "Nothing is queued until you press Queue it."));
  wide.appendChild(head);

  const where = el("div", "planwhere");
  where.appendChild(el("span", "plab", "Folder"));
  where.appendChild(pathLine(PLAN.folder || ""));
  wide.appendChild(where);

  // The notes come before the cards. They are the warnings a person most needs - that two tracks
  // arriving at the simulation double the number of runs, that a length is a long commitment - and
  // under a list of five cards they would be read after the decision rather than before it.
  if ((PLAN.notes || []).length) {
    wide.appendChild(planBlock("planotes", "Before you queue this", PLAN.notes));
  }
  if ((PLAN.problems || []).length) {
    wide.appendChild(planBlock("planbad", "This flow cannot be queued yet", PLAN.problems));
  }

  const steps = el("ol", "plansteps");
  (PLAN.cards || []).forEach((row, i) => steps.appendChild(planStep(row, i + 1)));
  wide.appendChild(steps);

  if ((PLAN.problems || []).length) {
    why.appendChild(el("p", "runbad", "Go back and fix that first."));
    return;
  }
  $("planqueue").hidden = false;
  const next = el("p", "pgood");
  next.appendChild(el("span", "nextlab", "Next"));
  next.appendChild(document.createTextNode("Queue it starts these " + (PLAN.cards || []).length
    + " cards."));
  why.appendChild(next);
}

/* ------------------------------------------------------------------ a flow that is running */
/* Whether anything is still to happen, which is what the refreshing and the two buttons turn on.

   Nothing below a card that failed or was stopped will run: the step jobs are chained with
   afterok, so Slurm never starts the ones under it, and those cards sit at waiting for ever.
   Counting them as still to happen left the page re-reading a flow that could not change, and -
   worse - kept Carry on hidden, which is the one button that would have got the flow moving
   again. So once something has failed, busy means only that something is still in the queue. */
function busy(got) {
  const rows = (got && got.cards) || [];
  // Nothing of it in the queue and cards still unfinished: it has stalled, and saying "running"
  // over a flow that will never move again is the one answer that is certainly wrong.
  if (got && got.stalled) return false;
  if (rows.some((r) => r.state === "failed" || r.state === "stopped")) {
    return rows.some((r) => r.state === "queued" || r.state === "running");
  }
  return rows.some((r) => ENDED.indexOf(r.state) < 0);
}

/* The one word for the whole flow. The record's own `state` is not used for it: that calls a flow
   failed as soon as anything in it did not finish, which is every card of a flow somebody stopped
   on purpose, and a person who pressed Stop should not be told their flow failed. */
function overall(got) {
  const rows = (got && got.cards) || [];
  if (!rows.length) return "waiting";
  if (rows.some((r) => r.state === "failed")) return "failed";
  if (got && got.stalled) return "stalled";
  if (busy(got)) return "running";
  if (rows.every((r) => r.state === "done")) return "done";
  return "stopped";
}

/* How long ago the record was read. app.js's when() is for run folders, where a minute's
   resolution is plenty; here somebody is watching, and "1 min ago" the instant after pressing
   Refresh reads as though nothing had happened. */
function ago(ms) {
  const gap = Math.round((Date.now() - ms) / 1000);
  if (gap < 10) return "just now";
  if (gap < 90) return gap + " seconds ago";
  return when(Math.round(ms / 1000));
}

function watch(folder) {
  const kept = QUEUED.find((r) => r.folder === folder);
  WATCHING = { folder: folder, flow: kept && kept.flow ? asDrawn(kept.flow) : null,
               status: null, read: 0, why: "" };
  PICKED_STAGE = "";
  PICKED_BY_HAND = false;
  PANEL_DRAWN = "";
  stage("run");
  paintRun();
  // The drawing is read out of the flow's own folder whatever this computer remembers, and drawn
  // in place of it when it comes. A flow queued from another machine, or before this one kept
  // drawings, is shown as it was drawn rather than as a list with a gray space beside it; and a
  // flow that has been corrected is shown as it now is. What this computer remembers is the
  // drawing a flow was launched with, so after Save and carry on the run view drew the cards that
  // had been taken out and none of the ones added, beside a list of stages that had them.
  fetch(fileUrl(folder + "/flow.json", "get"))
    .then((r) => (r.ok ? r.json() : null))
    .then((doc) => {
      if (doc && WATCHING && WATCHING.folder === folder) {
        WATCHING.flow = asDrawn(doc);
        paintRun();
      }
    })
    .catch(() => { /* the remembered drawing, or the list alone, as before */ });
  return readRun();
}

async function readRun() {
  if (!WATCHING) return;
  const folder = WATCHING.folder;
  let got = null, failed = "";
  try {
    got = await api("/api/flow/status", { folder: folder });
  } catch (failure) {
    failed = failure.message;
  }
  // Another flow may have been picked while the cluster was answering about this one; that answer
  // belongs to a folder nobody is looking at any more.
  if (!WATCHING || WATCHING.folder !== folder) return;
  // A read that failed says so in the bar but leaves the last answer on the screen. A connection
  // that drops for a minute is the ordinary case, and blanking a flow a person is watching over
  // one missed read would look like the flow had gone.
  if (got) WATCHING.status = got;
  WATCHING.why = failed;
  WATCHING.read = Date.now();
  paintRun();
  later();
}

/* Read it again while anything is still to happen, and not once everything has ended: a finished
   flow does not change again, and a timer left running on one would wake the login node for an
   answer nobody is waiting for. */
function later() {
  clearTimeout(runTimer);
  runTimer = 0;
  if (STAGE !== "run" || !WATCHING || !busy(WATCHING.status)) return;
  runTimer = setTimeout(() => { readRun(); }, RUN_EVERY);
}

/* A card as the list beside the drawing has it: what became of it, why, whose jobs were its own,
   and where its work is. A finished card's folder is the thing a person actually came for, so it
   is offered the way Results hands a run on - the path on the clipboard, and the tool that reads
   it opened on a tab of its own, because the tools cannot yet be opened on a folder. */
/* The drawing beside a running flow is to be read, not edited, so it is fitted to its space
   rather than left to run off behind the card list: a flow four cards wide is wider than that
   space on a laptop, and a card cut off at the edge reads as a card missing. What is fitted is
   the cards themselves, not the canvas they were drawn on: measured from the canvas's corner, the
   empty margin a flow is drawn with counted as drawing, and at 1280 by 720 the last card of a
   five-card chain was cut off even at the smallest scale allowed. Only ever shrunk, never below
   RUN_FIT_MIN, and centred in what is left. Safe here and only here, because nothing on this
   drawing is dragged or wired, so no pointer has to be translated through the scale. */
const RUN_FIT_MIN = 0.45;
function fitRunDrawing() {
  const grid = $("runcanvasgrid"), stage = $("runcanvas");
  if (!grid || !stage || grid.hidden) return;
  stage.style.transform = "";
  stage.style.marginRight = stage.style.marginBottom = "";
  let left = Infinity, top = Infinity, right = 0, bottom = 0;
  stage.querySelectorAll(".node").forEach((n) => {
    left = Math.min(left, n.offsetLeft);
    top = Math.min(top, n.offsetTop);
    right = Math.max(right, n.offsetLeft + n.offsetWidth);
    bottom = Math.max(bottom, n.offsetTop + n.offsetHeight);
  });
  if (!right) return;
  const pad = 18;                       // the sockets stand a few pixels proud of a card's edge
  const wide = right - left + 2 * pad, tall = bottom - top + 2 * pad;
  const room = grid.clientWidth, high = grid.clientHeight;
  // Never larger than the text size the page is drawn at.
  const k = Math.max(RUN_FIT_MIN, Math.min(uiScale(), room / wide, high / tall));
  const dx = Math.max(0, (room - wide * k) / 2), dy = Math.max(0, (high - tall * k) / 2);
  const tx = dx - (left - pad) * k, ty = dy - (top - pad) * k;
  stage.style.transformOrigin = "0 0";
  stage.style.transform = "translate(" + tx.toFixed(1) + "px," + ty.toFixed(1) + "px) scale("
    + k.toFixed(3) + ")";
  // What it is drawn at, for --nd-name: a card's name is the same number of screen pixels here
  // as on the canvas, however far the stage had to shrink to fit the flow in.
  stage.style.setProperty("--flow-eff", k.toFixed(3));
  stage.classList.toggle("overview", k < DRAW_READABLE);
  // A transform moves what is drawn and not the room it takes, so the margins make the scrollable
  // area what is on the screen, as applyZoom does for the canvas: no field of empty scrolling
  // beside a drawing that fits, and room to reach the last card of one that does not.
  stage.style.marginRight = Math.round(dx + wide * k - stage.offsetWidth) + "px";
  stage.style.marginBottom = Math.round(dy + tall * k - stage.offsetHeight) + "px";
  // Too big even so: the stage being shown is kept in view, rather than left off the edge.
  const picked = stage.querySelector(".node.picked");
  if (picked && (wide * k > room + 1 || tall * k > high + 1)) {
    const x = tx + picked.offsetLeft * k, y = ty + picked.offsetTop * k;
    const w = picked.offsetWidth * k, h = picked.offsetHeight * k;
    if (x < grid.scrollLeft || x + w > grid.scrollLeft + room) grid.scrollLeft = Math.max(0, x - (room - w) / 2);
    if (y < grid.scrollTop || y + h > grid.scrollTop + high) grid.scrollTop = Math.max(0, y - (high - h) / 2);
  }
}
addEventListener("resize", () => { if (STAGE === "run") fitRunDrawing(); });

/* What the cluster cannot work out for itself: which flow is being watched, how its cards stand,
   and which card the person pressed. The cluster adds the jobs, the logs and the versions. */
function reportContext(row) {
  const got = (WATCHING && WATCHING.status) || {};
  const drawn = WATCHING && WATCHING.flow;
  const node = drawn && row ? (drawn.nodes || []).find((n) => n.card === row.card) : null;
  return {
    name: got.name || (drawn && drawn.name) || "",
    folder: (WATCHING && WATCHING.folder) || "",
    overall: overall(got),
    // Which card was asked about, so the log quoted is that card's and not whichever failed last;
    // and each card's step job, whose log in the flow's own folder is all a card that failed
    // before its tool ran has to say why.
    about: row ? row.id : "",
    cards: (got.cards || []).map((c) => ({
      id: c.id, card: c.card, state: c.state, jobs: c.jobs || [], note: c.note || "",
      rundir: c.rundir || "", step: c.step || "",
    })),
    settings: node ? node.settings : null,
  };
}

/* Which stage the panel beside the drawing is showing, and what it last drew there. The panel
   reads the stage's folder for its results, which takes a moment and several reads of the
   cluster, so it is drawn again only when the stage chosen, or what that stage is, has changed -
   not on every twenty-second read of the record. */
let PICKED_STAGE = "";
let PICKED_BY_HAND = false;
let PANEL_DRAWN = "";

const STAGE_MARK = { done: "✓", failed: "✕", stopped: "■", running: "●",
                     queued: "●", ready: "○", waiting: "○" };

/* The stage a person most likely wants to see: the one that needs them, else the one working,
   else the last one to finish, else the first. In a flow that has stalled, the one that needs them
   is the first that did not finish - the stage whose turn never came - and not, as it used to be,
   the last that did, which showed a finished stage's pictures under a banner about a stall. */
function defaultStage(rows, stalled) {
  const by = (states) => rows.find((r) => states.indexOf(r.state) >= 0);
  const pick = by(["failed"]) || by(["stopped"])
    || (stalled ? rows.find((r) => r.state !== "done") : null) || by(["running", "queued"])
    || rows.slice().reverse().find((r) => r.state === "done") || rows[0];
  return pick ? pick.id : "";
}

function pickStage(id) {
  PICKED_STAGE = id;
  PICKED_BY_HAND = true;
  PANEL_DRAWN = "";
  paintRun();
}

/* The stage beside the drawing: what it is, where its work went, and what it made, with the
   molecules, pictures and tables shown in place - the same gallery Results has, for one stage. */
function stagePanel(box, row) {
  box.innerHTML = "";
  const card = cardOf(row.card);
  const head = el("div", "rchead stagehead");
  const badge = el("span", "pbadge");
  if (card) badge.appendChild(cardGlyph(card));
  head.appendChild(badge);
  head.appendChild(el("span", "pstname", row.name || row.card));
  head.appendChild(el("span", "sp " + row.state, row.state));
  if (row.changed) {
    const since = Math.max(0, Date.now() / 1000 - row.changed);
    const long = since < 3600 ? Math.max(1, Math.round(since / 60)) + " min"
      : (since / 3600).toFixed(since < 36000 ? 1 : 0) + " h";
    head.appendChild(el("span", "rcwhen", row.state === "running" ? "running for " + long
      : row.state === "queued" ? "queued for " + long : when(row.changed)));
  }
  box.appendChild(head);
  if (row.note) box.appendChild(el("p", "rcnote", row.note));
  const meta = el("div", "rcmeta");
  if ((row.jobs || []).length) {
    meta.appendChild(el("span", "rcjob", (row.jobs.length === 1 ? "job " : "jobs ") + row.jobs.join(" ")));
  }
  if (row.rundir) meta.appendChild(pathLine(row.rundir));
  if (meta.childNodes.length) box.appendChild(meta);

  // A stage of a flow that has stalled, still waiting: the job that was to start it has ended
  // without starting it, and nothing else of the flow is in the queue. "Waits for the one before
  // it" would be untrue - the one before it has finished - and its step job's log is the only
  // account there is of what happened.
  const stuck = !!(WATCHING && WATCHING.status && WATCHING.status.stalled)
    && (row.state === "waiting" || row.state === "ready");
  // What it is doing now, or why it stopped: the end of its log, in place. Shown for a stage at
  // work, and for one that failed, was stopped or is stuck, which is when somebody wants it and
  // least wants to go looking for the file.
  if (["running", "failed", "stopped"].indexOf(row.state) >= 0 || stuck) {
    const logBox = el("div", "stagelog");
    box.appendChild(logBox);
    stageLog(logBox, row);
  }
  if (!row.rundir) {
    box.appendChild(el("p", "phint", stuck
      ? "Its turn did not come: nothing is queued to start it. Carry on queues it again."
      : {
        waiting: "Nothing yet: it waits for the stage before it.",
        ready: "It starts when the stage before it finishes.",
        queued: "Queued: its job is waiting for a node.",
        stopped: "It did not start, so there is nothing to show.",
        failed: "It stopped before it made anything.",
      }[row.state] || "Nothing to show yet."));
    return;
  }
  const gallery = el("div", "stagegallery");
  box.appendChild(gallery);
  // The gallery brings its own Open in, All files and Download the run.
  resultsInto(gallery, { name: row.name || row.card, path: row.rundir,
                         tool: card && card.tool ? card.tool : "" });
}

/* The newest log a stage has left, from its own run folder or from the flow's logs folder, where
   the step job that starts each stage writes - which is where a stage that failed before its tool
   ever ran left its reason. The last forty lines, with how old they are. */
async function stageLog(box, row) {
  box.innerHTML = "";
  const head = el("div", "reshead", row.state === "running" ? "Its log, as it goes" : "The end of its log");
  box.appendChild(head);
  const says = el("p", "why", "Looking for it…");
  box.appendChild(says);
  /* Which log, in order: the tool's own, in the stage's run folder, newest first - that is where
     the work is written up; failing that, the log of the job that started this stage, which the
     record names; failing that, the latest such job by number. Not simply the newest file: a
     stage's folder of step logs also holds wake-ups that found nothing to do, and the newest of
     those says nothing at all. */
  const logs = /\.(out|err|log)$/i;
  const list = async (where) => {
    try { return await api("/api/files/ls", { path: where }); } catch (e) { return null; }
  };
  let best = null;
  const consider = (got, inside) => (got ? got.entries : []).forEach((entry) => {
    if (entry.dir || !logs.test(entry.name)) return;
    if (!best || entry.when > best.when) {
      best = { name: entry.name, label: inside + entry.name, when: entry.when,
               path: got.path.replace(/\/$/, "") + "/" + entry.name };
    }
  });
  if (row.rundir) {
    const got = await list(row.rundir);
    consider(got, "");
    // HOPE-MD keeps its logs in a folder of its own inside the run, one file per job. Read only
    // at the top, a stage of it showed the two lines of the step job that started it -
    // "queued as" - instead of the simulation it had started.
    const sub = (got ? got.entries : []).find((e) => e.dir && /^logs?$/i.test(e.name));
    if (sub) consider(await list(got.path.replace(/\/$/, "") + "/" + sub.name), sub.name + "/");
  }
  if (!best && WATCHING) {
    const got = await list(WATCHING.folder + "/logs");
    const mine = (got ? got.entries : []).filter((e) => !e.dir && logs.test(e.name)
      && e.name.indexOf(row.id + "-") === 0);
    const jobOf = (e) => Number((e.name.slice(row.id.length + 1).match(/^\d+/) || ["0"])[0]);
    const pick = (row.step && mine.find((e) => jobOf(e) === Number(row.step)))
      || mine.sort((a, b) => jobOf(b) - jobOf(a))[0];
    if (pick) best = { name: pick.name, when: pick.when, path: got.path.replace(/\/$/, "") + "/" + pick.name };
  }
  if (!best) { says.textContent = "No log yet."; return; }
  try {
    const text = await (await fetch(fileUrl(best.path, "get"))).text();
    const lines = text.replace(/\s+$/, "").split("\n");
    const pre = el("pre", "logtail", lines.slice(-40).join("\n") || "(empty)");
    box.appendChild(pre);
    pre.scrollTop = pre.scrollHeight;
    says.textContent = (best.label || best.name) + ", written " + when(best.when)
      + (lines.length > 40 ? ", last 40 of " + lines.length + " lines" : "");
    const again = el("button", "btn sm", "Read it again");
    again.type = "button";
    again.onclick = () => stageLog(box, row);
    const get = el("a", "btn sm", "Download");
    get.href = fileUrl(best.path, "get");
    get.setAttribute("download", best.name);
    const acts = el("div", "acts");
    acts.appendChild(again);
    acts.appendChild(get);
    box.appendChild(acts);
  } catch (failure) {
    says.textContent = (best.label || best.name) + " could not be read";
  }
}

function paintRun() {
  const head = $("runhead"), list = $("runcards"), why = $("runwhy");
  head.innerHTML = "";
  why.innerHTML = "";
  if (!WATCHING) return;
  const got = WATCHING.status;
  const rows = (got && got.cards) || [];
  const drawing = WATCHING.flow;
  const state = got ? overall(got) : "";

  // ---- the head: what this is, how far it has got, and every stage in the order they run ----
  // Named after its folder while nothing else is known: "this flow" over a folder that could not
  // be read leaves a person with two flows open not knowing which one has gone wrong.
  const name = (got && got.name) || (drawing && drawing.name)
    || WATCHING.folder.split("/").filter(Boolean).pop() || "this flow";
  const title = el("div", "runtitle");
  title.appendChild(el("h2", null, name));
  if (got) title.appendChild(el("span", "sp " + state, state));
  // A first read that failed has finished, and saying "reading…" beside its error for ever made
  // the page look as though it might yet answer.
  title.appendChild(el("span", "runread", got ? "read " + ago(WATCHING.read)
    : WATCHING.why ? "could not be read" : "reading…"));
  head.appendChild(title);
  if (rows.length) {
    const done = rows.filter((r) => r.state === "done").length;
    const pct = Math.round((100 * done) / rows.length);
    const bar = el("div", "progress");
    const fill = el("div", "progfill" + (state === "failed" || state === "stopped" || state === "stalled" ? " held" : ""));
    fill.style.width = pct + "%";
    bar.appendChild(fill);
    // The stages at work, striped and moving; after them the ones whose jobs are still waiting
    // for a node, in the amber of their word and still, because a job in the queue is not under
    // way and the bar used to say it was.
    let from = pct;
    [["running", "progpart"], ["queued", "progqueued"]].forEach(([which, cls]) => {
      const share = Math.round((100 * rows.filter((r) => r.state === which).length) / rows.length);
      if (!share) return;
      const part = el("div", cls);
      part.style.left = from + "%";
      part.style.width = share + "%";
      bar.appendChild(part);
      from += share;
    });
    const prog = el("div", "progrow");
    prog.appendChild(bar);
    prog.appendChild(el("span", "progtext", done + " of " + rows.length + " stages done · " + pct + "%"));
    head.appendChild(prog);

    // The stage shown follows the flow - to the one working now, to the one that failed - until
    // somebody picks one, and then stays where they put it.
    if (!PICKED_BY_HAND || !rows.some((r) => r.id === PICKED_STAGE)) {
      PICKED_STAGE = defaultStage(rows, !!(got && got.stalled));
    }
    const steps = el("div", "stepper");
    rows.forEach((row, i) => {
      if (i) steps.appendChild(el("span", "stepjoin"));
      const step = el("button", "step " + row.state + (row.id === PICKED_STAGE ? " on" : ""));
      step.type = "button";
      step.title = (row.name || row.card) + ": " + row.state + (row.note ? ". " + row.note : "");
      step.appendChild(el("span", "stepmark", STAGE_MARK[row.state] || "○"));
      step.appendChild(el("span", "stepname", row.name || row.card));
      step.onclick = () => pickStage(row.id);
      steps.appendChild(step);
    });
    head.appendChild(steps);
  }
  const folder = el("div", "runline");
  folder.appendChild(el("span", "plab", "Folder"));
  folder.appendChild(pathLine(WATCHING.folder));
  head.appendChild(folder);

  // ---- the banner: only when the flow needs somebody, or when it has finished ----
  const banner = $("runbanner"), said = $("runbannertext");
  said.innerHTML = "";
  const running = busy(got);
  const failures = rows.filter((r) => r.state === "failed");
  const broke = failures[0] || rows.find((r) => r.state === "stopped");
  const firstUndone = rows.find((r) => r.state !== "done");
  const stalled = !!(got && got.stalled);
  const kept = rows.filter((r) => r.state === "done").map((r) => r.name || r.card);
  const keptLine = kept.length
    ? (kept.length === 1 ? kept[0] + " has" : kept.slice(0, -1).join(", ") + " and " + kept[kept.length - 1] + " have")
      + " finished and " + (kept.length === 1 ? "is" : "are") + " kept."
    : "";
  banner.className = "runbanner";
  banner.hidden = true;
  $("runfix").hidden = $("runcarry").hidden = $("runtell").hidden = true;
  if (got && !running && rows.length && rows.every((r) => r.state === "done")) {
    banner.hidden = false;
    banner.className = "runbanner good";
    said.appendChild(el("b", null, "Every stage has finished. "));
    said.appendChild(document.createTextNode("Results of the whole flow gathers them on one page."));
  } else if (got && (broke || stalled) && (!running || failures.length)) {
    // A card that failed needs somebody even while another track is still going: the word at the
    // top said failed and nothing on the page said what, or offered to report it, until the
    // whole flow had stopped - which can be a day later. Carrying on waits for the queue to
    // empty, so that much is said rather than offered.
    banner.hidden = false;
    const why = el("span", "runbannerwhy");
    if (stalled && !broke) {
      why.appendChild(el("b", null, "This flow has stalled. "));
      why.appendChild(document.createTextNode("Nothing of it is in the queue."));
    } else {
      // "Stopped by hand" is the whole story of a stop somebody pressed, so it is said once, in
      // the bold line, rather than once there and again as the note under it.
      const byHand = broke.state === "stopped" && /stopped by hand/i.test(broke.note || "");
      why.appendChild(el("b", null, (broke.name || broke.card)
        + (broke.state === "failed" ? " failed" : byHand ? " was stopped by hand" : " was stopped")
        + ". "));
      if (broke.note && !byHand) {
        const note = broke.note.replace(/\.?$/, ".");
        why.appendChild(document.createTextNode(note.charAt(0).toUpperCase() + note.slice(1)));
        // A note can be a traceback four lines long. The banner keeps three lines of it and the
        // stage beside the drawing has the whole, so the drawing is not pushed off the screen.
        why.title = broke.note;
      }
      // The banner names the first failure; a second one was only a cross on the stepper.
      const others = failures.filter((r) => r !== broke).map((r) => r.name || r.card);
      if (others.length) {
        why.appendChild(document.createTextNode(" " + others.join(", ") + " failed as well."));
      }
    }
    said.appendChild(why);
    if (running) {
      const still = rows.filter((r) => r.state === "running" || r.state === "queued")
        .map((r) => r.name || r.card);
      said.appendChild(document.createTextNode(still.join(" and ") + (still.length === 1
        ? " is still going; Carry on comes once it ends." : " are still going; Carry on comes "
        + "once they end.")));
    } else {
      if (keptLine) said.appendChild(document.createTextNode(keptLine));
      $("runfix").hidden = false;
      $("runcarry").hidden = false;
      $("runcarry").textContent = firstUndone
        ? "Carry on from " + (firstUndone.name || firstUndone.card) : "Carry on";
    }
    // Reporting is for what went wrong: a failure, or a stall, which is the machinery's fault
    // and never the person's. A stop somebody pressed is neither.
    $("runtell").hidden = !(failures.length || (stalled && !broke));
  }
  $("runfix").disabled = $("runcarry").disabled = false;

  // ---- the drawing, which can be clicked to choose a stage ----
  $("runcanvasgrid").hidden = !drawing;
  if (drawing) {
    // Drawn at full size and only then shrunk: the wires are placed by measuring the sockets, and
    // measured through a scale already applied they come out shrunk twice, as marks in a corner.
    $("runcanvas").style.transform = "";
    $("runcanvas").style.marginRight = $("runcanvas").style.marginBottom = "";
    drawFlow($("runcanvas"), $("runwires"), drawing, rows);
    $("runcanvas").querySelectorAll(".node").forEach((n) => {
      n.classList.toggle("picked", n.dataset.node === PICKED_STAGE);
      n.style.cursor = "pointer";
      n.onclick = () => pickStage(n.dataset.node);
    });
    fitRunDrawing();
  }

  // ---- the stage beside it ----
  const row = rows.find((r) => r.id === PICKED_STAGE);
  // What the panel shows of a stage depends on the stage and on whether the flow has stalled
  // round it; with no stage, on whether the record could be read at all - left out of this, a
  // read that failed went on saying "Reading the flow's record…" under its own error.
  const key = row ? [row.id, row.state, row.rundir, (row.jobs || []).join(","),
                     stalled ? "stalled" : ""].join("|")
    : "none" + (WATCHING.why ? "|unread" : "");
  if (key !== PANEL_DRAWN) {
    PANEL_DRAWN = key;
    if (row) stagePanel(list, row);
    else {
      list.innerHTML = "";
      list.appendChild(el("p", "empty", WATCHING.why ? "The flow's record could not be read."
        : "Reading the flow's record…"));
    }
  }

  if (WATCHING.why) {
    why.appendChild(el("p", "runbad", WATCHING.why));
  } else if (got) {
    why.appendChild(el("p", "pgood", running
      ? "Updated every " + Math.round(RUN_EVERY / 1000) + " seconds. It runs whether or not "
        + "this window is open."
      : "Nothing more will happen on its own."));
  }
  // A flow's results are in as many folders as it has stages, so the page that gathers them is
  // offered the moment there is anything in it rather than only at the end.
  $("runreport").hidden = !got || !rows.some((r) => r.state === "done");
  $("runstop").hidden = !running;
  $("runstop").disabled = false;
}

/* ------------------------------------------------------------------ the view */
/* What a launcher built before 0.6.1 drops from every setting as it takes the cluster's cards:
   the bounds under a number, the step of its arrows, the gray words in an empty box, the line
   against each choice, and the rules that change one field with another's answer. This page is
   fetched from the cluster at every sign-in and the launcher is not, so the page puts them back
   itself from the same cards, kept as a file in the install (hope_flow/catalogue.json). Nothing
   happens on a launcher that kept them, and nothing is said if the file cannot be read: the
   fields then work as before, without their bounds. */
const DETAILS = ["least", "most", "step", "placeholder", "notes", "varies"];
async function fillDetails(cat) {
  const mine = (cat.cards || []).flatMap((c) => c.settings || []);
  if (mine.some((f) => DETAILS.some((k) => k in f))) return 0;
  const labs = labsFolder();
  if (!labs) return 0;
  let full = null;
  try { full = await api("/api/files/get?path=" + encodeURIComponent(labs + "/hope_flow/catalogue.json")); } catch (e) { return 0; }
  const theirs = new Map((full.cards || []).map((c) => [c.key, new Map((c.settings || []).map((f) => [f.key, f]))]));
  let put = 0;
  (cat.cards || []).forEach((card) => {
    const fields = theirs.get(card.key);
    if (!fields) return;
    (card.settings || []).forEach((f) => {
      const there = fields.get(f.key);
      if (!there || there.kind !== f.kind) return;
      DETAILS.forEach((k) => { if (!(k in f) && k in there) { f[k] = there[k]; put += 1; } });
    });
  });
  return put;
}

/* ---- the two side panes, as wide as the person wants them ----
   [least, usual, most] in pixels. The drawing between them keeps 320 at least, so neither pane
   can be dragged over it. The widths are kept in this browser, and a double-click on an edge
   puts that pane back to its usual width. */
const PANES = { palw: [140, 200, 440], panw: [220, 288, 680] };
function paneWidth(key) {
  const got = parseFloat(getComputedStyle($("flowwrap")).getPropertyValue("--" + key));
  return Number.isFinite(got) ? got : PANES[key][1];
}
function setPane(key, px) {
  const wrap = $("flowwrap");
  const [least, , most] = PANES[key];
  const room = wrap.getBoundingClientRect().width - paneWidth(key === "palw" ? "panw" : "palw") - 320;
  const wide = Math.round(Math.max(least, Math.min(most, room, px)));
  wrap.style.setProperty("--" + key, wide + "px");
  try { localStorage.setItem("hl-" + key, String(wide)); } catch (e) { /* a private window */ }
}
function armPanes() {
  const wrap = $("flowwrap");
  if (!wrap || wrap.querySelector(".panedrag")) return;
  try {
    Object.keys(PANES).forEach((key) => {
      const kept = parseFloat(localStorage.getItem("hl-" + key));
      if (Number.isFinite(kept)) wrap.style.setProperty("--" + key, kept + "px");
    });
  } catch (e) { /* nothing kept */ }
  [["l", "palw", "the cards"], ["r", "panw", "the settings"]].forEach(([side, key, what]) => {
    const edge = el("div", "panedrag " + side);
    edge.tabIndex = 0;
    edge.setAttribute("role", "separator");
    edge.setAttribute("aria-orientation", "vertical");
    edge.setAttribute("aria-label", "Width of " + what);
    edge.title = "Drag to make " + what + " wider or narrower; double-click for the usual width";
    const by = (ev) => {
      const box = wrap.getBoundingClientRect();
      setPane(key, side === "l" ? ev.clientX - box.left : box.right - ev.clientX);
    };
    edge.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      edge.setPointerCapture(ev.pointerId);
      edge.classList.add("on");
      document.body.classList.add("dragpane");
      const done = () => {
        edge.classList.remove("on");
        document.body.classList.remove("dragpane");
        edge.removeEventListener("pointermove", by);
        edge.removeEventListener("pointerup", done);
        edge.removeEventListener("pointercancel", done);
      };
      edge.addEventListener("pointermove", by);
      edge.addEventListener("pointerup", done);
      edge.addEventListener("pointercancel", done);
    });
    edge.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      ev.preventDefault();
      const grow = (ev.key === "ArrowRight") === (side === "l") ? 16 : -16;
      setPane(key, paneWidth(key) + grow);
    });
    edge.addEventListener("dblclick", () => {
      wrap.style.removeProperty("--" + key);
      try { localStorage.removeItem("hl-" + key); } catch (e) { /* nothing kept */ }
    });
    wrap.appendChild(edge);
  });
}

async function openFlows() {
  armPanes();
  if (!CAT) {
    try {
      CAT = await api("/api/flow/cards");
    } catch (failure) {
      flash(failure.message, true);
      return;
    }
    await fillDetails(CAT);
    paintPalette();
  }
  await loadSaved();
  await loadQueued();
  // Coming back to the view leaves it on the stage it was left on: somebody who went to Results to
  // look something up has not stopped watching their flow.
  if (STAGE === "plan") return paintPlan();
  if (STAGE === "run") {
    paintRun();
    return readRun();
  }
  paint();
  await check();
}
window.openFlows = openFlows;

// Tools that have no page of their own are listed on the Tools screen and run from here: their
// tile hands the card over rather than offering a Launch that could only fail.
window.addFlowCard = function (key) {
  openFlows();
  const id = addCard(key);
  /* Said only where the card has a target socket it will not start without: a design card's
     takes the target, and a target linked to it is what it needs. A fold, a preparation and a
     simulation have none, and a flow that aims at no site needs no target card at all. */
  if (id) {
    const card = cardOf(key) || {};
    const wants = (card.inputs || []).some((p) => p.required
      && ((p.kinds || []).indexOf("target") >= 0 || (p.kinds || []).indexOf("receptor") >= 0));
    flash("Added " + card.name + " to the flow."
          + (wants ? " Link a target to it." : " Link what it works on into it."));
  }
  return id;
};

$("flowname").addEventListener("input", () => { FLOW.name = $("flowname").value.trim(); touch(); });

$("flowexample").onclick = async () => {
  try {
    const example = await api("/api/flow/example");
    stopCorrecting();
    openFlow(example);
    flash("An example: a receptor and the peptide that binds it folded together, prepared,"
          + " then simulated in HOPE-MD.");
  } catch (failure) { flash(failure.message, true); }
};

$("flowclear").onclick = () => {
  stopCorrecting();
  FLOW = blankFlow();
  $("flowname").value = "";
  PICKED = "";
  CHECKED = false;
  paint();
  check();
};

$("flowsave").onclick = async () => {
  FLOW.name = $("flowname").value.trim();
  if (!FLOW.name) {
    flash("Name the flow first.", true);
    $("flowname").focus();
    return;
  }
  try {
    SAVED = (await api("/api/flow/save", { flow: FLOW })).flows || [];
    paintSaved();
    flash("Saved as " + FLOW.name + " on this computer.");
  } catch (failure) { flash(failure.message, true); }
};

$("flowforget").onclick = async () => {
  const name = FLOW.name;
  try {
    SAVED = (await api("/api/flow/delete", { name: name })).flows || [];
    paintSaved();
    flash(name + " forgotten. The canvas is unchanged.");
  } catch (failure) { flash(failure.message, true); }
};

$("flowsaved").onchange = (e) => {
  const chosen = SAVED.find((f) => f.name === e.target.value);
  if (chosen) {
    stopCorrecting();
    openFlow(chosen);
  }
  e.target.value = "";
};

$("flowruns").onchange = (e) => {
  const folder = e.target.value;
  e.target.value = "";
  if (folder) watch(folder);
};

/* The drawer is put away the way every layer over a page is: a click beside it, or Escape. */
if ($("paneback")) $("paneback").onclick = closePanel;
addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented) return;
  // Not while a dialog of its own is open: that one has first claim on Escape.
  if (document.querySelector("dialog[open], .sheetback")) return;
  if ($("panel") && !$("panel").classList.contains("empty")) closePanel();
});

/* Launch reads the plan out of the cluster and shows it. Queue it is the press that commits. */
$("zoomin").onclick = () => setZoom(ZOOM * 1.2);
$("zoomout").onclick = () => setZoom(ZOOM / 1.2);
$("zoomreset").onclick = () => setZoom(1);
$("zoomfit").onclick = () => fitZoom(true);
$("canvasgrid").addEventListener("wheel", (e) => {
  // Ctrl with the wheel, or a pinch on a touchpad, which browsers report the same way. A plain
  // wheel is left to scroll, which is what it has always done here.
  if (!e.ctrlKey) return;
  e.preventDefault();
  setZoom(ZOOM * Math.exp(-e.deltaY / 400), { x: e.clientX, y: e.clientY });
}, { passive: false });

$("flowlaunch").onclick = async () => {
  if (!EDITING) { showPlan(); return; }
  const button = $("flowlaunch");
  button.disabled = true;
  const was = button.textContent;
  button.textContent = "saving…";
  try {
    const folder = EDITING.folder;
    await api("/api/flow/edit", { folder: folder, flow: FLOW });
    const went = await api("/api/flow/resume", { folder: folder });
    EDITING = null;
    flash(carriedOn(went) ? "Saved. " + carriedOn(went)
      : "Saved. Carrying on from the first unfinished card.");
    await watch(folder);
  } catch (failure) {
    flash(failure.message, true);
    // A flow the cluster says would not start is put on the list, with a Fix that goes to the box,
    // rather than left in a message that fades: the canvas had no way to see it for itself.
    const said = /^that flow would not start: ([\s\S]+)$/.exec(failure.message || "");
    if (said && EDITING) {
      REFUSED = [placeOf(said[1].trim())];
      paintProblems();
      goToIssue(REFUSED[0]);
    }
  }
  button.textContent = was;
  button.disabled = false;
};

$("planback").onclick = () => {
  PLAN = null;
  stage("draw");
  paint();
  check();
};

$("planqueue").onclick = async () => {
  const button = $("planqueue");
  button.disabled = true;
  button.textContent = "queueing…";
  try {
    const got = await api("/api/flow/launch", { flow: FLOW });
    await loadQueued();
    flash((PLAN && PLAN.name ? PLAN.name : "The flow") + " is queued. It runs whether or not "
          + "this window is open.");
    PLAN = null;
    await watch(got.folder);
    return;
  } catch (failure) { flash(failure.message, true); }
  button.disabled = false;
  button.textContent = "Queue it";
};

$("runback").onclick = () => {
  WATCHING = null;
  stage("draw");
  paint();
  check();
};

$("runreload").onclick = () => { readRun(); };

$("runcarry").onclick = async () => {
  if (!WATCHING) return;
  const button = $("runcarry");
  button.disabled = true;
  button.textContent = "queueing…";
  try {
    const went = await api("/api/flow/resume", { folder: WATCHING.folder });
    flash(carriedOn(went) || "Carrying on from the first unfinished card. Finished cards are not "
          + "run again.");
  } catch (failure) { flash(failure.message, true); }
  button.textContent = "Carry on";
  await readRun();
};

/* What to say when the runner found nothing to queue: every card left has run, or is already
   waiting its turn. The page said it was carrying on all the same - after the cards still to run
   had been taken out of a flow, "carrying on from the first card that has not finished" was said
   of a flow that had finished. The runner's own words are matched, and a test holds the two
   together. */
function carriedOn(went) {
  return /nothing to carry on/.test((went && went.resumed) || "")
    ? "Nothing to queue: every card left has run, or is waiting its turn."
    : "";
}

/* Correcting a flow that stopped. The drawing is read back from the flow's own folder rather than
   from what this computer remembers, so a flow queued from another machine can be corrected here
   too. Which cards have already run comes from the record beside it, and those are shown on the
   canvas rather than offered: their runs are on disk and are not made again. */
$("runfix").onclick = async () => {
  if (!WATCHING) return;
  const button = $("runfix");
  button.disabled = true;
  const was = button.textContent;
  button.textContent = "reading it…";
  try {
    const answer = await fetch(fileUrl(WATCHING.folder + "/flow.json", "get"));
    if (!answer.ok) throw new Error("the flow's own drawing could not be read back");
    const doc = JSON.parse(await answer.text());
    const rows = (WATCHING.status && WATCHING.status.cards) || [];
    const stopped = rows.find((r) => r.state === "failed") || rows.find((r) => r.state === "stopped");
    EDITING = {
      folder: WATCHING.folder,
      done: rows.filter((r) => r.state === "done").map((r) => r.id),
      why: stopped && stopped.note
        ? { card: stopped.name || stopped.card, node: stopped.id, note: stopped.note } : null,
      seen: false,
    };
    FIXING = null;
    openFlow(doc);
    stage("draw");
    paintPanel();
    // Straight to the first thing, once the check has said what the things are.
    await check();
    const todo = fixList();
    if (todo.length) goToIssue(todo[0]);
  } catch (failure) {
    flash(failure.message, true);
  }
  button.textContent = was;
  button.disabled = false;
};

/* The flow's own page. The runner writes it when a flow ends, and writes it again on any status
   of one that has - so a flow still going has an older one, and asking refreshes it first. */
$("runreport").onclick = async () => {
  if (!WATCHING) return;
  const button = $("runreport");
  const was = button.textContent;
  button.disabled = true;
  button.textContent = "gathering…";
  try {
    await api("/api/flow/report", { folder: WATCHING.folder });
    await openAsPage(WATCHING.folder + "/report.html", "report.html");
  } catch (failure) {
    flash(failure.message, true);
  }
  button.textContent = was;
  button.disabled = false;
};

$("runtell").onclick = () => {
  const got = (WATCHING && WATCHING.status) || {};
  const rows = got.cards || [];
  // The card the banner is about: the first that failed, or in a stall the first whose turn did
  // not come. It used to fall back to the first card of all, which in a stall is one that finished.
  const asked = rows.find((r) => r.state === "failed") || rows.find((r) => r.state === "stopped")
    || (got.stalled ? rows.find((r) => r.state !== "done") : null) || rows[0];
  openReport("failure", reportContext(asked));
};

$("runstop").onclick = async () => {
  if (!WATCHING) return;
  const button = $("runstop");
  button.disabled = true;
  try {
    await api("/api/flow/stop", { folder: WATCHING.folder });
    flash("Stopped. Finished cards are kept; Carry on picks up the rest.");
  } catch (failure) { flash(failure.message, true); }
  await readRun();
};

/* The empty canvas's two ways in: a target to start from, or the example to look at. */
if ($("emptytarget")) $("emptytarget").onclick = () => { if (CAT) addCard("target"); };
if ($("emptyexample")) $("emptyexample").onclick = () => $("flowexample").click();

/* a card dragged off the palette lands where it was dropped */
$("canvasgrid").addEventListener("dragover", (e) => { e.preventDefault(); });
$("canvasgrid").addEventListener("drop", (e) => {
  e.preventDefault();
  const key = e.dataTransfer.getData("text/plain");
  if (!cardOf(key)) return;
  const at = pointIn(e);
  addCard(key, [Math.max(0, Math.round(at.x - 100)), Math.max(0, Math.round(at.y - 26))]);
});
