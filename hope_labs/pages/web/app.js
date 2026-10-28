/* The design page, for whichever of the design tools the server was started for.
   Two screens: Design, which reads a target and queues a run, and Runs, which reads the run
   folders and the queue.

   The form is the steps a person takes, in order: the target, the site, the binder, and what the
   model is told. The rail beside it holds the resources, where the run goes, and Submit, with
   the run plan directly above the button - and nothing that is already on the form. Every
   reason, caveat and sentence is behind the ? beside the thing it is about, or in the docs.

   Nothing about either tool is written here. The settings are built from the card the server
   sends, which is the same card the canvas draws, so a setting added to the card appears on this
   page without a line changing; and every refusal comes from the server, which refuses what a
   flow refuses, in the same words. */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

/* The settings that are about the machine rather than about the science. They go in the rail
   under Resources, because somebody looking for the card and the walltime should not have to
   open More settings to find half of them. */
const RESOURCE = new Set(["gpu", "gpus", "walltime", "partition", "account"]);

/* RFD4-Proteina's three conditions. They are settings of its card like any other, and they are
   drawn as the switches of their own card below, so they are left out of the settings groups:
   a setting offered twice is two places to look and two answers to reconcile. */
const TOLDKEYS = ["hold_target", "reveal_sequence", "use_hotspots"];

let HELLO = null;
let FIELDS = [];
/* The server's last answer to /api/check, so the guidance can be painted again when something
   changes on the page that the server has no opinion about - the PDB code box opening, a switch
   moving - without asking it the same question again. */
let LAST = null;
/* Whether the PDB code box is open. The card keeps "Choose a structure" either way; this is only
   so the Next bar names the press that is actually in front of somebody. */
let CODING = false;

async function api(path, body) {
  const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const answer = await fetch(url, init);
  let data = {};
  try { data = await answer.json(); } catch (e) { data = { error: "the server sent something that is not JSON (" + answer.status + ")" }; }
  if (!answer.ok && !data.fields && !data.problems) throw new Error(data.error || "the server answered " + answer.status);
  return data;
}

let flashTimer = 0;
function flash(text, bad) {
  clearTimeout(flashTimer);
  document.querySelectorAll(".flash").forEach((n) => n.remove());
  const box = el("div", "flash" + (bad ? " bad" : ""), text);
  document.body.appendChild(box);
  flashTimer = setTimeout(() => box.remove(), bad ? 15000 : 8000);
}

/* ------------------------------------------------------------------ theme */
const theme = $("theme");
function isDark() {
  return document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches);
}
function paintTheme() { theme.textContent = isDark() ? "Light" : "Dark"; }
/* The key the documentation reads as well, so /docs opens in the theme the page is in. */
try { if (localStorage.hldesigntheme) document.documentElement.dataset.theme = localStorage.hldesigntheme; } catch (e) { /* private window */ }
theme.onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.hldesigntheme = document.documentElement.dataset.theme; } catch (e) { /* ignore */ }
  paintTheme();
  /* The viewer's background is a color it was made with, so it follows the page rather than
     staying the light one behind a dark page. */
  if (VIEW) buildViewer();
};
paintTheme();

/* ------------------------------------------------------------------ screens */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("on", t === tab);
      t.setAttribute("aria-selected", t === tab ? "true" : "false");
    });
    const want = tab.dataset.screen;
    $("screen-setup").hidden = want !== "setup";
    $("screen-runs").hidden = want !== "runs";
    if (want === "runs") loadRuns();
  };
});

/* ------------------------------------------------------------------ the rail */
/* hh:mm:ss, or d-hh:mm:ss past a day, which is what Slurm takes. */
function asWalltime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const days = Math.floor(s / 86400);
  const pad = (n) => String(n).padStart(2, "0");
  const clock = pad(Math.floor((s % 86400) / 3600)) + ":" + pad(Math.floor((s % 3600) / 60))
    + ":" + pad(s % 60);
  return days ? days + "-" + clock : clock;
}

function walltimeSeconds(text) {
  const m = /^(?:(\d+)-)?(\d+):([0-5]\d):([0-5]\d)$/.exec(String(text || "").trim());
  if (!m) return NaN;
  return (+(m[1] || 0)) * 86400 + (+m[2]) * 3600 + (+m[3]) * 60 + (+m[4]);
}

/* Two arrows on a box: the browser's own are a few pixels of target clipped against the border,
   and on a touchpad they are unhittable. Out of the tab order, because a number input already
   has up and down from the keyboard. */
