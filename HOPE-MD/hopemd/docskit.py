"""Documentation pages served by the interface, laid out as ADapt's are.

By Aadhil Haq

A page is an HTML fragment, pages/<slug>.html: an <h1>, a lead paragraph
(<p class="lead">), then sections under <h2>. This module wraps each one in
the shared layout: the sidebar with its sections, previous and next, and the
footer. Screenshots, where an interface has them, are in img/: of the
interface itself, taken by the repository's tools/docs_screenshots.py, so they
show the page as it is."""
import html
import os
import re

#: The colors ADapt's documentation uses, as the default. An interface
#: passes its own, so the docs read as part of the same tool.
LIGHT = {"bg": "#f7f8fb", "card": "#fff", "line": "#dfe3ee", "fg": "#1b2333",
         "dim": "#5d6780", "accent": "#1a56db", "accent2": "#e8efff",
         "warn": "#8a5300", "warnbg": "#fff6e0", "code": "#f2f4fa", "mark": "#d6336c"}
DARK = {"bg": "#0f1219", "card": "#161b26", "line": "#28303f", "fg": "#e7ecf5",
        "dim": "#9aa6bd", "accent": "#6d9bf5", "accent2": "#1d2942",
        "warn": "#f0b35a", "warnbg": "#2a2213", "code": "#1b2130", "mark": "#ff7aa2"}

_IMG = re.compile(r"img/[A-Za-z0-9_.-]+\.(png|jpg|svg)$")
_TYPES = {"png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml"}


class Site:
    """Everything the layout needs to know about one interface's docs."""

    def __init__(self, app, tagline, version, copyright, nav, root,
                 help_links=None, credits=(), theme_key="", light=None, dark=None,
                 shots="tools/docs_screenshots.py", cite_href=""):
        self.app = app
        self.tagline = tagline          # under the name in the sidebar
        self.version = version
        self.copyright = copyright      # "Copyright (c) 2026 ..., as LICENSE states"
        self.nav = list(nav)            # [(slug, title, section), ...]
        self.root = root                # folder holding pages/ and img/
        self.help_links = dict(help_links or {})   # help key -> "page.html#anchor"
        self.credits = list(credits)    # who does the work, for the bar
        self.theme_key = theme_key      # the interface's localStorage key
        self.light = dict(LIGHT, **(light or {}))
        self.dark = dict(DARK, **(dark or {}))
        self.shots = shots
        self.cite_href = cite_href
        self.order = [s for s, _t, _sec in self.nav]
        self.titles = {s: t for s, t, _sec in self.nav}


# --- the pages ---------------------------------------------------------------
def _anchor(text):
    a = re.sub(r"[^a-z0-9]+", "-", html.unescape(re.sub(r"<[^>]+>", "", text)).lower())
    return a.strip("-") or "section"


def with_ids(body):
    """Every h2 gets an id, so every section can be linked to."""
    seen = set(re.findall(r'<h[23][^>]*\sid="([^"]+)"', body))

    def add(m):
        attrs, inner = m.group(1), m.group(2)
        if re.search(r'\sid=', attrs):
            return m.group(0)
        a = _anchor(inner)
        while a in seen:
            a += "-2"
        seen.add(a)
        return '<h2%s id="%s">%s</h2>' % (attrs, a, inner)
    return re.sub(r"<h2([^>]*)>(.*?)</h2>", add, body, flags=re.S)


def fragment(site, slug):
    try:
        with open(os.path.join(site.root, "pages", slug + ".html"), encoding="utf-8") as fh:
            return with_ids(fh.read().strip())
    except OSError:
        return None


def nav_html(site, current):
    out, section = [], None
    for slug, title, sec in site.nav:
        if sec != section:
            if section is not None:
                out.append("</ul>")
            out.append("<h4>%s</h4><ul>" % html.escape(sec))
            section = sec
        cls = ' class="here" aria-current="page"' if slug == current else ""
        out.append('<li><a%s href="%s.html">%s</a></li>' % (cls, slug, html.escape(title)))
    out.append("</ul>")
    return "\n".join(out)


def render(site, slug):
    """One page in the layout, or None when there is no such page."""
    if slug not in site.titles:
        return None
    content = fragment(site, slug)
    if content is None:
        return None
    m = re.search(r"<h1>(.*?)</h1>", content, re.S)
    title = re.sub("<[^>]+>", "", m.group(1)) if m else site.titles[slug]
    i = site.order.index(slug)
    prev_ = ('<a href="%s.html">&larr; %s</a>' % (site.order[i - 1], html.escape(site.titles[site.order[i - 1]]))
             if i else "<span></span>")
    next_ = ('<a href="%s.html">%s &rarr;</a>' % (site.order[i + 1], html.escape(site.titles[site.order[i + 1]]))
             if i + 1 < len(site.order) else "<span></span>")
    theme = ("try{var k=%s;var t=k&&localStorage.getItem(k);"
             "if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}catch(e){}"
             % _js(site.theme_key))
    return TEMPLATE.format(
        title=html.escape(title), app=html.escape(site.app), tagline=html.escape(site.tagline),
        version=html.escape(site.version), theme=theme, nav=nav_html(site, slug),
        content=content, prev=prev_, next=next_,
        copyright=html.escape(site.copyright.rstrip(".")), shots=html.escape(site.shots))


