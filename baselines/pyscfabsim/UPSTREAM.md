# Upstream provenance — PySCFabSim-revised

Vendored, **read-only**. Do not develop here; changes belong in `dispatch/` or
`bench/`. This directory exists to give the C++ dispatcher a pinned, reproducible
baseline to be measured against.

| | |
|---|---|
| Upstream | `git@github.com:david-dd/PySCFabSim-revised.git` |
| Pinned commit | `ae3d55ef08cd0d6bc9cd114eb800e6c920dbede1` |
| Commit date | 2025-07-03 |
| Subject | Merge pull request #2 from spooferHD/heik_update |
| Vendored on | 2026-08-29 |
| Method | squash (no submodule, no subtree) |

Squashed rather than submoduled so `make` works from a plain `git clone` with no
`--recursive`. We are not contributing upstream and do not need their history —
we need a fixed baseline.

## Deviations from upstream

1. **`datasets/` is now a symlink** to `../../data/smt2020`. This is the point of
   the monorepo: the dispatcher and the baseline must read the *same* SMT2020
   load, or every comparison between them is meaningless in a way nobody would
   notice.
2. Excluded from the vendoring: `.git/`, `.venv/` (4.8 GB), `wandb/` (offline run
   logs), `__pycache__/`, `debug_data/` (42 MB of regenerable pickles), and
   `docs/` (25 MB generated site). Everything excluded is either regenerable or
   environment-local.

## Refreshing the pin

Re-vendor from a fresh clone at the new SHA, re-apply deviation 1, update the
table above. Do not `git pull` into this directory.
