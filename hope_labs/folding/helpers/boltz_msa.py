#!/usr/bin/env python
"""Fetch Boltz-2's alignments, on a login node, where there is a network.

Run by Boltz-2's own Python, on the login node, before anything is queued. Reads a plan on
stdin and writes one MSA per distinct protein sequence into the folder it is given.

    <boltz env>/bin/python boltz_msa.py <<'PLAN'
    {"sequences": {"entity_1": "MADQ..."}, "out": "/run/msa",
     "url": "https://api.colabfold.com", "pairing": "greedy"}
    PLAN

It prints one JSON object: {"written": {name: path}, "rows": {name: how many sequences}}, or
{"problem": "..."} when the server would not answer.

Why this is a helper and not a call to boltz's own compute_msa
--------------------------------------------------------------
compute_msa is what `boltz predict --use_msa_server` uses, and going through a tool's own door is
the rule everywhere else in this repository. It cannot be done here: compute_msa is in
boltz/main.py, which imports torch and pytorch_lightning at module level, and `from boltz.main
import compute_msa` took four minutes and fifty seconds on a Grace login node before failing.
Importing run_mmseqs2 and the constants takes seven seconds, because neither pulls in torch.

So the combining below is compute_msa's, step for step, against boltz 2.2.1: the paired search
when there is more than one entity, the unpaired search always, the paired rows first with their
pairing key and the unpaired rows after with a key of -1, the query dropped from the unpaired
block when a paired block is present, and the two-column CSV boltz/data/parse/csv.py reads. It is
a copy, and it is the one copy in this page, written down here so that a Boltz-2 upgrade that
changes compute_msa has somewhere obvious to be checked against.

The CSV rather than a plain .a3m: boltz takes either, but a .a3m carries no pairing, and pairing
is what makes an alignment worth having for a complex. The CSV is the format boltz writes for
itself.
"""
import json
import os
import sys


def main():
    plan = json.load(sys.stdin)
    out = plan["out"]
    os.makedirs(out, exist_ok=True)

    from boltz.data import const
    from boltz.data.msa.mmseqs2 import run_mmseqs2

    data = dict(plan["sequences"])
    url = plan.get("url") or "https://api.colabfold.com"
    pairing = plan.get("pairing") or "greedy"
    names = list(data)
    seqs = [data[n] for n in names]

    # Paired only when there is something to pair. One entity asked for in pairing mode comes
    # back as a single column, which is the unpaired search done twice.
    if len(data) > 1:
        paired_msas = run_mmseqs2(seqs, os.path.join(out, "_paired_tmp"), use_env=True,
                                  use_pairing=True, host_url=url, pairing_strategy=pairing)
    else:
        paired_msas = [""] * len(data)

    unpaired_msas = run_mmseqs2(seqs, os.path.join(out, "_unpaired_tmp"), use_env=True,
                                use_pairing=False, host_url=url, pairing_strategy=pairing)

    written, rows = {}, {}
    for idx, name in enumerate(names):
        # The a3m comes back as alternating header and sequence lines, so [1::2] is the sequences.
        paired = paired_msas[idx].strip().splitlines()[1::2][:const.max_paired_seqs]
        # A row that is all gaps is a species this entity was not found in, and it pairs with
        # nothing. Its index is the pairing key for the rows that are kept.
        keys = [row for row, seq in enumerate(paired) if seq != "-" * len(seq)]
        paired = [seq for seq in paired if seq != "-" * len(seq)]

        unpaired = unpaired_msas[idx].strip().splitlines()[1::2]
        unpaired = unpaired[: (const.max_msa_seqs - len(paired))]
        if paired:
            unpaired = unpaired[1:]          # the query is already the first paired row

        lines = ["key,sequence"] + ["%s,%s" % (key, seq) for key, seq
                                    in zip(keys + [-1] * len(unpaired), paired + unpaired)]
        path = os.path.join(out, "%s.csv" % name)
        with open(path, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        written[name] = path
        rows[name] = len(lines) - 1
    return {"written": written, "rows": rows}


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except Exception as why:                                       # noqa: BLE001
        # The caller shows this sentence beside the Submit button, so it has to be the reason
        # rather than a traceback. The traceback goes to stderr, where the page's terminal has it.
        import traceback
        traceback.print_exc()
        print(json.dumps({"problem": "%s: %s" % (type(why).__name__, why)}))
