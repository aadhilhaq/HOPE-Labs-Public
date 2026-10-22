/* The folding page, for whichever of the two tools the server was started for.
   Two screens: Fold, which collects the entities and queues a run, and Runs, which reads the run
   folders, the queue and the structures.

   Nothing about either tool is written here. The types a row may be and the settings in the rail
   are the tool's own tables, sent by the server, so a type the tool cannot fold is never offered;
   and every refusal comes from the server, which refuses what the job would fail on, in the words
   a person can act on. */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const VENDOR = "/static/vendor/";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* The settings that are about the machine rather than about the molecule. They get a group of
   their own wherever the tool's table puts them, because somebody looking for the card and the
   walltime should not have to open More settings to find half of them. */
const RESOURCE = new Set(["gpu", "walltime", "account"]);

/* AlphaFold's own pLDDT bands and AlphaFold's own colours. A person reading one of these
   structures has seen this scale before, and inventing another one would only mean they had to
   learn it. */
const PLDDT = [[90, "#0053d6", "very high"], [70, "#65cbf3", "confident"],
               [50, "#ffdb13", "low"], [0, "#ff7d45", "very low"]];

let HELLO = null;
let FIELDS = [];
let ENTITIES = [];
let LIMITS = { max_copies: 20, max_tokens: 5000, chains: 26, upload: 2097152 };
let ROWS = [];
/* The server's last answer about the form, kept so that a row drawn again, or the modifications
   dialog opened, shows what is wrong with it without waiting for the next check. */
let LASTCHECK = null;

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
function paintTheme() { theme.innerHTML = isDark() ? "&#9680; light" : "&#9681; dark"; }
/* The key the documentation reads as well, so /docs opens in the theme the page is in, and the
   same key the design pages use, so a person moving between the lab's pages keeps their choice. */
try { if (localStorage.hldesigntheme) document.documentElement.dataset.theme = localStorage.hldesigntheme; } catch (e) { /* private window */ }
theme.onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.hldesigntheme = document.documentElement.dataset.theme; } catch (e) { /* ignore */ }
  paintTheme();
  if (MV) molstarBackground();
};
paintTheme();

/* ------------------------------------------------------------------ screens */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === tab));
    const want = tab.dataset.screen;
    $("screen-setup").hidden = want !== "setup";
    $("screen-runs").hidden = want !== "runs";
    if (want === "runs") loadRuns();
  };
});

/* ------------------------------------------------------------------ the entity rows */
function entityOf(kind) { return ENTITIES.find((e) => e.kind === kind) || ENTITIES[0]; }

/* The caret position, counted in characters of the plain text: the only measure that survives
   the box being drawn again. Taken from the docking console's peptide rows, which solved this. */
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
function modName(entity, code) {
  const found = (entity.modifications || []).find(([c]) => c === code);
  return found ? ", " + found[1] : "";
}
function cloneRow(row) {
  return Object.assign({}, row, { modifications: (row.modifications || []).map((m) => Object.assign({}, m)) });
}
function checkSoon() { clearTimeout(typing); typing = setTimeout(check, 300); }
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

/* The box a row is filled in with, by how its type is given: a sequence, typed; one of a list,
   picked; a CCD code, typed; or a SMILES, typed and kept exactly as it was. */
