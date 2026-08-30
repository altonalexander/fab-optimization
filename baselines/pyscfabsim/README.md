# PySCFabSim — vendored baseline

**This is third-party code, vendored read-only. Do not develop here.**
Changes belong in `dispatch/` or `bench/`. This directory exists to give the C++
dispatcher a pinned, reproducible baseline to be measured against.

| | |
|---|---|
| Upstream project | [prosysscience/PySCFabSim-release](https://github.com/prosysscience/PySCFabSim-release) |
| Vendored via fork | [david-dd/PySCFabSim-revised](https://github.com/david-dd/PySCFabSim-revised) |
| Pinned commit | `ae3d55ef08cd0d6bc9cd114eb800e6c920dbede1` (2025-07-03) |
| Vendored on | 2026-08-29 |
| License | **MIT** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) |

## License

PySCFabSim is MIT-licensed, copyright (c) 2026 Research Group Production
Systems. The full text is in [`LICENSE`](LICENSE) and **must be retained in any
copy or redistribution of these files**.

The fork this was vendored from carries no LICENSE file of its own. That does
not change the terms: they descend from the root project above, and a fork
cannot strip them. [`NOTICE`](NOTICE) records the provenance chain.

The MIT license covers this directory only. The rest of this repository is
licensed separately — see the [`LICENSE`](../../LICENSE) at the repository root.

## This copy has been modified

It is **not** a byte-for-byte copy of upstream. Seven deviations are documented
in [`UPSTREAM.md`](UPSTREAM.md); the ones that change behaviour:

- **Scenario default is LVHM, not HVLM.** A run launched without `--dataset`
  would otherwise silently measure the other fab.
- **`datasets/` is a symlink** to `../../data/smt2020`, so the dispatcher and
  the baseline read the same SMT2020 load. If this symlink breaks, stop — do
  not point it at a private copy.
- **`main.py` bootstraps `sys.path`**, and **`simulation/stats.py` creates its
  results directory** before writing. Both fix silent failures.
- **`eval_results.py` tolerates missing reference files**, and
  **`reproduce_dispatcher_experiments.sh` was rewritten** to fail loudly and use
  the local `.venv`.

Read `UPSTREAM.md` before refreshing the pin — these have to be re-applied.

## Running it

```bash
cd baselines/pyscfabsim
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

./reproduce_dispatcher_experiments.sh    # FIFO / CR dispatching experiments
```

Results land in `greedy/_greedy_sum.txt`; the dashboard is `dashboard_local.html`.
Override the sweep with env vars:

```bash
PYSCFABSIM_DAYS=30 ./reproduce_dispatcher_experiments.sh
```

RL entry points are `rl_train.py` and `rl_test.py` (see `SETUP.md`).

## Dataset

The simulator reads the SMT2020 testbed, a separate work with its own terms,
available from [FernUniversität Hagen](https://p2schedgen.fernuni-hagen.de/index.php/downloads/simulation):

> Kopp, D., Hassoun, M., Kalir, A., & Mönch, L. (2020). SMT2020 — A
> Semiconductor Manufacturing Testbed. *IEEE Transactions on Semiconductor
> Manufacturing.* doi:10.1109/TSM.2020.3001933
