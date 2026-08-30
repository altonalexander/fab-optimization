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
| Consolidated on | 2026-08-30 |
| Method | squash (no submodule, no subtree) |

Squashed rather than submoduled so `make` works from a plain `git clone` with no
`--recursive`. We are not contributing upstream and do not need their history —
we need a fixed baseline.

## Consolidated 2026-08-30

The repo previously held two copies: this one, and a working clone at
`PySCFabSim-revised/` that `.gitignore` excluded. This directory is now that
working clone, promoted in place; the separate copy is gone and the `.gitignore`
entry with it. The clone was the one actually being run, and it carries a built
`.venv/`, so promoting it removes a split between the code that was read and the
code that was executed.

Verified byte-identical before the swap, so this changed no source and no data:

- `simulation/` matched the previous vendored copy exactly (excluding
  `__pycache__`).
- the clone's `datasets/` matched `data/smt2020/` exactly.
- the clone's `master`, its `origin/master`, and the pin above were all
  `ae3d55ef` — no local commits, nothing unpushed.

What the promotion brought with it: `.venv/` (working, ~4.8 GB), `wandb/`,
`debug_data/`, `docs/`, and a few extra greedy result files. All are gitignored
or regenerable. Its nested `.git/` was **removed** — a git directory inside the
outer repo makes `git worktree` resolve to the wrong repository, and the policy
below is re-vendor-from-a-fresh-clone anyway, so no history is needed here. It
is recoverable from upstream at the SHA above.

## Deviations from upstream

1. **`datasets/` is a symlink** to `../../data/smt2020`. This is the point of
   the monorepo: the dispatcher and the baseline must read the *same* SMT2020
   load, or every comparison between them is meaningless in a way nobody would
   notice. The clone shipped a real directory; it was replaced with the symlink
   after verifying the two matched byte for byte. **If this symlink is ever
   broken, stop — do not point it at a private copy.**
2. Not tracked (see the root `.gitignore`): `.venv/`, `wandb/`, `__pycache__/`,
   `debug_data/`, `docs/`. Present on disk, regenerable or environment-local.
3. **Scenario default is LVHM, not HVLM** (2026-08-30). `simulation/greedy.py`,
   `greedy_runner.py` and `exp_set_gen.py` defaulted to HVLM; this project
   standardises on LVHM, so a run launched without `--dataset` no longer
   silently measures the other fab. See `bench/SCENARIO.md` for the reasoning
   and for what would overturn it. HVLM still works when passed explicitly.
4. **`main.py` bootstraps `sys.path` from `__file__`** (2026-08-30). Upstream
   relied on the environment providing `simulation/` on the path; here that was
   a `pyscfabsim.pth` in the venv that still pointed at the pre-consolidation
   `PySCFabSim-revised/` directory. Python ignores dead `.pth` entries silently,
   so every entry point died on `No module named 'classes'` with nothing to
   indicate why. Paths are **appended**, not inserted: `simulation/gym/` would
   otherwise shadow the real `gym` that `rl_train` imports.
5. **`simulation/stats.py` creates its results directory** before writing
   (2026-08-30). A completed run was otherwise lost at the final write on any
   checkout without a `greedy/` directory.

Deviations 3-5 are edits inside this vendored tree, which the policy above says
to avoid. They are here rather than in `dispatch/` or `bench/` because each one
is a property of the baseline's own entry points — a default, an import path, an
output path — that cannot be expressed from outside. Re-apply them when
refreshing the pin.

## Environment

`.venv/` here is the interpreter for both the baseline and `bench/tools/`. It is
gitignored, so a fresh clone rebuilds it:

```bash
cd baselines/pyscfabsim
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Refreshing the pin

Re-vendor from a fresh clone at the new SHA, re-apply deviation 1, delete the
clone's `.git/`, update the table above. Do not `git pull` into this directory.
