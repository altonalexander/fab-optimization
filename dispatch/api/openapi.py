"""Self-generating OpenAPI spec + Swagger UI for the dispatch API.

The spec is built by walking `app.url_map` on each request, so a new
`@app.get`/`@app.post` shows up in the docs the moment it is added -- there is
no second place to update. Method, path, path params and the
summary/description all come from the route itself (the view function's
docstring).

What a URL map cannot know is the shape of a JSON body or a response, so that
hand-written detail lives in ENRICH below, keyed by "METHOD /rule". The table
is additive: a route missing from it is still documented, just without body
and response schemas. Those routes are listed under `x-undocumented` in the
spec so the gap stays visible instead of quietly accumulating.

Registers:
    /openapi.json   the spec
    /docs           Swagger UI
    /redoc          ReDoc
"""

import json
import re

# Flask URL converter -> (JSON type, format). `path` is a string that may
# contain slashes, which is how tool ids and cohort names arrive.
_CONVERTERS = {
    "string": ("string", None),
    "path": ("string", None),
    "int": ("integer", None),
    "float": ("number", None),
    "uuid": ("string", "uuid"),
}
_DEFAULT_CONVERTER = ("string", None)

# <converter(args):name> or <name>
_RULE_PARAM = re.compile(r"<(?:(?P<conv>[a-zA-Z_][a-zA-Z0-9_]*)(?:\([^)]*\))?:)?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)>")

_OBJ = {"type": "object"}
_ARR_OBJ = {"type": "array", "items": _OBJ}


def _limit(default, maximum, desc):
    return {
        "name": "limit", "in": "query", "required": False,
        "description": desc,
        "schema": {"type": "integer", "default": default, "maximum": maximum},
    }


def _json(schema):
    return {"content": {"application/json": {"schema": schema}}}


_ERROR = {
    "type": "object",
    "properties": {"error": {"type": "string"}},
    "required": ["error"],
}

_SCENARIO_BODY = {
    "type": "object",
    "properties": {
        "lots": dict(_ARR_OBJ, description=(
            "Lots to plan. Omitted or empty means the live ready pool "
            "(or DEMO_LOTS when that is set).")),
        "tool_overrides": dict(_ARR_OBJ, description=(
            "What-if perturbations applied to the cloned tool registry, "
            "e.g. taking a tool down.")),
    },
}

_SCENARIO_RESULT = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"lot_id": {"type": "string"},
                               "tool": {"type": "string"}},
            },
        },
    },
}

