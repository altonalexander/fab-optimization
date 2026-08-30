// Headless sanity check for route_diagram.html: runs the page's own script against a
// minimal DOM stub for all 10 routes and asserts the generated SVG geometry is sane.
import fs from 'fs';

const html = fs.readFileSync(new URL('./route_diagram.html', import.meta.url), 'utf8');
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));

const store = {};
function mkEl(id) {
  return {
    id, innerHTML: '', dataset: {}, style: {},
    setAttribute() {}, addEventListener() {},
    getBoundingClientRect: () => ({ width: 200, height: 80 }),
    querySelectorAll: () => [],
  };
}
for (const id of ['tip', 'tiles', 'lanewrap', 'floorwrap', 'routebtns']) store[id] = mkEl(id);

globalThis.document = {
  getElementById: id => store[id] || mkEl(id),
  querySelector: () => mkEl('q'),
  querySelectorAll: () => [],
};
globalThis.innerWidth = 1400;
globalThis.innerHeight = 900;

new Function(script)();

// re-run for every route by invoking the exposed select via the module scope:
// the script calls select('1') on load; to cover all routes we re-evaluate with a hook.
const patched = script.replace(/select\('1'\);\s*$/, 'globalThis.__select = select;');
new Function(patched)();

const DATA = JSON.parse(html.slice(html.indexOf('const DATA = ') + 13, html.indexOf(';\nconst AREAS')));
let fail = 0;
const bad = (m) => { console.log('  FAIL ' + m); fail++; };

for (const k of Object.keys(DATA.routes).sort((a, b) => a - b)) {
  globalThis.__select(k);
  const r = DATA.routes[k];
  const lane = store.lanewrap.innerHTML;
  const floor = store.floorwrap.innerHTML;
  const tag = `r_${k}`;

  for (const [name, svg] of [['lane', lane], ['floor', floor]]) {
    if (!svg.startsWith('<svg')) { bad(`${tag} ${name}: no svg emitted`); continue; }
    const m = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
    if (!m) { bad(`${tag} ${name}: no viewBox`); continue; }
    const [W, H] = [parseFloat(m[1]), parseFloat(m[2])];
    if (!(W > 0 && H > 0)) bad(`${tag} ${name}: degenerate viewBox ${W}x${H}`);
    if (/(width|height)="-/.test(svg)) bad(`${tag} ${name}: negative rect dimension`);
    if (/NaN|undefined|Infinity/.test(svg)) bad(`${tag} ${name}: NaN/undefined in output`);
    // every rect must sit inside the viewBox
    for (const rc of svg.matchAll(/<rect[^>]*x="([-\d.]+)"[^>]*y="([-\d.]+)"[^>]*width="([\d.]+)"[^>]*height="([\d.]+)"/g)) {
      const [x, y, w, h] = rc.slice(1).map(Number);
      if (x + w > W + 1.5 || y + h > H + 1.5) bad(`${tag} ${name}: rect overflows (${x}+${w} > ${W} or ${y}+${h} > ${H})`);
    }
  }
  // lane count must equal the areas the route actually uses
  const lanes = (lane.match(/text-anchor="end"/g) || []).length;
  const used = Object.keys(r.area_visits).length;
  if (lanes !== used) bad(`${tag}: ${lanes} lane labels for ${used} used areas`);
  // every visit must produce a mark
  const marks = (lane.match(/class="mk"/g) || []).length;
  if (marks !== r.visits.length) bad(`${tag}: ${marks} marks for ${r.visits.length} visits`);
  // every used area must get a bay
  const bays = (floor.match(/class="bay"/g) || []).length;
  if (bays !== used) bad(`${tag}: ${bays} bays for ${used} used areas`);

  console.log(`  ${tag}: ${r.n_steps} steps, ${used} areas, ${marks} marks, ${bays} bays  ok`);
}

console.log(fail ? `\n${fail} FAILURES` : '\nall render checks passed');
process.exit(fail ? 1 : 0);
