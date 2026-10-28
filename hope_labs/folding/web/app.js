/* The folding page, for whichever of the two tools the server was started for.
   Two screens: Fold, which collects what to fold and queues a run, and Runs, which reads the run
   folders, the queue and the structures.

   Nothing about either tool is written here. The types a row may be and the settings are the
   tool's own tables, sent by the server, so a type the tool cannot fold is never offered; and
   every refusal comes from the server, which refuses what the job would fail on, in the words a
   person can act on.

   The form was rejected in review for its type, its paragraphs and its five ways in, so
   it keeps three rules. Every setting is drawn once. Every reason is behind a ?. And the form says
   in one line what to do next. A structure is chosen in the one browser every page of ours shares
   (/static/picker.js), a FASTA too, and a structure's chains become rows. */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const VENDOR = "/static/vendor/";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* The settings that are about the machine rather than the molecule. They live in the rail; every
   other setting lives in the Settings card, and none of them is drawn twice. */
const RESOURCE = ["gpu", "walltime", "account"];
/* The short name a choice is shown by. The longer note is its tooltip, and the ? says the rest. */
const SHORT = {
  alignments: (v) => ({ fetch: "Fetch", single: "Sequence only" }[v] || v),
  output_format: (v) => ({ mmcif: "mmCIF", pdb: "PDB" }[v] || v),
  rank: (v) => ({ auto: "Auto", plddt: "pLDDT", ptm: "pTM", iptm: "ipTM", multimer: "Multimer" }[v] || v),
  gpu: (v) => (v === "auto"
    ? "Auto" + (((HELLO && HELLO.site && HELLO.site.gpus) || [])[0] ? " (" + HELLO.site.gpus[0].toUpperCase() + ")" : "")
    : String(v).toUpperCase()),
};

/* AlphaFold's own pLDDT bands and AlphaFold's own colors. A person reading one of these
   structures has seen this scale before, and inventing another one would only mean they had to
   learn it. */
const PLDDT = [[90, "#0053d6", "very high"], [70, "#65cbf3", "confident"],
               [50, "#ffdb13", "low"], [0, "#ff7d45", "very low"]];

let HELLO = null;
let FIELDS = [];
let ENTITIES = [];
/* Enough to draw the form with before /api/hello answers, which is a moment. No list of suffixes
   here: the server keeps the one list, and a fallback copy of it is a copy that can be wrong.
   Until hello lands the file chooser simply offers every file. */
let LIMITS = { max_copies: 20, max_tokens: 5000, chains: 26, upload: 2097152,
               structure: 10485760 };
let ROWS = [];
/* The structures read so far, one summary card each: the file, how it came, its chains, and which
   of them are on the form. A row read off one carries its id as `struct`. */
let STRUCTS = [];
let STRUCTN = 0;
/* The server's last answer about the form, kept so that a row drawn again, or the modifications
   dialog opened, shows what is wrong with it without waiting for the next check. */
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
function capital(text) { const t = String(text || ""); return t.charAt(0).toUpperCase() + t.slice(1); }

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
/* The key the documentation reads as well, so /docs opens in the theme the page is in, and the
   same key the design pages use, so a person moving between the lab's pages keeps their choice. */
try { if (localStorage.hldesigntheme) document.documentElement.dataset.theme = localStorage.hldesigntheme; } catch (e) { /* private window */ }
theme.onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.hldesigntheme = document.documentElement.dataset.theme; } catch (e) { /* ignore */ }
  paintTheme();
  if (V) { V.setBackgroundColor(cssVar("--card") || "white"); V.render(); }
  if (MV) molstarBackground();
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

/* Show a card: scrolled to, and flashed so the eye finds it. Steps 1 and 2 are cards down the
   form; step 3 is the foot of the rail, which is already on the screen unless the rail is
   scrolling as one column, so that one scrolls the rail rather than the form. */
function showCard(which) {
  const box = $("step-" + which);
  if (!box) return;
  if (which === "submit") {
    const rail = $("railside"), pane = $("railscroll");
    const scroller = rail && rail.classList.contains("railscrolls") ? rail : pane;
    if (scroller) scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  } else {
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  box.classList.remove("flash-on");
  void box.offsetWidth;
  box.classList.add("flash-on");
  setTimeout(() => box.classList.remove("flash-on"), 1400);
}
document.querySelectorAll("#rail .st").forEach((b) => { b.onclick = () => showCard(b.dataset.goto); });

/* ------------------------------------------------------------------ the ? dialog */
/* The page's own explanations, for the parts that are not a setting in the tool's table. A
   setting's reason is its `explain`, which the tool's table carries. */
function toolName() { return HELLO ? HELLO.app.replace(/ on .*/, "") : "The tool"; }
function helpFor(key) {
  const tool = toolName();
  const named = ENTITIES.map(noun);
  const kinds = named.length > 1 ? named.slice(0, -1).join(", ") + " and " + named[named.length - 1] : (named[0] || "protein");
  const topics = {
    what: { title: "What to fold", docs: "/docs/entities.html",
      body: "One row per chain or molecule: " + kinds + ". Copies are more of the same row, so a "
          + "homodimer is one row with two copies and two different chains are two rows.\n\n"
          + "Choose a structure opens a browser of this computer, your scratch, home, the run "
          + "folders of the lab's tools and your group's folders. A structure's chains become rows, "
          + "with their sequences and copies read off the file, and its chips say which chains are "
          + "on the form. A FASTA chosen there becomes a row per record.\n\n"
          + "Paste sequences takes plain letters or FASTA. A PDB code is fetched from the RCSB, here "
          + "on the login node. A file dropped on this card is read the same way."
          + (named.length > 1 ? "\n\n" + tool + " also takes small molecules and ions: add a "
            + "molecule and choose its type." : "") },
    paste: { title: "Pasting sequences", docs: "/docs/entities.html",
      body: "A bare sequence, or FASTA with a header per record. Each record becomes a row, named "
          + "after its header. The type is read off the letters where they settle it, and shown on "
          + "the row so it can be changed." },
    settings: { title: tool + " settings", docs: "/docs/alignments.html",
      body: "These are " + tool + "'s own defaults, and right for most runs. Each one has a ? of "
          + "its own. More options holds the ones few people change." },
    plan: { title: "The run plan", docs: "/docs/alignments.html",
      body: "What happens when you press Submit, step by step.\n\n"
          + "The alignments are fetched first, here on the login node, because a compute node has "
          + "no route to the internet. The page waits for them: that is the minute or two after "
          + "Submit. Then the job is queued, and folds once it has a card. The time beside it is "
          + "roughly how long it runs once it starts, not the wait in the queue.\n\n"
          + "Show the job opens the input file exactly as it will be written, and the command that "
          + "reads it." },
    results: { title: "Taking the results somewhere", docs: "/docs/results.html",
      body: "A folding run is gigabytes once the alignments are in it, so the structures and the "
          + "whole folder are offered separately. Copying on the cluster is much faster than "
          + "through this page, and the lab's work stays on the cluster.\n\n"
          + "Not into your home: it is 10 GB and 10,000 files, and one run is more than both." },
    viewer: { title: "The viewer", docs: "/docs/viewing.html",
      body: "The structure is colored by the model's own confidence, pLDDT, in AlphaFold's four "
          + "bands. Click a residue to see its chain, number and score.\n\n"
          + "Mol* has more ways to look at a structure; 3Dmol.js is quicker to draw." },
  };
  return topics[key];
}
/* Which documentation page each setting's ? links to. */
const DOCS_OF = {
  alignments: "/docs/alignments.html", gpu: "/docs/resources.html", walltime: "/docs/resources.html",
  account: "/docs/resources.html", keep_files: "/docs/runs.html#tidy",
};
function openHelp(key) {
  const f = FIELDS.find((x) => x.key === key);
  const own = helpFor(key);
  $("helptitle").textContent = (own && own.title) || (f && f.label) || key;
  $("helpbody").innerHTML = "";
  const body = (own && own.body) || (f && (f.explain || f.why)) || "";
  String(body).split("\n\n").forEach((p) => { if (p.trim()) $("helpbody").appendChild(el("p", null, p)); });
  if (f && f.notes && Object.keys(f.notes).length) {
    const list = el("dl", "helpnotes");
    Object.entries(f.notes).forEach(([k, v]) => {
      list.appendChild(el("dt", null, SHORT[f.key] ? SHORT[f.key](k) : k));
      list.appendChild(el("dd", null, capital(v)));
    });
    $("helpbody").appendChild(list);
  }
  $("helpdocs").href = (own && own.docs) || DOCS_OF[key] || "/docs/getting-started.html";
  $("helpdlg").showModal();
}
document.addEventListener("click", (e) => {
  const q = e.target.closest && e.target.closest("[data-help]");
  if (q) { e.preventDefault(); openHelp(q.dataset.help); }
});
$("helpclose").onclick = () => $("helpdlg").close();
$("helpx").onclick = () => $("helpdlg").close();
function helpButton(key, label) {
  const b = el("button", "q", "?");
  b.type = "button";
  b.dataset.help = key;
  b.setAttribute("aria-label", "About " + String(label || key).toLowerCase());
  return b;
}

/* ------------------------------------------------------------------ the rows */
function entityOf(kind) { return ENTITIES.find((e) => e.kind === kind) || ENTITIES[0]; }

/* The caret position, counted in characters of the plain text: the only measure that survives
   the box being drawn again. */
function caretOffset(box) {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount || !box.contains(sel.anchorNode)) return null;
  const r = sel.getRangeAt(0).cloneRange();
  r.selectNodeContents(box);
  r.setEnd(sel.getRangeAt(0).endContainer, sel.getRangeAt(0).endOffset);
  return r.toString().length;
}
function setCaret(box, off) {
  if (off == null) return;
  const walk = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
  let n, seen = 0;
  while ((n = walk.nextNode())) {
    if (seen + n.length >= off) {
      const r = document.createRange();
      r.setStart(n, Math.max(0, off - seen));
      r.collapse(true);
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      return;
    }
    seen += n.length;
  }
  const r = document.createRange();
  r.selectNodeContents(box); r.collapse(false);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
}

/* The sequence, in groups of ten with the residue number over each group, and every letter the
   tool has no residue for marked where it is. A modified residue is marked too, with what it
   becomes on hover. A SMILES is one string with no residues in it, so it is shown as typed. */
function renderSeq(box, text, entity, mods) {
  if (!text) { box.innerHTML = ""; return; }
  if (!entity.alphabet) { box.textContent = text; return; }
  const good = new Set(entity.alphabet.split(""));
  const unknown = new Set((entity.unknown || "").split(""));
  const at = new Map((mods || []).map((m) => [m.position, m.ccd]));
  const out = [];
  for (let i = 0; i < text.length; i += 10) {
    const chunk = text.slice(i, i + 10);
    out.push('<span class=grp10 data-n="' + Math.min(i + 10, text.length) + '">'
      + Array.from(chunk).map((c, k) => {
        const p = i + k + 1;
        let cls = good.has(c) ? "" : (unknown.has(c) ? "u" : "x");
        if (at.has(p)) cls = (cls ? cls + " " : "") + "m";
        const says = at.has(p) ? ' title="' + esc(c + p + " becomes " + at.get(p) + modName(entity, at.get(p))) + '"' : "";
        return cls ? '<span class="' + cls + '"' + says + ">" + esc(c) + "</span>" : esc(c);
      }).join("") + "</span>");
  }
  box.innerHTML = out.join("");
}

/* Protein, DNA and RNA are chains: residues to number, modify and count. Everything else is one
   small molecule or ion a copy, which Boltz-2 is given as a ligand. */
const CHAINS = new Set(["protein", "dna", "rna"]);
function isChain(row) { return CHAINS.has(row.kind); }
/* "1ycr.cif chain A": where a row's sequence was read from. The phrase is the server's, carried
   with the row as `from_words` and with a finished run's entities, never spelled again here. The
   page did spell its own, and the two had already come apart on the case the feature is for: a
   row of more than one chain read "1ycr.cif chains A, C" under the row and "chains A and C" in
   the record and the report of the same run. One phrase, one place it is written. */
function fromWords(row) {
  return (row && row.source && row.from_words) || "";
}
function modName(entity, code) {
  const found = (entity.modifications || []).find(([c]) => c === code);
  return found ? ", " + found[1] : "";
}
/* A copy of a row is no longer the file's chain on the form: the chips go on saying which chains
   came in, and they say it about the rows that did. */
function cloneRow(row) {
  return Object.assign({}, row, { struct: null,
    modifications: (row.modifications || []).map((m) => Object.assign({}, m)) });
}
function checkSoon() { later(300); }
/* A type's name in the middle of a sentence: "protein" and "ligand", but "DNA" and "SMILES". */
function noun(entity) {
  return /^[A-Z]{2}/.test(entity.label) ? entity.label.replace(/ Code$/, " code") : entity.label.toLowerCase();
}
/* What a row costs the model, near enough for a walltime: a residue a token, an ion one, and any
   other small molecule a token an atom, about thirty for a typical one. The server counts the
   atoms properly, and its count is what the plan box shows. */