# "METHOD /flask/rule" -> partial OpenAPI operation, merged over the generated one.
ENRICH = {
    "GET /health": {
        "responses": {"200": {"description": "Service is up.", **_json({
            "type": "object",
            "properties": {"ok": {"type": "boolean"},
                           "read_only": {"type": "boolean"},
                           "zone": {"type": "string"}},
        })}},
    },
    "GET /api/zones": {
        "responses": {
            "200": {"description": "Parsed contents of ZONES_FILE.", **_json(_OBJ)},
            "404": {"description": "Zones file missing or unparseable.", **_json(_ERROR)},
        },
    },
    "GET /api/routes": {
        "responses": {
            "200": {"description": "Product route index: one summary row per "
                                   "saleable product, with live lot and cohort "
                                   "counts.",
                    **_json({"type": "object", "properties": {
                        "dataset": {"type": "string"},
                        "areas": {"type": "array", "items": {"type": "string"}},
                        "products": _ARR_OBJ}})},
            "404": {"description": "Routes file missing.", **_json(_ERROR)},
        },
    },
    "GET /api/routes/<path:product>": {
        "parameters": [_limit(8, 60, "Cohorts to sample, ranked by last movement.")],
        "responses": {
            "200": {"description": "One product's route -- area visits, "
                                   "transitions and rework loops -- plus a "
                                   "sample of the cohorts currently walking it. "
                                   "Accepts a product name (`part_3`) or a "
                                   "route id (`r_3`).",
                    **_json({"type": "object", "properties": {
                        "product": {"type": "string"},
                        "route": {"type": "string"},
                        "n_steps": {"type": "integer"},
                        "visits": _ARR_OBJ,
                        "cohorts": _ARR_OBJ}})},
            "404": {"description": "Unknown product, or routes file missing.",
                    **_json(_ERROR)},
        },
    },
    "GET /api/state": {
        "responses": {"200": {"description": "Current mirror snapshot: tools, "
                                             "queues, counters, sim clock.",
                              **_json(_OBJ)}},
    },
    "GET /api/layout": {
        "responses": {
            "200": {"description": "Floorplan geometry and placement, plus the "
                                   "Delay_* tools that have no physical cell.",
                    **_json({"type": "object", "properties": {
                        "cells": _ARR_OBJ, "delays": _ARR_OBJ}})},
            "500": {"description": "Floorplan unavailable.", **_json(_ERROR)},
        },
    },
    "GET /api/layout/state": {
        "responses": {"200": {"description": "Per-cell live state to paint over "
                                             "the cached geometry.",
                              **_json({"type": "object", "properties": {
                                  "ts": {"type": "number"},
                                  "cells": _ARR_OBJ, "delays": _ARR_OBJ}})}},
    },
    "GET /api/tools": {
        "responses": {"200": {"description": "Tools grouped by type, ranked by "
                                             "dispatch count.",
                              **_json({"type": "object", "properties": {
                                  "groups": _ARR_OBJ,
                                  "total": {"type": "integer"}}})}},
    },
    "GET /api/tools/availability": {
        "responses": {"200": {
            "description": "Online-tool count over time. Parallel arrays, not "
                           "objects -- at 2,880 points the object form is "
                           "several times the bytes, and the strip polls every "
                           "few seconds. `total` is historical, so early-run "
                           "roster growth does not read as an outage. Tools "
                           "restored by inference rather than observation are "
                           "counted in `recovered`; a climbing number there is "
                           "a bug to chase, not a healthy steady state.",
            **_json({"type": "object", "properties": {
                "ts": {"type": "array", "items": {"type": "number"}},
                "online": {"type": "array", "items": {"type": "integer"}},
                "total": {"type": "array", "items": {"type": "integer"}},
                "now": {"type": "object", "properties": {
                    "online": {"type": "integer"},
                    "total": {"type": "integer"},
                    "down": {"type": "integer"}}},
                "down_now": {"type": "integer",
                             "description": "Tools currently held down with no "
                                            "recovery seen."},
                "recovered": {"type": "object",
                              "description": "Count per inference source that "
                                             "restored a tool.",
                              "additionalProperties": {"type": "integer"}},
                "ttl_s": {"type": "number",
                          "description": "Watchdog TOOL_DOWN_TTL_S."},
                "sample_s": {"type": "number",
                             "description": "Availability sampling interval."}}})}},
    },
    "GET /api/tools/<path:tool_id>": {
        "responses": {
            "200": {"description": "Tool row with recent decisions and events.",
                    **_json(_OBJ)},
            "404": {"description": "Unknown tool.", **_json(_ERROR)},
        },
    },
    "GET /api/events": {
        "parameters": [_limit(100, 500, "Most recent N events. Capped at 500.")],
        "responses": {"200": {"description": "Most recent events, oldest first.",
                              **_json(_ARR_OBJ)}},
    },
    "GET /api/decisions": {
        "parameters": [_limit(100, 500, "Most recent N decisions. Capped at 500.")],
        "responses": {"200": {"description": "Most recent dispatch decisions, "
                                             "oldest first.", **_json(_ARR_OBJ)}},
    },
    "GET /api/lots": {
        "parameters": [_limit(60, 500, "Cohorts to return, ranked by last movement.")],
        "responses": {"200": {"description": "Cohort index for the burndown view.",
                              **_json({"type": "object", "properties": {
                                  "now_t": {"type": "number"},
                                  "cohorts": _ARR_OBJ,
                                  "total_cohorts": {"type": "integer"},
                                  "lots_tracked": {"type": "integer"},
                                  "points_held": {"type": "integer"},
                                  "points_cap": {"type": "integer"}}})}},
    },
    "GET /api/lots/<path:cohort>": {
        "responses": {"200": {"description": "Per-lot burndown series for one "
                                             "cohort. `steps_remaining` is not "
                                             "monotonic -- rework splices "
                                             "processed steps back onto the route.",
                              **_json({"type": "object", "properties": {
                                  "cohort": {"type": "string"},
                                  "now_t": {"type": "number"},
                                  "lots": _ARR_OBJ}})}},
    },
    "GET /api/stream": {
        "responses": {"200": {
            "description": "Server-Sent Events. Long-lived; each message is a "
                           "`data:` line of JSON. Swagger UI cannot render a "
                           "stream -- use `curl -N`.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }},
    },
    "POST /api/scenario": {
        "requestBody": _json(_SCENARIO_BODY),
        "responses": {
            "200": {"description": "Planner output.", **_json(_SCENARIO_RESULT)},
            "400": {"description": "No lots supplied and the live ready pool is empty.",
                    **_json(_ERROR)},
            "500": {"description": "Planner binary missing, failed, or returned "
                                   "unparseable output.", **_json(_ERROR)},
            "504": {"description": "Solve exceeded the 30s budget.", **_json(_ERROR)},
        },
    },
    "POST /api/scenario/compare": {
        "requestBody": _json(_SCENARIO_BODY),
        "responses": {
            "200": {"description": "Baseline, what-if, and the diff between them.",
                    **_json({"type": "object", "properties": {
                        "baseline": _SCENARIO_RESULT,
                        "scenario": _SCENARIO_RESULT,
                        "diff": {"type": "object", "properties": {
                            "rerouted": {"type": "array", "items": {
                                "type": "object", "properties": {
                                    "lot_id": {"type": "string"},
                                    "from": {"type": "string"},
                                    "to": {"type": "string"}}}},
                            "dropped": {"type": "array",
                                        "items": {"type": "string"}}}}}})},
            "400": {"description": "No lots supplied and the live ready pool is empty.",
                    **_json(_ERROR)},
            "500": {"description": "One or both scenario runs failed.", **_json(_ERROR)},
        },
    },
    "GET /api/sim/control": {
        "responses": {"200": {
            "description": "Playback pacing. `available` is false when no feed "
                           "has ever written the control file.",
            **_json({"type": "object", "properties": {
                "available": {"type": "boolean"},
                "speed": {"type": "number", "nullable": True},
                "paused": {"type": "boolean"},
                "updated": {"type": "number"},
                "speeds": {"type": "array", "items": {"type": "number"}}}})}},
    },
    "POST /api/sim/control": {
        "requestBody": _json({
            "type": "object",
            "properties": {
                "speed": {"type": "number",
                          "description": "Playback multiplier. Must be in "
                                         "(0, max(speeds)]."},
                "paused": {"type": "boolean"},
            },
        }),
        "responses": {
            "200": {"description": "The control state after the write.", **_json(_OBJ)},
            "400": {"description": "Speed was not a number, or out of range.",
                    **_json(_ERROR)},
            "500": {"description": "Control file could not be written.", **_json(_ERROR)},
        },
    },
    "GET /api/chat/status": {
        "responses": {"200": {"description": "Whether the assistant is configured, "
                                             "and which model backs it.",
                              **_json({"type": "object", "properties": {
                                  "available": {"type": "boolean"},
                                  "error": {"type": "string", "nullable": True},
                                  "model": {"type": "string"}}})}},
    },
    "POST /api/chat": {
        "requestBody": _json({
            "type": "object",
            "required": ["messages"],
            "properties": {"messages": {
                "description": "Conversation so far. Only the last 40 are kept.",
                "type": "array",
                "items": {"type": "object", "properties": {
                    "role": {"type": "string", "enum": ["user", "assistant"]},
                    "content": {"type": "string"}}},
            }},
        }),
        "responses": {
            "200": {"description": "Grounded reply plus the tool results behind it.",
                    **_json({"type": "object", "properties": {
                        "reply": {"type": "string"},
                        "error": {"type": "string", "nullable": True}}})},
            "400": {"description": "No messages supplied.", **_json(_ERROR)},
            "503": {"description": "Assistant unavailable and produced no reply.",
                    **_json(_ERROR)},
        },
    },
}

# Rules that are plumbing rather than API surface.
_SKIP_ENDPOINTS = {"static", "openapi_spec", "swagger_ui", "redoc_ui"}

_TAG_DESCRIPTIONS = {
    "system": "Liveness and configuration.",
    "zones": "Segmentation policy driving the UI topology.",
    "state": "Live mirror of fab state.",
    "layout": "Floorplan geometry and the state painted onto it.",
    "tools": "Tool index and per-tool detail.",
    "events": "Raw event feed.",
    "decisions": "Dispatch decisions.",
    "lots": "Cohort and per-lot burndown.",
    "routes": "Product routes and the cohorts walking them.",
    "stream": "Server-Sent Events feed.",
    "scenario": "What-if planning against a cloned registry.",
    "sim": "Simulator playback control.",
    "chat": "Grounded assistant over live state.",
}


def _split_docstring(fn):
    """First non-empty line is the summary; whatever follows is the description."""
    doc = (fn.__doc__ or "").strip() if fn is not None else ""
    if not doc:
        return None, None
    lines = [ln.strip() for ln in doc.splitlines()]
    for i, ln in enumerate(lines):
        if ln:
            return ln, "\n".join(lines[i + 1:]).strip() or None
    return None, None


def _path_and_params(rule_str):
    """Flask rule string -> OpenAPI path template plus its path parameters."""
    params = []

    def sub(m):
        conv = (m.group("conv") or "string").lower()
        name = m.group("name")
        jtype, jfmt = _CONVERTERS.get(conv, _DEFAULT_CONVERTER)
        schema = {"type": jtype}
        if jfmt:
            schema["format"] = jfmt
        param = {"name": name, "in": "path", "required": True, "schema": schema}
        if conv == "path":
            param["description"] = "May contain slashes."
        params.append(param)
        return "{%s}" % name

    return _RULE_PARAM.sub(sub, rule_str), params


def _tag_for(rule_str):
    parts = [p for p in rule_str.strip("/").split("/") if p]
    if not parts:
        return "system"
    if parts[0] == "api":
        if len(parts) < 2:
            return "api"
        return parts[1].split("<")[0].strip() or "api"
    return "system"


def _merge(base, extra):
    """Shallow merge, going one level deep for dict values (responses, etc.)."""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def build_spec(app, server_url=None):
    """Walk the app's URL map and produce an OpenAPI 3.0 document."""
    paths = {}
    tags_seen = set()
    undocumented = []

    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint in _SKIP_ENDPOINTS:
            continue
        methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"})
        if not methods:
            continue

        view = app.view_functions.get(rule.endpoint)
        summary, description = _split_docstring(view)
        path, path_params = _path_and_params(rule.rule)
        tag = _tag_for(rule.rule)
        tags_seen.add(tag)

        for method in methods:
            op = {
                "operationId": "%s_%s" % (method.lower(), rule.endpoint),
                "tags": [tag],
                "summary": summary or "%s %s" % (method, path),
                "responses": {"200": {"description": "Success."}},
            }
            if description:
                op["description"] = description
            if path_params:
                op["parameters"] = list(path_params)

            key = "%s %s" % (method, rule.rule)
            extra = ENRICH.get(key)
            if extra:
                extra = dict(extra)
                # Query params in ENRICH extend the generated path params
                # rather than replacing them.
                if "parameters" in extra:
                    extra["parameters"] = list(path_params) + list(extra["parameters"])
                op = _merge(op, extra)
            else:
                undocumented.append(key)

            paths.setdefault(path, {})[method.lower()] = op

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Fab Dispatch API",
            "version": "1.0.0",
            "description": (
                "Live fab state, floorplan, dispatch decisions and what-if "
                "planning behind the dispatch dashboard.\n\n"
                "Generated from the Flask URL map at request time, so it cannot "
                "drift from the routes that actually exist."
            ),
        },
        "servers": [{"url": server_url or "/"}],
        "tags": [{"name": t, "description": _TAG_DESCRIPTIONS.get(t, "")}
                 for t in sorted(tags_seen)],
        "paths": paths,
        # Surfaced deliberately: a route with no ENRICH entry is documented by
        # path and docstring only. Listing them keeps the gap honest.
        "x-undocumented": sorted(undocumented),
    }


