# Architecture decision records

Why things are the way they are, what each choice assumes, and what would
overturn it. Not how anything works — that lives beside the code.

| | |
|---|---|
| [0000](0000-motivation-scope-and-boundaries.md) | Motivation, scope, and what this project is not |
| [0001](0001-lvhm-default-scenario.md) | LVHM is the default scenario |
| [0002](0002-dispatcher-inside-pyscfabsim.md) | Run the dispatcher inside PySCFabSim to compare it |
| [0003](0003-cold-start-snapshot-and-delta.md) | Cold start: snapshot + delta over compacted topics |
| [0004](0004-kafka-for-state-postgres-for-runs.md) | Kafka holds live state, Postgres holds runs |
| [0005](0005-cp-kafka-image.md) | cp-kafka rather than apache/kafka |
| [0006](0006-zeromq-inbound-kafka-outbound.md) | ZeroMQ inbound, Kafka outbound: the transport split |
| [0007](0007-playback-is-a-cursor-not-a-throttle.md) | Playback is a cursor, not a throttle: run unpaced, replay at will |
| [0008](0008-what-pyscfabsim-simplifies.md) | What PySCFabSim simplifies (transport, delay, storage, CQT), and what that hides |

## Why these are central and not filed under bench/ or dispatch/

Most decisions worth recording here cross that boundary, and not by accident:
this repository exists because the dispatcher and the simulator must read the
same data. A choice that fits cleanly inside one half is usually a local
implementation detail, not an ADR. Filing the crossing ones under one subtree
means picking a home that is half wrong, and the reader who needs it looks in
the other place.

So: **reference is local, decisions are central.** `BUILD.md`,
`dispatch/READMEinfra.md`, `baselines/pyscfabsim/UPSTREAM.md` and
`bench/README.md` describe how a thing works and belong beside it. These
describe why it was chosen, and they outlive the code they were about.

## Rules

**Supersede, never rewrite.** An ADR is a record of what was believed when a
choice was made. If measurement later contradicts it, add a status note and
write a new ADR that supersedes it — do not tidy the old one. 0001 currently
carries a decision whose leading argument a 60-day probe has falsified, and
that is the most useful thing in it. Editing it to look clean would destroy
exactly the information a reader needs.

**Every ADR states how it could be wrong.** A "how would we know" section is
mandatory. It is what separates these from rationalisation after the fact, and
in this repo it is what caused the probe that falsified part of 0001.

**Status is one of:** Proposed · Accepted · Superseded by NNNN · Partially
falsified (see §). Put it near the top, with a date.

**Numbers are claimed, not assigned later.** Several agents work in this repo
at once. If you find a number taken, take the next one — a collision is a
rename, not a crisis.

## Format

Follow 0001. It is a better template than the standard Nygard form and it was
written here:

1. What the options are
2. Why we picked this one
3. What that assumes
4. How to know whether it is right
5. Evidence in hand — including evidence against
6. What follows from the decision
7. What we would do if it turns out to be wrong

Short decisions do not need all seven. 0005 is four paragraphs, and that is the
right length for it.
