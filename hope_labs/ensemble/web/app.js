/* The ensemble page: Sample, which collects the molecules and queues a job each, and Runs, which
   reads the run folders, the queue and what reading the samples found.

   Nothing about the tool is written here. The settings are the tool's own table, sent by the
   server, and every refusal comes from the server, which refuses what the job would fail on, in
   the words a person can act on.

   Two things this page insists on. A structure is read rather than described: choose a file, a
   code or a run and the chain's sequence comes off it, which is the difference between sampling
   the chain you meant and the chain you typed. And an ensemble is not a trajectory: the viewer
   draws the shapes overlaid rather than played, and there is no time axis anywhere.

   And one thing the form insists on, because it was rejected for the opposite: every setting is
   in one place, every reason is behind a ?, and the form says in one line what to do next. */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

/* Where each setting is drawn. Every one of them is in exactly one place: Model and Alignment
   were once in the Sampling card and in the rail at the same time. */
const RESOURCE = ["gpu", "walltime", "account"];
const IN_SAMPLING = ["num_samples", "model_name", "alignments", "msa_file"];
const IN_AFTER = new Set(["keep_files"]);
/* The short name a choice is shown by. The longer note is its tooltip, and the ? says the rest. */
const SHORT = {
  model_name: (v) => String(v).replace(/^bioemu-/, ""),
  alignments: (v) => ({ fetch: "Fetch", file: "My .a3m" }[v] || v),
  gpu: (v) => (v === "auto" ? "Auto (" + ((HELLO && HELLO.site && HELLO.site.gpus || [])[0] || "GPU").toUpperCase() + ")" : String(v).toUpperCase()),
};
const STRUCTURE_KINDS = [".pdb", ".ent", ".cif", ".mmcif"];

let HELLO = null;
let FIELDS = [];
let PRESETS = [];
let MOLECULES = [];
let LASTCHECK = null;
let TOLD = null;

async function api(path, body) {
  const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const answer = await fetch(url, init);
  let data = {};
  try { data = await answer.json(); } catch (e) { data = { error: "the server sent something that is not JSON (" + answer.status + ")" }; }
  if (!answer.ok && !data.fields && !data.problems) throw new Error(data.error || "the server answered " + answer.status);
  return data;
}

/* A refusal is a sentence or three; the first one is what to do, and the rest is the reason,
   which the tooltip keeps. */
function firstSentence(text) {
  const said = String(text || "").trim();
  const cut = said.search(/\.\s+[A-Z(]/);
  const one = cut > 0 ? said.slice(0, cut + 1) : said;
  return one.charAt(0).toUpperCase() + one.slice(1);
}

let flashTimer = 0;
function flash(text, bad) {
  clearTimeout(flashTimer);
  document.querySelectorAll(".flash").forEach((n) => n.remove());
  const box = el("div", "flash" + (bad ? " bad" : ""), text);
  box.setAttribute("role", "status");
  document.body.appendChild(box);
  flashTimer = setTimeout(() => box.remove(), bad ? 15000 : 7000);
}

/* ------------------------------------------------------------------ theme */
const theme = $("theme");
function isDark() {
  return document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches);
}
function paintTheme() { theme.textContent = isDark() ? "Light" : "Dark"; }
try { if (localStorage.hldesigntheme) document.documentElement.dataset.theme = localStorage.hldesigntheme; } catch (e) { /* private window */ }
theme.onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.hldesigntheme = document.documentElement.dataset.theme; } catch (e) { /* ignore */ }
  paintTheme();
  if (VIEW) paintViewerBackground();
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
    else holdSubmit();
  };
});

/* Show a card: scrolled to, and flashed so the eye finds it. */
function showCard(which) {
  const box = $("step-" + which);
  if (!box) return;
  box.scrollIntoView({ behavior: "smooth", block: which === "submit" ? "end" : "start" });
  box.classList.remove("flash-on");
  void box.offsetWidth;
  box.classList.add("flash-on");
  setTimeout(() => box.classList.remove("flash-on"), 1400);
}
document.querySelectorAll("#rail .st").forEach((b) => { b.onclick = () => showCard(b.dataset.goto); });

/* ------------------------------------------------------------------ the ? dialog */
function openHelp(key) {
  const f = FIELDS.find((x) => x.key === key);
  const own = HELP[key];
  const title = (own && own.title) || (f && f.label) || key;
  let body = (own && own.body) || (f && (f.explain || f.why)) || "";
  if (key === "ensemble" && HELLO) body = HELLO.statement + "\n\n" + body;
  $("helptitle").textContent = title;
  $("helpbody").innerHTML = "";
  String(body).split("\n\n").forEach((p) => { if (p.trim()) $("helpbody").appendChild(el("p", null, p)); });
  /* The word list: a setting's choices, or, for the page's own ?s, the words the box uses. Every
     word a newcomer cannot be expected to know is explained here rather than on the form. */
  let notes = (own && own.notes) || (f && f.notes) || null;
  /* The run plan names the card this site actually has, so the word explained is the word on
     the plan: "A100" rather than a general note about graphics cards. */
  if (key === "plan" && HELLO && HELLO.site) {
    const card = ((HELLO.site.gpus || [])[0] || "").toUpperCase();
    if (card) {
      notes = Object.assign({}, notes);
      notes[card] = "A kind of graphics card. One of them draws the structures, and the time "
                  + "beside Sampling is how long that takes.";
    }
  }
  if (notes && Object.keys(notes).length) {
    const list = el("dl", "helpnotes");
    Object.entries(notes).forEach(([k, v]) => {
      list.appendChild(el("dt", null, f && SHORT[f.key] ? SHORT[f.key](k) : k));
      list.appendChild(el("dd", null, v));
    });
    $("helpbody").appendChild(list);
  }
  $("helpdocs").href = (own && own.docs) || DOCS_OF[key] || "/docs/index.html";
  $("helpdlg").showModal();
}
/* Which documentation page each setting's ? links to. */
const DOCS_OF = {
  num_samples: "/docs/resources.html", model_name: "/docs/resources.html", gpu: "/docs/resources.html",
  walltime: "/docs/resources.html", account: "/docs/resources.html", seed: "/docs/resources.html",
  filter_samples: "/docs/resources.html", alignments: "/docs/phases.html", msa_file: "/docs/sequence.html",
  keep_files: "/docs/results.html",
};
/* The page's own explanations, for the parts that are not a setting in the tool's table. */
const HELP = {
  molecule: { title: "The molecule",
    body: "One protein chain. Choose a structure and the chain's sequence is read off the file. "
        + "The file is kept with the run and the samples are measured against it, so the results "
        + "say how far the chain moves from the pose it was in.\n\n"
        + "Choose a structure opens a browser of this computer, your scratch, home, the run folders "
        + "of the lab's tools and your group's folders. A PDB code is fetched here, on the login "
        + "node. A pasted sequence works too; there is then nothing to measure the samples against.\n\n"
        + "There is no alphabet here for DNA or RNA, and no way to give the model a ligand.",
    docs: "/docs/sequence.html" },
  sequence: { title: "Pasting a sequence",
    body: "Any form: plain letters, a FASTA record, wrapped or numbered lines out of a paper. "
        + "Headers, spaces, numbers and line breaks are dropped and the letters are read as one "
        + "chain of the twenty amino acids.",
    docs: "/docs/sequence.html" },
  variant: { title: "Variants",
    body: "A variant is the same chain with mutations, written as W23A in the structure's own "
        + "numbering, several separated by spaces. Each molecule is sampled in its own job and the "
        + "runs know about each other, so a mutant can be put against its wild type when both have "
        + "finished.",
    docs: "/docs/sequence.html" },
  against: { title: "Compare with a run",
    body: "A HOPE-MD run of the same chain, or another ensemble from this page. The comparison "
        + "says which sampled shapes that run came within 0.3 nm of, whether the two agree about "
        + "which residues move, and how far their spreads overlap.\n\n"
        + "It runs in the same job, once the samples have been read, so nothing is queued twice.",
    docs: "/docs/comparing.html" },
  ensemble: { title: "What an ensemble is not",
    body: "So nothing is plotted against the sample number, and the shapes are drawn overlaid "
        + "rather than played.",
    docs: "/docs/shapes.html" },
  plan: { title: "The run plan",
    body: "What this run will do, in order, with where each step happens and roughly how long it "
        + "takes. Nothing is queued until you press Submit.",
    notes: {
      "Alignment": "A search for related sequences. The model reads one to work out what this "
                 + "chain can do, so it is found before anything is sampled.",
      "This login node": "The computer you are signed in to. It has a network, which the "
                       + "machines that do the sampling do not, so the search happens here.",
      "Sampling": "Drawing the structures themselves, in a job the cluster runs when a machine "
                + "is free.",
      "One GPU": "One graphics card in the cluster, of the kind named beside it. That is what "
               + "the time and the queueing are for.",
    },
    docs: "/docs/phases.html" },
  numbers: { title: "The numbers at the top",
    body: "What was drawn, and how far it moved.",
    notes: {
      "Samples": "How many structures the run drew.",
      "Shapes": "How many groups of alike structures they fall into.",
      "Residues": "How long the chain is.",
      "Radius of gyration": "How spread out the chain is, in nanometres: a small number is a "
                          + "tight ball, a larger one is extended. The ± is how much it varied.",
      "From the mean shape": "How far a sample sits from the average of them all, on average. "
                           + "The bigger it is, the more this chain moves.",
      "From the structure given": "How far the samples sit from the structure you gave, and how "
                                + "close the nearest of them came to it.",
    },
    docs: "/docs/results.html" },
  shapes: { title: "The shapes in this ensemble",
    body: "The structures are grouped by how alike they are, and each group is a shape. Press a "
        + "row to draw that one in color.",
    notes: {
      "Shape": "The groups in order, the most populated first.",
      "Share of samples": "How much of the run came out in this shape.",
      "Samples": "How many structures are in it.",
      "Width": "How much the structures within one shape differ, in nanometres. A small width "
             + "is a tight group.",
      "Sample": "Which structure of the run is drawn for this shape.",
      "In the viewer": "Whether it is drawn. A group of one or two is not.",
    },
    docs: "/docs/shapes.html" },
  moves: { title: "Which residues move",
    body: "One value for each residue of the chain: how far that residue sits from where it sits "
        + "in the average structure, in nanometres. A peak is a part of the chain that moves; a "
        + "flat stretch is a part that holds still.\n\n"
        + "The dashed line is the same measured against the structure you gave, where there is "
        + "one. Residue number runs along the bottom; nothing here is plotted against time.",
    docs: "/docs/results.html" },
};
document.addEventListener("click", (e) => {
  const q = e.target.closest && e.target.closest("[data-help]");
  if (q) { e.preventDefault(); openHelp(q.dataset.help); }
});
$("helpclose").onclick = () => $("helpdlg").close();
$("helpx").onclick = () => $("helpdlg").close();

