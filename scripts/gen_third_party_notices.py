#!/usr/bin/env python3
"""Generate THIRD_PARTY_NOTICES.md from the dependency manifests.

Run from the repo root:

    python3 scripts/gen_third_party_notices.py            # write the file
    python3 scripts/gen_third_party_notices.py --check    # CI: fail if stale

The point of generating rather than hand-maintaining: the notices go stale the
moment a pin moves, and a stale notices file is worse than none -- it asserts
something untrue about what you are shipping.

No silent fallbacks. A dependency that appears in a manifest with no entry in
REGISTRY below is a hard error. Adding a dependency therefore forces a
deliberate decision about its licence, which is the point.
"""
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- component metadata -------------------------------------------------
# Keyed by the name as it appears in a manifest. Licence identifiers are SPDX.
# "bundled" lists components that ship *inside* another distribution -- they
# carry their own obligations even though no manifest names them.
REGISTRY = {
  # ---- C++ link-time, dispatch/CMakeLists.txt + Dockerfiles ----
  "ortools": dict(
      name="Google OR-Tools", license="Apache-2.0",
      copyright="Copyright 2010-2025 Google LLC",
      url="https://github.com/google/or-tools",
      note="CP-SAT is the tactical solver. Distributed as a prebuilt archive "
           "that bundles the components listed under 'Bundled inside OR-Tools'.",
      bundled=[
        ("abseil-cpp", "Apache-2.0", "Copyright Google LLC", "https://github.com/abseil/abseil-cpp"),
        ("Protocol Buffers", "BSD-3-Clause", "Copyright 2008 Google Inc.", "https://github.com/protocolbuffers/protobuf"),
        ("RE2", "BSD-3-Clause", "Copyright 2009 The RE2 Authors", "https://github.com/google/re2"),
        ("SCIP", "Apache-2.0", "Copyright Zuse Institute Berlin (ZIB)", "https://scipopt.org/"),
        ("SoPlex", "Apache-2.0", "Copyright Zuse Institute Berlin (ZIB)", "https://soplex.zib.de/"),
        ("Cbc / Cgl / Clp / CoinUtils / Osi", "EPL-2.0", "Copyright the COIN-OR Foundation", "https://github.com/coin-or"),
        ("HiGHS", "MIT", "Copyright the HiGHS authors", "https://github.com/ERGO-Code/HiGHS"),
        ("Boost (headers)", "BSL-1.0", "Copyright the Boost authors", "https://www.boost.org/"),
        ("Eigen", "MPL-2.0", "Copyright the Eigen authors", "https://eigen.tuxfamily.org/"),
        ("zlib", "Zlib", "Copyright Jean-loup Gailly and Mark Adler", "https://zlib.net/"),
        ("bzip2", "bzip2-1.0.6", "Copyright Julian R Seward", "https://sourceware.org/bzip2/"),
      ]),
  "librdkafka": dict(
      name="librdkafka", license="BSD-2-Clause",
      copyright="Copyright (c) 2012-2022, Magnus Edenhill",
      url="https://github.com/confluentinc/librdkafka",
      note="Kafka client for the C++ dispatcher. Linked dynamically "
           "(-lrdkafka++ -lrdkafka); Ubuntu package librdkafka-dev."),
  "libzmq": dict(
      name="ZeroMQ (libzmq)", license="MPL-2.0",
      copyright="Copyright (c) the ZeroMQ authors and contributors",
      url="https://github.com/zeromq/libzmq",
      note="AMHS transport, used through the C API <zmq.h>. Linked "
           "DYNAMICALLY (-lzmq; runtime package libzmq5). MPL-2.0 is "
           "file-level copyleft: using it unmodified through a dynamic link "
           "imposes no obligation on this project's own source, but any "
           "MODIFIED libzmq file must be published under MPL-2.0. This "
           "project does not modify libzmq. Note that libzmq was LGPLv3 "
           "before 4.2 -- the pin below matters."),
  "gurobi": dict(
      name="Gurobi Optimizer", license="PROPRIETARY",
      copyright="Copyright Gurobi Optimization, LLC",
      url="https://www.gurobi.com/",
      note="OPTIONAL and NOT LINKED in any build here, NOT redistributed, and "
           "NOT present in any image. Requires a commercial licence. The "
           "CMake option exists; enabling it makes the resulting binary "
           "non-redistributable."),
  # ---- container images (run, not redistributed) ----
  "confluentinc/cp-kafka": dict(
      name="Confluent Platform Kafka image", license="Apache-2.0 + Confluent Community License",
      copyright="Copyright Confluent, Inc.; Apache Kafka copyright the Apache Software Foundation",
      url="https://github.com/confluentinc/kafka-images",
      note="Pulled and run, not redistributed. The image mixes Apache-2.0 "
           "Kafka with Confluent Community Licence components; the CCL is "
           "NOT an open-source licence and restricts offering the software "
           "as a competing SaaS. Review before any commercial deployment."),
  "postgres": dict(
      name="PostgreSQL", license="PostgreSQL",
      copyright="Copyright (c) 1996-2024, PostgreSQL Global Development Group",
      url="https://www.postgresql.org/", note="Pulled and run, not redistributed."),
  # ---- vendored source ----
  "pyscfabsim": dict(
      name="PySCFabSim", license="MIT",
      copyright="Copyright (c) 2026 Research Group Production Systems",
      url="https://github.com/prosysscience/PySCFabSim-release",
      note="Vendored and MODIFIED in baselines/pyscfabsim/. See that "
           "directory's LICENSE, NOTICE and UPSTREAM.md."),
  "smt2020": dict(
      name="SMT2020 Semiconductor Manufacturing Testbed", license="SEE-SOURCE",
      copyright="Kopp, Hassoun, Kalir & Moench (2020); FernUniversitaet in Hagen",
      url="https://p2schedgen.fernuni-hagen.de/index.php/downloads/simulation",
      note="Dataset, not software, and NOT covered by this project's "
           "Apache-2.0 licence. Cite doi:10.1109/TSM.2020.3001933 for any "
           "published result derived from it. The source distribution states "
           "NO licence and NO terms of use -- verified against the download "
           "page and the contents of the official SMT2020.zip, neither of "
           "which carries any permission language. Redistributed here with "
           "attribution, following the precedent of the upstream simulator, "
           "but absence of a licence is not a grant of one. See "
           "data/smt2020/PROVENANCE.md."),
}

