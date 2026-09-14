"""trim -- right-size the tool set beside the testbed (ADR 0015).

SMT2020 LVHM ships 913 process tools (plus 400 `Delay` placeholders) and, at
the start rate `order.txt` schedules, they carry a **fab-wide designed load of
55%**. The load is also badly unbalanced: exactly ONE family sits above 90%
(`LithoMet_FE_19`, 14 tools) while 44 of 60 families are below 80% and
`DE_FE_86` has 118 tools in a single family.

That is the reason ADR 0013 and ADR 0014 both measured nothing. A constraint
only costs the fab something if blocking PROPAGATES, and in a fab with this
much slack a blocked lot always finds another tool -- block a lot at DE_FE_86
and it has 117 alternatives. Qualification could not bite, reticle exclusivity
could not bite, and the knee sat at 1.03x starts not because the fab was full
but because one 14-tool family saturated while the average ran at 55%.

So the fab cannot be stressed as a whole; it can only have its single
constraint destroyed. Right-sizing fixes that: remove tools from the
over-provisioned families until most of them run where a real fab runs, and
blocked work has nowhere else to go.

**A trim is data beside the dataset, never an edit to it.** ADR 0001 makes
LVHM the default scenario and ADR 0013 §2 refused to touch `data/smt2020/` so
the pristine fab stays the comparable baseline; a trim is the same kind of
object as a qualification matrix or a reticle library. It is applied to the
tool master BEFORE `FileInstance` is constructed, so machine indices,
`family_machines` and every utilisation denominator are consistent by
construction -- there is no surgery on a built instance and nothing to get
subtly wrong.

The honest cost: a trimmed fab is no longer the published SMT2020 and cannot
be compared to external results. That is acceptable for "does the solver earn
its place", where the comparison that matters is rule against rule on one fab,
and it is why the trim is a named variant rather than a new default.
"""
import hashlib
import os

TRIM_FILE = 'trim.tsv'
HEADER = ['STNFAM', 'STNQTY']

#: Never trimmed: the Delay pseudo-toolset is fixed waits, not capacity
#: (ADR 0008), so its count is not a sizing decision.
NEVER_TRIM_PREFIX = 'Delay'


def table_hash(table):
    h = hashlib.blake2b(digest_size=8)
    for fam in sorted(table):
        h.update(f'{fam}\t{table[fam]}\n'.encode())
    return h.hexdigest()


class Trim:
    """A per-family tool count. Families absent from the table keep theirs."""

    def __init__(self, name, table, provenance=None):
        self.name = name
        self.table = {k: int(v) for k, v in table.items()}
        self.provenance = provenance or {}
        self.hash = table_hash(self.table)

    # ---- loading -------------------------------------------------------

    @classmethod
    def load(cls, name, root=None):
        import overlay as overlay_mod
        d = os.path.join(root or overlay_mod.OVERLAY_DIR, name)
        path = os.path.join(d, TRIM_FILE)
        if not os.path.isfile(path):
            raise FileNotFoundError(f'no trim {name!r}: {path} is missing')
        table = {}
        with open(path) as f:
            for line in f:
                line = line.rstrip('\n')
                if not line or line.startswith('#'):
                    continue
                cols = line.split('\t')
                if cols[:2] == HEADER:
                    continue
                table[cols[0]] = int(float(cols[1]))
        prov = {}
        import json
        pj = os.path.join(d, 'provenance.json')
        if os.path.isfile(pj):
            with open(pj) as f:
                prov = json.load(f)
        return cls(name, table, prov)

    def write(self, root=None):
        import json
        import overlay as overlay_mod
        d = os.path.join(root or overlay_mod.OVERLAY_DIR, self.name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, TRIM_FILE), 'w') as f:
            f.write('# per-family tool count; families absent keep theirs\n')
            f.write('\t'.join(HEADER) + '\n')
            for fam in sorted(self.table):
                f.write(f'{fam}\t{self.table[fam]}\n')
        prov = dict(self.provenance, trim_hash=self.hash)
        with open(os.path.join(d, 'provenance.json'), 'w') as f:
            json.dump(prov, f, indent=2, sort_keys=True)
            f.write('\n')
        return d

    # ---- application ---------------------------------------------------

    def apply(self, files):
        """Rewrite `STNQTY` in a loaded tool master, in place.

        Called from `sim_runner.build` between `read_all` and `FileInstance`.
        Doing it here rather than on a built instance is the whole design: the
        simulator then constructs exactly the fab we asked for, so machine
        indices are dense and contiguous, `family_machines` is right, and the
        utilisation denominator is right -- none of which is true if you delete
        machines afterwards.
        """
        changed, removed = 0, 0
        for row in files['tool.txt.1l']:
            fam = row['STNFAM']
            if fam.startswith(NEVER_TRIM_PREFIX) or fam not in self.table:
                continue
            was = int(float(row['STNQTY']))
            now = max(1, self.table[fam])
            if now == was:
                continue
            row['STNQTY'] = now
            changed += 1
            removed += was - now
        return changed, removed

    # ---- provenance ----------------------------------------------------

    def stamp(self):
        return {'trim': self.name, 'trim_hash': self.hash,
                'trim_families': len(self.table)}


def load(name, root=None):
    """`--trim` in one call. None/'' is the full tool set."""
    return Trim.load(name, root) if name else None


def stamp(trim):
    return (trim.stamp() if trim is not None
            else {'trim': None, 'trim_hash': None, 'trim_families': 0})


def key(trim):
    """Checkpoint key fragment. A trimmed fab is a DIFFERENT fab: it must not
    resume a checkpoint warmed on the full tool set, for the reason ADR 0013
    §3.5 gives about the qualification matrix. Empty for the full fab, so
    every existing checkpoint filename is unchanged."""
    return '' if trim is None else f'_tr{trim.hash}'
