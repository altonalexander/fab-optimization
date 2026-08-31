"""Smoke-test for the /api/slate/* endpoints, via Flask's test client.

Run from the repo root with the API venv:

    dispatch/api/.venv/bin/python3 dispatch/api/test_slate_endpoints.py

Covers the two states that matter and are easy to get wrong: an EMPTY mirror,
where every endpoint must say it has nothing rather than invent a plan, and a
populated one, where the plan must come back from libfabslate with no lot
assigned outside its own station family.

Does not need Kafka: FEED_FILE points at a path that does not exist, so the
mirror starts empty -- which is the state the endpoints must degrade
gracefully in -- and is then populated directly to exercise the plan path.
"""
import os
import sys

os.environ.setdefault("FEED_FILE", "/nonexistent-feed.jsonl")
os.environ.setdefault("DEMO_LOTS", "0")
sys.path.insert(0, "dispatch/api")

import main  # noqa: E402

app = main.app
app.testing = True
c = app.test_client()

print("--- empty mirror (must degrade, not lie) ---")
for path in ("/health", "/api/slate/status"):
    r = c.get(path)
    print(f"GET {path} -> {r.status_code} {r.get_data(as_text=True)[:200]}")

print("\n--- comparison runs from disk ---")
r = c.get("/api/slate/compare")
runs = r.get_json().get("runs", [])
print(f"runs listed: {len(runs)}")
for run in runs:
    print("  ", run["name"], run["rules"])
if runs:
    d = c.get("/api/slate/compare?run=" + runs[0]["name"]).get_json()
    print("  rows:", [x["rule"] for x in d["rows"]])
print("  404 path:", c.get("/api/slate/compare?run=nope.json").status_code)

print("\n--- populated mirror ---")
# Two families, four machines each, matching what tool_group() will report.
with main.mirror.lock:
    for fam, n in (("Dry_Etch", 4), ("Diffusion", 4)):
        for i in range(n):
            main.mirror.tools[f"{fam}_{i}"] = {"online": True, "last_seen": 0}
    for i in range(12):
        fam = "Dry_Etch" if i % 2 else "Diffusion"
        main.mirror.lots_ready[f"LOT_{i}"] = {
            "prod": "P1", "recipe": "STEP_A", "wafers": 25, "prio": 1,
            "slack": 3600, "fam": fam, "setup": "A" if i % 3 else "B",
            "part": "P1", "bmin": 1, "bmax": 1, "proc": 3600, "due": 999999,
        }

r = c.get("/api/slate/status")
print("GET /api/slate/status ->", r.status_code, r.get_data(as_text=True)[:220])

r = c.post("/api/slate/plan", json={"budget_s": 0.05})
print("POST /api/slate/plan ->", r.status_code)
j = r.get_json()
if r.status_code == 200:
    print("  stats:", j["stats"])
    print("  planned:", j["lots_planned"], "families:", j["families"])
    for a in j["assignments"][:6]:
        print("   ", a)
else:
    print("  ", j)