/* ------------------------------------------------------------------ the settings */
function spinArrows(onStep, upSays, downSays, of) {
  const box = el("span", "arrows");
  [["▴", 1, upSays], ["▾", -1, downSays]].forEach(([glyph, way, says]) => {
    const button = el("button", "arrow", glyph);
    button.type = "button"; button.tabIndex = -1; button.title = says;
    button.setAttribute("aria-label", of ? says + " " + String(of).toLowerCase() : says);
    button.onclick = () => onStep(way);
    box.appendChild(button);
  });
  return box;
}
function asWalltime(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60;
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}
function walltimeSeconds(text) {
  const parts = String(text || "").trim().split(":").map(Number);
  if (!parts.length || parts.some((n) => !Number.isFinite(n))) return NaN;
  while (parts.length < 3) parts.push(0);
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}

function helpButton(key, label) {
  const b = el("button", "q", "?");
  b.type = "button";
  b.dataset.help = key;
  b.setAttribute("aria-label", "About " + (label || key).toLowerCase());
  return b;
}

function shortOf(f, choice) {
  return SHORT[f.key] ? SHORT[f.key](choice) : String(choice);
}

function fieldNode(f) {
  const wrap = el("div", "fld");
  wrap.dataset.key = f.key;
  if (f.kind === "yesno") {
    const row = el("div", "tickrow");
    const tick = el("label", "tick");
    const input = document.createElement("input");
    input.type = "checkbox"; input.id = f.key; input.checked = !!f.default;
    tick.appendChild(input);
    tick.appendChild(document.createTextNode(" " + f.label));
    row.appendChild(tick);
    if (f.explain || f.why) row.appendChild(helpButton(f.key, f.label));
    wrap.appendChild(row);
    return wrap;
  }
  const label = el("label", "f");
  label.htmlFor = f.key;
  label.appendChild(el("span", "fname", f.label));
  if (f.explain || f.why) label.appendChild(helpButton(f.key, f.label));
  wrap.appendChild(label);

  if (f.kind === "choice") {
    if (f.choices.length && f.choices.length <= 3) {
      /* A short list is a row of chips: one press rather than two, every choice visible. */
      const seg = el("div", "seg");
      seg.id = f.key;
      seg.setAttribute("role", "radiogroup");
      seg.setAttribute("aria-label", f.label);
      seg.dataset.value = f.default == null ? "" : String(f.default);
      f.choices.forEach((choice) => {
        const on = String(f.default) === String(choice);
        const b = el("button", "chip" + (on ? " on" : ""), shortOf(f, choice));
        b.type = "button";
        b.dataset.value = choice;
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", on ? "true" : "false");
        const note = (f.notes || {})[choice];
        b.title = note ? shortOf(f, choice) + ": " + note : String(choice);
        b.onclick = () => {
          seg.dataset.value = choice;
          seg.querySelectorAll(".chip").forEach((o) => {
            o.classList.toggle("on", o === b);
            o.setAttribute("aria-checked", o === b ? "true" : "false");
          });
          seg.dispatchEvent(new Event("change", { bubbles: true }));
        };
        seg.appendChild(b);
      });
      wrap.appendChild(seg);
    } else {
      const select = el("select", "inp");
      select.id = f.key;
      f.choices.forEach((choice) => {
        const option = new Option(shortOf(f, choice), choice);
        if ((f.notes || {})[choice]) option.title = f.notes[choice];
        select.appendChild(option);
      });
      select.value = f.default == null ? "" : String(f.default);
      wrap.appendChild(select);
    }
  } else if (f.kind === "number" || f.kind === "duration") {
    const spin = el("div", "spin");
    const input = el("input", "inp");
    input.id = f.key;
    input.type = f.kind === "number" ? "number" : "text";
    input.autocomplete = "off";
    if (f.kind === "duration") input.inputMode = "numeric";
    if (f.kind === "number") {
      if (f.least != null) input.min = String(f.least);
      if (f.most != null) input.max = String(f.most);
      if (f.step != null) input.step = String(f.step);
    }
    input.placeholder = capital(f.placeholder || (f.optional ? "optional" : ""));
    if (f.default != null && f.default !== "") input.value = String(f.default);
    const by = Number(f.step) || (f.kind === "duration" ? 1800 : 1);
    const step = (way) => {
      if (f.kind === "duration") {
        const now = walltimeSeconds(input.value);
        let want = (Number.isFinite(now) ? now : (Number(f.least) || 3600)) + way * by;
        if (f.least != null) want = Math.max(Number(f.least), want);
        if (f.most != null) want = Math.min(Number(f.most), want);
        input.value = asWalltime(want);
      } else {
        const from = input.value === "" ? (f.least != null ? Number(f.least) : 0) : Number(input.value);
        let want = (Number.isFinite(from) ? from : 0) + way * by;
        if (f.least != null) want = Math.max(Number(f.least), want);
        if (f.most != null) want = Math.min(Number(f.most), want);
        input.value = String(Math.round(want));
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    spin.appendChild(input);
    spin.appendChild(spinArrows(step, f.kind === "duration" ? "Longer" : "More",
                                f.kind === "duration" ? "Shorter" : "Less",
                                f.kind === "duration" ? "" : f.label));
    wrap.appendChild(spin);
  } else {
    const input = el("input", "inp");
    input.id = f.key;
    input.autocomplete = "off";
    input.spellcheck = false;
    if (f.default != null && f.default !== "") input.value = String(f.default);
    else input.placeholder = capital(f.placeholder || (f.optional ? "optional" : ""));
    if (f.key === "msa_file") {
      input.placeholder = "An .a3m on the cluster";
      const line = el("div", "withbtn");
      line.appendChild(input);
      const pick = el("button", "btn sm", "Browse");
      pick.type = "button";
      pick.id = "msabrowse";
      pick.onclick = chooseAlignment;
      line.appendChild(pick);
      wrap.appendChild(line);
    } else {
      wrap.appendChild(input);
    }
  }
  const err = el("p", "errmsg");
  err.id = f.key + "-err";
  err.hidden = true;
  wrap.appendChild(err);
  return wrap;
}
function capital(text) { const t = String(text || ""); return t.charAt(0).toUpperCase() + t.slice(1); }

function valueOf(key) {
  const node = $(key);
  if (!node) return undefined;
  if (node.classList && node.classList.contains("seg")) return node.dataset.value;
  return node.type === "checkbox" ? node.checked : node.value;
}

function listen(node) {
  const kind = (node.tagName === "SELECT" || node.type === "checkbox" || node.classList.contains("seg")) ? "change" : "input";
  node.addEventListener(kind, () => { paintAlignmentFile(); paintPresets(); later(); });
}

let typing = 0;
function later(ms) { clearTimeout(typing); typing = setTimeout(check, ms || 280); }

/* Every setting, each drawn once: the resources in the rail, the sampling ones in the Sampling
   card, the advanced ones under More options there, Keep every file under After the run. */
function buildSettings() {
  const byKey = {};
  FIELDS.forEach((f) => { byKey[f.key] = f; });
  const placed = new Set();

  const res = $("fields-res");
  res.innerHTML = "";
  RESOURCE.forEach((key) => {
    if (!byKey[key]) return;
    res.appendChild(fieldNode(byKey[key]));
    placed.add(key);
  });

  buildPresets();
  const rest = $("sampling-rest");
  rest.innerHTML = "";
  IN_SAMPLING.forEach((key) => {
    const f = byKey[key];
    if (!f) return;
    const node = fieldNode(key === "num_samples" ? Object.assign({}, f, { label: "Structures" }) : f);
    if (key === "num_samples") node.classList.add("num");
    if (key === "msa_file") node.classList.add("wide");
    rest.appendChild(node);
    placed.add(key);
  });

  IN_AFTER.forEach((key) => placed.add(key));
  const more = $("fields-more");
  more.innerHTML = "";
  let count = 0;
  FIELDS.forEach((f) => {
    if (placed.has(f.key)) return;
    more.appendChild(fieldNode(f));
    count += 1;
  });
  $("moreopts").hidden = !count;
  $("morecount").textContent = count ? "(" + count + (count === 1 ? " setting)" : " settings)") : "";

  FIELDS.concat([{ key: "name" }, { key: "out" }]).forEach((f) => {
    if (IN_AFTER.has(f.key)) return;
    const node = $(f.key);
    if (node) listen(node);
  });
  paintAlignmentFile();
  paintPresets();
}

/* The alignment file box is only read when Alignment says to read it, so it is only shown then. */
function paintAlignmentFile() {
  const where = $("msa_file");
  if (!where) return;
  const wrap = where.closest(".fld");
  if (wrap) wrap.hidden = valueOf("alignments") !== "file";
}

/* ------------------------------------------------------------------ the presets
   How many structures to draw is one setting, so it has one control at a time: the tiles, or,
   behind the Custom tile, the box for somebody who wants 437. Both at once on the one card was
   the same number shown twice, which is the thing the form was rejected for. */
let CUSTOM = false;
function buildPresets() {
  const box = $("presets");
  box.innerHTML = "";
  PRESETS.forEach((p) => {
    const b = el("button", "ptile");
    b.type = "button";
    b.dataset.samples = p.samples;
    b.title = p.why;
    b.appendChild(el("b", null, p.label));
    b.appendChild(el("span", "n", p.samples.toLocaleString() + " structures"));
    b.appendChild(el("span", "cost", "about " + p.minutes + " min"));
    b.onclick = () => { CUSTOM = false; setSamples(p.samples); check(); };
    box.appendChild(b);
  });
  const own = el("button", "ptile custom");
  own.type = "button";
  own.title = "Any number of structures";
  own.appendChild(el("b", null, "Custom"));
  own.appendChild(el("span", "n", "your own number"));
  own.appendChild(el("span", "cost", ""));
  own.onclick = () => {
    CUSTOM = true;
    paintPresets();
    const field = $("num_samples");
    if (field) { field.focus(); try { field.select(); } catch (e) { /* not selectable */ } }
  };
  box.appendChild(own);
}
function paintPresetCosts(plan) {
  const minutes = (plan && plan.preset_minutes) || {};
  document.querySelectorAll("#presets .ptile").forEach((t) => {
    if (t.classList.contains("custom")) {
      t.querySelector(".cost").textContent = plan && plan.minutes ? "about " + plan.minutes + " min" : "";
      return;
    }
    const p = PRESETS.find((one) => String(one.samples) === t.dataset.samples);
    const got = minutes[t.dataset.samples] || (p && p.minutes);
    if (got) t.querySelector(".cost").textContent = "about " + got + " min";
  });
}
function setSamples(n) {
  const box = $("num_samples");
  if (box) box.value = String(n);
  paintPresets();
}
function paintPresets() {
  const now = Number(valueOf("num_samples") || 0);
  let matched = false;
  document.querySelectorAll("#presets .ptile:not(.custom)").forEach((t) => {
    const on = Number(t.dataset.samples) === now;
    if (on) matched = true;
    t.classList.toggle("on", on);
    t.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const own = document.querySelector("#presets .ptile.custom");
  const open = CUSTOM || !matched;
  if (own) {
    own.classList.toggle("on", open);
    own.setAttribute("aria-pressed", open ? "true" : "false");
    own.querySelector(".n").textContent = open && now
      ? now.toLocaleString() + " structures" : "your own number";
  }
  const field = $("num_samples");
  const wrap = field && field.closest(".fld");
  if (wrap) wrap.hidden = !open;
}

/* ------------------------------------------------------------------ the molecule */
const FILEICON = '<svg class="pic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 13c1.5-2 4.5-2 6 0M9 16c1.5 2 4.5 2 6 0"/></svg>';
const SEQICON = '<svg class="pic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h16M4 12h16M4 16h10"/></svg>';

function showSource(which) {
  $("pickzone").hidden = which !== "zone";
  $("codepane").hidden = which !== "code";
  $("pastepane").hidden = which !== "paste";
  if (which === "code") setTimeout(() => $("pdbid").focus(), 0);
  if (which === "paste") setTimeout(() => $("seq").focus(), 0);
}

function sourceSays(text, bad) {
  $("src-err").hidden = !bad;
  $("src-err").textContent = bad ? firstSentence(text) : "";
  $("src-err").title = bad ? String(text || "") : "";
  $("srcsays").hidden = !!bad || !text;
  $("srcsays").textContent = bad ? "" : (text || "");
}

function drawMolecules() {
  const box = $("molecules");
  /* The cards are drawn again after every check, which is while somebody may be typing a name or
     a mutation into one: the box they were in gets the focus back, with the caret where it was. */
  const was = document.activeElement;
  const keep = was && box.contains(was) && (was.id || was.dataset.key)
    ? { id: was.id, key: was.dataset.key, from: was.selectionStart, to: was.selectionEnd } : null;
  box.innerHTML = "";
  MOLECULES.forEach((m, n) => box.appendChild(moleculeCard(m, n)));
  if (keep) {
    const again = keep.id ? $(keep.id) : box.querySelector('[data-key="' + keep.key + '"]');
    if (again) {
      again.focus({ preventScroll: true });
      try { if (keep.from != null) again.setSelectionRange(keep.from, keep.to); } catch (e) { /* not a text box */ }
    }
  }
  $("molacts").hidden = !MOLECULES.length || !MOLECULES[0].structure && !MOLECULES[0].sequence && !MOLECULES[0].pasted;
  $("addvariant").disabled = !MOLECULES.length;
  $("molnote").textContent = MOLECULES.length > 1 ? MOLECULES.length + " molecules, one job each" : "";
  if (!MOLECULES.length) showSource($("codepane").hidden && $("pastepane").hidden ? "zone" : ($("codepane").hidden ? "paste" : "code"));
  else showSource("none");
}

/* Where in the sequence a mutation lands, counted from the residue the chain starts at: W23A on
   a chain that starts at 25 is not the 23rd letter of it. */
function mutationSpots(m, length) {
  const first = m.first || 1;
  return (m.mutations || []).map((one) => Number(String(one).replace(/[^0-9]/g, "")) - first)
    .filter((n) => Number.isFinite(n) && n >= 0 && n < length);
}
/* A stretch of the sequence, with the mutated residues drawn in color inside it. */
function writeSeq(into, seq, from, to, spots) {
  let at = from;
  spots.filter((n) => n >= from && n < to).sort((x, y) => x - y).forEach((n) => {
    if (n > at) into.appendChild(document.createTextNode(seq.slice(at, n)));
    into.appendChild(el("b", null, seq[n]));
    at = n + 1;
  });
  if (to > at) into.appendChild(document.createTextNode(seq.slice(at, to)));
}
function peek(m) {
  const seq = m.sequence || m.pasted || "";
  const b = el("button", "seqpeek");
  b.type = "button";
  const first = m.first || 1;
  const long = seq.length > 64;
  const spots = mutationSpots(m, seq.length);
  const draw = (all) => {
    b.innerHTML = "";
    if (all || !long) {
      writeSeq(b, seq, 0, seq.length, spots);
    } else {
      /* The start, the end, and a window around every mutated residue. A variant differs from
         its parent in one place, and that place was inside the middle this leaves out: both
         cards showed the same letters, and only the W23A tag told them apart. */
      const keep = [[0, 40], [Math.max(0, seq.length - 14), seq.length]]
        .concat(spots.map((n) => [Math.max(0, n - 6), Math.min(seq.length, n + 7)]))
        .sort((x, y) => x[0] - y[0]);
      const runs = [];
      keep.forEach((one) => {
        const last = runs[runs.length - 1];
        if (last && one[0] <= last[1]) last[1] = Math.max(last[1], one[1]);
        else runs.push([one[0], one[1]]);
      });
      runs.forEach((one, n) => {
        if (n) b.appendChild(document.createTextNode(" … "));
        writeSeq(b, seq, one[0], one[1], spots);
      });
    }
    b.appendChild(el("span", "ix", "  " + first + "–" + (first + seq.length - 1)));
    b.classList.toggle("all", all || !long);
  };
  draw(false);
  b.title = long ? "Show the whole sequence" : "";
  b.setAttribute("aria-label", "Sequence, " + seq.length + " residues");
  let all = false;
  b.onclick = () => { if (!long) return; all = !all; draw(all); b.title = all ? "Show less" : "Show the whole sequence"; };
  return b;
}

function moleculeCard(m, n) {
  const card = el("div", "picked" + (m.ok ? " ok" : ""));
  card.dataset.n = n;
  const head = el("div", "picked-head");
  const icon = document.createElement("span");
  icon.innerHTML = m.structure ? FILEICON : SEQICON;
  head.appendChild(icon.firstChild);
  const name = el("input", "picked-name");
  name.dataset.key = "molname" + n;
  name.value = m.name || "";
  name.placeholder = "Name";
  name.setAttribute("aria-label", "Name of molecule " + (n + 1));
  const fit = () => { name.size = Math.max(6, Math.min(32, (name.value || name.placeholder).length + 2)); };
  fit();
  name.oninput = () => { m.name = name.value; fit(); later(); };
  head.appendChild(name);
  const from = m.variant ? "variant of " + (m.what || "the structure")
    : m.structure ? (m.file || "") + (m.chains && m.chains.length > 1 ? " · chain " + m.chain : "")
    : "pasted sequence";
  const fromNode = el("span", "picked-from", from);
  fromNode.title = m.structure || "";
  head.appendChild(fromNode);
  if (m.mutations && m.mutations.length) head.appendChild(el("span", "tag mut", m.mutations.join(" ")));
  const act = el("button", "btn sm", m.variant ? "Remove" : "Change");
  act.type = "button";
  act.onclick = () => {
    if (m.variant) MOLECULES.splice(n, 1); else { MOLECULES = []; sourceSays(""); }
    drawMolecules(); check();
    if (!MOLECULES.length) $("choose").focus();
  };
  head.appendChild(act);
  card.appendChild(head);

  /* The chains of the structure, so the one to sample is a press rather than a guess. A chain
     the tool cannot sample is shown dashed, with the reason as its tooltip. */
  if (m.chains && m.chains.length && !m.variant) {
    const row = el("div", "pchains");
    row.setAttribute("role", "radiogroup");
    row.setAttribute("aria-label", "Chain to sample");
    m.chains.forEach((c) => {
      const why = (m.why_not || {})[c.id] || "";
      const on = c.id === m.chain;
      const pick = el("button", "pchain");
      pick.type = "button";
      pick.setAttribute("role", "radio");
      pick.setAttribute("aria-pressed", on ? "true" : "false");
      pick.setAttribute("aria-checked", on ? "true" : "false");
      pick.dataset.chain = c.id;
      pick.appendChild(el("b", null, c.id));
      pick.appendChild(el("span", "pk", "· " + c.kind + " · " + c.n + " aa"));
      pick.disabled = !!why;
      pick.title = why ? firstSentence(why) : "Sample chain " + c.id;
      pick.onclick = () => { if (why || on) return; m.chain = c.id; useChain(m); };
      row.appendChild(pick);
    });
    card.appendChild(row);
  }

  /* A variant is the same chain with mutations written in the structure's own numbering. */
  if (m.variant) {
    const line = el("div", "mutrow");
    const label = el("label", null, "Mutations");
    label.htmlFor = "mut" + n;
    line.appendChild(label);
    const input = el("input", "inp");
    input.id = "mut" + n;
    input.value = m.mutations_text || "";
    input.placeholder = "W23A";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.oninput = () => { m.mutations_text = input.value; later(320); };
    line.appendChild(input);
    line.appendChild(helpButton("variant", "variants"));
    card.appendChild(line);
  }
  if (m.sequence || m.pasted) card.appendChild(peek(m));
  const note = el("p", "errmsg");
  note.id = "molerr" + n;
  note.hidden = true;
  card.appendChild(note);
  if (m.notes && m.notes.length) {
    const said = el("p", "note warn", firstSentence(m.notes[0]));
    said.title = m.notes.join("\n\n");
    card.appendChild(said);
  }
  return card;
}

async function readStructure(body) {
  let got;
  try { got = await api("/api/structure", body); }
  catch (failure) { throw new Error(firstSentence(failure.message)); }
  const picked = got.chains.find((c) => c.id === got.chain) || got.chains[0] || {};
  MOLECULES = [{
    name: got.name, structure: got.path, file: (got.path || "").split("/").pop(),
    chains: got.chains, chain: got.chain, why_not: got.why_not, sequence: got.sequence,
    first: picked.first || 1, residues: picked.n || 0, variant: false, mutations_text: "",
    what: "chain " + (picked.id || "?") + " of " + got.name,
  }];
  sourceSays("");
  drawMolecules();
  check();
  return got;
}

function useChain(m) {
  const picked = (m.chains || []).find((c) => c.id === m.chain);
  if (picked) {
    m.sequence = picked.sequence;
    m.first = picked.first;
    m.residues = picked.n;
    m.what = "chain " + picked.id + " of " + (m.name || "the structure");
  }
  drawMolecules();
  check();
}

function sendFile(file) {
  return new Promise((resolve, reject) => {
    if (!/\.(pdb|ent|cif|mmcif)$/i.test(file.name)) {
      reject(new Error("A structure is a .pdb or a .cif file."));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read on this computer."));
    reader.onload = () => {
      const data = String(reader.result || "").split(",")[1] || "";
      readStructure({ kind: "file", filename: file.name, data: data }).then(resolve, reject);
    };
    reader.readAsDataURL(file);
  });
}

/* The structure browser, shared by every page of ours (/static/picker.js). */
function chooseStructure(startWith) {
  if (!window.HLPicker) { $("file").click(); return; }
  HLPicker.open({
    title: "Choose a structure", key: "bioemu-structure", api: api,
    places: "/api/places", browse: "/api/browse?want=structure",
    accept: ".pdb,.ent,.cif,.mmcif", kinds: STRUCTURE_KINDS,
    acceptSays: "A .pdb or .cif file.",
    onFile: (file) => sendFile(file),
    onPath: (path) => readStructure({ kind: "path", path: path }),
    onCode: (code) => readStructure({ kind: "pdb", id: code }),
    onRun: (folder) => readStructure({ kind: "run", path: folder }),
    startWith: startWith,
  });
}
function chooseAlignment() {
  HLPicker.open({
    title: "Choose an alignment", key: "bioemu-a3m", api: api,
    places: "/api/places", browse: "/api/browse?want=alignment", kinds: [".a3m"],
    onPath: (path) => { $("msa_file").value = path; check(); return Promise.resolve(); },
  });
}
function chooseFolder(title, useLabel, then, mkdir) {
  HLPicker.open({
    title: title, key: "bioemu-folder", api: api, folders: true, useLabel: useLabel,
    places: "/api/places", browse: "/api/browse?dirs=1", mkdir: mkdir ? "/api/mkdir" : "",
    onFolder: (path) => { then(path); return Promise.resolve(); },
  });
}

$("choose").onclick = () => chooseStructure();
$("codebtn").onclick = () => showSource("code");
$("pastebtn").onclick = () => showSource("paste");
$("codecancel").onclick = () => { showSource("zone"); sourceSays(""); };
$("pastecancel").onclick = () => { showSource("zone"); sourceSays(""); };
$("fetch").onclick = () => {
  const code = $("pdbid").value.trim();
  if (!/^[A-Za-z0-9]{4}$/.test(code)) { sourceSays("A PDB code is four characters, like 1YCR.", true); return; }
  $("fetch").disabled = true;
  sourceSays("Fetching " + code.toUpperCase() + " from the RCSB…");
  readStructure({ kind: "pdb", id: code }).catch((why) => sourceSays(why.message, true))
    .finally(() => { $("fetch").disabled = false; });
};
$("pdbid").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("fetch").click(); } });
$("pdbid").addEventListener("input", () => { $("pdbid").value = $("pdbid").value.toUpperCase(); });
$("file").onchange = () => {
  const f = $("file").files[0];
  $("file").value = "";
  if (f) sendFile(f).catch((why) => sourceSays(why.message, true));
};
/* A file dropped on the card is the same as one chosen on This computer. */
const zone = $("step-what");
["dragenter", "dragover"].forEach((k) => zone.addEventListener(k, (e) => {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
  e.preventDefault(); $("pickzone").classList.add("over");
}));
["dragleave", "drop"].forEach((k) => zone.addEventListener(k, (e) => {
  if (k === "dragleave" && zone.contains(e.relatedTarget)) return;
  $("pickzone").classList.remove("over");
}));
zone.addEventListener("drop", (e) => {
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  e.preventDefault();
  sourceSays("Sending " + f.name + "…");
  sendFile(f).catch((why) => sourceSays(why.message, true));
});
$("seqadd").onclick = () => {
  const text = $("seq").value.trim();
  if (!text) { $("seqsays").textContent = "Paste a sequence first."; return; }
  $("seqsays").textContent = "";
  // A FASTA record names itself: the header's first word is the molecule's name.
  const header = (text.match(/^>\s*(\S+)/) || [])[1] || "";
  MOLECULES = [{ name: header.replace(/[^A-Za-z0-9_.-]+/g, "_").slice(0, 40), sequence: "", pasted: text,
                 first: 1, variant: false, what: "a pasted sequence" }];
  drawMolecules();
  check();
};
$("addvariant").onclick = () => {
  const base = MOLECULES[0];
  if (!base) return;
  MOLECULES.push({
    name: (base.name || "molecule") + " variant", structure: base.structure, file: base.file,
    chains: base.chains, chain: base.chain, why_not: base.why_not, sequence: base.sequence,
    pasted: base.pasted, first: base.first, residues: base.residues, variant: true,
    mutations_text: "", what: base.what,
  });
  drawMolecules();
  check();
  const box = $("mut" + (MOLECULES.length - 1));
  if (box) box.focus();
};

/* ------------------------------------------------------------------ after the run */
function setAgainst(path) {
  $("against").value = path || "";
  $("againstname").textContent = path ? path.split("/").filter(Boolean).pop() : "None chosen";
  $("againstname").title = path || "";
  $("againstname").classList.toggle("set", !!path);
  check();
}
$("opt_against").onchange = () => {
  $("againstrow").hidden = !$("opt_against").checked;
  check();
};
$("againstbrowse").onclick = () => chooseFolder("Choose a run to compare with", "Use this run", setAgainst);
$("opt_keep").onchange = check;
$("outbrowse").onclick = () => chooseFolder("Choose a run folder", "Use this folder",
  (path) => { $("out").value = path; $("wherefolder").open = true; check(); }, true);

/* ------------------------------------------------------------------ the form */
function form() {
  const body = {
    name: $("name").value, out: $("out").value,
    molecules: MOLECULES.map((m) => ({
      name: m.name || "",
      sequence: m.structure ? "" : (m.pasted || m.sequence || ""),
      structure: m.structure || "", chain: m.chain || "",
      mutations: m.mutations_text || "",
    })),
    against: $("opt_against").checked ? $("against").value : "",
    keep_files: $("opt_keep").checked,
  };
  FIELDS.forEach((f) => {
    if (f.key === "keep_files") return;
    const got = valueOf(f.key);
    if (got !== undefined) body[f.key] = got;
  });
  return body;
}

function showErrors(answer) {
  const bad = answer.fields || {};
  FIELDS.concat([{ key: "out" }, { key: "against" }]).forEach((f) => {
    const box = $(f.key + "-err");
    if (!box) return;
    box.textContent = bad[f.key] ? firstSentence(bad[f.key]) : "";
    box.title = bad[f.key] || "";
    box.hidden = !bad[f.key];
    const input = $(f.key);
    if (input && input.classList && input.classList.contains("inp")) input.classList.toggle("bad", !!bad[f.key]);
  });
  const per = bad.molecules || {};
  MOLECULES.forEach((m, n) => {
    const box = $("molerr" + n);
    if (!box) return;
    const all = Object.values(per[String(n)] || {}).filter(Boolean);
    box.textContent = all.length ? firstSentence(all[0]) : "";
    box.title = all.join("\n\n");
    box.hidden = !all.length;
  });
  if (bad.out) $("wherefolder").open = true;
  const list = $("problems");
  list.innerHTML = "";
  (answer.problems || []).forEach((says) => { const li = el("li", null, firstSentence(says)); li.title = says; list.appendChild(li); });
  list.hidden = !(answer.problems || []).length;
}

/* Where the results land, said the way the structure browser names the places: My scratch, Home,
   a group's folder by its name. Anywhere else keeps its last folders behind a …/ rather than
   running the whole path across the rail. */
function shortPath(path) {
  const p = String(path || "");
  const home = (HELLO && HELLO.home) || "";
  const scratch = (HELLO && HELLO.scratch) || "";
  /* The place, then at most the last two folders of it: the one that holds the results and the
     one above it. The whole of it is the tooltip. */
  const under = (place, root) => {
    const tail = p.slice(root.length).split("/").filter(Boolean);
    if (!tail.length) return place;
    return place + "/" + (tail.length > 2 ? "…/" + tail.slice(-2).join("/") : tail.join("/"));
  };
  if (scratch && (p === scratch || p.startsWith(scratch + "/"))) return under("My scratch", scratch);
  if (home && (p === home || p.startsWith(home + "/"))) return under("Home", home);
  const group = p.match(/^\/scratch\/group\/([^/]+)(\/.*)?$/);
  if (group) return under(group[1] + " group", "/scratch/group/" + group[1]);
  const parts = p.split("/").filter(Boolean);
  return parts.length > 3 ? "…/" + parts.slice(-3).join("/") : p;
}

function paintPlan(answer) {
  const plan = answer && answer.plan;
  const box = $("plan");
  if (!MOLECULES.length || !plan || !(plan.phases || []).length) { box.hidden = true; return; }
  const list = $("phases");
  list.innerHTML = "";
  plan.phases.forEach((phase, n) => {
    const li = el("li", "phase");
    li.appendChild(el("span", "pn", String(n + 1)));
    const what = el("span", "pw");
    what.appendChild(el("b", null, phase.step || ""));
    what.appendChild(document.createTextNode(" "));
    what.appendChild(el("span", null, phase.detail || ""));
    li.appendChild(what);
    li.appendChild(el("span", "pt", phase.time || ""));
    li.title = [phase.what, phase.where, phase.why].filter(Boolean).join(". ");
    list.appendChild(li);
  });
  /* The folder, named: the run's own name was being replaced by an ellipsis, which left the one
     line that says where the results go saying nothing a person could go and look in. */
  const name = $("name").value.trim() || answer.name || "the run";
  const base = $("out").value.trim() || ((HELLO && HELLO.roots && HELLO.roots[0]) || "") + "/" + name;
  $("planwhere").textContent = "Results in " + shortPath(base);
  $("planwhere").title = base + (MOLECULES.length > 1 ? ", one folder for each molecule" : "")
    + "\nThe folder carries the date it was queued.";
  box.hidden = false;
}

/* What still has to be done, card by card: the word at the end of each title, the outline on the
   card to do next, the steps along the top, and the one line in the Next bar. */
function paintProgress(answer, ok) {
  const bad = (answer && answer.fields) || {};
  const molBad = !!bad.molecules || !!bad.sequence || !!bad.chain || !!bad.mutations;
  const sampBad = ["num_samples", "msa_file", "model_name", "alignments"].some((k) => bad[k]);
  const afterBad = !!bad.against || ($("opt_against").checked && !$("against").value);
  const resBad = RESOURCE.some((k) => bad[k]) || !!bad.out;
  const state = {
    what: !MOLECULES.length ? "need" : molBad ? "fix" : "done",
    sampling: sampBad ? "fix" : "done",
    after: afterBad ? "need" : ($("opt_against").checked || $("opt_keep").checked ? "on" : "optional"),
    submit: ok ? "ready" : "wait",
  };
  /* Sampling is set before anybody touches it, which is not the same as the run being ready:
     chipped Ready beside a Molecule chipped Needed, it read as though the run could be queued. */
  const words = { need: "Needed", fix: "Fix", done: "Done", set: "Set", on: "On", optional: "Optional", wait: "" };
  [["st-what", state.what], ["st-sampling", state.sampling === "done" ? "set" : state.sampling], ["st-after", state.after]].forEach(([id, s]) => {
    const chip = $(id);
    chip.textContent = words[s] || "";
    chip.className = "cstate" + (s === "need" || s === "fix" ? " need" : (s === "done" || s === "set" || s === "on") ? " done" : "");
  });
  const next = state.what !== "done" ? "what" : state.sampling !== "done" ? "sampling"
    : state.after === "need" ? "after" : resBad ? "submit" : "submit";
  ["what", "sampling", "after"].forEach((k) => $("step-" + k).classList.toggle("is-next", k === next && !ok));
  document.querySelectorAll("#rail .st").forEach((b) => {
    const k = b.dataset.goto;
    const done = k === "what" ? state.what === "done" : k === "sampling" ? state.sampling === "done"
      : k === "after" ? state.after !== "need" : ok;
    const order = ["what", "sampling", "after", "submit"];
    b.classList.toggle("done", done && order.indexOf(k) < order.indexOf(next));
    b.classList.toggle("next", k === next);
    b.setAttribute("aria-current", k === next ? "step" : "false");
  });
  return next;
}

/* What each step is called, for the two lines that point at one rather than repeat what it
   says. A refusal belongs beside the box it is about, and it used to be written there, in the
   Next bar and under Submit at once: one mistyped mutation filled the screen with three copies
   of one sentence. The card keeps the reason; these two say where it is and why Submit waits. */
const CARDNAME = { what: "Molecule", sampling: "Sampling", after: "After the run", submit: "Resources" };

function paintNext(answer, ok, next) {
  const bar = $("nextbar"), text = $("nexttext");
  bar.classList.toggle("ready", !!ok);
  $("nextgo").dataset.goto = next;
  if (!MOLECULES.length) { text.textContent = "Choose a structure, or paste a sequence."; return; }
  if (!ok) {
    const marked = Object.keys((answer && answer.fields) || {}).length;
    if (next === "after" && !marked) { text.textContent = "Choose the run to compare with."; return; }
    if (!marked && ((answer && answer.problems) || []).length) {
      text.textContent = "See what is listed under Submit."; return;
    }
    text.textContent = "Fix what is marked on " + (CARDNAME[next] || "the form") + ".";
    return;
  }
  const n = MOLECULES.length;
  text.textContent = n === 1 ? "Ready. Press Submit to queue the run." : "Ready. Press Submit to queue " + n + " runs.";
}
$("nextgo").onclick = () => {
  const goto = $("nextgo").dataset.goto || "what";
  showCard(goto);
  if (goto === "what" && !MOLECULES.length) setTimeout(() => $("choose").focus({ preventScroll: true }), 350);
  if (goto === "submit") setTimeout(() => { if (!$("go").disabled) $("go").focus({ preventScroll: true }); }, 350);
};

/* Submit is the foot of the rail's own column, so it is on the screen at every text size and
   covers nothing. It used to be held there by position:sticky and let go above three fifths of
   the rail, which at 150% on a laptop left it 454px below the window while the Next bar still
   said to press it. All that is left to do here is the shadow along its top, and only while
   there are settings scrolled under it. */
function holdSubmit() { heldOver(); }
function heldOver() {
  const box = $("step-submit"), rail = $("railscroll");
  if (!box || !rail) return;
  box.classList.toggle("held", rail.scrollHeight - rail.scrollTop - rail.clientHeight > 2);
}
window.addEventListener("resize", holdSubmit);
window.addEventListener("hl-zoom", holdSubmit);
$("railscroll").addEventListener("scroll", heldOver, { passive: true });

function paintSubmit(answer, ok, next) {
  const installed = !!(HELLO && HELLO.installed);
  $("go").disabled = !ok || !installed;
  $("dry").disabled = !ok;
  const why = $("gowhy");
  let says = "";
  if (!MOLECULES.length) says = "Choose a structure or paste a sequence first.";
  else if (!ok) {
    /* The reasons themselves are listed here when the page as a whole is refused; otherwise the
       card carries the reason and this says which card Submit is waiting on. */
    says = ((answer && answer.problems) || []).length ? "" : (CARDNAME[next] || "The form") + " is not ready.";
  } else if (!installed) says = "BioEmu is not installed here, so only a dry run can be written.";
  why.textContent = says;
  why.hidden = !says;
  holdSubmit();
}

let checking = 0;
async function check() {
  const mine = ++checking;
  TOLD = null;
  if (!MOLECULES.length) {
    LASTCHECK = null;
    showErrors({});
    paintPlan(null);
    const next = paintProgress({}, false);
    paintNext({}, false, next);
    paintSubmit({}, false, next);
    return;
  }
  let answer;
  try { answer = await api("/api/check", form()); }
  catch (failure) { flash(failure.message, true); return; }
  if (mine !== checking) return;
  LASTCHECK = answer;
  const ok = !(answer.problems || []).length && !Object.keys(answer.fields || {}).length;
  /* What the server made of each card, back onto the cards: the sequence a structure gave with the
     mutations applied, and the length. The server's sequence, always: kept from the card instead,
     a variant showed its wild type's letters while its own tag said W23A. */
  (answer.molecules || []).forEach((read, n) => {
    const m = MOLECULES[n];
    if (!m) return;
    m.residues = read.residues;
    m.ok = ok;
    if (read.sequence) m.sequence = read.sequence;
    if (!m.structure) m.first = 1;
    m.mutations = read.mutations;
    m.notes = read.notes || [];
    if (!m.name) m.name = read.name;
  });
  drawMolecules();
  showErrors(answer);                 // after the cards exist: drawing them again empties them
  $("srcsays").hidden = true;
  $("name").placeholder = answer.name || "Named for you";
  paintPresetCosts(answer.plan);
  paintPlan(answer);
  const next = paintProgress(answer, ok);
  paintNext(answer, ok, next);
  paintSubmit(answer, ok, next);
}

/* ------------------------------------------------------------------ the job, for the curious */
let TOLDMODE = "input";
document.querySelectorAll("#toldmodes button").forEach((b) => {
  b.onclick = () => {
    TOLDMODE = b.dataset.told;
    document.querySelectorAll("#toldmodes button").forEach((o) => o.classList.toggle("on", o === b));
    paintTold();
  };
});
function paintTold() {
  const text = !TOLD ? "" : TOLDMODE === "command" ? TOLD.command : TOLD.text;
  $("toldtext").textContent = text || "Reading…";
  $("toldwhat").textContent = TOLDMODE === "command" ? "The job script, as it will be queued."
    : "The sequence, as it will be sampled.";
}
$("showjob").onclick = async () => {
  paintTold();
  $("jobdlg").showModal();
  if (TOLD) return;
  try { TOLD = await api("/api/told", form()); }
  catch (failure) { TOLD = { text: failure.message, command: failure.message }; }
  paintTold();
};
$("jobclose").onclick = () => $("jobdlg").close();
$("jobx").onclick = () => $("jobdlg").close();

/* ------------------------------------------------------------------ submitting */
async function submit(dry) {
  $("go").disabled = $("dry").disabled = true;
  $("golast").hidden = false;
  $("golast").textContent = dry ? "Writing the run folder…"
    : "Fetching the alignment. This is the slow part of a first run.";
  let answer;
  try { answer = await api("/api/submit", Object.assign(form(), { dry_run: !!dry })); }
  catch (failure) {
    $("golast").textContent = firstSentence(failure.message);
    $("golast").title = failure.message;
    $("go").disabled = $("dry").disabled = false;
    return;
  }
  if (!answer.run) {
    showErrors(answer);
    $("golast").textContent = "Nothing was queued: see what is marked.";
    $("go").disabled = $("dry").disabled = false;
    return;
  }
  const runs = answer.runs || [answer.run];
  $("golast").textContent = answer.run.job_id ? "Queued as job " + answer.run.job_id + "." : "Written. Nothing was queued.";
  $("golast").title = (answer.said || []).join("\n");
  flash(runs.length > 1
    ? "Queued " + runs.length + " jobs. The Runs tab follows them."
    : (answer.run.job_id ? "Queued as job " + answer.run.job_id + ". The Runs tab follows it."
                         : "Dry run written to " + shortPath(answer.run.path) + "."));
  $("go").disabled = $("dry").disabled = false;
  document.querySelector('.tab[data-screen="runs"]').click();
  loadRuns(answer.run.path);
}
$("go").onclick = () => submit(false);
$("dry").onclick = () => submit(true);

/* ------------------------------------------------------------------ the Runs screen */
let SCOPE = "mine";
let OPEN = "";
let runsTimer = 0;

function dotClass(run) {
  const state = (run.state || "").toUpperCase();
  if (state === "DRY RUN") return "dot dry";
  if (state === "RUNNING") return "dot run";
  if (state === "PENDING" || state === "CONFIGURING") return "dot q";
  if (state === "COMPLETED") return "dot fin";
  if (!state) return run.counts && run.counts.samples ? "dot fin" : "dot unk";
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
      row.type = "button";
      row.appendChild(el("span", dotClass(run)));
      const name = el("span", "name", run.name);
      name.title = run.path;
      row.appendChild(name);
      if (SCOPE !== "mine") row.appendChild(el("span", "ago", run.owner));
      row.appendChild(el("span", "ago", (run.modified || "").slice(5)));
      row.onclick = () => openRun(run.path);
      list.appendChild(row);
    });
    if (open) openRun(open);
    else if (OPEN) openRun(OPEN, true);
    if (!$("screen-runs").hidden) runsTimer = setTimeout(() => loadRuns(), 30000);
  } catch (failure) {
    $("runlist").innerHTML = "";
    $("runlist").appendChild(el("p", "empty", failure.message));
  }
}

