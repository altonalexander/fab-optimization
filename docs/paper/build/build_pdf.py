#!/usr/bin/env python3
"""Build the paper: paper.md.in -> paper.md -> paper.html -> paper.pdf.

Every table is generated here from paper_data.json, so no result number is
typed by hand and the whole document regenerates from the raw result files:

    python3 docs/paper/build/paper_data.py     # consolidate results
    python3 docs/paper/build/figures.py        # render figures
    python3 docs/paper/build/build_pdf.py      # tables, markdown, html, pdf
"""
import json
import os
import re
import subprocess

import markdown
from weasyprint import HTML

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.dirname(HERE)
D = json.load(open(os.path.join(HERE, 'paper_data.json')))
F = json.load(open(os.path.join(HERE, 'fab_facts.json')))
SHA = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True,
                     text=True, cwd=PAPER).stdout.strip()


def md_table(header, rows, align=None):
    align = align or ['l'] + ['r'] * (len(header) - 1)
    sep = ['---' if a == 'l' else '---:' for a in align]
    out = ['| ' + ' | '.join(header) + ' |', '| ' + ' | '.join(sep) + ' |']
    for r in rows:
        out.append('| ' + ' | '.join(str(x) for x in r) + ' |')
    return '\n'.join(out)


def f1(x): return f'{x:.1f}'
def f2(x): return f'{x:.2f}'
def pct(x): return f'{x:.2f}%'
def big(x): return f'{x:,.0f}'
def sgn(x): return f'{x:+.2f}'


# -- Table: three-seed viability --------------------------------------------
def t_seeds():
    rows = []
    for s in (0, 1, 2):
        for rule in ('fifo', 'cr', 'qt'):
            r = D[f'{rule}_s{s}']
            rows.append([s, rule.upper(), f1(r['good_per_day']), pct(r['on_time_pct']),
                         f1(r['cycle_time_days']), big(r['tardiness_lot_days']),
                         f1(r['scrap_per_day']), f1(r['violations_per_day']),
                         f"{r['wip_first']:,}→{r['wip_last']:,}",
                         sgn(r['wip_slope_final_third'])])
    return md_table(['seed', 'rule', 'good/day', 'on-time', 'CT (d)', 'tardiness',
                     'scrap/day', 'viol/day', 'WIP', 'slope, final ⅓'], rows)


# -- Table: main comparison, seed 0 ----------------------------------------
MAIN = [('fifo_s0', 'FIFO'), ('cr_s0', 'CR'), ('qt_s0', 'QT'),
        ('qt50', 'QT, promote < 50 % of window'), ('qt25', 'QT, promote < 25 % of window'),
        ('slate_inert_a', 'SLATE, q-time term inert (a)'),
        ('slate_inert_b', 'SLATE, q-time term inert (b)'),
        ('slate_fixed_a', 'SLATE, window-relative, untuned fallback (a)'),
        ('slate_fixed_b', 'SLATE, window-relative, untuned fallback (b)'),
        ('slate_sym', 'SLATE, window-relative, tuned fallback')]


def t_main():
    rows = []
    for k, lab in MAIN:
        r = D[k]
        rows.append([lab, f1(r['good_per_day']), pct(r['on_time_pct']),
                     f1(r['cycle_time_days']), big(r['tardiness_lot_days']),
                     f1(r['scrap_per_day']), f1(r['violations_per_day']),
                     f1(r['util_pct']), f"{r['wip_last']:,}",
                     sgn(r['wip_slope_final_third']), f"{r['wall_s']:,.0f}"])
    return md_table(['configuration', 'good/day', 'on-time', 'CT (d)', 'tardiness',
                     'scrap/day', 'viol/day', 'util %', 'end WIP', 'slope', 'wall (s)'],
                    rows)


