"""What the launcher does when started, however it was started: `python -m hope_labs`, the
console script, or a bundled executable running a script that imports it."""
import sys


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if "--version" in argv:
        from . import credit, __version__
        print("HOPE Labs %s\n%s" % (__version__, credit()))
        return 0
    # --selftest is for the build: a windowed executable cannot be driven without a display, but
    # it can prove that it starts, that its imports resolve and that what it needs came along.
    if "--selftest" in argv:
        from . import tools, tunnel, hub, clusters, credit            # noqa: F401
        from hope_flow import cards as flowcards
        # Counted against the cards rather than against a number: every tool has a card that
        # starts it and every such card a tool, so one cannot go missing without the other.
        started = {c.tool for c in flowcards.CARDS if c.tool}
        assert tools.TOOLS and started == set(tools.BY_KEY) and \
            len(tools.BY_KEY) == len(tools.TOOLS), \
            "a tool went missing from the catalog: %s" % sorted(started ^ set(tools.BY_KEY))
        # Every tool here has a page of its own and starts with a line that honors the runs
        # folder. The pages that are ours are in this repository rather than in the install, so
        # their lines run from the HOPE Labs checkout and name the install as a setting.
        pages = [t for t in tools.TOOLS if not t.flow_only]
        assert len(pages) == len(tools.TOOLS), "a tool lost its page: " + str(
            sorted(set(tools.BY_KEY) - {t.key for t in pages}))
        for tool in pages:
            line = tool.command(runs="~/r", port=8123)
            assert tool.install in line or '"$HOME"' in line, line
            assert '"$HOME"/r' in line, (tool.key, line)
        from . import ensemble as labs_ensemble, \
            folding as labs_folding, pages as labs_pages
        ours = []
        for module, serves in (("pages", labs_pages.SERVES), ("folding", labs_folding.SERVES),
                               ("ensemble", labs_ensemble.SERVES)):
            ours += [(k, module) for k in serves]
        assert {k for k, _m in ours} == {t.key for t in pages if "-m hope_labs." in
                                         t.command(runs="", port=8123)}, \
            "a page of ours went missing, or one is started from an install: " + str(ours)
        for key, module in ours:
            line = tools.BY_KEY[key].command(runs="", port=8123)
            assert tools.LABS_INSTALL in line, line
            assert "hope_labs.%s.serve %s" % (module, key) in line, line
            assert "NO-LABS" in line, "a missing HOPE Labs checkout is not named: " + line
        # the three handshakes, each read off what its tool actually prints
        assert tools.BY_KEY["hopemd"].ready("open  http://127.0.0.1:8098/?t=abcDEF_-", 0) == (8098, "abcDEF_-")
        assert tools.BY_KEY["rfdiffusion"].ready("open  http://127.0.0.1:8131/?t=Qx_7-y", 0) == (8131, "Qx_7-y")
        assert tools.BY_KEY["boltz2"].ready("open  http://127.0.0.1:8141/?t=fold_1-x", 0) == (8141, "fold_1-x")
        assert tools.BY_KEY["hopemd"].ready("nothing yet", 0) is None
        # A copy in somebody's own scratch: one root moves them all, a tool named on its own
        # wins over it, and nothing set leaves the lab's installs alone.
        mine = tools.installs_for("/scratch/user/jane.doe")
        assert len(mine) == len(tools.TOOLS), mine
        assert mine["boltz2"] == "/scratch/user/jane.doe/envs/boltz", mine
        assert mine["hopemd"] == "/scratch/user/jane.doe/HOPE-MD/MD", mine
        assert tools.installs_for("") == {}, "no root means the lab's own installs"
        assert tools.installs_for("", {"hopemd": "/u/md"}) == {"hopemd": "/u/md"}
        assert tools.rebase("/elsewhere/HOPE-MD", "/x") == "/elsewhere/HOPE-MD"
        assert clusters.half_typed("/scratch/user/j/x", "jane.doe")
        # A cluster new to HOPE Labs is set up from the script this launcher carries, so a build
        # that left it behind could sign in anywhere and set up nowhere.
        from . import hub as hubmod
        assert hubmod.bootstrap_script().startswith("#!/bin/sh"), "the set-up script is missing"
        assert tools.find("setup") is not None and "setup" not in tools.BY_KEY
        # the account box: an e-mail address is cut to the name before the @, and every site
        # shows an example in the empty box
        assert clusters.clean_user(" jdoe@tamu.edu ") == "jdoe"
        assert clusters.clean_user("jdoe") == "jdoe" and clusters.clean_user("") == ""
        assert all(clusters.defaults_for(site)["placeholder"] for site in clusters.sites())
        assert "@tamu.edu" in clusters.defaults_for("Grace (TAMU)")["placeholder"]
        notes = []
        try:
            import paramiko
            notes.append("paramiko " + paramiko.__version__)
        except ImportError:
            notes.append("paramiko NOT INSTALLED - the launcher cannot connect until it is")
        try:
            from . import app
            assert hasattr(app, "App") and hasattr(app, "main"), "no window"
            # A hand-written "root" has to survive the sign-in boxes being written over it:
            # they are saved as Sign in is pressed, just before the installs are read.
            import tempfile, shutil, os as _os
            from . import settings as store
            tmp = tempfile.mkdtemp()
            try:
                store.CONFIG, keep = _os.path.join(tmp, "settings.json"), store.CONFIG
                app.save({"root": "/scratch/user/jane.doe"})
                store.flow_save({"version": 1, "name": "kept", "nodes": [], "edges": []})
                app.save({"site": "Grace (TAMU)", "user": "jane.doe"})
                kept = app.load()
                assert kept.get("root") == "/scratch/user/jane.doe", kept
                assert kept.get("user") == "jane.doe", kept
                assert [f["name"] for f in store.flows()] == ["kept"], kept
                store.CONFIG = keep
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            notes.append("window ok, settings and flows kept across a sign-in")
        except ImportError as exc:
            notes.append("no tkinter here (%s)" % exc)
        import os
        for name in ("index.html", "app.js", "flow.js", "style.css", "tool.html", "tool.js"):
            path = os.path.join(hub.WEB, name)
            assert os.path.isfile(path), "the page is missing from the bundle: " + name
        notes.append("page ok")
        # The viewer's surfaces are drawn by the page code the shared surface module carries,
        # served as /static/msms.js; a build that left the module behind would serve none.
        from . import surface
        assert "function msmsAdd" in surface.VIEWER_JS, "the surface module has no page code"
        notes.append("surface page code ok")
        # The canvas: the card catalog the runner reads, and the example it starts from.
        from . import canvas
        from hope_flow import cards
        assert len(canvas.catalogue()["cards"]) == len(cards.CARDS) > 9, \
            "a card went missing from the catalog"
        assert canvas.check(canvas.example())["problems"] == [], canvas.check(canvas.example())
        notes.append("canvas ok, %d cards" % len(canvas.catalogue()["cards"]))
        # the docs ride along in web/, so a build that left them behind is caught here too
        from . import docs
        missing = docs.audit()
        assert not missing, "the docs are not whole: " + "; ".join(missing[:3])
        page = hub.index_page()
        assert "__CREDIT" not in page and 'class="creditbar"' in page, "the bar along the bottom is missing"
        notes.append("docs ok, %d pages" % len(docs.SITE.order))
        print("%s\n  %s\nSELFTEST OK" % (credit(), "\n  ".join(notes)))
        return 0
    from .app import main as run
    run()
    return 0
