"""The window: sign in, then hand over to the page.

Tkinter, because it is in the standard library and a launcher that needs a toolkit installed
before it can ask for a password is not a launcher. It is deliberately small — a cluster, an
account, a button and a log — because the tools themselves live in the browser, where there is
room for them. The sign-in is the HOPE-MD launcher's, Duo dialogue and all, since that part is
the same wherever it is used.
"""
import os
import queue
import threading
import webbrowser

import tkinter as tk
from tkinter import messagebox, ttk

from . import APP, TAGLINE, __version__, credit, ACK
from . import clusters, hub as hubmod, tunnel
# The settings file is read by the hub and the canvas as well as by this window, so where it is
# and how it is written live in one module rather than here.
from .settings import SCHEMA, labs_for, load, save

BANNER = dict(deep="#0b2b33", deeper="#061a20", teal="#5fb3c4", warm="#c2603a",
              paper="#f4f8f8", muted="#b7d6dc", faint="#8fb6bd")


def labs_here(settings, address):
    """Where HOPE Labs is on the cluster at `address`: what this computer was told for it, else
    the folder named by "root" (or HOPE_LABS_ROOT), else "" for the lab's own."""
    told = labs_for(address)
    if told:
        return told
    root = (os.environ.get("HOPE_LABS_ROOT", "") or (settings or {}).get("root", "")).strip()
    return root.rstrip("/") + "/HOPE-labs" if root else ""


def chosen_installs(settings):
    """Where this person's copies of the tools are, from the settings file.

    Nothing set means the lab's own installs, which is what a member of the group wants. "root"
    is one folder holding all five in the lab's layout; "installs" names a tool on its own and
    wins over it. HOPE_LABS_ROOT in the environment beats the file, for trying one out without
    editing it. Documented in docs/install-on-grace.md.
    """
    from . import tools as catalogue
    root = os.environ.get("HOPE_LABS_ROOT", "") or (settings or {}).get("root", "")
    named = (settings or {}).get("installs") or {}
    if not isinstance(named, dict):
        named = {}
    return catalogue.installs_for(root, named)


def centre_on(pw, ph, px, py, w, h, sw, sh):
    """Where a w x h dialogue goes over a pw x ph parent at (px, py): clamped onto the screen."""
    if pw > w and ph > h:
        x, y = px + (pw - w) // 2, py + (ph - h) // 3
    else:
        x, y = (sw - w) // 2, (sh - h) // 3
    return max(0, min(x, max(0, sw - w))), max(0, min(y, max(0, sh - h)))


def place_over(win, parent, log=None):
    """Put a dialogue over the window that raised it, then show it: never mapped unplaced."""
    try:
        if parent is not None:
            parent.update_idletasks()
        win.update_idletasks()
        w = max(win.winfo_width(), win.winfo_reqwidth())
        h = max(win.winfo_height(), win.winfo_reqheight())
        pw = ph = px = py = 0
        if parent is not None and parent.winfo_viewable():
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
        x, y = centre_on(pw, ph, px, py, w, h, win.winfo_screenwidth(), win.winfo_screenheight())
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        win.deiconify()
        win.lift()
    except Exception as exc:                                    # noqa: BLE001
        if log:
            log("could not place the dialogue (%s); it will open where the window manager puts it" % exc)
        try:
            win.deiconify()
        except Exception:                                       # noqa: BLE001
            pass


def mark_image(size, teal="#0d5c6b", rust="#c2603a"):
    """The HOPE Labs mark as a Tk image: four tiles, the last one rust, on a clear ground.

    Drawn pixel by pixel rather than loaded, so the window's icon cannot go missing from a bundle
    and needs neither Pillow nor an image file. The proportions are docs/brand/make_icon.py's, so
    the title bar, the taskbar and the .exe all show the same mark.
    """
    img = tk.PhotoImage(width=size, height=size)
    tile = max(3, round(size * 0.30))
    gap = max(1, round(size * 0.08))
    left = (size - (2 * tile + gap)) // 2
    radius = tile * 0.26 if size >= 24 else 0
    for row in (0, 1):
        for col in (0, 1):
            x0 = left + col * (tile + gap)
            y0 = left + row * (tile + gap)
            img.put(rust if (row, col) == (1, 1) else teal, to=(x0, y0, x0 + tile, y0 + tile))
            if not radius:
                continue
            # round each tile's corners: clear the pixels outside the corner's quarter circle
            r = int(radius)
            for cx, cy, sx, sy in ((x0 + r, y0 + r, -1, -1), (x0 + tile - 1 - r, y0 + r, 1, -1),
                                   (x0 + r, y0 + tile - 1 - r, -1, 1),
                                   (x0 + tile - 1 - r, y0 + tile - 1 - r, 1, 1)):
                for dx in range(r + 1):
                    for dy in range(r + 1):
                        if dx * dx + dy * dy > r * r:
                            img.transparency_set(cx + sx * dx, cy + sy * dy, True)
    return img


