"""
Generate dispatch/config/floorplan.json from the bay/segment grid.

The grid is the source of truth and metres are derived, not the other way
round: grid indices are what the transport-time function consumes, and they
survive layout revisions that metre positions do not.

SYNTHETIC. SMT2020 carries no floorplan -- every machine's `loc` is the string
"Fab" -- so this layout is a plausible 300mm arrangement, not the dataset's
geometry. It is committed rather than generated at runtime so that a tool keeps
the same cell across restarts; a map that reshuffles on reload is worse than no
map.

    python3 scripts/gen_floorplan.py
"""
import json
import os

# Row S0 and S7 are the interbay backbone; S1..S6 hold tools.
GRID = [
    "STK STK STK STK STK STK STK STK STK STK STK STK",
    "CMP CMP DIF DIF TF  TF  LIT LIT LIT ETC ETC IMP",
    "CMP CMP DIF DIF TF  TF  LIT LIT LIT ETC ETC IMP",
    "MET CMP MET DIF MET TF  RET LIT MET MET ETC IMP",
    "CLN CLN DIF DIF TF  TF  LIT LIT LIT ETC ETC IMP",
    "CLN CLN DIF RTP TF  TF  LIT LIT LIT ETC ETC IMP",
    "CLN MET RTP RTP TF  MET LIT MET LIT ETC MET IMP",
    "STK STK STK STK STK STK STK STK STK STK STK STK",
]

ZONES = {
    "CMP": ("Planarisation",   "#7c3aed"),
    "CLN": ("Wet clean",       "#0891b2"),
    "DIF": ("Diffusion",       "#b45309"),
    "RTP": ("Rapid thermal",   "#c2410c"),
    "TF":  ("Thin film",       "#2563eb"),
    "LIT": ("Photolithography", "#0d9488"),
    "ETC": ("Etch",            "#be123c"),
    "IMP": ("Implant",         "#4d7c0f"),
    "MET": ("Metrology",       "#6b7280"),
    "RET": ("Reticle stocker", "#a16207"),
    "STK": ("Stocker / track", "#9ca3af"),
}

BAY_PITCH_M = 11.8
SEG_M = 12.5
BAYS = 12
SEGS = 8


def main():
    cells = []
    by_zone = {}
    for seg, row in enumerate(GRID):
        codes = row.split()
        assert len(codes) == BAYS, f"row {seg} has {len(codes)} bays"
        for bay, code in enumerate(codes):
            assert code in ZONES, f"unknown zone {code}"
            cells.append({
                "bay": bay,
                "seg": seg,
                "zone": code,
                # Derived from the grid, never the source of truth.
                "x_m": round(bay * BAY_PITCH_M + BAY_PITCH_M / 2, 2),
                "y_m": round(seg * SEG_M + SEG_M / 2, 2),
            })
            by_zone.setdefault(code, []).append([bay, seg])

    doc = {
        "synthetic": True,
        "note": ("Synthetic layout. SMT2020 has no floorplan (every machine's "
                 "loc is 'Fab'), so tools are assigned to cells by family. "
                 "Positions are plausible, not measured."),
        "envelope_m": [round(BAYS * BAY_PITCH_M, 1), round(SEGS * SEG_M, 1)],
        "bay_pitch_m": BAY_PITCH_M,
        "seg_m": SEG_M,
        "bays": BAYS,
        "segs": SEGS,
        "cells": cells,
        "zones": [
            {"id": z, "label": ZONES[z][0], "color": ZONES[z][1],
             "cells": by_zone.get(z, [])}
            for z in ZONES
        ],
        # S0 and S7 are the backbone rows, so the interbay track is the grid
        # itself rather than a loop drawn outside it.
        "track": {
            "interbay_segs": [0, SEGS - 1],
            "intrabay": [{"bay": b, "from_seg": 0, "to_seg": SEGS - 1}
                         for b in range(BAYS)],
        },
        # Lots must enter and leave through something, or a lot-loss bug looks
        # identical to a lot that has not started yet.
        "virtual": [
            {"id": "SOURCE", "bay": 0,  "seg": 0, "role": "wafer start"},
            {"id": "SINK",   "bay": 11, "seg": 7, "role": "finished lot"},
        ],
    }

    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "dispatch", "config", "floorplan.json")
    with open(os.path.abspath(out), "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")

    counts = {z: len(by_zone.get(z, [])) for z in ZONES}
    print(f"wrote {os.path.abspath(out)}")
    print(f"  {len(cells)} cells, {BAYS} bays x {SEGS} segs, "
          f"{doc['envelope_m'][0]}m x {doc['envelope_m'][1]}m")
    for z, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {z:<4} {n:>3} cells   {ZONES[z][0]}")


if __name__ == "__main__":
    main()
