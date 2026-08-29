"""Build a self-contained HTML dashboard from the greedy/*.json simulation results.

Usage:  .venv/bin/python build_dashboard.py [--days 730] [--out dashboard.html]

Reads every greedy/greedy_seed<N>_<days>days_<DATASET>_<dispatcher>.json produced
by greedy_runner.py, aggregates across seeds, and writes one offline HTML file.
"""
import argparse
import glob
import json
import os
import re
import statistics
from collections import defaultdict

DAY = 86400.0
RESET = 31536000.0  # 1-year warm-up discarded by ResetEvent

FNAME = re.compile(r'greedy_seed(?P<seed>\w+)_(?P<days>\d+)days_(?P<ds>SMT2020_\w+?)_(?P<disp>\w+)\.json$')

# dataviz reference palette, validated for light+dark (see build notes)
SERIES_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']
SERIES_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500']

DISPATCHER_LABEL = {
    'fifo': 'FIFO',
    'cr': 'Critical Ratio',
    'random': 'Random',
    'lifo_org': 'LIFO',
    'lifo_anders': 'LIFO (alt)',
}


def load_runs(days):
    """Return {(dataset, dispatcher): [run, ...]} for runs of the given length."""
    runs = defaultdict(list)
    for path in sorted(glob.glob('greedy/*.json')):
        m = FNAME.search(os.path.basename(path))
        if not m or int(m.group('days')) != days:
            continue
        with open(path) as f:
            data = json.load(f)
        data['_seed'] = m.group('seed')
        data['_path'] = path
        runs[(m.group('ds'), m.group('disp'))].append(data)
    return runs


def scenario_metrics(run, days):
    """Collapse one run into headline numbers. Returns None if the run lacks
    the fields this dashboard reports (older result files are not comparable)."""
    lots = run['lots']
    if not lots or any('throughput_one_year' not in v for v in lots.values()):
        return None

    total_ty = sum(v['throughput_one_year'] for v in lots.values())
    total_th = sum(v['throughput'] for v in lots.values())
    on_time = sum(v['on_time'] for v in lots.values())
    if total_ty <= 0:
        return None

    # ACT is a per-lot-type mean in days; weight by that type's volume.
    act = sum(v['ACT'] * v['throughput_one_year'] for v in lots.values()) / total_ty
    tardiness = sum(v['tardiness'] for v in lots.values()) / total_ty / DAY

    comp = {}
    for key, label in [('processing_time', 'Processing'), ('waiting_time', 'Queueing'),
                       ('waiting_time_batching', 'Batching'), ('transport_time', 'Transport')]:
        comp[label] = sum(v[key] for v in lots.values()) / total_ty / DAY

    machines = run.get('machines', {})
    utils = [m['util'] for m in machines.values() if 'util' in m]

    return dict(
        on_time_pct=on_time / total_ty * 100.0,
        act=act,
        throughput_per_day=total_th / days,
        tardiness_days=tardiness,
        cost=run.get('plugins', {}).get('cost'),
        composition=comp,
        mean_util=statistics.mean(utils) * 100 if utils else None,
        machines=machines,
        lots=lots,
    )


def agg(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], 0.0
    return statistics.mean(values), statistics.stdev(values)


# ---------------------------------------------------------------- SVG helpers

def esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def grouped_bars(categories, series, unit='', width=680, height=300, pad_left=132):
    """Horizontal grouped bars. series = [(name, [v per category]), ...]."""
    if not categories or not series:
        return '<p class="empty">No data.</p>'
    n_cat, n_ser = len(categories), len(series)
    top, bottom, right = 12, 28, 56
    plot_w = width - pad_left - right
    band = (height - top - bottom) / n_cat
    bar_h = min(18, (band - 8) / n_ser)
    vmax = max([v for _, vals in series for v in vals if v is not None] or [1]) or 1

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    # recessive gridlines
    for i in range(5):
        x = pad_left + plot_w * i / 4
        out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="{height-bottom+16}" class="tick mid">{vmax*i/4:.6g}</text>')
    for ci, cat in enumerate(categories):
        y0 = top + band * ci
        out.append(f'<text x="{pad_left-10}" y="{y0+band/2+4:.1f}" class="tick end">{esc(cat)}</text>')
        for si, (name, vals) in enumerate(series):
            v = vals[ci]
            if v is None:
                continue
            w = plot_w * (v / vmax)
            y = y0 + (band - bar_h * n_ser) / 2 + si * bar_h
            # 2px surface gap between adjacent bars
            out.append(
                f'<rect x="{pad_left}" y="{y+1:.1f}" width="{max(w,0.5):.1f}" height="{bar_h-2:.1f}" '
                f'rx="4" class="s{si}"><title>{esc(name)} · {esc(cat)}: {v:.3g}{esc(unit)}</title></rect>')
            if w > 46:
                out.append(f'<text x="{pad_left+w-6:.1f}" y="{y+bar_h/2+3:.1f}" class="barlab end">{v:.3g}</text>')
    out.append('</svg>')
    return ''.join(out)


