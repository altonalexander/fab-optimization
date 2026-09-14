#!/usr/bin/env python3
"""Render every figure in the paper from paper_data.json.

Static figures for print, so the interactive layer does not apply; the rest of
the chart discipline does. Colour follows the entity and never its rank: each
rule/configuration owns one categorical slot everywhere it appears, and legends
are ordered by slot so adjacent pairs are the validated ones. Text is ink, not
series colour. Thin marks, hairline solid grid, one axis per plot.

Palette: the reference categorical palette, validated with
scripts/validate_palette.js for the two legend orders used here (both pass;
the aqua/magenta adjacency in Fig. 3 sits in the 6-8 CVD band and so carries
secondary encoding -- 2px gaps and direct labels -- and every figure has a
table twin in the paper).
"""
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import rcParams  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, 'paper_data.json')))
OUT = os.path.join(os.path.dirname(HERE), 'figures')
os.makedirs(OUT, exist_ok=True)

# -- palette: entity -> (slot, hex) ----------------------------------------
SLOT = {
    'QT':          (1, '#2a78d6'),
    'CR':          (2, '#eb6834'),
    'SLATE_fixed': (3, '#1baf7a'),
    'FIFO':        (4, '#eda100'),
    'SLATE_inert': (5, '#e87ba4'),
    'QT_tuned':    (6, '#008300'),
    'SLATE_sym':   (7, '#4a3aa7'),
}
LABEL = {
    'QT': 'QT (promote any saveable lot)',
    'CR': 'CR (critical ratio)',
    'FIFO': 'FIFO',
    'SLATE_inert': 'SLATE, q-time term inert',
    'SLATE_fixed': 'SLATE, window-relative, untuned fallback',
    'QT_tuned': 'QT, promote < 50% of window',
    'SLATE_sym': 'SLATE, window-relative, tuned fallback',
}
SURFACE, INK, INK2, MUTED, GRID, AXIS = ('#fcfcfb', '#0b0b0b', '#52514e',
                                         '#898781', '#e1e0d9', '#c3c2b7')

rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['DejaVu Sans', 'Helvetica', 'Arial'],
    'font.size': 9,
    'axes.edgecolor': AXIS, 'axes.labelcolor': INK2, 'axes.titlecolor': INK,
    'axes.facecolor': SURFACE, 'figure.facecolor': SURFACE,
    'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.5,
    'grid.linestyle': '-',
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.linewidth': 0.6,
    'xtick.color': MUTED, 'ytick.color': MUTED, 'xtick.labelcolor': INK2,
    'ytick.labelcolor': INK2,
    'legend.frameon': False, 'legend.fontsize': 8,
    'lines.linewidth': 1.5,
    'savefig.dpi': 200, 'savefig.facecolor': SURFACE,
})


def by_slot(keys):
    return sorted(keys, key=lambda k: SLOT[k][0])


def save(fig, name):
    for ext in ('png', 'svg'):
        fig.savefig(os.path.join(OUT, f'{name}.{ext}'), bbox_inches='tight')
    plt.close(fig)
    print('  wrote', name)


def endpoint_label(ax, x, y, text, color, dy=0):
    ax.annotate(text, (x, y), xytext=(4, dy), textcoords='offset points',
                fontsize=7.5, color=INK2, va='center', ha='left',
                annotation_clip=False)


# -- Fig 1: WIP trajectories, seed 0, all six configurations --------------
def fig1():
    runs = {'QT': 'qt_s0', 'CR': 'cr_s0', 'FIFO': 'fifo_s0',
            'SLATE_inert': 'slate_inert_a', 'SLATE_fixed': 'slate_fixed_a',
            'SLATE_sym': 'slate_sym'}
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    ends = []
    for ent in by_slot(runs):
        s = DATA[runs[ent]]['series']
        x = [p['day'] for p in s]
        y = [p['wip'] for p in s]
        ax.plot(x, y, color=SLOT[ent][1], label=LABEL[ent])
        ends.append((y[-1], x[-1], ent))
    # endpoint labels, nudged apart where they collide
    ends.sort()
    last = -1e9
    for y, x, ent in ends:
        yy = max(y, last + 110)
        endpoint_label(ax, x, yy, f'{y:,}', SLOT[ent][1])
        last = yy
    ax.set_xlabel('simulated day (warm-up ends at day 90)')
    ax.set_ylabel('WIP, lots')
    ax.set_xlim(90, 285)
    ax.set_ylim(1800, 4700)
    # legend below the axes: every quadrant of the plot has a line in it
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18), ncol=2)
    ax.set_title('WIP over the 180-day window, seed 0, one shared starting fab',
                 loc='left', fontsize=10)
    save(fig, 'fig1_wip_trajectories')


