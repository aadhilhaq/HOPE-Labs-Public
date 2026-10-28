"""The bar along the bottom of a form page of ours: one still line that fits, and Credits.

By Aadhil Haq

docskit.creditbar_html is the documentation kit's bar, shared byte for byte with the lab's other
interfaces, and it rolls the credits across the window when they do not fit. On a form that is a
line of text moving past the edges of the screen while somebody is trying to fill the form in.

So a form page carries this one instead. One line, cut with an ellipsis where the window ends
rather than moving: the page and its version, then the first sentence of the copyright. Beside it
a Credits button, which opens the whole statement above the line, wrapped, with every credit on a
line of its own; then Docs and How to cite. Nothing is dropped and nothing runs off the window.
Its styles are in /static/style.css (.hlfoot), so every page that loads the shared stylesheet
draws it the same way.

The bar sits at the end of the page's body, which is a column the full height of the window, so
it takes its own row at the bottom rather than being laid over the form.
"""
from __future__ import annotations

import html
import re

#: Opens and closes the list. Inline, because the bar is put into the page by the server and
#: must work on a page that loads none of the shared scripts.
_TOGGLE = """<script>
(function () {
  var foot = document.currentScript.parentNode;
  var more = foot.querySelector(".hlfoot-more"), all = foot.querySelector(".hlfoot-all");
  more.onclick = function () {
    var opening = all.hidden;
    all.hidden = !opening;
    more.setAttribute("aria-expanded", String(opening));
    more.textContent = opening ? "Hide credits" : "Credits";
  };
})();
</script>"""


def bar(site, docs_href="/docs/index.html"):
    """The bar for a docskit.Site: the same statement and credits its own bar carries."""
    head = "%s %s" % (html.escape(site.app), html.escape(site.version))
    statement = html.escape(site.copyright)
    # The first sentence, for the one line. "(c)" has no full stop in it, so splitting on ". "
    # cannot cut the statement in the wrong place.
    first = html.escape(site.copyright.split(". ")[0].rstrip(".") + ".")
    plain = html.unescape(re.sub(r"<[^>]+>", "", " · ".join(
        ["%s %s" % (site.app, site.version), site.copyright] + list(site.credits))))
    links = ['<a href="%s" target="_blank" rel="noopener">Docs</a>' % html.escape(docs_href)]
    if site.cite_href:
        links.append('<a href="%s" target="_blank" rel="noopener">How to cite</a>'
                     % html.escape(site.cite_href))
    credits = "".join("<li>%s</li>" % line for line in site.credits)  # already HTML
    return (
        '<footer class="hlfoot" role="contentinfo" aria-label="%s">'
        '<div class="hlfoot-all" id="hlfoot-all" hidden>'
        '<p><b>%s</b></p><p>%s</p><ul>%s</ul></div>'
        '<div class="hlfoot-line">'
        '<span class="hlfoot-text" title="%s">%s &middot; %s</span>'
        '<button type="button" class="hlfoot-more" aria-expanded="false" '
        'aria-controls="hlfoot-all">Credits</button>'
        '<span class="hlfoot-links">&middot; %s</span>'
        '</div>%s</footer>'
        % (html.escape(plain, quote=True), head, statement, credits,
           html.escape(plain, quote=True), head, first, " &middot; ".join(links), _TOGGLE))
