/* The Set up page: the cluster as Slurm describes it, the tools and how far each install has got,
   and the log of the one being watched. Everything shown is read from the server, which reads
   the files the installs write; this page keeps nothing of its own but which tools are ticked.

   By Aadhil Haq */
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN = new URLSearchParams(location.search).get("t") || "";

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

async function api(path, body) {
  const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const answer = await fetch(url, init);
  let data = {};
  try { data = await answer.json(); } catch (e) { data = { error: "the server sent something that is not JSON (" + answer.status + ")" }; }
  if (!answer.ok) throw new Error(data.error || "the server answered " + answer.status);
  return data;
}

let flashTimer = 0;
function flash(text, bad) {
  clearTimeout(flashTimer);
  document.querySelectorAll(".flash").forEach((n) => n.remove());
  const box = el("div", "flash" + (bad ? " bad" : ""), text);
  document.body.appendChild(box);
  flashTimer = setTimeout(() => box.remove(), bad ? 15000 : 7000);
}

/* ------------------------------------------------------------------ theme */
const theme = $("theme");
function isDark() {
  return document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches);
}
function paintTheme() { theme.innerHTML = isDark() ? "&#9680; light" : "&#9681; dark"; }
/* The key the other pages and the documentation read, so all of them open in one theme. */
try { if (localStorage.hldesigntheme) document.documentElement.dataset.theme = localStorage.hldesigntheme; } catch (e) { /* private window */ }
theme.onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.hldesigntheme = document.documentElement.dataset.theme; } catch (e) { /* ignore */ }
  paintTheme();
};
paintTheme();

/* ------------------------------------------------------------------ state */
let HELLO = null;
let PROFILE = null;
let TOOLS = [];
let CHECKING = {};
const TICKED = new Set();
let LOGTOOL = "";
let LOGAT = 0;
let pollTimer = 0;

const WORDS = {
  absent: "Not installed", present: "Installed here", installing: "Installing",
  checking: "Checking", ready: "Installed and checked", failed: "Stopped with a problem",
  stopped: "Stopped part way",
};
const BUSY = new Set(["installing", "checking"]);

try { $("account").value = localStorage.hlsetupaccount || ""; } catch (e) { /* ignore */ }
$("account").addEventListener("change", () => {
  try { localStorage.hlsetupaccount = $("account").value.trim(); } catch (e) { /* ignore */ }
});
function account() { return $("account").value.trim(); }

function hours(h) {
  if (h === null || h === undefined) return "no limit";
  if (h >= 24) { const d = h / 24; return (Number.isInteger(d) ? d : d.toFixed(1)) + (d === 1 ? " day" : " days"); }
  if (h < 1) return Math.round(h * 60) + " min";
  return (Number.isInteger(h) ? h : h.toFixed(1)) + " h";
}

function cardName(kind) {
  const info = ((PROFILE || {}).cards || {})[kind] || {};
  if (kind === "any") return "a GPU of any kind";
  return kind + (info.memory_gb ? " (" + info.memory_gb + " GB" + (info.seen ? "" : "?") + ")" : "");
}

function number(n) { return Number(n || 0).toLocaleString("en-GB"); }

/* ------------------------------------------------------------------ the cluster */
function option(select, value, text, chosen) {
  const o = el("option", "", text);
  o.value = value;
  if (value === chosen) o.selected = true;
  select.appendChild(o);
}

