from collections import defaultdict
from typing import Dict, List, Set, Tuple
import os
import sys

import pandas as pd
#sys.path.append(os.path.join('C:/','Users','willi','OneDrive','Documents','Studium','Diplomarbeit','Programm + Datengrundlage','PySCFabSim-release-William-Rodmann','simulation'))
#sys.path.append(os.path.join('C:/','Users','willi','OneDrive','Documents','Studium','Diplomarbeit','Programm + Datengrundlage','PySCFabSim-release-William-Rodmann','simulation', 'gym'))
sys.path.append(os.path.join('C:/','Users','David Heik','Desktop','Arbeit2024','PySCFabSim','Projekt-Reproduktion','Mai-Session', 'PySCFabSim-release','simulation'))
sys.path.append(os.path.join('C:/','Users','David Heik','Desktop','Arbeit2024','PySCFabSim','Projekt-Reproduktion','Mai-Session', 'PySCFabSim-release','simulation', 'gym'))
sys.path.append(os.path.join(os.path.sep, 'projects','p078','p_htw_promentat','Heik_Reproduktion_2', 'simulation'))
sys.path.append(os.path.join(os.path.sep, 'projects','p078','p_htw_promentat','Heik_Reproduktion_2', 'simulation', 'gym'))

from classes import Machine, Route, Lot
from dispatching.dm_lot_for_machine import LotForMachineDispatchManager
from dispatching.dm_machine_for_lot import MachineForLotDispatchManager
from event_queue import EventQueue
from events import MachineDoneEvent, LotDoneEvent, BreakdownEvent, ReleaseEvent
from plugins.interface import IPlugin
import pickle