function rowLength(row) {
  if (isChain(row)) return row.sequence.length;
  return !row.sequence ? 0 : (row.kind === "ion" ? 1 : 30);
}
function rowChains(row) { return String((row && row.chain) || "").split(",").filter(Boolean); }

/* A row that stops being the file's chain, by being edited, retyped or taken off, no longer
   counts as that chain on the structure's chips. */
function untag(row) {
  if (!row || !row.struct) return;
  const s = STRUCTS.find((one) => one.id === row.struct);
  if (s) {
    const gone = new Set(rowChains(row));
    s.picked = s.picked.filter((id) => !gone.has(id));
  }
  row.struct = null;
  drawStructs();
}

/* The second line of a row: the sequence the whole width of the card, or for a small molecule
   the list it is picked from, its CCD code, or its SMILES. */
function entryBody(row, i, entity) {
  const body = el("div", "ent-body");
  const what = noun(entity);
  if (entity.given === "pick") {
    const pick = el("select", "inp");
    pick.id = "seq-" + i;
    pick.setAttribute("aria-label", capital(what) + " for row " + (i + 1));
    pick.appendChild(new Option(entity.kind === "ion" ? "Choose an ion…" : "Choose a ligand…", ""));
    (entity.choices || []).forEach(([code, name]) => pick.appendChild(new Option(code + " — " + name, code)));
    pick.value = (entity.choices || []).some(([code]) => code === row.sequence) ? row.sequence : "";
    pick.title = entity.what;
    pick.onchange = () => { row.sequence = pick.value; paintLen(i); paintTotal(); check(); };
    body.appendChild(pick);
  } else if (entity.given === "code") {
    const code = el("input", "inp");
    code.id = "seq-" + i;
    code.maxLength = 5;
    code.spellcheck = false;
    code.autocomplete = "off";
    code.placeholder = entity.placeholder;
    code.value = row.sequence;
    code.title = entity.what;
    code.setAttribute("aria-label", "CCD code for row " + (i + 1));
    code.oninput = () => {
      const at = code.selectionStart;
      code.value = code.value.toUpperCase().replace(/\s+/g, "");
      code.setSelectionRange(at, at);
      row.sequence = code.value;
      paintLen(i); paintTotal(); checkSoon();
    };
    body.appendChild(code);
  } else {
    /* A SMILES has no residues to number, so its box has no room kept for the numbers. */
    const seq = el("div", entity.given === "smiles" ? "seq plain" : "seq");
    seq.contentEditable = "plaintext-only";
    seq.spellcheck = false;
    seq.dataset.ph = entity.placeholder;
    seq.id = "seq-" + i;
    seq.setAttribute("role", "textbox");
    seq.setAttribute("aria-multiline", "true");
    seq.setAttribute("aria-label", (entity.given === "smiles" ? "SMILES" : "Sequence") + " of row " + (i + 1));
    renderSeq(seq, row.sequence, entity, row.modifications);
    seq.addEventListener("input", () => {
      const off = caretOffset(seq);
      const raw = seq.textContent.replace(/\s+/g, "");
      row.sequence = row.kind === "smiles" ? raw : raw.toUpperCase();
      /* A row read off a structure stops claiming to be when its letters are no longer the ones
         that were read. The run's record names a file and a chain of it, and a record that named
         them for a sequence not in that file would be worse than one that named nothing. */
      if (row.source && row.sequence !== row.read) { row.source = null; untag(row); }
      renderSeq(seq, row.sequence, entityOf(row.kind), row.modifications);
      setCaret(seq, off == null ? row.sequence.length : Math.min(off, row.sequence.length));
      paintLen(i); paintTotal(); checkSoon();
    });
    seq.addEventListener("paste", (e) => {
      /* A pasted FASTA record is its sequence: the header is not part of the chain, and pasting
         one in would make a chain of its letters. */
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData).getData("text");
      const only = text.split("\n").filter((l) => l[0] !== ">").join("").replace(/\s+/g, "");
      document.execCommand("insertText", false, only);
    });
    body.appendChild(seq);
  }
  const len = el("div", "seqlen");
  len.id = "len-" + i;
  body.appendChild(len);
  const err = el("p", "errmsg");
  err.id = "rowerr-" + i;
  err.hidden = true;
  body.appendChild(err);
  return body;
}

/* Two arrows on a box. The browser's own are a few pixels of target clipped against the border
   and on a touchpad they are unhittable, so they are drawn; see .spin in the shared stylesheet.
   Out of the tab order, because a number input already has up and down from the keyboard. */
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

function drawRows() {
  const box = $("rows");
  box.innerHTML = "";
  const oneKind = ENTITIES.length <= 1;
  ROWS.forEach((row, i) => {
    const entity = entityOf(row.kind);
    const node = el("div", "ent");
    node.dataset.i = String(i);
    const top = el("div", "ent-top");
    top.appendChild(el("span", "entnum", String(i + 1)));

    /* A tool that folds one kind of thing has no type to choose, so it shows none. */
    if (!oneKind) {
      const kind = el("select", "inp kindsel");
      kind.setAttribute("aria-label", "Type of row " + (i + 1));
      ENTITIES.forEach((e) => kind.appendChild(new Option(e.label, e.kind)));
      kind.value = row.kind;
      kind.title = entity.what;
      kind.onchange = () => changeKind(row, kind.value);
      top.appendChild(kind);
    }

    /* The box wears its word, the way Copies does: a box with letters in it and nothing beside
       it to say what they are is a box nobody can name. */
    const nm = el("label", "nmwrap");
    nm.appendChild(document.createTextNode("Name"));
    const name = el("input", "inp namebox");
    name.value = row.name || "";
    name.placeholder = capital(noun(entity)) + " " + (i + 1);
    name.autocomplete = "off";
    name.spellcheck = false;
    name.setAttribute("aria-label", "Name of row " + (i + 1));
    name.title = "What this row is called on the form and in the report. Named for you if left empty.";
    name.oninput = () => { row.name = name.value; later(400); };
    nm.appendChild(name);
    top.appendChild(nm);

    const cp = el("label", "cpwrap");
    cp.appendChild(document.createTextNode("Copies"));
    const spin = el("div", "spin");
    const copies = el("input", "inp");
    copies.type = "number";
    copies.min = "1";
    copies.max = String(LIMITS.max_copies);
    copies.value = String(row.copies);
    copies.setAttribute("aria-label", "Copies of row " + (i + 1));
    copies.title = isChain(row) ? "Each copy is another chain of the same sequence."
      : "Each copy is another molecule of the same kind.";
    copies.oninput = () => {
      row.copies = Math.max(1, Math.min(LIMITS.max_copies, parseInt(copies.value, 10) || 1));
      paintLen(i); paintTotal(); armSpin(); checkSoon();
    };
    spin.appendChild(copies);
    const arrows = spinArrows((by) => {
      /* Read off the box rather than off the row, so an out-of-range number somebody typed steps
         from what they can see. The row keeps the clamped count, which is what the form sends,
         and check_rows is what says the typed one was out of range. */
      const now = parseInt(copies.value, 10) || row.copies;
      const want = Math.max(1, Math.min(LIMITS.max_copies, now + by));
      copies.value = String(want);
      row.copies = want;
      paintLen(i); paintTotal(); armSpin(); checkSoon();
    }, "One more copy", "One fewer copy");
    function armSpin() {
      const now = parseInt(copies.value, 10) || 1;
      arrows.children[0].disabled = now >= LIMITS.max_copies;
      arrows.children[1].disabled = now <= 1;
    }
    armSpin();
    spin.appendChild(arrows);
    cp.appendChild(spin);
    top.appendChild(cp);

    /* Everything else about the row, behind one button. */
    const more = el("button", "pill dots", "⋮");
    more.type = "button";
    more.title = "Modifications, duplicate, delete";
    more.setAttribute("aria-label", "More for row " + (i + 1));
    more.setAttribute("aria-haspopup", "menu");
    more.onclick = (e) => { e.stopPropagation(); openRowMenu(more, i); };
    top.appendChild(more);
    node.appendChild(top);

    node.appendChild(entryBody(row, i, entity));
    box.appendChild(node);
  });
  ROWS.forEach((_r, i) => paintLen(i));
  paintTotal();
  paintSource();
  if (LASTCHECK) showErrors(LASTCHECK);
}

/* A row's type changed. A chain's letters are kept when it becomes another kind of chain:
   somebody who picked DNA for a protein sequence wants the type changed, not the sequence thrown
   away, and the letters are checked again against the new type. What cannot be carried across
   is not: a sequence is not a ligand on the list, nor a CCD code. Modifications belong to the
   kind of chain they were chosen for, so they go, and the person is told. */
function changeKind(row, kind) {
  const was = row.kind;
  const now = entityOf(kind);
  row.kind = kind;
  if (now.given === "pick") {
    const code = row.sequence.toUpperCase();
    row.sequence = (now.choices || []).some(([c]) => c === code) ? code : "";
  } else if (now.given === "code") {
    row.sequence = /^[A-Za-z0-9]{1,5}$/.test(row.sequence) ? row.sequence.toUpperCase() : "";
  } else if (now.given === "sequence") {
    row.sequence = entityOf(was).given === "pick" || entityOf(was).given === "code"
      ? "" : row.sequence.toUpperCase();
  }
  /* On a change of kind the source goes whatever happened to the letters. Everywhere else the
     test is the letters, and that is right: edit a chain and it is no longer the chain in that
     file. Here it is not enough, because protein, DNA and RNA are all given as a sequence, so
     changing one to another only upper-cases what is already upper case. The row would stay a
     DNA row whose record named chain A of a protein structure, which a short peptide of nothing
     but A, C, G, T and N passes both checks to reach. */
  if (row.source && (kind !== was || row.sequence !== row.read)) { row.source = null; untag(row); }
  if ((row.modifications || []).length && kind !== was) {
    flash("Row " + (ROWS.indexOf(row) + 1) + "'s " + row.modifications.length + " modification"
      + (row.modifications.length === 1 ? " was" : "s were") + " for " + entityOf(was).label
      + ", so " + (row.modifications.length === 1 ? "it was" : "they were") + " taken off.");
    row.modifications = [];
  }
  drawRows(); check();
}

/* ---- the row's menu ---- */
let MENU = null;
function closeRowMenu() { if (MENU) { MENU.remove(); MENU = null; } }
document.addEventListener("click", (e) => { if (MENU && !MENU.contains(e.target)) closeRowMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeRowMenu(); });
window.addEventListener("resize", closeRowMenu);
window.addEventListener("scroll", closeRowMenu, true);

function openRowMenu(anchor, i) {
  const row = ROWS[i];
  if (MENU && MENU.dataset.row === String(i)) { closeRowMenu(); return; }
  closeRowMenu();
  const menu = el("div", "rowmenu");
  menu.dataset.row = String(i);
  menu.setAttribute("role", "menu");
  const item = (label, says, act, off) => {
    const b = el("button", "rmitem");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.appendChild(el("span", null, label));
    if (says) b.appendChild(el("small", null, says));
    b.disabled = !!off;
    b.onclick = (e) => { e.stopPropagation(); closeRowMenu(); act(); };
    menu.appendChild(b);
  };
  if (isChain(row)) {
    const n = (row.modifications || []).length;
    item("Modifications…", !row.sequence ? "Add the sequence first"
      : n ? n + " on this " + (row.kind === "protein" ? "chain" : "strand") : "",
    () => openMods(i), !row.sequence);
  }
  if (row.kind === "dna" || row.kind === "rna") {
    item("Reverse complement", row.sequence ? "" : "Add the sequence first",
      () => reverseComplement(i), !row.sequence);
  }
  item("Duplicate", "", () => {
    ROWS.splice(i + 1, 0, cloneRow(row)); drawRows(); check();
  });
  item("Delete", "", () => {
    untag(row);
    ROWS.splice(i, 1);
    drawRows(); check();
  });
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  const below = r.bottom + 4 + menu.offsetHeight < window.innerHeight;
  menu.style.top = (below ? r.bottom + 4 : r.top - 4 - menu.offsetHeight) + "px";
  menu.style.left = Math.max(8, Math.min(window.innerWidth - menu.offsetWidth - 8, r.right - menu.offsetWidth)) + "px";
  MENU = menu;
  const first = menu.querySelector("button:not(:disabled)");
  if (first) first.focus();
}

/* The paired strand, read 5' to 3' as every strand on this form is, put in as the next row with
   the same number of copies: a duplex in two rows, which is how Boltz-2 is given one. */
const PAIRS = { dna: { A: "T", T: "A", C: "G", G: "C", N: "N" },
                rna: { A: "U", U: "A", C: "G", G: "C", N: "N" } };