def _js(s):
    return '"' + str(s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} &middot; {app} documentation</title>
<script>{theme}</script>
<link rel="stylesheet" href="style.css?v={version}">
</head><body><div class="layout">
<nav class="side">
<a class="brand" href="index.html">{app}<small>{tagline}</small></a>
{nav}
</nav>
<main>
{content}
<div class="pn">{prev}{next}</div>
<footer class="page">{app} documentation, version {version}. {copyright}.
Every screenshot is of {app} itself, made by <code>{shots}</code>.</footer>
</main></div></body></html>
"""


# --- the stylesheet ------------------------------------------------------------
def _tokens(t):
    return "".join("--%s:%s;" % (k, v) for k, v in t.items())


def style_css(site):
    return STYLE.replace("__LIGHT__", _tokens(site.light)).replace("__DARK__", _tokens(site.dark))


STYLE = """/* The documentation's look, shared with the interface's colors. */
:root{color-scheme:light;__LIGHT__}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;__DARK__}}
:root[data-theme="dark"]{color-scheme:dark;__DARK__}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);
  font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}
nav.side{position:sticky;top:0;align-self:start;height:100vh;overflow:auto;
  padding:22px 18px;border-right:1px solid var(--line);background:var(--card)}
nav.side .brand{display:block;font-size:22px;font-weight:700;letter-spacing:.2px;color:var(--fg)}
nav.side .brand:hover{text-decoration:none}
nav.side .brand small{display:block;font-size:12.5px;font-weight:400;color:var(--dim);letter-spacing:0}
nav.side h4{margin:22px 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim)}
nav.side ul{list-style:none;margin:0;padding:0}
nav.side li a{display:block;padding:4px 10px;border-radius:6px;color:var(--fg);font-size:14.5px}
nav.side li a:hover{background:var(--accent2);text-decoration:none}
nav.side li a.here{background:var(--accent2);color:var(--accent);font-weight:600}
main{max-width:1060px;min-width:0;padding:34px 48px 80px}
h1{font-size:32px;line-height:1.2;margin:0 0 6px}
.lead{font-size:18px;color:var(--dim);margin:0 0 26px}
h2{font-size:23px;margin:40px 0 10px;padding-top:6px;border-top:1px solid var(--line)}
h3{font-size:18px;margin:26px 0 6px}
p,li{max-width:78ch}
code,kbd{font:13.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);
  padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}
pre{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--card);
  border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow:auto}
pre code{background:none;padding:0}
figure{margin:18px 0 26px}
figure img{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;
  box-shadow:0 2px 10px rgba(20,30,60,.07);background:#fff}
figure.small img{max-width:min(560px,100%)}
figcaption{font-size:14px;color:var(--dim);margin-top:8px;max-width:95ch}
figcaption b{color:var(--fg)}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.pair figure{margin:0}
.note{background:var(--warnbg);border-left:4px solid #e0a100;padding:10px 14px;border-radius:6px;margin:16px 0;max-width:86ch}
.note b:first-child{color:var(--warn)}
.tip{background:var(--accent2);border-left:4px solid var(--accent);padding:10px 14px;border-radius:6px;margin:16px 0;max-width:86ch}
table{border-collapse:collapse;margin:12px 0 20px;font-size:14.5px;background:var(--card)}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--code)}
ol.legend{columns:2;column-gap:36px;padding-left:22px}
ol.legend li{break-inside:avoid;margin-bottom:6px}
ol.legend li::marker{color:var(--mark);font-weight:700}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin:14px 0}
.card{display:block;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;color:var(--fg)}
.card:hover{border-color:var(--accent);text-decoration:none}
.card img{display:block;width:100%;height:150px;object-fit:cover;object-position:left top;border-bottom:1px solid var(--line)}
.card span{display:block;padding:9px 12px 11px;font-size:14px}
.card span b{display:block;font-size:15px;margin-bottom:2px}
.steps{counter-reset:s;list-style:none;padding-left:0}
.steps>li{counter-increment:s;position:relative;padding-left:38px;margin-bottom:10px}
.steps>li::before{content:counter(s);position:absolute;left:0;top:1px;width:26px;height:26px;border-radius:13px;
  background:var(--accent);color:var(--card);font-weight:700;font-size:14px;display:flex;align-items:center;justify-content:center}
