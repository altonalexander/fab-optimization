"""overlay -- tool qualification matrices read beside the testbed (ADR 0013).

SMT2020's tools are interchangeable within a station family: same speed, no
dedication, no reticles, one setup time per layer. ADR 0012 §3 established
what that costs the experiment -- "which lot to which tool" collapses to
"which lot next", so a per-family assignment has nothing to assign and the
solver's value over a sort key is near zero by construction.

An overlay makes the per-family problem a real matching problem by qualifying
each part on a subset of each family's tools. It is a directory BESIDE the
dataset, never inside it (ADR 0013 §2): `data/smt2020/` is symlinked into the
vendored simulator so that the dispatcher and the simulator cannot read
different loads, and the pristine LVHM has to stay the baseline row.

The same object is read by both sides, which is the whole point:

    simulator   Instance.eligible()      <- Overlay.allows()
    solver      slate_rule._tool_dict()  <- Overlay.parts_for()

so a run cannot have the matrix on one side and not the other. That failure
would look exactly like summary §4.4 -- a row labelled `slate` that was in
truth measuring its fallback -- and it is why `--overlay` keys the warm-up
checkpoint and is stamped on every result row.

Machine naming. SMT2020 has no per-tool identifier: `tool.txt.1l` gives a
family and an `STNQTY`, and `FileInstance` expands that into that many
identical `Machine` objects in file order. So a tool's name here is
`<STNFAM>#<ordinal>` with the ordinal its position within its family, which
is stable for a given dataset and is what `bind()` resolves against a live
instance's `family_machines` (built in the same construction order).
"""
import hashlib
import json
import os


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
OVERLAY_DIR = os.path.join(REPO, 'data', 'smt2020', 'overlays')

QUAL_FILE = 'qualification.tsv'
PROV_FILE = 'provenance.json'


def machine_name(family, ordinal):
    """The stable name of the `ordinal`-th tool of `family`. See module docs."""
    return f'{family}#{ordinal}'


def table_hash(rows):
    """Content hash of a qualification table.

    Over the CANONICAL form -- pairs sorted, machine lists sorted -- so two
    generators that dealt the same matrix in a different order agree, and a
    checkpoint keyed by this hash is keyed by the fab and not by the file.
    """
    h = hashlib.blake2b(digest_size=8)
    for (fam, part) in sorted(rows):
        stns = ';'.join(sorted(rows[(fam, part)]))
        h.update(f'{fam}\t{part}\t{stns}\n'.encode())
    return h.hexdigest()


