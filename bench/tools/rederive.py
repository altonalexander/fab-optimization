#!/usr/bin/env python3
"""Re-derive every reported counter from a per-lot event ledger.

Audit plan item 3. Every defect found so far has been a MECHANISM defect --
the simulator did the wrong thing -- and the synthetic tests in
bench/tests/test_cqt_mechanism.py now cover those. This closes the other
half: a COUNTING defect, where the mechanism is right and the number printed
beside it is not. The counters on `Instance` are incremented at the moment
something happens; nothing has ever checked that the totals they carry agree
with what the lots actually did.

The method is deliberately redundant rather than clever. A plugin records,
during the run, one line per (lot, step) visit:

    (t_start, t_complete, lot_idx, step_order, n_processed_before)

and one line per release and per completion. Nothing in that ledger is a
counter; it is the raw history. An offline pass then rebuilds, from the
ledger ALONE plus the dataset's window table:

    throughput, cycle time, on-time %, tardiness   (vs compare.kpis)
    queue-time violations                          (vs counter_cqt_violated)
    reworks                                        (vs counter_cqt_rework)
    scraps                                         (vs counter_cqt_scrapped)
    releases = done + scrapped + WIP               (conservation)
    cycle time = wait + process + transport        (per-lot identity)

The violation re-derivation is the point of the exercise: it never looks at
`cqt_waiting`, `cqt_deadline` or the violation flag. It takes each step that
carries STEP_CQT, finds when that visit COMPLETED, finds when the named exit
step next STARTED, and compares the span to `cqt_time * scale` itself. If the
simulator's window bookkeeping and the dataset disagree, these two counts
diverge.

Usage:
    rederive.py --days 20 --dispatcher fifo --cqt --cqt-scale 10
    rederive.py --days 20 --dispatcher qt --cqt --cqt-scale 1 --json out.json
"""
import argparse
import json
import sys
from collections import defaultdict

import sim_runner                                      # noqa: E402  (bootstraps sys.path/cwd)
from plugins.interface import IPlugin                  # noqa: E402

SECONDS_PER_DAY = 86400


class Ledger(IPlugin):
    """Raw history. Deliberately holds no counters and makes no judgements."""

    def __init__(self):
        self.visits = []        # (t_start, t_complete, lot_idx, step_id, n_before)
        self.released = {}      # lot_idx -> (release_at, deadline_at, part)
        self.done = {}          # lot_idx -> done_at
        self.seen = set()       # every lot idx that ever appeared
        self.frees = []         # (t, lot_idx) each time a lot finishes a step
        self.flagged = []       # the simulator's own violation events

    def _note(self, lot):
        if lot.idx not in self.released:
            self.released[lot.idx] = (float(lot.release_at), float(lot.deadline_at),
                                      lot.part_name)
        self.seen.add(lot.idx)

    def on_sim_init(self, instance):
        # Initial WIP is already active at t=0 and is never "released".
        for lot in instance.active_lots:
            self._note(lot)

    def on_lots_release(self, instance, lots):
        for lot in lots:
            self._note(lot)

    def on_dispatch(self, instance, machine, lots, machine_end_time, lot_end_time):
        t = instance.current_time
        for lot in lots:
            self._note(lot)
            st = lot.actual_step
            if st is None:
                continue
            # Identity, not order: step ORDER repeats across routes (up to 10
            # steps share one order in LVHM), so keying a window table by
            # order matches entrance steps against other products' exits.
            #
            # `lot_end_time` is recorded but NOT used as the completion
            # instant: it is the time predicted AT DISPATCH, and `move_event`
            # pushes a lot's completion later when the machine breaks down or
            # goes into PM. Using it read a 0.25 h span as 10.29 h on the
            # first run of this tool. The true completion is the next on_lot_free.
            self.visits.append((float(t), float(lot_end_time), lot.idx,
                                id(st), len(lot.processed_steps)))

    def on_lot_free(self, instance, lot):
        # The lot has finished a step and is available for the next one. This
        # is the instant the simulator opens a window at, so it is the instant
        # the ledger must measure from.
        #
        # The processed-step count is recorded HERE as well as at dispatch,
        # because a rollback is visible at this moment whereas inferring it
        # from the next dispatch censors every lot that is rolled back near
        # the horizon and never starts another step (184 of them on a 20-day
        # qt run at scale 1). `actual_step` is where the lot has landed, which
        # separates a queue-time rollback from the route's own rework.
        st = lot.actual_step
        self.frees.append((float(instance.current_time), lot.idx,
                           len(lot.processed_steps),
                           id(st) if st is not None else None))

    def on_lot_done(self, instance, lot):
        self.done[lot.idx] = float(instance.current_time)

    def on_cqt_violated(self, instance, machine, lot):
        # The simulator's OWN verdict, captured for a set-difference against
        # the ledger's independent one. Recorded as (lot, exit step) because
        # that is the moment of detection: the exit step starting.
        st = lot.actual_step
        self.flagged.append((lot.idx, id(st) if st is not None else None,
                             float(instance.current_time)))


