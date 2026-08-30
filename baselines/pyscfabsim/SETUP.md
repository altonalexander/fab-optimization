# Setup, provenance, and what this project actually does

## Where this code came from

| | |
|---|---|
| Remote | `git@github.com:david-dd/PySCFabSim-revised.git` |
| Commit | `ae3d55e` — *Merge pull request #2 from spooferHD/heik_update* (2025-07-03) |
| Lineage | a fork of **PySCFabSim**, itself the simulator from Kopp et al.'s SMT2020 work |
| Dataset | **SMT2020**, a public semiconductor-fab testbed — <https://p2schedgen.fernuni-hagen.de/index.php/downloads/simulation> |

> Kopp, D., Hassoun, M., Kalir, A., Mönch, L. (2020). *SMT2020 — A Semiconductor
> Manufacturing Testbed.* IEEE Transactions on Semiconductor Manufacturing.
> doi:10.1109/TSM.2020.3001933

Two fab models ship in `datasets/`: **HVLM** (high volume, low mix) and
**LVHM** (low volume, high mix). **This project standardises on LVHM** — it is
the default everywhere, and HVLM is only used when passed explicitly.

## The situation being modelled

A wafer fab is generally considered the hardest scheduling problem in
manufacturing, for one structural reason: **re-entrant flow**. A silicon lot
makes hundreds of passes through the same few hundred machines, coming back to
the same toolsets again and again at different stages of its recipe. The queue a
lot joins therefore depends on every dispatching decision made before it.

On top of that the simulator models machine breakdowns, preventive maintenance,
sequence-dependent setup times (switching a tool between recipes costs real
hours), batching tools that want a full load before starting, and lots with
hard deadlines and priorities.

Every time a machine becomes free, *something* must choose which waiting lot
runs next. That choice is the **dispatching rule**, and it is made tens of
thousands of times per simulated day. Fabs cannot A/B-test these rules on a
$10B facility, which is what the simulator is for.

## What it does to solve it

`main.py` runs a discrete-event simulation loop (`simulation/greedy.py`). At
each decision point it picks a free machine, ranks the lots that machine could
legally run, and dispatches the best one. The ranking function is the rule:

| Rule | Key | Behaviour |
|---|---|---|
| `fifo` | longest wait first | simple, predictable, **blind to deadlines** |
| `cr` | `(deadline − now) / remaining work` | below 1.0 a lot cannot make its date even with zero queueing, so the most endangered lot goes first |
| `lifo_org`, `lifo_anders` | newest first | included for contrast |
| `random` | uniform | the null baseline |

All rules share leading tie-breakers that prefer a lot needing **no setup
change**, so the fab does not thrash between recipes.

There is also a reinforcement-learning path (`rl_train.py` / `rl_test.py`,
PPO via stable-baselines3) that learns a dispatching policy instead of using a
fixed rule. It is **not** currently runnable: it expects an `experiments/*/config.json`
tree that is not in the repository.

## Reading the results

```bash
./reproduce_dispatcher_experiments.sh          # runs the sweep → greedy/*.json
.venv/bin/python build_dashboard.py            # → dashboard.html
```

Sweep size is controlled by environment variables:

```bash
PYSCFABSIM_DAYS=730 \
PYSCFABSIM_SEEDS=0,1 \
PYSCFABSIM_MATRIX=LVHM:fifo,LVHM:cr \
  ./reproduce_dispatcher_experiments.sh
```

### Runs must be longer than 365 days

`simulation/greedy.py` registers a `ResetEvent` at the one-year mark that zeroes
every machine's utilisation counter, and `simulation/stats.py` then divides by
`current_time − 31536000`. The first simulated year is a **warm-up that is
deliberately discarded**. A run shorter than 365 days reports meaningless tool
statistics (utilisation above 100%, negative values), and a run of a few days
completes *zero* lots — average cycle time is ~28 days, so nothing has finished.

Expect roughly **16 s per simulated day** per run on CPython. A 730-day run is
about 3 hours; the authors used `pypy3`, which is several times faster
(`PYSCFABSIM_PYTHON=pypy3` if you have it).

## Environment

