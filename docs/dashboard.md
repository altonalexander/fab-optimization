# The dashboard, screen by screen

A screenshot tour of the React dashboard in `dispatch/ui/`. For what the
project is and how to run it, see the [README](../README.md); for how to start
a session, [`docs/running.md`](running.md).

Captured from a live session: the fab warmed up for 90 simulated days under
`fifo`, the CP-SAT slate dispatcher switched on at day 90, paused at day
93. Every number on these screens is computed by the simulator feed, not
the browser, which is what makes a live run comparable with a benchmark row.

### Live — the fab right now
![live](screenshots/live.png)

The digital twin's front page. The header carries the fab KPIs — WIP,
throughput, starts, cycle time, on-time delivery, tool utilisation, and
**optimized decisions**, the share of the trailing day's dispatch decisions
the solver actually made rather than its fallback (44% here). Below it, WIP
split into waiting and running from day 0, the raw event feed, and every KPI
as a series with the warm-up in black and the run under test in blue, so the
effect of switching the dispatcher on is visible as a break at the day-90
rule rather than inferred from a table.

### Live — playback control and the assistant
![live-controls](screenshots/live-controls.png)

Two interactive features on the same page. **Playback** (the badge next to
the sim clock) pauses and resumes the simulated fab and sets the replay
speed from 1x to 1600x. It changes pacing only — the run, its seed and every
decision are unchanged, so the same fab can be watched slowly or raced
through; this is working, and the dashboard's choice persists across API
restarts. The **Assistant** rail is a conceptual mockup of where an
operator would ask the fab questions in plain language — "what happens if
LITHO_03 goes down?", "which tool is the bottleneck?" — with answers
grounded in the live state and the same C++ planner the dispatcher uses,
read-only by construction. The panel and its tool contract exist; it is not
wired to a model in this checkout.

### Lots — cohort burndown
![lots](screenshots/lots.png)

One product's releases from one day, drawn as steps-remaining against
simulated time — here six `part_3` lots released on day 47 with a 583-step
route, warm-up in black, the run under test in blue from the `sim start`
rule. A cohort is the set of lots that can actually share a furnace batch,
so the band's thickness is the cohort's spread and a widening band means it
is desynchronising and will stall at the next batch step — this one has
opened to 78 steps between fastest and slowest. The red dots on the zero
line are the due dates; a naive projection from the product's achieved rate
says whether they are in reach (`0 of 6 projected late`, worst slack
+3.5 d). Rework shows as a jog upward. This is the product-level view of
what the dispatch rule is doing.

The **lots** tab draws one cohort's burndown (steps left against simulated
time). Two toggles add context: **± cohorts** overlays the nearest earlier
(cyan) and later (orange) cohorts of the same product, nearest by release
*and* due date together so they are the ones that will actually meet it at a
batch step; **hot lots catching up** overlays the M hot lots (priority 20,
red dashed) of that product that are behind it in the route and released
nearest to it — the ones moving fast enough to contend for its batches.
`/api/lots?part=…` and `/api/lots/hot` serve them.

### Lots — one lot at a time
![lots-lotview](screenshots/lots-lotview.png)

The same cohort view switched from **envelope** to **lots**: `part_3-d47`,
six lots released on day 47 with 583 steps ahead of them, each drawn as its
own line. Warm-up is black; from the `sim start` rule each lot is coloured
by what it is doing at the last point — waiting on its cohort for a batch
(purple), queued for a tool (grey), processing (green) — and the dashed
rays project each one to the zero line at the product's achieved rate, to
be read against its due-date dot. Here the cohort has spread to 78 steps
between fastest and slowest, yet 0 of 6 are projected late with 3.5 days
of slack on the worst. Clicking a line opens that lot.

### Tools — who is busy, who is down
![tools](screenshots/tools.png)

All 1,313 tools in 106 groups, busiest first, with the online roster over
time (breakdowns and PM take tools out; the feed brings them back, and a
watchdog holds the roster if an event is lost). Expanding a group — here
`WE_FE_84`, 17 wet-etch tools, 7,247 dispatches — shows each tool's queue
and dispatch count; queue depth beside an online tool is where lots are
waiting, i.e. where the dispatch decision matters most. Each tool card
drills down to its dispatches and the choice set it was offered.