footer.page{margin-top:60px;padding-top:14px;border-top:1px solid var(--line);font-size:13px;color:var(--dim)}
.pn{display:flex;justify-content:space-between;margin-top:40px;font-size:15px}
@media (max-width:900px){.layout{grid-template-columns:minmax(0,1fr)}nav.side{position:static;height:auto}
  main{padding:24px 18px}.pair{grid-template-columns:1fr}ol.legend{columns:1}}
"""


# --- the bar along the bottom of the interface ---------------------------------
#: ADapt's app page carries one line along the bottom: the version, the
#: copyright and who does the work. The interface inlines this style and
#: places creditbar_html() last in its body.
#: The credits are long enough to fill the line, so they are the part that is
#: cut, and the links keep their place at the end where they can be clicked.
CREDITBAR_CSS = """
.creditbar{position:fixed;left:0;right:0;bottom:0;z-index:40;height:28px;line-height:28px;
  padding:0 14px;font-size:11.5px;color:var(--dim);background:var(--card);
  border-top:1px solid var(--line);display:flex;gap:0 6px;align-items:center}
/* The credits are longer than any bar: every tool and every engine is named, and the answer to a
   credit that will not fit is not to drop the credit. So when it overflows it rolls, once the
   page has worked out that it does; until then, and whenever it fits, it sits still and is cut
   with an ellipsis exactly as it always was. It stops while the pointer is on it, so a name can
   be read, and it never starts at all for somebody who has asked for less motion. */
.creditbar .rolling{overflow:hidden;flex:1 1 auto;min-width:0}
.creditbar .rolling .credittext{display:inline-block;padding-right:3em;
  animation:creditroll var(--rolltime,60s) linear infinite}
.creditbar .rolling:hover .credittext,
.creditbar .rolling:focus-within .credittext{animation-play-state:paused}
@keyframes creditroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}
@media (prefers-reduced-motion:reduce){
  .creditbar .rolling .credittext{animation:none}
}
/* For somebody who has asked for less motion the credits do not roll, and a still line would cut
   them off, so the bar grows to as many lines as they need instead. */