function entryColumn(row, i, entity) {
  const col = el("div", "col sqwrap");
  if (entity.given === "pick") {
    col.appendChild(el("label", null, entity.label));
    const pick = el("select", "inp");
    pick.id = "seq-" + i;
    pick.appendChild(new Option(entity.kind === "ion" ? "Choose an ion…" : "Choose a ligand…", ""));
    (entity.choices || []).forEach(([code, name]) => pick.appendChild(new Option(code + " — " + name, code)));
    pick.value = (entity.choices || []).some(([code]) => code === row.sequence) ? row.sequence : "";
    pick.title = entity.what;
    pick.onchange = () => { row.sequence = pick.value; paintLen(i); paintTotal(); check(); };
    col.appendChild(pick);
  } else if (entity.given === "code") {
    col.appendChild(el("label", null, "CCD code"));
    const code = el("input", "inp mono");
    code.id = "seq-" + i;
    code.maxLength = 5;
    code.spellcheck = false;
    code.autocomplete = "off";
    code.placeholder = entity.placeholder;
    code.value = row.sequence;
    code.title = entity.what;
    code.oninput = () => {
      const at = code.selectionStart;
      code.value = code.value.toUpperCase().replace(/\s+/g, "");
      code.setSelectionRange(at, at);
      row.sequence = code.value;
      paintLen(i); paintTotal(); checkSoon();
    };
    col.appendChild(code);
  } else {
    col.appendChild(el("label", null, entity.given === "smiles" ? "SMILES" : "Sequence"));
    /* A SMILES has no residues to number, so its box has no room kept for the numbers. */
    const seq = el("div", entity.given === "smiles" ? "seq plain" : "seq");
    seq.contentEditable = "plaintext-only";
    seq.spellcheck = false;
    seq.dataset.ph = entity.placeholder;
    seq.id = "seq-" + i;
    renderSeq(seq, row.sequence, entity, row.modifications);
    seq.addEventListener("input", () => {
      const off = caretOffset(seq);
      const raw = seq.textContent.replace(/\s+/g, "");
      row.sequence = row.kind === "smiles" ? raw : raw.toUpperCase();
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
    col.appendChild(seq);
  }
  const len = el("div", "seqlen");
  len.id = "len-" + i;
  col.appendChild(len);
  const err = el("p", "errmsg");
  err.id = "rowerr-" + i;
  err.hidden = true;
  col.appendChild(err);
  return col;
}

function drawRows() {
  const box = $("rows");
  box.innerHTML = "";
  ROWS.forEach((row, i) => {
    const entity = entityOf(row.kind);
    const node = el("div", "ent");
    node.appendChild(el("span", "entnum", String(i + 1)));

    const kindCol = el("div", "col kind");
    kindCol.appendChild(el("label", null, "Entity type"));
    const kind = el("select", "inp");
    ENTITIES.forEach((e) => kind.appendChild(new Option(e.label, e.kind)));
    kind.value = row.kind;
    kind.onchange = () => changeKind(row, kind.value);
    kind.disabled = ENTITIES.length === 1;
    kind.title = entity.what;
    kindCol.appendChild(kind);
    node.appendChild(kindCol);

    const nameCol = el("div", "col nm");
    nameCol.appendChild(el("label", null, "Name"));
    const name = el("input", "inp mono");
    name.value = row.name || "";
    name.placeholder = noun(entity) + " " + (i + 1);
    name.oninput = () => { row.name = name.value; };
    nameCol.appendChild(name);
    node.appendChild(nameCol);

    node.appendChild(entryColumn(row, i, entity));

    const cpCol = el("div", "col cp");
    cpCol.appendChild(el("label", null, "Copies"));
    const spin = el("div", "spin");
    const copies = el("input", "inp mono");
    copies.type = "number";
    copies.min = "1";
    copies.max = String(LIMITS.max_copies);
    copies.value = String(row.copies);
    copies.title = isChain(row) ? "Each copy is another chain of the same sequence."
      : "Each copy is another molecule of the same kind.";
    copies.oninput = () => {
      row.copies = Math.max(1, Math.min(LIMITS.max_copies, parseInt(copies.value, 10) || 1));
      paintLen(i); paintTotal(); armSpin(); checkSoon();
    };
    spin.appendChild(copies);
    /* The two arrows, drawn because the browser's own are five pixels of target clipped against
       the border of a box this tall; see .spin in folding.css. They are taken out of the tab
       order: the box they sit on is a number input, so the keyboard already has up and down, and
       two more stops per row would put six of them between a sequence and the next sequence. */
    const arrows = el("div", "arrows");
    const up = el("button", null, "▲");
    const down = el("button", null, "▼");
    [[up, +1, "one more copy"], [down, -1, "one fewer copy"]].forEach(([button, by, says]) => {
      button.type = "button";
      button.tabIndex = -1;
      button.title = says;
      button.setAttribute("aria-label", says);
      button.onclick = () => {
        /* Read off the box rather than off the row, so an out-of-range number somebody typed
           steps from what they can see. The row keeps the clamped count, which is what the form
           sends, and check_rows is what says the typed one was out of range. */
        const now = parseInt(copies.value, 10) || row.copies;
        const want = Math.max(1, Math.min(LIMITS.max_copies, now + by));
        copies.value = String(want);
        row.copies = want;
        paintLen(i); paintTotal(); armSpin(); checkSoon();
      };
      arrows.appendChild(button);
    });
    function armSpin() {
      const now = parseInt(copies.value, 10) || 1;
      up.disabled = now >= LIMITS.max_copies;
      down.disabled = now <= 1;
    }
    armSpin();
    spin.appendChild(arrows);
    cpCol.appendChild(spin);
    node.appendChild(cpCol);

    /* Everything else about the row, behind one button, as AlphaFold Server has it. */
    const tools = el("div", "tools");
    const more = el("button", "pill dots", "⋮");
    more.type = "button";
    more.title = "Modifications, reverse complement, duplicate, delete";
    more.setAttribute("aria-label", "More for row " + (i + 1));
    more.setAttribute("aria-haspopup", "menu");
    more.onclick = (e) => { e.stopPropagation(); openRowMenu(more, i); };
    tools.appendChild(more);
    node.appendChild(tools);

    box.appendChild(node);
  });
  ROWS.forEach((_r, i) => paintLen(i));
  paintTotal();
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
    item("Modifications…", !row.sequence ? "add the sequence first"
      : n ? n + " on this " + (row.kind === "protein" ? "chain" : "strand") : "",
    () => openMods(i), !row.sequence);
  }
  if (row.kind === "dna" || row.kind === "rna") {
    item("Reverse complement", row.sequence ? "adds the paired strand as the next row" : "add the sequence first",
      () => reverseComplement(i), !row.sequence);
  }
  item("Duplicate", "another row the same as this one", () => {
    ROWS.splice(i + 1, 0, cloneRow(row)); drawRows(); check();
  });
  item("Delete", "", () => {
    ROWS.splice(i, 1);
    if (!ROWS.length) ROWS.push(blankRow());
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
   AlphaFold Server's: the chain laid out residue by residue, a click on one, and a choice of what
   it becomes. Only the modifications made from that residue are offered, and the server checks
   the same thing, because Boltz-2 does not: it puts whatever it is given wherever it is told. */
let MODROW = -1;
let MODAT = 0;
function openMods(i) {
  MODROW = i;
  MODAT = 0;
  let dlg = $("mods");
  if (!dlg) {
    dlg = el("dialog");
    dlg.id = "mods";
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

  const head = el("div", "dlg-h");
  head.appendChild(el("b", null, "Modifications"));
  head.appendChild(el("span", "why", "row " + (MODROW + 1) + ", " + entity.label
    + (row.name ? " " + row.name : "") + ", " + row.sequence.length
    + (row.kind === "protein" ? " residues" : " bases")));
  const shut = el("button", "pill", "✕");
  shut.type = "button";
  shut.title = "Close";
  shut.onclick = () => dlg.close();
  head.appendChild(shut);
  dlg.appendChild(head);

  const body = el("div", "modbody");
  body.appendChild(el("p", "why", "Click a residue, then choose what it becomes. Only the "
    + "modifications made from that residue are listed; another CCD code can be typed."));
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
    pick.appendChild(el("b", "mono", letter + MODAT));
    const choose = el("select", "inp");
    choose.appendChild(new Option(offer.length ? "Choose a modification…" : "None listed for " + letter, ""));
    offer.forEach(([code, name]) => choose.appendChild(new Option(code + " — " + name, code)));
    choose.appendChild(new Option("Another CCD code…", "*"));
    const other = el("input", "inp mono");
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
  if (!mods.length) {
    list.appendChild(el("span", "why", "No modifications on this " + (row.kind === "protein" ? "chain" : "strand") + " yet."));
  }
  mods.forEach((m) => {
    const chip = el("span", "modchip");
    const go = el("button", "modgo", (row.sequence[m.position - 1] || "?") + m.position + " → " + m.ccd + modName(entity, m.ccd));
    go.type = "button";
    go.title = "Choose this residue";
    go.onclick = () => { MODAT = m.position; paintMods(); };
    const drop = el("button", "modx", "✕");
    drop.type = "button";
    drop.title = "Remove " + m.ccd + " from residue " + m.position;
    drop.onclick = () => { row.modifications = (row.modifications || []).filter((n) => n !== m); paintMods(); checkSoon(); };
    chip.appendChild(go);
    chip.appendChild(drop);
    list.appendChild(chip);
  });
  body.appendChild(list);
  const err = el("p", "errmsg");
  err.id = "moderr";
  const says = ((LASTCHECK && LASTCHECK.fields) || {})[MODROW] || ((LASTCHECK && LASTCHECK.fields) || {})[String(MODROW)] || {};
  err.textContent = says.modifications || "";
  err.hidden = !says.modifications;
  body.appendChild(err);
  dlg.appendChild(body);

  const foot = el("div", "dlg-f");
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

/* The line under a row: how long it is, or which molecule it is, and its modifications, each a
   way back into the dialog. */
function paintLen(i) {
  const row = ROWS[i];
  const box = $("len-" + i);
  if (!row || !box) return;
  box.innerHTML = "";
  if (!row.sequence) return;
  const entity = entityOf(row.kind);
  const times = row.copies > 1 ? " × " + row.copies + " copies" : "";
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
    box.textContent = row.sequence.length + " characters of SMILES" + times;
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
}

function paintTotal() {
  const chains = ROWS.filter((r) => isChain(r) && r.sequence);
  const others = ROWS.filter((r) => !isChain(r) && r.sequence);
  const residues = chains.reduce((n, r) => n + r.sequence.length * r.copies, 0);
  const strands = chains.reduce((n, r) => n + r.copies, 0);
  const molecules = others.reduce((n, r) => n + r.copies, 0);
  const said = [];
  if (strands) said.push(strands + (strands === 1 ? " chain, " : " chains, ") + residues + " residues");
  if (molecules) said.push(molecules + (molecules === 1 ? " small molecule or ion" : " small molecules and ions"));
  $("total").textContent = said.length ? said.join("; ") + " in all" : "";
}

$("addrow").onclick = () => { ROWS.push(blankRow()); drawRows(); check(); };

/* ---- pasted and uploaded sequences ---- */
$("pastebtn").onclick = () => { $("pastebox").hidden = !$("pastebox").hidden; if (!$("pastebox").hidden) $("pastetext").focus(); };
$("pastecancel").onclick = () => { $("pastebox").hidden = true; $("pastesays").textContent = ""; };
$("pasteadd").onclick = async () => {
  try {
    const got = await api("/api/paste", { text: $("pastetext").value });
    takeRows(got);
    $("pastebox").hidden = true;
    $("pastetext").value = "";
  } catch (failure) { $("pastesays").textContent = failure.message; }
};
/* ---- the picker: a FASTA, or a run folder, from the cluster's own filesystem ----
   The docking console's dialog and its routes (ADCP_docking/adcp_dock), brought over whole
   rather than invented again: a person who has picked a receptor in that console has already
   learned this. It opens on Places - the group allocation, then scratch, then home - because a
   dialog that opened in a home directory would start where nothing is kept. */
let ROOTS = null;
let PICK = null;

async function places() {
  if (!ROOTS) ROOTS = (await api("/api/roots")).roots || [];
  return ROOTS;
}

function openPicker(how) {
  PICK = how;                                    // {dirs, title, start, onPick}
  $("pickertitle").textContent = how.title;
  // Both only mean anything when a folder is what is being chosen.
  $("pickernew").hidden = $("pickeruse").hidden = !how.dirs;
  $("pickernote").textContent = "";
  /* Emptied before the dialog opens. The listing is fetched, so a dialog opened a second time
     would otherwise show the folder it was last in until the answer arrived - which is a flicker
     to look at and, worse, a file that can be clicked out of a dialog that is choosing a folder. */
  $("listing").innerHTML = "";
  $("crumbs").innerHTML = "";
  $("pickfind").value = "";
  $("pickfindnote").textContent = "";
  if (how.start) showDir(how.start); else showPlaces();
  const box = $("picker");
  if (box.showModal) box.showModal(); else box.setAttribute("open", "");
}
function closePicker() {
  const box = $("picker");
  if (box.close) box.close(); else box.removeAttribute("open");
}
$("pickerclose").onclick = closePicker;
$("picker").addEventListener("cancel", closePicker);
/* Escape clears the filter before it closes the dialog, so a filter that matched nothing does
   not cost the whole navigation that got there. */
$("pickfind").addEventListener("input", pickFilter);
$("pickfind").addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("pickfind").value) {
    e.stopPropagation(); e.preventDefault();
    $("pickfind").value = ""; pickFilter();
  }
});

async function showPlaces() {
  let roots = [];
  try { roots = await places(); } catch (failure) { $("pickernote").textContent = failure.message; }
  $("crumbs").innerHTML = "<b>Places</b>";
  $("listing").innerHTML = roots.map((r) =>
    '<button class=place data-go="' + esc(r.path) + '">\u{1F4CC} ' + esc(r.label)
    + "<span class=sz>" + esc(r.path) + "</span></button>").join("")
    || '<span class=why>nowhere this page can start from</span>';
  wireListing();
}

function crumbsFor(path) {
  /* Places / scratch / user / name, every part its own button, so going back up is one click
     rather than a path retyped. */
  const parts = String(path || "").split("/").filter(Boolean);
  let at = "";
  const bits = ['<button data-go="">Places</button>'];
  parts.forEach((part) => {
    at += "/" + part;
    bits.push('<button data-go="' + esc(at) + '">' + esc(part) + "</button>");
  });
  return bits.join(" / ");
}

function sizeOf(n) {
  return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB"
    : n >= 1024 ? Math.round(n / 1024) + " kB" : n + " B";
}

async function showDir(path) {
  if (!path) return showPlaces();
  let got;
  try {
    got = await api("/api/browse?path=" + encodeURIComponent(path)
      + (PICK && PICK.dirs ? "&dirs=1" : ""));
  } catch (failure) { got = { error: failure.message }; }
  if (got.error) {
    /* Said where the listing would have been, with the crumbs left alone: a folder nobody is
       allowed to read is a reason to go somewhere else, not to close the dialog. */
    $("listing").innerHTML = '<span class=why>' + esc(got.error) + "</span>";
    return;
  }
  PICK.at = got.path;
  /* A filter belongs to the folder it was typed in. Carried into the next one it hides most of
     what is there, which reads as an empty folder. */
  $("pickfind").value = "";
  $("crumbs").innerHTML = crumbsFor(got.path);
  $("pickernote").innerHTML = got.writable ? "" : "not writable by you";
  const files = got.files || [];
  $("listing").innerHTML =
    '<button data-go="' + esc(got.parent) + '">\u2191 up</button>'
    + files.map((f) => '<button data-name="' + esc(f.name) + '" data-pick="' + esc(f.path)
      + '">\u{1F4C4} ' + esc(f.name) + "<span class=sz>" + sizeOf(f.size) + "</span></button>").join("")
    + (files.length && got.dirs.length ? '<span class=why>folders</span>' : "")
    + got.dirs.map((d) => '<button data-name="' + esc(d.name) + '" data-go="' + esc(d.path)
      + '">\u{1F4C1} ' + esc(d.name) + "</button>").join("")
    + (got.dirs.length || files.length ? ""
      : '<span class=why>' + (PICK.dirs ? "no folders here"
        : "no sequence file here. A FASTA is .fasta, .fa, .faa, .txt or .seq") + "</span>")
    + (got.truncated ? '<span class=why>the first ' + got.most
      + " of this folder; type above to find a name in the rest</span>" : "");
  wireListing();
}

/* Filter what is already on the screen, in the page rather than on the server: the folder's
   contents are here, so a request per keystroke would be slower than the scrolling it replaces.
   The crumbs and the up button are left alone, so a filter matching nothing still lets you
   leave. */
function pickFilter() {
  const want = $("pickfind").value.trim().toLowerCase();
  let shown = 0, total = 0;
  $("listing").querySelectorAll("button").forEach((b) => {
    if (!b.hasAttribute("data-name")) { b.hidden = false; return; }   // the up button
    total++;
    const hit = !want || b.getAttribute("data-name").toLowerCase().includes(want);
    b.hidden = !hit;
    if (hit) shown++;
  });
  // the "folders" divider is noise when everything under it is hidden
  $("listing").querySelectorAll("span.why").forEach((n) => {
    if (n.textContent.trim() === "folders") n.hidden = !!want;
  });
  $("pickfindnote").textContent = want
    ? (shown ? shown + " of " + total : 'nothing here matches "' + want + '"') : "";
}

function wireListing() {
  pickFilter();
  $("listing").querySelectorAll("button[data-go]").forEach((b) => {
    b.onclick = () => showDir(b.getAttribute("data-go"));
  });
  $("listing").querySelectorAll("button[data-pick]").forEach((b) => {
    b.onclick = () => { const got = b.getAttribute("data-pick"); closePicker(); PICK.onPick(got); };
  });
  $("crumbs").querySelectorAll("button[data-go]").forEach((b) => {
    b.onclick = () => showDir(b.getAttribute("data-go"));
  });
}

/* A run folder that does not exist yet is usually what is wanted: a folder beside last week's
   runs rather than inside them. It is made here and opened, so the next click is Use this one. */
$("pickernew").onclick = async () => {
  if (!PICK || !PICK.at) return;
  const name = prompt("Name for the new folder, inside\n" + PICK.at);
  if (name === null) return;
  let got;
  try { got = await api("/api/mkdir", { parent: PICK.at, name: name }); }
  catch (failure) { got = { error: failure.message }; }
  if (got.error) { $("pickernote").textContent = got.error; return; }
  $("pickernote").textContent = got.existed ? "that folder was already here" : "made";
  showDir(got.path);
};
$("pickeruse").onclick = () => {
  if (!PICK || !PICK.at) return;
  const got = PICK.at;
  closePicker();
  PICK.onPick(got);
};

/* The two things on this page that are a path on the cluster. A FASTA read here crosses nothing:
   the page's own process opens it, which is why there is no size to send and no cap to explain. */
$("fastabrowse").onclick = () => openPicker({
  dirs: false, title: "Choose a FASTA on " + (HELLO && HELLO.site ? HELLO.site.name : "this cluster"),
  onPick: async (path) => {
    try {
      takeRows(await api("/api/sequences", { path: path }));
    } catch (failure) { flash(failure.message, true); }
  },
});

$("outbrowse").onclick = () => openPicker({
  dirs: true, title: "Choose where this run goes",
  start: $("out").value.trim() || (HELLO && HELLO.roots ? HELLO.roots[0] : ""),
  onPick: (path) => {
    $("out").value = path;
    clearTimeout(typing); typing = setTimeout(check, 60);
  },
});

$("uploadbtn").onclick = () => $("uploadfile").click();
$("uploadfile").onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > LIMITS.upload) {
    flash("that file is " + (file.size / 1e6).toFixed(1) + " MB, and "
      + Math.round(LIMITS.upload / 1048576) + " MB is the most this page takes", true);
    e.target.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      takeRows(await api("/api/upload", {
        filename: file.name, data: String(reader.result).split(",")[1] || ""
      }));
    } catch (failure) { flash(failure.message, true); }
  };
  reader.onerror = () => flash("could not read that file from your computer", true);
  reader.readAsDataURL(file);
  e.target.value = "";
};

