# SMT2020 — provenance and redistribution status

The files here are the SMT2020 Semiconductor Manufacturing Testbed. They are
**not this project's work** and are **not covered by this repository's
Apache-2.0 licence**.

| | |
|---|---|
| Source | <https://p2schedgen.fernuni-hagen.de/index.php/downloads/simulation> |
| Archive | `SMT2020.zip` (~110 MB), published by the Chair of Enterprise-wide Software Systems, FernUniversität in Hagen |
| Subset here | `SMT2020_HVLM/` and `SMT2020_LVHM/`, the AutoSched model inputs |
| Stated licence | **none** |

## Cite it

> Kopp, D., Hassoun, M., Kalir, A., & Mönch, L. (2020). SMT2020 — A
> Semiconductor Manufacturing Testbed. *IEEE Transactions on Semiconductor
> Manufacturing.* doi:[10.1109/TSM.2020.3001933](https://doi.org/10.1109/TSM.2020.3001933)

Cite it for **any** published result derived from these files, including
results this project reports — every benchmark here reads this load.

## Redistribution status — read before making this repository public

This was checked directly against the source rather than assumed:

- The download page carries **no licence, no terms of use, and no click-through
  agreement**. The archive is a direct link.
- The official `SMT2020.zip` contains **no LICENSE, README, COPYRIGHT, NOTICE or
  terms file** among its 233 entries. Its two `.docx` files are purely technical
  format documentation, with no permission or licensing language.
- The upstream simulator this project benchmarks against
  ([PySCFabSim](https://github.com/prosysscience/PySCFabSim-release)) **also
  redistributes these files** in a public repository, as do its forks. Its
  README points at the source and gives the citation, and claims no licence for
  the data.

So there is clear precedent for public redistribution with attribution, and the
work is a *testbed* — published expressly so others could run against a common
load. But **absence of a stated licence is not a grant of one.** Default
copyright applies, and no redistribution right has been affirmatively given.

The definitive fix is one email to the authors at FernUniversität Hagen asking
for written permission to redistribute the subset in `data/smt2020/`. Until
that answer exists, this file records exactly what was and was not verified.

If permission is declined or never arrives, the alternative is to remove these
files, gitignore the directory, and ship a fetch script that downloads
`SMT2020.zip` from the source and extracts the two models — at the cost of a
clone no longer being self-contained.
