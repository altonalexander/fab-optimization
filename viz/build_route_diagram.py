"""Render the SMT2020 route diagram: a re-entrancy map and a shop-floor bay map.

Reads routes_lvhm.json (from extract_routes.py) and writes a self-contained
HTML page with the route data inlined.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, 'routes_lvhm.json')))

# Shop-floor zones. A real fab groups bays into modules; these follow the
# SMT2020 area names, ordered the way a wafer physically moves through a loop.
ZONES = [
    ('Thermal / Deposition', ['Diffusion', 'Dielectric', 'TF', 'TF_Met']),
    ('Patterning',           ['Litho', 'Litho_Met']),
    ('Etch',                 ['Dry_Etch', 'Wet_Etch']),
    ('Doping & Planarization', ['Implant', 'Planar']),
    ('Metrology & Queue',    ['Def_Met', 'Delay_32', 'Delay']),
]

payload = {
    'areas': DATA['areas'],
    'zones': [{'name': n, 'areas': a} for n, a in ZONES],
    'routes': {k: {
        'id': v['id'],
        'n_steps': v['n_steps'],
        'n_visits': v['n_visits'],
        'areas': v['areas'],
        'area_visits': v['area_visits'],
        'visits': v['visits'],
        'transitions': v['transitions'],
        'sampled': v['sampled'],
        'rework': v['rework'],
        'batch_steps': v['batch_steps'],
    } for k, v in DATA['routes'].items()},
}

# sanity: every area in the data must be placed in exactly one zone
placed = [a for _, areas in ZONES for a in areas]
assert sorted(placed) == sorted(DATA['areas']), (
    f"zone/area mismatch: unplaced={set(DATA['areas'])-set(placed)} "
    f"extra={set(placed)-set(DATA['areas'])}")
assert len(placed) == len(set(placed)), 'an area is in two zones'

HTML = r'''<title>Fab Route Explorer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
*{box-sizing:border-box}
:root{
  color-scheme:light;
  --bg:#f4f6f7; --surface:#fdfdfe; --border:#dfe4e8; --lane:#eef1f3; --lane-alt:#e7ebee;
  --text-primary:#0d1117; --text-secondary:#4a555f; --text-muted:#7d8894;
  /* sequential blue, 4 ordinal steps, validated light->dark on this surface */
  --q1:#86b6ef; --q2:#3987e5; --q3:#256abf; --q4:#104281;
  --accent:#eb6834;          /* reserved: rework only */
  --accent-soft:#fbe4da;
  --flow:#9fb0bd;
  --display:"IBM Plex Sans Condensed",ui-sans-serif,system-ui,sans-serif;
  --body:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  color-scheme:dark;
  --bg:#0f1214; --surface:#171b1e; --border:#2b3238; --lane:#1f2428; --lane-alt:#242a2f;
  --text-primary:#f2f5f7; --text-secondary:#b3bec7; --text-muted:#7f8b95;
  --q1:#3987e5; --q2:#6da7ec; --q3:#9ec5f4; --q4:#cde2fb;
  --accent:#d95926; --accent-soft:#3a2318;
  --flow:#3d4750;
}}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#0f1214; --surface:#171b1e; --border:#2b3238; --lane:#1f2428; --lane-alt:#242a2f;
  --text-primary:#f2f5f7; --text-secondary:#b3bec7; --text-muted:#7f8b95;
  --q1:#3987e5; --q2:#6da7ec; --q3:#9ec5f4; --q4:#cde2fb;
  --accent:#d95926; --accent-soft:#3a2318;
  --flow:#3d4750;
}
body{margin:0;background:var(--bg);color:var(--text-primary);font-family:var(--body);
  font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:40px 24px 72px;display:flex;flex-direction:column;gap:30px}
header{display:flex;flex-direction:column;gap:10px;border-bottom:1px solid var(--border);padding-bottom:22px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--text-muted)}
h1{font-family:var(--display);font-size:38px;line-height:1.08;margin:0;text-wrap:balance;letter-spacing:-.01em}
.sub{color:var(--text-secondary);max-width:64ch;margin:0}
h2{font-family:var(--display);font-size:21px;margin:0;letter-spacing:-.005em}
.sec{display:flex;flex-direction:column;gap:14px}
.sec-head{display:flex;flex-direction:column;gap:5px}
.sec-head p{margin:0;color:var(--text-secondary);max-width:70ch;font-size:13.5px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:20px}
/* ---- controls ---- */
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.ctl-label{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--text-muted);margin-right:4px}
button.route{font-family:var(--mono);font-size:12.5px;padding:5px 11px;border-radius:4px;
  border:1px solid var(--border);background:var(--surface);color:var(--text-secondary);cursor:pointer;
  transition:background .12s,border-color .12s,color .12s}
button.route:hover{border-color:var(--q2);color:var(--text-primary)}
button.route[aria-pressed="true"]{background:var(--q2);border-color:var(--q2);color:#fff;font-weight:600}
:root:not([data-theme="light"]) button.route[aria-pressed="true"]{color:#0f1214}
@media (prefers-color-scheme:light){button.route[aria-pressed="true"]{color:#fff}}
:root[data-theme="dark"] button.route[aria-pressed="true"]{color:#0f1214}
button:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
/* ---- stat tiles ---- */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:1px;
  background:var(--border);border:1px solid var(--border);border-radius:6px;overflow:hidden}
.tile{background:var(--surface);padding:14px 16px;display:flex;flex-direction:column;gap:3px}
.tile .v{font-family:var(--mono);font-size:25px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.tile .k{font-size:11.5px;color:var(--text-muted);letter-spacing:.03em}
.tile.rw .v{color:var(--accent)}
/* ---- scroll frames ---- */
.scroll{overflow-x:auto;border:1px solid var(--border);border-radius:6px;background:var(--surface)}
.scroll svg{display:block}
/* ---- legend ---- */
.legend{display:flex;flex-wrap:wrap;gap:16px;align-items:center;font-size:12px;color:var(--text-secondary)}
.legend .item{display:flex;align-items:center;gap:6px}
.sw{width:13px;height:13px;border-radius:3px;flex:none}
.ramp{display:flex;gap:2px}
.ramp i{width:20px;height:11px;border-radius:2px;display:block}
/* ---- tooltip ---- */
#tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;z-index:50;
  background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:8px 11px;
  font-size:12.5px;box-shadow:0 6px 20px rgba(13,17,23,.16);max-width:280px}
#tip .t{font-family:var(--mono);font-weight:600;font-size:12px;margin-bottom:3px}
#tip .r{color:var(--text-secondary);font-variant-numeric:tabular-nums}
#tip .r b{color:var(--text-primary);font-weight:600;font-family:var(--mono)}
/* ---- table ---- */
details{border:1px solid var(--border);border-radius:6px;background:var(--surface)}
summary{padding:12px 18px;cursor:pointer;font-family:var(--mono);font-size:12px;
  letter-spacing:.08em;text-transform:uppercase;color:var(--text-secondary)}
summary:hover{color:var(--text-primary)}
.tbl-wrap{overflow-x:auto;border-top:1px solid var(--border)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:8px 14px;text-align:right;border-bottom:1px solid var(--border);
  font-variant-numeric:tabular-nums;font-family:var(--mono)}
th:first-child,td:first-child{text-align:left;font-family:var(--body)}
th{font-family:var(--body);font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;
  color:var(--text-muted);font-weight:600;position:sticky;top:0;background:var(--surface)}
tbody tr:last-child td{border-bottom:none}
.note{font-size:12.5px;color:var(--text-muted);max-width:72ch;margin:0}
.note code{font-family:var(--mono);font-size:11.5px;background:var(--lane);
  padding:1px 5px;border-radius:3px;color:var(--text-secondary)}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">SMT2020 LVHM &middot; PySCFabSim</div>
    <h1>Fab Route Explorer</h1>
    <p class="sub">Each product's route is a few hundred process steps, so a step-by-step
    flowchart is unreadable. Collapsing every step to its process area shows the thing that
    actually defines a wafer fab: lots do not flow through once. They loop back through the
    same bays dozens of times.</p>
  </header>

  <div class="controls">
    <span class="ctl-label">Product</span>
    <div id="routebtns" style="display:flex;flex-wrap:wrap;gap:8px"></div>
  </div>

  <div class="tiles" id="tiles"></div>

  <section class="sec">
    <div class="sec-head">
      <h2>Re-entrancy map</h2>
      <p>The route read left to right. One column per process step, one lane per area &mdash;
      a mark shows where that step runs. Identity comes from the lane, so no colour is spent
      naming areas; the ramp encodes how heavily the lane is used. Orange marks the
      lithography steps that can send a lot backwards.</p>
    </div>
    <div class="scroll" id="lanewrap"></div>
    <div class="legend">
      <span class="item"><span class="ctl-label" style="margin:0">Lane use</span>
        <span class="ramp"><i style="background:var(--q1)"></i><i style="background:var(--q2)"></i><i style="background:var(--q3)"></i><i style="background:var(--q4)"></i></span>
        <span style="color:var(--text-muted)">few &rarr; many visits</span></span>
      <span class="item"><span class="sw" style="background:var(--accent)"></span> rework point</span>
    </div>
  </section>

  <section class="sec">
    <div class="sec-head">
      <h2>Shop floor</h2>
      <p>The same route as a floor plan. Bays are grouped into modules; each bay is sized and
      shaded by how many times a lot visits it. Lines are the lot's transfers between bays,
      thicker where the path is worn deeper &mdash; the heavy Etch&ndash;Patterning loop is the
      photolithography cycle repeating for every layer on the wafer.</p>
    </div>
    <div class="scroll" id="floorwrap"></div>
    <div class="legend">
      <span class="item"><span class="ctl-label" style="margin:0">Bay visits</span>
        <span class="ramp"><i style="background:var(--q1)"></i><i style="background:var(--q2)"></i><i style="background:var(--q3)"></i><i style="background:var(--q4)"></i></span>
        <span style="color:var(--text-muted)">low &rarr; high</span></span>
      <span class="item"><svg width="26" height="9" aria-hidden="true"><line x1="1" y1="4.5" x2="25" y2="4.5" stroke="var(--flow)" stroke-width="5" stroke-linecap="round"/></svg> transfers between bays</span>
    </div>
  </section>

  <details>
    <summary>Bay visit table</summary>
    <div class="tbl-wrap"><table id="tbl">
      <thead><tr><th>Process area</th><th>Steps</th><th>Bay visits</th><th>Share of steps</th><th>Avg steps per visit</th></tr></thead>
      <tbody></tbody>
    </table></div>
  </details>

  <p class="note">Both views show the <em>nominal</em> route. At run time roughly one step in
  six carries a <code>StepPercent</code> below 100 and is skipped per lot, and the orange
  rework points send a lot back to an earlier step at a 0.7&ndash;1.7% chance, so a realised
  path is never exactly this one. Areas <code>Delay</code> and <code>Delay_32</code> are
  queue-time constraints rather than tools.</p>
</div>

<div id="tip" role="status" aria-live="polite"></div>

<script>
const DATA = __DATA__;
const AREAS = DATA.areas, ZONES = DATA.zones;
let current = '1';

const tip = document.getElementById('tip');
function showTip(e, html){ tip.innerHTML = html; tip.style.opacity = 1;
  const r = tip.getBoundingClientRect();
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
  tip.style.left = x + 'px'; tip.style.top = y + 'px'; }
function hideTip(){ tip.style.opacity = 0; }

// quartile bin -> ordinal ramp step
function binOf(v, max){ if (max <= 0) return 0;
  const f = v / max; return f > .66 ? 3 : f > .38 ? 2 : f > .15 ? 1 : 0; }
const RAMP = ['var(--q1)','var(--q2)','var(--q3)','var(--q4)'];
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const nice = s => String(s).replace(/_/g,' ');

/* ---------- stat tiles ---------- */
function renderTiles(r){
  const used = Object.keys(r.area_visits).length;
  const t = [
    [r.n_steps, 'process steps'],
    [r.n_visits, 'bay visits'],
    [used, 'areas used'],
    [(r.n_steps / used).toFixed(0), 'steps per area'],
    [r.sampled, 'sampled steps'],
    [r.rework.length, 'rework points', 'rw'],
    [r.batch_steps, 'batch steps'],
  ];
  document.getElementById('tiles').innerHTML = t.map(([v,k,c]) =>
    `<div class="tile ${c||''}"><span class="v">${v}</span><span class="k">${k}</span></div>`).join('');
}

/* ---------- re-entrancy map ---------- */
function renderLanes(r){
  // lanes ordered by the zone grouping so related bays sit together
  const lanes = ZONES.flatMap(z => z.areas).filter(a => r.area_visits[a]);
  const LH = 21, PAD_L = 108, PAD_T = 12, PAD_B = 30;
  const cw = Math.max(1.6, Math.min(3.2, 1400 / r.n_steps));
  const W = PAD_L + r.n_steps * cw + 18, H = PAD_T + lanes.length * LH + PAD_B;
  const maxV = Math.max(...lanes.map(a => r.area_visits[a]));
  const yOf = a => PAD_T + lanes.indexOf(a) * LH;
  const reworkAt = new Map(r.rework.map(w => [w.at, w]));

  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Re-entrancy map for ${r.id}">`;
  lanes.forEach((a, i) => {
    const y = yOf(a), bin = binOf(r.area_visits[a], maxV);
    s += `<rect x="${PAD_L}" y="${y}" width="${r.n_steps*cw}" height="${LH-3}" fill="${i%2?'var(--lane-alt)':'var(--lane)'}"/>`;
    s += `<text x="${PAD_L-10}" y="${y+LH/2+1}" text-anchor="end" font-size="11" font-family="var(--body)" fill="var(--text-secondary)" dominant-baseline="middle">${esc(nice(a))}</text>`;
    // marks for this lane
    for (const v of r.visits){
      if (v.area !== a) continue;
      const x = PAD_L + (v.first - 1) * cw;
      const w = Math.max(cw*0.85, (v.last - v.first + 1) * cw - 0.6);
      const rw = reworkAt.has(v.first) || (v.last!==v.first && reworkAt.has(v.last));
      s += `<rect class="mk" x="${x.toFixed(1)}" y="${y+2}" width="${w.toFixed(1)}" height="${LH-7}" rx="1.5" `
         + `fill="${rw?'var(--accent)':RAMP[bin]}" data-a="${esc(a)}" data-f="${v.first}" data-l="${v.last}" data-n="${v.steps}"${rw?' data-rw="1"':''}/>`;
    }
  });
  // step axis
  const tickEvery = r.n_steps > 400 ? 100 : 50;
  for (let t = 0; t <= r.n_steps; t += tickEvery){
    const x = PAD_L + t * cw;
    s += `<line x1="${x}" y1="${PAD_T+lanes.length*LH-3}" x2="${x}" y2="${PAD_T+lanes.length*LH+3}" stroke="var(--border)"/>`;
    s += `<text x="${x}" y="${PAD_T+lanes.length*LH+17}" text-anchor="middle" font-size="10.5" font-family="var(--mono)" fill="var(--text-muted)">${t||1}</text>`;
  }
  s += `<text x="${PAD_L}" y="${H-2}" font-size="10.5" font-family="var(--body)" fill="var(--text-muted)">process step &rarr;</text>`;
  s += '</svg>';
  const host = document.getElementById('lanewrap');
  host.innerHTML = s;
  host.querySelectorAll('.mk').forEach(el => {
    el.addEventListener('mousemove', e => {
      const f = el.dataset.f, l = el.dataset.l, n = el.dataset.n;
      let h = `<div class="t">${esc(nice(el.dataset.a))}</div>`
            + `<div class="r">step <b>${f}${l!==f?'&ndash;'+l:''}</b> &middot; <b>${n}</b> consecutive</div>`;
      if (el.dataset.rw){
        const w = r.rework.find(x => x.at == f || x.at == l);
        if (w) h += `<div class="r" style="color:var(--accent)">rework <b>${w.pct}%</b> &rarr; back to step <b>${w.back_to}</b></div>`;
      }
      showTip(e, h);
    });
    el.addEventListener('mouseleave', hideTip);
  });
}

/* ---------- shop floor ---------- */
function renderFloor(r){
  const W = 1060, ZGAP = 20, PAD = 22;
  const zones = ZONES.map(z => ({...z, areas: z.areas.filter(a => r.area_visits[a])}))
                     .filter(z => z.areas.length);
  const maxV = Math.max(...Object.values(r.area_visits));
  // lay zones out as columns, bays stacked inside; bay height scales with visits
  const colW = (W - PAD*2 - ZGAP*(zones.length-1)) / zones.length;
  const pos = {};
  let maxBottom = 0;
  zones.forEach((z, zi) => {
    const x = PAD + zi * (colW + ZGAP);
    let y = 56;
    z.x = x; z.y = 30;
    z.areas.sort((a,b) => r.area_visits[b] - r.area_visits[a]);
    for (const a of z.areas){
      const v = r.area_visits[a];
      const h = 40 + 52 * (v / maxV);
      pos[a] = {x, y, w: colW, h, v, bin: binOf(v, maxV)};
      y += h + 9;
    }
    z.h = y - 56 + 26;
    maxBottom = Math.max(maxBottom, y);
  });
  const H = maxBottom + 16;

  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Shop floor bay map for ${r.id}">`;
  // zone frames
  zones.forEach(z => {
    s += `<rect x="${z.x-7}" y="${z.y+8}" width="${colW+14}" height="${z.h}" rx="5" fill="none" stroke="var(--border)" stroke-dasharray="3 3"/>`;
    s += `<text x="${z.x-7}" y="${z.y}" font-size="10.5" font-family="var(--mono)" fill="var(--text-muted)" letter-spacing=".07em">${esc(z.name.toUpperCase())}</text>`;
  });
  // flow arcs, drawn under the bays
  const tr = r.transitions.filter(t => pos[t.from] && pos[t.to] && t.from !== t.to).slice(0, 16);
  const maxT = tr.length ? tr[0].n : 1;
  s += '<g>';
  for (const t of tr){
    const a = pos[t.from], b = pos[t.to];
    const x1 = a.x + a.w/2, y1 = a.y + a.h/2, x2 = b.x + b.w/2, y2 = b.y + b.h/2;
    const mx = (x1+x2)/2, my = (y1+y2)/2 - Math.min(60, Math.abs(x2-x1)*0.24 + 16);
    const sw = 1.2 + 5.5 * (t.n / maxT);
    s += `<path class="fl" d="M${x1.toFixed(1)} ${y1.toFixed(1)} Q${mx.toFixed(1)} ${my.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}" `
       + `fill="none" stroke="var(--flow)" stroke-width="${sw.toFixed(2)}" stroke-linecap="round" opacity=".62" `
       + `data-f="${esc(t.from)}" data-t="${esc(t.to)}" data-n="${t.n}"/>`;
  }
  s += '</g>';
  // bays
  for (const [a, p] of Object.entries(pos)){
    const dark = p.bin >= 2;
    s += `<g class="bay" data-a="${esc(a)}" tabindex="0" role="listitem">`
       + `<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="4" fill="${RAMP[p.bin]}" stroke="var(--surface)" stroke-width="2"/>`
       + `<text x="${p.x+11}" y="${p.y+21}" font-size="12.5" font-weight="600" font-family="var(--body)" fill="${dark?'#fdfdfe':'#0d1117'}">${esc(nice(a))}</text>`
       + `<text x="${p.x+11}" y="${p.y+39}" font-size="16" font-weight="600" font-family="var(--mono)" fill="${dark?'#fdfdfe':'#0d1117'}">${p.v}<tspan font-size="10.5" font-weight="400" dx="4"> visits</tspan></text>`
       + `</g>`;
  }
  s += '</svg>';
  const host = document.getElementById('floorwrap');
  host.innerHTML = s;
  host.querySelectorAll('.bay').forEach(el => {
    const a = el.dataset.a, p = pos[a];
    const h = () => `<div class="t">${esc(nice(a))}</div>`
      + `<div class="r"><b>${p.v}</b> bay visits &middot; <b>${r.areas[a]}</b> steps</div>`
      + `<div class="r"><b>${(100*r.areas[a]/r.n_steps).toFixed(1)}%</b> of the route</div>`;
    el.addEventListener('mousemove', e => showTip(e, h()));
    el.addEventListener('mouseleave', hideTip);
    el.addEventListener('focus', e => showTip({clientX: p.x+p.w/2, clientY: p.y+p.h}, h()));
    el.addEventListener('blur', hideTip);
  });
  host.querySelectorAll('.fl').forEach(el => {
    el.addEventListener('mousemove', e => showTip(e,
      `<div class="t">${esc(nice(el.dataset.f))} &rarr; ${esc(nice(el.dataset.t))}</div>`
      + `<div class="r"><b>${el.dataset.n}</b> transfers per lot</div>`));
    el.addEventListener('mouseleave', hideTip);
  });
}

/* ---------- table ---------- */
function renderTable(r){
  const rows = Object.keys(r.areas).sort((a,b) => r.areas[b] - r.areas[a]).map(a =>
    `<tr><td>${esc(nice(a))}</td><td>${r.areas[a]}</td><td>${r.area_visits[a]}</td>`
    + `<td>${(100*r.areas[a]/r.n_steps).toFixed(1)}%</td>`
    + `<td>${(r.areas[a]/r.area_visits[a]).toFixed(2)}</td></tr>`).join('');
  document.querySelector('#tbl tbody').innerHTML = rows;
}

function select(k){
  current = k;
  const r = DATA.routes[k];
  document.querySelectorAll('#routebtns button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.k === k)));
  renderTiles(r); renderLanes(r); renderFloor(r); renderTable(r);
}

const keys = Object.keys(DATA.routes).sort((a,b) => (+a) - (+b));
document.getElementById('routebtns').innerHTML = keys.map(k =>
  `<button class="route" data-k="${k}" aria-pressed="false">${esc(DATA.routes[k].id)}</button>`).join('');
document.querySelectorAll('#routebtns button').forEach(b =>
  b.addEventListener('click', () => select(b.dataset.k)));
select('1');
</script>
'''

out = HTML.replace('__DATA__', json.dumps(payload, separators=(',', ':')))
dest = os.path.join(HERE, 'route_diagram.html')
with open(dest, 'w') as f:
    f.write(out)
print(f'wrote {dest} ({len(out)/1024:.0f} KB, {len(payload["routes"])} routes)')