function paintCluster() {
  const p = PROFILE;
  $("unread").hidden = !!p;
  $("choices").hidden = !p;
  $("morebox").hidden = !p;
  $("save").hidden = !p;
  $("probe").textContent = p ? "Read the cluster again" : "Read the cluster";
  $("probe").classList.toggle("pri", !p);
  const table = $("parts");
  table.innerHTML = "";
  if (!p) { $("probed").textContent = ""; $("gpucheck").disabled = true; paintNet(); return; }
  $("probed").textContent = (p.name || p.cluster || "this cluster") + (p.slurm ? " · Slurm " + p.slurm : "")
    + " · read " + p.probed + (p.probed_by ? " by " + p.probed_by : "");
  const head = el("tr");
  ["Partition", "Longest job", "Cards per node", "Nodes", "You may use it"].forEach((t) => head.appendChild(el("th", "", t)));
  table.appendChild(head);
  const use = p.use || {};
  for (const part of p.partitions || []) {
    const row = el("tr", (part.usable ? "" : "off") + ([use.gpu_partition, use.cpu_partition].includes(part.name) ? " chosen" : ""));
    row.appendChild(el("td", "mono", part.name + (part.default ? " *" : "")));
    row.appendChild(el("td", "", hours(part.max_hours)));
    const kinds = Object.keys(part.gpus || {});
    row.appendChild(el("td", "mono", kinds.length ? kinds.map((k) => (k === "any" ? "gpu" : k) + " x" + part.gpus[k]).join(", ") : "none"));
    row.appendChild(el("td", "", number(part.nodes)));
    const may = el("td", part.usable ? "" : "no", part.usable ? "yes" : "no: " + (part.said || "refused"));
    row.appendChild(may);
    table.appendChild(row);
  }
  const usable = (p.partitions || []).filter((x) => x.usable);
  const gpuParts = usable.filter((x) => Object.keys(x.gpus || {}).length);
  const gp = $("gpupart");
  gp.innerHTML = "";
  if (!gpuParts.length) option(gp, "", "no partition with GPUs you may use", "");
  gpuParts.forEach((x) => option(gp, x.name, x.name + " (" + hours(x.max_hours) + ")", use.gpu_partition));
  paintCards(use.default_gpu);
  const cp = $("cpupart");
  cp.innerHTML = "";
  usable.slice().sort((a, b) => (Object.keys(a.gpus || {}).length - Object.keys(b.gpus || {}).length))
    .forEach((x) => option(cp, x.name, x.name + " (" + hours(x.max_hours) + ")", use.cpu_partition));
  $("cname").value = p.name || "";
  $("caddress").value = p.address || "";
  $("cproxy").value = (p.network || {}).proxy || "";
  $("gpucheck").disabled = !gpuParts.length || ["queued", "running"].includes(CHECKING.state);
  paintGpu();
  paintNet();
}

function paintCards(chosen) {
  const part = ((PROFILE || {}).partitions || []).find((x) => x.name === $("gpupart").value);
  const box = $("card");
  box.innerHTML = "";
  const kinds = Object.keys((part || {}).gpus || {});
  kinds.forEach((k) => option(box, k, cardName(k), chosen));
}
$("gpupart").addEventListener("change", () => paintCards(""));

function paintGpu() {
  const p = PROFILE || {};
  const g = p.gpu_check || {};
  const box = $("gpufacts");
  box.innerHTML = "";
  if (["queued", "running"].includes(CHECKING.state)) {
    box.textContent = "asking: job " + CHECKING.job + " is " + (CHECKING.state === "running" ? "running" : "waiting in the queue");
    return;
  }
  if (CHECKING.state === "failed") box.appendChild(el("span", "no", "the last check failed: " + (CHECKING.said || "") + ". "));
  if (!g.driver) {
    box.appendChild(document.createTextNode("not asked yet. The first install asks, because the driver decides which build of PyTorch the nodes can run."));
    return;
  }
  const cards = (g.cards || []).map((c) => c.name + ", " + c.memory_gb + " GB").join("; ");
  box.textContent = (g.host ? g.host + ": " : "") + cards + ". Driver " + g.driver + ", CUDA " + (g.cuda || "unknown")
    + ". " + (g.network === false ? "Compute nodes cannot reach the internet, so everything is downloaded here first."
      : g.network ? "Compute nodes can reach the internet." : "")
    + (g.when ? " Asked " + g.when + "." : "");
}

