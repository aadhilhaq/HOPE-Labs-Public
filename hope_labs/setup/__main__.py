"""Set HOPE Labs up from a terminal, the same way the Set up page does it.

By Aadhil Haq

    python -m hope_labs.setup probe [--save]       what this cluster has; --save writes cluster.json
    python -m hope_labs.setup status               every tool, and how far its install has got
    python -m hope_labs.setup install boltz2       install a tool here, then check it with a job
    python -m hope_labs.setup check boltz2         only the check: fold something on the queue
    python -m hope_labs.setup stop boltz2          stop an install at its next step
    python -m hope_labs.setup log boltz2           what the install has printed

Run them on a login node, from the HOPE Labs checkout. --root names the HOPE Labs folder when it
is not the one holding this checkout. install and check run in this terminal until they finish;
with --detach they carry on in the background, which is what the page does.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import labs_root, profile_path
from . import engine
from . import profile as profilemod
from .recipes import RECIPES


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m hope_labs.setup",
                                     description="Set HOPE Labs up on this cluster.")
    parser.add_argument("action", choices=("probe", "status", "install", "check", "stop", "log"))
    parser.add_argument("tool", nargs="?", default="")
    parser.add_argument("--root", default="", help="the HOPE Labs folder (default: the one "
                                                   "holding this checkout)")
    parser.add_argument("--account", default="", help="the Slurm account for the check jobs")
    parser.add_argument("--save", action="store_true", help="probe: write cluster.json")
    parser.add_argument("--detach", action="store_true",
                        help="install/check: carry on in the background")
    parser.add_argument("--json", action="store_true", help="answer in JSON")
    args = parser.parse_args(argv)
    root = os.path.abspath(os.path.expanduser(args.root)) if args.root else labs_root()

    if args.action == "probe":
        doc = profilemod.probe(account=args.account,
                               say=lambda *a: print("  ", *a, file=sys.stderr))
        old = profilemod.load(root)
        if old:
            # A probe is asked again to see what changed, not to forget what was confirmed.
            for key in ("name", "address"):
                doc[key] = old.get(key) or doc.get(key, "")
            for key, value in (old.get("use") or {}).items():
                if value:
                    doc["use"][key] = value
            if old.get("gpu_check"):
                doc["gpu_check"] = old["gpu_check"]
            doc["network"]["proxy"] = (old.get("network") or {}).get("proxy", "")
        if args.save:
            profilemod.save(doc, root)
            print("written: %s" % profile_path(root), file=sys.stderr)
        print(json.dumps(doc, indent=2))
        return 0

    if args.action == "status":
        rows = [engine.status(root, recipe) for recipe in RECIPES.values()]
        if args.json:
            print(json.dumps({"root": root, "tools": rows}, indent=2))
        else:
            for row in rows:
                print("%-10s %-11s %s" % (row["tool"], row["state"], row.get("detail", "")))
        return 0

    recipe = RECIPES.get(args.tool)
    if recipe is None:
        print("which tool? %s" % ", ".join(RECIPES), file=sys.stderr)
        return 2

    if args.action == "log":
        text, _ = engine.log_tail(root, recipe.key)
        sys.stdout.write(text)
        return 0

    if args.action == "stop":
        engine.stop(root, recipe.key)
        print("asked %s to stop" % recipe.name)
        return 0

    if args.detach:
        pid = engine.start(root, recipe.key, args.action, args.account)
        print("%s: %s in the background (process %d). Its log: %s"
              % (recipe.name, args.action, pid, engine.log_path(root, recipe.key)))
        return 0

    # In the foreground, the log is followed as it is written, so a terminal sees what the page
    # would show.
    path = engine.log_path(root, recipe.key)
    start = os.path.getsize(path) if os.path.exists(path) else 0
    import threading
    finished = threading.Event()

    def follow():
        offset = start
        while not finished.is_set():
            text, offset = engine.log_tail(root, recipe.key, offset)
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
            time.sleep(1)
        text, offset = engine.log_tail(root, recipe.key, offset)
        sys.stdout.write(text)

    shown = threading.Thread(target=follow, daemon=True)
    if sys.stdout.isatty():
        shown.start()
    try:
        state = engine.run(root, recipe, action=args.action, account=args.account)
    except engine.Failed as why:
        print("%s: %s" % (recipe.name, why), file=sys.stderr)
        return 1
    finally:
        finished.set()
        if shown.is_alive():
            shown.join(5)
    print("%s: %s%s" % (recipe.name, state.get("state"),
                        (" - " + state.get("problem")) if state.get("problem") else ""))
    return 0 if state.get("state") == engine.READY else 1


if __name__ == "__main__":
    sys.exit(main())