# -- Table: per-part on-time ------------------------------------------------
PART_COLS = [('qt_s0', 'QT'), ('qt50', 'QT tuned'), ('slate_inert_a', 'SLATE inert'),
             ('slate_fixed_a', 'SLATE fixed (a)'), ('slate_fixed_b', 'SLATE fixed (b)'),
             ('slate_sym', 'SLATE sym')]


def t_parts():
    parts = sorted(D['qt_s0']['by_part'],
                   key=lambda p: D['qt_s0']['by_part'][p]['on_time_pct'])
    rows = []
    for p in parts:
        row = [p.replace('part_', 'product ')]
        for k, _ in PART_COLS:
            row.append(pct(D[k]['by_part'][p]['on_time_pct']))
        rows.append(row)
    spread = ['**spread (max − min)**']
    for k, _ in PART_COLS:
        spread.append(f"**{D[k]['part_spread']:.1f} pts**")
    rows.append(spread)
    fab = ['fab-wide']
    for k, _ in PART_COLS:
        fab.append(pct(D[k]['on_time_pct']))
    rows.append(fab)
    return md_table(['product'] + [lab for _, lab in PART_COLS], rows)


def t_parts_ct():
    parts = sorted(D['qt_s0']['by_part'],
                   key=lambda p: D['qt_s0']['by_part'][p]['on_time_pct'])
    rows = []
    for p in parts:
        row = [p.replace('part_', 'product '), F['route_steps'][p],
               F['cqt_steps_per_product'][p]]
        for k in ('qt_s0', 'qt50', 'slate_sym'):
            bp = D[k]['by_part'][p]
            row += [f1(bp['cycle_time_days']), big(bp['tardiness_lot_days'])]
        rows.append(row)
    return md_table(['product', 'route steps', 'q-time steps',
                     'QT CT', 'QT tard.', 'QT-tuned CT', 'QT-tuned tard.',
                     'SLATE-sym CT', 'SLATE-sym tard.'], rows)


# -- Table: the q-time term at this fab's scale ----------------------------
def t_boost():
    rows = []
    for lab, h in (('1 minute', 1 / 60), ('10 minutes', 1 / 6), ('1 hour', 1),
                   ('4 hours', 4), ('16 hours (p75 of saveable at-risk lots)', 16.2),
                   ('2 days', 48), ('10 days', 240)):
        s = h * 3600
        b600 = 1 + 600 / max(s, 60)
        b3600 = 1 + 3600 / max(s, 60)
        # window-relative, for a 24 h and a 240 h window
        w24 = 1 + 3600 / max(600 * s / (24 * 3600), 60)
        w240 = 1 + 3600 / max(600 * s / (240 * 3600), 60)
        rows.append([lab, f'{b600:.3f}×', f'{b3600:.3f}×',
                     f'{w24:.2f}×' if h <= 24 else '—', f'{w240:.2f}×'])
    return md_table(['slack remaining', '`cost()`, 600/slack', 'CP-SAT, 3600/slack',
                     'window-relative, 24 h window', 'window-relative, 240 h window'],
                    rows)


# -- Table: mechanism checks (40 cold days) --------------------------------
def t_mech():
    rows = []
    for k in ('dx_nocqt', 'dx_s8norw', 'dx_s8rw', 'dx_s4rw', 'cap_s4'):
        r = D[k]
        rows.append([r['label'], r['good'], f1(r['util_pct']), r['violations'],
                     r['reworks'], r['scrapped'], f"{r['wip_last']:,}"])
    return md_table(['arm (40 cold days, seed 0)', 'lots out', 'util %',
                     'violations', 'reworks', 'scrapped', 'end WIP'], rows)