function takeRows(got) {
  const rows = got.rows || [];
  /* An empty first row is the one the page starts with, and it is replaced rather than left
     above what was just read. */
  if (ROWS.length === 1 && !ROWS[0].sequence) ROWS = [];
  rows.forEach((r) => ROWS.push({ kind: r.kind, name: r.name || "", sequence: r.sequence, copies: r.copies || 1, modifications: [] }));
  drawRows();
  check();
  const said = got.said || [];
  /* One message, not two. `from` is set only for a file read on the cluster, and it is said
     because that path is the only record of where those sequences came from: a file sent up from
     a laptop is still on the laptop, and this one was never copied anywhere. */
  flash(rows.length + (rows.length === 1 ? " sequence read" : " sequences read")
    + (got.from ? " from " + got.from + ", on the cluster; nothing was uploaded" : "")
    + (said.length ? ". " + said.join(" ") : ""), said.length > 0);
}

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

/* Two arrows on a box. The browser's own are a few pixels of target clipped against the border
   and on a touchpad they are unhittable, so they are drawn; see .spin in folding.css. Out of the
   tab order, because a number input already has up and down from the keyboard. */
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

/* How long this form is likely to want, as a generous allowance rather than a measurement. It
   follows the one thing the time actually follows - how much there is to fold - and the models
   and recycles asked for. Rounded up to the half hour, never under an hour, and clamped to what
   the partition allows. A job that ends early is charged for what it used; one that runs out has
   to be queued again from the beginning, so the rounding is deliberately generous. */