function reverseComplement(i) {
  const row = ROWS[i];
  const pair = PAIRS[row.kind];
  const odd = Array.from(new Set(Array.from(row.sequence).filter((c) => !pair[c])));
  if (odd.length) {
    flash("Row " + (i + 1) + " has " + odd.join(", ") + " in it, which " + (odd.length === 1 ? "is" : "are")
      + " not " + (row.kind === "dna" ? "a DNA base" : "an RNA base") + ", so it has no pair. Put the strand right first.", true);
    return;
  }
  const other = Array.from(row.sequence).reverse().map((c) => pair[c]).join("");
  ROWS.splice(i + 1, 0, { kind: row.kind, name: row.name ? row.name + "_rc" : "", sequence: other,
                          copies: row.copies, modifications: [] });
  drawRows(); check();
  flash("Row " + (i + 2) + " is the reverse complement of row " + (i + 1) + ", written 5′ to 3′.");
}

/* ---- the modifications dialog ----
   The chain laid out residue by residue, a click on one, and a choice of what it becomes.
   Only the modifications made from that residue are offered, and submit checks the same thing, because Boltz-2 does not: it puts whatever it is given wherever it is told. */
let MODROW = -1;
let MODAT = 0;
function openMods(i) {
  MODROW = i;
  MODAT = 0;
  let dlg = $("mods");
  if (!dlg) {
    dlg = el("dialog", "hlmodal");
    dlg.id = "mods";
    dlg.setAttribute("aria-labelledby", "modstitle");
    document.body.appendChild(dlg);
    dlg.addEventListener("close", () => { MODROW = -1; drawRows(); check(); });
  }
  paintMods();
  if (!dlg.open) dlg.showModal();
}

function paintMods() {
  const dlg = $("mods");
  const row = ROWS[MODROW];
  if (!dlg || !row) return;
  const entity = entityOf(row.kind);
  const mods = row.modifications || (row.modifications = []);
  const at = new Map(mods.map((m) => [m.position, m]));
  const kept = dlg.querySelector(".modgrid");
  const scroll = kept ? kept.scrollTop : 0;
  dlg.innerHTML = "";

  const head = el("div", "mhead");
  const title = el("h2", null, "Modifications");
  title.id = "modstitle";
  title.appendChild(el("span", "why", "Row " + (MODROW + 1) + (row.name ? ", " + row.name : "")
    + ", " + row.sequence.length + (row.kind === "protein" ? " residues" : " bases")));
  head.appendChild(title);
  const shut = el("button", "mx", "×");
  shut.type = "button";
  shut.setAttribute("aria-label", "Close");
  shut.onclick = () => dlg.close();
  head.appendChild(shut);
  dlg.appendChild(head);

  const body = el("div", "mbody");
  body.appendChild(el("p", "why", "Click a residue, then choose what it becomes."));
  const grid = el("div", "modgrid");
  for (let s = 0; s < row.sequence.length; s += 10) {
    const group = el("span", "grp10");
    group.dataset.n = String(Math.min(s + 10, row.sequence.length));
    for (let p = s + 1; p <= Math.min(s + 10, row.sequence.length); p++) {
      const res = el("button", "modres" + (at.has(p) ? " on" : "") + (p === MODAT ? " sel" : ""),
        row.sequence[p - 1]);
      res.type = "button";
      res.dataset.p = String(p);
      res.title = row.sequence[p - 1] + p + (at.has(p) ? " becomes " + at.get(p).ccd + modName(entity, at.get(p).ccd) : "");
      group.appendChild(res);
    }
    grid.appendChild(group);
  }
  grid.onclick = (e) => {
    const res = e.target.closest(".modres");
    if (!res) return;
    MODAT = parseInt(res.dataset.p, 10);
    paintMods();
  };
  body.appendChild(grid);

  const pick = el("div", "modpick");
  if (!MODAT) {
    pick.appendChild(el("span", "why", "No residue chosen yet."));
  } else {
    const letter = row.sequence[MODAT - 1];
    const offer = (entity.modifications || []).filter(([, , parent]) => parent === letter || parent === "*");
    const now = at.get(MODAT);
    pick.appendChild(el("b", null, letter + MODAT));
    const choose = el("select", "inp");
    choose.setAttribute("aria-label", "What " + letter + MODAT + " becomes");
    choose.appendChild(new Option(offer.length ? "Choose a modification…" : "None listed for " + letter, ""));
    offer.forEach(([code, name]) => choose.appendChild(new Option(code + " — " + name, code)));
    choose.appendChild(new Option("Another CCD code…", "*"));
    const other = el("input", "inp");
    other.maxLength = 5;
    other.placeholder = "CCD code";
    other.spellcheck = false;
    other.hidden = true;
    if (now) {
      if (offer.some(([code]) => code === now.ccd)) choose.value = now.ccd;
      else { choose.value = "*"; other.hidden = false; other.value = now.ccd; }
    }
    choose.onchange = () => { other.hidden = choose.value !== "*"; if (!other.hidden) other.focus(); };
    other.oninput = () => { other.value = other.value.toUpperCase().replace(/\s+/g, ""); };
    const apply = el("button", "btn sm pri", now ? "Change" : "Add");
    apply.type = "button";
    apply.onclick = () => {
      const code = choose.value === "*" ? other.value.trim().toUpperCase() : choose.value;
      if (!/^[A-Z0-9]{1,5}$/.test(code)) {
        flash("Choose a modification, or type a CCD code: one to five capital letters and digits.", true);
        return;
      }
      row.modifications = mods.filter((m) => m.position !== MODAT)
        .concat([{ position: MODAT, ccd: code }])
        .sort((a, b) => a.position - b.position);
      paintMods(); checkSoon();
    };
    other.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); apply.click(); } };
    pick.appendChild(choose);
    pick.appendChild(other);
    pick.appendChild(apply);
    if (now) {
      const off = el("button", "btn sm", "Remove");
      off.type = "button";
      off.onclick = () => { row.modifications = mods.filter((m) => m.position !== MODAT); paintMods(); checkSoon(); };
      pick.appendChild(off);
    }
  }
  body.appendChild(pick);

  const list = el("div", "modlist");
  mods.forEach((m) => {
    const chip = el("span", "modchip");
    const go = el("button", "modgo", (row.sequence[m.position - 1] || "?") + m.position + " → " + m.ccd + modName(entity, m.ccd));
    go.type = "button";
    go.title = "Choose this residue";
    go.onclick = () => { MODAT = m.position; paintMods(); };
    const drop = el("button", "modx", "×");
    drop.type = "button";
    drop.title = "Remove " + m.ccd + " from residue " + m.position;
    drop.setAttribute("aria-label", drop.title);
    drop.onclick = () => { row.modifications = (row.modifications || []).filter((n) => n !== m); paintMods(); checkSoon(); };
    chip.appendChild(go);
    chip.appendChild(drop);
    list.appendChild(chip);
  });
  body.appendChild(list);
  const err = el("p", "errmsg");
  err.id = "moderr";
  const says = ((LASTCHECK && LASTCHECK.fields) || {})[MODROW] || ((LASTCHECK && LASTCHECK.fields) || {})[String(MODROW)] || {};
  err.textContent = says.modifications ? firstSentence(says.modifications) : "";
  err.title = says.modifications || "";
  err.hidden = !says.modifications;
  body.appendChild(err);
  dlg.appendChild(body);

  const foot = el("div", "mfoot");
  foot.appendChild(el("span", "why", mods.length + " modification" + (mods.length === 1 ? "" : "s")));
  const done = el("button", "btn sm pri", "Done");
  done.type = "button";
  done.onclick = () => dlg.close();
  foot.appendChild(done);
  dlg.appendChild(foot);

  const again = dlg.querySelector(".modgrid");
  if (again) again.scrollTop = scroll;
}

function blankRow() { return { kind: ENTITIES[0] ? ENTITIES[0].kind : "protein", name: "", sequence: "", copies: 1, modifications: [] }; }

/* The line under a row: how long it is, or which molecule it is, its modifications, each a way
   back into the dialog, and the file it was read from. */
function paintLen(i) {
  const row = ROWS[i];
  const box = $("len-" + i);
  if (!row || !box) return;
  box.innerHTML = "";
  if (!row.sequence) return;
  const entity = entityOf(row.kind);
  const times = row.copies > 1 ? " × " + row.copies : "";
  if (entity.given === "pick") {
    box.textContent = row.copies > 1 ? row.copies + " copies" : "";
    return;
  }
  if (entity.given === "code") {
    const look = el("a", null, "RCSB's page for " + row.sequence + " ↗");
    look.href = "https://www.rcsb.org/ligand/" + encodeURIComponent(row.sequence);
    look.target = "_blank";
    look.rel = "noopener";
    box.appendChild(look);
    if (times) box.appendChild(document.createTextNode(" · " + row.copies + " copies"));
    return;
  }
  if (entity.given === "smiles") {
    box.textContent = row.sequence.length + " characters" + times;
    return;
  }
  box.appendChild(document.createTextNode(row.sequence.length + (row.kind === "protein" ? " residues" : " bases") + times));
  const mods = row.modifications || [];
  if (mods.length) {
    box.appendChild(document.createTextNode(" · "));
    const open = el("button", "modslink", mods.length + " modification" + (mods.length === 1 ? "" : "s") + ": "
      + mods.map((m) => (row.sequence[m.position - 1] || "?") + m.position + " " + m.ccd).join(", "));
    open.type = "button";
    open.title = "Open the modifications";
    open.onclick = () => openMods(i);
    box.appendChild(open);
  }
  const from = fromWords(row);
  if (from) {
    const said = el("span", "fromfile", " · " + from);
    said.title = row.source.file
      + (((HELLO && HELLO.source_words) || {})[row.source.how]
        ? ", " + HELLO.source_words[row.source.how] : "");
    box.appendChild(said);
  }
}

function paintTotal() {
  const chains = ROWS.filter((r) => isChain(r) && r.sequence);
  const others = ROWS.filter((r) => !isChain(r) && r.sequence);
  const residues = chains.reduce((n, r) => n + r.sequence.length * r.copies, 0);
  const strands = chains.reduce((n, r) => n + r.copies, 0);
  const molecules = others.reduce((n, r) => n + r.copies, 0);
  const said = [];
  if (strands) said.push(strands + (strands === 1 ? " chain" : " chains") + " · " + residues.toLocaleString() + " residues");
  if (molecules) said.push(molecules + (molecules === 1 ? " small molecule" : " small molecules"));
  $("total").textContent = said.join(" · ");
}

/* What the card shows: the ways in while there is nothing on it, the rows and the quiet ways to
   add more once there is. */
function paintSource() {
  const has = ROWS.length > 0;
  const open = !$("codepane").hidden || !$("pastebox").hidden;
  $("ents").hidden = !has;
  $("entbar").hidden = !has;
  $("pickzone").hidden = has || open || STRUCTS.length > 0;
  $("addpaste").hidden = !$("pastebox").hidden;
}
function showSource(which) {
  $("codepane").hidden = which !== "code";
  $("pastebox").hidden = which !== "paste";
  paintSource();
  /* The Next bar follows the pane that is open, so it never points at a button the pane hides. */
  if (!ROWS.some((r) => r.sequence)) paintNext({}, false, "what");
  if (which === "code") setTimeout(() => $("pdbid").focus(), 0);
  if (which === "paste") setTimeout(() => $("pastetext").focus(), 0);
}
function sourceSays(text, bad) {
  $("src-err").hidden = !bad || !text;
  $("src-err").textContent = bad ? firstSentence(text) : "";
  $("src-err").title = bad ? String(text || "") : "";
  $("srcsays").hidden = !!bad || !text;
  $("srcsays").textContent = bad ? "" : (text || "");
}

$("addrow").onclick = () => {
  ROWS.push(blankRow());
  drawRows();
  check();
  const box = $("seq-" + (ROWS.length - 1));
  if (box) { box.scrollIntoView({ block: "nearest" }); box.focus(); }
};

/* ------------------------------------------------------------------ what came in */
const FILEICON = '<svg class="pic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 13c1.5-2 4.5-2 6 0M9 16c1.5 2 4.5 2 6 0"/></svg>';

/* Where the results land, said the way the browser names the places. */
function shortPath(path) {
  const p = String(path || "");
  const home = (HELLO && HELLO.home) || "";
  const scratch = (HELLO && HELLO.scratch) || "";
  if (scratch && (p === scratch || p.startsWith(scratch + "/"))) return "My scratch" + p.slice(scratch.length);
  if (home && (p === home || p.startsWith(home + "/"))) return "Home" + p.slice(home.length);
  const parts = p.split("/").filter(Boolean);
  return parts.length > 3 ? "…/" + parts.slice(-3).join("/") : p;
}

/* A row as the form keeps it, from a row the server read. `read` is the sequence as it arrived
   and is sent nowhere: it is what tells an edited row from an untouched one. */
function rowFrom(r, s) {
  return { kind: r.kind, name: r.name || "", sequence: r.sequence, copies: r.copies || 1,
           modifications: [], source: r.source || null, from_words: r.from_words || "",
           read: r.sequence, chain: r.chain || "", struct: s ? s.id : null };
}
function chainsOf(rows) { return rows.reduce((all, r) => all.concat(rowChains(r)), []); }
/* The rows of one structure taken off the form, and where the first of them was. */
function pullRows(id) {
  const at = ROWS.findIndex((r) => r.struct === id);
  ROWS = ROWS.filter((r) => r.struct !== id);
  return at;
}
function putRows(rows, at) {
  if (at < 0 || at > ROWS.length) ROWS = ROWS.concat(rows);
  else ROWS.splice(at, 0, ...rows);
}