# -- Table: the short-window trap -------------------------------------------
def t_window():
    def mean_over(key, field, w):
        s = [p for p in D[key]['series'] if p['day'] - 90 < w]
        v = [p[field] for p in s if p[field] is not None]
        return sum(v) / len(v)
    rows = []
    for w in (20, 60, 120, 180):
        rows.append([f'days 90–{90 + w}',
                     f1(mean_over('slate_inert_a', 'thr', w)),
                     f1(mean_over('slate_inert_a', 'otd', w)),
                     f1(mean_over('qt_s0', 'thr', w)),
                     f1(mean_over('qt_s0', 'otd', w))])
    return md_table(['window read', 'SLATE-inert thr/day', 'SLATE-inert on-time %',
                     'QT thr/day', 'QT on-time %'], rows)


# -- Table: testbed facts ----------------------------------------------------
def t_fab():
    rows = []
    for p in sorted(F['route_steps'], key=lambda x: int(x.split('_')[1])):
        rows.append([p.replace('part_', 'product '), F['route_steps'][p],
                     F['cqt_steps_per_product'][p], F['batch_steps_per_product'][p],
                     F['setup_steps_per_product'][p]])
    return md_table(['product', 'route steps', 'steps with a q-time window',
                     'batch steps', 'setup-bearing steps'], rows)


# -- Table: five-seed viability (one deterministic run per sort key) --------
def t_viability5():
    rows = []
    for sd in range(5):
        for key, lab in ((f'fifo_s{sd}', 'FIFO'), (f'cr_s{sd}', 'CR'),
                         (f'qt_s{sd}', 'QT'), (f'qt50_s{sd}', 'QT tuned')):
            r = D.get(key)
            if not r:
                rows.append([sd, lab] + ['—'] * 8)
                continue
            rows.append([sd, lab, f1(r['good_per_day']), pct(r['on_time_pct']),
                         f1(r['cycle_time_days']), big(r['tardiness_lot_days']),
                         f1(r['scrap_per_day']), f1(r['violations_per_day']),
                         f"{r['wip_first']:,}→{r['wip_last']:,}",
                         sgn(r['wip_slope_final_third'])])
    return md_table(['seed', 'rule', 'good/day', 'on-time', 'CT (d)', 'tardiness',
                     'scrap/day', 'viol/day', 'WIP', 'slope, final ⅓'], rows)


def _slate_reps(sd):
    return [D[k] for k in (f'slate_s{sd}_a', f'slate_s{sd}_b', f'slate_s{sd}_c') if k in D]


def _slate_rep_keys(sd):
    return [k for k in (f'slate_s{sd}_a', f'slate_s{sd}_b', f'slate_s{sd}_c') if k in D]


def _mmm(vals, fmt):
    """mean (min–max) over replicates, or the single value."""
    if not vals:
        return '—'
    if len(vals) == 1:
        return fmt(vals[0])
    return f'{fmt(sum(vals) / len(vals))} ({fmt(min(vals))}–{fmt(max(vals))})'


# -- Table: the paired comparison, per seed ---------------------------------
def t_paired():
    metrics = [('on_time_pct', 'on-time %', f2, +1), ('tardiness_lot_days', 'tardiness', big, -1),
               ('good_per_day', 'good/day', f1, +1), ('cycle_time_days', 'CT (d)', f1, -1),
               ('part_spread', 'per-product spread', f1, -1)]
    rows = []
    for sd in range(3):
        q = D.get(f'qt50_s{sd}')
        reps = _slate_reps(sd)
        for key, lab, fmt, better in metrics:
            qv = q[key] if q else None
            sv = [r[key] for r in reps if r.get(key) is not None]
            if qv is None or not sv:
                rows.append([sd, lab, fmt(qv) if qv is not None else '—',
                             _mmm(sv, fmt), '—', '—'])
                continue
            diffs = [v - qv for v in sv]
            allwin = all((d * better) > 0 for d in diffs)
            alllose = all((d * better) < 0 for d in diffs)
            verdict = 'solver' if allwin else ('rule' if alllose else 'mixed')
            if len(sv) == 1:
                verdict += ' (n=1)'
            rows.append([f'{sd} (n={len(sv)})' if key == 'on_time_pct' else '', lab, fmt(qv), _mmm(sv, fmt),
                         _mmm(diffs, lambda x: f'{x:+.2f}' if key == 'on_time_pct' else f'{x:+,.1f}'),
                         verdict])
    return md_table(['seed', 'metric', 'QT tuned', 'SLATE (mean, min–max)',
                     'SLATE − QT (mean, range)', 'better on every replicate'],
                    rows, align=['l', 'l', 'r', 'r', 'r', 'l'])


