# Reticles — the coupling constraint, and the answer (2026-09-12)

ADR 0014 §3.6. `compare.py --days 180 --warmup-days 90`, LVHM seed 0, 90-day
reporting window (days 90–180, ≈2.4 cycle times), each fab warmed under its
own mix and keyed by it. `reticles-1x` is one mask per (part, photo layer) —
251 masks over 82 scanners — with 900 s of transport when a mask moves
between scanners.

**The answer: the real-time layer should be a sort key.** The mask constraint
is real and large, a third of it is recoverable by dispatching, and `cr`
recovers that third. The CP-SAT slate adds nothing beyond `cr` at 14× the
compute.

## The headline rows

Uniform LVHM (no ramp), which is the cleanest case and where the constraint
already costs the most relative to a near-stationary baseline:

| fab | rule | lots/90d | util % | CT d | on-time | WIP slope | wall s |
|---|---|---:|---:|---:|---:|---:|---:|
| pristine | fifo | 5096 | 80.5 | 35.5 | 98.21% | +2.8 | 3366 |
| pristine | cr | 5052 | 81.4 | 37.5 | 99.66% | **−1.8** | 1641 |
| pristine | slate | 5041 | 80.6 | 36.5 | 99.50% | +2.6 | 20580 |
| masks | fifo | 4340 | 71.6 | 56.9 | 2.65% | +11.4 | 3019 |
| masks | cr | **4579** | 73.3 | 54.1 | 2.45% | +7.9 | 1650 |
| masks | slate | 4575 | 73.5 | 54.0 | 2.51% | +7.3 | 23748 |

- **The masks cost 756 lots and +21 days of cycle time** (`fifo`, pristine →
  masks). That is the largest constraint effect produced anywhere in ADR 0013
  or 0014.
- **A third of it is a dispatching loss, not a capacity loss.** `cr` recovers
  **+239 lots (+5.5%)** over `fifo` on the same mask fab. So the penalty is
  not simply capacity deleted; some of it is `fifo` scattering same-layer lots
  across scanners and paying transport it did not have to.
- **The slate recovers none of the remainder.** `cr → slate` is **−4 lots
  (−0.09%)**, cycle time −0.09 d, on-time +0.06 pp — noise, for **14.4× the
  compute**. On the pristine fab the same: −11 lots (−0.22%).

## The ramp sweep: the cost is transport, not exclusivity

`part_1`/`part_2` ramped 1.00× → 2.75×, with the other eight scaled down so
total starts stay pinned at 57 lots/day. Mask load on the ramped parts is
linear in the ramp (27% of a mask-day at 1.00×), so this sweeps mask
saturation while holding the fab's lot count constant. `fifo` only.

| ramp | mask load | pristine lots | masks lots | Δ lots | Δ CT | pristine WIP slope | masks WIP slope |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.00× | 27% | 5096 | 4340 | −756 | +21.3 d | +2.8 | +11.3 |
| 1.25× | 34% | 5033 | 4129 | −904 | +19.8 d | +2.0 | +12.7 |
| 1.50× | 40% | 4997 | 3959 | −1038 | +19.9 d | +1.0 | +13.5 |
| 1.75× | 47% | 4782 | 3730 | −1052 | +20.5 d | +2.2 | +14.1 |
| 2.00× | 54% | 4609 | 3596 | −1013 | +20.3 d | +6.1 | +16.9 |
| 2.25× | 61% | 4609 | 3558 | −1051 | +20.1 d | +3.5 | +16.7 |
| 2.50× | 68% | 4445 | 3491 | −954 | +20.2 d | +6.3 | +16.7 |
| 2.75× | 74% | 4336 | 3278 | −1058 | +19.4 d | +9.3 | +20.5 |
| 3.00× | 81% | 4275 | 3393 | −882 | +16.4 d | +8.8 | +19.2 |

**The penalty is flat.** Mask load triples across the sweep — 27% to 81% of a
mask-day — and the cost stays at roughly −1000 lots and +20 days throughout,
with no trend. A coupling constraint bites harder as its resource saturates;
this does not. The cost is the 900 s transport, charged per move, and it is
indifferent to how contended the mask is.

The sweep also prices the mix itself, which is why the pristine arm is run at
every step: the pristine fab loses 5096 → 4275 lots (−16%) and its WIP slope
goes +2.8 → +8.8 purely from concentrating demand on two parts. Holding total
starts at 57 lots/day holds the fab's LOT count constant, not its work
content — the ten parts have different routes. Any read of the mask effect
that skipped this control would have attributed that −821 to the masks.

That is corroborated directly: with `--transport-s 0` the library is
**indistinguishable from the pristine fab** (493 lots against 495 over a
matched 8-day window). Exclusivity — one mask, one scanner, the constraint
`solver.hpp` has encoded since ADR 0009 — **never binds on this fab.**

The reason is arithmetic that should have been checked first: 251 masks over
82 scanners at ~27% of a mask-day means two lots of the same (part, layer)
almost never want a scanner at the same instant, and when one is blocked the
scanner takes one of the other ~250 layers. A blocked lot never idles a tool,
so exclusivity costs nothing. That is a property of **high mix**: on
`SMT2020_HVLM` (2 parts, 60 masks) one mask per layer needs **169% of a
mask-day** and is refused as infeasible — masks there run at 84–93% and the
constraint would bind. LVHM cannot be made to care by ramping, because the
ramp raises load on the mask *and* on every tool the part uses.

## What the numbers do NOT support

- **Not "masks don't matter."** They cost 15–22% of throughput. They matter a
  great deal; they just cost it through transport rather than exclusivity.
- **Not a clean operating point.** Every mask arm has WIP climbing (+7.3 to
  +11.4 lots/day) — the transport tax pushes the fab over capacity for all
  three rules. Only pristine `cr` is genuinely stationary (−1.8). So the mask
  rows compare three rules on a fab none of them can hold, which is the
  weakest form of the comparison. It is also the most favourable form
  available: the alternative is a fab where the constraint costs nothing.
- **Not a statement about all fabs.** The library is invented (SMT2020 ships
  no reticle data) and the transport is a constant. A low-mix fab is a
  different answer; see HVLM above.

## Verdict against ADR 0012's overturn condition

ADR 0012 named *an overlay fab where the assignment solver still adds nothing
over a sort*. That is now measured, on the most favourable configuration the
testbed permits:

- the constraint class a sort key provably cannot represent (coupling), built
  into both solver paths and fed real data
- transport on, so *which* scanner matters and the problem is an assignment
- a 756-lot penalty with a third of it demonstrably recoverable
- warmed, controlled, per-part, WIP-checked

**The solver adds −0.09%.** The condition is met. The real-time layer is a
sort key with the downstream and batch terms, and the optimisation effort
belongs in the segment scheduler (NEXT §3).

The mechanisms stay regardless, which ADR 0013 §7 anticipated: a realistic fab
needs qualification, masks and the eligibility predicate whichever dispatcher
wins. And two of them found real defects in the simulator that had nothing to
do with reticles — see `docs/adr/0014` §3.3.

## Reproducing

```
make -C dispatch slate
V=baselines/pyscfabsim/.venv/bin/python3
$V bench/tools/gen_reticles.py --name reticles-1x --copies 1      # 900s transport
$V bench/tools/compare.py --days 180 --warmup-days 90 \
     --rules fifo,cr,slate --overlay reticles-1x
```

The sweep adds `--starts-part part_1=R --starts-part part_2=R` with the other
eight at `(10-2R)/8`. The mix is applied during the warm-up and hashed into
the checkpoint name; do not also apply it after the resume (ADR 0014 §3.5).