/* A structure, read on the login node by the one reader these pages share
   (hope_labs/structure.py), and its chains made rows. Read again when it replaces another, so the
   rows come in where that one's were. */
async function readStructure(body, replacing) {
  let got;
  try { got = await api("/api/structure", body); }
  catch (failure) { throw new Error(firstSentence(failure.message)); }
  if (!(got.rows || []).length) {
    throw new Error(firstSentence((got.left_out || [])[0] || ("No chain of " + got.file + " can be folded here.")));
  }
  const s = { id: ++STRUCTN, file: got.file, from: got.from, how: got.how,
              chains: got.chains || [], no_sequence: got.no_sequence || [],
              left_out: got.left_out || [], picked: [] };
  const rows = got.rows.map((r) => rowFrom(r, s));
  s.picked = chainsOf(rows);
  const k = replacing ? STRUCTS.indexOf(replacing) : -1;
  if (k >= 0) {
    putRows(rows, pullRows(replacing.id));
    STRUCTS.splice(k, 1, s);
  } else {
    STRUCTS.push(s);
    putRows(rows, -1);
  }
  sourceSays("");
  showSource("none");
  drawStructs();
  drawRows();
  check();
  return got;
}

/* A chain pressed on a structure's chips: on the form or off it. Read again with the chains that
   are wanted, because which chains are copies of which is worked out by the one piece of code
   that knows, and a homodimer has to arrive as one row of two copies whether both of its chains
   are wanted or one. */
async function toggleChain(s, id) {
  const want = new Set(s.picked);
  if (want.has(id)) want.delete(id); else want.add(id);
  const ids = s.chains.map((c) => c.id).filter((c) => want.has(c));
  let rows = [];
  if (ids.length) {
    try {
      const got = await api("/api/structure", { kind: "path", path: s.from, how: s.how, chains: ids });
      rows = (got.rows || []).map((r) => rowFrom(r, s));
    } catch (failure) { sourceSays(failure.message, true); return; }
  }
  s.picked = chainsOf(rows);
  putRows(rows, pullRows(s.id));
  drawStructs();
  drawRows();
  check();
}

function removeStruct(s) {
  pullRows(s.id);
  STRUCTS = STRUCTS.filter((one) => one !== s);
  drawStructs();
  drawRows();
  check();
}

function kindWord(kind) {
  const k = String(kind || "");
  return /^(DNA|RNA)$/i.test(k) ? k.toUpperCase() : k.toLowerCase();
}

/* One structure, as the card shows it once chosen: the file, how it came, its chains as chips
   saying which are on the form, and Change. A chain that cannot be folded here is a dashed chip
   with its reason as the tooltip, so nothing in the file goes unmentioned. */
function structCard(s) {
  const card = el("div", "picked" + (s.picked.length ? " ok" : ""));
  card.dataset.struct = String(s.id);
  const head = el("div", "picked-head");
  const icon = document.createElement("span");
  icon.innerHTML = FILEICON;
  head.appendChild(icon.firstChild);
  const name = el("span", "picked-file", s.file);
  name.title = s.from;
  head.appendChild(name);
  const how = ((HELLO && HELLO.source_words) || {})[s.how] || "";
  head.appendChild(el("span", "picked-from", capital(how)));
  const change = el("button", "btn sm", "Change");
  change.type = "button";
  change.title = "Choose another structure in place of this one";
  change.onclick = () => chooseStructure(s);
  head.appendChild(change);
  const x = el("button", "picked-x", "×");
  x.type = "button";
  x.title = "Take " + s.file + " and its rows off the form";
  x.setAttribute("aria-label", x.title);
  x.onclick = () => removeStruct(s);
  head.appendChild(x);
  card.appendChild(head);

  const chips = el("div", "pchains");
  chips.setAttribute("role", "group");
  chips.setAttribute("aria-label", "Chains of " + s.file);
  const cannot = [];
  s.chains.forEach((c) => {
    const on = s.picked.includes(c.id);
    const chip = el("button", "pchain");
    chip.type = "button";
    chip.dataset.chain = c.id;
    chip.setAttribute("aria-pressed", on ? "true" : "false");
    chip.appendChild(el("b", null, c.id));
    const unit = /^(DNA|RNA)$/i.test(c.kind) ? " nt" : " aa";
    chip.appendChild(el("span", "pk", "· " + kindWord(c.kind) + " · " + c.n + unit));
    /* A gap in the file's numbering is never closed silently: the residues either side of it are
       folded as neighbors, and the chip says so. */
    const gap = c.gaps ? (c.gaps === 1 ? "1 gap" : c.gaps + " gaps") : "";
    if (gap) chip.appendChild(el("span", "pgap", "· " + gap));
    const gapSays = gap ? " " + capital(gap) + " in the file's numbering: the residues either side are folded as neighbors." : "";
    if (c.why_not) {
      /* Off the form, but not out of reach: pressed, it copies the chain's letters, so a chain
         refused for a residue nothing here has a letter for can still be put right by hand. */
      chip.classList.add("off");
      chip.setAttribute("aria-disabled", "true");
      chip.title = firstSentence(c.why_not) + " Press to copy its sequence.";
      chip.onclick = () => copyText(c.sequence || "", firstSentence(c.why_not)
        + " Its sequence is copied, to put right by hand.");
      cannot.push(c.id);
    } else {
      chip.title = (on ? "On the form. Press to leave chain " + c.id + " out." : "Press to fold chain " + c.id + " too.") + gapSays;
      chip.onclick = () => toggleChain(s, c.id);
    }
    chips.appendChild(chip);
  });
  (s.no_sequence || []).forEach((id) => {
    const chip = el("button", "pchain");
    chip.type = "button";
    chip.disabled = true;
    chip.appendChild(el("b", null, id));
    chip.appendChild(el("span", "pk", "· no residues"));
    chip.title = "Chain " + id + " has no backbone, so there is no sequence to read: a ligand, an ion or a sugar given a chain of its own.";
    chips.appendChild(chip);
    cannot.push(id);
  });
  card.appendChild(chips);
  /* One line, and only when it says something the chips do not: none on the form, or some that
     cannot be. */
  const said = !s.picked.length ? "No chain of this file is on the form."
    : cannot.length ? (cannot.length === 1 ? "Chain " + cannot[0] + " cannot be folded here."
      : "Chains " + cannot.join(", ") + " cannot be folded here.") : "";
  if (said) {
    const line = el("p", "structnote", said);
    line.title = (s.left_out || []).join("\n");
    card.appendChild(line);
  }
  return card;
}
function drawStructs() {
  const box = $("structs");
  box.innerHTML = "";
  STRUCTS.forEach((s) => box.appendChild(structCard(s)));
}

/* Rows that came from a FASTA, pasted or read: added under whatever is on the form already. */
function takeRows(got, says) {
  const rows = (got.rows || []).map((r) => rowFrom(r, null));
  ROWS = ROWS.concat(rows);
  showSource("none");
  drawRows();
  check();
  flash(says || (rows.length + (rows.length === 1 ? " sequence read" : " sequences read")
    + (got.from ? " from " + String(got.from).split("/").pop() : "") + "."));
}

/* What a file is, by its suffix, from the server's own two lists. */
function kindsOf() {
  return (LIMITS.structure_files || []).concat(LIMITS.sequence_files || []);
}
function isStructureName(name) {
  const low = String(name || "").toLowerCase();
  return (LIMITS.structure_files || [".pdb", ".ent", ".cif", ".mmcif"]).some((k) => low.endsWith(k));
}
function isSequenceName(name) {
  const low = String(name || "").toLowerCase();
  return (LIMITS.sequence_files || [".fasta", ".fa", ".faa", ".txt", ".seq"]).some((k) => low.endsWith(k));
}
function readFasta(path, replacing) {
  return api("/api/sequences", { path: path }).then((got) => {
    if (replacing) removeStruct(replacing);
    takeRows(got);
    return got;
  }, (failure) => { throw new Error(firstSentence(failure.message)); });
}

/* A file from this computer, read in the browser and sent as its contents: the page runs on the
   cluster, and the cluster cannot see this computer's disk. The size is checked here as well as
   there, so a file far too large is refused without being read and encoded first. */
function sendFile(file, replacing) {
  return new Promise((resolve, reject) => {
    const structure = isStructureName(file.name);
    if (!structure && !isSequenceName(file.name)) {
      reject(new Error("A structure is a .pdb or .cif file, and a FASTA a .fasta, .fa or .txt file."));
      return;
    }
    const cap = structure ? LIMITS.structure : LIMITS.upload;
    if (cap && file.size > cap) {
      reject(new Error("That file is " + (file.size / 1e6).toFixed(1) + " MB, and "
        + Math.round(cap / 1048576) + " MB is the most this page takes from your computer. "
        + "Put it in your scratch and choose it there."));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read on this computer."));
    reader.onload = () => {
      const data = String(reader.result || "").split(",")[1] || "";
      if (structure) {
        readStructure({ kind: "file", filename: file.name, data: data }, replacing).then(resolve, reject);
      } else {
        api("/api/upload", { filename: file.name, data: data }).then((got) => {
          if (replacing) removeStruct(replacing);
          takeRows(got, (got.rows || []).length + " sequences read from " + file.name + ".");
          resolve(got);
        }, (failure) => reject(new Error(firstSentence(failure.message))));
      }
    };
    reader.readAsDataURL(file);
  });
}

/* The structure browser, shared by every page of ours (/static/picker.js). It is also where a
   FASTA is chosen, on the cluster or on this computer: one dialog for every file this form reads. */
function chooseStructure(replacing) {
  if (!window.HLPicker) { $("file").click(); return; }
  HLPicker.open({
    title: "Choose a structure", key: "fold-" + ((HELLO && HELLO.tool) || "page"), api: api,
    places: "/api/places", browse: "/api/browse?want=fold",
    accept: kindsOf().join(","), kinds: kindsOf(),
    acceptSays: "A .pdb or .cif structure, or a FASTA of sequences.",
    onFile: (file) => sendFile(file, replacing),
    onPath: (path) => (isStructureName(path) ? readStructure({ kind: "path", path: path }, replacing)
      : readFasta(path, replacing)),
    onCode: (code) => readStructure({ kind: "pdb", id: code }, replacing),
    onRun: (_folder, run) => readStructure({ kind: "path", path: run.structure }, replacing),
  });
}
function chooseFolder(title, useLabel, then) {
  if (!window.HLPicker) return;
  HLPicker.open({
    title: title, key: "fold-folder", api: api, folders: true, useLabel: useLabel,
    places: "/api/places", browse: "/api/browse?dirs=1", mkdir: "/api/mkdir",
    onFolder: (path) => { then(path); return Promise.resolve(); },
  });
}

$("choose").onclick = () => chooseStructure();
$("addstruct").onclick = () => chooseStructure();
$("codebtn").onclick = () => showSource("code");
$("pastebtn").onclick = () => showSource("paste");
$("addpaste").onclick = () => showSource("paste");
$("codecancel").onclick = () => { showSource("none"); sourceSays(""); };
$("pastecancel").onclick = () => { showSource("none"); $("pastesays").textContent = ""; };
$("fetch").onclick = () => {
  const code = $("pdbid").value.trim();
  if (!/^[A-Za-z0-9]{4}$/.test(code)) { sourceSays("A PDB code is four characters, like 1YCR.", true); return; }
  $("fetch").disabled = true;
  sourceSays("Fetching " + code.toUpperCase() + " from the RCSB…");
  readStructure({ kind: "pdb", id: code }).then(() => { $("pdbid").value = ""; },
    (why) => sourceSays(why.message, true)).finally(() => { $("fetch").disabled = false; });
};
$("pdbid").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("fetch").click(); } });
$("pdbid").addEventListener("input", () => { $("pdbid").value = $("pdbid").value.toUpperCase(); });
$("pasteadd").onclick = async () => {
  const text = $("pastetext").value;
  if (!text.trim()) { $("pastesays").textContent = "Paste a sequence first."; return; }
  try {
    takeRows(await api("/api/paste", { text: text }));
    $("pastetext").value = "";
    $("pastesays").textContent = "";
  } catch (failure) { $("pastesays").textContent = firstSentence(failure.message); $("pastesays").title = failure.message; }
};
$("file").onchange = () => {
  const f = $("file").files[0];
  $("file").value = "";
  if (f) sendFile(f).catch((why) => sourceSays(why.message, true));
};
/* A file dropped on the card is the same as one chosen on This computer. */
const zone = $("step-what");
["dragenter", "dragover"].forEach((k) => zone.addEventListener(k, (e) => {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
  e.preventDefault();
  if (!$("pickzone").hidden) $("pickzone").classList.add("over"); else zone.classList.add("dropover");
}));
["dragleave", "drop"].forEach((k) => zone.addEventListener(k, (e) => {
  if (k === "dragleave" && zone.contains(e.relatedTarget)) return;
  $("pickzone").classList.remove("over");
  zone.classList.remove("dropover");
}));
zone.addEventListener("drop", (e) => {
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  e.preventDefault();
  sourceSays("Sending " + f.name + "…");
  sendFile(f).then(() => sourceSays(""), (why) => sourceSays(why.message, true));
});