/* A box of the results screen: its title and the one ? that explains every word in it. A
   newcomer could not say what Width or Radius of gyration was: the words were explained by a
   tooltip on each, which a touch and a keyboard never reach. */
function boxHead(title, key, label) {
  const row = el("div", "boxhead");
  row.appendChild(el("h3", null, title));
  row.appendChild(helpButton(key, label));
  return row;
}
function tile(key, value, sub) {
  const box = el("div", "tile");
  box.appendChild(el("div", "tk", key));
  box.appendChild(el("div", "big", value));
  if (sub) box.appendChild(el("div", "sub", sub));
  return box;
}
function fileUrl(run, file, save) {
  return "/api/download?t=" + encodeURIComponent(TOKEN) + "&path=" + encodeURIComponent(run.path)
    + "&file=" + encodeURIComponent(file) + (save ? "&save=1" : "");
}
function download(run, file, label) {
  const a = el("a", "btn sm", label);
  a.href = fileUrl(run, file, true);
  return a;
}
function reportButton(run) {
  const a = el("a", "btn sm pri", "Report (PDF)");
  a.href = "/report?t=" + encodeURIComponent(TOKEN) + "&rundir=" + encodeURIComponent(run.path) + "&print=1";
  a.target = "_blank"; a.rel = "noopener";
  a.title = "The write-up of this run, ready to save as a PDF";
  return a;
}

