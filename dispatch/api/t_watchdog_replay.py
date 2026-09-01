"""Replay a one-way feed (downs, no recoveries) through the mirror.

The feed fix and the API watchdog are independent guards. This one asserts the
second still holds the roster up when the first is absent -- which is what a
dropped partition, a --tool-prefix filter, or any future producer that forgets
to emit online=1 looks like.
"""
import json
import os
import sys

os.environ["TOOL_DOWN_TTL_S"] = "5"
os.environ["AVAIL_SAMPLE_S"] = "3600"      # no background sweep; we drive it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as M  # noqa: E402

m = M.mirror
for line in open(sys.argv[1]):
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    if rec.get("payload") is None:      # compaction tombstone
        continue
    ev = dict(kv.split("=", 1) for kv in rec["payload"].split(";") if "=" in kv)
    # Drop every recovery, so the mirror sees the strictly one-way stream.
    if ev.get("type") == "TOOL_STATUS" and ev.get("online") != "0":
        if ev["tool"] in m.tools:
            continue
    m.apply(rec["topic"], ev)

ids = set(m.tools) | set(m.tool_stats)
online = sum(1 for t in ids if m.tools.get(t, {}).get("online", True))
by = {}
for how in m.recovered_by.values():
    by[how] = by.get(how, 0) + 1

print(f"roster     {len(ids)}")
print(f"online     {online}  ({100*online/len(ids):.1f}%)")
print(f"restored   {by}")
print(f"still down {len(m.down_since)}")

fail = []
if not by.get("activity"):
    fail.append("activity recovery never fired on a one-way feed")
if online < 0.9 * len(ids):
    fail.append(f"roster still decayed to {online}/{len(ids)}")
for f in fail:
    print("FAIL " + f)
print("\nPASS" if not fail else "\nFAILED")
sys.exit(1 if fail else 0)
