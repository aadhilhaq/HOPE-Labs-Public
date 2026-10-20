/* The tab a tool opens in. It starts the tool on the login node, shows the wait, then goes on to
   the tool's own page: the same page the tool's own launcher opens, in this tab, with nothing of
   the launcher's around it. */
"use strict";

const Q = new URLSearchParams(location.search);
const TOKEN = Q.get("t") || "";
const KEY = Q.get("key") || "";
const FROM = Q.get("from") || "";          // a run folder handed over from another tool
const VIEW = Q.get("view") || "";          // a run of this tool's own, to be shown rather than started from

/* The pages that can be opened on one of their own runs. Every one takes ?open=<folder> - by the
   folder, but HOPE-MD by its last part, which is how it names a run - and opens on that run's
   results. */
const OPENS_RUN = { boltz2: 1, colabfold: 1, rfdiffusion: 1, boltzgen: 1, hopemd: 1,
                    adcp: 1, bindcraft: 1, haddock: 1 };
const RUNS_AT = {};

function runUrl(tool) {
  const url = new URL(tool.url);
  if (RUNS_AT[KEY]) url.pathname = RUNS_AT[KEY];
  if (OPENS_RUN[KEY]) url.searchParams.set("open", VIEW);
  return url.toString();
}
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

async function api(path, body) {
  const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const answer = await fetch(url, init);
  let data = {};
  try { data = await answer.json(); } catch (e) { data = { error: "the launcher sent something unreadable" }; }
  if (!answer.ok) throw new Error(data.error || "the launcher answered " + answer.status);
  return data;
}

let flashTimer = 0;
function flash(text, bad) {
  clearTimeout(flashTimer);
  document.querySelectorAll(".flash").forEach((n) => n.remove());
  const box = el("div", "flash" + (bad ? " bad" : ""), text);
  document.body.appendChild(box);
  flashTimer = setTimeout(() => box.remove(), bad ? 16000 : 7000);
}

function selectPath() {
  const range = document.createRange();
  range.selectNodeContents($("t-from-path"));
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

async function copyPath() {
  try { await navigator.clipboard.writeText(FROM); return true; }
  catch (e) { selectPath(); return false; }
}

/* On to the tool's own page. A run handed over waits for one click first: the tool's page cannot be
   opened on a folder, so the folder goes on the clipboard, and a page may write there only when
   clicked. */
function go(tool) {
  $("t-wait").hidden = true;
  if (VIEW && (OPENS_RUN[KEY] || RUNS_AT[KEY])) { location.replace(runUrl(tool)); return; }
  if (!FROM) { location.replace(tool.url); return; }
  $("t-ready").hidden = false;
  $("t-ready-name").textContent = tool.name;
  $("t-go").textContent = "Copy the path and open " + tool.name;
  $("t-go").onclick = async () => {
    if (await copyPath()) { location.replace(tool.url); return; }
    flash("The path is selected: press Ctrl+C to copy it, then open the tool.", true);
    $("t-go").textContent = "Open " + tool.name;
    $("t-go").onclick = () => location.replace(tool.url);
  };
}

function failed(name, why) {
  $("t-wait").hidden = true;
  $("t-failed").hidden = false;
  $("t-failed-name").textContent = name;
  $("t-failed-why").textContent = why;
}

async function start() {
  const state = await api("/api/state");
  const tool = state.tools.find((t) => t.key === KEY) || (KEY === "setup" ? state.setup : null);
  if (!tool) { failed("HOPE Labs", "There is no tool called " + KEY + "."); return; }
  document.title = tool.name + " · HOPE Labs";
  $("t-name").textContent = tool.name;
  $("t-tag").textContent = tool.tagline;
  $("t-wait-name").textContent = tool.name;
  if (tool.running && tool.url) { go(tool); return; }
  let up;
  try { up = await api("/api/launch", { tool: KEY }); }
  catch (failure) { failed(tool.name, failure.message); return; }
  go(Object.assign(tool, up));
}

if (FROM) {
  $("t-from").hidden = false;
  $("t-from-path").textContent = FROM;
  $("t-from-copy").onclick = async () => {
    flash(await copyPath() ? "Copied. Paste it into the tool's import box." : "Selected. Press Ctrl+C to copy it.");
  };
}
$("t-retry").onclick = () => location.reload();

start().catch((failure) => failed("The tool", failure.message));