Python 3.11 in `.venv` (the pinned `numpy`/`pandas` have no wheels for 3.12).
Note `requirements.txt` is a `pip list` dump, not an installable requirements
file — `pip install -r` fails on it. The working set is:

```
numpy<2 pandas matplotlib gym gymnasium==0.29.1 stable-baselines3==2.1.0
torch wandb pyinstrument openpyxl PyPDF2
```

The package imports (`from classes import ...`) assume `simulation/` is on
`sys.path`; upstream did this with `sys.path.append` calls hardcoded to the
original authors' machines. `main.py` now derives those paths from its own
`__file__` and appends them, so it works from any checkout with no environment
setup.

The order matters: the entries must be **appended**, after site-packages. Both
`simulation/gym/` and the real `gym` package are importable as `gym`, and
`rl_train` needs the real one, so putting `simulation/` first (via `PYTHONPATH`,
or `sys.path.insert`) breaks the RL entry points with a confusing
`cannot import name 'Env' from 'gym'`.

This replaces an earlier `pyscfabsim.pth` in the venv's `site-packages`, which
still pointed at the pre-reorg `PySCFabSim-revised/` path. Nonexistent `.pth`
entries are ignored silently, so every entry point died on `No module named
'classes'` with nothing indicating why. If that stale file is still in your
venv it is now inert, but worth correcting:

```bash
printf '%s\n' \
  "$PWD/simulation" "$PWD/simulation/gym" \
  > .venv/lib/python3.11/site-packages/pyscfabsim.pth
```

`wandb` is called unconditionally by `simulation/plugins/wandb_plugin.py`; the
scripts set `WANDB_MODE=offline` so it does not demand an API key.

## Known gaps in the upstream code

- **Reference baselines are missing.** `eval_results.py` wants
  `datasets/{lots,machines}_SMT2020_{HVLM,LVHM}.txt` — the paper's comparison
  values. They are not in the repo, so its `old`/`delta` columns fall back to 0
  and are not meaningful. It now warns rather than crashing.
- **Machine-level `waiting_time` is no longer emitted** (commented out in
  `stats.py`, *"Heik — brauchen wir diese Zeilen?"*), so `eval_results.py` would
  crash on any freshly generated run. It now defaults that field to 0.
- **Result files from different code versions are not comparable.** The
  pre-existing `greedy_seedNone_730days_*.json` files have differing schemas
  between dispatchers; `build_dashboard.py` excludes any run missing fields the
  others have, and says so on the page.

## The dashboard

`build_dashboard.py` reads `greedy/*.json` and writes one self-contained page.

```bash
.venv/bin/python build_dashboard.py                # → dashboard.html   (fragment, for publishing)
.venv/bin/python build_dashboard.py --standalone \
    --out dashboard_local.html                     # → open this one in a browser
```

Two formats because the Artifact publisher supplies its own
`<!doctype>`/`<head>` wrapper, so the published file must stay fragment-only,
while a file opened from disk needs a real document or it renders in quirks
mode. `reproduce_dispatcher_experiments.sh` writes both.

It aggregates across seeds, reports mean ± spread, and **excludes any run missing
fields the others have** (see "known gaps" above), naming the excluded files on
the page so a silent apples-to-oranges comparison cannot happen.

It also absorbs what `gui.html` used to do. Upstream, passing `--chart` made
`ChartPlugin` write `chart_jobs.html` and `chart_tools.html`, two Google-Charts
timeline pages that `gui.html` opened in side-by-side iframes. Those are
re-rendered inline as SVG instead, because the Google loader is fetched from
`gstatic.com` — fine locally, but blocked by the Artifact CSP, where it would
render blank. Note the plugin writes timestamps in **milliseconds** (it
multiplies sim seconds by 1000); the parser divides them back out.

Generate the timeline data with:

```bash
.venv/bin/python main.py --days 1 --dataset LVHM --dispatcher fifo --seed 0 --chart
```

A short run is the right choice here — the Gantt is a qualitative view of
dispatch behaviour, not a statistic, and `--chart` rewrites a multi-megabyte
file every 10 dispatches, so long runs are slow.