def stacked_bars(categories, segments, unit=' d', width=680, height=260, pad_left=132):
    """Horizontal stacked bars. segments = [(name, [v per category]), ...]."""
    if not categories:
        return '<p class="empty">No data.</p>'
    top, bottom, right = 12, 28, 56
    plot_w = width - pad_left - right
    band = (height - top - bottom) / len(categories)
    bar_h = min(26, band - 12)
    totals = [sum(vals[i] or 0 for _, vals in segments) for i in range(len(categories))]
    vmax = max(totals or [1]) or 1

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    for i in range(5):
        x = pad_left + plot_w * i / 4
        out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="{height-bottom+16}" class="tick mid">{vmax*i/4:.3g}</text>')
    for ci, cat in enumerate(categories):
        y = top + band * ci + (band - bar_h) / 2
        out.append(f'<text x="{pad_left-10}" y="{y+bar_h/2+4:.1f}" class="tick end">{esc(cat)}</text>')
        x = pad_left
        for si, (name, vals) in enumerate(segments):
            v = vals[ci] or 0
            w = plot_w * (v / vmax)
            if w <= 0:
                continue
            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w-2,0.5):.1f}" height="{bar_h:.1f}" '
                f'rx="3" class="s{si}"><title>{esc(name)} · {esc(cat)}: {v:.3g}{esc(unit)}</title></rect>')
            if w > 52:
                out.append(f'<text x="{x+w/2-1:.1f}" y="{y+bar_h/2+4:.1f}" class="barlab mid">{v:.3g}</text>')
            x += w
        out.append(f'<text x="{x+8:.1f}" y="{y+bar_h/2+4:.1f}" class="tick start">{totals[ci]:.3g}{esc(unit)}</text>')
    out.append('</svg>')
    return ''.join(out)


def legend(names):
    items = ''.join(
        f'<span class="lg"><i class="sw s{i}"></i>{esc(n)}</span>' for i, n in enumerate(names))
    return f'<div class="legend">{items}</div>'


# ---------------------------------------------------------------- Gantt view

ROW_RE = re.compile(
    r"\['((?:[^'\\]|\\.)*)', '((?:[^'\\]|\\.)*)', 'fill-color: (#[0-9A-Fa-f]{6})',"
    r" new Date\(([0-9.eE+-]+)\), new Date\(([0-9.eE+-]+)\)\]")