# -- Fig 2: replication on three seeds (FIFO / CR / QT) --------------------
def fig2():
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.7), sharey=True)
    for i, ax in enumerate(axes):
        for ent in by_slot(['QT', 'CR', 'FIFO']):
            s = DATA[f'{ent.lower()}_s{i}']['series']
            ax.plot([p['day'] for p in s], [p['wip'] for p in s],
                    color=SLOT[ent][1], label=LABEL[ent].split(' (')[0])
        ax.set_title(f'seed {i}', loc='left', fontsize=9)
        ax.set_xlim(90, 270)
        ax.set_xlabel('simulated day')
        if i == 0:
            ax.set_ylabel('WIP, lots')
            ax.legend(loc='upper left')
    fig.suptitle('The same three rules on three independent seeds',
                 x=0.02, ha='left', fontsize=10)
    fig.tight_layout()
    save(fig, 'fig2_seeds')


# -- Fig 3: per-part on-time, grouped bars --------------------------------
def fig3():
    cfg = {'QT': 'qt_s0', 'SLATE_fixed': 'slate_fixed_a',
           'SLATE_inert': 'slate_inert_a', 'QT_tuned': 'qt50',
           'SLATE_sym': 'slate_sym'}
    ents = by_slot(cfg)
    parts = sorted(DATA['qt_s0']['by_part'],
                   key=lambda p: DATA['qt_s0']['by_part'][p]['on_time_pct'])
    n = len(ents)
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    group_h = 0.82
    bar_h = group_h / n
    gap = 0.06 * bar_h      # a surface gap between adjacent fills
    for j, ent in enumerate(ents):
        vals = [DATA[cfg[ent]]['by_part'][p]['on_time_pct'] for p in parts]
        ys = [i + (j - (n - 1) / 2) * bar_h for i in range(len(parts))]
        ax.barh(ys, vals, height=bar_h - gap, color=SLOT[ent][1],
                label=LABEL[ent], edgecolor=SURFACE, linewidth=0.6)
        # direct labels on the worst product only -- the row the paper argues
        # from; the top rows all sit at ~99% and labels there would collide
        for i, (y, v) in enumerate(zip(ys, vals)):
            if parts[i] == parts[0]:
                ax.annotate(f'{v:.0f}%', (v, y), xytext=(3, 0),
                            textcoords='offset points', fontsize=6.5,
                            color=INK2, va='center')
    ax.set_yticks(range(len(parts)))
    ax.set_yticklabels([p.replace('part_', 'product ') for p in parts])
    ax.set_xlabel('on-time delivery, % of completed lots (180-day window)')
    ax.set_xlim(0, 104)
    ax.grid(axis='y', visible=False)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.09), ncol=2,
              fontsize=7.5)
    ax.set_title('Per-product on-time, products ordered by their QT result',
                 loc='left', fontsize=10)
    save(fig, 'fig3_per_part')


