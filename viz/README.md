# Route visualization

Aggregates SMT2020 route files to process areas and renders them two ways:
a **re-entrancy map** (step sequence as lanes) and a **shop floor** (bays sized
and shaded by visit count, with lot-transfer arcs).

    python3 viz/extract_routes.py SMT2020_LVHM > viz/routes_lvhm.json
    python3 viz/build_route_diagram.py          # -> viz/route_diagram.html
    node    viz/check_render.mjs                # geometry / runtime checks

`route_diagram.html` is self-contained (data inlined) apart from the Google
Fonts stylesheet.

Both views show the *nominal* route. Realized paths differ per lot: ~1 step in 6
carries `StepPercent < 100` and is skipped, and rework points send a lot back to
an earlier step. Set `NOSAMPLING=1 NOREWORK=1` to make routes deterministic.