def parse_gantt(path, horizon=None):
    """Pull (row, label, start, end) tuples out of a ChartPlugin timeline file.

    The upstream template carries commented-out sentinel rows (row label '_' with
    epoch-scale dates); those are dropped."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        src = f.read()
    out = []
    for row, label, _color, t0, t1 in ROW_RE.findall(src):
        if row == '_':
            continue
        # ChartPlugin emits JS milliseconds (it multiplies sim seconds by 1000).
        a, b = float(t0) / 1000.0, float(t1) / 1000.0
        if b <= a or a > 1e8:
            continue
        if horizon and a > horizon:
            continue
        out.append((row, label, a, min(b, horizon) if horizon else b))
    return out


def gantt_svg(bars, family_of, families, n_rows=24, width=680, row_h=15, pad_left=132):
    """Render a timeline slice as inline SVG. Rows are ordered by busy time."""
    if not bars:
        return '<p class="empty">No timeline data.</p>'
    busy = defaultdict(float)
    for row, _l, a, b in bars:
        busy[row] += b - a
    rows = [r for r, _ in sorted(busy.items(), key=lambda kv: -kv[1])[:n_rows]]
    keep = set(rows)
    bars = [x for x in bars if x[0] in keep]
    tmax = max(b for _r, _l, _a, b in bars) or 1

    top, bottom, right = 10, 30, 16
    height = top + bottom + row_h * len(rows)
    plot_w = width - pad_left - right
    fam_idx = {f: i for i, f in enumerate(families)}

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    for i in range(5):
        x = pad_left + plot_w * i / 4
        out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="{height-bottom+16}" class="tick mid">'
                   f'{tmax*i/4/3600:.1f}h</text>')
    for ri, row in enumerate(rows):
        y = top + row_h * ri
        out.append(f'<text x="{pad_left-10}" y="{y+row_h/2+4:.1f}" class="tick end">{esc(row)}</text>')
        out.append(f'<rect x="{pad_left}" y="{y+1:.1f}" width="{plot_w}" height="{row_h-3:.1f}" '
                   f'rx="3" class="lane"/>')
    ypos = {r: top + row_h * i for i, r in enumerate(rows)}
    for row, label, a, b in bars:
        y = ypos[row]
        x = pad_left + plot_w * (a / tmax)
        w = max(plot_w * ((b - a) / tmax), 1.0)
        si = fam_idx.get(family_of(row, label), 0)
        out.append(f'<rect x="{x:.2f}" y="{y+1:.1f}" width="{w:.2f}" height="{row_h-3:.1f}" '
                   f'rx="2" class="s{si}"><title>{esc(row)} · {esc(label)}: '
                   f'{a/3600:.2f}h → {b/3600:.2f}h</title></rect>')
    out.append('</svg>')
    return ''.join(out)


# ------------------------------------------------------------------ page body

HEAD = ('<title>Fab Dispatch Bench</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">')

CSS = """
*{box-sizing:border-box}
:root{
  color-scheme:light;
  /* neutrals carry a slight cool bias - silicon, not flat grey */
  --bg:#f4f6f7; --surface-1:#fdfdfe; --border:#dfe4e8; --lane:#eef1f3;
  --text-primary:#0d1117; --text-secondary:#4a555f; --text-muted:#7d8894;
  --s0:#2a78d6; --s1:#eb6834; --s2:#1baf7a; --s3:#eda100;
  --display:"IBM Plex Sans Condensed",ui-sans-serif,system-ui,sans-serif;
  --body:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  color-scheme:dark;
  --bg:#0f1214; --surface-1:#171b1e; --border:#2b3238; --lane:#1f2428;
  --text-primary:#f2f5f7; --text-secondary:#b3bec7; --text-muted:#7f8b95;
  --s0:#3987e5; --s1:#d95926; --s2:#199e70; --s3:#c98500;
}}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#0f1214; --surface-1:#171b1e; --border:#2b3238; --lane:#1f2428;
  --text-primary:#f2f5f7; --text-secondary:#b3bec7; --text-muted:#7f8b95;
  --s0:#3987e5; --s1:#d95926; --s2:#199e70; --s3:#c98500;
}
body{margin:0;background:var(--bg);color:var(--text-primary);font-family:var(--body);
  font-size:15px;line-height:1.62}
.viz-root{padding:52px 24px 80px}
.wrap{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:2px}
h1{font-family:var(--display);font-size:38px;line-height:1.1;font-weight:700;
  margin:0 0 10px;letter-spacing:-.01em;text-wrap:balance}
h2{font-family:var(--display);font-size:24px;font-weight:600;margin:48px 0 4px;
  letter-spacing:-.005em;text-wrap:balance}
h3{font-family:var(--display);font-size:16px;font-weight:600;margin:26px 0 6px;
  color:var(--text-primary);letter-spacing:.005em}
.card h3{margin-top:0}
p{margin:10px 0;color:var(--text-secondary);max-width:68ch}
.sub{color:var(--text-muted);font-size:13px;margin:0 0 4px;font-family:var(--mono);
  letter-spacing:.01em}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;
  padding:22px 24px;margin:16px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(172px,1fr));gap:12px;margin:14px 0}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:16px 18px}
.tile .k{font-family:var(--mono);font-size:11px;text-transform:uppercase;
  letter-spacing:.08em;color:var(--text-muted)}
