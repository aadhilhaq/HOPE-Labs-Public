"""Transmembrane segments of the protein being set up, and what to do about them.

Where they come from, in order:
  1. UniProt, for an RCSB entry: PDBe's SIFTS mapping says which UniProt entry each chain is
     and how its numbering maps onto the chain's author numbering; the UniProt entry carries
     the curated "Transmembrane" features (helical or beta stranded), the "Intramembrane"
     ones, the topology (which side is cytoplasmic), and the Membrane keyword. NCBI entries
     cross-reference the same UniProt records, so this is the annotation NCBI would give.
  2. UniProt, for an AlphaFold job whose name carries an accession (P0A910): the model is
     full length, so UniProt numbering is the model's.
  3. A hydrophobicity scan of the sequence (Kyte-Doolittle, 19-residue window) when nothing
     maps: it finds transmembrane helices, not beta barrels, and says it is a prediction.

A structure is a membrane protein when segments land on the residues it holds, not when its
UniProt entry carries the Membrane keyword: the keyword is on whole entries, a polyprotein's
included, and a soluble domain cut from a membrane protein wants no bilayer.

The page shows the result as a check ("membrane protein: 8 transmembrane beta strands,
UniProt P0A910"), colors those residues, warns when such a protein is set up in a water
box, and can orient the structure on the segments so the bilayer is built where they are
(membrane.orientation = "annotation"). Stdlib only; every network call has a timeout and
every failure is a note, never an error: an import must not depend on a web service.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request

SIFTS_URL = "https://www.ebi.ac.uk/pdbe/api/mappings/uniprot/{pdb}"
UNIPROT_URL = ("https://rest.uniprot.org/uniprotkb/{acc}.json?fields=accession,id,protein_name,organism_name,"
               "keyword,ft_transmem,ft_intramem,ft_topo_dom,sequence,length")
TIMEOUT = 15
ACCESSION = re.compile(r"(?<![A-Za-z0-9])([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?![A-Za-z0-9])")   # an underscore is a separator here

KD = {"A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5, "G": -0.4, "H": -3.2, "I": 4.5,
      "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8, "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2}
THREE_TO_ONE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H",
                "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
                "TYR": "Y", "VAL": "V", "HID": "H", "HIE": "H", "HIP": "H", "CYX": "C", "ASH": "D", "GLH": "E", "LYN": "K", "MSE": "M"}
KIND = {"Helical": "helix", "Beta stranded": "beta", "Discontinuously helical": "helix"}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "hopemd"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- the sources
def parse_sifts(payload: dict, pdb_id: str) -> dict:
    """{accession: {"identifier": ..., "mappings": [{"chain", "unp_start", "unp_end", "auth_start", "auth_end"}]}}"""
    out: dict = {}
    for acc, v in (payload.get(pdb_id.lower(), {}).get("UniProt") or {}).items():
        maps = []
        for m in v.get("mappings", []):
            try:
                us, ue = int(m["unp_start"]), int(m["unp_end"])
                a0, a1 = m["start"].get("author_residue_number"), m["end"].get("author_residue_number")
            except (KeyError, TypeError, ValueError):
                continue
            # an unresolved terminus has no author number at that end: the offset comes from the other end
            if a0 is None and a1 is None:
                continue
            if a0 is None:
                a0 = int(a1) - (ue - us)
            if a1 is None:
                a1 = int(a0) + (ue - us)
            maps.append({"chain": m["chain_id"], "unp_start": us, "unp_end": ue, "auth_start": int(a0), "auth_end": int(a1)})
        out[acc] = {"identifier": v.get("identifier") or v.get("name") or acc, "mappings": maps}
    return out


def parse_uniprot(payload: dict) -> dict:
    """The parts of a UniProt entry this needs: segments in UniProt numbering."""
    segs, topo = [], []
    for f in payload.get("features", []):
        loc = f.get("location", {})
        try:
            a, b = int(loc["start"]["value"]), int(loc["end"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        t = f.get("type")
        if t == "Transmembrane":
            segs.append({"start": a, "end": b, "kind": KIND.get(f.get("description", ""), "helix"), "note": f.get("description", "")})
        elif t == "Intramembrane":
            segs.append({"start": a, "end": b, "kind": "intramembrane", "note": f.get("description", "")})
        elif t == "Topological domain":
            topo.append({"start": a, "end": b, "side": f.get("description", "")})
    kw = [k.get("name", "") for k in payload.get("keywords", [])]
    pd = payload.get("proteinDescription", {})
    name = ((pd.get("recommendedName") or {}).get("fullName") or {}).get("value") or \
           ((pd.get("submissionNames") or [{}])[0].get("fullName") or {}).get("value") or ""
    return {"accession": payload.get("primaryAccession", ""), "identifier": payload.get("uniProtkbId", ""), "protein": name,
            "organism": (payload.get("organism") or {}).get("scientificName", ""), "keywords": kw,
            "membrane": bool(segs) or "Transmembrane" in kw or "Membrane" in kw,
            "segments": segs, "topology": topo, "sequence": (payload.get("sequence") or {}).get("value", ""),
            "length": (payload.get("sequence") or {}).get("length")}


def map_to_author(segments: list[dict], mappings: list[dict]) -> tuple[list[dict], int]:
    """Segments in the chain's author numbering, clipped to what the structure has; the count
    of segments that fall outside it (a signal peptide's neighbor, an unresolved terminus)."""
    out, outside = [], 0
    for s in segments:
        placed = False
        for m in mappings:
            lo, hi = max(s["start"], m["unp_start"]), min(s["end"], m["unp_end"])
            if lo > hi:
                continue
            off = m["auth_start"] - m["unp_start"]
            out.append({**s, "start": lo + off, "end": hi + off, "unp_start": s["start"], "unp_end": s["end"]})
            placed = True
        if not placed:
            outside += 1
    return out, outside


def summary_of(chains: dict, method: str, source: str) -> str:
    n_h = sum(1 for c in chains.values() for s in c["segments"] if s["kind"] == "helix")
    n_b = sum(1 for c in chains.values() for s in c["segments"] if s["kind"] == "beta")
    n_i = sum(1 for c in chains.values() for s in c["segments"] if s["kind"] == "intramembrane")
    outside = sum(c.get("outside", 0) for c in chains.values())
    parts = []
    if n_h: parts.append(f"{n_h} transmembrane helices" if n_h != 1 else "1 transmembrane helix")
    if n_b: parts.append(f"{n_b} transmembrane β-strands" if n_b != 1 else "1 transmembrane β-strand")
    if n_i: parts.append(f"{n_i} intramembrane segment{'s' if n_i != 1 else ''}")
    what = ", ".join(parts) if parts else "no transmembrane segments" + (
        f" in the structure ({outside} annotated outside it)" if outside else "")
    return f"{what} ({source})" + (" · predicted from hydrophobicity, not an annotation" if method == "predicted" else "")


def annotate_pdb_entry(pdb_id: str, chain_ids: list[str] | None = None, fetch=None) -> dict:
    """UniProt's transmembrane annotation for every chain of an RCSB entry, in author numbering. A chain is
    a membrane protein when segments land on the residues it holds: a soluble domain cut from a membrane
    protein has none, and neither has a domain of a polyprotein whose entry carries the Membrane keyword for
    another of its chains (HIV-1 integrase in Gag-Pol)."""
    fetch = fetch or _get_json
    sifts = parse_sifts(fetch(SIFTS_URL.format(pdb=pdb_id.lower())), pdb_id)
    chains: dict = {}
    sources = []
    for acc, v in sifts.items():
        entry = parse_uniprot(fetch(UNIPROT_URL.format(acc=acc)))
        for m in v["mappings"]:
            if chain_ids and m["chain"] not in chain_ids:
                continue
            segs, outside = map_to_author(entry["segments"], [m])
            c = chains.setdefault(m["chain"], {"accession": acc, "identifier": v["identifier"], "protein": entry["protein"],
                                               "organism": entry["organism"], "membrane": False, "segments": [], "topology": [], "outside": 0})
            c["segments"] += segs
            c["topology"] += map_to_author(entry["topology"], [m])[0]
            c["outside"] += outside
        if entry["membrane"] or entry["segments"]:
            sources.append(f"UniProt {acc}, {entry['protein']}" if entry["protein"] else f"UniProt {acc}")
    for c in chains.values():
        c["segments"].sort(key=lambda s: s["start"])
        c["membrane"] = bool(c["segments"])
    membrane = any(c["membrane"] for c in chains.values())
    source = "; ".join(dict.fromkeys(sources)) or ("UniProt " + ", ".join(sorted({c["accession"] for c in chains.values()})) if chains else "no UniProt mapping")
    return {"method": "annotation" if chains else "none", "membrane": membrane, "chains": chains,
            "source": source, "summary": summary_of(chains, "annotation", source) if chains else "no UniProt mapping for this entry"}


def annotate_accession(acc: str, chain_ids: list[str], fetch=None) -> dict:
    """An entry by accession (an AlphaFold job named after it): the model is full length, so
    UniProt numbering is the model's; the same segments go on every chain given."""
    fetch = fetch or _get_json
    entry = parse_uniprot(fetch(UNIPROT_URL.format(acc=acc)))
    chains = {c: {"accession": acc, "identifier": entry["identifier"], "protein": entry["protein"], "organism": entry["organism"],
                  "membrane": bool(entry["segments"]), "segments": list(entry["segments"]), "topology": list(entry["topology"]), "outside": 0}
              for c in chain_ids}
    source = f"UniProt {acc}" + (f", {entry['protein']}" if entry["protein"] else "") + "; the model is taken as full length"
    return {"method": "annotation", "membrane": bool(entry["segments"]) and bool(chains), "chains": chains, "source": source,
            "summary": summary_of(chains, "annotation", source)}


# ---------------------------------------------------------------- the fallback: hydrophobicity
def predict_tm_helices(seq: str, window: int = 19, threshold: float = 1.6, numbering: list[int] | None = None) -> list[dict]:
    """Kyte-Doolittle windows over the sequence: a run of windows averaging over the threshold
    is a candidate helix (its center residues), merged when they touch. Finds helices; a beta
    barrel's alternating strands stay below the threshold. Positions are 1-based, or the
    numbering given (the author numbers of the residues)."""
    seq = seq.upper()
    n = len(seq)
    if n < window:
        return []
    num = numbering or list(range(1, n + 1))
    half = window // 2
    hot = []
    for i in range(half, n - half):
        w = seq[i - half:i + half + 1]
        score = sum(KD.get(a, -0.5) for a in w) / window
        if score >= threshold:
            hot.append((i, score))
    segs: list[dict] = []
    for i, score in hot:
        a, b = i - half, i + half
        if segs and a <= segs[-1]["_b"] + 1:
            segs[-1]["_b"] = b; segs[-1]["score"] = max(segs[-1]["score"], score)
        else:
            segs.append({"_a": a, "_b": b, "score": score})
    out = []
    for s in segs:
        if s["_b"] - s["_a"] + 1 >= 17:
            out.append({"start": num[s["_a"]], "end": num[s["_b"]], "kind": "helix", "note": f"hydrophobicity {s['score']:.2f}", "score": round(s["score"], 2)})
    return out


def sequences_of(pdb_text: str) -> dict[str, tuple[str, list[int]]]:
    """{chain: (one-letter sequence, author residue numbers)} from the CA atoms of a PDB text."""
    out: dict[str, tuple[list[str], list[int]]] = {}
    seen = set()
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")) or line[12:16].strip() != "CA":
            continue
        chain = line[21].strip() or "A"
        key = (chain, line[22:27])
        if key in seen:
            continue
        seen.add(key)
        aa = THREE_TO_ONE.get(line[17:20].strip(), "X")
        try:
            num = int(line[22:26])
        except ValueError:
            continue
        out.setdefault(chain, ([], []))
        out[chain][0].append(aa); out[chain][1].append(num)
    return {c: ("".join(s), n) for c, (s, n) in out.items()}


def annotate_by_sequence(pdb_text: str) -> dict:
    chains = {}
    for c, (seq, num) in sequences_of(pdb_text).items():
        segs = predict_tm_helices(seq, numbering=num)
        chains[c] = {"accession": "", "identifier": "", "protein": "", "organism": "", "membrane": bool(segs), "segments": segs, "topology": [], "outside": 0}
    membrane = any(c["membrane"] for c in chains.values())
    source = "Kyte-Doolittle scan of the sequence"
    return {"method": "predicted", "membrane": membrane, "chains": chains, "source": source,
            "summary": summary_of(chains, "predicted", source) if chains else "no protein chain to scan"}


def accession_in_text(text: str) -> str:
    m = ACCESSION.search(text or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- orientation on the segments
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _norm(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def membrane_axis(pdb_text: str, chains: dict) -> tuple[tuple, tuple, int]:
    """(unit normal, center, segments used) from the CA atoms of the transmembrane segments:
    each segment's first-to-last CA direction, signs aligned to the first, averaged; the
    center is the mean CA of every segment. Helix bundles and barrels alike: the strands of a
    barrel run antiparallel and are aligned by sign before the average."""
    ca: dict[tuple[str, int], tuple] = {}
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and line[12:16].strip() == "CA":
            try:
                ca[(line[21].strip() or "A", int(line[22:26]))] = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            except ValueError:
                continue
    dirs, pts = [], []
    for c, info in chains.items():
        for s in info.get("segments", []):
            if s.get("kind") == "intramembrane":
                continue
            coords = [ca[(c, r)] for r in range(s["start"], s["end"] + 1) if (c, r) in ca]
            if len(coords) < 5:
                continue
            pts += coords
            dirs.append(_norm(_sub(coords[-1], coords[0])))
    if not dirs:
        return (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), 0
    ref = dirs[0]
    acc = [0.0, 0.0, 0.0]
    for d in dirs:
        sgn = 1.0 if _dot(d, ref) >= 0 else -1.0
        acc = [acc[i] + sgn * d[i] for i in range(3)]
    axis = _norm(tuple(acc))
    if axis[2] < 0:
        axis = (-axis[0], -axis[1], -axis[2])
    center = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    return axis, center, len(dirs)


def orient_by_segments(pdb_text: str, chains: dict) -> tuple[str, dict]:
    """The structure rotated so the segments' axis is z and translated so their center is the
    origin: the bilayer is then built where the segments are. Returns the new PDB text and
    what was done (tilt before, in degrees; segments used; center moved)."""
    axis, center, n = membrane_axis(pdb_text, chains)
    if n == 0:
        return pdb_text, {"segments": 0, "tilt_deg": None, "note": "no transmembrane segment with CA atoms: left as imported"}
    z = (0.0, 0.0, 1.0)
    v = _cross(axis, z)
    s = math.sqrt(_dot(v, v))
    c = _dot(axis, z)
    tilt = math.degrees(math.atan2(s, c))
    if s < 1e-9:
        R = [[1, 0, 0], [0, 1, 0], [0, 0, 1]] if c > 0 else [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
    else:
        k = (v[0] / s, v[1] / s, v[2] / s)
        K = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]]
        I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        KK = [[sum(K[i][m] * K[m][j] for m in range(3)) for j in range(3)] for i in range(3)]
        R = [[I[i][j] + s * K[i][j] + (1 - c) * KK[i][j] for j in range(3)] for i in range(3)]
    out = []
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and len(line) >= 54:
            try:
                p = (float(line[30:38]) - center[0], float(line[38:46]) - center[1], float(line[46:54]) - center[2])
            except ValueError:
                out.append(line); continue
            q = [sum(R[i][j] * p[j] for j in range(3)) for i in range(3)]
            line = line[:30] + f"{q[0]:8.3f}{q[1]:8.3f}{q[2]:8.3f}" + line[54:]
        out.append(line)
    return "\n".join(out) + ("\n" if pdb_text.endswith("\n") else ""), {"segments": n, "tilt_deg": round(tilt, 1), "centre_moved_A": [round(x, 1) for x in center],
                                                                       "note": f"oriented on {n} transmembrane segments: the axis was {tilt:.0f}° off z, the center {math.sqrt(_dot(center, center)):.0f} Å from the origin"}


# ---------------------------------------------------------------- BLAST, when nothing maps
BLAST_URL = "https://www.ebi.ac.uk/Tools/services/rest/ncbiblast"
BLAST_WAIT_S = 240


def _post(url: str, fields: dict) -> str:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Accept": "text/plain", "User-Agent": "hopemd"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode().strip()


def _get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"Accept": "text/plain, application/json", "User-Agent": "hopemd"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode()


def blast_swissprot(seq: str, email: str, wait_s: float = BLAST_WAIT_S, sleep=None) -> dict | None:
    """The best Swiss-Prot hit of a sequence, by EBI's NCBI BLAST service (blastp, e < 1e-5):
    {"accession", "identifier", "identity", "hsp": {qseq, hseq, query_from, hit_from}} or None.
    An asynchronous job: submitted, polled every few seconds, its JSON result read."""
    import time as _t
    sleep = sleep or _t.sleep
    job = _post(BLAST_URL + "/run", {"email": email, "program": "blastp", "stype": "protein", "database": "uniprotkb_swissprot",
                                     "sequence": seq, "exp": "1e-5", "alignments": "5", "scores": "5"})
    t0 = _t.time()
    while True:
        st = _get_text(f"{BLAST_URL}/status/{job}").strip()
        if st == "FINISHED":
            break
        if st in ("ERROR", "FAILURE", "NOT_FOUND"):
            raise RuntimeError(f"BLAST job {job}: {st}")
        if _t.time() - t0 > wait_s:
            raise TimeoutError(f"BLAST job {job} still {st} after {wait_s:.0f} s")
        sleep(5)
    return parse_blast(json.loads(_get_text(f"{BLAST_URL}/result/{job}/json")))


def parse_blast(payload: dict) -> dict | None:
    hits = payload.get("hits") or []
    if not hits:
        return None
    h = hits[0]
    hsp = (h.get("hit_hsps") or [{}])[0]
    acc = h.get("hit_acc") or (h.get("hit_id", "").split(":")[-1])
    return {"accession": acc, "identifier": h.get("hit_id", ""), "description": h.get("hit_desc", ""),
            "identity": float(hsp.get("hsp_identity", 0) or 0), "length": int(hsp.get("hsp_align_len", 0) or 0),
            "hsp": {"qseq": hsp.get("hsp_qseq", ""), "hseq": hsp.get("hsp_hseq", ""),
                    "query_from": int(hsp.get("hsp_query_from", 1) or 1), "hit_from": int(hsp.get("hsp_hit_from", 1) or 1)}}


def map_through_alignment(segments: list[dict], hsp: dict, numbering: list[int]) -> list[dict]:
    """UniProt segments of the hit, carried onto the query's residues through the aligned
    strings (gaps skipped), then onto the query's author numbering."""
    hit_to_query: dict[int, int] = {}
    q, h = hsp["query_from"], hsp["hit_from"]
    for a, b in zip(hsp["qseq"], hsp["hseq"]):
        if a != "-" and b != "-":
            hit_to_query[h] = q
        if a != "-":
            q += 1
        if b != "-":
            h += 1
    out = []
    for s in segments:
        qs = [hit_to_query[i] for i in range(s["start"], s["end"] + 1) if i in hit_to_query]
        if len(qs) < 5:
            continue
        a, b = min(qs), max(qs)
        if a - 1 < len(numbering) and b - 1 < len(numbering):
            out.append({**s, "start": numbering[a - 1], "end": numbering[b - 1], "unp_start": s["start"], "unp_end": s["end"]})
    return out


def annotate_by_blast(pdb_text: str, email: str, blast=None, fetch=None) -> dict:
    """Every protein chain: its sequence to BLAST, the best Swiss-Prot entry's transmembrane
    segments carried through the alignment. Says which entry, at what identity."""
    blast = blast or blast_swissprot
    fetch = fetch or _get_json
    chains, sources, membrane = {}, [], False
    for c, (seq, num) in sequences_of(pdb_text).items():
        if len(seq) < 30:
            continue
        hit = blast(seq, email)
        if not hit:
            chains[c] = {"accession": "", "identifier": "", "protein": "", "organism": "", "membrane": False, "segments": [], "topology": [], "outside": 0}
            continue
        entry = parse_uniprot(fetch(UNIPROT_URL.format(acc=hit["accession"])))
        segs = map_through_alignment(entry["segments"], hit["hsp"], num)
        chains[c] = {"accession": hit["accession"], "identifier": entry["identifier"], "protein": entry["protein"], "organism": entry["organism"],
                     "membrane": bool(segs), "segments": segs, "topology": map_through_alignment(entry["topology"], hit["hsp"], num),
                     "outside": len(entry["segments"]) - len(segs), "identity": hit["identity"], "aligned": hit["length"]}
        membrane = membrane or bool(segs)
        sources.append(f"UniProt {hit['accession']}, {entry['protein'] or entry['identifier']}, {hit['identity']:.0f}% identity over {hit['length']} residues by BLAST")
    source = "; ".join(dict.fromkeys(sources)) or "no Swiss-Prot hit"
    return {"method": "blast", "membrane": membrane, "chains": chains, "source": source,
            "summary": summary_of(chains, "blast", source) if chains else "no protein chain long enough to search"}


# ---------------------------------------------------------------- typed by the person
SEGMENT = re.compile(r"^\s*([A-Za-z0-9]):\s*(-?\d+)\s*-\s*(-?\d+)\s*$")


def parse_manual_segments(text: str) -> dict:
    """'A:30-56, A:71-96; B:10-30' -> {chain: [{start, end, kind: "typed"}]}. A ValueError
    names the piece that does not parse."""
    out: dict[str, list[dict]] = {}
    for piece in re.split(r"[,;\n]+", text or ""):
        if not piece.strip():
            continue
        m = SEGMENT.match(piece)
        if not m:
            raise ValueError(f"{piece.strip()!r} is not chain:start-end (like A:30-56)")
        a, b = int(m.group(2)), int(m.group(3))
        if b < a:
            a, b = b, a
        out.setdefault(m.group(1), []).append({"start": a, "end": b, "kind": "typed", "note": "typed"})
    return out


def annotate_manual(text: str) -> dict:
    segs = parse_manual_segments(text)
    chains = {c: {"accession": "", "identifier": "", "protein": "", "organism": "", "membrane": True, "segments": v, "topology": [], "outside": 0} for c, v in segs.items()}
    n = sum(len(v) for v in segs.values())
    return {"method": "manual", "membrane": n > 0, "chains": chains, "source": "typed",
            "summary": f"{n} transmembrane segment{'s' if n != 1 else ''} as typed"}