def set_window_icon(root):
    """The mark on every window this launcher opens: the title bar, the taskbar, and the Duo
    and error dialogues, which Tk otherwise gives its own feather. Returns the images, which the
    caller keeps: Tk drops an image the moment Python forgets it."""
    try:
        images = [mark_image(s) for s in (256, 64, 32, 16)]
        root.iconphoto(True, *images)
        return images
    except Exception:                                           # noqa: BLE001
        return []                                               # a missing icon is never fatal


class Placeholder:
    """Grey example text over an empty box, there until something is typed.

    Tk has no placeholder of its own, and writing the example into the box would make it the
    value: it would be saved, sent to the cluster as the account name and have to be deleted
    before typing. A label laid over the box keeps it out of the variable altogether. Clicking the
    label puts the cursor in the box, so it never gets in the way.
    """

    def __init__(self, entry, var, text=""):
        self.entry, self.var = entry, var
        style = ttk.Style()
        ground = style.lookup("TEntry", "fieldbackground") or "white"
        self.label = tk.Label(entry, text=text, fg="#8a9a9c", bg=ground, font="TkTextFont",
                              anchor="w", cursor="xterm", padx=0, pady=0, bd=0)
        self.label.bind("<Button-1>", lambda _e: (entry.focus_set(), entry.icursor("end")))
        var.trace_add("write", lambda *_a: self.refresh())
        self.refresh()

    def set(self, text):
        self.label.configure(text=text)
        self.refresh()

    def refresh(self):
        if self.var.get() or not self.label.cget("text"):
            self.label.place_forget()
        else:
            # inside the box's own left padding, centred on its line
            self.label.place(x=5, rely=0.5, anchor="w")