/* ------------------------------------------------------------------ the settings */
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

/* How long this form is likely to want, as a generous allowance rather than a measurement. It
   follows the one thing the time actually follows - how much there is to fold - and the models
   and recycles asked for. Rounded up to the half hour, never under an hour, and clamped to what
   the partition allows. A job that ends early is charged for what it used; one that runs out has
   to be queued again from the beginning, so the rounding is deliberately generous. */
function suggestWalltime() {
  const residues = ROWS.reduce((n, r) => n + rowLength(r) * r.copies, 0);
  if (!residues) return 0;
  const n = (key, fallback) => {
    const got = Number(valueOf(key));
    return Number.isFinite(got) && got > 0 ? got : fallback;
  };
  const models = n("num_models", 1) * n("num_seeds", 1) * n("diffusion_samples", 1);
  const recycles = n("num_recycle", n("recycling_steps", 3));
  // Folding cost grows faster than the chain does, so the length term is squared about a
  // hundred-residue system rather than taken straight.
  const size = Math.max(1, Math.pow(residues / 100, 1.7));
  const perModel = 6 * Math.max(1, recycles / 3) * size;
  const minutes = 25 + perModel * models * (HELLO.tool === "boltz2" ? 2 : 1);
  const want = Math.max(3600, Math.ceil((minutes * 60) / 1800) * 1800);
  const cap = (LIMITS && LIMITS.max_gpu_seconds) || 0;
  return cap ? Math.min(cap, want) : want;
}

function shortOf(f, choice) { return SHORT[f.key] ? SHORT[f.key](choice) : (choice === "" ? "The tool's own" : String(choice)); }

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
    if (f.choices.length && f.choices.length <= 3 && f.key !== "gpu") {
      /* A short list is a row of chips: one press rather than two, every choice visible. */
      wrap.classList.add("segf");
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
        b.title = note ? shortOf(f, choice) + ": " + note : shortOf(f, choice);
        b.onclick = () => { setValue(f.key, choice); seg.dispatchEvent(new Event("change", { bubbles: true })); };
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
    wrap.classList.add("num");
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
      if (f.least != null && f.most != null) input.title = f.least + " to " + f.most;
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
        input.value = String(Math.round(want * 1e6) / 1e6);
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    spin.appendChild(input);
    spin.appendChild(spinArrows(step, f.kind === "duration" ? "Half an hour more" : "More",
      f.kind === "duration" ? "Half an hour less" : "Less"));
    wrap.appendChild(spin);
    if (f.kind === "duration") {
      /* Typing 04:00:00 to ask for four hours is eight keystrokes and two chances to get it
         wrong. The arrows move it by the half hour; Estimate fills in an allowance from what is
         on the form, which is the only thing here that knows how much there is to fold. */
      const acts = el("div", "fldacts");
      const guess = el("button", "btn sm", "Estimate");
      guess.type = "button";
      guess.id = "walltime-estimate";
      guess.title = "A generous allowance from what is on the form. A job that ends early is charged for what it used.";
      guess.onclick = () => {
        const want = suggestWalltime();
        if (!want) { flash("Put a sequence on the form first: the estimate follows how much there is to fold.", true); return; }
        input.value = asWalltime(want);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      };
      acts.appendChild(guess);
      wrap.appendChild(acts);
    }
  } else {
    const input = el("input", "inp");
    input.id = f.key;
    input.autocomplete = "off";
    input.spellcheck = false;
    if (f.default != null && f.default !== "") input.value = String(f.default);
    else input.placeholder = capital(f.placeholder || (f.optional ? "optional" : ""));
    wrap.appendChild(input);
  }
  const err = el("p", "errmsg");
  err.id = f.key + "-err";
  err.hidden = true;
  wrap.appendChild(err);
  return wrap;
}

function valueOf(key) {
  const node = $(key);
  if (!node) return undefined;
  if (node.classList && node.classList.contains("seg")) return node.dataset.value;
  return node.type === "checkbox" ? node.checked : node.value;
}
function setValue(key, value) {
  const node = $(key);
  if (!node) return;
  if (node.classList && node.classList.contains("seg")) {
    node.dataset.value = String(value);
    node.querySelectorAll(".chip").forEach((b) => {
      const on = b.dataset.value === String(value);
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
  } else if (node.type === "checkbox") node.checked = !!value;
  else node.value = String(value);
}

let typing = 0;
function later(ms) { clearTimeout(typing); typing = setTimeout(check, ms || 280); }
function listen(node) {
  const kind = (node.tagName === "SELECT" || node.type === "checkbox" || (node.classList && node.classList.contains("seg"))) ? "change" : "input";
  node.addEventListener(kind, () => later());
}

/* Every setting, each drawn once: the machine's in the rail, the tool's in the Settings card, and
   the ones few people change under More options there. */
function buildSettings() {
  const main = $("fields-main"), more = $("fields-more"), res = $("fields-res");
  [main, more, res].forEach((box) => { box.innerHTML = ""; });
  let count = 0;
  FIELDS.forEach((f) => {
    if (RESOURCE.includes(f.key)) return;
    if (f.advanced) { more.appendChild(fieldNode(f)); count += 1; }
    else main.appendChild(fieldNode(f));
  });
  RESOURCE.forEach((key) => {
    const f = FIELDS.find((one) => one.key === key);
    if (f) res.appendChild(fieldNode(f));
  });
  $("moreopts").hidden = !count;
  /* A bare numeral beside the words read as the card's note line saying "4". */
  $("morecount").textContent = count ? count + (count === 1 ? " setting" : " settings") : "";
  FIELDS.concat([{ key: "name" }, { key: "out" }]).forEach((f) => {
    const node = $(f.key);
    if (node) listen(node);
  });
}
$("outbrowse").onclick = () => chooseFolder("Choose a run folder", "Use this folder", (path) => {
  $("out").value = path;
  later(60);
});

/* ------------------------------------------------------------------ checking */
function form() {
  const body = {
    rows: ROWS.map((r) => ({ kind: r.kind, name: r.name, sequence: r.sequence, copies: r.copies,
                             modifications: isChain(r) ? (r.modifications || []) : [],
                             source: r.source || null })),
    name: $("name").value, out: $("out").value
  };
  FIELDS.forEach((f) => {
    const got = valueOf(f.key);
    if (got !== undefined) body[f.key] = got;
  });
  return body;
}

function rowSays(fields, i) {
  const row = fields[i] || fields[String(i)] || {};
  return row.sequence || row.kind || row.modifications || row.copies || "";
}
function isClean(answer) {
  return !(answer.problems || []).length
    && !Object.keys(answer.fields || {}).some((k) => Object.keys(answer.fields[k] || {}).length);
}

function showErrors(answer) {
  const fields = (answer && answer.fields) || {};
  ROWS.forEach((_r, i) => {
    const row = fields[i] || fields[String(i)] || {};
    const says = rowSays(fields, i);
    const err = $("rowerr-" + i);
    const seq = $("seq-" + i);
    if (err) { err.textContent = says ? firstSentence(says) : ""; err.title = says; err.hidden = !says; }
    if (seq) seq.classList.toggle("bad", !!(row.sequence || row.kind || row.modifications));
  });
  const moderr = $("moderr");
  if (moderr && MODROW >= 0) {
    const row = fields[MODROW] || fields[String(MODROW)] || {};
    moderr.textContent = row.modifications ? firstSentence(row.modifications) : "";
    moderr.hidden = !row.modifications;
  }
  const settings = fields.settings || {};
  FIELDS.concat([{ key: "out" }]).forEach((f) => {
    const box = $(f.key + "-err");
    const input = $(f.key);
    const says = settings[f.key] || "";
    if (box) { box.textContent = says ? firstSentence(says) : ""; box.title = says; box.hidden = !says; }
    if (input && input.classList && input.classList.contains("inp")) input.classList.toggle("bad", !!says);
  });
  $("g-res").classList.toggle("bad", RESOURCE.some((k) => settings[k]));
  $("g-where").classList.toggle("bad", !!settings.out);
  const list = $("problems");
  list.innerHTML = "";
  ((answer && answer.problems) || []).forEach((says) => {
    const li = el("li", null, firstSentence(says));
    li.title = says;
    list.appendChild(li);
  });
  list.hidden = !((answer && answer.problems) || []).length;
}

/* What the run plan does not say already, one line each. */
function paintWarnings(answer) {
  const list = $("warnings");
  list.innerHTML = "";
  const said = (answer && (answer.warnings || answer.notes)) || [];
  said.forEach((one) => {
    const li = el("li", null, firstSentence(one));
    li.title = one;
    list.appendChild(li);
  });
  list.hidden = !said.length;
}

function paintPlan(answer) {
  const plan = answer && answer.plan;
  const box = $("plan");
  if (!ROWS.some((r) => r.sequence) || !plan || !(plan.phases || []).length) { box.hidden = true; return; }
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
    li.title = capital([phase.what, phase.where].filter(Boolean).join(", ")) + ".";
    list.appendChild(li);
  });
  const name = $("name").value.trim() || answer.name || "the run";
  /* The folder is named <run name>_<date and time> when Submit is pressed, and the date is not
     known before then. Said as a placeholder in angle brackets, the way the job preview says
     <run folder>: a trailing ellipsis read as a path the page had cut short, which is the one
     line in the plan that has to be read whole. */
  const base = $("out").value.trim()
    || ((HELLO && HELLO.roots && HELLO.roots[0]) || "") + "/" + name + "_<date>";
  $("planwhere").textContent = "Results in " + shortPath(base);
  $("planwhere").title = base;
  box.hidden = false;
}

/* What still has to be done, card by card: the word at the end of each title, the outline on the
   card to do next, the steps along the top, and the one line in the Next bar. */
function paintProgress(answer, ok) {
  const fields = (answer && answer.fields) || {};
  const settings = fields.settings || {};
  const has = ROWS.some((r) => r.sequence);
  const rowBad = ROWS.some((_r, i) => rowSays(fields, i));
  const setBad = FIELDS.some((f) => !RESOURCE.includes(f.key) && settings[f.key]);
  const state = { what: !has ? "need" : rowBad ? "fix" : "done", settings: setBad ? "fix" : "done" };
  const chip = (id, s, words) => {
    $(id).textContent = words[s];
    $(id).className = "cstate" + (s === "need" || s === "fix" ? " need" : " done");
  };
  chip("st-what", state.what, { need: "Needed", fix: "Fix", done: "Done" });
  chip("st-settings", state.settings, { fix: "Fix", done: "Ready" });
  const next = state.what !== "done" ? "what" : state.settings !== "done" ? "settings" : "submit";
  ["what", "settings"].forEach((k) => $("step-" + k).classList.toggle("is-next", k === next && !ok));
  const order = ["what", "settings", "submit"];
  document.querySelectorAll("#rail .st").forEach((b) => {
    const k = b.dataset.goto;
    const done = k === "submit" ? ok : state[k] === "done";
    b.classList.toggle("done", done && order.indexOf(k) < order.indexOf(next));
    b.classList.toggle("next", k === next);
    b.setAttribute("aria-current", k === next ? "step" : "false");
  });
  return next;
}

/* The one thing to say about a form that cannot be queued: a row's trouble first, then a
   setting's, then the page's. */
function firstProblem(answer) {
  const fields = (answer && answer.fields) || {};
  for (let i = 0; i < ROWS.length; i++) {
    const says = rowSays(fields, i);
    if (says) return "Row " + (i + 1) + ": " + firstSentence(says);
  }
  const settings = fields.settings || {};
  for (const f of FIELDS.concat([{ key: "out", label: "Run folder" }])) {
    if (settings[f.key]) return f.label + ": " + firstSentence(settings[f.key]);
  }
  const problem = ((answer && answer.problems) || [])[0];
  return problem ? firstSentence(problem) : "";
}

function paintNext(answer, ok, next) {
  const bar = $("nextbar"), text = $("nexttext");
  bar.classList.toggle("ready", !!ok);
  $("nextgo").dataset.goto = next;
  if (!ROWS.some((r) => r.sequence)) {
    text.textContent = !$("pastebox").hidden ? "Paste the sequences, then press Add these."
      : !$("codepane").hidden ? "Type a PDB code, then press Fetch."
      : ROWS.length ? "Give row " + (ROWS.findIndex((r) => !r.sequence) + 1) + " a sequence."
      : "Choose a structure, or paste sequences.";
    return;
  }
  if (!ok) { text.textContent = firstProblem(answer) || "Fix what is marked."; return; }
  text.textContent = HELLO && HELLO.dry_run ? "Ready. Press Submit to write the run."
    : "Ready. Press Submit to queue the run.";
}
$("nextgo").onclick = () => {
  const goto = $("nextgo").dataset.goto || "what";
  showCard(goto);
  if (goto === "what" && !ROWS.length) setTimeout(() => $("choose").focus({ preventScroll: true }), 350);
  if (goto === "what" && ROWS.length) {
    const bad = document.querySelector("#rows .seq.bad, #rows .errmsg:not([hidden])");
    const row = bad && bad.closest(".ent");
    if (row) setTimeout(() => row.scrollIntoView({ behavior: "smooth", block: "center" }), 350);
  }
  if (goto === "submit") setTimeout(() => { if (!$("go").disabled) $("go").focus({ preventScroll: true }); }, 350);
};

