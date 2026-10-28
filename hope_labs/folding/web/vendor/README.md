# Bundled viewers

The folding pages draw a finished structure with two viewers, bundled here so that they work
with no content delivery network. That is not a preference: the page is served on a login node
and reached through a forwarded port, so a browser on it can fetch from the page and from
nowhere else. These files are copies of HOPE-MD's, taken from `hopemd/web/vendor` unchanged, and
the pages load them from `/static/vendor/` exactly as HOPE-MD does. Each
keeps its own licence, and the licence texts of the libraries inside each minified bundle sit
beside it under the name the bundle's first line gives.

| File | What it is | Version | Licence |
| --- | --- | --- | --- |
| `3Dmol-min.js` | 3Dmol.js, University of Pittsburgh and contributors (3dmol.org) | 2.5.5 | BSD 3-Clause, with the GLmol, Three.js and jQuery notices: `LICENSE-3Dmol.txt`; bundled libraries: `3Dmol-min.js.LICENSE.txt` |
| `molstar.js`, `molstar.css` | The Mol* viewer, Mol* contributors (molstar.org) | 4.9.0 | MIT: `LICENSE-molstar.txt`; bundled libraries: `molstar.js.LICENSE.txt` |
| `drag.js` | Label dragging in the viewer, the HOPE Lab's own | | Part of HOPE-MD |

Update these by copying them from HOPE-MD again rather than by fetching a release: the versions
in the table are the ones the lab has tested, and the licence texts beside them belong to those
versions.