class Overlay:
    """A qualification matrix, bound to one simulator instance.

    `table` maps (family, part) to the set of qualified machine names. A pair
    ABSENT from the table is fully qualified, so an empty table is the
    pristine fab and `--overlay` on an empty matrix is a no-op by
    construction rather than by a special case.
    """

    def __init__(self, name, table, provenance=None):
        self.name = name
        self.table = {k: set(v) for k, v in table.items()}
        self.provenance = provenance or {}
        self.hash = self.provenance.get('table_hash') or table_hash(self.table)
        self._idx = None          # (family, part) -> frozenset of machine.idx

    # ---- loading -------------------------------------------------------

    @classmethod
    def load(cls, name, root=None):
        d = os.path.join(root or OVERLAY_DIR, name)
        qual = os.path.join(d, QUAL_FILE)
        if not os.path.isfile(qual):
            raise FileNotFoundError(f'no overlay {name!r}: {qual} is missing')
        table = {}
        with open(qual) as f:
            head = f.readline().rstrip('\n').split('\t')
            if head[:3] != ['STNFAM', 'PART', 'STNS']:
                raise ValueError(f'{qual}: expected STNFAM/PART/STNS, got {head}')
            for line in f:
                line = line.rstrip('\n')
                if not line:
                    continue
                fam, part, stns = line.split('\t')[:3]
                table[(fam, part)] = set(s for s in stns.split(';') if s)
        prov = {}
        pj = os.path.join(d, PROV_FILE)
        if os.path.isfile(pj):
            with open(pj) as f:
                prov = json.load(f)
        ov = cls(name, table, prov)
        # The provenance records the hash the generator computed. If the table
        # has been edited since, the checkpoint key and the row stamp would
        # both describe a fab that is not the one being simulated -- exactly
        # the "different fabs at day 90" failure ADR 0013 §5 lists. Recompute
        # and refuse rather than carry a stale label.
        want = prov.get('table_hash')
        got = table_hash(ov.table)
        if want and want != got:
            raise ValueError(
                f'overlay {name!r}: {QUAL_FILE} hashes {got} but '
                f'{PROV_FILE} records {want}. The table was edited after it '
                'was generated; regenerate it rather than relabelling.')
        ov.hash = got
        return ov

    def write(self, root=None):
        d = os.path.join(root or OVERLAY_DIR, self.name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, QUAL_FILE), 'w') as f:
            f.write('STNFAM\tPART\tSTNS\n')
            for (fam, part) in sorted(self.table):
                stns = ';'.join(sorted(self.table[(fam, part)]))
                f.write(f'{fam}\t{part}\t{stns}\n')
        prov = dict(self.provenance, table_hash=self.hash)
        with open(os.path.join(d, PROV_FILE), 'w') as f:
            json.dump(prov, f, indent=2, sort_keys=True)
            f.write('\n')
        return d

    # ---- binding -------------------------------------------------------

    def bind(self, instance):
        """Resolve machine names to indices against `instance` and attach.

        Called once per run, before the first decision point. After this the
        hot path is a dict lookup and a set membership test.
        """
        by_name = {}
        for fam, machines in instance.family_machines.items():
            for ordinal, m in enumerate(machines):
                by_name[machine_name(fam, ordinal)] = m.idx
        idx = {}
        for (fam, part), stns in self.table.items():
            unknown = [s for s in stns if s not in by_name]
            if unknown:
                raise ValueError(
                    f'overlay {self.name!r}: {fam}/{part} names tools that do '
                    f'not exist in this dataset: {sorted(unknown)[:4]}')
            idx[(fam, part)] = frozenset(by_name[s] for s in stns)
        self._idx = idx
        instance.overlay = self
        return self

    # ---- the predicate -------------------------------------------------

    def allows(self, machine, lot):
        """Is `machine` qualified for `lot`'s part in its family?

        The simulator half of the overlay, called from `Instance.qualified`.
        Hot: it runs once per (lot, machine) pair considered at every
        decision point.
        """
        q = self._idx.get((machine.family, lot.part_name))
        return q is None or machine.idx in q

    def parts_for(self, family, ordinal):
        """The parts tool `ordinal` of `family` is qualified for.

        The solver half: `slate_rule` sends this once per tool at `set_tools`
        (ADR 0013 §3.4), so the matrix crosses the ctypes boundary once per
        run rather than per planning cycle. An EMPTY result means the tool is
        qualified for everything -- the same convention as the table's absent
        pairs and as `FamilyTool`'s empty recipe list.
        """
        name = machine_name(family, ordinal)
        parts, constrained = [], False
        for (fam, part), stns in self.table.items():
            if fam != family:
                continue
            constrained = True
            if name in stns:
                parts.append(part)
        return sorted(parts) if constrained else []

    # ---- provenance ----------------------------------------------------

    def stamp(self):
        """What goes on a result row and on the Results tab (ADR 0013 §3.5).

        An overlay row is never laid over a pristine one unlabelled, so this
        travels with the numbers rather than with the person reading them.
        """
        return {'overlay': self.name, 'overlay_hash': self.hash,
                'overlay_pairs': len(self.table)}


def load(name, root=None):
    """`--overlay` in one call. None/'' is the pristine fab."""
    return Overlay.load(name, root) if name else None


def stamp(overlay):
    """`stamp()` for an optional overlay, so callers need no branch."""
    return (overlay.stamp() if overlay is not None
            else {'overlay': None, 'overlay_hash': None, 'overlay_pairs': 0})


def key(overlay):
    """The checkpoint key fragment for an overlay (ADR 0013 §3.5).

    A fab warmed 90 days WITHOUT the matrix has the wrong WIP, so a run under
    an overlay must not resume a pristine checkpoint. Empty for the pristine
    fab, which keeps every existing checkpoint filename byte-identical.
    """
    return '' if overlay is None else f'_ov{overlay.hash}'
