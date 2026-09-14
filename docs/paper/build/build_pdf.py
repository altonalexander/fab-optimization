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


TABLES = {
    'table_seeds': t_seeds, 'table_main': t_main, 'table_parts': t_parts,
    'table_parts_ct': t_parts_ct, 'table_boost': t_boost, 'table_mech': t_mech,
    'table_window': t_window, 'table_fab': t_fab,
}

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
