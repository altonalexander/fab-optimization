"""End-to-end check of tool recovery + the availability series.

Drives the mirror directly (no broker, no Flask client for the feed) and
asserts the three ways a tool comes back: a status event, activity while
marked down, and the watchdog TTL.
"""
import os
import sys
import time

os.environ["TOOL_DOWN_TTL_S"] = "1"
os.environ["AVAIL_SAMPLE_S"] = "0.2"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as M  # noqa: E402

m = M.mirror
ok = fail = 0


def check(cond, what):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok    {what}")
    else:
        fail += 1
        print(f"  FAIL  {what}")


def status(tool, online, **kv):
    ev = {"type": "TOOL_STATUS", "tool": tool, "online": "1" if online else "0"}
    ev.update(kv)
    m.apply("fab.tool.events", ev)


def online_of(t):
    return M._tool_row(t)["online"]


# roster
for i in range(5):
    status(f"T_{i}", True)
check(M._tool_row("T_0")["online"], "tool comes up online")

# 1. status recovery
status("T_0", False, reason="breakdown", down_s="1800")
check(not online_of("T_0"), "status online=0 takes a tool down")
check(M._tool_row("T_0")["down_reason"] == "breakdown", "down reason is carried")
check(M._tool_row("T_0")["down_s"] == 1800.0, "advertised outage length is carried")
status("T_0", True)
check(online_of("T_0"), "status online=1 brings it back")
check(M._tool_row("T_0")["recovered_by"] is None,
      "a normal status recovery is not flagged as inferred")
check(M._tool_row("T_0")["down_reason"] is None, "recovery clears the down reason")

# 2. activity recovery -- a down tool that starts a lot is running
status("T_1", False, reason="pm")
m.apply("fab.lot.events", {"type": "LOT_STARTED", "lot": "L1", "tool": "T_1"})
check(online_of("T_1"), "a lot starting on a down tool brings it back")
check(M._tool_row("T_1")["recovered_by"] == "activity", "flagged as recovered by activity")

# 3. watchdog TTL
status("T_2", False, reason="breakdown")
check(not online_of("T_2"), "T_2 is down")
time.sleep(1.2)
m.sweep_and_sample()
check(online_of("T_2"), "watchdog restores a tool stuck past the TTL")
check(M._tool_row("T_2")["recovered_by"] == "watchdog", "flagged as recovered by watchdog")

# 4. an up tool is not churned by the hot path
before = dict(m.recovered_by)
for _ in range(50):
    m.apply("fab.lot.events", {"type": "LOT_STARTED", "lot": "L2", "tool": "T_3"})
check(m.recovered_by == before, "LOT_STARTED on an already-up tool records nothing")

# 5. availability series
status("T_4", False)
m.sweep_and_sample()
c = M.app.test_client()
a = c.get("/api/tools/availability").get_json()
check(len(a["ts"]) == len(a["online"]) == len(a["total"]) >= 2,
      "availability returns three parallel arrays")
check(a["now"]["total"] == 5, "roster total is the 5 tools we announced")
check(a["now"]["online"] == 4 and a["now"]["down"] == 1, "one tool down right now")
check(a["online"][-1] <= a["total"][-1], "online never exceeds total")
check(a["ttl_s"] == 1.0, "TTL is reported so the UI can explain the watchdog")

# 6. the availability route is not shadowed by /api/tools/<tool_id>
check(c.get("/api/tools/T_0").get_json()["id"] == "T_0", "tool detail still resolves")
check("now" in c.get("/api/tools/availability").get_json(),
      "availability route wins over the tool-id converter")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
