/* reportkit: the lab's report page. By Aadhil Haq.

   One module, the same file in every tool, lays out the report of a run. The
   tool's server decides what the report says and sends it as one JSON
   document (GET /api/report, the contract in the README); this file decides
   how it looks and behaves:

     - the bar along the top: the sections to include, the tool's own options,
       zoom (50-200 %, kept in this browser and never in the PDF), Save as PDF
       (the browser's print window) and Copy the methods and references;
     - sections numbered 1, 2, 2.1 ..., the appendix lettered, and tables and
       figures numbered in reading order, "Table N." and "Figure N." before
       each caption; {{table:id}} and {{figure:id}} in any text become that
       block's number;
     - tables, figures (SVG, PNG, bar charts, structures drawn in 3Dmol and
       turned into pictures before printing), lists, notes and logs;
     - the views added from the tool's viewer, their own words editable in
       place where the tool saves them.

   It prints black on white at full size, whatever the zoom: SVG stays vector
   in the PDF, and tables and figures are kept whole on a page.

   Nothing here knows which tool it is in. Start it with

     ReportKit.start({api: '/api/report', token: T, params: {rundir: R},
                      store: 'tool.report', edit: '/api/report/edit'});
*/
(function(){
'use strict';

const VERSION = '1';
const esc = s => String(s === undefined || s === null ? '' : s).replace(/[&<>"']/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]);
const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
/* Tables longer than this may run over a page, their heading repeated; shorter
   ones are kept whole. */
const KEEP_ROWS = 40;

let CFG = null;           // what the tool started the page with
let D = null;             // the report, as the server sent it
let OPT = {};             // the tool's options, as chosen here
let SHOW = {};            // which sections are included
let ZOOM = 1;
const JOBS = [];          // structures still to draw
let DRAWING = 0, PRINT_WAITING = false, PRINTED = false;

/* ---- small helpers ------------------------------------------------------------ */
const $ = id => document.getElementById(id);
function store(key, value){
  const k = (CFG.store || 'reportkit') + '.' + key;
  try {
    if(value === undefined){ const v = localStorage.getItem(k); return v === null ? undefined : JSON.parse(v); }
    localStorage.setItem(k, JSON.stringify(value));
  } catch(e){ return undefined; }
}
/* A same-site path carries the page's token, as every request to the tool must. */
function withToken(url){
  if(!url || !CFG.token || !/^\//.test(url) || /[?&]t=/.test(url)) return url;
  return url + (url.indexOf('?') >= 0 ? '&' : '?') + 't=' + encodeURIComponent(CFG.token);
}
function query(o){
  return Object.keys(o).filter(k => o[k] !== undefined && o[k] !== null)
    .map(k => encodeURIComponent(k) + '=' + encodeURIComponent(o[k])).join('&');
}
async function call(url, body){
  const opts = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'},
                                         body: JSON.stringify(body)};
  try {
    const r = await fetch(withToken(url), opts);
    try { return await r.json(); } catch(e){ return {error: 'the server answered ' + r.status}; }
  } catch(e){ return {error: 'the server did not answer; is it still running?'}; }
}
/* A number as a report prints it: fixed decimals, a true minus sign, and
   thousands grouped from 10,000 up. */
function num(v, digits){
  if(v === null || v === undefined || v === '' || (typeof v === 'number' && !isFinite(v))) return '';
  if(typeof v !== 'number') return esc(v);
  let s = digits === undefined ? String(v) : v.toFixed(digits);
  if(Math.abs(v) >= 10000){
    const [a, b] = s.replace('-', '').split('.');
    s = (v < 0 ? '-' : '') + a.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (b ? '.' + b : '');
  }
  return s.replace(/^-/, '\u2212');
}
function status(text, ready){
  const el = $('rk-status');
  if(!el) return;
  el.textContent = text;
  el.classList.toggle('rk-ready', !!ready);
}

/* ---- numbering ---------------------------------------------------------------- */
/* Every table and figure that will be shown gets its number before anything is
   drawn, so a caption can name a block that comes after it. */
let NUMS = {};
function shownSections(){
  return (D.sections || []).filter(s => SHOW[s.id] !== false);
}
function number(){
  NUMS = {table: {}, figure: {}};
  let t = 0, f = 0;
  const walk = blocks => (blocks || []).forEach(b => {
    if(!b) return;
    if(b.kind === 'table' && b.caption !== undefined){ t++; if(b.id) NUMS.table[b.id] = t; b._n = t; }
    if(b.kind === 'figure'){ f++; if(b.id) NUMS.figure[b.id] = f; b._n = f; }
  });
  const sec = s => { walk(s.blocks); (s.sections || []).forEach(sec); };
  shownSections().forEach(sec);
  if(SHOW.views !== false) (D.views || []).forEach(v => { f++; v._n = f; walk(v.blocks); });
  if(SHOW.appendix !== false) (D.appendix || []).forEach(a => walk(a.blocks));
}
function refs(html){
  return String(html || '').replace(/\{\{(table|figure):([\w.-]+)\}\}/g, (m, kind, id) => {
    const n = (NUMS[kind] || {})[id];
    return n ? (kind === 'table' ? 'Table ' : 'Figure ') + n : (kind === 'table' ? 'a table' : 'a figure') + ' not in this report';
  });
}

/* ---- the document ------------------------------------------------------------- */
function build(){
  JOBS.length = 0;
  number();
  const out = [titleHTML()];
  (D.notes || []).forEach(n => out.push(blockHTML(n)));
  let k = 0;
  shownSections().forEach(s => { k++; out.push(sectionHTML(s, String(k), 2)); });
  if(Array.isArray(D.views) && SHOW.views !== false){ k++; out.push(viewsHTML(String(k))); }
  if(D.methods && SHOW.methods !== false){ k++; out.push(methodsHTML(String(k))); }
  if((D.appendix || []).length && SHOW.appendix !== false) out.push(appendixHTML());
  $('rk-doc').innerHTML = out.join('');
  wireViews();
  run();
}

function titleHTML(){
  return '<section class=rk-title>' + (D.kicker ? '<div class=rk-kicker>' + esc(D.kicker) + '</div>' : '')
    + '<h1>' + esc(D.title || 'Report') + '</h1>'
    + (D.subtitle ? '<p class=rk-subtitle>' + D.subtitle + '</p>' : '')
    + (D.byline ? '<p class=rk-meta>' + esc(D.byline) + '</p>' : '')
    + (D.path ? '<p class=rk-meta><code>' + esc(D.path) + '</code></p>' : '') + '</section>';
}

/* A section that holds only sections, the first of them on a new page, starts
   the page itself, so its heading is never left alone at the foot of one. */
function sectionHTML(s, n, level, nobreak){
  const kids = s.sections || [];
  const lift = !(s.blocks || []).length && kids.length && kids[0].newpage;
  const cls = ['rk-section'];
  if((s.newpage || lift) && !nobreak) cls.push('rk-newpage');
  const h = level === 2 ? 'h2' : 'h3';
  return '<section class="' + cls.join(' ') + '" id="rk-s-' + esc(s.id) + '"><' + h + '>' + n + ' ' + esc(s.heading)
    + '</' + h + '>' + (s.blocks || []).map(blockHTML).join('')
    + kids.map((c, i) => sectionHTML(c, n + '.' + (i + 1), 3, lift && i === 0)).join('') + '</section>';
}

function blockHTML(b){
  if(!b) return '';
  if(typeof b === 'string') return '<p>' + refs(b) + '</p>';
  switch(b.kind){
    case 'p': return '<p class="' + (b.tone ? 'rk-' + esc(b.tone) : '') + '">' + refs(b.html) + '</p>';
    case 'heading': return '<h4 class=rk-h4>' + esc(b.text) + '</h4>';
    case 'list': {
      const tag = b.ordered ? 'ol' : 'ul';
      return '<' + tag + ' class=rk-list>' + (b.items || []).map(i => '<li>' + refs(i) + '</li>').join('') + '</' + tag + '>';
    }
    case 'pre': return '<pre class=rk-pre>' + esc(b.text) + '</pre>';
    case 'facts': return '<table class="rk-table rk-facts"><tbody>' + (b.rows || []).map(([k, v]) =>
      '<tr><th>' + esc(k) + '</th><td>' + refs(v) + '</td></tr>').join('') + '</tbody></table>';
    case 'table': return tableHTML(b);
    case 'figure': return figureHTML(b);
    default: return '';
  }
}

function cell(col, row){
  const v = row[col.key];
  if(v && typeof v === 'object' && 'html' in v) return refs(v.html);
  if(v === null || v === undefined || v === '') return col.empty !== undefined ? esc(col.empty) : '';
  if(typeof v === 'number') return num(v, col.digits);
  return esc(v);
}
function tableHTML(b){
  const cols = b.columns || [], rows = b.rows || [];
  const keep = rows.length <= KEEP_ROWS ? ' rk-keep' : '';
  const th = c => '<th class="' + (c.num ? 'rk-num' : '') + '"' + (c.help ? ' title="' + esc(c.help) + '"' : '')
    + '>' + esc(c.label) + '</th>';
  const tr = (r, tag) => '<tr' + (r._class ? ' class="' + esc(r._class) + '"' : '') + '>'
    + cols.map(c => '<' + tag + ' class="' + (c.num ? 'rk-num' : '') + '">' + cell(c, r) + '</' + tag + '>').join('') + '</tr>';
  const cap = b.caption !== undefined ? '<caption><b>Table ' + b._n + '.</b> ' + refs(b.caption) + '</caption>' : '';
  return '<div class=rk-tablebox' + (b.id ? ' id="rk-t-' + esc(b.id) + '"' : '') + '><table class="rk-table' + keep + '">'
    + cap + '<thead><tr>' + cols.map(th).join('') + '</tr></thead><tbody>'
    + (rows.length ? rows.map(r => tr(r, 'td')).join('')
      : '<tr><td colspan=' + Math.max(1, cols.length) + ' class=rk-why>' + esc(b.empty || 'Nothing to show.') + '</td></tr>')
    + '</tbody>' + ((b.foot || []).length ? '<tfoot>' + b.foot.map(r => tr(r, 'td')).join('') + '</tfoot>' : '')
    + '</table>' + (b.note ? '<p class=rk-note>' + refs(b.note) + '</p>' : '') + '</div>';
}

/* The sheet is A4 with 15 mm margins (reportkit.css: #rk-doc), so a figure is 180 mm wide
   however wide the window is: 680 CSS pixels. A chart is drawn in that many user units rather
   than in 640 that are then stretched to fit, which makes one user unit one pixel on the screen
   and one 96th of an inch on paper, and a label's size in units the size it is drawn at.

   That is what lets a chart keep the floor the rest of the lab keeps. Before this every chart
   was 640 units wide whatever its box, so 11-unit labels landed at 11.7 px across the page and
   at 5.8 px inside a half-width panel - 8.7 px even with the zoom at 150 % - which was the
   smallest type in the product, on the page a tutorial film shows after a run. The font size
   could not be raised to reach 13 px drawn, because 24.8 units in a chart whose rows are 20
   units apart is a pile-up; drawn at the width it will occupy, 13 is just 13. */
const FIG = 680, PANEL_GAP = 8;            /* .rk-panels{gap:var(--sp-2)} */
/* Nothing the lab draws is under 13 px, a chart's furniture least of all: it is the floor, and
   the floor leaves the most room for the data. */
const LABEL = 13;
function panelWidth(cols){
  cols = Math.max(1, Math.min(cols || 1, 4));
  return Math.floor((FIG - PANEL_GAP * (cols - 1)) / cols);
}
/* About how wide a label is drawn, in pixels: digits, a point and a minus sign average a little
   under two thirds of the size in the report's sans-serif. A margin worked out from this is
   never the reason a label is clipped. */
const wide = (says, size) => 0.62 * (size || LABEL) * String(says).length;

let SLOT = 0;
function bodyHTML(x, box){
  box = box || FIG;
  if(x.svg) return '<div class=rk-svg>' + x.svg + '</div>';
  if(x.png) return '<img class=rk-png alt="" src="' + esc(withToken(x.png)) + '">';
  if(x.bars) return (Array.isArray(x.bars) ? x.bars : [x.bars]).map(c => barChart(c, box)).join('');
  if(x.structure){
    const id = 'rk-mol-' + (++SLOT);
    JOBS.push({el: id, spec: x.structure});
    return '<div class=rk-mol id="' + id + '"><span class=rk-why>drawing\u2026</span></div>';
  }
  return '';
}
function figureHTML(b){
  let body;
  if(b.panels && b.panels.length){
    const n = Math.min(b.columns || b.panels.length, 4);
    body = '<div class="rk-panels rk-cols' + n + '">' + b.panels.map(p => '<div class=rk-panel>'
      + bodyHTML(p, panelWidth(n))
      + (p.label ? '<div class=rk-sub>' + refs(p.label) + '</div>' : '') + '</div>').join('') + '</div>';
  } else body = bodyHTML(b, FIG);
  return '<figure class="rk-figure' + (b.wide ? ' rk-wide' : '') + '"' + (b.id ? ' id="rk-f-' + esc(b.id) + '"' : '') + '>'
    + body + '<figcaption><b>Figure ' + b._n + '.</b> ' + refs(b.caption) + '</figcaption></figure>';
}

/* Horizontal bars, from zero to each value, the first point at the top, with
   its SD as an error bar. A point marked dim is drawn paler.

   Drawn at `box`, the width the figure or the panel gives it, so the labels are
   the size they are written at. The row height, the gutter the names sit in and
   the margin the last tick needs are all worked out from that size: a row has to
   clear a 13 px name, not the 20 units that used to be enough for an 11-unit one. */
function barChart(c, box){
  const pts = c.points || [];
  if(!pts.length) return '';
  const W = Math.max(160, box || FIG), fs = LABEL;
  const row = Math.round(fs * 2), top = 8;
  const lo = Math.min(0, ...pts.map(p => p.v - (p.sd || 0))), hi = Math.max(0, ...pts.map(p => p.v + (p.sd || 0)));
  const span = (hi - lo) || 1;
  const tick = Math.pow(10, Math.floor(Math.log10(span / 4)));
  const step = [1, 2, 5, 10].map(k => k * tick).find(k => span / k <= 6) || tick * 10;
  const ticks = [];
  for(let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(v);
  const said = ticks.map(v => num(+v.toFixed(6)));
  /* The ticks are centered on their lines, so the outermost needs half its width
     outside the plot or it is cut off at the edge of the drawing. */
  const R = Math.max(12, Math.ceil(Math.max(...said.map(t => wide(t, fs))) / 2) + 4);
  /* The names get the room they need, up to a third of the chart; what will not
     fit is shortened, and the whole name stays in the tooltip either way. */
  const longest = Math.max(...pts.map(p => String(p.name).length));
  const L = Math.min(Math.round(W * 0.34), Math.ceil(0.62 * fs * longest) + 12);
  const fits = Math.max(4, Math.floor((L - 12) / (0.62 * fs)));
  const short = n => String(n).length > fits ? String(n).slice(0, fits - 1) + '\u2026' : String(n);
  /* The axis is a sentence, and in a panel half the page wide it is wider than
     the chart: it is broken over two lines rather than run off the edge, because
     the one thing it may not do is get smaller. */
  const axis = String(c.axis || ''), lines = [];
  if(wide(axis, fs) > W - 10){
    const words = axis.split(' ');
    let line = '';
    words.forEach(w => {
      if(line && wide(line + ' ' + w, fs) > W - 10){ lines.push(line); line = w; }
      else line = line ? line + ' ' + w : w;
    });
    if(line) lines.push(line);
  } else if(axis) lines.push(axis);
  /* Two bands under the plot: the ticks, then the axis, a clear line apart, with
     room under the axis for its descenders. The axis used to sit 2 units off the
     bottom of a 640-unit drawing and lose the tail of every (kcal/mol). */
  const band = (1 + Math.max(1, lines.length)) * fs + 16;
  const H = top + pts.length * row + band, floor = H - band;
  const X = v => (L + (v - lo) / span * (W - L - R)).toFixed(1);
  let s = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" style="max-width:'
    + W + 'px;margin-inline:auto" font-family="Helvetica, Arial, sans-serif" font-size="' + fs + '" '
    + 'role="img" aria-label="' + esc(c.axis || '') + '">';
  ticks.forEach((v, k) => {
    s += '<line x1="' + X(v) + '" x2="' + X(v) + '" y1="' + top + '" y2="' + floor + '" stroke="#e3e6ec"/>'
      + '<text x="' + X(v) + '" y="' + (floor + fs + 4) + '" text-anchor="middle" fill="#555">' + said[k] + '</text>';
  });
  s += '<line x1="' + X(0) + '" x2="' + X(0) + '" y1="' + top + '" y2="' + floor + '" stroke="#888"/>';
  pts.forEach((p, k) => {
    const y = top + k * row + 4, h = row - 9;
    const x0 = X(Math.min(0, p.v)), x1 = X(Math.max(0, p.v));
    s += '<text x="' + (L - 8) + '" y="' + (y + h / 2 + fs * 0.35).toFixed(1) + '" text-anchor="end" fill="#20262b">'
      + esc(short(p.name)) + '</text>'
      + '<rect x="' + x0 + '" y="' + y + '" width="' + Math.max(1, x1 - x0).toFixed(1) + '" height="' + h + '" fill="'
      + (p.dim ? '#9fb3d9' : '#1a56db') + '" opacity="0.85"><title>' + esc(p.name) + ': ' + num(p.v, c.digits)
      + (p.sd ? ' \u00b1 ' + num(p.sd, c.digits) : '') + '</title></rect>';
    if(p.sd) s += '<line x1="' + X(p.v - p.sd) + '" x2="' + X(p.v + p.sd) + '" y1="' + (y + h / 2) + '" y2="' + (y + h / 2) + '" stroke="#20262b"/>'
      + '<line x1="' + X(p.v - p.sd) + '" x2="' + X(p.v - p.sd) + '" y1="' + (y + 2) + '" y2="' + (y + h - 2) + '" stroke="#20262b"/>'
      + '<line x1="' + X(p.v + p.sd) + '" x2="' + X(p.v + p.sd) + '" y1="' + (y + 2) + '" y2="' + (y + h - 2) + '" stroke="#20262b"/>';
  });
  /* Centered on the whole drawing, not on the plot: a long axis under a narrow
     plot would otherwise hang off the right-hand edge. */
  lines.forEach((line, k) => {
    s += '<text x="' + (W / 2) + '" y="' + (floor + (2 + k) * fs + 11) + '" text-anchor="middle" fill="#333">'
      + esc(line) + '</text>';
  });
  s += '</svg>';
  return '<div class="rk-svg rk-chart">' + s + '</div>';
}

function viewsHTML(n){
  const vs = D.views || [];
  // With no views yet the section is a hint for the screen, and is not printed.
  const out = ['<section class="rk-section rk-newpage' + (vs.length ? '' : ' rk-noprint') + '" id=rk-views><h2>' + n + ' '
    + esc(D.views_heading || 'Views added from the viewer') + '</h2>'];
  if(!vs.length) out.push('<p class="rk-why rk-noprint">' + esc(D.views_hint || 'None yet. Show a structure in the results and press Add to report.') + '</p>');
  const edit = !!(CFG.edit && D.views_editable !== false);
  vs.forEach((v, k) => {
    out.push('<figure class="rk-figure rk-view" data-id="' + esc(v.id) + '">'
      + (edit ? '<div class="rk-noprint rk-viewbtns"><button type=button class=rk-mini data-act=up data-id="' + esc(v.id) + '"'
        + (k ? '' : ' disabled') + '>Up</button><button type=button class=rk-mini data-act=down data-id="' + esc(v.id) + '"'
        + (k < vs.length - 1 ? '' : ' disabled') + '>Down</button><button type=button class=rk-mini data-act=remove data-id="'
        + esc(v.id) + '">Take out</button></div>' : '')
      + (v.png ? '<img class=rk-png alt="" src="' + esc(withToken(v.png)) + '">' : bodyHTML(v))
      + '<figcaption><b>Figure ' + v._n + '.</b> ' + refs(v.caption || '')
      + (edit ? ' <span class=rk-own contenteditable=true data-id="' + esc(v.id) + '" data-ph="Add your own words">'
        + esc(v.own || '') + '</span>' : (v.own ? ' ' + esc(v.own) : '')) + '</figcaption></figure>'
      + (v.blocks || []).map(blockHTML).join(''));
  });
  out.push('</section>');
  return out.join('');
}

function methodsHTML(n){
  return '<section class="rk-section rk-newpage" id=rk-methods><h2>' + n + ' Methods</h2><div class=rk-methods>'
    + refs(D.methods) + '</div>' + ((D.references || []).length ? '<h3>References</h3><ol class=rk-refs>'
    + D.references.map(r => '<li value="' + esc(r.n) + '">' + esc(r.text) + '</li>').join('') + '</ol>' : '') + '</section>';
}

function appendixHTML(){
  return '<section class="rk-section rk-newpage" id=rk-appendix><h2>Appendix</h2>' + D.appendix.map((a, i) =>
    '<section class=rk-appx><h3>' + LETTERS[i % 26] + '. ' + esc(a.heading) + '</h3>' + (a.blocks || []).map(blockHTML).join('')
    + '</section>').join('') + '</section>';
}

/* ---- structures, drawn off the page and put in as pictures -------------------- */
let OFF = null;
function offViewer(size){
  if(!window.$3Dmol) return null;
  if(!OFF){
    const el = document.createElement('div');
    el.style.cssText = 'position:fixed;left:-12000px;top:0;width:' + size[0] + 'px;height:' + size[1] + 'px';
    document.body.appendChild(el);
    OFF = window.$3Dmol.createViewer(el, {backgroundColor: 'white', antialias: true});
  }
  return OFF;
}
/* The structure turned so the line from one side's center to the other's runs
   across the picture: an interface seen side on. PDB text only. */
function sideOn(pdb, a, b){
  const rows = pdb.split('\n').filter(l => /^(ATOM  |HETATM)/.test(l));
  const xyz = l => [+l.slice(30, 38), +l.slice(38, 46), +l.slice(46, 54)];
  const mean = ls => { const s = [0, 0, 0]; ls.forEach(l => { const p = xyz(l); s[0] += p[0]; s[1] += p[1]; s[2] += p[2]; });
    return s.map(v => v / Math.max(1, ls.length)); };
  const ca = mean(rows.filter(l => a.includes(l[21]))), cb = mean(rows.filter(l => b.includes(l[21])));
  let u = [cb[0] - ca[0], cb[1] - ca[1], cb[2] - ca[2]];
  const n = Math.hypot(...u) || 1; u = u.map(v => v / n);
  const ax = [0, u[2], -u[1]], s = Math.hypot(...ax), c = u[0];
  let R = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  if(s > 1e-6){
    const k = ax.map(v => v / s);
    const K = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]];
    const KK = K.map((r, i) => r.map((_, j) => K[i][0] * K[0][j] + K[i][1] * K[1][j] + K[i][2] * K[2][j]));
    R = R.map((r, i) => r.map((v, j) => v + s * K[i][j] + (1 - c) * KK[i][j]));
  } else if(c < 0) R = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]];
  const mid = ca.map((v, i) => (v + cb[i]) / 2);
  const f = v => (v.toFixed(3)).padStart(8).slice(-8);
  return pdb.split('\n').map(l => {
    if(!/^(ATOM  |HETATM)/.test(l)) return l;
    const p = xyz(l).map((v, i) => v - mid[i]);
    const q = R.map(r => r[0] * p[0] + r[1] * p[1] + r[2] * p[2]);
    return l.slice(0, 30) + f(q[0]) + f(q[1]) + f(q[2]) + l.slice(54);
  }).join('\n');
}
async function fetchText(url){
  const r = await fetch(withToken(url));
  const t = await r.text();
  if(!r.ok){ let why = t; try { why = JSON.parse(t).error || t; } catch(e){} throw new Error(why.slice(0, 200)); }
  return t;
}
async function drawStructure(job){
  const el = $(job.el);
  if(!el) return;
  const sp = job.spec || {};
  const size = sp.size || [1200, 860];
  const v = offViewer(size);
  if(!v){ el.innerHTML = '<span class=rk-why>the 3D viewer did not load</span>'; return; }
  let text = sp.text || await fetchText(sp.url);
  const fmt = sp.format || 'pdb';
  if(sp.side_on && fmt === 'pdb') text = sideOn(text, sp.side_on[0] || [], sp.side_on[1] || []);
  v.removeAllModels(); v.removeAllLabels(); v.removeAllShapes();
  try { v.removeAllSurfaces(); } catch(e){}
  v.addModel(text, fmt);
  (sp.layers || [{sel: {}, style: {cartoon: {color: 'spectrum'}}}]).forEach(L =>
    (L.add ? v.addStyle : v.setStyle).call(v, L.sel || {}, L.style || {}));
  if(!sp.side_on) (sp.shapes || []).forEach(S => { if(S.box) v.addBox(S.box); if(S.sphere) v.addSphere(S.sphere); });
  (sp.labels || []).forEach(L => {
    const a = v.selectedAtoms(L.sel || {})[0];
    if(a) v.addLabel(L.text, {position: {x: a.x, y: a.y, z: a.z}, fontSize: 15, fontColor: '#20262b',
      backgroundColor: '#ffffff', backgroundOpacity: 0.75, borderThickness: 0, inFront: true});
  });
  if(sp.zoom_to) v.zoomTo(sp.zoom_to); else v.zoomTo();
  v.zoom(sp.zoom || 1.08);
  v.render();
  await new Promise(r => setTimeout(r, 60));
  el.innerHTML = '<img class=rk-png alt="" src="' + await cropped(v.pngURI()) + '">';
}
/* A picture trimmed to what is drawn in it, with a margin. */
async function cropped(uri){
  const img = new Image();
  await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = uri; });
  const w = img.naturalWidth, h = img.naturalHeight;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d');
  g.drawImage(img, 0, 0);
  const px = g.getImageData(0, 0, w, h).data;
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for(let y = 0; y < h; y += 2) for(let x = 0; x < w; x += 2){
    const k = (y * w + x) * 4;
    if(px[k] < 245 || px[k + 1] < 245 || px[k + 2] < 245){
      if(x < x0) x0 = x; if(x > x1) x1 = x; if(y < y0) y0 = y; if(y > y1) y1 = y;
    }
  }
  if(x1 < 0) return uri;
  const m = Math.round(0.04 * Math.max(x1 - x0, y1 - y0));
  x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m); x1 = Math.min(w - 1, x1 + m); y1 = Math.min(h - 1, y1 + m);
  const o = document.createElement('canvas');
  o.width = x1 - x0 + 1; o.height = y1 - y0 + 1;
  const og = o.getContext('2d');
  og.fillStyle = '#ffffff'; og.fillRect(0, 0, o.width, o.height);
  og.drawImage(c, x0, y0, o.width, o.height, 0, 0, o.width, o.height);
  return o.toDataURL('image/png');
}
async function run(){
  const mine = ++DRAWING;
  const jobs = JOBS.slice(), total = jobs.length;
  for(let k = 0; k < total; k++){
    if(mine !== DRAWING) return;           // the page was built again meanwhile
    status('Drawing ' + (k + 1) + ' of ' + total + '\u2026');
    try { await drawStructure(jobs[k]); }
    catch(e){ const el = $(jobs[k].el); if(el) el.innerHTML = '<span class=rk-why>could not be drawn: ' + esc(e.message || e) + '</span>'; }
  }
  if(mine !== DRAWING) return;
  DRAWING = 0;
  status(D.ready || 'Ready. Press Save as PDF and choose \u201cSave as PDF\u201d as the printer.', true);
  if(PRINT_WAITING || (CFG.autoprint && !PRINTED)){ PRINT_WAITING = false; PRINTED = true; setTimeout(() => window.print(), 400); }
}
function savePDF(){
  if(DRAWING){ PRINT_WAITING = true; status('Still drawing; the print window opens when it is done\u2026'); return; }
  window.print();
}