function paintNet() {
  const box = $("netfacts");
  box.innerHTML = "";
  const login = (((PROFILE || {}).network) || {}).login || {};
  const names = Object.keys(login);
  if (!names.length) { box.textContent = "not checked yet"; return; }
  names.forEach((n) => box.appendChild(el("span", "src " + (login[n] ? "yes" : "no"), n + (login[n] ? "" : ": unreachable"))));
}

$("probe").onclick = async () => {
  const button = $("probe");
  button.disabled = true;
  $("clustersays").textContent = "asking Slurm, about half a minute...";
  try {
    const got = await api("/api/probe", { account: account() });
    PROFILE = got.profile;
    paintCluster();
    $("clustersays").textContent = "read and saved";
    refresh(true);
  } catch (e) {
    $("clustersays").textContent = "";
    flash(e.message, true);
  } finally {
    button.disabled = false;
  }
};

$("save").onclick = async () => {
  try {
    const got = await api("/api/profile", {
      use: { gpu_partition: $("gpupart").value, default_gpu: $("card").value, cpu_partition: $("cpupart").value },
      name: $("cname").value, address: $("caddress").value, proxy: $("cproxy").value,
    });
    PROFILE = got.profile;
    paintCluster();
    $("clustersays").textContent = "saved: every page on this cluster reads it from now on";
  } catch (e) { flash(e.message, true); }
};

$("gpucheck").onclick = async () => {
  try {
    const got = await api("/api/gpucheck", { account: account() });
    CHECKING = got.gpu_check || {};
    paintCluster();
    schedule(2000);
  } catch (e) { flash(e.message, true); }
};