let VIEW = null;
let VIEWAT = -1;
function forgetViewer() { VIEW = null; VIEWAT = -1; }
function paintViewerBackground() {
  if (VIEW) { VIEW.setBackgroundColor(isDark() ? 0x14191c : 0xf7f6f1); VIEW.render(); }
}
const COLORS = ["#0d5c6b", "#c98a3c", "#7a5ea7", "#2f9e6e", "#b4362f", "#1f6f9c", "#8a6d3b", "#555"];

/* The shapes, overlaid rather than played. Every shape faint and the one picked in color on top
   of them: stepped through, these read as a film of a protein moving, which they are not. */
async function drawShapes(run, box, at) {
  if (typeof $3Dmol === "undefined") {
    box.appendChild(el("p", "note", "The viewer could not be loaded. Download has the shapes."));
    return;
  }
  let text;
  try { text = await (await fetch(fileUrl(run, run.ensemble))).text(); }
  catch (failure) { box.appendChild(el("p", "note", "The shapes could not be read.")); return; }
  const models = text.split(/^ENDMDL\s*$/m).map((p) => p.trim()).filter((p) => p.includes("ATOM"));
  box.innerHTML = "";
  VIEW = $3Dmol.createViewer(box, { backgroundColor: isDark() ? 0x14191c : 0xf7f6f1 });
  models.forEach((one, n) => {
    VIEW.addModel(one, "pdb");
    const lit = at < 0 || n === at;
    VIEW.setStyle({ model: n }, { cartoon: {
      color: lit ? COLORS[n % COLORS.length] : "#9aa5a3",
      opacity: lit ? 1 : 0.28, thickness: lit ? 0.4 : 0.2 } });
  });
  VIEW.zoomTo();
  VIEW.render();
  VIEWAT = at;
}
window.addEventListener("hl-zoom", () => { if (VIEW) { VIEW.resize(); VIEW.render(); } });