/* ---- views and the methods ---------------------------------------------------- */
function wireViews(){
  document.querySelectorAll('.rk-own[contenteditable]').forEach(el => el.addEventListener('blur', async () => {
    const r = await call(CFG.edit, Object.assign({}, CFG.params, {opt: OPT, id: el.dataset.id, own: el.textContent.trim()}));
    if(r.error) status(r.error); else if(r.views) D.views = r.views;
  }));
  document.querySelectorAll('.rk-viewbtns button[data-act]').forEach(b => b.addEventListener('click', async () => {
    const act = b.dataset.act;
    if(act === 'remove' && !confirm('Take this view out of the report?')) return;
    const body = Object.assign({}, CFG.params, {opt: OPT, id: b.dataset.id});
    if(act === 'remove') body.remove = true; else body.move = act === 'up' ? -1 : 1;
    const r = await call(CFG.edit, body);
    if(r.error){ status(r.error); return; }
    if(r.views) D.views = r.views;
    build();
  }));
}
function methodsText(){
  const div = document.createElement('div');
  div.innerHTML = refs(String(D.methods || '').replace(/<\/p>\s*<p[^>]*>/g, '</p>\n\n<p>'));
  const body = (div.innerText || div.textContent || '').trim();
  const list = (D.references || []).map(r => r.n + '. ' + r.text).join('\n');
  return body + (list ? '\n\nReferences\n' + list : '') + '\n';
}
async function copyMethods(){
  const text = methodsText();
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; }
  catch(e){
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand('copy'); } catch(e2){}
    ta.remove();
  }
  const b = $('rk-copy');
  if(b){ b.textContent = ok ? 'Copied' : 'Could not copy'; setTimeout(() => { b.textContent = 'Copy the methods and references'; }, 2500); }
}

