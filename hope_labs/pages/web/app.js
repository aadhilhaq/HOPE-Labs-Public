/* The design page, for whichever of the design tools the server was started for.
   Two screens: Design, which reads a target and queues a run, and Runs, which reads the run
   folders and the queue.

   Nothing about either tool is written here. The rail is built from the card the server sends,
   which is the same card the canvas draws, so a setting added to the card appears on this page
   without a line changing; and every refusal comes from the server, which refuses what a flow
   refuses, in the same words. */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

/* The settings that are about the machine rather than about the science. They get a group of
   their own wherever the card puts them, because somebody looking for the card and the walltime
   should not have to open More settings to find half of them. */
const RESOURCE = new Set(["gpu", "gpus", "walltime", "partition", "account"]);

let HELLO = null;
let FIELDS = [];

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
/* The key the documentation reads as well, so /docs opens in the theme the page is in. */
try { if (localStorage.hldesigntheme) document.documentElement.dataset.theme = localStorage.hldesigntheme; } catch (e) { /* private window */ }
theme.onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.hldesigntheme = document.documentElement.dataset.theme; } catch (e) { /* ignore */ }
  paintTheme();
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

function fieldNode(f) {
  /* One setting, drawn the way its kind says. A yes or no is a tick with the reason beside it;
     a number and a walltime are boxes with arrows, because the card says what they may be and
     stepping between those is quicker and safer than typing; everything else is a labelled box,
     and a choice is a menu of exactly the card's choices. */
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
    f.choices.forEach((choice) => {
      /* An empty choice is "keep the tool's own", which is what the card means by it. */
      const note = (f.notes || {})[choice];
      select.appendChild(new Option(
        note ? choice + " — " + note : (choice === "" ? "the tool's own" : choice), choice));
    });
    select.value = f.default == null ? "" : String(f.default);
    wrap.appendChild(select);
  } else if (f.kind === "number" || f.kind === "duration") {
    const clock = f.kind === "duration";
    const spin = el("div", "spin");
    const input = el("input", "inp mono");
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
  $("g-tool-name").textContent = HELLO.card.name;
  $("g-tool").title = HELLO.card.note || "";
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

$("g-more").onclick = () => {
  const box = $("fields-more");
  box.hidden = !box.hidden;
  $("morecount").textContent = box.hidden ? FIELDS.filter((f) => f.advanced && !RESOURCE.has(f.key)).length + " more" : "hide";
};

/* ------------------------------------------------------------------ the target */
const T = { path: "", name: "", pdb_id: "", chains: [], picked: [] };
let MODE = "pdb";

document.querySelectorAll("#modes button").forEach((b) => {
  b.onclick = () => {
    MODE = b.dataset.mode;
    document.querySelectorAll("#modes button").forEach((o) => o.classList.toggle("on", o === b));
    ["pdb", "file", "path"].forEach((m) => { $("mode-" + m).hidden = m !== MODE; });
    clearTarget();
  };
});

function clearTarget() {
  T.path = ""; T.name = ""; T.pdb_id = ""; T.chains = []; T.picked = [];
  $("targetout").hidden = true;
  check();
}

function targetError(text) {
  $("terr").textContent = text || "";
  $("terr").hidden = !text;
}

function paintTarget(info) {
  T.path = info.path || "";
  T.name = info.name || "";
  T.pdb_id = info.pdb_id || "";
  T.chains = info.chains || [];
  /* Every chain to begin with: a target is usually given as the chains to bind, and a receptor
     that is a dimer is bound as a dimer. */
  T.picked = T.chains.map((c) => c.id);
  $("tname").textContent = info.name || "target";
  $("tsays").textContent = info.residues + " residues in " + T.chains.length
    + (T.chains.length === 1 ? " chain" : " chains");
  $("tpath").textContent = info.path.replace(/^.*\/(?=[^/]*$)/, "");
  $("tpath").title = info.path;
  const chips = $("chainchips");
  chips.innerHTML = "";
  chips.appendChild(el("span", "why", "chains to bind: "));
  T.chains.forEach((c) => {
    const chip = el("span", "chip on", c.id + "  " + c.n + " res  " + c.first + "-" + c.last);
    chip.title = "chain " + c.id + ", residues " + c.first + " to " + c.last
      + ". Hotspots are written in these numbers.";
    chip.onclick = () => {
      const i = T.picked.indexOf(c.id);
      if (i >= 0 && T.picked.length > 1) T.picked.splice(i, 1);
      else if (i < 0) T.picked.push(c.id);
      chip.classList.toggle("on", T.picked.includes(c.id));
      check();
    };
    chips.appendChild(chip);
  });
  if (!$("name").value) $("name").placeholder = info.name || "from the target";
  $("targetout").hidden = false;
  targetError("");
  check();
}

async function readTarget(body, button) {
  targetError("");
  if (button) { button.disabled = true; button.dataset.was = button.textContent; button.textContent = "reading…"; }
  try {
    paintTarget(await api("/api/target", body));
  } catch (failure) {
    clearTarget();
    targetError(failure.message);
  } finally {
    if (button) { button.disabled = false; button.textContent = button.dataset.was; }
  }
}

$("fetch").onclick = () => readTarget({ kind: "pdb", id: $("pdbid").value }, $("fetch"));
$("pdbid").onkeydown = (e) => { if (e.key === "Enter") $("fetch").click(); };
$("readpath").onclick = () => readTarget({ kind: "path", path: $("serverpath").value }, $("readpath"));
$("serverpath").onkeydown = (e) => { if (e.key === "Enter") $("readpath").click(); };

function sendFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => readTarget({ kind: "file", filename: file.name, data: String(reader.result).split(",")[1] || "" });
  reader.onerror = () => targetError("could not read that file from your computer");
  reader.readAsDataURL(file);
}
$("file").onchange = (e) => sendFile(e.target.files[0]);
const drop = $("drop");
["dragenter", "dragover"].forEach((k) => drop.addEventListener(k, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((k) => drop.addEventListener(k, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => sendFile(e.dataTransfer.files[0]));

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
  return body;
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

function paintPreview(seen) {
  const box = $("hotpreview");
  const lines = (seen && seen.lines) || [];
  box.hidden = !lines.length;
  if (!lines.length) return;
  $("hotpreviewv").textContent = lines[0];
  $("hotpreviewn").textContent = lines.slice(1).join("\n");
}

function paintPlan(plan, notes) {
  const box = $("plan");
  box.innerHTML = "";
  if (!plan || !plan.gpu) {
    box.appendChild(el("div", "k", "plan"));
    box.appendChild(el("div", "n", "Give a target to see the card, the partition and the memory the job asks for."));
  } else {
    box.appendChild(el("div", "k", "slurm"));
    box.appendChild(el("div", "v", "1 × " + plan.gpu + " on " + plan.partition + ", "
      + plan.cpus + " cores, " + plan.mem + " GB, " + plan.walltime));
    box.appendChild(el("div", "n", plan.designs + " design" + (plan.designs === 1 ? "" : "s")
      + " in one job"
      + (plan.account ? ", charged to " + plan.account : ", charged to your default account")
      + ". Neither tool writes a checkpoint, so a job that runs out of walltime starts again from the beginning."));
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
  if (!T.path) {
    $("go").disabled = $("dry").disabled = true;
    paintPlan(null, []); paintProblems([]); paintPreview(null); showFieldErrors({});
    return;
  }
  try {
    const answer = await api("/api/check", form());
    if (mine !== checking) return;                     // a later keystroke already asked
    showFieldErrors(answer.fields || {});
    paintProblems(answer.problems);
    paintPlan(answer.plan, answer.notes);
    paintPreview(answer.preview);
    const clean = !(answer.problems || []).length && !Object.keys(answer.fields || {}).length;
    $("go").disabled = $("dry").disabled = !clean;
    $("g-binder").classList.toggle("done", clean);
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
  spec.columns.forEach((c) => head.appendChild(el("th", null, c)));
  t.appendChild(el("thead")).appendChild(head);
  const body = el("tbody");
  spec.rows.forEach((row) => {
    const tr = el("tr");
    spec.columns.forEach((c) => {
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

  const counts = run.counts || {};
  const tiles = el("div", "tiles");
  tiles.appendChild(tile("designs", run.designs == null ? "—" : String(run.designs),
    run.designs == null ? "designs.json is written when the run finishes" : "in designs.json, best first"));
  Object.keys(counts).sort().forEach((key) => tiles.appendChild(tile(key, String(counts[key]), "on disk so far")));
  const asked = run.asked || {};
  tiles.appendChild(tile("asked for", String(asked.designs == null ? "?" : asked.designs),
    [asked.gpu, asked.walltime].filter(Boolean).join(", ")));
  main.appendChild(tiles);

  const what = el("div", "box");
  what.appendChild(el("h2", null, "What was asked for"));
  const kv = el("dl", "kv");
  const target = run.target || {};
  [["target", (target.pdb_id ? target.pdb_id + ", " : "") + "chains " + (target.chains || "every one")],
   ["hotspots", target.hotspots || "none"],
   ["binder", (target.binder || ["?", "?"]).join(" to ") + " residues"],
   ["results", run.results],
   ["submitted", run.submitted || "—"],
   ["owner", run.owner || "—"]].forEach(([k, v]) => {
    kv.appendChild(el("dt", null, k)); kv.appendChild(el("dd", null, String(v)));
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
  if (run.log_name) actions.appendChild(download(run, run.log_name, "Full log"));
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
    box.appendChild(el("h2", null, "Designs, best first — " + run.table.total + " in all"
      + (run.table.rows.length < run.table.total ? ", " + run.table.rows.length + " shown" : "")));
    box.appendChild(table(run.table));
    main.appendChild(box);
  }

  if (run.log) {
    const box = el("div", "box");
    box.appendChild(el("h2", null, "Log — " + run.log_name));
    const pre = el("pre", "log", run.log);
    box.appendChild(pre);
    main.appendChild(box);
    pre.scrollTop = pre.scrollHeight;
  }
}

/* ------------------------------------------------------------------ start */
(async function start() {
  if (!TOKEN) { flash("This page needs the token the server printed: open the URL from the terminal.", true); return; }
  try {
    HELLO = await api("/api/hello");
    FIELDS = HELLO.fields || [];
    $("where").textContent = HELLO.user + " · " + HELLO.host + " · " + HELLO.tool + " " + HELLO.tool_version
      + (HELLO.dry_run ? " · dry run" : "");
    $("appname").textContent = HELLO.app;
    document.title = HELLO.app;
    (HELLO.target_fields || []).forEach((f) => { if ($(f.key) && f.default != null) $(f.key).value = f.default; });
    buildRail();
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