# Python and JS deps: name -> (licence, copyright)
PY_JS = {
  "flask": ("BSD-3-Clause", "Copyright Pallets"),
  "flask-cors": ("MIT", "Copyright Cory Dolphin"),
  "gunicorn": ("MIT", "Copyright Benoit Chesneau"),
  "gevent": ("MIT", "Copyright gevent contributors"),
  "confluent-kafka": ("Apache-2.0", "Copyright Confluent, Inc."),
  "pyyaml": ("MIT", "Copyright Ingy dot Net, Kirill Simonov"),
  "anthropic": ("MIT", "Copyright Anthropic, PBC"),
  "google-auth": ("Apache-2.0", "Copyright Google LLC"),
  "react": ("MIT", "Copyright Meta Platforms, Inc. and affiliates"),
  "react-dom": ("MIT", "Copyright Meta Platforms, Inc. and affiliates"),
  "vite": ("MIT", "Copyright Evan You and Vite contributors"),
  "@vitejs/plugin-react": ("MIT", "Copyright Evan You and Vite contributors"),
}

def read(p):
    f = ROOT / p
    return f.read_text(encoding="utf-8") if f.exists() else ""

def ortools_pin():
    """The OR-Tools version is the single most important pin in this repo:
    CP-SAT performance changes between releases, so a benchmark number
    without it is not reproducible. Parse it, never assume it."""
    m = re.search(r"or-tools_amd64_ubuntu-[\d.]+_cpp_v([\d.]+)\.tar\.gz", read("BUILD.md"))
    if not m:
        sys.exit("FATAL: no OR-Tools version pin found in BUILD.md. Refusing to "
                 "emit notices with an unknown solver version.")
    return m.group(1)

def libzmq_pin():
    m = re.search(r"libzmq3-dev", read("dispatch/infra/Dockerfile.dispatcher"))
    return "4.3.5 (Ubuntu 24.04 libzmq3-dev)" if m else "unpinned"

def pyreqs(path):
    out = []
    for line in read(path).splitlines():
        line = line.split("#")[0].strip()
        if not line: continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*[=><~!]{1,2}\s*([\w.]+)", line)
        if m: out.append((m.group(1), m.group(2)))
    return out

def jsdeps():
    p = ROOT / "dispatch/ui/package.json"
    if not p.exists(): return []
    d = json.loads(p.read_text())
    out = []
    for sec in ("dependencies", "devDependencies"):
        for k, v in (d.get(sec) or {}).items():
            out.append((k, v.lstrip("^~")))
    return out

def lookup(name):
    k = name.lower()
    if k in PY_JS: return PY_JS[k]
    sys.exit(f"FATAL: dependency '{name}' has no licence entry. Add it to "
             f"PY_JS in {Path(__file__).name} -- do not ship notices that "
             f"silently omit a component.")

