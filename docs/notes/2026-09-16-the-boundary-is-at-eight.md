# Lab notes — the boundary is at eight

**2026-09-16, small hours.** The sweep and the re-tuning grid both landed.
This is the entry that checks the predictions written
[yesterday afternoon](2026-09-15-what-we-are-actually-testing.md) against what
came back. Numbers: [`bench/results/cqtsweep90/`](../../bench/results/cqtsweep90/)
and [`bench/results/cqtretune/`](../../bench/results/cqtretune/), regenerable
with `bench/tools/analyse_cqtsweep.py` and `analyse_qt_retune.py`.

---

## The prediction I got wrong

I wrote: *"the interesting region is narrow, and I expect it between 2 and 5
rather than at 1."*

Wrong, and not marginally. The viable region is **at or above 5**, and the
only scale where the untuned rule holds every seed is **10** — where we were
already operating. Below that the fab does not degrade, it fails:

| scale | lots/day (56.6 released) | worst WIP slope | worst scrap | verdict |
|---|---|---|---|---|
| 10 | 56.8 | +4.8 | 0 | viable, all five seeds |
| 5 | 41.4 | +32.7 | 347 | bifurcates, 2 of 5 |
| 3 | 23.9 | +28.9 | 871 | collapses |
| 2 | 23.6 | +29.7 | 678 | collapses |
| 1 | 21.1 | +24.2 | 1,400 | collapses |

At scale 3 and below the fab ships 23 lots a day against 56.6 released and WIP
climbs 20–30 lots a day on every seed. That is a fab destroying material faster
than it ships it. I had also called it "a gradient, not a cliff" from the
mid-run progress lines, and that was wrong too — the mid-run lines were mostly
warm-up and early transient. **Do not read a running log as a result.**

## The trap in the middle of the table

On-time delivery *rises* as the fab dies. 97.3 % at scale 1 on seed 0; 94–98 %
across scales 2 and 3. Read that column alone and scale 1 is the best
configuration we have ever run.

It is survivorship. The lots that escape a collapsing fab are the ones that
never got stuck, so they are fast (25-day cycle time against 37) and they are
on time. The ~60 % that never ship at all are simply absent from the average.
Throughput and the WIP slope carry the signal; on-time cannot be read without
them. Worth remembering that the paper leads with on-time.

## What tuning actually bought

The sweep ran the **untuned** rule — `QT_PROMOTE_FRAC` defaults to 1.0, while
the paper's tuned rule is 0.50 — so "scale 5 is infeasible" was not a claim the
data supported. Yesterday's note predicted the right threshold is
scale-dependent. It is, and it moved the boundary by a whole step:

| scale | frac | seeds viable | mean lots/day | mean on-time | mean tardiness |
|---|---|---|---|---|---|
| 10 | 1.00 | **5/5** | 56.8 | 94.32 % | 262.6 |
| 8 | 0.25 | **5/5** | 57.6 | 94.56 % | 209.3 |
| 8 | 0.50 | **5/5** | 57.1 | 95.18 % | 205.3 |
| 5 | 0.50 | 2/5 | 44.8 | 88.25 % | 2,470.6 |
| 5 | 1.00 | 2/5 | 41.4 | 88.94 % | 865.1 |
| 3 | any | 0/5 | ~21 | — | — |

Viable here means WIP not *growing* (a draining fab is not a diverging one, so
the test is one-sided) and scrap under 1 % of releases.

So **scale 8 at frac 0.50 is the operating point**, by the criterion fixed
before the numbers arrived: the hardest fab on which the tuned baseline is
viable on *every* seed. It is a genuinely harder fab than the scale 10 we have
been publishing, at full throughput and zero scrap.

Scale 5 is the **robustness boundary**, and it belongs in the paper as that
rather than as an operating point. Tuning rescues individual cells there — seed
1 goes from 2,412 lots with 325 scrapped to 5,264 lots with 1 — but no tuning
rescues all five, and the best we found still loses seeds 0 and 4 outright.

## Why we are not running at five, however tempting

Scale 5 is where simple rules bifurcate, which makes it exactly where a solver
would look most heroic. That is the reason to be suspicious of it, not the
reason to choose it.

If the baseline has collapsed, "the solver keeps the fab stable where the rule
cannot" reduces to *the solver beat a broken baseline*, and the honest referee
response is that the baseline was misconfigured. The claim is only worth making
against a rule that is working. At scale 8 the tuned rule works everywhere —
and still leaves room: seed 2 sits at 84.0 % on-time with 910 lot-days of
tardiness while seed 3 is at 99.8 %. That spread is the solver's opportunity,
and it is an honest one.

## The difficulty ordering changed, and that is worth noticing

Under the old window, seeds 0 and 4 were the hard draws and 1, 2, 3 the easy
ones; the replicates we published were on 0, 1 and 2. On the corrected window
at the new operating point the hard seeds are **2 and 4** (84.0 % and 95.3 %),
while seed 0 — the seed the paper's headline result rests on — is now at
97.1 %.

Nothing about the published comparison is invalidated: every policy in a table
met the same fab. But any intuition of the form "seed 0 is the hard one" is an
artefact of a window measured from the wrong instant, and it should not survive
into the next round. It is a small mercy that we were already committed to
replicating every seed rather than the three we happened to pick.

## A fourth defect, found by designing the next experiment

The checkpoint key did not record `QT_PROMOTE_FRAC`. `qt` is a *family* of
rules and 0.50 warms a measurably different fab from 1.0, but the key called
both `qt` — so a fab warmed under one tuning could be silently resumed under
another. That is the [ADR 0013](../adr/0013-tool-dedication-overlay.md) §3.5 failure
for the third time, after the qualification matrix and queue-time enforcement.

Caught *before* it corrupted anything: the re-tuning grid would have shared one
warm-up across all three tunings and produced confident nonsense. Fixed with a
fragment that is deliberately **not** empty at the default value, because an
existing `_qt_` checkpoint's tuning was never recorded and cannot honestly be
claimed for either setting — so every one of them is orphaned and rebuilt.

Three of the four defects this week were found by *designing an experiment* or
*writing a test*, and only one by looking at a result. That is the argument for
doing the audit work before the expensive runs rather than after.

## Next

1. Re-run the operating point (scale 8, frac 0.50) at **full length**, 180-day
   window. The 90-day window was for finding the boundary, not for publishing.
2. Solver replicates there, on **all five seeds including seed 4**, three
   replicates each, against the tuned rule.
3. Objective v2, which exists precisely because v1 was effectively
   shortest-job-first, and the easy-seed losses look like exactly that.
4. Scale 5 as a reported robustness boundary, not an operating point.

The prediction I will write down now, to be checked the same way: at scale 8
the solver's margin over the tuned rule should be **largest on seeds 2 and 4**
and near zero on seed 3, because that is where the rule has left anything on
the table. If the margin turns up somewhere else, the coupling story is not
what is driving it.
