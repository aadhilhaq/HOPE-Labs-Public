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
const OPENS_RUN = { boltz2: 1, colabfold: 1, rfdiffusion: 1, boltzgen: 1, hopemd: 1 };
const RUNS_AT = {};

function runUrl(tool) {
  const url = new URL(tool.url);
  if (RUNS_AT[KEY]) url.pathname = RUNS_AT[KEY];
  if (OPENS_RUN[KEY]) url.searchParams.set("open", VIEW);
  return url.toString();
}
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

/* The tool's page at the text size this tab is drawn at: the hub's, which it was opened with, or
   what was chosen here since. Said at 100% as well, as the hub says it to this tab (app.js). */
function withScale(address) {
  if (!window.HLZoom) return address;
  try {
    const url = new URL(address, location.href);
    url.searchParams.set("ui_scale", String(window.HLZoom.get()));
    return url.toString();
  } catch (e) { return address; }
}

/* The bar along the bottom, one still line with Credits beside it: as in app.js. */
function stillCredits() {
  const bar = document.querySelector(".creditbar");
  const text = bar && bar.querySelector(".credittext");
  if (!text || bar.querySelector(".hlc-more")) return;
  const sep = " \u00b7 ";
  let said = text.innerHTML;
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
  const page = withScale(tool.url);
  if (VIEW && (OPENS_RUN[KEY] || RUNS_AT[KEY])) { location.replace(withScale(runUrl(tool))); return; }
  if (!FROM) { location.replace(page); return; }
  $("t-ready").hidden = false;
  $("t-ready-name").textContent = tool.name;
  $("t-go").textContent = "Copy and open " + tool.name;
  $("t-go").onclick = async () => {
    if (await copyPath()) { location.replace(page); return; }
    flash("The path is selected: press Ctrl+C to copy it, then open the tool.", true);
    $("t-go").textContent = "Open " + tool.name;
    $("t-go").onclick = () => location.replace(page);
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
stillCredits();

start().catch((failure) => failed("The tool", failure.message));
