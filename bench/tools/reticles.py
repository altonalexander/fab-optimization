"""reticles -- a reticle library read beside the testbed (ADR 0014).

ADR 0013 added tool qualification and it did not separate the rules. The
reason, with hindsight, is the SHAPE of the constraint rather than its
strength: qualification is *unary*. "Lot L may run on tool set M(L)" filters
each (lot, tool) pair on its own, and a greedy sort handles that with no
cleverness at all -- walk the queue in priority order, skip the lots this
tool is not qualified for, take the first that fits. There is no
combinatorial structure for an assignment solver to exploit, which is why
tightening the matrix from 0.80 to 0.65 moved nothing.

A reticle is a *coupling* constraint, and that is a different class of
problem. One physical mask cannot be mounted on two scanners at once, so two
lots needing the same reticle cannot run concurrently even when both
scanners are idle and both lots are fully qualified. The value of assigning
lot A to scanner 1 now depends on what is assigned to scanner 2. A sort key
cannot represent that -- it scores each lot independently and commits
per-tool as tools free -- while the CP-SAT model already encodes it exactly,
as `AddAtMostOne` over the scanner assignments grouped by reticle
(`dispatch/include/fab/solver.hpp`). Both solver paths have carried the
constraint since ADR 0009; nothing has ever fed them a reticle id.

Moving a reticle between scanners costs transport time, so there is also a
run-length decision: keeping a mask mounted for consecutive lots of the same
layer saves the move. That is a sequencing tradeoff a myopic rule cannot
see and is the second thing the solver is being asked to find.

Identity. A reticle is per (part, photo layer). The layer is the step's
`DESC`, not its order, so a lot sent back by rework returns to the SAME
physical mask -- which is what happens in a fab.

Scope. Only the scanners: station group `Litho` minus the `LithoTrack_*`
families, which are the coat/develop tracks. `LithoMet`/`Litho_REG` are a
separate group (metrology and registration) and hold no mask.

Copies. High-volume parts get more than one mask, which is what a real fab
buys to keep its bottleneck layer fed. The count is per reticle and is the
knob that decides how hard the constraint bites.
"""
import hashlib
import os

RETICLE_FILE = 'reticles.tsv'
HEADER = ['PART', 'LAYER', 'RETICLE', 'COPIES']

#: Station group holding the scanners, and the prefix inside it that is NOT
#: a scanner. Kept here so the simulator, the generator and the solver feed
#: all answer "is this tool a scanner" the same way.
SCANNER_GROUP = 'Litho'
NOT_SCANNER_PREFIX = 'LithoTrack'


def is_scanner_family(family, group):
    """Does `family` (in station group `group`) hold a reticle?

    The station group is required, not inferred from the name: `Litho_REG_*`
    starts with `Litho_` but is registration metrology in group `Litho_Met`
    and holds no mask, so a name-prefix rule would silently add 20 tools to
    the scanner set and misreport scanner utilisation.
    """
    return group == SCANNER_GROUP and not family.startswith(NOT_SCANNER_PREFIX)


def scanner_families_for(dataset):
    """The scanner families of `dataset`, read from its tool master.

    Available without a reticle library so that scanner utilisation can be
    reported on the pristine fab too -- a scanner-scoped number is only
    meaningful against the same number without masks.
    """
    from read import read_all
    files = read_all('datasets/' + dataset)
    return {d['STNFAM'] for d in files['tool.txt.1l']
            if is_scanner_family(d['STNFAM'], d['STNGRP'])}


def table_hash(table, transport_s):
    """Content hash of a reticle library, over its canonical form."""
    h = hashlib.blake2b(digest_size=8)
    h.update(f'transport_s={transport_s:g}\n'.encode())
    for (part, layer) in sorted(table):
        ret, copies = table[(part, layer)]
        h.update(f'{part}\t{layer}\t{ret}\t{copies}\n'.encode())
    return h.hexdigest()