function suggestWalltime() {
  const residues = ROWS.reduce((n, r) => n + rowLength(r) * r.copies, 0);
  if (!residues) return 0;
  const n = (key, fallback) => {
    const box = $(key);
    const got = box ? Number(box.value) : NaN;
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

function fieldNode(f) {
  const wrap = el("div", "fld");
  if (f.kind === "yesno") {
    const tick = el("label", "tick");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = f.key;
    input.checked = !!f.default;
    tick.appendChild(input);
    tick.appendChild(el("span", null, f.label));
    if (f.why) tick.appendChild(el("span", "why", f.why));
    wrap.appendChild(tick);
    return wrap;
  }
  const label = el("label", "f");
  label.htmlFor = f.key;
  label.appendChild(el("span", "fname", f.label));
  if (f.why) label.appendChild(el("span", "why", f.why));
  wrap.appendChild(label);

  if (f.kind === "choice") {
    const select = el("select", "inp");
    select.id = f.key;
    f.choices.forEach((choice) => select.appendChild(new Option(choiceName(f, choice), choice)));
    select.value = f.default == null ? "" : String(f.default);
    wrap.appendChild(select);
  } else if (f.kind === "number") {
    const spin = el("div", "spin");
    const input = el("input", "inp mono");
    input.id = f.key;
    input.type = "number";
    input.autocomplete = "off";
    if (f.least != null) input.min = String(f.least);
    if (f.most != null) input.max = String(f.most);
    if (f.step != null) input.step = String(f.step);
    input.placeholder = f.placeholder || (f.optional ? "optional" : "");
    if (f.default != null && f.default !== "") input.value = String(f.default);
    const by = Number(f.step) || 1;
    const step = (way) => {
      const from = input.value === "" ? (f.least != null ? Number(f.least) : 0)
        : Number(input.value);
      let want = (Number.isFinite(from) ? from : 0) + way * by;
      if (f.least != null) want = Math.max(Number(f.least), want);
      if (f.most != null) want = Math.min(Number(f.most), want);
      input.value = String(Math.round(want * 1e6) / 1e6);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    spin.appendChild(input);
    spin.appendChild(spinArrows(step, "more", "less"));
    wrap.appendChild(spin);
    if (f.least != null && f.most != null) {
      wrap.appendChild(el("span", "why bounds", f.least + " to " + f.most));
    }
  } else if (f.kind === "duration") {
    /* Typing 04:00:00 to ask for four hours is eight keystrokes and two chances to get it wrong.
       The arrows move it by the half hour; Estimate fills in an allowance from what is on the
       form, which is the only thing here that knows how much there is to fold. */
    const spin = el("div", "spin");
    const input = el("input", "inp mono");
    input.id = f.key;
    input.type = "text";
    input.autocomplete = "off";
    input.placeholder = f.placeholder || "04:00:00";
    if (f.default != null && f.default !== "") input.value = String(f.default);
    const by = Number(f.step) || 1800;
    const step = (way) => {
      const now = walltimeSeconds(input.value);
      const from = Number.isFinite(now) ? now : (Number(f.least) || 3600);
      let want = from + way * by;
      if (f.least != null) want = Math.max(Number(f.least), want);
      if (f.most != null) want = Math.min(Number(f.most), want);
      input.value = asWalltime(want);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    spin.appendChild(input);
    spin.appendChild(spinArrows(step, "half an hour more", "half an hour less"));
    wrap.appendChild(spin);
    const acts = el("div", "fldacts");
    const guess = el("button", "btn sm", "Estimate");
    guess.type = "button";
    guess.id = "walltime-estimate";
    guess.title = "A generous allowance worked out from the sequences on this form and the "
      + "models asked for. It is a guess, not a measurement; a job that ends early is charged "
      + "for what it used.";
    guess.onclick = () => {
      const want = suggestWalltime();
      if (!want) {
        flash("Put a sequence on the form first: the estimate follows how much there is to fold.",
              true);
        return;
      }
      input.value = asWalltime(want);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    acts.appendChild(guess);
    wrap.appendChild(acts);
  } else {
    const input = el("input", "inp mono");
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

/* The alignment choice is the only setting whose values are not self-explanatory, because what
   they mean is where the work happens rather than a number. */
function choiceName(f, choice) {
  const note = (f.notes || {})[choice];
  if (note) return choice + " \u2014 " + note;
  if (f.key !== "alignments") return choice === "" ? "the tool's own" : choice;
  return choice === "fetch" ? "fetch them on the login node" : "fold from the sequence alone";
}

function buildRail() {
  const groups = { "fields-main": [], "fields-res": [], "fields-more": [] };
  FIELDS.forEach((f) => {
    if (RESOURCE.has(f.key)) groups["fields-res"].push(f);
    else if (f.advanced) groups["fields-more"].push(f);
    else groups["fields-main"].push(f);
  });
  Object.entries(groups).forEach(([where, list]) => {
    const box = $(where);
    box.innerHTML = "";
    list.forEach((f) => box.appendChild(fieldNode(f)));
  });
  $("morecount").textContent = groups["fields-more"].length + " more";
  $("g-tool-name").textContent = HELLO.app.replace(/ on .*/, "") + " settings";
  FIELDS.concat([{ key: "name" }, { key: "out" }]).forEach((f) => {
    const input = $(f.key);
    if (!input) return;
    const kind = input.tagName === "SELECT" || input.type === "checkbox" ? "change" : "input";
    input.addEventListener(kind, () => { clearTimeout(typing); typing = setTimeout(check, 280); });
  });
}

$("g-more").onclick = () => {
  const box = $("fields-more");
  box.hidden = !box.hidden;
  $("morecount").textContent = box.hidden ? FIELDS.filter((f) => f.advanced && !RESOURCE.has(f.key)).length + " more" : "hide";
};

/* ------------------------------------------------------------------ checking */
function form() {
  const body = {
    rows: ROWS.map((r) => ({ kind: r.kind, name: r.name, sequence: r.sequence, copies: r.copies,
                             modifications: isChain(r) ? (r.modifications || []) : [] })),
    name: $("name").value, out: $("out").value
  };
  FIELDS.forEach((f) => {
    const input = $(f.key);
    if (!input) return;
    body[f.key] = input.type === "checkbox" ? input.checked : input.value;
  });
  return body;
}

let TOLD = { input: "", command: "", what: "" };
let TOLDMODE = "input";
document.querySelectorAll("#toldmodes button").forEach((b) => {
  b.onclick = () => {
    TOLDMODE = b.dataset.told;
    document.querySelectorAll("#toldmodes button").forEach((o) => o.classList.toggle("on", o === b));
    paintTold();
  };
});
function paintTold() {
  $("toldtext").textContent = TOLDMODE === "input" ? TOLD.input : TOLD.command;
  $("toldwhat").textContent = TOLD.input
    ? (TOLDMODE === "input"
      ? "Written to " + TOLD.what + " in the run folder, and read by the job exactly as it is here."
      : "Run in the job, on the compute node, with no network.")
    : "Add an entity to see it.";
}

function showErrors(answer) {
  LASTCHECK = answer;
  const fields = answer.fields || {};
  ROWS.forEach((_r, i) => {
    const row = fields[i] || fields[String(i)] || {};
    const says = row.sequence || row.kind || row.modifications || row.copies || "";
    const err = $("rowerr-" + i);
    const seq = $("seq-" + i);
    if (err) { err.textContent = says; err.hidden = !says; }
    if (seq) seq.classList.toggle("bad", !!(row.sequence || row.kind || row.modifications));
  });
  const moderr = $("moderr");
  if (moderr && MODROW >= 0) {
    const row = fields[MODROW] || fields[String(MODROW)] || {};
    moderr.textContent = row.modifications || "";
    moderr.hidden = !row.modifications;
  }
  const settings = fields.settings || {};
  FIELDS.concat([{ key: "out" }]).forEach((f) => {
    const box = $(f.key + "-err");
    const input = $(f.key);
    const says = settings[f.key] || "";
    if (box) { box.textContent = says; box.hidden = !says; }
    if (input) input.classList.toggle("bad", !!says);
  });
  $("g-res").classList.toggle("bad", !!(settings.walltime || settings.gpu));
}

function paintPlan(plan, notes) {
  const box = $("plan");
  box.innerHTML = "";
  if (!plan || !plan.gpu) {
    box.appendChild(el("div", "k", "plan"));
    box.appendChild(el("div", "n", "Add an entity to see the card, the partition and the memory the job asks for."));
  } else {
    box.appendChild(el("div", "k", "slurm on " + plan.site));
    box.appendChild(el("div", "v", "1 × " + plan.gpu + " on " + plan.partition + ", "
      + plan.cpus + " cores, " + plan.mem + " GB, " + plan.walltime));
    box.appendChild(el("div", "n", plan.tokens + " tokens, one a residue and one an atom of a small molecule"
      + (plan.card_memory ? " on a card with " + plan.card_memory + " GB" : "")
      /* Said, because the card in the line above is not the word on the form. Leaving auto on
         is the common case and the right one, and somebody reading this box should be able to
         see which card that got them without going looking for it. On a cluster whose cards are
         unknown here there is no card to name, and saying one would be inventing it. */
      + (plan.chosen ? ""
        : plan.any_card
          ? ". No kind of card is named: nobody here has a table of " + plan.site
            + "'s cards, so Slurm gives the job whichever it has"
          : ". The card was left on auto, so the job asks for " + plan.gpu
            + ", the largest " + plan.site + " has")
      + (plan.alignments === "fetch"
        ? ". The alignments are fetched here, on the login node, when you press Submit; the job itself runs with no network."
        : ". No alignment is fetched, so the job can be queued at once.")
      + (plan.account ? " Charged to " + plan.account + "." : " Charged to your default account.")));
  }
  const note = $("notes");
  note.textContent = (notes || []).join(". ");
  note.hidden = !(notes || []).length;
  note.className = "note" + ((notes || []).length ? " warn" : "");
}

function paintProblems(problems) {
  const list = $("problems");
  list.innerHTML = "";
  (problems || []).forEach((p) => list.appendChild(el("li", null, p)));
  $("probbox").hidden = !(problems || []).length;
}

let typing = 0;
let checking = 0;
async function check() {
  const mine = ++checking;
  paintTotal();
  if (!ROWS.some((r) => r.sequence)) {
    $("go").disabled = $("dry").disabled = true;
    TOLD = { input: "", command: "", what: "" };
    paintTold(); paintPlan(null, []); paintProblems([]); showErrors({});
    $("phaseone").hidden = true;
    return;
  }
  try {
    const answer = await api("/api/check", form());
    if (mine !== checking) return;                     // a later keystroke already asked
    showErrors(answer);
    paintProblems(answer.problems);
    paintPlan(answer.plan, answer.notes);
    const told = answer.told || {};
    TOLD = { input: told.text || "", command: told.command || "", what: told.what || "" };
    paintTold();
    $("phaseone").textContent = told.phase_one ? "Before the job is queued: " + told.phase_one : "";
    $("phaseone").hidden = !told.phase_one;
    if (answer.name && !$("name").value) $("name").placeholder = answer.name;
    const clean = !(answer.problems || []).length
      && !Object.keys(answer.fields || {}).some((k) => Object.keys(answer.fields[k] || {}).length);
    $("go").disabled = $("dry").disabled = !clean;
    $("g-tool").classList.toggle("done", clean);
  } catch (failure) {
    if (mine === checking) flash(failure.message, true);
  }
}

/* ------------------------------------------------------------------ submitting */
async function go(dry) {
  const button = dry ? $("dry") : $("go");
  const was = button.textContent;
  button.disabled = true;
  button.textContent = dry ? "writing…" : "fetching alignments…";
  try {
    const answer = await api("/api/submit", Object.assign(form(), { dry_run: dry }));
    if (answer.problems || answer.fields) {
      showErrors(answer);
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
  a.style.textDecoration = "none";
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
  a.title = "The write-up of this run: the chains, the samples, the confidence along them and "
    + "what it was folded from. Opens in a new tab, ready to save as a PDF.";
  a.style.textDecoration = "none";
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
  head.appendChild(el("h1", null, run.name));
  head.appendChild(el("span", "pill" + (run.state === "RUNNING" ? " on" : ""), run.state || "not queued"));
  if (run.job_id) head.appendChild(el("span", "why", "job " + run.job_id
    + (run.elapsed ? ", " + run.elapsed + " elapsed" : "") + (run.left ? ", " + run.left + " left" : "")));
  if (run.reason) head.appendChild(el("span", "why", run.reason));
  if ((run.entities || []).length) {
    const again = el("button", "btn sm", "Reuse");
    again.title = "Put this run's entities and settings on the Fold page, to run it again or change it";
    again.onclick = () => reuseRun(run);
    head.appendChild(again);
  }
  main.appendChild(head);

  const counts = run.counts || {};
  const tiles = el("div", "tiles");
  tiles.appendChild(tile("structures", run.structures == null ? "—" : String(run.structures),
    run.structures == null ? "written when the job finishes" : "best first"));
  tiles.appendChild(tile("alignments", String(counts.alignments || 0),
    run.alignments === "single sequence" ? "folded from the sequence alone" : "fetched on the login node"));
  const chains = (run.rows || []).reduce((n, r) => n + (r.copies || 1), 0);
  tiles.appendChild(tile("chains", String(chains),
    (run.rows || []).map((r) => r.kind).filter((k, i, a) => a.indexOf(k) === i).join(", ")));
  /* The card out of the plan rather than out of the settings: the settings hold the word that
     was on the form, which for almost every run is "auto", and what somebody looking at a
     finished run wants to know is which card it actually ran on. */
  tiles.appendChild(tile("residues", String((run.plan || {}).tokens || "—"),
    [(run.plan || {}).gpu || (run.asked || {}).gpu,
     (run.asked || {}).walltime].filter(Boolean).join(", ")));
  main.appendChild(tiles);

  /* The entities, as they were given. A structure a year later is worth nothing without them,
     and the input file carries chain ids and alignment paths rather than row names. */
  const what = el("div", "box");
  what.appendChild(el("h2", null, "What was folded"));
  const kv = el("dl", "kv");
  (run.rows || []).forEach((row, i) => {
    kv.appendChild(el("dt", null, (row.name || row.kind) + " · " + row.chains.join("")));
    /* A record written before ligands were named says ligand and 1, which was one molecule;
       anything since gives the code, and its size in atoms. */
    const n = row.residues;
    const unit = row.kind === "protein" ? "residue"
      : (row.kind === "dna" || row.kind === "rna") ? "base"
        : (row.ccd || row.kind === "smiles") ? "heavy atom" : "molecule";
    const dd = el("dd", null, entityOf(row.kind).label + (row.ccd ? " " + row.ccd : "") + ", "
      + n + " " + unit + (n === 1 ? "" : "s")
      + (row.copies > 1 ? ", " + row.copies + " copies" : "")
      + ((row.modifications || []).length ? ", modified at " + row.modifications.join(", ") : ""));
    /* The sequence itself, as it was typed, under its line: a structure a month later is read
       against the sequence that went in, and the input file is the place nobody looks. */
    const typed = ((run.entities || [])[i] || {}).sequence || "";
    if (typed && !row.ccd) {
      const line = el("div", "seqshow");
      line.appendChild(el("code", null, typed));
      const copy = el("button", "pill", "copy");
      copy.title = "Copy this sequence";
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(typed); flash("Copied " + typed.length + " characters."); }
        catch (failure) { flash("could not copy it: " + failure.message, true); }
      };
      line.appendChild(copy);
      dd.appendChild(line);
    }
    kv.appendChild(dd);
  });
  [["alignments", run.alignments || "—"],
   ["submitted", run.submitted || "—"],
   ["owner", run.owner || "—"]].forEach(([k, v]) => {
    kv.appendChild(el("dt", null, k)); kv.appendChild(el("dd", null, String(v)));
  });
  what.appendChild(kv);

  const actions = el("div", "btns");
  if ((run.table && run.table.rows.length) || run.over) actions.appendChild(reportButton(run));
  (run.files || []).forEach((name) => {
    if (/\.(yaml|fasta)$/.test(name)) actions.appendChild(download(run, name, "The input"));
    else if (/job\.sbatch$/.test(name)) actions.appendChild(download(run, name, "Job script"));
    else if (/entities\.json$/.test(name)) actions.appendChild(download(run, name, "The entities"));
  });
  if (run.log_name) actions.appendChild(download(run, run.log_name, "Full log"));
  if (run.job_id && !run.over) {
    const stop = el("button", "btn sm", "Cancel the job");
    stop.onclick = async () => {
      if (!confirm("Cancel job " + run.job_id + "? The alignments stay in the run folder, so queueing it again needs no network.")) return;
      try { const a = await api("/api/cancel", { path: run.path }); flash(a.message, !a.ok); loadRuns(run.path); }
      catch (failure) { flash(failure.message, true); }
    };
    actions.appendChild(stop);
  } else if (run.job_id) {
    const again = el("button", "btn sm", "Queue again");
    again.title = "the same job script: phase two only, so it needs no network";
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
    const box = el("div", "box");
    box.appendChild(el("h2", null, "What the job was told · " + run.told.what));
    box.appendChild(el("pre", "told", run.told.text));
    if (run.told.command) {
      box.appendChild(el("h2", null, "The command it ran"));
      box.appendChild(el("pre", "told", run.told.command));
    }
    main.appendChild(box);
  }

  if (run.log) {
    const box = el("div", "box");
    box.appendChild(el("h2", null, "Log · " + run.log_name));
    const pre = el("pre", "log", run.log);
    box.appendChild(pre);
    main.appendChild(box);
    pre.scrollTop = pre.scrollHeight;
  }
}

function fmt(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) && String(value).includes(".") ? n.toFixed(3) : String(value);
}

/* A run's entities and settings, back on the Fold page. Everything but the name of a folder: a
   run given the folder of the one it copies would be put in a folder inside it, which is not what
   anybody pressing Reuse means. A ligand row from before ligands were picked by name was a
   SMILES, or a code, and comes back as one, rather than as a ligand row with nothing picked. */
function reuseRun(run) {
  const listed = new Set(((ENTITIES.find((e) => e.kind === "ligand") || {}).choices || []).map(([c]) => c));
  ROWS = (run.entities || []).map((r) => {
    let kind = r.kind || "protein";
    const seq = String(r.sequence || "");
    if (kind === "ligand" && !listed.has(seq.toUpperCase())) kind = /^[A-Za-z0-9]{1,5}$/.test(seq) ? "ccd" : "smiles";
    if (!ENTITIES.some((e) => e.kind === kind)) kind = ENTITIES[0].kind;
    return { kind, name: r.name || "", sequence: seq, copies: r.copies || 1,
             modifications: (r.modifications || []).map((m) => ({ position: m.position, ccd: m.ccd })) };
  });
  if (!ROWS.length) ROWS = [blankRow()];
  const asked = run.asked || {};
  FIELDS.forEach((f) => {
    const box = $(f.key);
    if (!box || !(f.key in asked) || asked[f.key] == null) return;
    if (box.type === "checkbox") box.checked = !!asked[f.key];
    else box.value = String(asked[f.key]);
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.dispatchEvent(new Event("change", { bubbles: true }));
  });
  $("name").value = run.name || "";
  $("out").value = "";
  const tab = document.querySelector('.tab[data-screen="setup"]');
  if (tab) tab.click();
  drawRows();
  check();
  window.scrollTo(0, 0);
  flash(run.name + " is on the Fold page: " + ROWS.length + (ROWS.length === 1 ? " entity" : " entities")
    + " and its settings. Change what you want, and Submit.");
}

function structuresBox(run) {
  const box = el("div", "box");
  box.appendChild(el("h2", null, "Structures, best first · " + run.table.total + " in all"));
  const wrap = el("div", "tblbox");
  const table = el("table", "t");
  const head = el("tr");
  ["", "rank", "confidence", "pLDDT", "pTM", "ipTM", "chains", "file", ""].forEach((c) => head.appendChild(el("th", null, c)));
  table.appendChild(el("thead")).appendChild(head);
  const body = el("tbody");
  run.table.rows.forEach((row, i) => {
    const tr = el("tr");
    const pick = el("td");
    const button = el("button", "pill" + (i === pickedRow(run) ? " on" : ""), "view");
    button.onclick = () => {
      body.querySelectorAll(".pill").forEach((p) => p.classList.remove("on"));
      button.classList.add("on");
      PICKED[run.path] = row.file;
      showStructure(run, row);
    };
    pick.appendChild(button);
    tr.appendChild(pick);
    tr.appendChild(el("td", null, String(row.rank)));
    tr.appendChild(el("td", null, fmt(row.confidence)));
    tr.appendChild(el("td", null, fmt(row.plddt)));
    tr.appendChild(el("td", null, fmt(row.ptm)));
    tr.appendChild(el("td", null, fmt(row.iptm)));
    tr.appendChild(el("td", null, row.chains || "—"));
    tr.appendChild(el("td", "wrap", row.name));
    const save = el("td");
    const one = el("button", "pill", "save");
    one.title = "Save this structure on your own computer";
    one.onclick = async () => {
      try {
        const answer = await fetch(fileUrl(run, row.file));
        const said = await saveLocally(row.name, await answer.text());
        if (said) flash(row.name + " " + said);
      } catch (failure) { flash("could not save that: " + failure.message, true); }
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

/* ---- the viewer: 3Dmol by default, Mol* on request, as HOPE-MD and the docking console do.
   Both are bundled in /static/vendor, because the page is reached through a forwarded port and a
   browser on it can fetch from the page and from nowhere else. */
let V = null, MV = null, MVBUSY = false, ENGINE = "3dmol", STRUCT = "", STRUCTFMT = "pdb", STRUCTNAME = "";

function forgetViewers() {
  V = null;
  if (MV) { try { MV.plugin.dispose(); } catch (e) { /* it may already be gone */ } }
  MV = null;
  STRUCT = "";
}
try { if (localStorage.hlfoldviewer) ENGINE = localStorage.hlfoldviewer; } catch (e) { /* ignore */ }

function viewerBox(run) {
  const box = el("div", "box");
  const bar = el("div", "vbar");
  bar.appendChild(el("span", "tk", "structure"));
  const engine = el("select", "inp");
  engine.style.flex = "0 0 auto";
  engine.style.width = "auto";
  [["3dmol", "3Dmol.js"], ["molstar", "Mol*"]].forEach(([v, n]) => engine.appendChild(new Option(n, v)));
  engine.value = ENGINE;
  engine.onchange = () => {
    ENGINE = engine.value;
    try { localStorage.hlfoldviewer = ENGINE; } catch (e) { /* ignore */ }
    drawStructure();
  };
  bar.appendChild(engine);
  const vlabel = el("span", "why");
  vlabel.id = "vlabel";
  bar.appendChild(vlabel);
  box.appendChild(bar);
  const wrap = el("div", "vwrap");
  const a = el("div"); a.id = "viewer";
  const b = el("div"); b.id = "viewerM"; b.hidden = true;
  wrap.appendChild(a); wrap.appendChild(b);
  /* The key sits in the viewer's own corner, over whichever viewer is showing. It used to be a
     strip under the box, which a tall viewer pushed off the bottom of the window, so the colours
     were there and what they meant was not. */
  const key = el("div", "plddtkey");
  key.setAttribute("aria-label", "Model confidence, by pLDDT");
  key.appendChild(el("div", "pkeyhead", "Model confidence (pLDDT)"));
  PLDDT.forEach(([cut, colour, name], i) => {
    /* The bands read as the documentation's table reads them: above, between, and below, rather
       than a nought that is not a pLDDT anybody has. */
    const says = !i ? "> " + PLDDT[0][0]
      : (cut ? cut + " to " + PLDDT[i - 1][0] : "< " + PLDDT[i - 1][0]);
    const row = el("div", "pkeyrow");
    const swatch = el("span", "pkeyswatch");
    swatch.style.background = colour;
    row.appendChild(swatch);
    row.appendChild(el("span", null, name.charAt(0).toUpperCase() + name.slice(1) + " (" + says + ")"));
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
  if (label) label.textContent = "reading " + row.name + "…";
  try {
    const answer = await fetch(fileUrl(run, row.file));
    STRUCT = await answer.text();
  } catch (failure) {
    if (label) label.textContent = "could not read that structure: " + failure.message;
    return;
  }
  drawStructure();
}

/* Whether the B-factor column is a fraction or a percentage. ColabFold writes pLDDT out of a
   hundred and Boltz-2 writes it out of one, and a scale read the wrong way round colours a
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
  if (!STRUCT) return;
  if (mol) return drawMolstar();
  if (typeof $3Dmol === "undefined") {
    $("vlabel").textContent = "loading 3Dmol.js…";
    return loadScript(VENDOR + "3Dmol-min.js").then(draw3Dmol)
      .catch((failure) => { $("vlabel").textContent = failure.message; });
  }
  draw3Dmol();
}

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function draw3Dmol() {
  const host = $("viewer");
  if (!host) return;
  if (!V) V = $3Dmol.createViewer(host, { backgroundColor: cssVar("--card") || "white" });
  V.clear();
  V.addModel(STRUCT, STRUCTFMT);
  const scale = plddtScale(STRUCT);
  const colour = (atom) => {
    const b = (atom.b || 0) * scale;
    for (const [cut, hex] of PLDDT) if (b > cut) return hex;
    return PLDDT[PLDDT.length - 1][1];
  };
  V.setStyle({}, { cartoon: { colorfunc: colour } });
  /* A ligand has no cartoon, so it would be invisible in a complex that has one. */
  V.setStyle({ hetflag: true }, { stick: { radius: 0.18, colorfunc: colour } });
  V.setBackgroundColor(cssVar("--card") || "white");
  /* Resize before zooming, not after: zoomTo frames the structure for the canvas it believes it
     has, and a canvas resized afterwards keeps that framing. The structure then sits small in
     the middle of a box it should be filling. */
  V.resize();
  V.zoomTo();
  V.render();
  $("vlabel").textContent = STRUCTNAME + " · 3Dmol.js";
}

/* Mol*: loaded the first time it is asked for (5 MB), then kept. */
function loadScript(src) { return new Promise((ok, bad) => { const sc = document.createElement("script"); sc.src = src; sc.onload = ok; sc.onerror = () => bad(new Error("could not load " + src)); document.head.appendChild(sc); }); }
function loadCss(href) { if (document.querySelector('link[href="' + href + '"]')) return; const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); }

async function drawMolstar() {
  if (MVBUSY) return;
  MVBUSY = true;
  try {
    if (typeof molstar === "undefined") {
      $("vlabel").textContent = "loading Mol*…";
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
    $("vlabel").textContent = STRUCTNAME + " · Mol*";
  } catch (failure) {
    $("vlabel").textContent = "Mol* could not draw this: " + failure.message;
  } finally { MVBUSY = false; }
}

/* Mol*, coloured as 3Dmol is: AlphaFold's four bands, read from the B-factor column, where both
   tools write pLDDT. Mol*'s own pLDDT theme reads a ModelCIF confidence table these files do not
   have, and would paint every residue "no score". Its B-factor theme takes a list of colours across
   a range, so the list is one entry per point of pLDDT, each in its band's colour: neighbours the
   same colour, so the bands come out flat, with no blending across them. The theme reverses the
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
  box.appendChild(el("h2", null, "Take the results somewhere"));
  box.appendChild(el("p", "note", "A folding run is gigabytes once the alignments are in it, so "
    + "the structures and the whole folder are offered separately. Copying on the cluster is "
    + "much faster than through this page, and the lab's work stays on the cluster."));
  const wrap = el("div", "copybox");
  const where = el("div");
  const label = el("label", "f");
  label.appendChild(el("span", "fname", "A folder on the cluster"));
  label.appendChild(el("span", "why", "Not in your home: it is 10 GB and 10,000 files, and one run is more than both."));
  where.appendChild(label);
  const dest = el("input", "inp mono");
  dest.placeholder = (HELLO.roots[0] || "/scratch/user/<NetID>") + "/kept";
  where.appendChild(dest);
  wrap.appendChild(where);
  const buttons = el("div", "btns");
  [["structures", "Copy the structures"], ["run", "Copy the whole run"]].forEach(([what, says]) => {
    const button = el("button", "btn sm", says);
    button.onclick = async () => {
      const was = button.textContent;
      button.disabled = true; button.textContent = "copying…";
      try {
        const answer = await api("/api/copy", { path: run.path, dest: dest.value, what: what });
        flash((answer.said || []).join(" ") || ("copied to " + answer.where));
      } catch (failure) { flash(failure.message, true); }
      finally { button.disabled = false; button.textContent = was; }
    };
    buttons.appendChild(button);
  });
  wrap.appendChild(buttons);
  box.appendChild(wrap);
  if ((run.table || {}).rows && run.table.rows.length) {
    const mine = el("div", "btns");
    const all = el("button", "btn sm", "Save the best model on my computer");
    all.onclick = async () => {
      const row = run.table.rows[0];
      try {
        const answer = await fetch(fileUrl(run, row.file));
        const said = await saveLocally(row.name, await answer.text());
        if (said) flash(row.name + " " + said);
      } catch (failure) { flash("could not save that: " + failure.message, true); }
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
    $("where").textContent = HELLO.user + " · " + HELLO.host + " · " + HELLO.site.name
      + " · " + HELLO.tool + " " + HELLO.tool_version + (HELLO.dry_run ? " · dry run" : "");
    $("appname").textContent = HELLO.app;
    document.title = HELLO.app;
    /* Lower case for the words and not for the names: "protein", but "DNA" and "SMILES". */
    const named = ENTITIES.map(noun);
    $("takes").textContent = (ENTITIES.length > 1
      ? "one row per chain or molecule: " + named.slice(0, -1).join(", ") + " or " + named[named.length - 1]
      : "one row per chain: " + named[0])
      + ". Copies are more of the same row.";
    if (!HELLO.installed) {
      $("notinstalled").textContent = HELLO.why_not;
      $("notinstalled").hidden = false;
    }
    ROWS = [blankRow()];
    buildRail();
    drawRows();
    if (HELLO.dry_run) $("go").textContent = "Submit (dry run)";
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
