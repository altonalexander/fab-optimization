"""Replay a generated feed file and assert the tool roster does not decay.

Before the fix the feed emitted only online=0, so this reduced monotonically to
zero. It is the regression that matters: everything else in the tool view is
downstream of the roster being right.
"""
import json
import sys

path = sys.argv[1]
roster, down_events, up_events, init = set(), 0, 0, 0
online = {}
low_water = None
seen_lots = False

for line in open(path):
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    if rec.get("topic") != "fab.tool.events":
        seen_lots = True
        continue
    ev = dict(kv.split("=", 1) for kv in rec["payload"].split(";") if "=" in kv)
    if ev.get("type") != "TOOL_STATUS":
        continue
    tool, up = ev["tool"], ev["online"] != "0"
    if tool not in roster:
        roster.add(tool)
        init += 1
    # Count transitions, not events. A tool can break down again while already
    # down -- a second outage extends the first rather than opening a new one,
    # so the raw event count would never balance against recoveries.
    elif up and not online.get(tool, True):
        up_events += 1
    elif not up and online.get(tool, True):
        down_events += 1
    online[tool] = up
    if not seen_lots:
        continue
    live = sum(1 for v in online.values() if v)
    low_water = live if low_water is None else min(low_water, live)

total = len(roster)
final = sum(1 for v in online.values() if v)
stuck = sorted(t for t, v in online.items() if not v)

print(f"roster        {total}")
print(f"announced     {init}")
print(f"down events   {down_events}")
print(f"recoveries    {up_events}")
print(f"low water     {low_water}")
print(f"online at end {final}  ({100*final/total:.1f}%)")
print(f"still down    {len(stuck)}  {stuck[:5]}")

fail = []
if down_events == 0:
    fail.append("no breakdowns in this feed -- the fixture proves nothing")
if up_events == 0:
    fail.append("no recovery events: the feed is still one-way")
# The books must balance: every outage either closed or is still open at the
# horizon. A percentage threshold would be wrong here -- a short run truncates
# more outages than a long one, so the ratio is a property of the window, not
# of the code. This identity holds for any window.
if up_events + len(stuck) != down_events:
    fail.append(f"{down_events} outages but {up_events} recoveries and "
                f"{len(stuck)} still open -- these should balance")
# The real symptom: availability trending to zero.
if low_water is not None and low_water < 0.9 * total:
    fail.append(f"roster dipped to {low_water}/{total} -- tools are being stranded")
if len(stuck) > 0.05 * total:
    fail.append(f"{len(stuck)}/{total} tools left down at the end")

for f in fail:
    print("FAIL " + f)
print("\nPASS" if not fail else "\nFAILED")
sys.exit(1 if fail else 0)