.creditbar.still{height:auto;line-height:1.45;padding:3px 14px;align-items:flex-start}
.creditbar.still .credittext{white-space:normal;overflow:visible;text-overflow:clip}
.creditbar .credittext{min-width:0;flex:1 1 auto;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.creditbar .creditlinks{flex:none;white-space:nowrap}
.creditbar a{color:var(--dim)}
.creditbar a:hover{color:var(--accent)}
body{padding-bottom:30px}
"""


#: Makes the credits roll, but only when they do not fit. Measured rather than assumed: a short
#: bar that scrolled anyway would be a fidget, and the same markup serves interfaces whose credits
#: are two lines long. The text is doubled so the loop has no gap in it, and the speed follows the
#: length so a long bar is not slower to read through than a short one.
CREDITROLL = """<script>
(function () {
  var bar = document.currentScript.parentNode;
  var text = bar.querySelector(".credittext");
  if (!text) return;
  var said = text.innerHTML;
  var calm = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  function measure() {
    var box = text.parentNode.classList.contains("rolling") ? text.parentNode : null;
    if (box) { box.replaceWith.call(box, text); text.innerHTML = said; }   // back to still
    text.classList.remove("rolled");
    bar.classList.remove("still");
    document.body.style.paddingBottom = "";
    if (text.scrollWidth <= text.clientWidth + 2) return;                  // it fits; leave it
    if (calm && calm.matches) {                    // no rolling: wrap onto more lines instead
      bar.classList.add("still");
      document.body.style.paddingBottom = (bar.offsetHeight + 4) + "px";
      return;
    }
    var roll = document.createElement("span");
    roll.className = "rolling";
    text.parentNode.insertBefore(roll, text);
    roll.appendChild(text);
    text.innerHTML = said + ' &middot; ' + said;      // doubled, so the loop closes on itself
    text.style.setProperty("--rolltime", Math.max(30, Math.round(text.scrollWidth / 55)) + "s");
  }
  measure();
  var again;
  // window. written out: a bare addEventListener is the same call, but a check that every name
  // a page calls is defined cannot tell it from a typo.
  window.addEventListener("resize", function () { clearTimeout(again); again = setTimeout(measure, 250); });
  if (calm && calm.addEventListener) calm.addEventListener("change", measure);
})();
</script>"""


def creditbar_html(site, docs_href="/docs/index.html", cite_id=""):
    """The bar. cite_id names the How to cite link, for an interface whose
    script opens its citation dialog from it."""
    parts = ["%s %s" % (html.escape(site.app), html.escape(site.version)),
             html.escape(site.copyright)]
    parts += [c for c in site.credits]          # already HTML, from the interface
    links = ['<a href="%s" target="_blank" rel="noopener">Docs</a>' % html.escape(docs_href)]
    if site.cite_href:
        links.append('<a href="%s"%s>How to cite</a>' % (
            html.escape(site.cite_href), (' id="%s"' % html.escape(cite_id)) if cite_id else ""))
    plain = html.unescape(re.sub(r"<[^>]+>", "", " · ".join(parts + links)))
    # aria-label carries the credits once, in reading order; the text itself is doubled to make
    # the roll seamless, and a reader given that twice is being told the same thing twice.
    return ('<div class="creditbar" role="contentinfo" title="%s" aria-label="%s">'
            '<span class="credittext" id="credittext" aria-hidden="true">%s</span>'
            '<span class="creditlinks">&middot; %s</span>%s</div>'
            % (html.escape(plain, quote=True), html.escape(plain, quote=True),
               " &middot; ".join(parts), " &middot; ".join(links), CREDITROLL))


# --- serving -------------------------------------------------------------------
def asset(site, name):
    """(bytes, content type) for style.css or an image, else None."""
    if name == "style.css":
        return style_css(site).encode("utf-8"), "text/css"
    if _IMG.match(name):
        try:
            with open(os.path.join(site.root, name), "rb") as fh:
                return fh.read(), _TYPES[name.rsplit(".", 1)[1]]
        except OSError:
            return None
    return None


def respond(site, path, docs_path="/docs/"):
    """What to send for a request under docs_path: (status, content type,
    bytes, location). The interface's handler writes it out."""
    if path.rstrip("/") == docs_path.rstrip("/"):
        return 302, "text/plain", b"", docs_path + "index.html"
    name = path[len(docs_path):]
    if name.endswith(".html") and "/" not in name:
        page = render(site, name[:-5])
        if page is None:
            return 404, "text/plain", b"No such page in the docs.", None
        return 200, "text/html; charset=utf-8", page.encode("utf-8"), None
    got = asset(site, name)
    if got is None:
        return 404, "text/plain", b"No such file in the docs.", None
    body, ctype = got
    return 200, ctype, body, None


# --- what a test can check -----------------------------------------------------
_DASHES = re.compile("—|–| - | -- ")


def audit(site):
    """Everything wrong with the docs, as a list of sentences; empty when
    every page exists, every link and picture resolves, and the prose
    carries no dash punctuation."""
    bad = []
    ids = {}
    bodies = {}
    for slug in site.order:
        body = fragment(site, slug)
        if body is None:
            bad.append("%s.html is in the sidebar but there is no pages/%s.html" % (slug, slug))
            continue
        bodies[slug] = body
        if "<h1>" not in body:
            bad.append("%s.html has no <h1>" % slug)
        if 'class="lead"' not in body and "class=lead" not in body:
            bad.append("%s.html has no lead paragraph" % slug)
        ids[slug] = set(re.findall(r'<h[23][^>]*\sid="([^"]+)"', body))
    for slug, body in bodies.items():
        for href in re.findall(r'href="([^"#]+\.html)(#[^"]*)?"', body):
            target, frag = href[0], href[1].lstrip("#")
            if "://" in target or target.startswith("/"):
                continue
            t = target[:-5]
            if t not in site.titles:
                bad.append("%s.html links to %s, which is not a page" % (slug, target))
            elif frag and t in ids and frag not in ids[t]:
                bad.append("%s.html links to %s#%s, and that section does not exist" % (slug, target, frag))
        for frag in re.findall(r'href="#([^"]+)"', body):
            if frag not in ids[slug]:
                bad.append("%s.html links to #%s on itself, which does not exist" % (slug, frag))
        for src in sorted(set(re.findall(r'src="(img/[^"]+)"', body))):
            if not os.path.isfile(os.path.join(site.root, src)):
                bad.append("%s.html shows %s, which is not there" % (slug, src))
        prose = re.sub(r"<(pre|code)[^>]*>.*?</\1>", " ", body, flags=re.S)
        prose = html.unescape(re.sub(r"<[^>]+>", " ", prose))
        m = _DASHES.search(prose)
        if m:
            at = max(0, m.start() - 40)
            bad.append("%s.html uses a dash as punctuation: %r" % (slug, prose[at:m.end() + 40].strip()))
    for key, link in site.help_links.items():
        page, _, frag = link.partition("#")
        t = page[:-5] if page.endswith(".html") else page
        if t not in site.titles:
            bad.append("help %s points at %s, which is not a page" % (key, page))
        elif frag and t in ids and frag not in ids[t]:
            bad.append("help %s points at %s#%s, and that section does not exist" % (key, page, frag))
    return bad
