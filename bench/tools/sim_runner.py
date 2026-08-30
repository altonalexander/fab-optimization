"""
sim_runner -- the one place that starts PySCFabSim.

tool_probe.py and sim_feed.py both used to carry their own copy of the same
twenty lines: put the vendored simulator on the path, chdir into it, read the
dataset, seed, build a FileInstance, then pump next_decision_point() /
get_lots_to_dispatch_by_machine() until done. The copies had already drifted
(one printed its Ctrl-C line to stdout, the other to stderr) and every new
consumer would have forked them again. They live here now.

Importing this module has the side effects the baseline requires, because
PySCFabSim imports its own modules flat ("from classes import Lot") and
resolves datasets/ relative to cwd:

    sys.path gains baselines/pyscfabsim and its simulation/ dir
    cwd becomes baselines/pyscfabsim

That has to happen before `from plugins.interface import IPlugin`, so import
this module first and let it do the bootstrapping.

The loop takes plugins, not behaviour. A caller that needs to act between
dispatches passes before_dispatch/after_dispatch; anything that belongs to the
simulation itself belongs in an IPlugin instead, and several plugins can ride
the same run -- which is the point. Measuring a run and publishing it are one
simulation, not two.

Warm-up is deliberately NOT handled here. There are two incompatible schemes
in use -- tool_probe's ResetEvent at one year, which zeroes the machine
counters so the numbers describe steady state, and sim_feed's snapshot, which
runs unpaced with emission suppressed and then publishes the WIP it found.
They disagree about what happens to counters mid-run, so each caller keeps its
own; see docs/adr/0001-lvhm-default-scenario.md, whose figures assume the
ResetEvent semantics.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
SIM = os.path.join(REPO, 'baselines', 'pyscfabsim')
sys.path.insert(0, os.path.join(SIM, 'simulation'))
sys.path.insert(0, SIM)
os.chdir(SIM)

SECONDS_PER_DAY = 86400

# greedy.py zeroes the machine counters after one simulated year so the results
# describe steady state rather than the fill-up transient. Anything comparing
# against the baseline's published numbers has to match it.
RESET_AT = 31536000


def add_common_args(p, days_default):
    """The arguments that describe a run rather than what is done with it.

    Every consumer needs these and they must agree, because two runs are only
    comparable if the dataset, rule, seed and batching match.
    """
    p.add_argument('--dataset', default='SMT2020_LVHM')
    p.add_argument('--days', type=int, default=days_default)
    p.add_argument('--dispatcher', default='fifo')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--batch-strat', default='Demand',
                   choices=['Max', 'Min', 'RoundRobin', 'Demand'])


def normalize_dataset(name):
    """`--dataset LVHM` and `--dataset SMT2020_LVHM` mean the same thing."""
    return name if name.startswith('SMT2020_') else 'SMT2020_' + name


def build(dataset, days, seed, plugins, batch_strat):
    """Load the dataset, seed the RNG, and construct the instance.

    Returns (instance, run_to). Seeding happens before FileInstance is built
    because the constructor draws from the same generator.
    """
    from file_instance import FileInstance
    from randomizer import Randomizer
    from read import read_all

    files = read_all('datasets/' + dataset)
    run_to = SECONDS_PER_DAY * days
    Randomizer().random.seed(seed)
    return FileInstance(files, run_to, True, plugins, None, batch_strat), run_to


def run(instance, run_to, dispatcher, before_dispatch=None,
        after_dispatch=None, stream=sys.stderr):
    """Pump the simulation to completion. Returns True if Ctrl-C stopped it.

    before_dispatch(instance) fires at a decision point, before the rule is
    consulted; after_dispatch(instance, machine, dispatched) fires once the
    machine has been given lots or removed from the usable set. Both see a
    fully consistent instance -- between one dispatch and the next event -- so
    a breakpoint or a snapshot taken there is coherent.

    Ctrl-C is a normal way to end a long run, so it summarises rather than
    raising: callers that need to know check the return value. Note that an
    interrupted run has NOT been finalized, so call instance.finalize() only
    when this returns False.
    """
    from dispatching.dispatcher import dispatcher_map
    from greedy import get_lots_to_dispatch_by_machine

    rule = dispatcher_map[dispatcher]
    try:
        while not instance.done:
            if instance.next_decision_point():
                break
            if instance.current_time > run_to:
                break

            if before_dispatch is not None:
                before_dispatch(instance)

            machine, lots = get_lots_to_dispatch_by_machine(instance, rule)
            if lots is None:
                instance.usable_machines.remove(machine)
                dispatched = False
            else:
                instance.dispatch(machine, lots)
                dispatched = True

            if after_dispatch is not None:
                after_dispatch(instance, machine, dispatched)
    except KeyboardInterrupt:
        print(f'\n  stopped at day {instance.current_time/SECONDS_PER_DAY:.3f}',
              file=stream)
        return True
    return False