# -- Fig 4: the short-window trap ------------------------------------------
def fig4():
    windows = [20, 60, 120, 180]

    def mean_over(key, field, w):
        s = [p for p in DATA[key]['series'] if p['day'] - 90 < w]
        v = [p[field] for p in s if p[field] is not None]
        return sum(v) / len(v)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9))
    for ax, field, ylab in ((axes[0], 'otd', 'on-time, %'),
                            (axes[1], 'thr', 'throughput, lots/day')):
        pair = {'QT': 'qt_s0', 'SLATE_inert': 'slate_inert_a'}
        for ent in by_slot(pair):
            key = pair[ent]
            ys = [mean_over(key, field, w) for w in windows]
            ax.plot(windows, ys, color=SLOT[ent][1], marker='o', markersize=5,
                    markeredgecolor=SURFACE, label=LABEL[ent])
            endpoint_label(ax, windows[-1], ys[-1], f'{ys[-1]:.1f}', SLOT[ent][1])
        ax.set_xticks(windows)
        ax.set_xlabel('window length read, days from day 90')
        ax.set_ylabel(ylab)
        ax.set_xlim(10, 205)
    axes[0].legend(loc='center right', fontsize=7.5)
    fig.suptitle('One SLATE run read over growing windows (means of '
                 'trailing-day samples)', x=0.02, ha='left', fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    save(fig, 'fig4_short_window')


# -- Fig 5: the queue-time term against the fab's actual windows -----------
def fig5():
    import numpy as np
    hours = np.logspace(np.log10(1 / 60), np.log10(400), 400)   # 1 min .. 400 h
    sec = hours * 3600
    curves = [
        ('cost(): 1 + 600/slack', 1 + 600 / np.maximum(sec, 60), '#2a78d6'),
        ('CP-SAT objective: 1 + 3600/slack', 1 + 3600 / np.maximum(sec, 60), '#eb6834'),
        ('window-relative, 10 h window', 1 + 600 / np.maximum(600 * sec / (10 * 3600), 60), '#1baf7a'),
        ('window-relative, 240 h window', 1 + 600 / np.maximum(600 * sec / (240 * 3600), 60), '#eda100'),
    ]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.axvspan(10, 240, color=GRID, alpha=0.6, lw=0)
    ax.text(110, 14, 'this fab\'s windows\n10-240 h', fontsize=7.5, color=INK2,
            ha='center')
    for lab, y, col in curves:
        ax.plot(hours, y, color=col, label=lab)
    ax.axvline(16.2, color=AXIS, lw=0.8)
    ax.text(16.2, 62, ' p75 slack of saveable\n at-risk lots: 16 h',
            fontsize=7, color=INK2, va='top')
    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.set_xlabel('slack remaining, hours')
    ax.set_ylabel('priority boost, x')
    ax.set_xlim(1 / 60, 400)
    ax.set_ylim(1.0, 70)
    ax.legend(loc='lower left', fontsize=7.5)
    ax.set_title('Why the solver could not see queue time: '
                 'the term is written for minute-scale windows',
                 loc='left', fontsize=10)
    save(fig, 'fig5_qtime_term')


# -- Fig 6: tardiness, all configurations, log scale ----------------------
def fig6():
    cfg = {'FIFO': 'fifo_s0', 'CR': 'cr_s0', 'QT': 'qt_s0', 'QT_tuned': 'qt50',
           'SLATE_inert': 'slate_inert_a', 'SLATE_fixed': 'slate_fixed_a',
           'SLATE_sym': 'slate_sym'}
    rows = sorted(((DATA[k]['tardiness_lot_days'], e) for e, k in cfg.items()),
                  reverse=True)
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    ys = range(len(rows))
    ax.barh(list(ys), [v for v, _ in rows], color='#2a78d6', height=0.62,
            edgecolor=SURFACE)
    for y, (v, e) in zip(ys, rows):
        ax.annotate(f'{v:,.0f}', (v, y), xytext=(4, 0), textcoords='offset points',
                    fontsize=7.5, color=INK2, va='center')
    ax.set_yticks(list(ys))
    ax.set_yticklabels([LABEL[e] for _, e in rows], fontsize=7.5)
    ax.set_xscale('log')
    ax.set_xlabel('total tardiness over the window, lot-days (log scale)')
    ax.set_xlim(40, 5e5)
    ax.invert_yaxis()
    ax.grid(axis='y', visible=False)
    ax.set_title('Total lateness, seed 0, 180-day window', loc='left', fontsize=10)
    save(fig, 'fig6_tardiness')


# -- Fig 7: the headline -- per seed, QT tuned vs three SLATE replicates -----
def fig7():
    seeds = [0, 1, 2]
    def reps(sd):
        return [DATA[k] for k in (f'slate_s{sd}_a', f'slate_s{sd}_b', f'slate_s{sd}_c')
                if k in DATA]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.9))
    panels = [('on_time_pct', 'on-time, %', False),
              ('tardiness_lot_days', 'total tardiness, lot-days', False),
              ('part_spread', 'per-product spread, pts', False)]
    for ax, (key, ylab, logy) in zip(axes, panels):
        for sd in seeds:
            q = DATA.get(f'qt50_s{sd}')
            rs = [r[key] for r in reps(sd) if r.get(key) is not None]
            if q:
                ax.plot([sd - 0.12], [q[key]], marker='D', markersize=6, linestyle='',
                        color=SLOT['QT_tuned'][1], markeredgecolor=SURFACE,
                        label=LABEL['QT_tuned'] if sd == 0 else None)
            if rs:
                ax.plot([sd + 0.12] * len(rs), rs, marker='o', markersize=5, linestyle='',
                        color=SLOT['SLATE_sym'][1], markeredgecolor=SURFACE, alpha=0.9,
                        label=LABEL['SLATE_sym'] if sd == 0 else None)
                if len(rs) > 1:
                    ax.plot([sd + 0.12] * 2, [min(rs), max(rs)], color=SLOT['SLATE_sym'][1],
                            lw=1.2, alpha=0.6)
        ax.set_xticks(seeds)
        ax.set_xticklabels([str(s) for s in seeds])
        ax.set_xlabel('seed')
        ax.set_xlim(-0.6, 2.6)
        ax.set_ylabel(ylab)
        if key == 'tardiness_lot_days':
            ax.set_ylim(bottom=0)
        ax.grid(axis='x', visible=False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', ncol=2, fontsize=7.5,
               bbox_to_anchor=(0.5, -0.04), frameon=False)
    fig.suptitle('Tuned sort key against the solver: three seeds, three solver '
                 'replicates per seed', x=0.02, ha='left', fontsize=10)
    fig.tight_layout(rect=[0, 0.06, 1, 0.92], w_pad=2.0)
    save(fig, 'fig7_headline')


for f in (fig1, fig2, fig3, fig4, fig5, fig6, fig7):
    f()