function spinArrows(onStep, upSays, downSays) {
  const box = el("div", "arrows");
  [["▲", +1, upSays], ["▼", -1, downSays]].forEach(([glyph, by, says]) => {
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

/* ------------------------------------------------------------------ the reason, behind a ?
   Every setting on these cards explains itself in whole sentences, which is right: the
   sentences are why anybody can use the tools at all. Under every box, though, they made a
   rail nobody reads - a person looking for the number of designs read four paragraphs to find
   it. So the sentence moves here, opened by the ? beside the name, and the rail is the
   choices again. */
const HELP = {
  target: ["The target",
           "The structure the binder is designed against. Choose a structure opens the browser, "
           + "which reaches this computer, your scratch, your home and the other tools' run "
           + "folders; a PDB code is fetched here and copied into the run folder.",
           "Its chains and the sequence of each are read off the file. Press a chain to leave it "
           + "out of the target: a chain left out is still drawn, and the site can be read off it.",
           "An mmCIF is written as a PDB first, because that is the form these tools read, and "
           + "the chains are then read off that copy. Where it cannot be written as one the page "
           + "says so rather than queueing a run that would find out."],
  site: ["The site",
         "The residues the binder has to reach. Press a residue on the chain above to mark it, "
         + "or take them from a partner this structure is already bound to.",
         "Every design tool on these pages is aimed at a site and will not start without one. A "
         + "design run given no site is a search of the whole surface, which is a far larger "
         + "campaign than anybody means by leaving a box alone, so it has to be asked for "
         + "deliberately rather than reached by forgetting."],
  hotspots: ["Typing the site",
             "54,56,66-70 for one chain, or A54,B12-16 across chains. The numbers are the ones "
             + "this structure carries, not a count from the first residue.",
             "The box and the chain above are the same site: type into one and the other follows, "
             + "because a site copied out of a paper should still work."],
  told: ["What the model is told",
         "RFD4-Proteina is not given a contig of what to keep and left to infer the rest. It is "
         + "told, condition by condition, what has to be in the right place: the target's shape, "
         + "its sequence, and the residues a binder must reach.",
         "Each switch is one condition in the spec the job reads. Turn one off and it is not "
         + "written, which is a different question asked of the model rather than a worse answer. "
         + "Show the job, under the run plan, is where the spec itself can be read."],
  /* The plan's own ?: the machine the step names in words, which is where the cores, the memory
     and the walltime went when the plan was cut back to what a newcomer can read. */
  plan: ["The run plan",
         "What will happen, in the order it happens, with roughly how long each part takes and "
         + "where the results land.",
         "The time against the design step is the walltime the job is given, which is a ceiling "
         + "rather than an estimate: nothing here can say how long a backbone takes on the card "
         + "it lands on. A job that finishes early releases the rest.",
         "Every job asks for one GPU, the cores and the memory this tool's own table says it "
         + "needs, and the queue works out which machine that is. Show the job, under the plan, "
         + "has the exact script and inputs."],
  binder: ["How long a binder",
           "A length is drawn from this range for each design, so a narrow range gives designs "
           + "that can be compared with one another.",
           "Longer is not better: a binder has to fold into what it is designed as, and the "
           + "longer it is the more ways it has of folding into something else."],
};

/* A card's `why` is written to follow its label on one line - "the model loads in the first few
   minutes of it" - so opened as the body of a dialog titled with that label it began mid-sentence
   in lower case. Given its own paragraph it is given a capital and a full stop, which is all that
   was wrong with it. A line that already starts with a capital, a number or a code is left alone. */
function sentence(line) {
  const text = String(line || "").trim();
  if (!text) return "";
  const head = /^[a-z]/.test(text) ? text[0].toUpperCase() + text.slice(1) : text;
  return /[.!?:]$/.test(head) ? head : head + ".";
}

/* THE FINDING, WITHOUT THE REASON. The server writes each note about a target as a short
   finding, then a colon or a full stop, then why it matters - "that file is an mmCIF: the target
   card has written it as a PDB, ...". The card shows the finding and keeps the reason behind the
   ?, because a form carries no paragraph. Where even the finding runs long it is cut again at a
   comma, and only then at twelve words, so nothing on the card is a sentence to wade through. */
function noteHead(line) {
  const text = String(line || "").trim();
  if (!text) return "";
  const words = (s) => s.split(/\s+/).filter(Boolean).length;
  let head = text;
  for (const cut of [": ", ". ", ", "]) {
    const at = head.indexOf(cut);
    if (at > 0 && (words(head.slice(0, at)) <= 12 || words(head) > 12)) head = head.slice(0, at);
    if (words(head) <= 12) break;
  }
  if (words(head) > 12) head = head.split(/\s+/).slice(0, 12).join(" ") + "\u2026";
  /* A cut inside a bracket leaves it hanging open - "...more than once (21" - so it goes too. */
  if ((head.match(/\(/g) || []).length > (head.match(/\)/g) || []).length) {
    head = head.slice(0, head.lastIndexOf("(")).trim();
  }
  return sentence(head);
}

function openHelp(title, lines) {
  $("helptitle").textContent = title;
  const body = $("helpbody");
  body.innerHTML = "";
  lines.filter(Boolean).forEach((line) => body.appendChild(el("p", null, line)));
  $("helpdocs").href = HELPDOCS[CURRENT_HELP] || "/docs/index.html";
  if (!$("helpdlg").open) $("helpdlg").showModal();
  $("helpclose").focus();
}

/* The page of the tool's own documentation that says more about this card, so More in the docs
   lands on the right page rather than on the front of the manual. */
const HELPDOCS = { target: "/docs/target.html", site: "/docs/target.html",
                   hotspots: "/docs/target.html", binder: "/docs/binder.html",
                   told: "/docs/binder.html", plan: "/docs/resources.html" };
let CURRENT_HELP = "";

function help(key, extra) {
  const got = HELP[key] || [key, extra || ""];
  CURRENT_HELP = key;
  openHelp(got[0], got.slice(1).concat(extra ? [extra] : []));
}
function closeHelp() { if ($("helpdlg").open) $("helpdlg").close(); }
$("helpclose").onclick = closeHelp;
$("helpx").onclick = closeHelp;
document.querySelectorAll("[data-help]").forEach((b) => {
  b.onclick = () => help(b.dataset.help);
});

/* A ? that carries one setting's own sentence, which is the card's `why`. */
function whyButton(f) {
  const q = el("button", "q", "?");
  q.type = "button";
  q.setAttribute("aria-label", "Why: " + f.label);
  q.onclick = (e) => {
    e.preventDefault();
    CURRENT_HELP = "";
    openHelp(f.label, [sentence(f.why)]);
    const body = $("helpbody");
    const notes = Object.entries(f.notes || {});
    if (notes.length) {
      const list = el("dl", "helpnotes");
      notes.forEach(([what, says]) => {
        list.appendChild(el("dt", null, TILE_NAMES[what] ? TILE_NAMES[what] + " (" + what + ")" : what));
        list.appendChild(el("dd", null, sentence(says)));
      });
      body.appendChild(list);
    }
    if (f.least != null && f.most != null) {
      body.appendChild(el("p", "why", "Between " + f.least + " and " + f.most
        + (f.default != null && f.default !== "" ? "; the tool's own is " + f.default : "") + "."));
    }
  };
  return q;
}

/* A choice of three or four, each with a line on it, is a row of tiles rather than a menu: the
   words on them are the choice being made, and a menu hides two of the three at the moment
   somebody is comparing them. */
function optionTiles(f) {
  const box = el("div", "opts");
  const hidden = el("input");
  hidden.type = "hidden";
  hidden.id = f.key;
  hidden.value = f.default == null ? "" : String(f.default);
  box.appendChild(hidden);
  f.choices.forEach((choice) => {
    const note = (f.notes || {})[choice] || "";
    const tile = el("button", "opt" + (String(choice) === hidden.value ? " on" : ""));
    tile.type = "button";
    /* The choice's own value is the heading where it reads as words, and the lab's own short name
       where the value is a path like "medium/front_loaded" that means nothing on a tile. */
    tile.appendChild(el("b", null, TILE_NAMES[choice] || (choice === "" ? "The tool's own" : choice)));
    /* The description is the tile's tooltip and the card's ?, not a paragraph on the tile: four
       tiles each carrying a sentence is the wall of words this card was rebuilt to lose. */
    if (note) tile.title = note;
    tile.onclick = () => {
      hidden.value = String(choice);
      box.querySelectorAll(".opt").forEach((o) => o.classList.toggle("on", o === tile));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    };
    box.appendChild(tile);
  });
  return box;
}

/* Values whose own name says nothing on a tile. RFD4-Proteina's recycling is "low/front_loaded":
   a schedule name and a shape, correct in the spec and meaningless as a label, so the tile carries
   the effort it amounts to and the card's own note says what it costs. */
const TILE_NAMES = {
  "low/front_loaded": "Quick", "medium/front_loaded": "Standard", "high/front_loaded": "Thorough",
};

function fieldNode(f) {
  /* One setting, drawn the way its kind says. A yes or no is a tick; a number and a walltime are
     boxes with arrows, because the card says what they may be and stepping between those is
     quicker and safer than typing; a choice of a few with notes is a row of tiles and any other
     choice is a menu of exactly the card's choices. The reason is always behind the ?.

     None of these is in monospace. A walltime, an account and a partition are things a person
     types, and typewriter letters in a box are what the pages were rejected for; monospace here
     is for a sequence, a residue list or a script, which is what the site box and the job dialog
     carry. */
  /* A menu of more than four is .menu, whose label sits over its box at the left of the row:
     laid out as a plain wide field, "What to design" put its label at one end of a 1500px row
     and its menu at the other. A number whose empty box says what the tool would do instead -
     "every one that passes" - is .roomy, because in the narrow column that came out as
     "every one tha". */
  const tiled = f.kind === "choice" && f.choices.length <= 4
    && Object.keys(f.notes || {}).length > 0;
  const long = String(f.placeholder || "").length > 9;
  const wrap = el("div", "fld " + (f.kind === "yesno" ? "wide"
    : tiled ? "opts" : f.kind === "choice" ? "menu"
    : f.kind === "number" || f.kind === "duration" ? (long ? "num roomy" : "num") : "wide"));
  if (f.kind === "yesno") {
    const tick = el("label", "tick");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = f.key;
    input.checked = !!f.default;
    tick.appendChild(input);
    tick.appendChild(el("span", null, f.label));
    if (f.why) tick.appendChild(whyButton(f));
    wrap.appendChild(tick);
    return wrap;
  }
  const label = el("label", "f");
  label.htmlFor = f.key;
  label.appendChild(el("span", "fname", f.label));
  if (f.why || f.notes) label.appendChild(whyButton(f));
  wrap.appendChild(label);
  if (tiled) {
    wrap.appendChild(optionTiles(f));
  } else if (f.kind === "choice") {
    const select = el("select", "inp");
    select.id = f.key;
    f.choices.forEach((choice) => {
      /* An empty choice is "keep the tool's own", which is what the card means by it. */
      const note = (f.notes || {})[choice];
      const one = new Option(choice === "" ? "The tool's own" : choice, choice);
      if (note) one.title = note;
      select.appendChild(one);
    });
    select.value = f.default == null ? "" : String(f.default);
    wrap.appendChild(select);
  } else if (f.kind === "number" || f.kind === "duration") {
    const clock = f.kind === "duration";
    const spin = el("div", "spin");
    const input = el("input", "inp");
    input.id = f.key;
    input.autocomplete = "off";
    if (clock) {
      input.type = "text";
      input.placeholder = f.placeholder || "12:00:00";
    } else {
      input.type = "number";
      if (f.least != null) input.min = String(f.least);
      if (f.most != null) input.max = String(f.most);
      if (f.step != null) input.step = String(f.step);
      input.placeholder = f.placeholder || (f.optional ? "optional" : "");
    }
    if (f.default != null && f.default !== "") input.value = String(f.default);
    const by = Number(f.step) || (clock ? 1800 : 1);
    const step = (way) => {
      const now = clock ? walltimeSeconds(input.value) : Number(input.value);
      const blank = input.value === "" || !Number.isFinite(now);
      const from = blank ? (f.least != null ? Number(f.least) : (clock ? 3600 : 0)) : now;
      let want = from + way * by;
      if (f.least != null) want = Math.max(Number(f.least), want);
      if (f.most != null) want = Math.min(Number(f.most), want);
      input.value = clock ? asWalltime(want) : String(Math.round(want * 1e6) / 1e6);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    spin.appendChild(input);
    spin.appendChild(spinArrows(step, clock ? "half an hour more" : "more",
                                clock ? "half an hour less" : "less"));
    wrap.appendChild(spin);
    if (!clock && f.least != null && f.most != null) {
      wrap.appendChild(el("span", "why bounds", f.least + " to " + f.most));
    }
  } else {
    const input = el("input", "inp");
    input.id = f.key;
    input.autocomplete = "off";
    if (f.default != null && f.default !== "") input.value = String(f.default);
    else input.placeholder = f.placeholder || (f.optional ? "optional" : "");
    wrap.appendChild(input);
  }
  const err = el("p", "errmsg");
  err.id = f.key + "-err";
  err.hidden = true;
  wrap.appendChild(err);
  return wrap;
}

function buildFields() {
  /* Where each setting goes. The science of the run is on the form, beside the target it is
     about; the machine is in the rail under Resources; the rest is behind More settings. The
     three conditions are left out of all three, because they are the switches of their own card.

     The groups are built from the card the server sends, so a setting added to the card appears
     here without a line being written - and appears once. */
  const groups = { "fields-main": [], "fields-res": [], "fields-res-more": [], "fields-more": [] };
  FIELDS.forEach((f) => {
    if (TOLDKEYS.includes(f.key)) return;
    /* The card and the walltime are what somebody looking for the machine came for; the queue
       and the account are settings they will need once a year. Both are resources, so both used
       to be in the rail unconditionally, and the first four things in the rail were "Card",
       "Walltime", "Partition", "Account to charge" - two of them empty boxes nobody new has an
       answer for. The card's own `advanced` already says which is which, so it decides here too. */
    if (RESOURCE.has(f.key)) groups[f.advanced ? "fields-res-more" : "fields-res"].push(f);
    else if (f.advanced) groups["fields-more"].push(f);
    else groups["fields-main"].push(f);
  });
  Object.entries(groups).forEach(([where, list]) => {
    const box = $(where);
    box.innerHTML = "";
    list.forEach((f) => box.appendChild(fieldNode(f)));
  });
  /* The count is the number alone, as a badge. "More settings 7 more" and "Queue and account
     2 more" read as labels of five and six words, which is over the four a label is allowed;
     what the number means is obvious from where it sits. */
  $("morecount").textContent = String(groups["fields-more"].length);
  $("morecount").title = groups["fields-more"].length + " more settings, with their reasons";
  $("moreopts").hidden = !groups["fields-more"].length;
  $("morerescount").textContent = String(groups["fields-res-more"].length);
  $("morerescount").title = groups["fields-res-more"].length + " more settings, with their reasons";
  $("moreres").hidden = !groups["fields-res-more"].length;
  /* Every box is watched, including the ones behind More settings: a check costs one request and
     the alternative is Submit going live for a form nobody has looked at since. */
  FIELDS.concat([{ key: "hotspots" }, { key: "binder_min" }, { key: "binder_max" },
                 { key: "name" }, { key: "out" }]).forEach((f) => {
    const input = $(f.key);
    if (!input) return;
    const kind = input.tagName === "SELECT" || input.type === "checkbox" ? "change" : "input";
    input.addEventListener(kind, () => { clearTimeout(typing); typing = setTimeout(check, 280); });
  });
}

/* ------------------------------------------------------------------ the target
   `also` is the chains the server read out of the file that this tool's own reader cannot see: a
   nucleic acid strand, a ligand in a chain of its own. They are not chains to design against, and
   they are chains a site can be read off, so they are offered on the partner button and nowhere
   else.

   `cutoff` is how close two heavy atoms have to be to count as touching. It comes with the target
   rather than being written here: the figure is hope_labs/structure.py's, every report here
   counts a contact at the same one, and the server already sends it back with every site
   it reads. Written out in this file it was a third copy of a measured number. */
const T = { path: "", name: "", pdb_id: "", chains: [], picked: [], also: [], cutoff: 4.5 };

function clearTarget() {
  T.path = ""; T.name = ""; T.pdb_id = ""; T.chains = []; T.picked = []; T.also = [];
  $("targetout").hidden = true;
  $("tnotes").hidden = true;
  $("tnotes").innerHTML = "";
  $("tseq").hidden = true;
  $("pickzone").hidden = false;
  $("codepane").hidden = true;
  targetError("");
  SITE.clear();
  drawRuler();
  forgetViewer();
  check();
}

/* ------------------------------------------------------------------ the site
   Marked residues as "<chain><number>", the way every one of these tools names a hotspot, held in
   one set that the ruler, the chips, the viewer and the box all read. One place, because the four
   of them disagreeing about what is marked is the bug this card is here to prevent. */
const SITE = new Set();

function siteText() {
  /* What goes in the box. Bare numbers while one chain is kept, because that is what a person
     types and what a paper gives; chain-prefixed the moment there are two, because then a bare
     number does not say which chain it means. */
  const ids = [...SITE];
  const bare = T.picked.length < 2;
  const key = (one) => [one.slice(0, 1), Number(one.slice(1))];
  ids.sort((a, b) => {
    const [ca, na] = key(a), [cb, nb] = key(b);
    return ca === cb ? na - nb : (ca < cb ? -1 : 1);
  });
  return ids.map((one) => (bare ? one.slice(1) : one)).join(",");
}

function readSiteBox() {
  /* The box back into the set, so a site pasted from a paper marks the chain. The same grammar
     the job's own helper reads: "54,56,66-70" on the first kept chain, "A54,B12-16" across
     chains. Anything the structure has not got is left in the box for the server to refuse by
     name rather than silently dropped here. */
  const first = T.picked[0] || (T.chains[0] || {}).id || "A";
  const want = new Set();
  String($("hotspots").value || "").split(/[,\s]+/).forEach((bit) => {
    if (!bit) return;
    const m = /^([A-Za-z]?)(\d+)(?:-(\d+))?$/.exec(bit.trim());
    if (!m) return;
    const chain = m[1] || first;
    const lo = Number(m[2]);
    const hi = m[3] ? Number(m[3]) : lo;
    for (let n = lo; n <= hi && n - lo < 400; n += 1) want.add(chain + n);
  });
  SITE.clear();
  want.forEach((one) => SITE.add(one));
}

function paintSite(says) {
  $("hotspots").value = siteText();
  const chips = $("hotchips");
  chips.innerHTML = "";
  const named = [...SITE];
  named.sort();
  named.forEach((one) => {
    const chip = el("span", "hotchip");
    chip.appendChild(el("span", null, one + (RESNAME[one] ? " " + RESNAME[one] : "")));
    const x = el("button", null, "×");
    x.type = "button";
    x.setAttribute("aria-label", "Unmark " + one);
    x.onclick = () => { SITE.delete(one); paintSite(); markRuler(); markViewer(); check(); };
    chip.appendChild(x);
    chips.appendChild(chip);
  });
  $("clearsite").hidden = !SITE.size;
  const box = $("sitesays");
  if (says != null) {
    box.textContent = says;
    box.className = "sitesays good";
  } else if (SITE.size) {
    box.textContent = SITE.size + (SITE.size === 1 ? " residue marked" : " residues marked");
    box.className = "sitesays";
  } else if (T.path) {
    box.textContent = "nothing marked: the binder may grow anywhere on the target";
    box.className = "sitesays";
  } else {
    box.textContent = "";
  }
}

/* Three-letter names, for the chips: a site of seven numbers is hard to argue about and
   "A54 Leu" is not. Filled in as the ruler is drawn, from what the server read off the file. */
const RESNAME = {};
const LONG = { A: "Ala", R: "Arg", N: "Asn", D: "Asp", C: "Cys", Q: "Gln", E: "Glu", G: "Gly",
               H: "His", I: "Ile", L: "Leu", K: "Lys", M: "Met", F: "Phe", P: "Pro", S: "Ser",
               T: "Thr", W: "Trp", Y: "Tyr", V: "Val" };

function drawRuler() {
  const box = $("siteruler");
  box.innerHTML = "";
  Object.keys(RESNAME).forEach((k) => delete RESNAME[k]);
  const kept = T.chains.filter((c) => T.picked.includes(c.id));
  if (!T.path || !kept.length) {
    /* One muted line, not an empty bar waiting to be filled: a box nobody can explain at a
       glance has no business on the page. */
    $("sitehint").textContent = "Choose a structure and its chains are drawn here.";
    return;
  }
  $("sitehint").textContent = "Press the residues the binder has to reach.";
  kept.forEach((chain) => {
    const residues = chain.residues || [];
    const wrap = el("p", "ruler");
    if (kept.length > 1 || T.chains.length > 1) {
      wrap.appendChild(el("span", "ch", "chain " + chain.id + " · " + chain.n
        + " residues · " + chain.first + " to " + chain.last));
    }
    /* A residue number this chain has already drawn. 21, 21A and 21B are three residues and one
       number, and a hotspot is a number with nowhere to put the code: drawn as three buttons they
       all carried data-id A21, pressing any of them marked all three, and the chip came back
       naming the last of them. So the first residue of a number is the button and the rest are
       drawn and not offered, which keeps the chain readable - thrombin's heavy chain has
       twenty-eight of them - without offering a press that aims somewhere else. */
    const drawn = new Set();
    residues.forEach(([number, letter], i) => {
      const id = chain.id + number;
      const again = drawn.has(id);
      drawn.add(id);
      /* "?" is the server saying its own reader has no residue at this number: a residue the
         tool's reader counts and this page's does not. The number is still the residue's own, so
         it is still pressable, and the note above the ruler says what happened. */
      if (LONG[letter] && !again) RESNAME[id] = LONG[letter];
      if (i % 10 === 0) {
        const group = el("span", "ten");
        group.appendChild(el("i", null, String(number)));
        wrap.appendChild(group);
      }
      const group = wrap.lastChild;
      if (again) {
        const spare = el("span", "r shared", letter);
        spare.title = "chain " + chain.id + " " + (LONG[letter] || "residue") + " " + number
          + " again, under an insertion code. A hotspot is a number, so this one cannot be marked "
          + "on its own: pressing " + number + " marks the first residue of that number.";
        group.appendChild(spare);
        return;
      }
      const one = el("button", "r", letter);
      one.type = "button";
      one.dataset.id = id;
      one.title = LONG[letter]
        ? "chain " + chain.id + " " + LONG[letter] + " " + number
        : "chain " + chain.id + " residue " + number + ", which this page's own reader has no "
          + "residue for: the number is the one a hotspot is written in";
      one.setAttribute("aria-label", one.title);
      one.setAttribute("aria-pressed", "false");
      one.onclick = () => {
        if (SITE.has(id)) SITE.delete(id); else SITE.add(id);
        paintSite();
        markRuler();
        markViewer();
        check();
      };
      group.appendChild(one);
    });
    box.appendChild(wrap);
  });
  markRuler();
}

/* Only the buttons: a residue drawn under an insertion code shares its number with the one above
   it and is not the residue a hotspot of that number aims at, so it is not lit up as if it were. */
function markRuler() {
  document.querySelectorAll("#siteruler button.r").forEach((one) => {
    const on = SITE.has(one.dataset.id);
    one.classList.toggle("hot", on);
    one.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

/* The residues a partner is already sitting on, shaded behind whatever is marked: it is the
   answer most people want and seeing it is what makes the button worth pressing. */
function shadeInterface(found) {
  document.querySelectorAll("#siteruler button.r").forEach((one) => {
    one.classList.toggle("near", !!found[one.dataset.id]);
  });
}

$("clearsite").onclick = () => {
  SITE.clear();
  paintSite();
  markRuler();
  markViewer();
  check();
};

/* The site read off the structure. Two questions, one request: the residues the partner left out
   is sitting on, and - for a structure of three chains, where there may be no partner left out at
   all - the residues where the chains touch each other. Marked rather than replacing what is
   marked, so a site can be built up from both and from the ruler. */
async function takeSite(button, every) {
  button.disabled = true;
  try {
    const got = await api("/api/interface",
                          { path: T.path, chains: T.picked.join(","), every: !!every });
    const shade = {};
    Object.entries(got.hotspots || {}).forEach(([chain, numbers]) => {
      numbers.forEach((n) => { SITE.add(chain + n); shade[chain + n] = true; });
    });
    paintSite(got.said);
    markRuler();
    shadeInterface(shade);
    markViewer();
    check();
  } catch (failure) {
    flash(failure.message, true);
  } finally {
    button.disabled = false;
  }
}

$("takeiface").onclick = () => takeSite($("takeiface"), false);
$("takeany").onclick = () => takeSite($("takeany"), true);

/* The box and the chain are one site: typing in one marks the other. */
$("hotspots").addEventListener("input", () => {
  readSiteBox();
  markRuler();
  markViewer();
  const chips = $("hotchips");
  chips.innerHTML = "";
  [...SITE].sort().forEach((one) => {
    const chip = el("span", "hotchip");
    chip.appendChild(el("span", null, one + (RESNAME[one] ? " " + RESNAME[one] : "")));
    chips.appendChild(chip);
  });
  $("clearsite").hidden = !SITE.size;
});

function targetError(text) {
  $("terr").textContent = text || "";
  $("terr").hidden = !text;
}

/* THE SEQUENCE, READ OFF THE STRUCTURE. The letters come with the target, from the server's own
   reader (hope_labs/structure.py), so the card says what the model will be given rather than
   making somebody open the file to find out. The start of each kept chain is shown and the whole
   of it is the line's tooltip: a card is not the place for nine hundred letters. */
function paintSequence() {
  const box = $("tseq");
  box.innerHTML = "";
  const kept = T.chains.filter((c) => T.picked.includes(c.id) && (c.residues || []).length);
  if (!kept.length) { box.hidden = true; return; }
  kept.forEach((c) => {
    const letters = (c.residues || []).map((r) => r[1]).join("");
    const line = el("div");
    line.appendChild(el("span", "ix", c.id + " "));
    line.appendChild(el("span", null, letters.length > 70 ? letters.slice(0, 70) + "…" : letters));
    line.title = "Chain " + c.id + ", " + letters.length + " residues: " + letters;
    box.appendChild(line);
  });
  box.hidden = false;
}

function paintTarget(info) {
  T.path = info.path || "";
  T.name = info.name || "";
  T.pdb_id = info.pdb_id || "";
  T.chains = info.chains || [];
  T.also = info.also || [];
  if (info.cutoff) T.cutoff = info.cutoff;
  /* Every chain to begin with: a target is usually given as the chains to bind, and a receptor
     that is a dimer is bound as a dimer. */
  T.picked = T.chains.map((c) => c.id);
  $("tname").textContent = info.name || "target";
  $("tsays").textContent = info.residues + " residues in " + T.chains.length
    + (T.chains.length === 1 ? " chain" : " chains");
  $("tpath").textContent = info.path.replace(/^.*\/(?=[^/]*$)/, "");
  $("tpath").title = info.path;
  /* What the server read that this tool's reader could not: a converted mmCIF, a chain of DNA, a
     residue it has no name for. Said here because the alternative is a page that draws less than
     the file holds and does not mention it. */
  const notes = $("tnotes");
  const said = info.notes || [];
  notes.innerHTML = "";
  if (said.length) {
    /* One short line, with the whole of it behind the ?: these are paragraphs about a converted
       file or a chain this tool's own reader cannot see, and a paragraph on the form is what
       the page was rebuilt to lose. */
    const line = el("p", "note");
    /* The finding itself, not a count of findings: "One thing worth knowing about this file"
       is a line a person cannot act on or even explain, and the whole of each note is a press
       away. Every note is written as a short finding, then a colon or a full stop, then the
       reason - so the finding is what is said here. */
    line.appendChild(document.createTextNode(noteHead(said[0])
      + (said.length > 1 ? " \u00b7 " + (said.length - 1) + " more" : "")));
    const q = el("button", "q", "?");
    q.type = "button";
    q.setAttribute("aria-label", "What this page read in the file");
    q.onclick = () => {
      CURRENT_HELP = "target";
      openHelp("What this page read", said.map(sentence));
    };
    line.appendChild(q);
    notes.appendChild(line);
  }
  notes.hidden = !said.length;
  const chips = $("chainchips");
  chips.innerHTML = "";
  T.chains.forEach((c) => {
    /* A chip per chain: the letter, how many residues it holds and where its numbering runs.
       Pressing one leaves that chain out of the target; it is still drawn, and the site can
       still be read off it. */
    const chip = el("button", "pchain");
    chip.type = "button";
    chip.setAttribute("aria-pressed", "true");
    chip.appendChild(el("b", null, c.id));
    chip.appendChild(el("span", "pk", c.n + " res · " + c.first + "-" + c.last));
    chip.title = "Chain " + c.id + ", residues " + c.first + " to " + c.last
      + ". Hotspots are written in these numbers. Press to leave it out of the target.";
    chip.onclick = () => {
      const i = T.picked.indexOf(c.id);
      if (i >= 0 && T.picked.length > 1) T.picked.splice(i, 1);
      else if (i < 0) T.picked.push(c.id);
      chip.setAttribute("aria-pressed", T.picked.includes(c.id) ? "true" : "false");
      /* A chain dropped from the target takes its marked residues with it, and the ruler, the
         viewer and the partner button all follow: a site on a chain that is no longer being
         designed against is a site aimed at nothing. */
      [...SITE].forEach((one) => {
        if (!T.picked.includes(one.slice(0, 1))) SITE.delete(one);
      });
      drawRuler();
      paintSite();
      paintSequence();
      paintTakeInterface();
      paintViewer();
      check();
    };
    chips.appendChild(chip);
  });
  /* What the file holds that this tool does not design against - a nucleic acid strand, a ligand
     in a chain of its own - named with its kind, because somebody looking at a target wants to
     know what else is in there. Not pressable: it is not a chain to bind. */
  T.also.forEach((c) => {
    const chip = el("span", "pchain");
    chip.setAttribute("aria-disabled", "true");
    chip.appendChild(el("b", null, c.id));
    chip.appendChild(el("span", "pk", c.n + " " + c.kind));
    chip.title = "Chain " + c.id + " holds " + c.n + " " + c.kind
      + (c.n === 1 ? " residue, which this tool" : " residues, which this tool")
      + " does not design against. The site can still be taken from it.";
    chips.appendChild(chip);
  });
  paintSequence();
  if (!$("name").value) $("name").placeholder = info.name || "named for the target";
  /* The way in is put away once something has been chosen: the card is then the summary of what
     was chosen, with Change to pick again. */
  $("pickzone").hidden = true;
  $("codepane").hidden = true;
  $("targetout").hidden = false;
  targetError("");
  /* A new target means a new site: the numbers of the last one mean something else here, and
     carrying them over would aim at whatever residue happened to share a number. */
  SITE.clear();
  drawRuler();
  paintSite();
  paintTakeInterface();
  showStructure();
  check();
}

/* The two buttons that read a site off the structure. The first is offered when a chain has been
   left out, because that chain is where the target is already bound; the chains this tool's own
   reader cannot see are among them, since a contact is measured on atoms and a DNA strand or a
   ligand sitting in the site is as good a partner as a peptide. The second is offered as soon as
   there are two chains at all: a trimer kept whole has no partner left out and its chains still
   touch each other, which is the site a binder has to compete with. */
function paintTakeInterface() {
  const others = T.chains.filter((c) => !T.picked.includes(c.id)).map((c) => c.id)
    .concat(T.also.map((c) => c.id));
  const near = T.cutoff.toFixed(1) + " A";
  const button = $("takeiface");
  button.hidden = !others.length;
  if (others.length) {
    button.textContent = "Site from chain " + others.join(", ");
    button.title = "Mark every residue of the kept chains within " + near + " of chain "
      + others.join(", ") + ", which is where it is already bound";
  }
  const every = $("takeany");
  const count = T.chains.length + T.also.length;
  every.hidden = count < 2 || !T.picked.length;
  if (!every.hidden) {
    every.textContent = "Site from all chains";
    every.title = "Mark every residue of chain " + T.picked.join(", ") + " within " + near
      + " of any other chain in this structure, the ones you are designing against included";
  }
  $("vpartnerleg").hidden = !others.length;
}

/* ------------------------------------------------------------------ the viewer on the form
   The site can be marked here as well as on the ruler, and for a face somebody is looking at that
   is the only way: counting along a sequence to find the residue you can see is the step where the
   wrong number gets typed. 3Dmol is the copy this checkout vendors, because the page is reached
   over a forwarded port and a viewer from a content delivery network would be an empty box. */
let VIEW = null;
let SPIN = false;
let DRAW = "cartoon";
/* The structure as it was read, kept so the viewer can be built again without fetching it a
   second time. See buildViewer for why it is built again rather than resized. */
let VIEWTEXT = "";

function forgetViewer() {
  VIEW = null;
  VIEWTEXT = "";
  $("vwrap").hidden = true;
  $("viewer").innerHTML = "";
}

async function showStructure() {
  if (typeof $3Dmol === "undefined" || !T.path) return;
  let text = "";
  try {
    const answer = await fetch("/api/structure?t=" + encodeURIComponent(TOKEN)
      + "&path=" + encodeURIComponent(T.path));
    if (!answer.ok) throw new Error("the structure could not be read");
    text = await answer.text();
  } catch (failure) {
    forgetViewer();
    return;                       // the ruler is the way in either way; no viewer is not an error
  }
  VIEWTEXT = text;
  buildViewer();
}

/* FILLING THE PANEL. zoomTo() fits the model's bounding SPHERE, and a protein is not a sphere:
   pdl1_5o45 measures 52 by 36 by 60 angstroms, whose enclosing sphere is 87 across, so the view
   was set for 87 and the structure drawn into 36 of it - 43% of the panel's height, a squiggle a
   hundred pixels across in a panel nine hundred wide. Pressing it marked nothing, because there
   was so little of it under the pointer. It is not the waters and it is not the text size:
   taking every water out of the model and fitting again gives a picture identical to the pixel,
   and so does naming the chains in zoomTo. A compact protein - MDM2, which is nearly spherical -
   fills the panel from the same call, which is why this went unseen.

   So the fit measures itself and corrects. Every atom is projected onto the panel, the tallest
   and widest the structure comes out is compared with the panel, and the view is magnified by
   whatever brings it to four fifths of the height without passing nine tenths of the width.
   A structure that already fills is magnified by about one and left alone; nothing is ever
   shrunk, because zoomTo has already guaranteed that it fits. */
const FILL_H = 0.8;
const FILL_W = 0.9;
const FILL_MOST = 3;

function fillPanel() {
  if (!VIEW) return;
  let box;
  try {
    const atoms = VIEW.selectedAtoms({});
    if (!atoms.length) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    atoms.forEach((a) => {
      const p = VIEW.modelToScreen({ x: a.x, y: a.y, z: a.z });
      if (p.x < x0) x0 = p.x;
      if (p.x > x1) x1 = p.x;
      if (p.y < y0) y0 = p.y;
      if (p.y > y1) y1 = p.y;
    });
    box = { w: x1 - x0, h: y1 - y0 };
  } catch (e) {
    return;                      // a build of 3Dmol without modelToScreen: the fit stands as is
  }
  const panel = $("viewer").getBoundingClientRect();
  if (!(box.w > 1 && box.h > 1 && panel.height > 1)) return;
  const by = Math.min(FILL_H * panel.height / box.h, FILL_W * panel.width / box.w, FILL_MOST);
  if (by > 1.05) {
    VIEW.zoom(by);
    VIEW.render();
  }
}

/* THE VIEWER, BUILT RATHER THAN RESIZED. 3Dmol takes the panel's size when the viewer is made
   and keeps it, and resize() does not put a canvas and its camera back in step. The panel's size
   is changed by the text-size control, so on a step the viewer is made again in the panel at its
   new size - the one path known to draw the structure at the size of the panel, because it is
   the path that runs on first load. Nothing is lost that is not lost by Reset: the chains drawn,
   the chains left out and every marked residue are read back out of the page's own state. */
function buildViewer() {
  if (typeof $3Dmol === "undefined" || !VIEWTEXT) return;
  $("vwrap").hidden = false;
  const box = $("viewer");
  box.innerHTML = "";
  VIEW = $3Dmol.createViewer(box, { backgroundColor: isDark() ? 0x16292e : 0xe9f0f0 });
  VIEW.addModel(VIEWTEXT, "pdb");
  paintViewer();
  if ($("vsurface").classList.contains("on")) {
    VIEW.addSurface($3Dmol.SurfaceType.VDW, { opacity: 0.68, color: "0xb9c9ce" },
                    { chain: T.picked });
  }
  /* Pressing an atom marks its residue, which is the whole reason the viewer is on this card. */
  VIEW.setClickable({}, true, (atom) => {
    if (!atom) return;
    const id = (atom.chain || "A") + atom.resi;
    if (!T.picked.includes(atom.chain || "A")) {
      flash("That residue is on chain " + (atom.chain || "A") + ", which is not one of the chains "
        + "you are designing against.", true);
      return;
    }
    if (SITE.has(id)) SITE.delete(id); else SITE.add(id);
    paintSite();
    markRuler();
    markViewer();
    check();
  });
  /* Registered BEFORE the last render, not after it: 3Dmol builds the geometry a press is
     tested against while it renders, so a handler added after the final render was tested
     against nothing and a press on the structure marked nothing at all. The folding page has
     always done it in this order. */
  VIEW.zoomTo();
  VIEW.render();
  fillPanel();
  if (SPIN) VIEW.spin("y");
}

function paintViewer() {
  if (!VIEW) return;
  const kept = T.picked;
  VIEW.setStyle({}, {});
  /* The chains left out are drawn thin and gray-green: they are not the target, and a person
     should see what they left out rather than wonder where it went. The chains this tool's own
     reader cannot see are among them, because a DNA strand or a ligand sitting in the site is
     exactly what somebody is looking for when they wonder what the site is already holding. */
  VIEW.setStyle({ chain: T.chains.map((c) => c.id).concat(T.also.map((c) => c.id))
                   .filter((c) => !kept.includes(c)) },
                { cartoon: { color: "0x86a98f", opacity: 0.45, thickness: 0.2 } });
  const style = DRAW === "sticks" ? { cartoon: { color: "0x9fb7c8", opacity: 0.5 } }
                                  : { cartoon: { color: "0x9fb7c8" } };
  VIEW.setStyle({ chain: kept }, style);
  markViewer();
}

function markViewer() {
  if (!VIEW) return;
  const by = {};
  [...SITE].forEach((one) => {
    const chain = one.slice(0, 1);
    by[chain] = by[chain] || [];
    by[chain].push(Number(one.slice(1)));
  });
  VIEW.removeAllShapes();
  Object.entries(by).forEach(([chain, numbers]) => {
    VIEW.addStyle({ chain: chain, resi: numbers },
                  { stick: { color: "0xc2603a", radius: 0.28 } });
    VIEW.addStyle({ chain: chain, resi: numbers, atom: "CA" },
                  { sphere: { color: "0xc2603a", radius: 1.1 } });
  });
  VIEW.render();
}

$("vcartoon").onclick = () => {
  DRAW = "cartoon";
  $("vcartoon").classList.add("on");
  $("vsticks").classList.remove("on");
  paintViewer();
};
$("vsticks").onclick = () => {
  DRAW = "sticks";
  $("vsticks").classList.add("on");
  $("vcartoon").classList.remove("on");
  paintViewer();
};
$("vsurface").onclick = () => {
  if (!VIEW) return;
  const on = $("vsurface").classList.toggle("on");
  VIEW.removeAllSurfaces();
  if (on) {
    VIEW.addSurface($3Dmol.SurfaceType.VDW, { opacity: 0.68, color: "0xb9c9ce" },
                    { chain: T.picked });
  }
  VIEW.render();
};
$("vspin").onclick = () => {
  if (!VIEW) return;
  SPIN = !SPIN;
  $("vspin").classList.toggle("on", SPIN);
  VIEW.spin(SPIN ? "y" : false);
};
$("vreset").onclick = () => {
  if (!VIEW) return;
  VIEW.zoomTo();
  VIEW.render();
  fillPanel();
};

/* The target, read and drawn. This one throws, because the structure browser shows the sentence a
   refusal failed with and stays open on the folder somebody had walked to. */
async function takeTarget(body) {
  const info = await api("/api/target", body);
  paintTarget(info);
  return info;
}

/* The same, for the buttons on the card itself, where a refusal is said under them. */
async function readTarget(body, button) {
  targetError("");
  if (button) { button.disabled = true; button.dataset.was = button.textContent; button.textContent = "Reading…"; }
  try {
    await takeTarget(body);
  } catch (failure) {
    clearTarget();
    targetError(failure.message);
  } finally {
    if (button) { button.disabled = false; button.textContent = button.dataset.was; }
  }
}

function sendFile(file) {
  if (!file) return Promise.reject(new Error("no file was given"));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => takeTarget({ kind: "file", filename: file.name,
                                       data: String(reader.result).split(",")[1] || "" })
      .then(resolve, reject);
    reader.onerror = () => reject(new Error("that file could not be read from your computer"));
    reader.readAsDataURL(file);
  });
}

/* ONE WAY IN: the structure browser (/static/picker.js), the same dialog on every page of ours,
   which reaches this computer, your scratch, your home, this tool's runs and the other tools'
   runs, and fetches a PDB code. Five buttons in a row is what this card was rejected for. The
   dialog does the choosing; what a choice means is here. */
function chooseStructure(startWith) {
  if (!window.HLPicker) { $("file").click(); return; }
  HLPicker.open({
    title: "Choose a structure", key: "design-structure", api: api,
    places: "/api/places", browse: "/api/browse?want=structure",
    accept: ".pdb,.ent,.cif,.mmcif", kinds: [".pdb", ".ent", ".cif", ".mmcif"],
    acceptSays: "A .pdb or .cif file.",
    onFile: (file) => sendFile(file),
    onPath: (path) => takeTarget({ kind: "path", path: path }),
    onCode: (code) => takeTarget({ kind: "pdb", id: code }),
    /* A run folder keeps its structure a level or two down, so the dialog offers the one it found
       rather than making somebody walk to inputs/. */
    onRun: (folder, run) => takeTarget({ kind: "path", path: (run && run.structure) || folder }),
    startWith: startWith,
  });
}

/* The PDB code box, which is also a place inside the browser: it is on the card as well because
   a four-character code is the one input nobody should have to open a dialog for. */
function showCode(on) {
  $("codepane").hidden = !on;
  /* The way in stays where it is. Pressing PDB code used to put the whole pick zone away, so
     "Choose a structure" left the card while the Next bar still said to press it; the code box
     opens under the buttons instead, and Cancel puts it away again. */
  CODING = on;
  if (on) $("pdbid").focus();
  paintGuide(LAST);
}

$("choose").onclick = () => chooseStructure();
$("codebtn").onclick = () => { targetError(""); showCode(true); };
$("codecancel").onclick = () => { targetError(""); showCode(false); };
$("changetarget").onclick = () => clearTarget();
$("fetch").onclick = () => {
  const code = $("pdbid").value.trim();
  if (!/^[A-Za-z0-9]{4}$/.test(code)) {
    targetError("A PDB code is four characters, like 1YCR.");
    return;
  }
  readTarget({ kind: "pdb", id: code }, $("fetch"));
};
$("pdbid").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("fetch").click(); } });
$("pdbid").addEventListener("input", () => { $("pdbid").value = $("pdbid").value.toUpperCase(); });
$("file").onchange = () => {
  const file = $("file").files[0];
  $("file").value = "";
  if (file) sendFile(file).catch((why) => targetError(why.message));
};

/* A file dropped on the card is a file chosen on This computer. */
const zone = $("step-target");
["dragenter", "dragover"].forEach((k) => zone.addEventListener(k, (e) => {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
  e.preventDefault();
  $("pickzone").classList.add("over");
}));
["dragleave", "drop"].forEach((k) => zone.addEventListener(k, (e) => {
  if (k === "dragleave" && zone.contains(e.relatedTarget)) return;
  $("pickzone").classList.remove("over");
}));
zone.addEventListener("drop", (e) => {
  if (!e.dataTransfer || !e.dataTransfer.files.length) return;
  e.preventDefault();
  sendFile(e.dataTransfer.files[0]).catch((why) => targetError(why.message));
});

/* Where the run goes, chosen in the same browser rather than typed from memory. */
$("outbrowse").onclick = () => {
  if (!window.HLPicker) return;
  HLPicker.open({
    title: "Choose a run folder", key: "design-folder", api: api, folders: true,
    useLabel: "Put the run here", places: "/api/places", browse: "/api/browse?dirs=1",
    mkdir: "/api/mkdir",
    onFolder: (path) => { $("out").value = path; check(); return Promise.resolve(); },
  });
};

/* ------------------------------------------------------------------ the form */
function form() {
  const body = { path: T.path, pdb_id: T.pdb_id, chains: T.picked.join(","),
                 hotspots: $("hotspots").value, binder_min: $("binder_min").value,
                 binder_max: $("binder_max").value, name: $("name").value, out: $("out").value };
  FIELDS.forEach((f) => {
    const input = $(f.key);
    if (!input) return;
    body[f.key] = input.type === "checkbox" ? input.checked : input.value;
  });
  /* The conditions, where this tool has them. Sent as they are switched rather than left out when
     they are on, so the job's record says what it was told rather than what the page's defaults
     were on the day. */
  Object.keys(TOLD).forEach((key) => {
    const sw = $("sw_" + key);
    if (sw) body[key] = sw.getAttribute("aria-pressed") === "true";
  });
  return body;
}

/* ------------------------------------------------------------------ what the model is told
   RFD4-Proteina is not handed a contig and left to infer the rest: it is told, condition by
   condition, what has to be in the right place. Three conditions are three switches - and they
   are NOT a card of their own any more. A card titled "What the model is told", carrying three
   switches each followed by a paragraph and a monospace key, was the one box on this page that
   was still the wall of words the pages were rejected for, and it read as a preview of the job
   while being three settings. So each switch now sits on the card it is about - two on the
   target, the third on the site - as one line: a switch, a name of at most four words, and a ?
   holding the paragraph and the key it writes. The other tools have no such conditions and
   nothing is drawn for them. */
const TOLD = {
  hold_target: ["Hold the target's shape", "C_CRDGRP", "tellbox",
                "Its backbone keeps its geometry and its side chains stay free, so the binder is "
                + "designed against the shape rather than taught to mimic the side chains. This "
                + "is what the tool's authors ask for in a protein-protein run."],
  reveal_sequence: ["Show the target's sequence", "C_SEQ", "tellbox",
                    "The model is told which residues it is designing against, not only where "
                    + "they are."],
  use_hotspots: ["Reach the site", "C_HOT", "siteswitch",
                 "The residues marked on this card. Switched off, the condition is left out of "
                 + "the spec and the model is not told to reach them. The site is still part of "
                 + "the run and is still required: this tool is aimed at a site."],
};

/* One switch, one line, on the card it belongs to. The paragraph and the key the job writes open
   from the ? beside the name, which is where every other reason on this page lives. */
function switchRow(key, name, spec, why) {
  const row = el("div", "swrow");
  const sw = el("button", "sw");
  sw.type = "button";
  sw.id = "sw_" + key;
  sw.setAttribute("aria-pressed", "true");
  sw.setAttribute("aria-label", name);
  sw.onclick = () => {
    const on = sw.getAttribute("aria-pressed") === "true";
    sw.setAttribute("aria-pressed", on ? "false" : "true");
    paintSwitches();
    check();
  };
  row.appendChild(sw);
  row.appendChild(el("span", "nm2", name));
  const q = el("button", "q", "?");
  q.type = "button";
  q.setAttribute("aria-label", "Why: " + name);
  q.onclick = () => {
    CURRENT_HELP = "told";
    openHelp(name, [why, "Written into the specification the job reads as " + spec + "."]);
  };
  row.appendChild(q);
  row.appendChild(el("span", "state", "on"));
  return row;
}

function buildSwitches() {
  const into = { tellbox: $("tellbox"), siteswitch: $("siteswitch") };
  Object.values(into).forEach((box) => { box.innerHTML = ""; box.hidden = false; });
  Object.entries(TOLD).forEach(([key, [name, spec, where, why]]) => {
    into[where].appendChild(switchRow(key, name, spec, why));
  });
  paintSwitches();
}

/* on or off at the end of the row, so a switch read at a glance says which it is rather than
   leaving it to the color of a sliver of track. */
function paintSwitches() {
  Object.keys(TOLD).forEach((key) => {
    const sw = $("sw_" + key);
    if (!sw) return;
    const state = sw.parentNode.querySelector(".state");
    if (state) state.textContent = sw.getAttribute("aria-pressed") === "true" ? "on" : "off";
  });
}

/* ------------------------------------------------------------------ the binder's length
   Two sliders over one track, each with its own label: there is no two-handle range in HTML, and
   two that a screen reader can read separately beats one widget built out of divs. The boxes stay
   for an exact figure and each moves its own handle. */
function lengthFromBoxes() {
  const lo = Number($("binder_min").value);
  const hi = Number($("binder_max").value);
  if (Number.isFinite(lo)) $("len_lo").value = String(Math.min(Math.max(lo, 20), 200));
  if (Number.isFinite(hi)) $("len_hi").value = String(Math.min(Math.max(hi, 20), 200));
  paintLength();
}

function paintLength() {
  const lo = Number($("len_lo").value);
  const hi = Number($("len_hi").value);
  const span = 200 - 20;
  const left = ((Math.min(lo, hi) - 20) / span) * 100;
  const right = ((Math.max(lo, hi) - 20) / span) * 100;
  $("lenfill").style.left = left + "%";
  $("lenfill").style.width = Math.max(0, right - left) + "%";
  $("lennow").textContent = Math.min(lo, hi) + " to " + Math.max(lo, hi) + " residues";
}

function wireLength() {
  ["len_lo", "len_hi"].forEach((id) => {
    $(id).addEventListener("input", () => {
      /* Either handle may be dragged past the other; the lower of the two is the shortest, which
         is what somebody dragging them means whichever one they have hold of. */
      const lo = Math.min(Number($("len_lo").value), Number($("len_hi").value));
      const hi = Math.max(Number($("len_lo").value), Number($("len_hi").value));
      $("binder_min").value = String(lo);
      $("binder_max").value = String(hi);
      paintLength();
      clearTimeout(typing);
      typing = setTimeout(check, 280);
    });
  });
  ["binder_min", "binder_max"].forEach((id) => {
    $(id).addEventListener("input", lengthFromBoxes);
  });
}

function showFieldErrors(errors) {
  const keys = FIELDS.map((f) => f.key).concat(["hotspots", "binder_min", "binder_max", "out", "chains"]);
  keys.forEach((key) => {
    const box = $(key + "-err");
    const input = $(key);
    const text = errors[key] || "";
    if (box) { box.textContent = text; box.hidden = !text; }
    if (input) input.classList.toggle("bad", !!text);
  });
  targetError(errors.target || "");
  $("g-res").classList.toggle("bad", !!(errors.walltime || errors.gpu));
}

/* WHAT THE JOB WILL BE TOLD, for the curious: the contig map and the inputs, and - for the one
   tool that has one - the condition spec the switches write. It is a dialog behind a quiet "Show
   the job" link under the plan, never a panel on the form: it is the thing nobody needs to read
   to queue a run and the first thing somebody wants when a run comes out wrong. */
let PREVIEW = null;
let TOLDVIEW = "input";

function paintPreview(seen) {
  PREVIEW = seen || null;
  const lines = (seen && seen.lines) || [];
  $("showjob").hidden = !lines.length;
  const specbtn = document.querySelector('#toldmodes button[data-told="spec"]');
  specbtn.hidden = !(seen && seen.spec);
  if (specbtn.hidden && TOLDVIEW === "spec") showTold("input");
  if ($("jobdlg").open) paintTold();
}

function paintTold() {
  const spec = TOLDVIEW === "spec" && PREVIEW && PREVIEW.spec;
  $("toldwhat").textContent = spec
    ? "Each condition the model is told, as it reads them."
    : "What the job is told about this target and the binder asked for.";
  $("toldtext").textContent = spec ? PREVIEW.spec : ((PREVIEW && PREVIEW.lines) || []).join("\n");
}

function showTold(which) {
  TOLDVIEW = which;
  document.querySelectorAll("#toldmodes button").forEach((b) => {
    b.classList.toggle("on", b.dataset.told === which);
  });
  paintTold();
}

/* A folder named the way a person names it: "My scratch/rfd4_runs" rather than sixty characters
   of path. The two roots come with the greeting, so the page does not guess at them. */
function shortPath(path) {
  const p = String(path || "");
  const home = (HELLO && HELLO.home) || "";
  const scratch = (HELLO && HELLO.scratch) || "";
  if (scratch && (p === scratch || p.startsWith(scratch + "/"))) return "My scratch" + p.slice(scratch.length);
  if (home && (p === home || p.startsWith(home + "/"))) return "Home" + p.slice(home.length);
  const parts = p.split("/").filter(Boolean);
  return parts.length > 3 ? "…/" + parts.slice(-3).join("/") : p;
}

/* The same, squeezed to a root and a last folder, for the one line in the header. A run folder
   can be nested as deep as somebody likes - in the rig it is four folders under a temporary
   directory - and the header has one line for it, so the whole of it there is a line cut with an
   ellipsis wherever the ellipsis happens to fall. Two named ends and a gap says the same thing
   and fits: "My scratch/…/rfd4_runs". The whole path is the line's own tooltip. */
function headPath(path) {
  const said = shortPath(path);
  const parts = said.split("/").filter(Boolean);
  if (parts.length <= 2) return said;
  return parts[0] + "/…/" + parts[parts.length - 1];
}

/* A walltime in the words somebody reads it in. The figure is the one the job is given, so the
   plan says "up to", which is the honest word for it: nothing here guesses how long a design
   actually takes on the card it lands on. */
function plainTime(clock) {
  const seconds = walltimeSeconds(clock);
  if (!Number.isFinite(seconds)) return String(clock || "");
  if (seconds < 3600) return Math.max(1, Math.round(seconds / 60)) + " min";
  const hours = seconds / 3600;
  if (hours < 48) return (Number.isInteger(hours) ? hours : hours.toFixed(1)) + " h";
  return Math.round(hours / 24) + " days";
}

/* The GPU named the way somebody reads it. "auto" is the page letting the queue decide, which is
   not a model to put on a plan at all, and a model reads as "one A100 GPU": the bare word card,
   which is what the rail and the plan both used to say, is the jargon they were marked down for. */
function oneGpu(gpu) {
  const name = String(gpu || "").trim();
  if (!name || name === "auto") return "one GPU";
  return "one " + name.toUpperCase() + " GPU";
}

function planStep(into, n, name, what, time) {
  const li = el("li");
  li.appendChild(el("span", "pn", String(n)));
  const mid = el("span", "pw");
  mid.appendChild(el("b", null, name));
  mid.appendChild(el("span", null, what));
  li.appendChild(mid);
  li.appendChild(el("span", "pt", time));
  into.appendChild(li);
}

/* THE RUN PLAN: what will happen, where, and roughly how long, as numbered steps in the rail
   directly above Submit. It is drawn only once there is a target - before that the rail says
   what is missing under a Submit that cannot be pressed, and shows no empty bar waiting. */
function paintPlan(plan, notes) {
  const box = $("plan");
  const steps = $("phases");
  const have = !!(plan && plan.gpu);
  box.hidden = !have;
  if (have) {
    steps.innerHTML = "";
    /* Plain numbered steps: what happens, where, and roughly how long. The cores, the memory,
       the partition and the word walltime are not here - they went behind the plan's own ?,
       because this box was reading as a Slurm request rather than as a plan. */
    planStep(steps, 1, "Target", "copied now, into the run folder", "seconds");
    planStep(steps, 2, "Design",
             plan.designs + (plan.designs === 1 ? " design" : " designs") + " on " + oneGpu(plan.gpu),
             "up to " + plainTime(plan.walltime));
    const out = ($("out").value || "").trim();
    const root = out || ((HELLO && (HELLO.roots || [])[0]) || "");
    /* The rail is one line wide, and a run folder nested five deep was broken across three
       lines and cut mid-name. Named the way the header names it - two ends and a gap - with
       the whole of it as the line's own tooltip. */
    $("planwhere").title = root;
    $("planwhere").textContent = (root ? "Results in " + headPath(root) : "")
      + (plan.account ? (root ? " · " : "") + "charged to " + plan.account : "");
  }
  const note = $("notes");
  note.textContent = (notes || []).join(". ");
  note.hidden = !(notes || []).length;
  note.className = "note" + ((notes || []).length ? " warn" : "");
}

/* What the flow's own check() refuses. ONE line under Submit says what to do about it, and the
   server's own sentences - which are the flow's, in the flow's words, and are long - open from a
   quiet link beside it. They used to be listed above Submit as well, so the same refusal was on
   the page twice, once in sixteen words and once in three. */
let REFUSALS = [];

function paintProblems(problems) {
  REFUSALS = (problems || []).slice();
  const link = $("morewhy");
  link.hidden = !REFUSALS.length;
  link.textContent = REFUSALS.length > 1 ? "Why not? (" + REFUSALS.length + ")" : "Why not?";
}

$("morewhy").onclick = () => {
  CURRENT_HELP = "";
  openHelp("Why it cannot be submitted", REFUSALS.map(sentence));
};

/* ------------------------------------------------------------------ what to do next
   A chip per card saying where that card stands, the step being worked on outlined in the row of
   steps, and one line in the Next bar naming the next action. That is the whole of the page's
   guidance: every reason sits behind a ?, and this says only what to do. */
const STEPS = ["target", "site", "binder", "submit"];
let NEXTGO = "target";

function cardState(id, text, kind) {
  const chip = $(id);
  if (!chip) return;
  chip.textContent = text;
  chip.className = "cstate" + (kind ? " " + kind : "");
  /* Nothing rather than an empty pill: a card whose state cannot be known yet says nothing. */
  chip.hidden = !text;
}

/* The plan and Submit are the foot of the rail, held there by the layout (.rail is a column, the
   settings scroll in the top of it, .submitbox is its last flex item), so they are on the screen
   whatever the settings above them do and at whatever text size. Nothing is decided here any
   more: the box used to be stuck over the settings by a class this function added and dropped,
   and the moment it dropped - which was the moment the run plan appeared and Submit became
   pressable - Submit went 263px below the bottom of the window while the Next bar said to press
   it. It also covered Run folder and Browse on the way.

   What is left is the one thing the layout cannot do for itself: the 3D viewer. */

/* THE VIEWER, FITTED TO ITS PANEL. 3Dmol takes the panel's size when the viewer is made and
   keeps that camera, so two things went wrong and both of them drew the target as a squiggle a
   hundred pixels across in a panel nine hundred wide:

     * the panel is revealed (vwrap.hidden = false) in the same task as the viewer is made, so
       the size 3Dmol measured was not the size the panel settled at, and the fit was computed
       against the wrong one;
     * the text size changes the panel's width, and resizing the canvas without fitting again
       leaves the structure at the size it was drawn for the old one.

   So the fit is done again: once layout has settled after the viewer is made, and again whenever
   the panel changes size. Fitting again is what Reset does, so a rotation somebody set is lost
   on a text-size step - which is the smaller cost by a long way: a structure drawn eight pixels
   wide cannot be rotated to any purpose, and a press on it marks a residue a hundred and sixty
   pixels from the one under the pointer. */
let fitting = 0;
function fitSoon() {
  clearTimeout(fitting);
  fitting = setTimeout(() => { if (VIEW) buildViewer(); }, 140);
}
window.addEventListener("resize", fitSoon);
window.addEventListener("hl-zoom", fitSoon);

function paintGuide(answer) {
  const fields = (answer && answer.fields) || {};
  const bad = Object.values(fields);
  const problems = (answer && answer.problems) || [];
  /* Whether this run needs a site is the card's own answer, and the server sends it with the
     greeting: every tool these pages serve is aimed at a site and none of them will start
     without one. The card used to say "Optional" until the server had refused a run for want of
     a site, which is to say it was wrong at the one moment anybody reads it - on the empty form,
     before they have given it anything - and right only once they were past it. */
  const wantsSite = !SITE.size && (HELLO ? HELLO.needs_site !== false : true);

  cardState("st-target", T.path ? "Chosen" : "Needed", T.path ? "done" : "need");
  cardState("st-site", SITE.size ? SITE.size + " marked" : "Needed",
            SITE.size ? "done" : "need");
  cardState("st-binder", bad.length && !wantsSite ? "Check this" : "Ready",
            bad.length && !wantsSite ? "need" : "done");

  let says = "";
  let goto = "target";
  let why = "";
  if (!T.path && CODING) {
    says = "Type a four-character PDB code and press Fetch.";
    why = "Choose a structure first.";
  } else if (!T.path) {
    says = "Choose a structure to design against.";
    why = "Choose a structure first.";
  } else if (wantsSite) {
    /* The one case worth its own words: the run is refused for want of a site, and the card to
       go to is the site rather than the one the refusal happens to name. */
    says = "Press the residues the binder has to reach.";
    goto = "site";
    why = fields.hotspots || "Mark the site first.";
  } else if (bad.length || problems.length) {
    /* Otherwise the server's own words, which are the flow's own words: one line, beside the
       button that cannot be pressed, rather than a list nobody scrolls to. */
    says = bad[0] || problems[0];
    goto = "binder";
    why = bad[0] || problems[0];
  } else {
    says = "Press Submit to queue the run.";
    goto = "submit";
  }
  NEXTGO = goto;
  $("nexttext").textContent = says;
  $("nextbar").classList.toggle("ready", goto === "submit");
  $("gowhy").textContent = why;
  $("gowhy").hidden = !why;
  $("gowhy").classList.toggle("ok", !why);

  const at = STEPS.indexOf(goto);
  document.querySelectorAll("#steps .st").forEach((st) => {
    const i = STEPS.indexOf(st.dataset.goto);
    st.classList.toggle("done", i < at);
    st.classList.toggle("next", i === at);
  });
  /* The card to do next, outlined, so the eye lands on it without reading the row of steps. */
  STEPS.forEach((which) => {
    const box = $("step-" + which);
    if (box) box.classList.toggle("is-next", which === goto);
  });
}

/* Show me, and the row of steps: the card scrolled to and outlined for a moment. "Press Submit"
   is no use while Submit is somewhere else on the page. */
function goToStep(which) {
  const box = $("step-" + which);
  if (!box) return;
  box.scrollIntoView({ behavior: "smooth", block: which === "submit" ? "end" : "start" });
  box.classList.add("lit");
  setTimeout(() => box.classList.remove("lit"), 1600);
}
$("nextgo").onclick = () => goToStep(NEXTGO);
document.querySelectorAll("#steps .st").forEach((st) => {
  st.onclick = () => goToStep(st.dataset.goto);
});

/* The job, for the curious, behind the quiet link under the plan. */
$("showjob").onclick = () => { paintTold(); $("jobdlg").showModal(); };
$("jobx").onclick = () => $("jobdlg").close();
$("jobclose").onclick = () => $("jobdlg").close();
document.querySelectorAll("#toldmodes button").forEach((b) => {
  b.onclick = () => showTold(b.dataset.told);
});

let typing = 0;
let checking = 0;
async function check() {
  const mine = ++checking;
  if (!T.path) {
    LAST = null;
    $("go").disabled = $("dry").disabled = true;
    paintPlan(null, []); paintProblems([]); paintPreview(null); showFieldErrors({});
    paintGuide(null);
    return;
  }
  try {
    const answer = await api("/api/check", form());
    if (mine !== checking) return;                     // a later keystroke already asked
    LAST = answer;
    showFieldErrors(answer.fields || {});
    paintProblems(answer.problems);
    paintPlan(answer.plan, answer.notes);
    paintPreview(answer.preview);
    const clean = !(answer.problems || []).length && !Object.keys(answer.fields || {}).length;
    $("go").disabled = $("dry").disabled = !clean;
    paintGuide(answer);
  } catch (failure) {
    if (mine === checking) flash(failure.message, true);
  }
}

/* ------------------------------------------------------------------ submitting */
async function go(dry) {
  const button = dry ? $("dry") : $("go");
  const was = button.textContent;
  button.disabled = true; button.textContent = dry ? "writing…" : "queueing…";
  try {
    const answer = await api("/api/submit", Object.assign(form(), { dry_run: dry }));
    if (answer.problems || answer.fields) {
      showFieldErrors(answer.fields || {});
      paintProblems(answer.problems);
      flash("Not submitted: the form has something to put right.", true);
      return;
    }
    const run = answer.run;
    $("golast").hidden = false;
    $("golast").textContent = (run.job_id ? "Queued as job " + run.job_id + ". " : "Written, nothing queued. ") + run.path;
    flash(run.job_id ? "Job " + run.job_id + " queued: " + run.name : "Run folder written: " + run.name);
    loadRuns(run.path);
    document.querySelector('.tab[data-screen="runs"]').click();
  } catch (failure) {
    flash(failure.message, true);
  } finally {
    button.disabled = false; button.textContent = was;
  }
}
$("go").onclick = () => go(false);
$("dry").onclick = () => go(true);

/* ------------------------------------------------------------------ runs */
let SCOPE = "mine";
let OPEN = "";
let runsTimer = 0;

function dotClass(run) {
  const state = (run.state || "").toUpperCase();
  if (state === "DRY RUN") return "dot dry";
  if (state === "RUNNING") return "dot run";
  if (state === "PENDING" || state === "CONFIGURING") return "dot q";
  if (state === "COMPLETED") return "dot fin";
  if (!state) return "dot unk";
  return run.over ? "dot stop" : "dot unk";
}

document.querySelectorAll("#scope button").forEach((b) => {
  b.onclick = () => {
    SCOPE = b.dataset.scope;
    document.querySelectorAll("#scope button").forEach((o) => o.classList.toggle("on", o === b));
    loadRuns();
  };
});
$("refresh").onclick = () => loadRuns(OPEN);

async function loadRuns(open) {
  clearTimeout(runsTimer);
  try {
    const answer = await api("/api/runs?scope=" + SCOPE);
    const list = $("runlist");
    list.innerHTML = "";
    const live = answer.runs.filter((r) => ["RUNNING", "PENDING"].includes((r.state || "").toUpperCase())).length;
    $("runcount").textContent = live;
    $("runcount").hidden = !live;
    if (!answer.runs.length) {
      list.appendChild(el("p", "empty", SCOPE === "mine" ? "You have no runs here yet." : "No runs in these folders yet."));
    }
    answer.runs.forEach((run) => {
      const row = el("button", "runrow" + (run.path === (open || OPEN) ? " on" : ""));
      row.appendChild(el("span", dotClass(run)));
      const name = el("span", "name", run.name);
      name.title = run.path;
      row.appendChild(name);
      if (SCOPE !== "mine") row.appendChild(el("span", "ago", run.owner));
      row.appendChild(el("span", "ago", run.modified.slice(5)));
      row.onclick = () => openRun(run.path);
      list.appendChild(row);
    });
    if (open) openRun(open);
    else if (OPEN) openRun(OPEN, true);
    /* A design job writes a backbone every few minutes: often enough to watch, cheap to read.
       Nothing is read while the Runs screen is put away. */
    if (!$("screen-runs").hidden) runsTimer = setTimeout(() => loadRuns(), 25000);
  } catch (failure) {
    $("runlist").innerHTML = "";
    $("runlist").appendChild(el("p", "empty", failure.message));
  }
}

/* Has anything scored this run's designs? Asked in one place, because the tile at the top and
   the note under the table used to answer it differently. A tool that places a binder and does
   not fold it back has nothing to rank by, and the rows are then in the order they were
   written. */
function ranked(run) {
  const rows = (run && run.table && run.table.rows) || [];
  return rows.some((r) => r.score != null || r.fold_rmsd != null);
}

function tile(key, value, sub) {
  const box = el("div", "tile");
  box.appendChild(el("div", "tk", key));
  box.appendChild(el("div", "big", value));
  if (sub) box.appendChild(el("div", "sub", sub));
  return box;
}

function table(spec) {
  const box = el("div", "tblbox");
  const t = el("table", "t");
  const head = el("tr");
  spec.columns.forEach((c) => head.appendChild(el("th", null,
    c === "reached" ? "reaches the site" : c === "fold_rmsd" ? "folds back (Å)"
      : c === "plddt" ? "pLDDT" : c)));
  t.appendChild(el("thead")).appendChild(head);
  const body = el("tbody");
  spec.rows.forEach((row) => {
    const tr = el("tr");
    spec.columns.forEach((c) => {
      /* What this design reached of what the run was aimed at, drawn as a tick per marked
         residue: the numbers 6 and 7 side by side in a column of numbers are read as a score,
         and the ticks say at a glance which design landed on the pocket. */
      if (c === "reached" && row.marked) {
        const cell = el("td");
        const bar = el("span", "reach");
        bar.title = (row.reach || []).map((r) => r.residue + (r.contacts
          ? ": " + r.contacts + " contact" + (r.contacts === 1 ? "" : "s")
            + (r.nearest ? ", nearest " + r.nearest : "")
          : ": not reached")).join("\n");
        (row.reach || []).forEach((r) => {
          bar.appendChild(el("i", r.contacts ? "on" : null));
        });
        cell.appendChild(bar);
        cell.appendChild(el("span", null, row.reached + " of " + row.marked));
        if (row.contacts) cell.appendChild(el("span", "why", " · " + row.contacts + " contacts"));
        tr.appendChild(cell);
        return;
      }
      let value = row[c] == null ? "" : String(row[c]);
      if (/^-?\d*\.\d{4,}$/.test(value)) value = Number(value).toFixed(3);
      tr.appendChild(el("td", c === "sequence" || c === "name" ? "wrap" : null, value));
    });
    body.appendChild(tr);
  });
  t.appendChild(body);
  box.appendChild(t);
  return box;
}

/* ONE SHORT LINE, AND A ?. The results page carried two paragraphs - thirty-six words on why a
   column was empty, thirty on what the ticks mean - which is the pattern the forms were rebuilt
   to lose, and there is no reason the Runs screen should keep it. */
function noteWithWhy(kind, line, title, lines) {
  const note = el("p", "note" + (kind ? " " + kind : ""), line);
  const q = el("button", "q", "?");
  q.type = "button";
  q.setAttribute("aria-label", title);
  q.onclick = () => { CURRENT_HELP = ""; openHelp(title, lines); };
  note.appendChild(q);
  return note;
}

function download(run, file, label) {
  const a = el("a", "btn sm", label);
  a.href = "/api/download?t=" + encodeURIComponent(TOKEN) + "&path=" + encodeURIComponent(run.path)
    + "&file=" + encodeURIComponent(file);
  a.style.textDecoration = "none";
  return a;
}

async function openRun(path, quiet) {
  OPEN = path;
  document.querySelectorAll(".runrow").forEach((row) => row.classList.toggle("on", row.querySelector(".name").title === path));
  const main = $("rundetail");
  if (!quiet) { main.innerHTML = ""; main.appendChild(el("p", "empty", "Reading the run…")); }
  let run;
  try { run = (await api("/api/run?path=" + encodeURIComponent(path))).run; }
  catch (failure) { main.innerHTML = ""; main.appendChild(el("p", "empty", failure.message)); return; }
  main.innerHTML = "";

  const head = el("div", "runhead");
  head.appendChild(el("h1", null, run.name));
  head.appendChild(el("span", "pill" + (run.state === "RUNNING" ? " on" : ""), run.state || "not queued"));
  if (run.job_id) head.appendChild(el("span", "why", "job " + run.job_id
    + (run.elapsed ? ", " + run.elapsed + " elapsed" : "") + (run.left ? ", " + run.left + " left" : "")));
  if (run.reason) head.appendChild(el("span", "why", run.reason));
  main.appendChild(head);

  /* THREE NUMBERS, THREE HEADINGS. Two of these tiles used to be headed "designs" and both
     showed the same figure, told apart only by their caption; the third was headed "asked for"
     and captioned with a walltime, so a walltime was the subtitle of a count. Each heading now
     names its own number, and the walltime is in What was asked for below, where the rest of
     the request is. */
  const counts = run.counts || {};
  const asked = run.asked || {};
  const tiles = el("div", "tiles");
  tiles.appendChild(tile("Designs asked for", String(asked.designs == null ? "?" : asked.designs),
    asked.gpu ? "on " + oneGpu(asked.gpu) : ""));
  /* What is on disk: "designs" is the backbones written so far, and BoltzGen also counts the
     ones it looked at and set aside. Named here rather than shown as the key the folder is. */
  const COUNTNAME = { designs: "Designs written", considered: "Designs set aside" };
  Object.keys(counts).sort().forEach((key) => tiles.appendChild(
    tile(COUNTNAME[key] || key, String(counts[key]),
         key === "designs" ? "files in the run folder" : "")));
  /* The third tile is drawn only where something has scored the designs. It used to read
     "Designs ranked 2, best first" above a table that says "Not ranked: nothing has scored
     these designs", which is the page contradicting itself on the one screen a person comes
     to for the answer - and a third big 2 beside the other two. */
  if (ranked(run)) {
    tiles.appendChild(tile("Designs ranked", String(run.designs == null ? "?" : run.designs),
      "best first"));
  }
  main.appendChild(tiles);

  const what = el("div", "box");
  what.appendChild(el("h2", null, "What was asked for"));
  const kv = el("dl", "kv");
  const target = run.target || {};
  const rows = [["target", (target.pdb_id ? target.pdb_id + ", " : "") + "chains " + (target.chains || "every one")],
   ["the site", target.hotspots || "none"],
   ["binder", (target.binder || ["?", "?"]).join(" to ") + " residues"],
   ["time allowed", asked.walltime || "—"],
   ["results", shortPath(run.results)],
   ["submitted", run.submitted || "—"],
   ["owner", run.owner || "—"]];
  rows.forEach(([k, v]) => {
    kv.appendChild(el("dt", null, k));
    const dd = el("dd", null, String(v));
    /* The folder named the way the header and the run plan name it - "My scratch/rfd4_runs/..." -
       with the whole path as the line's tooltip. It was the one path on these pages still shown
       raw, which on a run under a long scratch path was sixty characters of nothing to read. */
    if (k === "results") dd.title = run.results;
    kv.appendChild(dd);
  });
  what.appendChild(kv);

  const actions = el("div", "btns");
  /* THE RUN WRITTEN UP: its report, a page of its own to read and save as a PDF (/report, laid
     out by reportkit.js). Offered for a run that stopped as well as a finished one: the report
     of a run that stopped says where it stopped and what would fill the gap, which is the thing
     somebody most wants to read. print=1 opens the print window once everything is drawn. */
  const report = el("button", "btn sm primary", "Report (PDF)");
  report.type = "button";
  report.title = "The whole run written up: the ranking, how the designs converged, the "
    + "sequences, the best design on the target, and the methods with their references, in a "
    + "new tab ready to save as a PDF";
  report.onclick = () => window.open("/report?t=" + encodeURIComponent(TOKEN) + "&rundir="
    + encodeURIComponent(run.path) + "&print=1", "_blank", "noopener");
  actions.appendChild(report);
  (run.files || []).forEach((name) => {
    if (/designs\.json$/.test(name)) actions.appendChild(download(run, name, "designs.json"));
    else if (/plan\.json$/.test(name)) actions.appendChild(download(run, name, "The plan"));
    else if (/job\.sbatch$/.test(name)) actions.appendChild(download(run, name, "Job script"));
  });
  /* THE LOG, BEHIND A BUTTON. The last lines of it used to be poured down the results page in a
     panel of their own - FutureWarnings, a progress bar's block characters, atomworks telling
     somebody about an annotation value - under the designs anybody came to read. It is the first
     thing wanted when a run goes wrong and nothing to read when it has not, which is a dialog. */
  if (run.log_name) {
    const seen = el("button", "btn sm", "The log");
    seen.type = "button";
    seen.title = "The last of what the job printed, and a link to the whole file";
    seen.onclick = () => {
      $("logtitle").textContent = "The log";
      $("logsays").textContent = run.log_name;
      $("logtext").textContent = run.log || "nothing has been printed yet";
      const foot = $("logdlg").querySelector(".mfoot .why");
      foot.innerHTML = "";
      foot.appendChild(download(run, run.log_name, "Download the whole log"));
      $("logdlg").showModal();
      $("logtext").scrollTop = $("logtext").scrollHeight;
    };
    actions.appendChild(seen);
  }
  if (run.job_id && !run.over) {
    const stop = el("button", "btn sm", "Cancel the job");
    stop.onclick = async () => {
      if (!confirm("Cancel job " + run.job_id + "? What is already on disk stays in the run folder.")) return;
      try { const a = await api("/api/cancel", { path: run.path }); flash(a.message, !a.ok); loadRuns(run.path); }
      catch (failure) { flash(failure.message, true); }
    };
    actions.appendChild(stop);
  } else if (run.job_id) {
    const again = el("button", "btn sm", "Queue again");
    again.title = "the same job script, from the beginning: neither tool writes a checkpoint";
    again.onclick = async () => {
      try { const a = await api("/api/resubmit", { path: run.path }); flash(a.message, !a.ok); loadRuns(run.path); }
      catch (failure) { flash(failure.message, true); }
    };
    actions.appendChild(again);
  }
  what.appendChild(actions);
  main.appendChild(what);

  if (run.table && run.table.rows.length) {
    const box = el("div", "box");
    /* Ranked once something has measured the designs: a score from the tool, or the refold of
       each design's own sequence (rfd4_rank.py), which is what RFD4-Proteina's are ranked by. */
    const scored = ranked(run);
    /* The heading is the one word, and how many there are is the muted note beside it: as one
       line it read as a five-word label on a results page where everything else is three. */
    const head = el("h2", null, "Designs");
    head.appendChild(el("span", "why", run.table.total + " in all"
      + (run.table.rows.length < run.table.total ? ", " + run.table.rows.length + " shown" : "")
      + (scored ? ", best first" : "")));
    box.appendChild(head);
    box.appendChild(table(run.table));
    /* Said plainly rather than left to be assumed from the order. RFD4-Proteina designs the
       sequence with the backbone and nothing has scored the result, so the table is in the order
       the tool wrote them: read as a ranking, the first row would look like the best design. */
    if (!scored) {
      box.appendChild(noteWithWhy("warn", "Not ranked: nothing has scored these designs.",
        "Why these are not ranked",
        ["This tool places a binder on the target. It does not say whether the binder's own "
         + "sequence folds into the shape it was drawn as, so there is nothing here to rank by.",
         "The rows are in the order the tool wrote them, which is not an order of merit. Fold "
         + "each design back from its sequence alone and the refold gives a ranking: that is "
         + "what Fold and rank designs does on the form, and what rfd4_rank.py does to a run "
         + "that was queued without it."]));
    }
    if (run.table.site && run.table.site.length) {
      box.appendChild(noteWithWhy("", "Aimed at " + run.table.site.join(", ") + ".",
        "The ticks in the table",
        ["The run was aimed at these residues of the target.",
         "One tick per residue, filled where that design reaches it within 4.5 Å. Hover a row's "
         + "ticks for the contacts residue by residue, and the nearest atom of each."]));
    }
    main.appendChild(box);
  }

}

/* ------------------------------------------------------------------ start */
(async function start() {
  if (!TOKEN) { flash("This page needs the token the server printed: open the URL from the terminal.", true); return; }
  try {
    HELLO = await api("/api/hello");
    FIELDS = HELLO.fields || [];
    /* ONE muted line, in sans: the cluster and where the runs go. Who you are, which node you
       are on and which version this is are the line's tooltip and the bar along the bottom. */
    const root = (HELLO.roots || [])[0] || "";
    $("where").textContent = (HELLO.site || HELLO.cluster || HELLO.host)
      + (root ? " · runs in " + headPath(root) : "")
      + (HELLO.dry_run ? " · dry run" : "");
    $("where").title = HELLO.user + "@" + HELLO.host + "\n" + HELLO.tool + " "
      + HELLO.tool_version + (root ? "\n" + root : "");
    $("appname").textContent = HELLO.app;
    document.title = HELLO.app;
    (HELLO.target_fields || []).forEach((f) => { if ($(f.key) && f.default != null) $(f.key).value = f.default; });
    buildFields();
    /* The conditions are RFD4-Proteina's own: no other tool here is told what to hold and what to
       reveal, so the card is only built where it means something. */
    if (HELLO.tool === "rfd4") buildSwitches();
    wireLength();
    lengthFromBoxes();
    paintSite();
    paintGuide(null);
    /* A server started with --dry-run queues nothing whichever button is pressed, so the
       quiet Dry run beside it is the same press under another name. Said once, on the button
       that is there. */
    if (HELLO.dry_run) {
      $("go").textContent = "Submit (dry run)";
      $("dry").hidden = true;
    }
  } catch (failure) {
    flash(failure.message, true);
  }
  /* Opened on a run, when the launcher's Results asked for one: the Runs screen, with that run
     shown. The tab first, because switching to it reads the list afresh, and the run after. */
  const asked = new URLSearchParams(location.search).get("open");
  if (asked) {
    const tab = document.querySelector('.tab[data-screen="runs"]');
    if (tab) tab.click();
    loadRuns(asked);
  } else {
    loadRuns();
  }
})();