class Banner(tk.Canvas):
    """The header: the mark, the name and the build, drawn rather than pasted."""

    H = 96

    def __init__(self, parent, name, tagline, build):
        super().__init__(parent, height=self.H, highlightthickness=0, bd=0, bg=BANNER["deep"])
        self.lines = (name, tagline, build)
        import tkinter.font as tkfont
        fam = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self.fonts = (tkfont.Font(family=fam, size=17, weight="bold"),
                      tkfont.Font(family=fam, size=10),
                      tkfont.Font(family=fam, size=9))
        self.bind("<Configure>", self._draw)

    @staticmethod
    def _mix(a, b, t):
        a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))

    def _draw(self, _e=None):
        self.delete("all")
        w, h = max(self.winfo_width(), 1), self.H
        for x in range(0, w, 4):
            self.create_rectangle(x, 0, x + 4, h, width=0,
                                  fill=self._mix(BANNER["deep"], BANNER["deeper"], x / float(w)))
        # the mark: four tiles as a launcher's grid, one of them the odd one out
        cx, cy, tile, gap = 46, h / 2.0, 20, 6
        for row in (0, 1):
            for col in (0, 1):
                x = cx - tile - gap / 2.0 + col * (tile + gap)
                y = cy - tile - gap / 2.0 + row * (tile + gap)
                self.create_rectangle(x, y, x + tile, y + tile, width=0,
                                      fill=BANNER["warm"] if (row, col) == (1, 1) else BANNER["teal"])
        colours = (BANNER["paper"], BANNER["muted"], BANNER["faint"])
        y = (h - sum(f.metrics("linespace") for f in self.fonts) - 6) / 2.0
        for text, font, col in zip(self.lines, self.fonts, colours):
            self.create_text(96, y, text=text, font=font, fill=col, anchor="nw")
            y += font.metrics("linespace") + 3


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.lines = []
        self.t = None
        self.hub = None
        self.httpd = None
        self.url = ""
        cfg = load()

        root.title(APP)
        root.minsize(660, 520)
        self._icons = set_window_icon(root)
        pad = dict(padx=10, pady=5)

        build = os.environ.get("HOPELABS_BUILD") or __version__
        Banner(root, APP, TAGLINE, "build %s" % build).pack(fill="x", pady=(0, 6))

        form = ttk.LabelFrame(root, text="Sign in once, for every tool")
        form.pack(fill="x", **pad)

        self.site = tk.StringVar(value=cfg.get("site", clusters.sites()[0]))
        ttk.Label(form, text="Cluster").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        box = ttk.Combobox(form, textvariable=self.site, values=clusters.sites(), state="readonly")
        box.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        box.bind("<<ComboboxSelected>>", lambda *_a: self._apply_site())

        self._rows = {}
        self.host = self._row(form, 1, "host", "Address", cfg.get("host", ""))
        self.user_label = ttk.Label(form, text="NetID")
        self.user_label.grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.user = tk.StringVar(value=cfg.get("user", ""))
        user_box = ttk.Entry(form, textvariable=self.user)
        user_box.grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        self.user_hint = Placeholder(user_box, self.user)
        self.jump = self._row(form, 3, "jump", "Gateway (ACES)", cfg.get("jump", ""))
        self.key = self._row(form, 4, "key", "Key with certificate (ACES)", cfg.get("key", ""))
        self.note = ttk.Label(form, text="", foreground="#5f7070", wraplength=560, justify="left")
        self.note.grid(row=5, column=1, sticky="w", padx=6, pady=(0, 6))
        # wrap to the column as it is, not a fixed width: the ACES rows have longer names, which
        # narrow the column, and a fixed wrap then runs the note off the window's edge
        form.bind("<Configure>", lambda e: self.note.configure(
            wraplength=max(200, e.width - self.note.winfo_x() - 18)), add="+")
        self._touched = set()
        for name in ("host", "jump", "key"):
            getattr(self, name).trace_add("write", self._mark(name))
        self._apply_site(first=True, cfg=cfg)
        form.columnconfigure(1, weight=1)

        bar = ttk.Frame(root)
        bar.pack(fill="x", **pad)
        self.go = ttk.Button(bar, text="Sign in and open HOPE Labs", command=self.start)
        self.go.pack(side="left")
        self.open_btn = ttk.Button(bar, text="Open the page", command=self.open_browser, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(bar, text="Sign out", command=self.close, state="disabled")
        self.stop_btn.pack(side="left")
        self.status = ttk.Label(bar, text="not connected", foreground="#5f7070")
        self.status.pack(side="right")

        logbox = ttk.LabelFrame(root, text="What is happening")
        logbox.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logbox, height=13, wrap="word", font=("TkFixedFont", 10))
        self.log.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(logbox, command=self.log.yview)
        sb.pack(fill="y", side="right")
        self.log.configure(yscrollcommand=sb.set, state="disabled")

        self.verbose = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="Show technical details", variable=self.verbose,
                        command=self._replay).pack(anchor="w", padx=12)
        # On by default: the lab's install is where the page is kept up to date, and a person who
        # was given this programme once should not need to be given it again for every change to
        # it. Off serves the page this launcher was built with, which is what to do if a change on
        # the cluster ever makes trouble.
        self.cluster_page = tk.BooleanVar(value=True)
        ttk.Checkbutton(root, text="Take the page from the cluster, so it stays current",
                        variable=self.cluster_page).pack(anchor="w", padx=12)
        ttk.Label(root, text=credit(), foreground="#5f7070", wraplength=660,
                  justify="left").pack(anchor="w", padx=10, pady=(4, 8))

        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.pump()

    # ---- the form -------------------------------------------------------
    def _mark(self, name):
        def cb(*_a):
            if not getattr(self, "_filling", False):
                self._touched.add(name)
        return cb

    def _set(self, name, value):
        if name in self._touched:
            return
        self._filling = True
        try:
            getattr(self, name).set(value)
        finally:
            self._filling = False

    def _apply_site(self, first=False, cfg=None):
        d = clusters.defaults_for(self.site.get(), self.user.get().strip())
        cfg = cfg or {}
        if first and cfg.get("schema") == SCHEMA:
            for name in ("host", "jump", "key"):
                if cfg.get(name):
                    self._touched.add(name)
        self._set("host", d["host"])
        self._set("jump", d["jump"])
        self._set("key", d["key"])
        self.user_label.configure(text=d["label"])
        self.user_hint.set(d.get("placeholder", ""))
        # The gateway and key rows are ACES's, filled in, and Other's, empty and optional; a
        # cluster that needs neither shows neither.
        for name in ("jump", "key"):
            label, box = self._rows[name]
            label.configure(text=d[name + "_label"])
            for widget in (label, box):
                if d["jump"] or d["optional"]:
                    widget.grid()
                else:
                    widget.grid_remove()
        # the example in the account box already says what goes there
        self.note.configure(text=d["note"] if d.get("placeholder") else
                            ("%s  %s" % (d["note"], d["hint"])).strip())

    def _row(self, parent, r, key, label, value):
        name = ttk.Label(parent, text=label)
        name.grid(row=r, column=0, sticky="e", padx=6, pady=4)
        var = tk.StringVar(value=value)
        box = ttk.Entry(parent, textvariable=var)
        box.grid(row=r, column=1, sticky="ew", padx=6, pady=4)
        self._rows[key] = (name, box)
        return var

    # ---- the log --------------------------------------------------------
    def say(self, text):
        self.q.put(("log", str(text)))

    def detail(self, text):
        for ln in str(text).splitlines():
            if ln.strip():
                self.q.put(("detail", ln.rstrip()))

    def _replay(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        for kind, line in self.lines:
            if kind == "detail" and not self.verbose.get():
                continue
            self.log.insert("end", ("    " + line if kind == "detail" else line) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind in ("log", "detail"):
                    self.lines.append((kind, payload.rstrip()))
                    if kind == "detail" and not self.verbose.get():
                        continue
                    self.log.configure(state="normal")
                    self.log.insert("end", ("    " if kind == "detail" else "") + payload.rstrip() + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "status":
                    self.status.configure(text=payload)
                elif kind == "ready":
                    self.open_btn.configure(state="normal")
                    self.stop_btn.configure(state="normal")
                    self.go.configure(state="normal")
                    self.open_browser()
                elif kind == "details":
                    self.verbose.set(True)
                    self._replay()
                elif kind == "failed":
                    self.go.configure(state="normal")
                    messagebox.showerror(APP, payload)
        except queue.Empty:
            pass
        self.root.after(120, self.pump)

    # ---- the Duo dialogue, on the main thread ---------------------------
    def ask(self, title, instructions, prompts):
        done = threading.Event()
        answers = []

        def build():
            win = tk.Toplevel(self.root)
            win.withdraw()
            win.title(title or "%s - sign in" % APP)
            win.transient(self.root)
            if instructions and instructions.strip():
                ttk.Label(win, text=instructions.strip(), wraplength=460,
                          justify="left").pack(anchor="w", padx=12, pady=(12, 4))
            entries = []
            for text, echo in prompts:
                ttk.Label(win, text=text.strip() or "Response", wraplength=460,
                          justify="left").pack(anchor="w", padx=12, pady=(8, 2))
                e = ttk.Entry(win, width=44, show="" if echo else "•")
                e.pack(fill="x", padx=12)
                entries.append(e)
            if entries:
                entries[0].focus_set()
            row = ttk.Frame(win)
            row.pack(fill="x", padx=12, pady=12)

            def ok(*_):
                answers.extend(e.get() for e in entries)
                win.destroy()
                done.set()

            def cancel():
                win.destroy()
                done.set()

            ttk.Button(row, text="Continue", command=ok).pack(side="right")
            ttk.Button(row, text="Cancel", command=cancel).pack(side="right", padx=6)
            win.bind("<Return>", ok)
            win.bind("<Escape>", lambda *_a: cancel())
            win.protocol("WM_DELETE_WINDOW", cancel)
            win.resizable(False, False)
            place_over(win, self.root, log=self.say)
            win.grab_set()
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
            if entries:
                entries[0].focus_force()

        self.root.after(0, build)
        done.wait()
        return answers or [""] * len(prompts)

    # ---- the work -------------------------------------------------------
    def start(self):
        host, typed = self.host.get().strip(), self.user.get().strip()
        user = clusters.clean_user(typed)
        if user != typed:
            # put the cleaned name back in the box, so what is saved is what was used
            self.user.set(user)
            self.say("Using %s: the part before the @ is the account name." % user)
        if not host or not user:
            messagebox.showwarning(APP, "The cluster and your account are both needed.")
            return
        save({"site": self.site.get(), "host": host, "user": user,
              "jump": self.jump.get(), "key": self.key.get()})
        self.go.configure(state="disabled")
        self.q.put(("status", "connecting"))
        threading.Thread(target=self.work, args=(host, user), daemon=True).start()

    def work(self, host, user):
        try:
            jump = clusters.parse_jump(self.jump.get())
            key = self.key.get().strip() or None
            if jump:
                self.say("Connecting to %s as %s through the gateway %s:%d..." % (host, user, jump[0], jump[1]))
            else:
                self.say("Connecting to %s as %s..." % (host, user))
                self.say("Approve the Duo prompt when it reaches your phone. It is asked once, "
                         "however many tools you open.")
            self.t = tunnel.connect(host, user, self.ask, jump=jump, key=key, log=self.detail)
            node = tunnel.hostname(self.t)
            self.say("Signed in on %s." % node)
            cfg = load()
            named = cfg.get("installs") if isinstance(cfg.get("installs"), dict) else {}
            labs = labs_here(cfg, host)
            for key in sorted(named):
                self.say("  %s: %s" % (key, named[key]))
            self.hub = hubmod.Hub(self.t, user=user, host=node, say=self.say, installs=named,
                                  labs=labs, address=host)
            self.hub.labs_found = self.hub.check_labs()
            if not self.hub.labs_found:
                # A cluster new to HOPE Labs, or one where it is somewhere this computer was not
                # told. The page says so and offers both: set it up, or name where it is.
                self.say("HOPE Labs is not set up on this cluster yet (looked for %s). The page "
                         "offers to set it up." % self.hub.labs)
            elif self.hub.labs != hubmod.FLOW_INSTALL:
                self.say("  HOPE Labs on this cluster: %s" % self.hub.labs)
            # The page comes from the lab's install where it can, so a change to the page reaches
            # everybody the next time they sign in rather than when they next download this.
            if self.cluster_page.get() and self.hub.labs_found:
                self.hub.reload_from_cluster()
            want = clusters.suggested_port(user)
            self.httpd, self.url = hubmod.serve(self.hub, port=want)
            got = self.httpd.server_address[1]
            if got != want:
                self.say("  Port %d was busy, so the page is on %d. Chrome may ask about pop-ups "
                         "once more for this address." % (want, got))
            self.say("HOPE Labs is open. The tools start when you pick one.")
            self.say("  If the page does not open, paste this in: %s" % self.url)
            self.say("")
            self.say(ACK)
            self.q.put(("status", "signed in"))
            self.q.put(("ready", ""))
        except Exception as exc:                                # noqa: BLE001
            self.say("")
            self.say("Could not connect: %s" % exc)
            self.say("The technical detail is below - send it with a report.")
            self.q.put(("details", ""))
            self.q.put(("status", "not connected"))
            self.q.put(("failed", str(exc)))

    def open_browser(self):
        if self.url:
            webbrowser.open(self.url)

    def close(self):
        if self.hub:
            self.hub.stop_all()
            self.hub = None
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:                                   # noqa: BLE001
                pass
            self.httpd = None
        if self.t:
            try:
                self.t.close()
            except Exception:                                   # noqa: BLE001
                pass
            self.t = None
        self.url = ""
        self.open_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.q.put(("status", "not connected"))
        self.say("Signed out. The tools' pages are closed; anything already queued carries on.")

    def quit(self):
        self.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:                                           # noqa: BLE001
        pass
    App(root)
    root.mainloop()