/* ------------------------------------------------------------------ the tools */
function paintTools() {
  const list = $("toollist");
  list.innerHTML = "";
  for (const t of TOOLS) {
    const st = t.status || {};
    const state = st.state || "absent";
    const busy = BUSY.has(state);
    const recipe = t.recipe;
    const row = el("div", "trow " + (busy ? "busy" : state === "ready" ? "ready"
      : state === "failed" ? "failed" : state === "stopped" ? "stopped" : "") + (recipe ? "" : " manual"));
    row.dataset.key = t.key;
    const label = el("label", "tick");
    const box = el("input");
    box.type = "checkbox";
    box.disabled = !recipe || !["absent", "failed", "stopped"].includes(state) || !HELLO.writable;
    box.checked = !box.disabled && TICKED.has(t.key);
    box.onchange = () => { if (box.checked) TICKED.add(t.key); else TICKED.delete(t.key); paintRoom(); };
    label.appendChild(box);
    label.appendChild(el("b", "", t.name));
    label.appendChild(el("span", "why", t.tagline));
    row.appendChild(label);
    let words = WORDS[state] || state;
    if (state === "present" && recipe) words = "There already, not by Set up";
    if (state === "present" && !recipe) words = "Installed here";
    row.appendChild(el("span", "state", words));
    const meta = el("div", "meta");
    if (recipe) {
      meta.appendChild(document.createTextNode("About " + recipe.disk_gb + " GB and " + number(recipe.files)
        + " files, about " + recipe.minutes + " minutes. " + (recipe.needs.gpu ? "Needs a GPU partition. " : "")
        + "Licence: "));
      const a = el("a", "", recipe.licence.name);
      a.href = recipe.licence.url; a.target = "_blank"; a.rel = "noopener";
      meta.appendChild(a);
      meta.appendChild(document.createTextNode(". Goes in " + recipe.install + "."));
    } else {
      meta.textContent = (state === "present" ? "Found at " + t.install + ". " : "")
        + "Set up cannot install this one yet: its own instructions are in the documentation, under Your own copies.";
    }
    row.appendChild(meta);
    if (recipe) {
      const steps = el("div", "steps");
      for (const s of recipe.steps) {
        const done = (st.steps || {})[s.name];
        const now = busy && st.step === s.name;
        const bad = !busy && ["failed", "stopped"].includes(state) && st.step === s.name;
        steps.appendChild(el("span", "stp" + (now ? " now" : done ? " done" : "") + (bad ? " bad" : ""), s.title));
      }
      row.appendChild(steps);
    }
    if (busy || ["failed", "stopped", "present"].includes(state)) {
      const said = busy ? st.detail : (st.problem || st.detail);
      if (said) row.appendChild(el("div", "detail", said));
    }
    if (state === "ready") {
      // What the check proved, in the terms of what was checked: a fold, or a docking.
      const c = st.check || {}, r = st.receipt || {};
      const bits = [];
      const mins = Math.max(1, Math.round((c.took || 0) / 60));
      if (c.ok && c.adcp_best !== undefined) bits.push("Docked " + (c.peptide || "a peptide") + " on " + (c.partition || "the CPU partition")
        + " in " + mins + " min: ADCP " + c.adcp_best + (c.mmgbsa_best ? ", MM-GBSA " + c.mmgbsa_best + " kcal/mol" : "") + ".");
      else if (c.ok) bits.push("Folded on " + (c.node || "a GPU node") + " in " + mins + " min"
        + (c.confidence_score !== undefined ? ", confidence " + c.confidence_score : "") + ".");
      if (r.boltz) bits.push("Boltz " + r.boltz + ", PyTorch " + r.torch + (r.kernels === false ? ", without the CUDA kernels" : "") + ".");
      if (r.adcp_dock) bits.push("adcp-dock " + r.adcp_dock + (r.python ? ", Python " + r.python : "") + (r.ambertools ? ", with AmberTools" : "") + ".");
      if (bits.length) row.appendChild(el("div", "proof", bits.join(" ")));
    }
    const acts = el("div", "acts");
    if (recipe && (st.started || state !== "absent")) {
      const log = el("button", "btn sm", "Log");
      log.onclick = () => watch(t.key);
      acts.appendChild(log);
    }
    if (recipe && state === "ready") {
      const again = el("button", "btn sm", "Check again");
      again.title = "Queue the check fold again: worth doing after the cluster changes its drivers";
      again.onclick = () => act("/api/check", t.key);
      acts.appendChild(again);
    }
    if (busy) {
      const stop = el("button", "btn sm", "Stop");
      stop.onclick = () => act("/api/stop", t.key);
      acts.appendChild(stop);
    }
    row.appendChild(acts);
    list.appendChild(row);
  }
  paintRoom();
  paintLogPick();
}

function paintRoom() {
  const free = (HELLO || {}).free || {};
  const bits = [];
  if (free.gb !== null && free.gb !== undefined) bits.push(number(Math.round(free.gb)) + " GB");
  if (free.files !== null && free.files !== undefined) bits.push(number(free.files) + " files");
  $("room").textContent = bits.length ? "free here: " + bits.join(" and ") : "";
  const chosen = TOOLS.filter((t) => TICKED.has(t.key) && t.recipe);
  const gb = chosen.reduce((n, t) => n + t.recipe.disk_gb, 0);
  const files = chosen.reduce((n, t) => n + t.recipe.files, 0);
  $("install").disabled = !chosen.length || !PROFILE || !(HELLO || {}).writable;
  let says = chosen.length ? chosen.length + (chosen.length === 1 ? " tool, " : " tools, ") + "about " + gb + " GB and " + number(files) + " files" : "";
  if (chosen.length && !PROFILE) says += ". Read the cluster first";
  if (chosen.length && free.files !== null && free.files !== undefined && files > free.files) says += ". More files than the quota here allows";
  $("ticked").textContent = says;
}

async function act(path, key) {
  try {
    const got = await api(path, { tool: key, account: account() });
    if (got.tools) { TOOLS = got.tools; paintTools(); }
    watch(key);
    schedule(1500);
  } catch (e) { flash(e.message, true); }
}