/* The plan and Submit are the foot of the rail, on the screen however far the settings above
   them are scrolled - but only while what is left over is a column the settings can be read in.
   At 150% on a 1366x768 laptop the held foot took two thirds of the rail and left Resources a
   212px slot: Time limit's label showed and its box was below the edge, and Account, Run name
   and Run folder could not be reached at all. Past that point the whole rail scrolls as one
   column, the foot at the end of it, and step 3 and Show me scroll to it. */
const RAIL_LEAST = 240;
function holdSubmit() {
  const rail = $("railside"), box = $("step-submit"), pane = $("railscroll");
  if (!rail || !box || !pane || !rail.clientHeight) return;
  const scale = parseFloat(getComputedStyle(document.documentElement)
    .getPropertyValue("--ui-scale")) || 1;
  /* Measured with the rail let go, so the answer does not depend on the answer last time: held,
     the foot is capped by the stylesheet and reads back as the cap rather than as its height. */
  rail.classList.add("railscrolls");
  const room = rail.clientHeight - box.scrollHeight;
  rail.classList.toggle("railscrolls", room < RAIL_LEAST * scale);
  heldOver();
}
/* The shadow along its top only while settings are actually under it. */
function heldOver() {
  const box = $("step-submit"), rail = $("railside"), pane = $("railscroll");
  if (!box || !rail || !pane) return;
  const held = !rail.classList.contains("railscrolls");
  box.classList.toggle("held", held && pane.scrollHeight - pane.scrollTop - pane.clientHeight > 2);
}
window.addEventListener("resize", holdSubmit);
window.addEventListener("hl-zoom", holdSubmit);
$("railscroll").addEventListener("scroll", heldOver, { passive: true });

function paintSubmit(answer, ok) {
  const installed = !!(HELLO && HELLO.installed);
  $("go").disabled = !ok || !installed;
  $("dry").disabled = !ok || !!(HELLO && HELLO.dry_run);
  const why = $("gowhy");
  let says = "";
  if (!ROWS.some((r) => r.sequence)) says = "Choose a structure or paste sequences first.";
  else if (!ok) says = firstProblem(answer) || "Fix what is marked first.";
  else if (!installed) says = toolName() + " is not installed here.";
  why.textContent = says;
  why.hidden = !says;
  holdSubmit();
}

let checking = 0;
async function check() {
  const mine = ++checking;
  paintTotal();
  if (!ROWS.some((r) => r.sequence)) {
    LASTCHECK = null;
    TOLD = null;
    showErrors({});
    paintPlan(null);
    paintWarnings(null);
    const next = paintProgress({}, false);
    paintNext({}, false, next);
    paintSubmit({}, false);
    return;
  }
  let answer;
  try { answer = await api("/api/check", form()); }
  catch (failure) { if (mine === checking) flash(failure.message, true); return; }
  if (mine !== checking) return;                     // a later keystroke already asked
  LASTCHECK = answer;
  const ok = isClean(answer);
  showErrors(answer);
  const told = answer.told || {};
  TOLD = told.text ? { input: told.text, command: told.command || "", what: told.what || "",
                       first: told.phase_one || "", plan: answer.plan || {} } : null;
  if (answer.name && !$("name").value) $("name").placeholder = answer.name;
  paintPlan(answer);
  paintWarnings(answer);
  const next = paintProgress(answer, ok);
  paintNext(answer, ok, next);
  paintSubmit(answer, ok);
  if ($("jobdlg").open) paintTold();
}

/* ------------------------------------------------------------------ the job, for the curious */
/* The input file exactly as it will be written, and the command that reads it: a chain id or a
   copy count read wrong costs hours and does not fail, so the input is shown rather than
   described. In a dialog, for the person who wants it; nobody else has to read past it. */
let TOLDMODE = "input";
document.querySelectorAll("#toldmodes button").forEach((b) => {
  b.onclick = () => {
    TOLDMODE = b.dataset.told;
    document.querySelectorAll("#toldmodes button").forEach((o) => o.classList.toggle("on", o === b));
    paintTold();
  };
});
function paintTold() {
  if (!TOLD) {
    $("toldwhat").textContent = "The job is written once the form has nothing to put right.";
    $("toldtext").textContent = "";
    $("toldtext").hidden = true;
    $("toldfirst").hidden = true;
    $("toldslurm").textContent = "";
    return;
  }
  $("toldtext").hidden = false;
  $("toldtext").textContent = TOLDMODE === "input" ? TOLD.input : TOLD.command;
  $("toldwhat").textContent = TOLDMODE === "input"
    ? "Written to " + TOLD.what + " in the run folder, and read by the job exactly as it is here."
    : "Run in the job, on the compute node, with no network.";
  $("toldfirst").hidden = !TOLD.first;
  $("toldfirst").textContent = TOLD.first ? "Before the job is queued: " + TOLD.first + "." : "";
  const p = TOLD.plan || {};
  $("toldslurm").textContent = p.gpu ? "Slurm is asked for one " + String(p.gpu).toUpperCase() + " on "
    + p.partition + ", " + p.cpus + " cores, " + p.mem + " GB, for " + p.walltime + "." : "";
}
$("showjob").onclick = () => { paintTold(); $("jobdlg").showModal(); };
$("jobclose").onclick = () => $("jobdlg").close();
$("jobx").onclick = () => $("jobdlg").close();

/* ------------------------------------------------------------------ submitting */
async function go(dry) {
  const button = dry ? $("dry") : $("go");
  const was = button.textContent;
  $("go").disabled = $("dry").disabled = true;
  button.textContent = dry ? "Writing…" : "Fetching the alignments…";
  $("golast").hidden = true;
  try {
    const answer = await api("/api/submit", Object.assign(form(), { dry_run: dry }));
    if (answer.problems || answer.fields) {
      showErrors(answer);
      flash("Not submitted: the form has something to put right.", true);
      return;
    }
    const run = answer.run;
    $("golast").hidden = false;
    $("golast").textContent = run.job_id ? "Queued as job " + run.job_id + "." : "Written. Nothing was queued.";
    $("golast").title = run.path;
    flash(run.job_id ? "Queued as job " + run.job_id + ". The Runs tab follows it."
      : "Run folder written: " + shortPath(run.path) + ".");
    loadRuns(run.path);
    document.querySelector('.tab[data-screen="runs"]').click();
  } catch (failure) {
    flash(firstSentence(failure.message), true);
  } finally {
    button.textContent = was;
    check();
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
    /* A folding job writes everything at the end, so there is less to watch than a design run;
       the queue state is the thing that changes. Nothing is read while the screen is put away. */
    if (!$("screen-runs").hidden) runsTimer = setTimeout(() => loadRuns(), 30000);
  } catch (failure) {
    $("runlist").innerHTML = "";
    $("runlist").appendChild(el("p", "empty", failure.message));
  }
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

/* THE REPORT OF A RUN: the write-up of what was folded, in a tab of its own, with the print
   window opened for it. Offered for a run that stopped as well as for one that finished: a run
   that ran out of walltime with two samples on disk is exactly the one somebody needs the
   numbers of, and the report says in its first line that the job did not finish. */
function reportButton(run) {
  const a = el("a", "btn sm pri", "Report (PDF)");
  a.href = "/report?t=" + encodeURIComponent(TOKEN) + "&rundir=" + encodeURIComponent(run.path)
    + "&print=1";
  a.target = "_blank";
  a.rel = "noopener";
  a.title = "The write-up of this run, ready to save as a PDF";
  return a;
}

/* Saved where the person chooses, where the browser can do that. showSaveFilePicker is Chrome's
   and needs a secure context, which 127.0.0.1 is; everywhere else this falls back to an ordinary
   download, which is what Firefox and Safari have. Neither path is allowed to break the other. */
async function saveLocally(name, text) {
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({ suggestedName: name });
      const writer = await handle.createWritable();
      await writer.write(text);
      await writer.close();
      return "saved as " + handle.name;
    } catch (failure) {
      if (failure && failure.name === "AbortError") return "";      // they changed their mind
      /* Anything else, and the ordinary download below is tried instead rather than reported:
         a blocked picker should not be the end of saving a file. */
    }
  }
  const a = document.createElement("a");
  const url = URL.createObjectURL(new Blob([text], { type: "chemical/x-pdb" }));
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return "sent to your browser's downloads";
}

async function copyText(text, says) {
  try { await navigator.clipboard.writeText(text); flash(says); }
  catch (failure) { flash("Could not copy it: " + failure.message, true); }
}

/* What the person picked in each run's table, and what each run looked like when it was last
   drawn. The list is read again every thirty seconds, and the open run with it; drawn afresh each
   time, the viewer went back to the best structure whatever had been picked, half a minute after
   somebody chose another one to look at. Now an open run is drawn again only when something in
   it has changed, and the structure picked stays picked when it is. */
const PICKED = {};
const DRAWN = {};
function pickedRow(run) {
  const rows = (run.table && run.table.rows) || [];
  const at = rows.findIndex((r) => r.file === PICKED[run.path]);
  return at < 0 ? 0 : at;
}

function disclosure(title, open) {
  const box = el("details", "box disclose");
  box.open = !!open;
  box.appendChild(el("summary", null, title));
  return box;
}

