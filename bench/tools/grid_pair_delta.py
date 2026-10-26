#!/usr/bin/env python3
"""Paired per-seed deltas between two grid arms on the same checkpoints.

    grid_pair_delta.py ARM_A ARM_B [--win 60] [--scales 4 3 2] [--share-max 0.15]
    e.g. grid_pair_delta.py critv5K6s8U300B2P1800Wqtfw qtfwK6s8Wqtfw

Prints, per scale and seed: good/d, scrap share and drift for A and B, and A - B.
Then per scale: mean delta, how many seeds A wins on share, and whether each arm
is viable (drift < +5 AND share <= cutoff on every seed).
"""
import argparse
import glob
import os
import re
import statistics as st

import analyse_grid as ag

HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(HERE, '..', 'results', 'grid')


def arm(name, win):
    out = {}
    for p in glob.glob(os.path.join(G, f'{name}_x*_L100_s*_w{win}.json')):
        m = ag.PAT.search(os.path.basename(p))
        if not m or m['rule'] != name:
            continue
        c = ag.cell(p, name)
        if c:
            out[(int(m['scale']), int(m['seed']))] = c
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--win', type=int, default=60)
    ap.add_argument('--scales', type=int, nargs='*', default=[4, 3, 2])
    ap.add_argument('--share-max', type=float, default=0.15)
    x = ap.parse_args()
    A, B = arm(x.a, x.win), arm(x.b, x.win)
    print(f'A = {x.a}\nB = {x.b}\nwindow {x.win} d; viable = drift < +5 and share <= {x.share_max:.0%} on every seed\n')
    print(f'{"scale":>5} {"seed":>4} | {"A good/d":>8} {"A share":>8} {"A drift":>7} | '
          f'{"B good/d":>8} {"B share":>8} {"B drift":>7} | {"d good/d":>8} {"d share":>8}')
    for sc in x.scales:
        seeds = sorted(s for (k, s) in A if k == sc and (sc, s) in B)
        if not seeds:
            continue
        dg, ds = [], []
        for s in seeds:
            a, b = A[(sc, s)], B[(sc, s)]
            dg.append(a['good_d'] - b['good_d'])
            ds.append(a['share'] - b['share'])
            print(f'{sc:>5} {s:>4} | {a["good_d"]:>8.1f} {a["share"]:>8.1%} {a["drift"]:>+7.1f} | '
                  f'{b["good_d"]:>8.1f} {b["share"]:>8.1%} {b["drift"]:>+7.1f} | '
                  f'{dg[-1]:>+8.1f} {ds[-1] * 100:>+7.1f}pt')

        def viable(D):
            cs = [D[(sc, s)] for s in seeds]
            return all(c['drift'] is not None and c['drift'] < 5 and c['share'] <= x.share_max for c in cs)
        wins = sum(1 for d in ds if d < 0)
        print(f'{sc:>5} mean | n={len(seeds)}  d good/d {st.mean(dg):+.1f}  d share {st.mean(ds) * 100:+.1f}pt  '
              f'A lower share on {wins}/{len(seeds)} seeds  |  viable: A={"yes" if viable(A) else "no"} '
              f'B={"yes" if viable(B) else "no"}\n')


if __name__ == '__main__':
    main()