_SWAGGER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Fab Dispatch API</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
<style>body{margin:0}.swagger-ui .topbar{display:none}</style></head>
<body><div id="ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({url: "SPEC_URL", dom_id: "#ui", deepLinking: true,
                 defaultModelsExpandDepth: -1, tryItOutEnabled: true});
</script></body></html>"""

_REDOC_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Fab Dispatch API</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0}</style></head>
<body><redoc spec-url="SPEC_URL"></redoc>
<script src="https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js"></script>
</body></html>"""


def register_docs(app, spec_route="/openapi.json", swagger_route="/docs",
                  redoc_route="/redoc"):
    """Attach the spec endpoint and the two viewers.

    Order of registration does not matter -- the spec is rebuilt from the URL
    map on every request, so routes added after this call are still documented.
    """
    from flask import Response, request

    @app.get(spec_route, endpoint="openapi_spec")
    def openapi_spec():
        base = request.url_root.rstrip("/")
        return Response(json.dumps(build_spec(app, server_url=base), indent=2),
                        mimetype="application/json")

    @app.get(swagger_route, endpoint="swagger_ui")
    def swagger_ui():
        return Response(_SWAGGER_HTML.replace("SPEC_URL", spec_route),
                        mimetype="text/html")

    @app.get(redoc_route, endpoint="redoc_ui")
    def redoc_ui():
        return Response(_REDOC_HTML.replace("SPEC_URL", spec_route),
                        mimetype="text/html")

    return app