def window_table(instance):
    """id(entrance step) -> (id(exit step), window seconds), from the data.

    Read off the Step objects, which is where the loader put STEP_CQT/CQT.
    `cqt_for_step` names the EXIT step BY ORDER WITHIN ITS OWN ROUTE; the step
    carrying it is the ENTRANCE (audit F2). Keyed by object identity because
    step order is unique only within a route -- LVHM has 264 windows over 208
    distinct orders, and up to 10 steps share a single order across routes.
    """
    out = {}
    entrances = set()
    for route in instance.routes.values():
        by_order = {int(s.order): s for s in route.steps}
        for st in route.steps:
            fs = getattr(st, 'cqt_for_step', None)
            if isinstance(fs, (int, float)) and st.cqt_time:
                ex = by_order.get(int(fs))
                if ex is None:
                    continue
                out[id(st)] = (id(ex), float(st.cqt_time))
                entrances.add(id(st))
    return out, entrances


def rederive(ledger, windows, entrances, scale, warm_from, horizon_s):
    """Everything, from the ledger alone."""
    by_lot = defaultdict(list)
    for v in ledger.visits:
        by_lot[v[2]].append(v)
    for k in by_lot:
        by_lot[k].sort(key=lambda v: v[0])

    # When did each lot actually finish each step? The next on_lot_free after
    # the step started. NOT the dispatch-time prediction (see on_dispatch).
    frees_by_lot = defaultdict(list)
    free_rows = defaultdict(list)
    for t, idx, nproc, sid in ledger.frees:
        frees_by_lot[idx].append(t)
        free_rows[idx].append((t, nproc, sid))
    for k in frees_by_lot:
        frees_by_lot[k].sort()
    for k in free_rows:
        free_rows[k].sort(key=lambda r: r[0])

    import bisect

    def completion_of(idx, t_start):
        fs = frees_by_lot.get(idx)
        if not fs:
            return None
        j = bisect.bisect_right(fs, t_start)
        return fs[j] if j < len(fs) else None

    # --- lot-level KPIs -----------------------------------------------------
    n = on_time = 0
    ct_sum = tard = 0.0
    for idx, done_at in ledger.done.items():
        if done_at < warm_from:
            continue
        rel, due, _part = ledger.released[idx]
        n += 1
        ct_sum += (done_at - rel) / SECONDS_PER_DAY
        late = done_at - due
        if late <= 0:
            on_time += 1
        else:
            tard += late / SECONDS_PER_DAY

    # --- queue-time violations, from timestamps and the dataset -------------
    # For each visit to an entrance step: the window opens when that visit
    # COMPLETES and closes when the exit step next STARTS (WSC 2020 §2.1).
    violations = 0
    unclosed = 0
    detail = []
    for idx, vs in by_lot.items():
        for i, (_ts, _pred, _l, sid, _nb) in enumerate(vs):
            w = windows.get(sid)
            if w is None:
                continue
            exit_sid, win_s = w
            tc = completion_of(idx, _ts)
            if tc is None:
                unclosed += 1
                continue
            nxt = None
            voided = False
            for j in range(i + 1, len(vs)):
                # Was this entrance visit ROLLED BACK before the exit was
                # reached? 96 LVHM windows are chained -- the same step is one
                # window's exit and the next one's entrance -- so a violation
                # AT that step rolls the lot back past it and the visit never
                # stands. The simulator opens no window from a rolled-back
                # step (its `processed_steps[done_idx] is done_step` guard);
                # the redo that follows opens one of its own and is counted
                # then. Detected as the lot returning to this position or
                # earlier: processed-step count back at or below `_nb`.
                if vs[j][4] <= _nb:
                    voided = True
                    break
                if vs[j][3] == exit_sid:
                    nxt = vs[j][0]
                    break
            if voided:
                continue
            if nxt is None:
                unclosed += 1          # window still open at the horizon
                continue
            if nxt - tc > win_s * scale:
                violations += 1
                detail.append({'lot': idx, 'entrance': sid, 'exit': exit_sid,
                               'completed': tc, 'exit_started': nxt,
                               'span_s': nxt - tc, 'window_s': win_s * scale})

    # --- reworks: n_processed went DOWN between consecutive visits ----------
    # A rollback truncates processed_steps, so the count drops. But the ROUTE
    # has its own rework (52 LVHM steps carry REWORK, 0.5-1.8 %), which does
    # the same thing and has nothing to do with queue time. The discriminator
    # is the landing step: a queue-time rollback returns the lot to the step
    # that OPENED the window, which by construction carries STEP_CQT; a route
    # rework returns it to RWKSTEP. Both are reported, so a drift between them
    # is visible rather than hidden inside one number.
    rollbacks = 0
    reworks = 0
    for idx, rows_f in free_rows.items():
        prev = None
        for _t, nproc, sid in rows_f:
            if prev is not None and nproc < prev:
                rollbacks += 1
                if sid in entrances:
                    reworks += 1
            prev = nproc

    # --- scraps: appeared, never completed, and stopped moving early --------
    # A scrapped lot leaves active_lots, so its last visit is strictly before
    # the horizon and it has no completion. A lot still in WIP at the horizon
    # also has no completion, so the discriminator is that a WIP lot's last
    # visit runs up to the end; we resolve the ambiguity with the instance's
    # own WIP set in main(), and report this as a bound rather than a count.
    never_done = [i for i in ledger.seen if i not in ledger.done]

    return {
        'throughput': n,
        'cycle_time_days': round(ct_sum / n, 4) if n else 0.0,
        'on_time_pct': round(100.0 * on_time / n, 2) if n else 0.0,
        'tardiness_lot_days': round(tard, 2),
        'violations': violations,
        'windows_unclosed_at_horizon': unclosed,
        'reworks': reworks,
        'rollbacks_any': rollbacks,
        'violation_detail': detail,
        'never_done': len(never_done),
        'lots_seen': len(ledger.seen),
        'visits': len(ledger.visits),
    }