# -- Table: seed difficulty, read off the untuned rule -----------------------
def t_difficulty():
    rows = []
    for sd in range(5):
        u, q = D.get(f'qt_s{sd}'), D.get(f'qt50_s{sd}')
        reps = _slate_reps(sd)
        sv = [r['on_time_pct'] for r in reps]
        rows.append([sd, f"{u['wip_first']:,}", pct(u['on_time_pct']), big(u['tardiness_lot_days']),
                     pct(q['on_time_pct']), big(q['tardiness_lot_days']),
                     (f'{_mmm(sv, pct)} (n={len(sv)})' if sv else 'not run'),
                     (f'{(sum(sv) / len(sv)) - q["on_time_pct"]:+.2f}' if sv else '—')])
    return md_table(['seed', 'WIP at day 90', 'QT on-time', 'QT tardiness',
                     'QT tuned on-time', 'QT tuned tardiness', 'SLATE on-time', 'SLATE − QT tuned'],
                    rows)


# -- Table: every solver replicate, raw ---------------------------------------
def t_slate_reps():
    rows = []
    for sd in range(3):
        q = D.get(f'qt50_s{sd}')
        rows.append([sd, 'QT tuned', f1(q['good_per_day']), pct(q['on_time_pct']),
                     f1(q['cycle_time_days']), big(q['tardiness_lot_days']),
                     f1(q['violations_per_day']), f1(q['scrap_per_day']),
                     f1(q['part_spread']), sgn(q['wip_slope_final_third']), f"{q['wall_s']:,.0f}"])
        for k in _slate_rep_keys(sd):
            r = D[k]
            rows.append(['', f'SLATE {k[-1]}', f1(r['good_per_day']), pct(r['on_time_pct']),
                         f1(r['cycle_time_days']), big(r['tardiness_lot_days']),
                         f1(r['violations_per_day']), f1(r['scrap_per_day']),
                         f1(r['part_spread']), sgn(r['wip_slope_final_third']), f"{r['wall_s']:,.0f}"])
    return md_table(['seed', 'run', 'good/day', 'on-time', 'CT (d)', 'tardiness', 'viol/day',
                     'scrap/day', 'spread', 'slope', 'wall (s)'], rows)


# -- Table: what the solver decides (candidate-set histogram) ----------------
def t_coverage():
    r = D['slate_s1_c']
    h = r['candidate_hist']
    order = [('1', 'exactly 1 lot'), ('1+idle', '1 lot, other tools in the family idle'),
             ('2', '2 lots'), ('3-5', '3–5 lots'), ('6+', '6 or more lots')]
    rows = []
    tot = sum(v['fallback'] + v['covered'] for v in h.values())
    for k, lab in order:
        c, f = h[k]['covered'], h[k]['fallback']
        rows.append([lab, big(c + f), f'{100 * (c + f) / tot:.1f}%', f'{100 * c / (c + f):.1f}%'])
    ch = sum(h[k]['covered'] + h[k]['fallback'] for k in ('2', '3-5', '6+'))
    cc = sum(h[k]['covered'] for k in ('2', '3-5', '6+'))
    rows.append(['**all decisions with a choice (≥ 2 lots)**', f'**{big(ch)}**',
                 f'**{100 * ch / tot:.1f}%**', f'**{100 * cc / ch:.1f}%**'])
    rows.append(['all decisions', big(tot), '100%', f"{100 * r['coverage']:.1f}%"])
    return md_table(['candidate set when the tool freed', 'decisions', 'share of all',
                     'share made by the solver'], rows)