async function openRun(path, quiet) {
  OPEN = path;
  document.querySelectorAll(".runrow").forEach((row) => row.classList.toggle("on", row.querySelector(".name").title === path));
  const main = $("rundetail");
  if (!quiet) { main.innerHTML = ""; main.appendChild(el("p", "empty", "Reading the run…")); }
  let run;
  try { run = (await api("/api/run?path=" + encodeURIComponent(path))).run; }
  catch (failure) {
    if (quiet) return;                   // a refresh that could not read leaves what is shown
    main.innerHTML = ""; main.appendChild(el("p", "empty", failure.message)); return;
  }
  const looks = JSON.stringify([run.state, run.job_id, run.structures, run.reason,
    ((run.table && run.table.rows) || []).map((r) => r.file)]);
  if (quiet && DRAWN[path] === looks && main.querySelector(".runhead")) return;
  DRAWN[path] = looks;
  main.innerHTML = "";
  /* The viewers hold the element they were given, and that element has just been thrown away.
     Kept ones would render into a box no longer in the page and the viewer would look broken. */
  forgetViewers();

  const head = el("div", "runhead");
  head.appendChild(el("h2", null, run.name));
  const state = run.state || "Not queued";
  head.appendChild(el("span", "cstate" + (/COMPLETED/i.test(state) ? " done" : run.over ? " need" : ""),
    capital(String(state).toLowerCase())));
  if (run.job_id) head.appendChild(el("span", "why", "job " + run.job_id
    + (run.elapsed ? ", " + run.elapsed + " elapsed" : "") + (run.left ? ", " + run.left + " left" : "")));
  if (run.reason) head.appendChild(el("span", "why", run.reason));
  if ((run.entities || []).length) {
    const again = el("button", "btn sm", "Reuse");
    again.type = "button";
    again.title = "Put this run's rows and settings on the Fold page, to run it again or change it";
    again.onclick = () => reuseRun(run);
    head.appendChild(again);
  }
  main.appendChild(head);

  const counts = run.counts || {};
  const tiles = el("div", "tiles");
  /* Four counts, and under each one a line about that count and nothing else. The fourth used
     to carry the GPU and the time limit under the word Residues, which left a tile nobody could
     explain: what machine it ran on is a fact of the run and is in the list below instead. */
  const chains = (run.rows || []).reduce((n, r) => n + (r.copies || 1), 0);
  tiles.appendChild(tile("Structures", run.structures == null ? "—" : String(run.structures),
    run.structures == null ? "written when the job finishes"
      : run.structures === 1 ? "model written" : "models written"));
  const nmsa = counts.alignments || 0;
  tiles.appendChild(tile("Alignments", String(nmsa),
    run.alignments === "single sequence" ? "none: the sequence alone"
      : nmsa === 1 ? "file for this fold" : "files, one per protein chain"));
  tiles.appendChild(tile("Chains", String(chains),
    (run.rows || []).map((r) => kindWord(r.kind)).filter((k, i, a) => a.indexOf(k) === i).join(", ")));
  tiles.appendChild(tile("Residues", String((run.plan || {}).tokens || "—"),
    chains === 1 ? "in the one chain" : "across all " + chains + " chains"));
  main.appendChild(tiles);

  /* The entities, as they were given. A structure a year later is worth nothing without them,
     and the input file carries chain ids and alignment paths rather than row names. */
  const what = el("div", "box");
  what.appendChild(el("h2", null, "What was folded"));
  const list = el("div", "foldrows");
  (run.rows || []).forEach((row, i) => {
    const one = el("div", "foldrow");
    const line = el("div", "fr-head");
    line.appendChild(el("b", null, row.name || entityOf(row.kind).label));
    /* A record written before ligands were named says ligand and 1, which was one molecule;
       anything since gives the code, and its size in atoms. */
    const n = row.residues;
    const unit = row.kind === "protein" ? "residue"
      : (row.kind === "dna" || row.kind === "rna") ? "base"
        : (row.ccd || row.kind === "smiles") ? "heavy atom" : "molecule";
    line.appendChild(el("span", "why", "chain " + row.chains.join(", ") + " · "
      + entityOf(row.kind).label + (row.ccd ? " " + row.ccd : "") + ", " + n + " " + unit + (n === 1 ? "" : "s")
      + (row.copies > 1 ? ", " + row.copies + " copies" : "")
      + ((row.modifications || []).length ? ", modified at " + row.modifications.join(", ") : "")));
    one.appendChild(line);
    /* The sequence itself, as it was typed, under its line: a structure a month later is read
       against the sequence that went in, and the input file is the place nobody looks. */
    const typed = ((run.entities || [])[i] || {}).sequence || "";
    if (typed && !row.ccd) {
      const show = el("div", "seqshow");
      show.appendChild(el("code", null, typed));
      const copy = el("button", "pill", "Copy");
      copy.type = "button";
      copy.title = "Copy this sequence";
      copy.onclick = () => copyText(typed, "Copied " + typed.length + " characters.");
      show.appendChild(copy);
      one.appendChild(show);
    }
    /* And which file it was read off, when it was read off one. This is the line that settles an
       argument a year later: a sequence with a file and a chain beside it can be checked, and a
       sequence with nothing beside it reads as hand-typed whether it was or not. The phrase is
       the one the record and the report use, spelled by the server and sent with the run. */
    const kept = (run.entities || [])[i] || {};
    const source = row.source || kept.source;
    const from = fromWords({ source: source, from_words: row.from_words || kept.from_words });
    if (from) {
      const said = el("span", "fromfile", "Read from " + from);
      said.title = source.file
        + (((HELLO && HELLO.source_words) || {})[source.how]
          ? ", " + HELLO.source_words[source.how] : "");
      one.appendChild(said);
    }
    list.appendChild(one);
  });
  what.appendChild(list);
  const kv = el("dl", "kv foldfacts");
  /* Which machine it actually ran on, rather than the word that was on the form, which for
     almost every run is "auto". */
  const card = String((run.plan || {}).gpu || (run.asked || {}).gpu || "").toUpperCase();
  [["Alignments", run.alignments || "—"],
   ["Ran on", card ? "one " + card + " GPU" : "—"],
   ["Time limit", (run.asked || {}).walltime || "—"],
   ["Submitted", run.submitted || "—"],
   ["Owner", run.owner || "—"]].forEach(([k, v]) => {
    kv.appendChild(el("dt", null, k)); kv.appendChild(el("dd", null, String(v)));
  });
  what.appendChild(kv);

  const actions = el("div", "acts");
  if ((run.table && run.table.rows.length) || run.over) actions.appendChild(reportButton(run));
  /* A folder that holds two folds holds two inputs, and two buttons both called Input cannot be
     told apart, so each is then named by its file. */
  const inputs = (run.files || []).filter((name) => /\.(yaml|fasta)$/.test(name));
  (run.files || []).forEach((name) => {
    if (/\.(yaml|fasta)$/.test(name)) {
      actions.appendChild(download(run, name, inputs.length > 1 ? name.split("/").pop() : "Input"));
    }
    else if (/job\.sbatch$/.test(name)) actions.appendChild(download(run, name, "Job script"));
    else if (/entities\.json$/.test(name)) {
      /* "Rows" on a row of download buttons read as a button that would show the rows. */
      const a = download(run, name, "Rows (JSON)");
      a.title = "The rows exactly as the page sent them, as entities.json";
      actions.appendChild(a);
    }
  });
  if (run.log_name) actions.appendChild(download(run, run.log_name, "Log"));
  if (run.job_id && !run.over) {
    const stop = el("button", "btn sm", "Cancel the job");
    stop.type = "button";
    stop.onclick = async () => {
      if (!confirm("Cancel job " + run.job_id + "? The alignments stay in the run folder, so queueing it again needs no network.")) return;
      try { const a = await api("/api/cancel", { path: run.path }); flash(a.message, !a.ok); loadRuns(run.path); }
      catch (failure) { flash(failure.message, true); }
    };
    actions.appendChild(stop);
  } else if (run.job_id) {
    const again = el("button", "btn sm", "Queue again");
    again.type = "button";
    again.title = "The same job script, phase two only, so it needs no network";
    again.onclick = async () => {
      try { const a = await api("/api/resubmit", { path: run.path }); flash(a.message, !a.ok); loadRuns(run.path); }
      catch (failure) { flash(failure.message, true); }
    };
    actions.appendChild(again);
  }
  what.appendChild(actions);
  main.appendChild(what);

  if (run.table && run.table.rows.length) {
    main.appendChild(structuresBox(run));
    main.appendChild(viewerBox(run));
    main.appendChild(copyBox(run));
  } else if (run.over) {
    main.appendChild(copyBox(run));
  }

  if (run.told && run.told.text) {
    const box = disclosure("The job it was given");
    box.querySelector("summary").appendChild(el("span", "why", run.told.what));
    box.appendChild(el("pre", "told", run.told.text));
    if (run.told.command) {
      box.appendChild(el("h3", null, "The command it ran"));
      box.appendChild(el("pre", "told", run.told.command));
    }
    main.appendChild(box);
  }

  if (run.log) {
    const failed = run.over && !/COMPLETED/i.test(run.state || "");
    const box = disclosure("The log", failed);
    box.querySelector("summary").appendChild(el("span", "why", run.log_name));
    const pre = el("pre", "log", run.log);
    box.appendChild(pre);
    main.appendChild(box);
    box.addEventListener("toggle", () => { if (box.open) pre.scrollTop = pre.scrollHeight; });
    if (failed) pre.scrollTop = pre.scrollHeight;
  }
}

/* A score as its column shows it: every value of one column to the same places, so 84 and 89.5
   read as 84.0 and 89.5 rather than as two kinds of number. */
function fmt(value, places) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(places == null ? 3 : places) : String(value);
}

/* A run's rows and settings, back on the Fold page. Everything but the name of a folder: a run
   given the folder of the one it copies would be put in a folder inside it, which is not what
   anybody pressing Reuse means. A ligand row from before ligands were picked by name was a
   SMILES, or a code, and comes back as one, rather than as a ligand row with nothing picked. */
function reuseRun(run) {
  const listed = new Set(((ENTITIES.find((e) => e.kind === "ligand") || {}).choices || []).map(([c]) => c));
  STRUCTS = [];
  ROWS = (run.entities || []).map((r) => {
    let kind = r.kind || "protein";
    const seq = String(r.sequence || "");
    if (kind === "ligand" && !listed.has(seq.toUpperCase())) kind = /^[A-Za-z0-9]{1,5}$/.test(seq) ? "ccd" : "smiles";
    if (!ENTITIES.some((e) => e.kind === kind)) kind = ENTITIES[0].kind;
    return { kind, name: r.name || "", sequence: seq, copies: r.copies || 1,
             modifications: (r.modifications || []).map((m) => ({ position: m.position, ccd: m.ccd })),
             /* The file a row was read off comes back with it, and the server's phrase for it:
                a run reused is the same chains of the same structure until somebody edits them,
                and then it says so. The phrase comes with the run rather than being spelled
                here, which is why the run view asks for it (folding/runs.py, detail). */
             source: r.source || null, from_words: r.from_words || "", read: seq, struct: null };
  });
  const asked = run.asked || {};
  FIELDS.forEach((f) => {
    const box = $(f.key);
    if (!box || !(f.key in asked) || asked[f.key] == null) return;
    setValue(f.key, asked[f.key]);
  });
  $("name").value = run.name || "";
  $("out").value = "";
  const tab = document.querySelector('.tab[data-screen="setup"]');
  if (tab) tab.click();
  drawStructs();
  drawRows();
  check();
  $("stage").scrollTo(0, 0);
  flash(run.name + " is on the Fold page. Change what you want, and Submit.");
}

function structuresBox(run) {
  const box = el("div", "box");
  const title = el("h2", null, "Structures, best first");
  title.appendChild(el("span", "count", run.table.total + " in all"));
  box.appendChild(title);
  const wrap = el("div", "tblbox");
  const table = el("table", "t");
  const head = el("tr");
  ["", "Rank", "Confidence", "pLDDT", "pTM", "ipTM", "Chains", "File", ""].forEach((c) => head.appendChild(el("th", null, c)));
  table.appendChild(el("thead")).appendChild(head);
  const body = el("tbody");
  run.table.rows.forEach((row, i) => {
    const tr = el("tr");
    const pick = el("td");
    const button = el("button", "pill" + (i === pickedRow(run) ? " on" : ""), "View");
    button.type = "button";
    button.onclick = () => {
      body.querySelectorAll(".pill").forEach((p) => p.classList.remove("on"));
      button.classList.add("on");
      PICKED[run.path] = row.file;
      showStructure(run, row);
    };
    pick.appendChild(button);
    tr.appendChild(pick);
    tr.appendChild(el("td", "num", String(row.rank)));
    tr.appendChild(el("td", "num", fmt(row.confidence)));
    tr.appendChild(el("td", "num", fmt(row.plddt, 1)));
    tr.appendChild(el("td", "num", fmt(row.ptm)));
    tr.appendChild(el("td", "num", fmt(row.iptm)));
    tr.appendChild(el("td", null, row.chains || "—"));
    tr.appendChild(el("td", "wrap", row.name));
    const save = el("td");
    const one = el("button", "pill", "Save");
    one.type = "button";
    one.title = "Save this structure on your own computer";
    one.onclick = async () => {
      try {
        const answer = await fetch(fileUrl(run, row.file));
        const said = await saveLocally(row.name, await answer.text());
        if (said) flash(row.name + " " + said);
      } catch (failure) { flash("Could not save that: " + failure.message, true); }
    };
    save.appendChild(one);
    tr.appendChild(save);
    body.appendChild(tr);
  });
  table.appendChild(body);
  wrap.appendChild(table);
  box.appendChild(wrap);
  return box;
}

/* ---- the viewer: 3Dmol by default, Mol* on request, as HOPE-MD does.
   Both are bundled in /static/vendor, because the page is reached through a forwarded port and a
   browser on it can fetch from the page and from nowhere else. The viewer keeps its own pixel
   height whatever the text size: a canvas that changed size under the pointer is a canvas that
   reports the wrong residue. */
let V = null, MV = null, MVBUSY = false, ENGINE = "3dmol", STRUCT = "", STRUCTFMT = "pdb", STRUCTNAME = "";

function forgetViewers() {
  V = null;
  if (MV) { try { MV.plugin.dispose(); } catch (e) { /* it may already be gone */ } }
  MV = null;
  STRUCT = "";
}
try { if (localStorage.hlfoldviewer) ENGINE = localStorage.hlfoldviewer; } catch (e) { /* ignore */ }
window.addEventListener("hl-zoom", () => { if (V) { V.resize(); V.render(); } });

function viewerBox(run) {
  const box = el("div", "box");
  const bar = el("div", "vbar");
  const engine = el("select", "inp");
  engine.setAttribute("aria-label", "Viewer");
  [["3dmol", "3Dmol.js"], ["molstar", "Mol*"]].forEach(([v, n]) => engine.appendChild(new Option(n, v)));
  engine.value = ENGINE;
  engine.onchange = () => {
    ENGINE = engine.value;
    try { localStorage.hlfoldviewer = ENGINE; } catch (e) { /* ignore */ }
    drawStructure();
  };
  bar.appendChild(engine);
  const vlabel = el("span", "vname");
  vlabel.id = "vlabel";
  bar.appendChild(vlabel);
  const picked = el("span", "picknote");
  picked.id = "vpick";
  picked.setAttribute("aria-live", "polite");
  bar.appendChild(picked);
  bar.appendChild(helpButton("viewer", "the viewer"));
  box.appendChild(bar);
  const wrap = el("div", "vwrap");
  const a = el("div"); a.id = "viewer";
  const b = el("div"); b.id = "viewerM"; b.hidden = true;
  wrap.appendChild(a); wrap.appendChild(b);
  /* The key sits in the viewer's own corner, over whichever viewer is showing. It used to be a
     strip under the box, which a tall viewer pushed off the bottom of the window, so the colors
     were there and what they meant was not. */
  const key = el("div", "plddtkey");
  key.setAttribute("aria-label", "Model confidence, by pLDDT");
  key.appendChild(el("div", "pkeyhead", "Model confidence (pLDDT)"));
  PLDDT.forEach(([cut, color, name], i) => {
    /* The bands read as the documentation's table reads them: above, between, and below, rather
       than a nought that is not a pLDDT anybody has. */
    const says = !i ? "> " + PLDDT[0][0]
      : (cut ? cut + " to " + PLDDT[i - 1][0] : "< " + PLDDT[i - 1][0]);
    const row = el("div", "pkeyrow");
    const swatch = el("span", "pkeyswatch");
    swatch.style.background = color;
    row.appendChild(swatch);
    row.appendChild(el("span", null, capital(name) + " (" + says + ")"));
    key.appendChild(row);
  });
  wrap.appendChild(key);
  box.appendChild(wrap);
  /* Drawn once the box is in the page: 3Dmol measures the element it is given. */
  setTimeout(() => showStructure(run, run.table.rows[pickedRow(run)]), 0);
  return box;
}