.tile .v{font-family:var(--display);font-size:31px;font-weight:700;letter-spacing:-.01em;
  margin-top:6px;font-variant-numeric:tabular-nums;color:var(--text-primary)}
.tile .d{font-size:12.5px;color:var(--text-secondary);margin-top:3px}
.chart{width:100%;height:auto;display:block;overflow:visible;margin-top:4px}
.grid{stroke:var(--border);stroke-width:1}
.tick{fill:var(--text-muted);font-size:11.5px;font-family:var(--mono)}
.tick.end{text-anchor:end}.tick.mid{text-anchor:middle}.tick.start{text-anchor:start}
.barlab{font-size:11px;fill:var(--text-primary);font-family:var(--mono);
  font-variant-numeric:tabular-nums}
.barlab.end{text-anchor:end}.barlab.mid{text-anchor:middle}
rect.s0{fill:var(--s0)}rect.s1{fill:var(--s1)}rect.s2{fill:var(--s2)}rect.s3{fill:var(--s3)}
rect.lane{fill:var(--lane)}
rect[class^="s"]:hover{stroke:var(--surface-1);stroke-width:2}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:8px 0 12px;font-size:13px;
  color:var(--text-secondary)}
.lg{display:flex;align-items:center;gap:7px}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block;flex:none}
.sw.s0{background:var(--s0)}.sw.s1{background:var(--s1)}
.sw.s2{background:var(--s2)}.sw.s3{background:var(--s3)}
.tblwrap{overflow-x:auto}
table{border-collapse:collapse;font-size:13.5px;min-width:100%}
th,td{text-align:right;padding:8px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;
  letter-spacing:.07em;font-family:var(--mono)}
td{font-variant-numeric:tabular-nums;font-family:var(--mono);font-size:13px;
  color:var(--text-primary)}
td:first-child{font-family:var(--body);font-size:13.5px}
tbody tr:last-child td{border-bottom:none}
.note{border-left:2px solid var(--s3);background:var(--surface-1);border-radius:0 8px 8px 0;
  padding:13px 17px;margin:16px 0;font-size:14px;color:var(--text-secondary)}
.note b{color:var(--text-primary)}
code{font-family:var(--mono);font-size:12.5px;background:var(--bg);
  border:1px solid var(--border);border-radius:4px;padding:1px 5px}
