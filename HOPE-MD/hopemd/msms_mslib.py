"""MSMS's command line, answered by the mslib library of the ADCP 1.1 suite.

By Aadhil Haq

The ADCP 1.1 suite has no msms program, but it carries MSMS itself as the
Python library mslib, the same code the program is built from. This script
is run with the suite's own Python (3.7, where mslib is compiled) and takes
the arguments surface.run_msms gives the program:

    python msms_mslib.py -if spheres.xyzr -of surface -probe_radius 1.5 -density 1.0 -no_area

and writes what the program writes, surface.vert and surface.face for the
outer component. Run with no arguments it prints its version line, as the
program does. It imports nothing from this package, because it runs under
the suite's interpreter and not this package's.
"""
import sys


def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv):
    if not argv:
        print("MSMSLIB 1.4.4 (mslib, the MSMS library of the ADCP 1.1 suite)")
        return 0
    inp, out = _arg(argv, "-if"), _arg(argv, "-of")
    if not inp or not out:
        print("usage: msms_mslib.py -if spheres.xyzr -of name "
              "[-probe_radius 1.5] [-density 1.0]")
        return 2
    probe = float(_arg(argv, "-probe_radius", 1.5))
    density = float(_arg(argv, "-density", 1.0))
    from mslib import MSMS
    coords, radii = [], []
    for line in open(inp):
        f = line.split()
        if len(f) < 4:
            continue
        coords.append([float(f[0]), float(f[1]), float(f[2])])
        radii.append(float(f[3]))
    if not coords:
        print("MSMS: no spheres in %s" % inp)
        return 1
    m = MSMS(coords=coords, radii=radii)
    m.compute(probe_radius=probe, density=density)
    # The outer component only, under the names the program uses for it.
    m.write_triangulation(out, no_header=0, component=0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