### Tool — one machine's decisions
![tool](screenshots/tool.png)

`#/tools/Litho_BE_110_890`: one lithography tool's queue, lots in flight,
dispatches and changeovers, and then every recent dispatch decision made at
it — the simulated day, how many lots it **chose from**, how many were left
waiting, and **who decided**: `slate` when the CP-SAT slate held a pick for
this tool, `slate-fallback` when the solver-consistent fallback score did.
This is the optimized-decisions KPI at decision resolution, and litho is
where it counts: at the top of the log the tool is choosing one lot from
38–51 waiting, and every one of those choices is the slate's. The
`slate-fallback` rows at the bottom are from the first hours after the
switch, before the planner had a token for this tool.

### Tool — setups and changeovers
![tool-changeovers](screenshots/tool-changeovers.png)

`#/tools/Implant_132_870`: the same page on an implanter, where the
**setup** column is the story. This tool has done 21 changeovers; reading
down the log it runs a block of lots in `SU132_1`, switches to `SU132_2`,
then `SU132_3`, then back — each switch costs setup time and, under
SMT2020's minimum-run-length rule, commits the tool to a run of that setup
before it may switch again. The dispatch rule sees the queue of 6–16 lots
across those setups and decides both which lot goes next and, implicitly,
when the tool pays for a changeover. This is the sequencing problem a
per-cycle assignment is blind to (see `docs/adr/0002`), visible one
decision at a time.