$("install").onclick = async () => {
  const keys = [...TICKED];
  if (!keys.length) return;
  $("install").disabled = true;
  try {
    const got = await api("/api/install", { tools: keys, account: account() });
    TICKED.clear();
    TOOLS = got.tools || TOOLS;
    paintTools();
    watch(keys[0]);
    flash("Installing in the background. It carries on if this page or the launcher is closed.");
    schedule(1500);
  } catch (e) {
    $("install").disabled = false;
    flash(e.message, true);
  }
};

/* ------------------------------------------------------------------ the log */
function paintLogPick() {
  const pick = $("logtool");
  const keys = TOOLS.filter((t) => t.recipe).map((t) => t.key);
  if (!LOGTOOL) {
    const busy = TOOLS.find((t) => t.recipe && BUSY.has((t.status || {}).state));
    const any = TOOLS.find((t) => t.recipe && (t.status || {}).started);
    LOGTOOL = (busy || any || {}).key || keys[0] || "";
  }
  if (pick.options.length !== keys.length) {
    pick.innerHTML = "";
    TOOLS.filter((t) => t.recipe).forEach((t) => option(pick, t.key, t.name, LOGTOOL));
  }
  pick.value = LOGTOOL;
}
$("logtool").onchange = () => watch($("logtool").value);

function watch(key) {
  if (key !== LOGTOOL) { LOGTOOL = key; LOGAT = 0; $("log").textContent = ""; }
  paintLogPick();
  readLog();
}

async function readLog() {
  if (!LOGTOOL) return;
  try {
    const got = await api("/api/log?tool=" + encodeURIComponent(LOGTOOL) + "&since=" + LOGAT);
    const log = $("log");
    const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    if (LOGAT === 0) log.textContent = got.text || "Nothing has been written for this tool yet.";
    else if (got.text) log.textContent += got.text;
    LOGAT = got.size || LOGAT;
    if (atEnd || LOGAT === got.size) log.scrollTop = log.scrollHeight;
    $("logsays").textContent = got.size ? Math.round(got.size / 1024) + " KB" : "";
  } catch (e) { /* the next poll tries again */ }
}

/* ------------------------------------------------------------------ keeping up */
function schedule(ms) { clearTimeout(pollTimer); pollTimer = setTimeout(refresh, ms); }

async function refresh(once) {
  try {
    const got = await api("/api/state");
    TOOLS = got.tools || TOOLS;
    const before = JSON.stringify(PROFILE);
    PROFILE = got.profile || PROFILE;
    CHECKING = got.gpu_check || {};
    paintTools();
    if (JSON.stringify(PROFILE) !== before) paintCluster(); else paintGpu();
    $("gpucheck").disabled = !PROFILE || ["queued", "running"].includes(CHECKING.state);
    readLog();
  } catch (e) { /* a dropped poll is tried again */ }
  if (once === true) return;
  const busy = TOOLS.some((t) => BUSY.has((t.status || {}).state)) || ["queued", "running"].includes(CHECKING.state);
  schedule(busy ? 2500 : 15000);
}

async function start() {
  if (!TOKEN) { $("banner").textContent = "This page needs the token its server printed: open it from HOPE Labs."; $("banner").hidden = false; return; }
  try {
    HELLO = await api("/api/hello");
  } catch (e) {
    $("banner").textContent = "The page could not reach its server: " + e.message;
    $("banner").hidden = false;
    return;
  }
  PROFILE = HELLO.profile;
  TOOLS = HELLO.tools;
  CHECKING = HELLO.gpu_check || {};
  $("where").textContent = HELLO.user + "@" + HELLO.host + "  ·  " + HELLO.root;
  if (!HELLO.writable) {
    $("banner").textContent = "You cannot write to " + HELLO.root + ", so this page can show what is installed here and cannot install anything.";
    $("banner").hidden = false;
  }
  paintCluster();
  paintTools();
  readLog();
  schedule(TOOLS.some((t) => BUSY.has((t.status || {}).state)) ? 2500 : 15000);
}

start();