def render():
    ot = ortools_pin()
    L = []
    a = L.append
    a("# Third-party notices\n")
    a("<!-- GENERATED by scripts/gen_third_party_notices.py -- do not edit by hand. -->")
    a("<!-- Regenerate after changing any dependency pin: python3 scripts/gen_third_party_notices.py -->\n")
    a("This project is licensed under the Apache License 2.0 (see `LICENSE`). It")
    a("depends on, links against, and in one case vendors the third-party work")
    a("listed here. Full licence texts are in [`licenses/`](licenses/).\n")
    a("Anything distributed from this repository -- source, built binaries, or")
    a("container images -- must carry this file and the `licenses/` directory.\n")
    a("**This project modifies one third-party component:** the vendored")
    a("PySCFabSim baseline (see `baselines/pyscfabsim/UPSTREAM.md`). No other")
    a("third-party source is modified.\n")
    a("> Not legal advice. Have counsel review before commercial distribution --")
    a("> in particular the Confluent Community Licence terms in the Kafka image")
    a("> and the redistribution status of the SMT2020 dataset.\n")

    a("## Linked into the dispatcher binary\n")
    a("| Component | Version | Licence | Copyright |")
    a("|---|---|---|---|")
    for key, ver in (("ortools", f"v{ot}"), ("librdkafka", "Ubuntu 24.04 librdkafka-dev"),
                     ("libzmq", libzmq_pin())):
        c = REGISTRY[key]
        a(f"| [{c['name']}]({c['url']}) | `{ver}` | {c['license']} | {c['copyright']} |")
    a("")
    for key in ("ortools", "librdkafka", "libzmq", "gurobi"):
        c = REGISTRY[key]
        a(f"**{c['name']}** — {c['license']}  ")
        a(f"{c['copyright']}  ")
        a(f"<{c['url']}>\n")
        a(f"{c['note']}\n")

    a("### Bundled inside the OR-Tools distribution\n")
    a(f"The prebuilt archive `or-tools ... v{ot}` ships these as shared libraries.")
    a("Their obligations travel with any binary linked against it.\n")
    a("| Component | Licence | Copyright |")
    a("|---|---|---|")
    for n, lic, cp, url in REGISTRY["ortools"]["bundled"]:
        a(f"| [{n}]({url}) | {lic} | {cp} |")
    a("\nNote the **EPL-2.0** COIN-OR components: EPL-2.0 is a weak copyleft that")
    a("requires source availability for those components if you redistribute them.\n")

    a("## Vendored source\n")
    for key in ("pyscfabsim",):
        c = REGISTRY[key]
        a(f"**{c['name']}** — {c['license']}  \n{c['copyright']}  \n<{c['url']}>\n")
        a(f"{c['note']}\n")

    a("## Data\n")
    c = REGISTRY["smt2020"]
    a(f"**{c['name']}**  \n{c['copyright']}  \n<{c['url']}>\n")
    a(f"{c['note']}\n")

    a("## Container images (run, not redistributed)\n")
    a("| Image | Licence | Notes |")
    a("|---|---|---|")
    for key, img in (("confluentinc/cp-kafka", "confluentinc/cp-kafka:7.7.1"),
                     ("postgres", "postgres:16-alpine")):
        c = REGISTRY[key]
        a(f"| `{img}` | {c['license']} | {c['note']} |")
    a("")

    a("## Python — API service\n")
    a("| Package | Version | Licence | Copyright |")
    a("|---|---|---|---|")
    for n, v in pyreqs("dispatch/api/requirements.txt"):
        lic, cp = lookup(n)
        a(f"| `{n}` | `{v}` | {lic} | {cp} |")
    a("")
    a("## JavaScript — UI\n")
    a("| Package | Version | Licence | Copyright |")
    a("|---|---|---|---|")
    for n, v in jsdeps():
        lic, cp = lookup(n)
        a(f"| `{n}` | `{v}` | {lic} | {cp} |")
    a("")
    a("## Baseline Python environment\n")
    a("`baselines/pyscfabsim/requirements.txt` pins the baseline's own")
    a("environment (PyTorch, Gymnasium, wandb and friends). It is not")
    a("redistributed from this repository -- a fresh clone builds it with `pip")
    a("install -r`. Consult that file and each package's own licence if you")
    a("ship a prebuilt baseline image.\n")
    return "\n".join(L) + "\n"

# The container build context is dispatch/ (compose uses `context: ..` from
# dispatch/infra), so the root notices are not reachable from a Dockerfile.
# Mirror them into dispatch/legal/ so every image can carry its own copy --
# the licences travel with the binaries, not just with the repository.
MIRROR = Path("dispatch/legal")

def targets(new):
    """(path, content) for every file this script owns."""
    out = [(ROOT / "THIRD_PARTY_NOTICES.md", new),
           (ROOT / MIRROR / "THIRD_PARTY_NOTICES.md", new)]
    for lic in sorted((ROOT / "licenses").glob("*.txt")):
        out.append((ROOT / MIRROR / "licenses" / lic.name,
                    lic.read_text(encoding="utf-8")))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if any output is stale")
    args = ap.parse_args()
    new = render()
    tg = targets(new)
    if args.check:
        stale = [p for p, c in tg
                 if not p.exists() or p.read_text(encoding="utf-8") != c]
        if stale:
            sys.exit("Stale or missing: "
                     + ", ".join(str(p.relative_to(ROOT)) for p in stale)
                     + "\nRun: python3 scripts/gen_third_party_notices.py")
        print(f"All {len(tg)} generated legal files are up to date.")
        return
    for p, c in tg:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c, encoding="utf-8")
    print(f"wrote {len(tg)} files: THIRD_PARTY_NOTICES.md and {MIRROR}/")

if __name__ == "__main__":
    main()