### Tool — the bay in three dimensions (dry etch, CMP, furnace, litho)
On a dry-etch tool (`/tools/DE_FE_86_206`, any `DE_*` family), a CMP
polisher (`Planar_*`), a diffusion furnace (`Diffusion_*`) or a litho cell
(`LithoTrack_*`, `Litho_FE_*`, `Litho_BE_*`) the tool page also draws the
bay: the lots waiting for the family on track-side shelves,
the lot on each load port with its time left, and the one-way overhead
loop with three hoist vehicles. When a decision lands, the panel on the
right lists the candidates the rule chose from — priority, wait, slack,
setup match — scans them, locks the winner, and connects
**lot → vehicle → tool · port**; the winner's rail route lights up and a
vehicle fetches it. When the FOUP nearest the port by rail is not the one
chosen, its route is ghosted in red, which is the point of the picture:
the dispatcher ranks the lot, not the distance. The strip beneath reads the
machine state (`IDLE → LOT SELECTED → RESERVED → FOUP IN TRANSIT →
LOADING → PROCESSING`). Two modes. **Live twin** follows the feed on the
fab clock — a delivery is twenty-odd fab seconds, so it is only watchable
at **1x** playback (the scene's clock chip offers it); at 20x it is a
flick, and paused it stands still, like every other view here. **Playback**
replays one recorded decision at one fab second per second whatever the
feed is doing: pick **▶ watch** on a row under Recent decisions, and the
scene (amber-framed while it runs) shows the previous lot leaving, those
candidates on the shelves, the choice, and the delivery. The scene and the
lots view link both ways: a lot in a decision's rationale links to its
cohort, and a lot's journey links each step it has left to a replay of the
dispatch on the tool that ran it (`/tools/<tool>?lot=<lot>&mode=playback`),
and its current step to the live bay (`mode=live`); opened that way, or by
clicking a FOUP, the camera locks onto the lot instead of the tool.
CMP is the second scene and reuses all of this with a different focal tool
(three platens under a carousel that turn while a lot is on the tool, a
post-clean module, a slurry cabinet) and two more dispatch dimensions the
data actually has: **pad life**, drawn from SMT2020's piece-based PM
calendar (the feed publishes pieces until the next PM with each decision,
so the panel shows "n of 3,500 wafers since PM"), and **next ↓**, the
family each candidate's next route step needs and the queue waiting there
now — the downstream a fab-wide dispatcher weighs and a queue rule does
not. Setups are hidden where a family has none (CMP), and a tool down for
PM or a breakdown says so on the tool and in the panel.
The furnace is the third scene and the one where the right decision can be
to wait. SMT2020 furnaces batch 3–6 lots of the same product at the same
step (BATCHMN/BATCHMX in the route, in pieces), so the queue on the shelves
is grouped by product and step; while nothing runs and no group has reached
its minimum, the state strip reads **WAITING FOR BATCH** and the lots of the
largest forming group are tinted amber. When the decision lands it names
every lot of the batch: they lock together in the panel, then leave the
shelves one vehicle at a time (three vehicles, so the fourth FOUP waits for
the first to come back), onto six ports, and the tubes glow while the batch
runs. Alternatives in the panel show their step, which is usually why they
were not in the batch.
Litho is the fourth scene: a coat/develop track with the scanner behind it.
SMT2020 gives the track families one setup per layer (`SU015_1`,
`SU036_1`, … in the route) and a changeover time between setups, so every
FOUP's lid carries the colour of the setup it needs and the track wears a
band of the setup it is on. A lot that needs another layer's setup costs a
changeover, drawn as a **SETUP CHANGE** state between loading and
processing for exactly the seconds the decision record priced it at
(`setup_s` in the ranking tuple), after which the band takes the new
colour. The rules rank setup cost ahead of queue age, and the panel says so
when a lot that waited longer lost to one already on the layer. SMT2020
has no reticles, so none are modelled or drawn; the scanner families have
no setups, so their pages show the cell without the band.
SMT2020 has no AMHS (transport is a `Delay_*` step), so ports, shelves and
vehicles are a visualisation of the decision, not a second simulation; the
layout is the same synthetic one the floor map uses, and the neighbouring
tools are the real occupants of the tool's cell. Model in
`dispatch/ui/src/etch_geom.js` (tested), rendering in `EtchScene.jsx`
(three.js, loaded only on those pages).

### Floor — the cleanroom as a map
![floor](screenshots/floor.png)

A synthetic bay/chase layout of the same 913 process tools, coloured by
area, with WIP per bay and a heatmap toggle. Hatching marks bays with a
tool down. Clicking a bay opens its panel — here bay 8 · seg 2,
photolithography: 14 tools, 77 lots of WIP, 11 running, 1 down, and the
tool list, each a link into the tools tab. It answers the spatial question
the tables cannot: where in the fab the queue is building, and whether it
is one bay or a whole area. The selection lives in the URL
(`#/floor?bay=8,2`), so a view is pasteable.

### Products — the ten routes at a glance
![products](screenshots/products.png)

The ten saleable LVHM products, one card each: route length in steps,
**visits** (consecutive steps in one bay, collapsed — always well below
steps, which is the re-entrancy), the areas touched, a bar of where the
route's steps are spent by process area, and how many cohorts and lots of
that product are live in the fab right now. Routes run from 242 to 583
steps and every one of them spends most of its time in wet etch. Each card
opens the product's route page.

### Routes — what a product's journey looks like
![routes](screenshots/routes.png)

One page per product. The lane map draws the whole route — here 521 steps
across 12 areas — one lane per area, one column per step, with measurement
and rework points marked. Reading across shows the re-entrancy that makes
fab scheduling hard: the same few lanes fire over and over for 391 visits,
and a bad exposure sends the lot back three steps. The area table below
says where the steps go and how often the lot returns.

### Slate — the optimizer, on demand
![slate](screenshots/slate.png)

The CP-SAT planner from `dispatch/libfabslate.so`, the same library the
simulator's `slate` rule calls, applied to the live ready pool: one click
plans every waiting lot against every tool by family and returns the slate
— primary tool, alternate, rank. The head-to-head buttons open the
benchmark result files. This is the read-only window onto the dispatcher;
no write path reaches the fab from here.

### Results — dispatchers compared on equal terms

The screenshot at the top of this README. Every run in the Postgres run store,
each resumed from the same day-90 checkpoint, with post-switch means and
deltas against a chosen baseline — see [What it solves](#what-it-solves) and
`bench/README.md` for what the numbers do and do not say.

### Topology — the pipeline itself
![topology](screenshots/topology.png)

The four security zones and the stream between them: event throughput
(~570 envelopes/s here), the simulated clock rate *measured* against the
playback speed *requested* — 803x against 1600x, because the CP-SAT slate
cannot plan faster than that, so the gap is the solver's cost in fab time
— mirror lag from zone 2 to zone 3, frames seen, and which services
straddle a boundary. When the fab looks wrong, this is where to check
whether it is the fab or the pipe.

