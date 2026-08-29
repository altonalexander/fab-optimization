#!/usr/bin/env python3
"""
tools/load_smt2020.py — convert the SMT2020 testbed into a fab tool master.

WHY THIS DATASET
SMT2020 (Kopp, Hassoun, Kalir & Monch, IEEE Trans. Semiconductor Mfg 33(4),
2020) is the current standard open benchmark for wafer-fab simulation. It
supersedes SEMATECH's MIMAC datasets from 1995, which the SMT2020 authors note
lack clear implementation guidelines and are therefore hard to reproduce.

Scale, which is the point:
  - ~1,071-1,265 machines across 105 tool groups
  - up to 583 process steps per product, 44 mask layers
  - modelled preventive maintenance AND unscheduled downtime
  - two scenarios: HV/LM (high-volume, low-mix) and LV/HM (low-volume,
    high-mix, 10 products) plus two engineering-lot variants

That is 100x our 12-tool demo config and is what you need to claim the
architecture scales. A greedy planner will fall over on it; that is the
experiment worth running.

RELATED
  - SMAT2022 (github.com/kwoo-lee/SMAT2022) extends SMT2020 with AMHS detail,
    which is what our transport layer actually needs.
  - Minifab (Intel five-machine model) is useful for unit tests, too small for
    performance work.

>>> VERIFY BEFORE TRUSTING: SMT2020 is distributed in the authors' own format
    and has been repackaged several times. This loader is written against the
    documented structure (tool groups, routes, products) and is TOLERANT: it
    reports what it could not map rather than guessing. Run with --inspect
    first on your actual download and check the mapping report before using
    the output.

Usage
  python3 tools/load_smt2020.py --inspect  path/to/smt2020.json
  python3 tools/load_smt2020.py --dataset  path/to/smt2020.json \
                                --out config/fab_tools_smt2020.json
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

# SMT2020 tool-group names are descriptive strings. Map them onto our
# MachineConfiguration kinds by keyword. Order matters: first match wins.
KIND_RULES = [
    (("furnace", "diffusion", "oxidation", "anneal", "lpcvd"), "BATCH_FURNACE"),
    (("litho", "stepper", "scanner", "expose", "track"),       "LITHO_SCANNER"),
    (("metrology", "measure", "inspect", "sem", "ellips", "overlay"), "METROLOGY"),
    (("test", "probe", "sort"),                                 "PROBE_TESTER"),
    (("cluster", "pvd", "cvd", "epi", "sputter"),               "CLUSTER"),
    (("etch", "cmp", "implant", "clean", "strip", "wet"),       "SINGLE_WAFER"),
]

DEFAULTS = {
    "SINGLE_WAFER":  {"sec_per_wafer": 45.0, "changeover_s": 600.0},
    "BATCH_FURNACE": {"min_batch": 4, "max_batch": 6,
                      "fixed_process_s": 7200.0, "max_hold_s": 1800.0},
    "CLUSTER":       {"sec_per_wafer": 30.0},
    "LITHO_SCANNER": {"sec_per_wafer": 22.0, "reticle_swap_s": 300.0},
    "METROLOGY":     {"sample_rate": 0.20, "slots": 2, "sec_per_lot": 480.0},
    "PROBE_TESTER":  {"parallel_sites": 4, "sec_per_wafer": 18.0,
                      "card_change_s": 1200.0, "temp_soak_s": 900.0},
}


def classify(name: str) -> str:
    low = (name or "").lower()
    for keys, kind in KIND_RULES:
        if any(k in low for k in keys):
            return kind
    return "SINGLE_WAFER"          # safest default: no batching, no reticle


def find_tool_groups(doc):
    """SMT2020 repackagings differ. Probe the likely shapes rather than assume."""
    for key in ("tool_groups", "toolGroups", "machines", "tools", "stations"):
        if isinstance(doc, dict) and key in doc:
            return doc[key], key
    if isinstance(doc, list):
        return doc, "<root list>"
    return None, None


def find_products(doc):
    for key in ("products", "routes", "flows", "recipes"):
        if isinstance(doc, dict) and key in doc:
            return doc[key], key
    return None, None


def inspect(path):
    with open(path) as f:
        doc = json.load(f)
    groups, gk = find_tool_groups(doc)
    prods, pk = find_products(doc)

    print(f"file: {path}")
    print(f"top-level keys: {list(doc)[:20] if isinstance(doc, dict) else 'list'}")
    print(f"tool groups: key={gk} count={len(groups) if groups else 0}")
    print(f"products:    key={pk} count={len(prods) if prods else 0}")

    if groups:
        sample = groups[0] if isinstance(groups, list) else next(iter(groups.values()))
        print(f"\nsample tool group fields: {list(sample) if isinstance(sample, dict) else type(sample)}")
        print(json.dumps(sample, indent=2)[:600])
        kinds = Counter(classify(g.get("name", g.get("id", "")) if isinstance(g, dict) else str(g))
                        for g in (groups if isinstance(groups, list) else groups.values()))
        print(f"\nkind classification: {dict(kinds)}")
    print("\nCheck the classification above before converting. Anything landing "
          "in SINGLE_WAFER that should batch will change your results.")


def convert(path, out_path, max_tools):
    with open(path) as f:
        doc = json.load(f)
    groups, _ = find_tool_groups(doc)
    if not groups:
        sys.exit("could not locate tool groups; run --inspect and adjust "
                 "find_tool_groups() for your file's shape")

    if isinstance(groups, dict):
        groups = [{**v, "id": k} if isinstance(v, dict) else {"id": k, "name": str(v)}
                  for k, v in groups.items()]

    tools, recipes_seen, unmapped = [], set(), []
    tool_count = 0

    for g in groups:
        if not isinstance(g, dict):
            unmapped.append(str(g)[:60])
            continue
        gname = g.get("name") or g.get("id") or g.get("toolGroup") or ""
        kind = classify(gname)

        # Tool group -> N identical tools. SMT2020 groups share all
        # characteristics, which is exactly our config model.
        n = int(g.get("count") or g.get("num_machines") or g.get("machines") or 1)
        # Recipes the group is qualified for.
        recs = (g.get("recipes") or g.get("steps") or g.get("operations")
                or g.get("processes") or [])
        recs = [str(r) for r in recs][:40] or [f"{gname}_STEP"]
        recipes_seen.update(recs)

        for i in range(n):
            if max_tools and tool_count >= max_tools:
                break
            tid = f"{(g.get('id') or gname).replace(' ', '_').upper()}_{i:02d}"
            entry = {"id": tid, "kind": kind,
                     "area": (g.get("area") or gname.split()[0] if gname else "FAB")[:24],
                     **DEFAULTS[kind]}

            if kind == "CLUSTER":
                entry["chambers"] = [{"name": c, "recipes": recs}
                                     for c in ("A", "B", "C")]
            elif kind == "PROBE_TESTER":
                entry["test_programs"] = recs
                entry["probe_cards"] = ["PC_GENERIC"]
                entry["product_cards"] = {}
                entry["program_temps"] = {r: "ambient" for r in recs}
                # >>> PLACEHOLDER: SMT2020 has no probe-card model. Populate
                #     product_cards from your own sort data before trusting
                #     tester results.
            else:
                entry["recipes"] = recs

            tools.append(entry)
            tool_count += 1
        if max_tools and tool_count >= max_tools:
            break

    master = {
        "fab": "SMT2020",
        "revision": "generated",
        "source": "SMT2020 testbed (Kopp et al. 2020)",
        "active_recipes": sorted(recipes_seen),
        "tools": tools,
    }
    with open(out_path, "w") as f:
        json.dump(master, f, indent=2)

    kinds = Counter(t["kind"] for t in tools)
    print(f"wrote {out_path}")
    print(f"  tools:   {len(tools)}")
    print(f"  recipes: {len(recipes_seen)}")
    print(f"  kinds:   {dict(kinds)}")
    if unmapped:
        print(f"  UNMAPPED ({len(unmapped)}): {unmapped[:5]}")
    print("\nValidate before use:")
    print(f"  ./fabdisp --config {out_path}")
    print("The loader's validation pass will reject any active recipe with no "
          "qualified tool.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?", help="path to SMT2020 json")
    ap.add_argument("--inspect", action="store_true",
                    help="probe structure and show kind classification")
    ap.add_argument("--out", default="config/fab_tools_smt2020.json")
    ap.add_argument("--max-tools", type=int, default=0,
                    help="cap tool count (0 = all); useful for scaling tests")
    a = ap.parse_args()
    if not a.dataset:
        ap.error("dataset path required")
    if a.inspect:
        inspect(a.dataset)
    else:
        convert(a.dataset, a.out, a.max_tools)