/* Which shape the viewer is drawing, said on the row of buttons and on the table. Pressing one
   used to read the whole run again, and reading a run forgets which shape was picked, so the
   press put the viewer straight back to all of them overlaid: the one thing there is to do on
   the results screen did nothing at all. */
function litShapes(section, at) {
  section.querySelectorAll(".vbar .pill").forEach((b) => {
    const on = Number(b.dataset.at) === at;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  section.querySelectorAll("table.shapes tr[data-at]").forEach((tr) => {
    tr.classList.toggle("on", Number(tr.dataset.at) === at);
  });
}

function shapesTable(run, onPick) {
  const rows = (run.table && run.table.rows) || [];
  const box = el("div", "tblbox");
  const table = el("table", "shapes");
  const head = el("tr");
  ["", "Shape", "Share of samples", "Samples", "Width", "Sample", "In the viewer"].forEach((h) => head.appendChild(el("th", null, h)));
  table.appendChild(head);
  rows.forEach((row, n) => {
    const tr = el("tr", n === VIEWAT ? "on" : null);
    tr.dataset.at = String(n);
    const dot = el("td");
    const sw = el("span", "swatch");
    sw.style.background = COLORS[n % COLORS.length];
    dot.appendChild(sw);
    tr.appendChild(dot);
    tr.appendChild(el("td", "num", String(row.rank)));
    const share = el("td");
    const bar = el("span", "bar");
    const fill = el("span", "fill");
    fill.style.width = Math.max(2, Math.round(100 * (row.share || 0))) + "%";
    fill.style.background = COLORS[n % COLORS.length];
    bar.appendChild(fill);
    share.appendChild(bar);
    share.appendChild(el("span", "num pc", (100 * (row.share || 0)).toFixed(1) + "%"));
    tr.appendChild(share);
    tr.appendChild(el("td", "num", String(row.samples)));
    tr.appendChild(el("td", "num", (row.spread == null ? "" : row.spread.toFixed(2) + " nm")));
    tr.appendChild(el("td", "num", "#" + row.sample));
    tr.appendChild(el("td", "why", row.in_viewer ? "Yes" : "Too few samples"));
    if (row.in_viewer) { tr.style.cursor = "pointer"; tr.onclick = () => onPick(n); }
    table.appendChild(tr);
  });
  box.appendChild(table);
  return box;
}

/* One value per residue: which parts of this chain move. The lines are drawn to the box and the
   words about them are text on the page. Residue number along the bottom, which is a position
   in a molecule; nothing on this page is plotted against the sample number. */
function lineChart(values, second, first) {
  if (!values || values.length < 3) return null;
  const all = second && second.length >= 3 ? values.concat(second) : values;
  const top = Math.max.apply(null, all) || 1;
  const W = 1000, H = 100;
  const line = (vs) => vs.map((v, n) => (n * (W / (vs.length - 1))).toFixed(1) + "," + (H - 4 - (v / top) * (H - 8)).toFixed(1)).join(" ");
  const ns = "http://www.w3.org/2000/svg";
  const wrap = el("div", "chartbox");
  /* How far, up the left, in the unit it is measured in. Without it the heights said nothing,
     and the largest of them was written in the middle of the residue row, where it read as a
     tick on that row rather than as the top of this scale. */
  const yaxis = el("div", "charty");
  yaxis.appendChild(el("span", null, top.toFixed(2) + " nm"));
  yaxis.appendChild(el("span", null, (top / 2).toFixed(2)));
  yaxis.appendChild(el("span", null, "0"));
  wrap.appendChild(yaxis);
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "How far each residue sits from the mean shape, in nanometres, "
                   + "0 to " + top.toFixed(2) + ", residue " + first + " onward");
  const axis = document.createElementNS(ns, "line");
  axis.setAttribute("x1", 0); axis.setAttribute("y1", H - 4); axis.setAttribute("x2", W); axis.setAttribute("y2", H - 4);
  axis.setAttribute("stroke", "currentColor"); axis.setAttribute("opacity", "0.35");
  axis.setAttribute("vector-effect", "non-scaling-stroke");
  svg.appendChild(axis);
  [[values, "var(--teal)", ""], [second, "var(--rust)", "6 4"]].forEach(([vs, color, dash]) => {
    if (!vs || vs.length < 3) return;
    const poly = document.createElementNS(ns, "polyline");
    poly.setAttribute("points", line(vs));
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", color);
    poly.setAttribute("stroke-width", "2");
    poly.setAttribute("vector-effect", "non-scaling-stroke");
    if (dash) poly.setAttribute("stroke-dasharray", dash);
    svg.appendChild(poly);
  });
  wrap.appendChild(svg);
  const axisText = el("div", "chartaxis");
  axisText.appendChild(el("span", null, "Residue " + first));
  axisText.appendChild(el("span", null, "Residue " + (first + values.length - 1)));
  wrap.appendChild(axisText);
  const key = el("div", "chartkey");
  const k1 = el("span"); k1.appendChild(el("i")); k1.appendChild(document.createTextNode("From the mean shape"));
  key.appendChild(k1);
  if (second && second.length >= 3) {
    const k2 = el("span"); k2.appendChild(el("i", "given")); k2.appendChild(document.createTextNode("From the structure given"));
    key.appendChild(k2);
  }
  wrap.appendChild(key);
  return wrap;
}

async function paintComparisons(run, box) {
  let got;
  try { got = await api("/api/comparisons?path=" + encodeURIComponent(run.path)); }
  catch (failure) { return; }
  box.innerHTML = "";
  const head = el("div", "cmphead");
  head.appendChild(el("h3", null, "Compared with other runs"));
  head.appendChild(helpButton("against", "comparing"));
  const add = el("button", "btn sm", "Compare with a run");
  add.type = "button";
  add.onclick = () => { COMPARING = run; chooseFolder("Choose a run to compare with", "Use this run", queueCompare); };
  head.appendChild(add);
  box.appendChild(head);
  if (!(got.comparisons || []).length) {
    box.appendChild(el("p", "note", "None yet. Press Compare with a run."));
    return;
  }
  (got.comparisons || []).forEach((one) => {
    const card = el("div", "cmp");
    card.appendChild(el("div", "cmpname", one.against));
    if (one.headline) card.appendChild(el("p", "note", one.headline));
    const facts = el("div", "tiles");
    facts.appendChild(tile("Shapes visited", one.visited + " of " + one.of));
    facts.appendChild(tile("Of the spread", (100 * (one.share_visited || 0)).toFixed(0) + "%"));
    facts.appendChild(tile("Residues agree", one.correlation == null ? "–" : one.correlation.toFixed(2)));
    facts.appendChild(tile("Frames read", String((one.simulation || {}).frames_read || "–")));
    card.appendChild(facts);
    card.appendChild(download(run, one.file, "The numbers"));
    box.appendChild(card);
  });
}

let COMPARING = null;
async function queueCompare(md) {
  if (!COMPARING) return;
  try {
    const answer = await api("/api/compare", { path: COMPARING.path, md: md });
    flash(answer.queued ? "Comparison queued as job " + answer.queued + "."
                        : "The comparison job was written and nothing was queued.");
    openRun(COMPARING.path);
  } catch (failure) { flash(firstSentence(failure.message), true); }
}

function disclosure(title, open) {
  const box = el("details", "box disclose");
  box.open = !!open;
  box.appendChild(el("summary", null, title));
  return box;
}

const DRAWN = {};
async function openRun(path, quiet) {
  OPEN = path;
  document.querySelectorAll(".runrow").forEach((row) => row.classList.toggle("on", row.querySelector(".name").title === path));
  const main = $("rundetail");
  if (!quiet) { main.innerHTML = ""; main.appendChild(el("p", "empty", "Reading the run…")); }
  let run;
  try { run = (await api("/api/run?path=" + encodeURIComponent(path))).run; }
  catch (failure) {
    if (quiet) return;
    main.innerHTML = ""; main.appendChild(el("p", "empty", failure.message)); return;
  }
  const looks = JSON.stringify([run.state, run.job_id, run.samples, run.shapes, run.reason]);
  if (quiet && DRAWN[path] === looks && main.querySelector(".runhead")) return;
  DRAWN[path] = looks;
  main.innerHTML = "";
  forgetViewer();

  const head = el("div", "runhead");
  head.appendChild(el("h2", null, run.name));
  const state = run.state || (run.counts && run.counts.samples ? "Finished" : "Not queued");
  head.appendChild(el("span", "cstate" + (/COMPLETED|Finished/i.test(state) ? " done" : ""), capital(String(state).toLowerCase())));
  if (run.job_id) head.appendChild(el("span", "why", "job " + run.job_id));
  if (run.elapsed) head.appendChild(el("span", "why", run.elapsed + " elapsed"));
  if (run.left) head.appendChild(el("span", "why", run.left + " left"));
  if (run.reason) head.appendChild(el("span", "why", run.reason));
  if (run.mutations && run.mutations.length) head.appendChild(el("span", "cstate need", run.mutations.join(" ")));
  main.appendChild(head);

  if (run.headline) main.appendChild(el("p", "headline", run.headline));

  main.appendChild(boxHead("What the run found", "numbers", "the numbers at the top"));
  const tiles = el("div", "tiles");
  tiles.appendChild(tile("Samples", String(run.counts.samples || 0), "drawn"));
  tiles.appendChild(tile("Shapes", String(run.counts.shapes || 0), "found"));
  tiles.appendChild(tile("Residues", String(run.residues || 0), "one chain"));
  const spread = (run.analysis && run.analysis.spread) || {};
  if (spread.rg_mean != null) {
    tiles.appendChild(tile("Radius of gyration", spread.rg_mean.toFixed(2), "nm, ± " + (spread.rg_sd || 0).toFixed(2)));
  }
  if (spread.from_mean_mean != null) {
    tiles.appendChild(tile("From the mean shape", spread.from_mean_mean.toFixed(2), "nm on average"));
  }
  const given = (run.analysis && run.analysis.given) || {};
  if (given.mean_nm != null) {
    tiles.appendChild(tile("From the structure given", given.mean_nm.toFixed(2), "nm · closest " + given.closest_nm.toFixed(2)));
  }
  main.appendChild(tiles);

  const acts = el("div", "acts");
  acts.appendChild(reportButton(run));
  if (run.ensemble) acts.appendChild(download(run, run.ensemble, "Shapes (PDB)"));
  if ((run.files || []).some((f) => f.name.endsWith(".xtc"))) acts.appendChild(download(run, "out/samples.xtc", "Samples (XTC)"));
  if (run.log_name) acts.appendChild(download(run, run.log_name, "Log"));
  if (!run.over && run.job_id) {
    const stop = el("button", "btn sm", "Cancel");
    stop.type = "button";
    stop.onclick = async () => { const a = await api("/api/cancel", { path: run.path }); flash(a.message, !a.ok); loadRuns(run.path); };
    acts.appendChild(stop);
  }
  if (run.over) {
    const again = el("button", "btn sm", "Queue again");
    again.type = "button";
    again.title = "The same job script a second time. The alignment is already in the run folder.";
    again.onclick = async () => { const a = await api("/api/resubmit", { path: run.path }); flash(a.message, !a.ok); loadRuns(run.path); };
    acts.appendChild(again);
  }
  main.appendChild(acts);

  /* The other molecules of the same submission, so a variant and its wild type are one press
     apart rather than two folders somebody has to remember. */
  if ((run.set || {}).others && run.set.others.length) {
    const box = el("div", "box");
    box.appendChild(el("h3", null, "Sampled at the same time"));
    const row = el("div", "row");
    run.set.others.forEach((other) => {
      const b = el("button", "btn sm", other.name + (other.mutations && other.mutations.length ? " · " + other.mutations.join(",") : ""));
      b.type = "button";
      b.onclick = () => openRun(other.path);
      row.appendChild(b);
      const c = el("button", "btn sm quiet", "Compare");
      c.type = "button";
      c.title = "Put this ensemble against " + other.name;
      c.onclick = () => { COMPARING = run; queueCompare(other.path); };
      row.appendChild(c);
    });
    box.appendChild(row);
    main.appendChild(box);
  }

  if ((run.table.rows || []).length) {
    const section = el("div", "box");
    section.appendChild(boxHead("The shapes in this ensemble", "shapes", "the shapes table"));
    const viewer = el("div", "viewer");
    const vbar = el("div", "vbar");
    vbar.appendChild(el("span", "lb", "Show"));
    const pick = (n) => { drawShapes(run, viewer, n); litShapes(section, n); };
    const all = el("button", "pill" + (VIEWAT < 0 ? " on" : ""), "All overlaid");
    all.type = "button";
    all.dataset.at = "-1";
    all.setAttribute("aria-pressed", VIEWAT < 0 ? "true" : "false");
    all.onclick = () => pick(-1);
    vbar.appendChild(all);
    (run.table.rows || []).forEach((row, n) => {
      if (!row.in_viewer) return;
      const b = el("button", "pill" + (n === VIEWAT ? " on" : ""), "Shape " + row.rank);
      b.type = "button";
      b.dataset.at = String(n);
      b.setAttribute("aria-pressed", n === VIEWAT ? "true" : "false");
      b.onclick = () => pick(n);
      vbar.appendChild(b);
    });
    section.appendChild(shapesTable(run, pick));
    section.appendChild(vbar);
    section.appendChild(viewer);
    const said = el("p", "statement", "An ensemble is not a trajectory.");
    said.id = "statement";
    said.appendChild(helpButton("ensemble", "what an ensemble is not"));
    section.appendChild(said);
    main.appendChild(section);
    drawShapes(run, viewer, VIEWAT);
  }

  const rmsf = (run.analysis && run.analysis.rmsf) || [];
  const chart = lineChart(rmsf, given.per_residue, 1);
  if (chart) {
    const section = el("div", "box");
    section.appendChild(boxHead("Which residues move", "moves", "the chart"));
    section.appendChild(chart);
    main.appendChild(section);
  }

  const compare = el("div", "box");
  main.appendChild(compare);
  paintComparisons(run, compare);

  if (run.told && (run.told.text || run.told.command)) {
    const section = disclosure("The job it was given");
    if (run.told.text) section.appendChild(el("pre", "told", run.told.text));
    if (run.told.command) section.appendChild(el("pre", "told", run.told.command));
    main.appendChild(section);
  }
  if (run.log) {
    const failed = run.over && !/COMPLETED/i.test(run.state || "");
    const section = disclosure("The log", failed);
    section.appendChild(el("pre", "log", run.log));
    main.appendChild(section);
  }
}

/* ------------------------------------------------------------------ start */
(async function start() {
  try { HELLO = await api("/api/hello"); }
  catch (failure) {
    document.body.insertBefore(el("p", "banner", failure.message), document.body.children[1] || null);
    return;
  }
  /* The tool's name, and beside it the one muted line saying where the runs go. The app's full
     name carries the site for the docs and the window title; on the header it was said twice,
     "BioEmu on Grace" and then "Grace · runs in …". */
  const site = (HELLO.site && HELLO.site.name) || "";
  const tail = site ? new RegExp("\\s+on\\s+" + site.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$", "i") : null;
  $("appname").textContent = tail ? String(HELLO.app).replace(tail, "") : HELLO.app;
  $("appname").title = HELLO.app;
  const root = (HELLO.roots || [])[0] || "";
  $("where").textContent = site + " · runs in " + shortPath(root);
  $("where").title = HELLO.user + "@" + HELLO.host + "\n" + root;
  FIELDS = HELLO.fields || [];
  PRESETS = HELLO.presets || [];
  buildSettings();
  drawMolecules();
  if (!HELLO.installed) {
    $("notinstalled").hidden = false;
    $("notinstalled").textContent = firstSentence(HELLO.why_not);
    $("notinstalled").title = HELLO.why_not;
  }
  /* Its own line, not the one that says the tool is missing: the two mean different things and
     were written into the same banner, so the page said one of them in the other's words. */
  if (HELLO.dry_run && HELLO.installed) {
    $("dryline").hidden = false;
    $("dryline").textContent = "Dry run: Submit writes the run folder and queues nothing.";
  }
  check();
})();
