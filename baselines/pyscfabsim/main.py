import os
import sys
import time

# PySCFabSim's modules import each other flat ("from classes import Lot"), so
# simulation/ has to be importable in its own right. Derive that from __file__
# rather than relying on a .pth in the virtualenv: the venv's pyscfabsim.pth
# still pointed at the pre-reorg PySCFabSim-revised/ path and silently resolved
# to nothing, which is what made every entry point die on "No module named
# 'classes'".
#
# Append rather than insert. simulation/ contains a gym/ subpackage, and putting
# it ahead of site-packages would shadow the real gym that rl_train imports.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE,
           os.path.join(_HERE, 'simulation'),
           os.path.join(_HERE, 'simulation', 'gym')):
    if _p not in sys.path:
        sys.path.append(_p)

from simulation.greedy import run_greedy  # noqa: E402
from rl_train import main as rl_train  # noqa: E402
from rl_test import main as rl_test  # noqa: E402


def greedy():

    profile = False
    if profile:
        from pyinstrument import Profiler

        p = Profiler()
        p.start()

    run_greedy()
    print()
    print()

    if profile:
        p.stop()
        p.open_in_browser()


if __name__ == '__main__':
    greedy()