class Instance:

    # Tool qualification overlay (fab-optimization deviation 8, ADR 0013).
    # A CLASS attribute, not an instance one, so a checkpoint pickled before
    # the overlay existed unpickles into an instance that still answers
    # `eligible` -- the pristine rows resume from exactly such a file.
    # Set by bench/tools/overlay.py's Overlay.bind(); None is the pristine fab.
    overlay = None

    # Reticle library (fab-optimization deviation 9, ADR 0014). A class
    # attribute for the same reason as `overlay`: a checkpoint pickled before
    # masks existed must still unpickle into an instance that dispatches.
    # Set by bench/tools/reticles.py's Reticles.bind(); None is no masks.
    reticles = None

    # Queue-time enforcement (fab-optimization deviation 10, ADR 0016).
    # Class attributes so a checkpoint pickled before they existed still
    # unpickles into an instance that dispatches, as with `overlay` above.
    #
    # OFF by default and that is deliberate: ADR 0008 records that SMT2020's
    # CQT columns are parsed and ignored, every published row was produced
    # that way, and `slate_rule.QTIME_INERT` exists precisely so the solver
    # does not optimise against a signal the environment never punishes. The
    # flag is what lets the pristine rows stay reproducible.
    #
    # cqt_scale multiplies every window: >1 loosens, <1 tightens. It is the
    # Y axis of ADR 0017's grid.
    cqt_enforce = False
    cqt_scale = 1.0

    # cqt_rework=False counts violations but does NOT reroute the lot. That
    # opens the feedback loop ADR 0017 §3 predicts -- violations feed rework
    # feeds load feeds queues feeds violations -- so the same grid run with
    # this on and off isolates the loop's contribution from the constraint's.
    # If the two look the same, rework is a flat tax and the feedback story
    # is wrong.
    cqt_rework = True

    def eligible(self, lot, machine):
        """Can `machine` run `lot` at the step it is waiting for?

        The single place the question is asked. Two things narrow a family:

          * **lot-to-lens dedication** (`SVESTN`/`FORSTEP`) -- upstream, and
            lot state: once a lot has run a dedicated step on a tool it must
            return to that same tool. This is the check that used to be
            written out four times, in both dispatch managers, the greedy
            alternative-machine search and the dedication branch.
          * **tool qualification** -- an overlay (ADR 0013), and master data
            on the tool: a recipe is qualified on a subset of the family.
            Absent an overlay this half is not consulted at all, which is
            why the pristine fingerprints are unchanged by the refactor.
        """
        di = lot.actual_step.order
        if di in lot.dedications and machine.idx != lot.dedications[di]:
            return False
        return self.qualified(lot, machine)

    def mask_free(self, lot, machine):
        """Is the photomask this lot needs available on `machine` right now?

        Deliberately NOT part of `eligible` (ADR 0014 §3.3). `eligible` is a
        STATIC predicate and the dispatch managers call it once per lot, when
        the lot becomes available, to decide which machines it queues on
        (`dm_lot_for_machine.free_up_lots`). A mask's availability is
        time-varying, so asking it there loses every lot whose mask happened
        to be busy at the instant it arrived -- permanently, because the lot
        is never re-offered. That reads as a slow monotonic collapse rather
        than as a bug: utilisation decays as lots fall out one by one.

        So the question is asked where it belongs, at the moment of choosing
        what to run, and `wake_mask_waiters` re-offers the scanners when a
        mask comes back.
        """
        r = self.reticles
        return True if r is None else r.allows(lot, machine, self.current_time)

    def wake_mask_waiters(self):
        """A mask was released: re-offer every idle scanner with work queued.

        A machine leaves `usable_machines` when nothing on it was runnable,
        and only `free_up_machine` puts it back -- which fires when the tool
        finishes a job, and so never comes for a tool that is already idle.
        A mask freed elsewhere in the fab is exactly such a change: it makes
        work runnable on a tool that no event of its own will wake. Without
        this the scanners park one by one and never come back.
        """
        r = self.reticles
        if r is None:
            return
        for fam in r.scanner_families:
            for m in self.family_machines.get(fam, ()):
                if self.free_machines[m.idx] and m.waiting_lots:
                    self.usable_machines.add(m)

    def qualified(self, lot, machine):
        """The overlay half of `eligible`, alone.

        Split out for the one caller that must not have the dedication half:
        `greedy.py`'s dedication branch keys `lot.dedications` by the NEXT
        step (`actual_step.idx + 1`) where `eligible` keys it by the current
        one (`actual_step.order`), and those are different numbers. Routing
        it through the full predicate would change the pristine answer, which
        is the thing the refactor has to leave alone.
        """
        ov = self.overlay
        return True if ov is None else ov.allows(machine, lot)

    def __init__(self, machines: List[Machine], routes: Dict[str, Route], lots: List[Lot],
                 setups: Dict[Tuple, int], setup_min_run: Dict[str, int], breakdowns: List[BreakdownEvent],
                 lot_for_machine, plugins):
        self.plugins: List[IPlugin] = plugins
        self.lot_waiting_at_machine = defaultdict(lambda: (0, 0))

        self.free_machines: List[bool] = []
        self.usable_machines: Set[Machine] = set()
        self.usable_lots: List[Lot] = list()

        self.machines: List[Machine] = [m for m in machines]
        self.family_machines = defaultdict(lambda: [])
        for m in self.machines:
            self.family_machines[m.family].append(m)
        self.routes: Dict[str, Route] = routes
        self.setups: Dict[Tuple, int] = setups
        self.setup_min_run: Dict[str, int] = setup_min_run

        self.dm = LotForMachineDispatchManager() if lot_for_machine else MachineForLotDispatchManager()
        self.dm.init(self)

        self.dispatchable_lots: List[Lot] = lots
        self.dispatchable_lots.sort(key=lambda k: k.release_at)
        self.active_lots: List[Lot] = []
        self.done_lots: List[Lot] = []

        self.events = EventQueue()    

        #self.setup_per_timestep_when_needed = {}
        self.counter_cqt_violated = 0
        self.counter_cqt_rework = 0      # violations that actually rerouted

        self.current_time = 0 

        for plugin in self.plugins:
            plugin.on_sim_init(self)

        self.next_step()

        self.free_up_machines(self.machines)

        for br in breakdowns:
            self.add_event(br)

        self.printed_days = -1

    @property
    def current_time_days(self):
        return self.current_time / 3600 / 24
    
    def process_until_calc(self):
        process_until = []
        if len(self.events.arr) > 0:
            process_until.append(max(0, self.events.first.timestamp))
        if len(self.dispatchable_lots) > 0:
            process_until.append(max(0, self.dispatchable_lots[0].release_at))
        else: 
            process_until.append(0)
        return min(process_until)
    
    def move_event(self, ev):
        temp_time = 0
        for ev_m in ev.machine.events:
            if ev_m.timestamp > temp_time:
                temp_time = ev_m.timestamp
                
        delay = temp_time  - ev.timestamp     
        ev.timestamp += delay
        self.add_event(ev)


    def next_step(self):
        process_until = self.process_until_calc()
        while len(self.events.arr) > 0 and self.events.first.timestamp <= process_until:
            ev = self.events.pop_first()
            self.current_time = max(0, ev.timestamp, self.current_time)
            ev.handle(self)
        ReleaseEvent.handle(self, process_until)

    def free_up_machines(self, machines):
        # add machine to list of available machines
        for machine in machines:
            machine.events.clear()
            self.dm.free_up_machine(self, machine)

            for plugin in self.plugins:
                plugin.on_machine_free(self, machine)

    def free_up_lots(self, lots: List[Lot]):
        # add lot to lists, make it available
        for lot in lots:
            lot.free_since = self.current_time
            step_found = False
            while len(lot.remaining_steps) > 0:
                old_step = None
                if lot.actual_step is not None:
                    lot.processed_steps.append(lot.actual_step)
                    old_step = lot.actual_step
                if lot.actual_step is not None and lot.actual_step.has_to_rework(lot.idx):
                    rw_step = lot.actual_step.rework_step
                    removed = lot.processed_steps[rw_step - 1:]
                    lot.processed_steps = lot.processed_steps[:rw_step - 1]
                    lot.remaining_steps = removed + lot.remaining_steps
                # A missed queue-time window sends the lot back to the step
                # that OPENED it (ADR 0016): that is the operation whose
                # result went stale, so redoing from there is what a fab
                # does. Handled alongside the route's own rework because the
                # mechanism is identical -- move processed steps back onto
                # remaining -- and because doing it here means the closing
                # step has already been paid for, which is the conservative
                # direction: the fab loses the wasted operation AND the
                # rework, so violations cost more rather than less.
                #
                # Scanned from the END: routes are re-entrant, so the same
                # Step object can appear several times in processed_steps and
                # the most recent visit is the one that opened this window.
                if lot.cqt_violated and self.cqt_rework:
                    lot.cqt_violated = False
                    tgt = lot.cqt_open_step
                    pos = None
                    for i in range(len(lot.processed_steps) - 1, -1, -1):
                        if lot.processed_steps[i] is tgt:
                            pos = i
                            break
                    if pos is not None:
                        removed = lot.processed_steps[pos:]
                        lot.processed_steps = lot.processed_steps[:pos]
                        lot.remaining_steps = removed + lot.remaining_steps
                        self.counter_cqt_rework += 1
                    lot.cqt_open_step = None
                elif lot.cqt_violated:
                    # Counted, not rerouted. The flag must still be cleared
                    # or it would fire on the next window this lot opens.
                    lot.cqt_violated = False
                    lot.cqt_open_step = None
                lot.actual_step, lot.remaining_steps = lot.remaining_steps[0], lot.remaining_steps[1:]
                if lot.actual_step.has_to_perform():
                    self.dm.free_up_lots(self, lot)
                    step_found = True
                    for plugin in self.plugins:
                        plugin.on_step_done(self, lot, old_step)
                    break
            if not step_found:
                assert len(lot.remaining_steps) == 0
                lot.actual_step = None
                lot.done_at = self.current_time
                self.active_lots.remove(lot)
                self.done_lots.append(lot)
                for plugin in self.plugins:
                    plugin.on_lot_done(self, lot)

            for plugin in self.plugins:
                plugin.on_lot_free(self, lot)
        # A lot leaving a scanner hands its mask back (ADR 0014 §3.3), which
        # can make work runnable on an idle tool that has no event of its own
        # coming. Re-offer those tools here or they stay parked.
        self.wake_mask_waiters()

    def dispatch(self, machine: Machine, lots: List[Lot]):
        # remove machine and lot from active sets
        self.reserve_machine_lot(lots, machine)
        lwam = self.lot_waiting_at_machine[machine.family]
        self.lot_waiting_at_machine[machine.family] = (lwam[0] + len(lots),
                                                       lwam[1] + sum([self.current_time - l.free_since for l in lots]))
        for lot in lots:
            lot.waiting_time += self.current_time - lot.free_since
            if lot.actual_step.batch_max > 1:
                lot.waiting_time_batching += self.current_time - lot.free_since
            # Queue-time windows (ADR 0016). The clock is read HERE, at the
            # start of processing, which is the industry definition: material
            # degrades while it waits, and the wait ends when the next
            # operation begins, not when the lot joins a queue.
            #
            # CLOSE before OPEN: one step can both close an inbound window and
            # open an outbound one, and doing it the other way round would
            # have a step close the window it had just opened.
            #
            # Note the original commented-out code stored the window LENGTH
            # in cqt_deadline where it needed an absolute time -- the correct
            # line was there, commented out above the wrong one.
            if self.cqt_enforce:
                st = lot.actual_step
                if (lot.cqt_waiting is not None
                        and st.order == lot.cqt_waiting):
                    if lot.cqt_deadline is not None \
                            and self.current_time > lot.cqt_deadline:
                        self.counter_cqt_violated += 1
                        lot.cqt_violated = True
                        for plugin in self.plugins:
                            plugin.on_cqt_violated(self, machine, lot)
                    lot.cqt_waiting = None
                    lot.cqt_deadline = None
                fs = getattr(st, 'cqt_for_step', None)
                if isinstance(fs, (int, float)) and st.cqt_time:
                    lot.cqt_waiting = fs
                    lot.cqt_deadline = (self.current_time
                                        + st.cqt_time * self.cqt_scale)
                    lot.cqt_open_step = st
        # compute times for lot and machine
        lot_time, machine_time, setup_time = self.get_times(self.setups, lots, machine)
        # Mount the photomask (ADR 0014). Moving one between scanners costs
        # transport, which lands in the setup so it delays the lot and blocks
        # the tool exactly as a setup change does. Every lot in a batch shares
        # a (part, step) and therefore a reticle, so one claim covers them all.
        reticle_key = None
        if self.reticles is not None:
            transport_s, reticle_key = self.reticles.claim(
                lots[0], machine, self.current_time)
            setup_time += transport_s
        # compute per-piece preventive maintenance requirement
        for i in range(len(machine.pieces_until_maintenance)):
            machine.pieces_until_maintenance[i] -= sum([l.pieces for l in lots])
            if machine.pieces_until_maintenance[i] <= 0:
                s = machine.maintenance_time[i].sample()
                machine_time += s
                machine.pieces_until_maintenance[i] = machine.piece_per_maintenance[i]
                machine.pmed_time += s
                machine.current_setup = ''
        # compute timebased preventive maintenance requirement
        look_ahead_time = self.current_time + machine_time + setup_time
        for event in self.events.arr:
            if "BreakdownEvent" in str(event) and event.is_breakdown == False and event.machine.idx == machine.idx and event.timestamp <= look_ahead_time:
                self.events.remove(event)
                s = event.handle_timebased_pm(self)
                machine_time += s
            if event.timestamp > look_ahead_time:
                    break

        # if there is ltl dedication, dedicate lot for selected step
        for lot in lots:
            if lot.actual_step.lot_to_lens_dedication is not None:
                lot.dedications[lot.actual_step.lot_to_lens_dedication] = machine.idx
        # decrease / eliminate min runs required before next setup
        if machine.min_runs_left is not None:
            machine.min_runs_left -= len(lots)
            if machine.min_runs_left <= 0:
                machine.min_runs_left = None
                machine.min_runs_setup = None
        # add events
        machine_done = self.current_time + machine_time + setup_time
        lot_done = self.current_time + lot_time + setup_time
        # The mask is unavailable to every other scanner until this one is
        # done with it. This is the interval the solver forbids with
        # AddAtMostOne over the scanners sharing a reticle.
        #
        # Released at LOT done, not machine done: the mask is in the scanner
        # while the lot exposes, and can be pulled as soon as the lot leaves.
        # machine_done also carries preventive maintenance and any breakdown
        # folded in above, and holding a mask through a tool's PM would block
        # every other scanner for a reason that has nothing to do with the
        # mask.
        if self.reticles is not None:
            self.reticles.hold(reticle_key, lot_done)
        ev1 = MachineDoneEvent(machine_done, [machine])
        ev2 = LotDoneEvent(lot_done, [machine], lots)
        self.add_event(ev1)
        self.add_event(ev2)
        machine.events += [ev1, ev2]

        for plugin in self.plugins:
            plugin.on_dispatch(self, machine, lots, machine_done, lot_done)
        return machine_done, lot_done

    def get_times(self, setups, lots, machine):
        proc_t_samp = lots[0].actual_step.processing_time.sample()
        lot_time = proc_t_samp + machine.load_time + machine.unload_time
        for lot in lots:
            lot.processing_time += lot_time
        if len(lots[0].remaining_steps) > 0:
            tt = lots[0].remaining_steps[0].transport_time.sample()
            lot_time += tt
            for lot in lots:
                lot.transport_time += tt
        if lots[0].actual_step.processing_time == lots[0].actual_step.cascading_time:
            cascade_t_samp = proc_t_samp
        else:
            cascade_t_samp = lots[0].actual_step.cascading_time.sample()
        machine_time = cascade_t_samp + (machine.load_time + machine.unload_time if not machine.cascading else 0)
        new_setup = lots[0].actual_step.setup_needed
        #if new_setup != '':
        #    self.setup_count_when_needed(machine, new_setup)
        if new_setup != '' and machine.current_setup != new_setup:
            if lots[0].actual_step.setup_time is not None:
                setup_time = lots[0].actual_step.setup_time             # SetupTime für in der Route geplante Setups
            elif (machine.current_setup, new_setup) in setups:
                setup_time = setups[(machine.current_setup, new_setup)] # SetupTime für in setup.txt für DE_BE_ Maschinen
            elif ('', new_setup) in setups:
                setup_time = setups[('', new_setup)]                    # SetupTime für in setup.txt für Implant_91/128/131
            else:
                setup_time = 0                                          # SetupTime, wenn in DE_BE kein Setup vorhanden ist
        else:
            setup_time = 0
        if new_setup in self.setup_min_run and machine.min_runs_left is None and setup_time > 0:
            machine.min_runs_left = self.setup_min_run[new_setup]
            machine.min_runs_setup = new_setup
            machine.has_min_runs = True
        if setup_time > 0:
            machine.last_setup_time = setup_time
        machine.utilized_time += machine_time
        machine.setuped_time += setup_time
        machine.last_setup = machine.current_setup
        machine.current_setup = new_setup
        return lot_time, machine_time, setup_time

    def reserve_machine_lot(self, lots, machine):
        self.dm.reserve(self, lots, machine)

    def add_event(self, to_insert):
        self.events.ordered_insert(to_insert)

    def next_decision_point(self):
        return self.dm.next_decision_point(self)

    def handle_breakdown(self, machine, delay):
        ta = []
        for ev in machine.events: # nur Events mit LoteDone oder MachineDone werden verschoben
            if ev in self.events.arr:
                ta.append(ev)
                self.events.remove(ev)
        for ev in ta:
            ev.timestamp += delay
            self.add_event(ev)

    @property
    def done(self):
        return len(self.dispatchable_lots) == 0 and len(self.active_lots) == 0

    def finalize(self):
        for plugin in self.plugins:
            plugin.on_sim_done(self)

    def print_progress_in_days(self):
        import sys
        if int(self.current_time_days) > self.printed_days:
            self.printed_days = int(self.current_time_days)
            if self.printed_days > 0:
                sys.stderr.write(
                    f'\rDay {self.printed_days}===Throughput: {round(len(self.done_lots) / self.printed_days)}/day=')
                sys.stderr.flush()

    # def setup_count_when_needed(self, machine, setup_needed):
    #     if self.current_time > 0:
    #         #if self.current_time not in self.setup_per_timestep_when_needed[machine.family] or self.setup_count_when_needed == {}:
    #             self.setup_per_timestep_when_needed[int(self.current_time)] = [setup_needed, 'Verfügbar',{machine.family: {}}, 'Nicht frei', {machine.family: {}}]
    #             self.setup_per_timestep_when_needed[int(self.current_time)][2][machine.family]= {machine.current_setup: 1}
    #             for machines in self.machines:
    #                 if machines.idx != machine.idx:
    #                     if self.free_machines[machines.idx] == True:
    #                         if machines.family == machine.family:
    #                             if machines.current_setup in self.setup_per_timestep_when_needed[int(self.current_time)][2][machine.family]:
    #                                 self.setup_per_timestep_when_needed[int(self.current_time)][2][machine.family][machines.current_setup] += 1
    #                             else:
    #                                 self.setup_per_timestep_when_needed[int(self.current_time)][2][machine.family][machines.current_setup] =  1
    #                     else:
    #                         if machines.family == machine.family:
    #                             if machines.current_setup in self.setup_per_timestep_when_needed[int(self.current_time)][4][machine.family]:
    #                                 self.setup_per_timestep_when_needed[int(self.current_time)][4][machine.family][machines.current_setup] += 1
    #                             else:
    #                                 self.setup_per_timestep_when_needed[int(self.current_time)][4][machine.family][machines.current_setup] =  1



    # def save_setup_when_needed(self):
    #     with open('setup_when_needed.pkl', 'wb') as f:
    #         pickle.dump(self.setup_per_timestep_when_needed, f)
    
                