# -- Table: the window-open deviation, sized from the dataset ----------------
def t_cqt_dev():
    import csv, glob, statistics
    ds = os.path.join(PAPER, '..', '..', 'baselines', 'pyscfabsim', 'datasets', 'SMT2020_LVHM')
    units = {'sec': 1, 's': 1, 'min': 60, 'hr': 3600, 'day': 86400}
    fr, wins = [], []
    for f in sorted(glob.glob(os.path.join(ds, 'route_*.txt'))):
        steps = list(csv.DictReader(open(f), delimiter='\t'))
        by = {st['STEP']: st for st in steps}
        # STEP_CQT points FORWARD: the step carrying it is the ENTRANCE (the
        # window opens when it completes) and the step it names is the exit.
        # The first version of this table had the roles reversed and measured
        # the exit step's time; corrected 2026-09-15.
        for st in steps:
            c = st.get('STEP_CQT', '')
            if not c or c not in by:
                continue
            w = float(st['CQT']) * units[st['CQTUNITS']]
            pt = float(st['PTIME']) * units[st['PTUNITS']]
            if st.get('PTPER') == 'per_piece':
                pt *= 25
            fr.append(pt / w)
            wins.append(w)
    fr.sort()
    p90 = fr[int(0.9 * len(fr))]
    rows = [
        ['window length, min / median / max (h)',
         f'{min(wins) / 3600:.0f} / {statistics.median(wins) / 3600:.0f} / {max(wins) / 3600:.0f}',
         f'{10 * min(wins) / 3600:.0f} / {10 * statistics.median(wins) / 3600:.0f} / {10 * max(wins) / 3600:.0f}'],
        ['entrance-step time ÷ window, median', f'{100 * statistics.median(fr):.0f}%', f'{100 * statistics.median(fr) / 10:.1f}%'],
        ['mean', f'{100 * statistics.mean(fr):.0f}%', f'{100 * statistics.mean(fr) / 10:.1f}%'],
        ['90th percentile', f'{100 * p90:.0f}%', f'{100 * p90 / 10:.1f}%'],
        ['worst of the 264 pairs', f'{100 * max(fr):.0f}%', f'{100 * max(fr) / 10:.0f}%'],
    ]
    return md_table(['', 'native windows (scale 1)', 'as run (scale 10)'], rows)


# -- Table: determinism of the sort key -------------------------------------
def t_determinism():
    a, b = D.get('qt50_s0'), D.get('qt50_s0_repeat')
    if not (a and b):
        return '_(repeat run not yet available)_'
    same = a['fingerprint'] == b['fingerprint']
    return md_table(['run', 'good/day', 'on-time', 'tardiness', 'fingerprint'],
                    [['QT tuned, seed 0', f1(a['good_per_day']), pct(a['on_time_pct']),
                      big(a['tardiness_lot_days']), f'`{a["fingerprint"]}`'],
                     ['QT tuned, seed 0, repeat', f1(b['good_per_day']), pct(b['on_time_pct']),
                      big(b['tardiness_lot_days']), f'`{b["fingerprint"]}`'],
                     ['identical?', '', '', '', '**yes**' if same else '**NO**']])


TABLES = {
    'table_viability5': t_viability5, 'table_paired': t_paired,
    'table_determinism': t_determinism, 'table_difficulty': t_difficulty,
    'table_slate_reps': t_slate_reps, 'table_coverage': t_coverage, 'table_cqt_dev': t_cqt_dev,
    'table_seeds': t_seeds, 'table_main': t_main, 'table_parts': t_parts,
    'table_parts_ct': t_parts_ct, 'table_boost': t_boost, 'table_mech': t_mech,
    'table_window': t_window, 'table_fab': t_fab,
}

def main():
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == '--table':
        print(TABLES[sys.argv[2]]())
        return
    build()