/* ---- the bar ------------------------------------------------------------------ */
function sectionChoices(){
  const out = (D.sections || []).map(s => [s.id, s.label || s.heading]);
  if(Array.isArray(D.views)) out.push(['views', 'views']);
  if(D.methods) out.push(['methods', 'methods']);
  if((D.appendix || []).length) out.push(['appendix', 'appendix']);
  return out;
}
function optionHTML(o){
  const id = 'rk-o-' + o.key, val = OPT[o.key] !== undefined ? OPT[o.key] : (o.value !== undefined ? o.value : o.default);
  const help = o.help ? ' title="' + esc(o.help) + '"' : '';
  if(o.type === 'check') return '<label class=rk-chk' + help + '><input type=checkbox id="' + id + '"' + (val ? ' checked' : '')
    + '> ' + esc(o.label) + '</label>';
  if(o.type === 'select') return '<label' + help + '>' + esc(o.label) + ' <select id="' + id + '">' + (o.choices || []).map(([v, t]) =>
    '<option value="' + esc(v) + '"' + (String(v) === String(val) ? ' selected' : '') + '>' + esc(t) + '</option>').join('')
    + '</select></label>';
  return '<label' + help + '>' + esc(o.label) + ' <input type=number id="' + id + '" value="' + esc(val) + '"'
    + (o.least !== undefined ? ' min="' + esc(o.least) + '"' : '') + (o.most !== undefined ? ' max="' + esc(o.most) + '"' : '')
    + ' step="' + esc(o.step || 1) + '"></label>';
}
function bar(){
  const el = $('rk-bar');
  const secs = D ? sectionChoices() : [];
  el.innerHTML = '<div class=rk-barrow><b class=rk-name>' + esc(CFG.name || 'Report') + '</b>'
    + (D ? (D.options || []).map(optionHTML).join('') : '')
    + (secs.length ? '<span class=rk-secs><span class=rk-lb>Include</span>' + secs.map(([id, t]) => '<label class=rk-chk><input type=checkbox'
      + ' data-sec="' + esc(id) + '"' + (SHOW[id] !== false ? ' checked' : '') + '> ' + esc(t) + '</label>').join('') + '</span>' : '')
    + '<span class=rk-zoom title="Zoom the page to read it; the PDF is not affected">'
    + '<button id=rk-zout type=button aria-label="Zoom out">\u2212</button><button id=rk-zpct type=button title="Back to 100%">100%</button>'
    + '<button id=rk-zin type=button aria-label="Zoom in">+</button>'
    + '<button id=rk-zfit type=button title="The page as wide as the window">fit width</button></span>'
    + (D && D.methods ? '<button id=rk-copy type=button class=rk-ghost>Copy the methods and references</button>' : '')
    + '<button id=rk-print type=button class=rk-primary>Save as PDF</button></div><div id=rk-status class=rk-status>'
    + ($('rk-status') ? esc($('rk-status').textContent) : 'Reading the run\u2026') + '</div>';
  (D ? D.options || [] : []).forEach(o => {
    const c = $('rk-o-' + o.key);
    if(!c) return;
    c.addEventListener('change', () => {
      let v = o.type === 'check' ? c.checked : c.value;
      if(o.type === 'number'){
        v = Math.round(+v);
        if(!isFinite(v)) v = o.default;
        if(o.least !== undefined) v = Math.max(o.least, v);
        if(o.most !== undefined) v = Math.min(o.most, v);
        c.value = v;
      }
      OPT[o.key] = v; store('options', OPT); load();
    });
  });
  el.querySelectorAll('input[data-sec]').forEach(c => c.addEventListener('change', () => {
    SHOW[c.dataset.sec] = c.checked; store('sections', SHOW); build();
  }));
  $('rk-print').addEventListener('click', savePDF);
  if($('rk-copy')) $('rk-copy').addEventListener('click', copyMethods);
  $('rk-zout').addEventListener('click', () => setZoom(ZOOM - 0.1));
  $('rk-zin').addEventListener('click', () => setZoom(ZOOM + 0.1));
  $('rk-zpct').addEventListener('click', () => setZoom(1));
  $('rk-zfit').addEventListener('click', () => {
    const doc = $('rk-doc');
    doc.style.zoom = 1;
    setZoom((document.documentElement.clientWidth - 32) / doc.getBoundingClientRect().width);
  });
  setZoom(ZOOM);
}
/* The zoom is for reading on screen: kept in this browser, 50 to 200 %. The
   print style puts it back to 100 %, so the PDF is the same whatever it is. */