.empty{color:var(--text-muted);font-style:italic}
a{color:var(--s0)}
:focus-visible{outline:2px solid var(--s0);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media (max-width:640px){.viz-root{padding:32px 16px 56px}h1{font-size:30px}}
"""


def n_seeds_global(scenarios):
    return max((len(v) for v in scenarios.values()), default=0)


def scenario_metrics_agg(ms, key):
    return agg([m[key] for m in ms])[0]


def tile(k, v, d=''):
    return (f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>'
            f'<div class="d">{esc(d)}</div></div>')


def build(days, out_path):
    runs = load_runs(days)

    scenarios = {}   # (ds, disp) -> list of metric dicts, one per seed
    skipped = []
    for key, rs in sorted(runs.items()):
        ms = []
        for r in rs:
            m = scenario_metrics(r, days)
            (ms.append(m) if m else skipped.append(r['_path']))
        if ms:
            scenarios[key] = ms

    H = ['<div class="viz-root"><div class="wrap">']
    H.append('<h1>Semiconductor Fab Dispatching &mdash; Simulation Results</h1>')
    H.append(f'<p class="sub">PySCFabSim-revised &middot; SMT2020 testbed &middot; '
             f'{days}-day horizon &middot; first 365 days discarded as warm-up</p>')

    if not scenarios:
        H.append('<div class="note"><b>No completed runs yet.</b> Run '
                 '<code>./reproduce_dispatcher_experiments.sh</code> first.</div>')
        H.append('</div></div>')
        with open(out_path, 'w') as f:
            f.write(HEAD + f'<style>{CSS}</style>' + ''.join(H))
        return out_path, 0

    # ---- data status -----------------------------------------------------
    expected = {(d, r) for d in {k[0] for k in scenarios}
                for r in ('fifo', 'cr')}
    missing = sorted(expected - set(scenarios))
    if missing or n_seeds_global(scenarios) < 2:
        bits = []
        if missing:
            bits.append('missing ' + ', '.join(
                esc(f'{d.replace("SMT2020_","")} {DISPATCHER_LABEL.get(r,r)}') for d, r in missing))
        if n_seeds_global(scenarios) < 2:
            bits.append('only one seed per scenario, so the spreads are not yet meaningful')
        H.append('<div class="note"><b>Partial data.</b> This page shows every completed run ('
                 + '; '.join(bits)
                 + '). Re-run <code>build_dashboard.py</code> once the sweep finishes to refresh.</div>')

    # ---- narrative -------------------------------------------------------
    H.append('<h2>What this is</h2>')
    H.append(
        '<p>A <b>wafer fab</b> is the hardest scheduling problem in manufacturing. A silicon '
        'lot makes hundreds of passes through the same few hundred machines, revisiting the '
        'same toolsets at different stages &mdash; so the queue you join depends on every '
        'decision made before it. Machines break down, need preventive maintenance, require '
        'setup changes between recipes, and some process wafers in batches that must be filled. '
        'Every time a machine frees up, something has to choose which waiting lot goes next. '
        'That choice is the <b>dispatching rule</b>, and it is made tens of thousands of times a day.</p>')
    H.append(
        '<p>This simulator replays a full virtual fab from the public <b>SMT2020</b> testbed '
        'so those rules can be compared on identical demand, identical breakdowns and identical '
        'machine sets &mdash; something impossible in a real $10B fab.</p>')

    H.append('<h2>The two rules under test</h2>')
    H.append(
        '<p><b>FIFO</b> takes whichever lot has waited longest. It is simple, predictable, and '
        'blind to due dates &mdash; a lot with three weeks of slack can outrank one that is '
        'already late. <b>Critical Ratio (CR)</b> instead ranks by '
        '<code>(deadline &minus; now) / remaining work</code>: below 1.0 a lot cannot finish on '
        'time even with zero queueing, so the most endangered lot goes first. Both rules run '
        'behind the same tie-breakers, which prefer a lot needing no setup change so the fab '
        'does not thrash between recipes.</p>')

    # ---- headline tiles --------------------------------------------------
    H.append('<h2>Headline results</h2>')
    order = sorted(scenarios.keys())
    n_seeds = max(len(v) for v in scenarios.values())
    H.append(f'<p>Each scenario is the mean of {n_seeds} independent seeds '
             f'(different breakdown and arrival draws).</p>')

    for key in order:
        ds, disp = key
        ms = scenarios[key]
        ot, ot_sd = agg([m['on_time_pct'] for m in ms])
        act, act_sd = agg([m['act'] for m in ms])
        th, _ = agg([m['throughput_per_day'] for m in ms])
        util, _ = agg([m['mean_util'] for m in ms])
        H.append(f'<h3>{esc(ds.replace("SMT2020_",""))} &middot; '
                 f'{esc(DISPATCHER_LABEL.get(disp,disp))}</h3>')
        H.append('<div class="tiles">')
        H.append(tile('On-time delivery', f'{ot:.1f}%', f'±{ot_sd:.1f} across seeds'))
        H.append(tile('Avg cycle time', f'{act:.1f} d', 'release to ship'))
        H.append(tile('Throughput', f'{th:.0f}/day', 'lots completed'))
        H.append(tile('Mean tool utilisation', f'{util:.1f}%' if util else 'n/a', 'post warm-up'))
        H.append('</div>')

    # ---- comparison charts, grouped per dataset --------------------------
    by_ds = defaultdict(list)
    for (ds, disp) in order:
        by_ds[ds].append(disp)

    for ds, disps in sorted(by_ds.items()):
        short = ds.replace('SMT2020_', '')
        H.append(f'<h2>{esc(short)}: rule comparison</h2>')
        names = [DISPATCHER_LABEL.get(d, d) for d in disps]

        if len(disps) > 1:
            cats = ['On-time %', 'Cycle time (d)', 'Throughput /day', 'Mean util %']
            series = []
            for i, disp in enumerate(disps):
                ms = scenarios[(ds, disp)]
                series.append((names[i], [
                    agg([m['on_time_pct'] for m in ms])[0],
                    agg([m['act'] for m in ms])[0],
                    agg([m['throughput_per_day'] for m in ms])[0],
                    agg([m['mean_util'] for m in ms])[0],
                ]))
            H.append('<div class="card">')
            H.append('<h3>Headline metrics side by side</h3>')
            H.append('<p style="margin-top:0">Bars share one axis scaled to the largest value '
                     'across all four metrics, so compare within a row, not between rows.</p>')
            H.append(legend(names))
            H.append(grouped_bars(cats, series))
            H.append('</div>')

        # cycle-time composition — where the time actually goes
        seg_names = ['Processing', 'Queueing', 'Batching', 'Transport']
        segs = [(s, []) for s in seg_names]
        cats = []
        for i, disp in enumerate(disps):
            ms = scenarios[(ds, disp)]
            cats.append(names[i])
            for si, s in enumerate(seg_names):
                segs[si][1].append(agg([m['composition'][s] for m in ms])[0])
        H.append('<div class="card">')
        H.append('<h3>Where a lot&rsquo;s cycle time goes</h3>')
        H.append('<p style="margin-top:0">Days per completed lot. Processing time is fixed by '
                 'the recipe &mdash; a dispatching rule can only move the queueing bar.</p>')
        H.append(legend(seg_names))
        H.append(stacked_bars(cats, segs))
        H.append('</div>')

        # bottleneck tools
        H.append('<div class="card">')
        H.append('<h3>Busiest toolsets</h3>')
        H.append('<p style="margin-top:0">The ten highest-utilisation machine families. These '
                 'set the ceiling on fab throughput; everything else waits on them.</p>')
        base = scenarios[(ds, disps[0])][0]['machines']
        top = sorted(base.items(), key=lambda kv: -kv[1].get('util', 0))[:10]
        mcats = [k for k, _ in top]
        mseries = []
        for i, disp in enumerate(disps):
            mm = scenarios[(ds, disp)][0]['machines']
            mseries.append((names[i], [mm.get(k, {}).get('util', 0) * 100 for k in mcats]))
        H.append(legend(names))
        H.append(grouped_bars(mcats, mseries, unit='%', height=340))
        H.append('</div>')

    # ---- findings, computed from the data rather than asserted -----------
    verdicts = []
    for ds, disps in sorted(by_ds.items()):
        if 'fifo' not in disps or 'cr' not in disps:
            continue
        f = [scenario_metrics_agg(scenarios[(ds, 'fifo')], k) for k in ('on_time_pct', 'act')]
        c = [scenario_metrics_agg(scenarios[(ds, 'cr')], k) for k in ('on_time_pct', 'act')]
        verdicts.append((ds.replace('SMT2020_', ''), f[0], c[0], f[1], c[1]))

    if verdicts:
        H.append('<h2>What the results say</h2>')
        for short, f_ot, c_ot, f_act, c_act in verdicts:
            d_ot = c_ot - f_ot
            d_act = c_act - f_act
            better = 'Critical Ratio' if d_ot > 0 else 'FIFO'
            H.append(
                f'<p><b>{esc(short)}:</b> Critical Ratio delivers '
                f'<b>{c_ot:.1f}%</b> of lots on time versus FIFO&rsquo;s <b>{f_ot:.1f}%</b> '
                f'({d_ot:+.1f} points), while average cycle time moves from '
                f'{f_act:.1f} to {c_act:.1f} days ({d_act:+.1f}). '
                f'On this fab, <b>{esc(better)}</b> is the better due-date rule.</p>')
        H.append(
            '<div class="note"><b>Why the two metrics can disagree.</b> A due-date rule buys '
            'on-time delivery by letting slack-rich lots wait longer, which can leave average '
            'cycle time flat or slightly worse even as far more lots hit their dates. Average '
            'cycle time and on-time percentage are answering different questions &mdash; read '
            'both, and prefer whichever matches what the fab is actually penalised for.</div>')

    # ---- live timeline (the view gui.html used to open in two iframes) ---
    jobs = parse_gantt('chart_jobs.html')
    tools = parse_gantt('chart_tools.html')
    if jobs or tools:
        H.append('<h2>Dispatching timeline</h2>')
        H.append(
            '<p>The simulator can emit a Gantt trace of every dispatch decision '
            '(<code>--chart</code>). Upstream this was two Google-Charts pages opened side by '
            'side via <code>gui.html</code>; it is redrawn here inline so it travels with the '
            'rest of the dashboard and needs no network. Hover any bar for its exact window.</p>')
        fams = ['Lot_3', 'Lot_4', 'HotLot_3', 'HotLot_4']
        if jobs:
            H.append('<div class="card">')
            H.append('<h3>Lots &mdash; the journey of individual wafers</h3>')
            H.append('<p style="margin-top:0">One row per lot, busiest first. Each bar is a '
                     'processing step; the gaps between them are queueing &mdash; the time a '
                     'dispatching rule is fighting to remove.</p>')
            H.append(legend(fams))
            H.append(gantt_svg(jobs, lambda row, lab: row.split(' ')[0], fams))
            H.append('</div>')
        if tools:
            H.append('<div class="card">')
            H.append('<h3>Tools &mdash; machine occupancy</h3>')
            H.append('<p style="margin-top:0">One row per machine, busiest first. White space is '
                     'idle capacity: a tool standing still while lots wait elsewhere.</p>')
            H.append(gantt_svg(tools, lambda row, lab: fams[0], fams))
            H.append('</div>')

    # ---- table view (required relief for the contrast WARN) --------------
    H.append('<h2>All numbers</h2>')
    H.append('<div class="card"><div class="tblwrap"><table>')
    H.append('<thead><tr><th>Scenario</th><th>Seeds</th><th>On-time %</th><th>Cycle time (d)</th>'
             '<th>Throughput /day</th><th>Tardiness (d)</th><th>Mean util %</th><th>Cost</th></tr></thead><tbody>')
    for key in order:
        ds, disp = key
        ms = scenarios[key]
        ot, ot_sd = agg([m['on_time_pct'] for m in ms])
        act, act_sd = agg([m['act'] for m in ms])
        th, _ = agg([m['throughput_per_day'] for m in ms])
        td, _ = agg([m['tardiness_days'] for m in ms])
        ut, _ = agg([m['mean_util'] for m in ms])
        cost, _ = agg([m['cost'] for m in ms])
        H.append(
            f'<tr><td>{esc(ds.replace("SMT2020_",""))} &middot; {esc(DISPATCHER_LABEL.get(disp,disp))}</td>'
            f'<td>{len(ms)}</td><td>{ot:.2f} ±{ot_sd:.2f}</td><td>{act:.2f} ±{act_sd:.2f}</td>'
            f'<td>{th:.1f}</td><td>{td:.2f}</td>'
            f'<td>{ut:.2f}</td><td>{cost:,.0f}</td></tr>')
    H.append('</tbody></table></div></div>')

    if skipped:
        H.append(f'<div class="note"><b>{len(skipped)} result file(s) excluded.</b> They predate '
                 'the current stats code and lack fields the others have, so they are not '
                 'comparable: ' + esc(', '.join(os.path.basename(p) for p in skipped)) + '</div>')

    H.append('<h2>How to reproduce</h2>')
    H.append('<div class="card"><p style="margin-top:0">From the repository root:</p>'
             '<p><code>./reproduce_dispatcher_experiments.sh</code> &rarr; writes '
             '<code>greedy/*.json</code><br>'
             '<code>.venv/bin/python build_dashboard.py</code> &rarr; rebuilds this page</p>'
             '<p>Sweep size is set with <code>PYSCFABSIM_DAYS</code> and '
             '<code>PYSCFABSIM_SEEDS</code>. Runs must exceed 365 days: the simulator zeroes '
             'machine counters at the one-year mark and the statistics divide by the '
             'post-reset window, so shorter runs report meaningless tool numbers.</p></div>')

    H.append('</div></div>')
    html = HEAD + f'<style>{CSS}</style>' + ''.join(H)
    with open(out_path, 'w') as f:
        f.write(html)
    return out_path, len(scenarios)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=730)
    ap.add_argument('--out', type=str, default='dashboard.html')
    ap.add_argument('--standalone', action='store_true',
                    help='emit a full HTML document for opening as a local file; '
                         'omit for the Artifact publishing format')
    a = ap.parse_args()
    p, n = build(a.days, a.out)
    if a.standalone:
        # A local file needs a real document; the Artifact publisher supplies its
        # own <!doctype>/<head> wrapper, so that format stays fragment-only.
        with open(p) as f:
            body = f.read()
        with open(p, 'w') as f:
            f.write('<!doctype html><html lang="en"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    + body + '</html>')
    print(f'wrote {p} ({n} scenarios)')