async function showStructure(run, row) {
  STRUCTNAME = row.name;
  STRUCTFMT = /\.cif$/i.test(row.name) ? "cif" : "pdb";
  const label = $("vlabel");
  if (label) label.textContent = "Reading " + row.name + "…";
  if ($("vpick")) $("vpick").textContent = "";
  try {
    const answer = await fetch(fileUrl(run, row.file));
    STRUCT = await answer.text();
  } catch (failure) {
    if (label) label.textContent = "Could not read that structure: " + failure.message;
    return;
  }
  drawStructure();
}

/* Whether the B-factor column is a fraction or a percentage. ColabFold writes pLDDT out of a
   hundred and Boltz-2 writes it out of one, and a scale read the wrong way round colors a
   confident model as a hopeless one. Decided from the file rather than from the tool, so a
   structure copied in from anywhere reads correctly. */
function plddtScale(text) {
  let most = 0;
  text.split("\n").slice(0, 4000).forEach((line) => {
    if (!/^ATOM|^HETATM/.test(line)) return;
    const b = parseFloat(line.slice(60, 66));
    if (Number.isFinite(b) && b > most) most = b;
  });
  return most > 1.5 ? 1 : 100;
}

function drawStructure() {
  const mol = ENGINE === "molstar";
  if ($("viewer")) $("viewer").hidden = mol;
  if ($("viewerM")) $("viewerM").hidden = !mol;
  if ($("vpick")) $("vpick").textContent = "";
  if (!STRUCT) return;
  if (mol) return drawMolstar();
  if (typeof $3Dmol === "undefined") {
    $("vlabel").textContent = "Loading 3Dmol.js…";
    return loadScript(VENDOR + "3Dmol-min.js").then(draw3Dmol)
      .catch((failure) => { $("vlabel").textContent = failure.message; });
  }
  draw3Dmol();
}

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

/* What a click on the structure says: the chain, the residue and its confidence, in the bar over
   the viewer, where it can be read without taking the eye off the structure for long.

   3Dmol calls back only when a click lands on an atom, so a click on the empty space beside the
   structure used to leave the last residue standing in the bar, naming a residue the pointer was
   nowhere near. A click that hits nothing clears it: PICKED_ATOM says whether this one hit, and
   the pointer having moved says a rotate from a click. */
let PICKED_ATOM = false;
function clearPick() {
  const box = $("vpick");
  if (!box) return;
  box.textContent = "";
  delete box.dataset.chain;
  delete box.dataset.resi;
}
function watchPicking(host) {
  if (!host || host.dataset.picking) return;
  host.dataset.picking = "1";
  let from = null;
  host.addEventListener("pointerdown", (e) => { PICKED_ATOM = false; from = [e.clientX, e.clientY]; });
  host.addEventListener("click", (e) => {
    const moved = from && (Math.abs(e.clientX - from[0]) > 4 || Math.abs(e.clientY - from[1]) > 4);
    /* After the viewer's own handler, which is what sets PICKED_ATOM. */
    setTimeout(() => { if (!PICKED_ATOM && !moved) clearPick(); }, 0);
  });
}
function sayResidue(atom) {
  const box = $("vpick");
  if (!box || !atom) return;
  PICKED_ATOM = true;
  const scale = plddtScale(STRUCT);
  const score = Number.isFinite(atom.b) ? " · pLDDT " + (atom.b * scale).toFixed(1) : "";
  box.textContent = "Chain " + atom.chain + " · " + capital(String(atom.resn || "").toLowerCase()) + " "
    + atom.resi + score;
  box.dataset.chain = atom.chain;
  box.dataset.resi = String(atom.resi);
}

function draw3Dmol() {
  const host = $("viewer");
  if (!host) return;
  if (!V) V = $3Dmol.createViewer(host, { backgroundColor: cssVar("--card") || "white" });
  watchPicking(host);
  V.clear();
  V.addModel(STRUCT, STRUCTFMT);
  const scale = plddtScale(STRUCT);
  const color = (atom) => {
    const b = (atom.b || 0) * scale;
    for (const [cut, hex] of PLDDT) if (b > cut) return hex;
    return PLDDT[PLDDT.length - 1][1];
  };
  V.setStyle({}, { cartoon: { colorfunc: color } });
  /* A ligand has no cartoon, so it would be invisible in a complex that has one. */
  V.setStyle({ hetflag: true }, { stick: { radius: 0.18, colorfunc: color } });
  V.setBackgroundColor(cssVar("--card") || "white");
  V.setClickable({}, true, (atom) => sayResidue(atom));
  /* Resize before zooming, not after: zoomTo frames the structure for the canvas it believes it
     has, and a canvas resized afterwards keeps that framing. The structure then sits small in
     the middle of a box it should be filling. */
  V.resize();
  V.zoomTo();
  V.render();
  $("vlabel").textContent = STRUCTNAME;
  $("vlabel").title = STRUCTNAME + " · 3Dmol.js";
}

/* Mol*: loaded the first time it is asked for (5 MB), then kept. */
function loadScript(src) { return new Promise((ok, bad) => { const sc = document.createElement("script"); sc.src = src; sc.onload = ok; sc.onerror = () => bad(new Error("Could not load " + src)); document.head.appendChild(sc); }); }
function loadCss(href) { if (document.querySelector('link[href="' + href + '"]')) return; const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); }

async function drawMolstar() {
  if (MVBUSY) return;
  MVBUSY = true;
  try {
    if (typeof molstar === "undefined") {
      $("vlabel").textContent = "Loading Mol*…";
      loadCss(VENDOR + "molstar.css");
      await loadScript(VENDOR + "molstar.js");
    }
    /* extensions: [] keeps Mol* from calling out to its data services on start, which matters
       here for the same reason everything else does: there is no route out. */
    if (!MV) MV = await molstar.Viewer.create($("viewerM"), {
      layoutIsExpanded: false, layoutShowControls: false, layoutShowSequence: true,
      layoutShowLog: false, viewportShowExpand: true, collapseLeftPanel: true, extensions: []
    });
    molstarBackground();
    await MV.plugin.clear();
    await MV.loadStructureFromData(STRUCT, STRUCTFMT === "cif" ? "mmcif" : "pdb");
    await molstarPlddt();
    $("vlabel").textContent = STRUCTNAME;
    $("vlabel").title = STRUCTNAME + " · Mol*";
  } catch (failure) {
    $("vlabel").textContent = "Mol* could not draw this: " + failure.message;
  } finally { MVBUSY = false; }
}

/* Mol*, colored as 3Dmol is: AlphaFold's four bands, read from the B-factor column, where both
   tools write pLDDT. Mol*'s own pLDDT theme reads a ModelCIF confidence table these files do not
   have, and would paint every residue "no score". Its B-factor theme takes a list of colors across
   a range, so the list is one entry per point of pLDDT, each in its band's color: neighbors the
   same color, so the bands come out flat, with no blending across them. The theme reverses the
   list, which is why it runs from 100 down. */
async function molstarPlddt() {
  const top = 100 / plddtScale(STRUCT);              // 100 for ColabFold, 1 for Boltz-2
  const band = (p) => {
    for (const [cut, hex] of PLDDT) if (p > cut) return hex;
    return PLDDT[PLDDT.length - 1][1];
  };
  const colors = [];
  for (let p = 100; p >= 0; p--) colors.push(parseInt(band(p).slice(1), 16));
  const plugin = MV.plugin;
  for (const s of plugin.managers.structure.hierarchy.current.structures) {
    await plugin.managers.structure.component.updateRepresentationsTheme(s.components, {
      color: "uncertainty",
      colorParams: { domain: [0, top], list: { kind: "interpolate", colors: colors } },
    });
  }
}

function molstarBackground() {
  if (!MV) return;
  try { MV.plugin.canvas3d.setProps({ renderer: { backgroundColor: parseInt(cssVar("--card").replace("#", ""), 16) } }); } catch (e) { /* ignore */ }
}

/* ---- taking the results somewhere ---- */
function copyBox(run) {
  const box = el("div", "box");
  const title = el("h2", null, "Take the results somewhere");
  title.appendChild(helpButton("results", "taking the results somewhere"));
  box.appendChild(title);
  const wrap = el("div", "copybox");
  const line = el("div", "withbtn");
  const dest = el("input", "inp");
  dest.id = "copydest";
  dest.autocomplete = "off";
  dest.spellcheck = false;
  dest.setAttribute("aria-label", "A folder on the cluster");
  dest.placeholder = "A folder on the cluster, such as " + shortPath(((HELLO && HELLO.scratch) || (HELLO && HELLO.roots[0]) || "") + "/kept");
  line.appendChild(dest);
  const pick = el("button", "btn sm", "Browse");
  pick.type = "button";
  pick.onclick = () => chooseFolder("Choose where to copy it", "Copy here", (path) => { dest.value = path; });
  line.appendChild(pick);
  wrap.appendChild(line);
  [["structures", "Copy the structures"], ["run", "Copy the whole run"]].forEach(([what, says]) => {
    const button = el("button", "btn sm", says);
    button.type = "button";
    button.onclick = async () => {
      const was = button.textContent;
      button.disabled = true; button.textContent = "Copying…";
      try {
        const answer = await api("/api/copy", { path: run.path, dest: dest.value, what: what });
        flash((answer.said || []).join(" ") || ("Copied to " + answer.where));
      } catch (failure) { flash(firstSentence(failure.message), true); }
      finally { button.disabled = false; button.textContent = was; }
    };
    wrap.appendChild(button);
  });
  box.appendChild(wrap);
  if ((run.table || {}).rows && run.table.rows.length) {
    const mine = el("div", "acts");
    const all = el("button", "btn sm", "Save the best model on my computer");
    all.type = "button";
    all.onclick = async () => {
      const row = run.table.rows[0];
      try {
        const answer = await fetch(fileUrl(run, row.file));
        const said = await saveLocally(row.name, await answer.text());
        if (said) flash(row.name + " " + said);
      } catch (failure) { flash("Could not save that: " + failure.message, true); }
    };
    mine.appendChild(all);
    box.appendChild(mine);
  }
  return box;
}

/* ------------------------------------------------------------------ start */
(async function start() {
  if (!TOKEN) { flash("This page needs the token the server printed: open the URL from the terminal.", true); return; }
  try {
    HELLO = await api("/api/hello");
    FIELDS = HELLO.fields || [];
    ENTITIES = HELLO.entities || [];
    LIMITS = HELLO.limits || LIMITS;
    /* What a structure and a FASTA are, from the one list that says so. The file chooser shows
       these, so it cannot come to hide a suffix this page reads perfectly well, or offer one the
       route then refuses. */
    const kinds = (LIMITS.structure_files || []).concat(LIMITS.sequence_files || []);
    if (kinds.length) $("file").accept = kinds.join(",");
    $("appname").textContent = HELLO.app;
    document.title = HELLO.app;
    const root = (HELLO.roots || [])[0] || "";
    /* One muted line, and the cluster named once: the tool's own name is "Boltz-2 on Grace",
       so a line beginning "Grace ·" said it a second time. Where the name does not carry the
       site, the line does. */
    const site = (HELLO.site || {}).name || "";
    const said = site && !new RegExp("\\b" + site.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b", "i")
      .test(HELLO.app || "") ? site + " · runs in " : "Runs in ";
    $("where").textContent = said + shortPath(root);
    $("where").title = HELLO.user + "@" + HELLO.host + " · " + HELLO.tool + " " + HELLO.tool_version + "\n" + root;
    if (!HELLO.installed) {
      $("notinstalled").textContent = firstSentence(HELLO.why_not);
      $("notinstalled").title = HELLO.why_not;
      $("notinstalled").hidden = false;
    } else if (HELLO.dry_run) {
      $("notinstalled").classList.add("dryline");
      $("notinstalled").textContent = "Started as a dry run: Submit writes the run folder and queues nothing.";
      $("notinstalled").hidden = false;
    }
    ROWS = [];
    buildSettings();
    drawRows();
    /* Started as a dry run, Submit is itself the dry run: the banner above says so, and a
       second button beside it reading "Dry run" was the same action under another name. */
    if (HELLO.dry_run) { $("dry").hidden = true; $("dry").disabled = true; }
    check();
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