function setZoom(z){
  ZOOM = Math.max(0.5, Math.min(2, Math.round(z * 100) / 100));
  if($('rk-doc')) $('rk-doc').style.zoom = ZOOM;
  if($('rk-zpct')) $('rk-zpct').textContent = Math.round(ZOOM * 100) + '%';
  store('zoom', ZOOM);
}

/* ---- starting ----------------------------------------------------------------- */
async function load(){
  status('Reading the run\u2026');
  const q = Object.assign({}, CFG.params);
  Object.keys(OPT).forEach(k => { q['opt.' + k] = typeof OPT[k] === 'boolean' ? (OPT[k] ? 1 : 0) : OPT[k]; });
  const got = await call(CFG.api + (CFG.api.indexOf('?') >= 0 ? '&' : '?') + query(q));
  if(got.error){ status(got.error); if(!D) $('rk-doc').innerHTML = '<p class=rk-why>' + esc(got.error) + '</p>'; return; }
  D = got;
  (D.options || []).forEach(o => { if(OPT[o.key] === undefined && o.value !== undefined) OPT[o.key] = o.value; });
  if(D.title) document.title = D.title + ' \u00b7 report';
  bar();
  build();
}
function start(cfg){
  CFG = Object.assign({api: '/api/report', params: {}, store: 'reportkit', name: 'Report'}, cfg || {});
  if(!$('rk-bar')){ const b = document.createElement('div'); b.id = 'rk-bar'; document.body.insertBefore(b, document.body.firstChild); }
  if(!$('rk-doc')){ const m = document.createElement('main'); m.id = 'rk-doc'; document.body.appendChild(m); }
  OPT = store('options') || {};
  SHOW = store('sections') || {};
  const z = parseFloat(store('zoom'));
  ZOOM = z >= 0.5 && z <= 2 ? z : 1;
  bar();
  return load();
}

window.ReportKit = {start, version: VERSION, num, esc,
                    /* for a tool's own tests */ _state: () => ({D, OPT, SHOW, ZOOM, NUMS})};
})();