class Reticles:
    """A reticle library, bound to one simulator instance.

    `table` maps (part, layer) to (reticle_id, copies). A (part, layer) pair
    ABSENT from the table needs no reticle, so an empty library is the
    pristine fab and costs nothing on the hot path.
    """

    def __init__(self, table, transport_s=900.0, scanner_families=(),
                 provenance=None):
        self.table = {k: (str(v[0]), int(v[1])) for k, v in table.items()}
        self.transport_s = float(transport_s)
        self.scanner_families = set(scanner_families)
        self.provenance = provenance or {}
        self.hash = table_hash(self.table, self.transport_s)
        self._scanner_idx = None     # machine.idx -> True, bound per instance

    # ---- loading -------------------------------------------------------

    @classmethod
    def load_dir(cls, d):
        """Read `reticles.tsv` from an overlay directory, or None if absent."""
        path = os.path.join(d, RETICLE_FILE)
        if not os.path.isfile(path):
            return None
        table = {}
        transport_s, fams = 900.0, []
        with open(path) as f:
            for line in f:
                line = line.rstrip('\n')
                if not line:
                    continue
                if line.startswith('#'):
                    # `# transport_s=900` / `# scanner_families=A;B` header
                    k, _, v = line[1:].strip().partition('=')
                    if k.strip() == 'transport_s':
                        transport_s = float(v)
                    elif k.strip() == 'scanner_families':
                        fams = [s for s in v.split(';') if s]
                    continue
                cols = line.split('\t')
                if cols[:4] == HEADER:
                    continue
                part, layer, ret, copies = cols[:4]
                table[(part, layer)] = (ret, int(copies))
        return cls(table, transport_s, fams)

    def write_dir(self, d):
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, RETICLE_FILE)
        with open(path, 'w') as f:
            f.write(f'# transport_s={self.transport_s:g}\n')
            f.write('# scanner_families='
                    + ';'.join(sorted(self.scanner_families)) + '\n')
            f.write('\t'.join(HEADER) + '\n')
            for (part, layer) in sorted(self.table):
                ret, copies = self.table[(part, layer)]
                f.write(f'{part}\t{layer}\t{ret}\t{copies}\n')
        return path

    # ---- binding -------------------------------------------------------

    def bind(self, instance):
        """Attach to `instance` and build the per-run runtime state.

        `holder` is where each physical mask currently sits (a machine index
        or None), `locked_until` when it next becomes movable. A mask is
        held from dispatch until the machine finishes: that is the interval
        during which no other scanner can have it, and it is exactly the
        interval `AddAtMostOne` forbids on the solver side.
        """
        self._scanner_idx = set()
        for fam, machines in instance.family_machines.items():
            if fam in self.scanner_families:
                for m in machines:
                    self._scanner_idx.add(m.idx)
        self.holder = {}
        self.locked_until = {}
        self.moves = 0            # reticle transports paid, a run statistic
        for (ret, copies) in set(self.table.values()):
            for c in range(copies):
                self.holder[(ret, c)] = None
                self.locked_until[(ret, c)] = 0.0
        instance.reticles = self
        return self

    # ---- the predicate -------------------------------------------------

    def needed(self, lot, machine):
        """The reticle `lot` needs to run on `machine`, or None.

        None whenever the tool is not a scanner or the layer holds no mask,
        which keeps every non-litho decision on exactly the pristine path.
        """
        if machine.idx not in self._scanner_idx:
            return None
        ent = self.table.get((lot.part_name, lot.actual_step.step_name))
        return None if ent is None else ent

    def available_copy(self, ret, copies, machine_idx, now):
        """A copy of `ret` that could run on `machine_idx` at `now`.

        Prefers one already mounted here -- that is the copy that costs no
        transport -- and otherwise takes any copy that is not locked. Returns
        `(key, needs_move)` or None if every copy is in use elsewhere.
        """
        best = None
        for c in range(copies):
            k = (ret, c)
            if self.locked_until.get(k, 0.0) > now:
                continue
            if self.holder.get(k) == machine_idx:
                return k, False
            if best is None:
                best = k
        return (best, True) if best is not None else None

    def allows(self, lot, machine, now):
        """Can `machine` start `lot` now, as far as the mask is concerned?"""
        ent = self.needed(lot, machine)
        if ent is None:
            return True
        ret, copies = ent
        return self.available_copy(ret, copies, machine.idx, now) is not None

    def claim(self, lot, machine, now):
        """Mount a copy on `machine`, and report the move cost and the copy.

        Called from `Instance.dispatch` once the decision is made. Returns
        `(transport_s, key)`; the transport is zero when the mask was already
        on this scanner, which is the run-length incentive a solver can plan
        for and a sort key cannot see. The caller must then `hold()` the key
        until the machine finishes -- two steps because the transport lands
        in the setup time, which is itself an input to when that will be.
        """
        ent = self.needed(lot, machine)
        if ent is None:
            return 0.0, None
        ret, copies = ent
        got = self.available_copy(ret, copies, machine.idx, now)
        if got is None:
            # Only reachable if a caller dispatched without asking allows().
            return 0.0, None
        k, needs_move = got
        if needs_move:
            self.moves += 1
        self.holder[k] = machine.idx
        # Locked from now, so a second dispatch in the same instant cannot
        # take the same mask before `hold()` records the real end time.
        self.locked_until[k] = float('inf')
        return (self.transport_s if needs_move else 0.0), k

    def hold(self, key, until):
        """Release the mask at `until` -- when the scanner finishes the run."""
        if key is not None:
            self.locked_until[key] = until

    def lot_reticle(self, lot):
        """The reticle id for `lot` at its current step, or '' for none.

        The solver half (ADR 0014 §3.4): `slate_rule` sends this per lot, and
        `solver.hpp` groups scanner assignments by it. Empty means the lot
        needs no mask, which is the same "empty is unconstrained" convention
        the qualification matrix uses.
        """
        ent = self.table.get((lot.part_name, lot.actual_step.step_name))
        return '' if ent is None else ent[0]

    def scanner_indices(self):
        """Machine indices that hold a reticle, for the solver's model."""
        return sorted(self._scanner_idx or ())

    # ---- provenance ----------------------------------------------------

    def stamp(self):
        return {'reticles': len(set(r for r, _ in self.table.values())),
                'reticle_pairs': len(self.table),
                'reticle_hash': self.hash,
                'reticle_transport_s': self.transport_s}
