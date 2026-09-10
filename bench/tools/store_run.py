#!/usr/bin/env python3
"""Put a benchmark row into the run store, so the Results page can lay it
over a live run.

    python3 bench/tools/store_run.py bench/results/rematch/slate120_x1.03.json --label 'slate-0012'

`compare.py --out` JSON carries the feed's own hourly KPI samples (the same
_kpi_sample the live feed writes), so a row stored here is directly
comparable with a streamed run: same definitions, same warm-up line. The
dispatcher column becomes `<label>@<starts>x` so a row's start rate is never
hidden -- rows at different start rates are different experiments.
"""
import argparse, json, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# sim_feed changes the working directory when imported (it runs from the
# simulator's tree), so paths on the command line are resolved first.
_ARGS = [os.path.abspath(x) if x.endswith('.json') and os.path.exists(x) else x for x in sys.argv[1:]]
sys.argv[1:] = _ARGS
import sim_feed  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('json')
    p.add_argument('--label', default=None, help='dispatcher label; default: the rule name in the file')
    p.add_argument('--rule', default=None, help='only this rule from a multi-rule file')
    a = p.parse_args()
    doc = json.load(open(a.json))
    for row in doc['rows']:
        if a.rule and row['rule'] != a.rule:
            continue
        scale = row.get('starts_scale', 1.0) or 1.0
        # Overlay in the LABEL, not only in the notes: the Results page draws
        # rows by dispatcher, and a dedicated row laid over a pristine one
        # unlabelled is the failure adr/0013 §3.5 names by name.
        ovl = row.get('overlay') or doc.get('overlay')
        label = (f"{a.label or row['rule']}@{scale:.2f}x"
                 + (f"/{ovl}" if ovl else ''))
        args = types.SimpleNamespace(
            dataset=doc.get('dataset', 'SMT2020_LVHM'), seed=doc.get('seed', 0),
            dispatcher=label, batch_strat=doc.get('batch_strat', 'Demand'),
            days=doc.get('days'), warmup_days=doc.get('warmup_days'))
        key = f"bench:{row.get('fingerprint', label)}"
        notes = json.dumps({'source': os.path.relpath(a.json, os.path.dirname(HERE)), 'rule': row['rule'],
                            'starts_scale': scale, 'cycle_s': doc.get('cycle_s'),
                            'solver': doc.get('solver'), 'coverage': (row.get('detail') or {}).get('coverage'),
                            'overlay': ovl,
                            'overlay_hash': row.get('overlay_hash') or doc.get('overlay_hash'),
                            'idle_qualified_wip_tool_h_per_day':
                                row.get('idle_qualified_wip_tool_h_per_day'),
                            'idle_family_wip_tool_h_per_day':
                                row.get('idle_family_wip_tool_h_per_day')})
        store = sim_feed.RunStore()
        store.begin(key, args, notes=notes)
        if store.conn is None:
            sys.exit('  run store unavailable')
        samples = row.get('samples') or []
        warm = [s for s in samples if s.get('warmup')]
        live = [s for s in samples if not s.get('warmup')]
        store.samples(warm, True)
        store.samples(live, False)
        store.finish('finished')
        print(f"  stored {label}: {len(live)} live samples, {len(warm)} warm-up, run #{store.run_id}")


if __name__ == '__main__':
    main()
