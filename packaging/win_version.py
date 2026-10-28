"""Fill the Windows version resource in from the license.

By Aadhil Haq

PyInstaller's --version-file writes the .exe's Details tab. The copyright belongs there - the
launcher reserves all rights - but typing it here would make a third copy of a line that LICENSE
already states and that hope_labs reads from it. So the tracked file is a template with
__COPYRIGHT__ in it, this writes the filled copy beside it, and the build points at that.

    python packaging/win_version.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from hope_labs import COPYRIGHT                                        # noqa: E402

TEMPLATE = os.path.join(HERE, "win_version.txt")
BUILT = os.path.join(HERE, "win_version.built.txt")


def main():
    with open(TEMPLATE, encoding="utf-8") as fh:
        text = fh.read()
    if "__COPYRIGHT__" not in text:
        sys.exit("%s has no __COPYRIGHT__ to fill in" % TEMPLATE)
    # A single quote would close the string literal the resource is written as. The line has none
    # and is not meant to, so this stops rather than shipping a file PyInstaller cannot parse.
    if "'" in COPYRIGHT:
        sys.exit("the copyright line has a quote in it, which the resource file cannot carry")
    with open(BUILT, "w", encoding="utf-8") as fh:
        fh.write(text.replace("__COPYRIGHT__", COPYRIGHT))
    print("wrote %s" % BUILT)
    print("  LegalCopyright: %s" % COPYRIGHT)


if __name__ == "__main__":
    main()