def compare(name, mine, theirs, tol=0, out=None):
    ok = (abs(mine - theirs) <= tol) if isinstance(mine, (int, float)) else mine == theirs
    mark = 'ok   ' if ok else 'DIFF '
    line = f'  {mark} {name:<34} ledger {mine!s:>12}   instance {theirs!s:>12}'
    print(line)
    if out is not None:
        out.append({'name': name, 'ledger': mine, 'instance': theirs, 'ok': bool(ok)})
    return ok


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sim_runner.add_common_args(p, days_default=20)
    p.add_argument('--cqt', action='store_true')
    p.add_argument('--cqt-scale', type=float, default=1.0)
    p.add_argument('--cqt-no-rework', action='store_true')
    p.add_argument('--cqt-max-rework', type=int, default=3)
    p.add_argument('--warmup-days', type=float, default=0.0)
    p.add_argument('--json')
    a = p.parse_args()

    ledger = Ledger()
    instance, run_to = sim_runner.build(
        sim_runner.normalize_dataset(a.dataset), a.days, a.seed, [ledger],
        a.batch_strat)
    instance.cqt_enforce = bool(a.cqt)
    instance.cqt_scale = float(a.cqt_scale or 1.0)
    instance.cqt_rework = not a.cqt_no_rework
    instance.cqt_max_rework = None if not a.cqt_max_rework else int(a.cqt_max_rework)

    print(f'run: {a.days}d seed {a.seed} rule {a.dispatcher} '
          f'cqt={a.cqt} scale={a.cqt_scale} rework={instance.cqt_rework} '
          f'max_rework={instance.cqt_max_rework}', flush=True)
    sim_runner.run(instance, run_to, a.dispatcher, stream=sys.stderr)

    windows, entrances = window_table(instance)
    warm_from = a.warmup_days * SECONDS_PER_DAY
    mine = rederive(ledger, windows, entrances, instance.cqt_scale,
                    warm_from, run_to)

    # what the instance itself says
    import compare as compare_mod
    theirs = compare_mod.kpis(instance, warm_from)

    print(f'\nwindow table: {len(windows)} windows resolved to an exit step')
    print(f'ledger: {mine["visits"]} step visits, {mine["lots_seen"]} lots\n')

    rows = []
    good = True
    good &= compare('throughput (lots)', mine['throughput'], theirs['throughput'], out=rows)
    good &= compare('cycle time (days)', mine['cycle_time_days'],
                    theirs['cycle_time_days'], tol=1e-4, out=rows)
    good &= compare('on-time (%)', mine['on_time_pct'], theirs['on_time_pct'],
                    tol=1e-2, out=rows)
    good &= compare('tardiness (lot-days)', mine['tardiness_lot_days'],
                    theirs['tardiness_lot_days'], tol=0.02, out=rows)
    good &= compare('cqt violations', mine['violations'],
                    instance.counter_cqt_violated, out=rows)
    good &= compare('cqt reworks', mine['reworks'],
                    instance.counter_cqt_rework, out=rows)
    print(f'      (all rollbacks incl. route rework: {mine["rollbacks_any"]}; '
          f'windows still open at the horizon: '
          f'{mine["windows_unclosed_at_horizon"]})')

    # Where exactly do the two verdicts disagree? The simulator flags at the
    # EXIT step starting; the ledger names the same lot and exit step, so the
    # sets are directly comparable.
    sim_set = {(l, s) for l, s, _t in ledger.flagged}
    mine_set = {(d['lot'], d['exit']) for d in mine['violation_detail']}
    only_mine = mine_set - sim_set
    only_sim = sim_set - mine_set
    if only_mine or only_sim:
        print(f'\n  violation set difference: ledger-only {len(only_mine)}, '
              f'simulator-only {len(only_sim)}')
        bysid = {}
        for route in instance.routes.values():
            for st in route.steps:
                bysid[id(st)] = (route, st)
        for lot_idx, exit_sid in list(only_mine)[:8]:
            d = next(x for x in mine['violation_detail']
                     if x['lot'] == lot_idx and x['exit'] == exit_sid)
            ent = bysid.get(d['entrance'], (None, None))[1]
            ex = bysid.get(exit_sid, (None, None))[1]
            print(f'    lot {lot_idx}: step {ent.order if ent else "?"} -> '
                  f'{ex.order if ex else "?"}  span {d["span_s"] / 3600:.2f} h '
                  f'vs window {d["window_s"] / 3600:.2f} h')

    # conservation, from the instance's own sets
    released = len(ledger.seen)
    wip = len(instance.active_lots)
    scrapped = len(instance.scrapped_lots)
    done = len(instance.done_lots)
    print()
    good &= compare('conservation: seen == d+s+wip', released,
                    done + scrapped + wip, out=rows)
    good &= compare('scrapped (ledger never-done bound)',
                    mine['never_done'], scrapped + wip, out=rows)
    good &= compare('counter_cqt_scrapped == len(scrapped_lots)',
                    instance.counter_cqt_scrapped, scrapped, out=rows)

    # per-lot time identity: cycle time = wait + process + transport
    # Initial WIP is EXCLUDED: those lots start mid-route with a backdated
    # release_at, so their span covers time before the simulation existed and
    # no accumulator can account for it. (Worth knowing on its own: on a run
    # with no warm-up they dominate the reported cycle time.)
    # Initial WIP identifies itself: a lot placed at CURSTEP has already
    # processed steps before its first dispatch. (on_sim_init cannot see
    # them -- plugins are constructed before the WIP is placed.)
    first_seen = {}
    for t, _tc, idx, _sid, nb in ledger.visits:
        if idx not in first_seen or t < first_seen[idx][0]:
            first_seen[idx] = (t, nb)
    initial_wip = {i for i, (_t, nb) in first_seen.items() if nb > 0}

    bad = []
    resid = []
    skipped_wip = 0
    for lot in instance.done_lots:
        if lot.done_at is None or lot.done_at < warm_from:
            continue
        if lot.idx in initial_wip:
            skipped_wip += 1
            continue
        span = lot.done_at - lot.release_at
        parts = lot.waiting_time + lot.processing_time + lot.transport_time
        # NOT an equality. `processing_time` is proc + load + unload only, so
        # the span also absorbs setup and any breakdown or PM that lands while
        # the lot is on the tool (`move_event` pushes its completion out). The
        # invariant that DOES hold is one-sided: the accumulators can never
        # exceed elapsed time. A negative residual would mean a lot was
        # credited with more work than the clock allows.
        resid.append(span - parts)
        if parts - span > 1.0:
            bad.append((lot.idx, span, parts))
    print()
    good &= compare('lots with parts > elapsed (impossible)', len(bad), 0, out=rows)
    if resid:
        mean_h = sum(resid) / len(resid) / 3600.0
        print(f'      ({len(resid)} released-and-completed lots; '
              f'{skipped_wip} initial-WIP excluded as backdated. '
              f'mean unaccounted {mean_h:.2f} h/lot = setup + downtime on tool)')
    if bad[:3]:
        for idx, span, parts in bad[:3]:
            print(f'      lot {idx}: span {span:.1f}s vs parts {parts:.1f}s '
                  f'(gap {span - parts:+.1f}s)')

    print(f'\n{"ALL CHECKS PASS" if good else "MISMATCHES ABOVE"}')
    if a.json:
        with open(a.json, 'w') as f:
            json.dump({'args': vars(a), 'ledger': mine, 'instance_kpis': theirs,
                       'checks': rows, 'pass': bool(good)}, f, indent=2)
        print(f'wrote {a.json}')
    sys.exit(0 if good else 1)


if __name__ == '__main__':
    main()