def build():
    src = open(os.path.join(PAPER, 'paper.md.in'), encoding='utf-8').read()
    src = src.replace('{{git_sha}}', SHA)
    for name, fn in TABLES.items():
        src = src.replace('{{' + name + '}}', fn())
    missing = re.findall(r'\{\{(\w+)\}\}', src)
    if missing:
        raise SystemExit(f'unfilled placeholders: {missing}')
    open(os.path.join(PAPER, 'paper.md'), 'w', encoding='utf-8').write(src)

    body = markdown.markdown(src, extensions=['tables', 'fenced_code', 'toc',
                                              'attr_list', 'md_in_html',
                                              'footnotes', 'smarty'])
    CSS = """
    @page { size: A4; margin: 22mm 20mm 24mm 20mm;
      @bottom-center { content: counter(page); font: 9pt 'DejaVu Sans', sans-serif; color: #52514e; } }
    body { font: 10.4pt/1.42 'DejaVu Serif', Georgia, serif; color: #0b0b0b; }
    h1 { font: 700 19pt/1.2 'DejaVu Sans', sans-serif; margin: 0 0 6pt; }
    h2 { font: 700 13pt/1.25 'DejaVu Sans', sans-serif; margin: 20pt 0 6pt; page-break-after: avoid; }
    h3 { font: 700 11pt/1.25 'DejaVu Sans', sans-serif; margin: 14pt 0 4pt; page-break-after: avoid; }
    p { margin: 0 0 8pt; text-align: justify; hyphens: auto; }
    .meta { font: 9.5pt 'DejaVu Sans', sans-serif; color: #52514e; margin-bottom: 14pt; }
    .abstract { font-size: 9.6pt; margin: 10pt 18pt 16pt; padding: 10pt 12pt; border-top: 0.5pt solid #c3c2b7; border-bottom: 0.5pt solid #c3c2b7; }
    .abstract p { text-align: justify; }
    table { border-collapse: collapse; width: 100%; font: 8.2pt/1.3 'DejaVu Sans', sans-serif; margin: 8pt 0 10pt; page-break-inside: avoid; }
    th, td { padding: 3pt 5pt; border-bottom: 0.5pt solid #e1e0d9; vertical-align: top; }
    th { border-bottom: 0.8pt solid #c3c2b7; text-align: left; font-weight: 700; color: #0b0b0b; }
    td { font-variant-numeric: tabular-nums; }
    figure { margin: 12pt 0 14pt; page-break-inside: avoid; text-align: center; }
    figure img { max-width: 100%; }
    figcaption { font: 8.6pt/1.35 'DejaVu Sans', sans-serif; color: #52514e; text-align: left; margin-top: 6pt; }
    code { font: 8.6pt 'DejaVu Sans Mono', monospace; background: #f3f2ee; padding: 0 2pt; }
    pre { font: 8.2pt/1.35 'DejaVu Sans Mono', monospace; background: #f3f2ee; padding: 6pt 8pt; overflow-x: auto; page-break-inside: avoid; }
    pre code { background: none; padding: 0; }
    blockquote { margin: 8pt 14pt; padding-left: 8pt; border-left: 2pt solid #e1e0d9; color: #52514e; }
    ul, ol { margin: 0 0 8pt 18pt; padding: 0; }
    li { margin-bottom: 3pt; }
    .footnote { font-size: 8.6pt; }
    .refs p { font-size: 9.2pt; text-align: left; margin-left: 14pt; text-indent: -14pt; }
    sup { font-size: 7pt; }
    """
    html = f'<!doctype html><meta charset="utf-8"><title>paper</title><style>{CSS}</style><body>{body}</body>'
    open(os.path.join(PAPER, 'paper.html'), 'w', encoding='utf-8').write(html)
    HTML(string=html, base_url=PAPER).write_pdf(os.path.join(PAPER, 'paper.pdf'))
    print('wrote paper.md, paper.html, paper.pdf  (sha', SHA + ')')


main()
