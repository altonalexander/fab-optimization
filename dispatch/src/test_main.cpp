// test_main.cpp — the test suite.
//
// Built around the three bugs that benchmarking actually found, because those
// are the failure modes this system has demonstrated, not hypothetical ones:
//
//   1. A solver returning assignments that violate a hard constraint while
//      reporting a healthy assigned-count. (Greedy put one reticle on five
//      scanners.) -> audit_solution() runs on EVERY solver result.
//   2. An objective whose scaling makes idling cheaper than producing. The
//      solver returns OPTIMAL and the fab does nothing.
//      -> test_objective_scaling()
//   3. evaluate() and admit() disagreeing, so a lot passes eligibility and is
//      then rejected at the tool. -> property test over random states.
//
// Build:  g++ -std=c++20 -O2 -pthread -Iinclude src/test_main.cpp -o fabtest
// Run:    ./fabtest            all suites
//         ./fabtest --bench    solver comparison at scale
//         ./fabtest --audit-only

// The linked solver's own version string. Reported, never assumed: a CP-SAT
// benchmark number is meaningless without the version that produced it, and a
// plausible-looking default would be worse than no number at all.
#ifdef FAB_HAVE_ORTOOLS
#include "ortools/base/version.h"
#endif

#include <optional>
#include "fab/machine_config.hpp"
#include "fab/planner.hpp"
#include "fab/solver.hpp"
#include "fab/tool_factory.hpp"
#include "fab/state.hpp"

#include <algorithm>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <string>
#include <vector>

using namespace fab;

// ---------------------------------------------------------------------------
// Minimal assertion framework. No gtest dependency: this must build anywhere
// the dispatcher builds.
// ---------------------------------------------------------------------------

namespace t {

int g_pass = 0, g_fail = 0;
std::string g_suite;

void suite(const std::string& s) {
    g_suite = s;
    std::cout << "\n== " << s << " ==\n";
}

void check(bool cond, const std::string& what, const std::string& detail = "") {
    if (cond) { ++g_pass; std::cout << "  PASS  " << what << "\n"; }
    else {
        ++g_fail;
        std::cout << "  FAIL  " << what;
        if (!detail.empty()) std::cout << "\n          " << detail;
        std::cout << "\n";
    }
}

template <typename A, typename B>
void eq(const A& a, const B& b, const std::string& what) {
    std::ostringstream d;
    d << "expected " << b << ", got " << a;
    check(a == b, what, d.str());
}

int summary() {
    std::cout << "\n" << std::string(58, '-') << "\n"
              << g_pass << " passed, " << g_fail << " failed\n";
    return g_fail == 0 ? 0 : 1;
}

} // namespace t

// ---------------------------------------------------------------------------
// CONSTRAINT AUDIT — the most important function in this file.
//
// Every solver result gets audited. A solver that reports 80 assignments while
// double-booking a reticle has not solved the problem; it has produced a
// number. Without this, a benchmark flatters whichever heuristic cheats most.
// ---------------------------------------------------------------------------

std::vector<std::string> audit_solution(const AssignmentModel& m,
                                        const std::unordered_map<int,int>& asg) {
    std::vector<std::string> v;

    std::map<int, std::vector<int>> load;              // tool -> lots
    for (const auto& [l, tl] : asg) load[tl].push_back(l);

    // capacity
    for (const auto& [tl, lots] : load)
        if (static_cast<int>(lots.size()) > m.tool_capacity[tl])
            v.push_back("capacity: " + m.tool_ids[tl] + " has " +
                        std::to_string(lots.size()) + " > " +
                        std::to_string(m.tool_capacity[tl]));

    // batch minimum and single-recipe batches
    for (const auto& [tl, lots] : load) {
        if (m.tool_min_batch[tl] <= 0) continue;
        if (static_cast<int>(lots.size()) < m.tool_min_batch[tl])
            v.push_back("batch-min: " + m.tool_ids[tl] + " loaded " +
                        std::to_string(lots.size()) + " < " +
                        std::to_string(m.tool_min_batch[tl]) +
                        " (cannot legally fire)");
        std::set<RecipeId> recipes;
        for (int l : lots) recipes.insert(m.lot_recipe[l]);
        if (recipes.size() > 1)
            v.push_back("mixed-recipe batch on " + m.tool_ids[tl] + ": " +
                        std::to_string(recipes.size()) + " recipes");
    }

    // reticle exclusivity — one reticle cannot be on two scanners at once
    const std::set<int> scanners(m.scanner_tools.begin(), m.scanner_tools.end());
    std::map<ReticleId, std::set<int>> ret_tools;
    for (const auto& [l, tl] : asg) {
        if (!scanners.count(tl)) continue;
        if (m.lot_reticle[l].empty()) continue;
        ret_tools[m.lot_reticle[l]].insert(tl);
    }
    for (const auto& [ret, tls] : ret_tools)
        if (tls.size() > 1)
            v.push_back("reticle " + ret + " on " + std::to_string(tls.size()) +
                        " scanners simultaneously (physically impossible)");

    // assignment must reference a pair that evaluate() actually permitted
    std::set<std::pair<int,int>> feasible;
    for (const auto& e : m.entries) feasible.insert({e.lot_index, e.tool_index});
    for (const auto& [l, tl] : asg)
        if (!feasible.count({l, tl}))
            v.push_back("infeasible pair assigned: " + m.lot_ids[l] +
                        " -> " + m.tool_ids[tl]);

    return v;
}

// The objective, computed identically for every solver. Scoring solvers
// differently is how a comparison silently becomes meaningless.
double score(const AssignmentModel& m, const std::unordered_map<int,int>& asg) {
    double max_cost = 1.0;
    for (const auto& e : m.entries) max_cost = std::max(max_cost, e.cost);

    double total = 0.0;
    for (const auto& e : m.entries) {
        auto it = asg.find(e.lot_index);
        if (it != asg.end() && it->second == e.tool_index) total += e.cost;
    }
    std::set<int> eligible;
    for (const auto& e : m.entries) eligible.insert(e.lot_index);
    for (int l : eligible) {
        if (asg.count(l)) continue;
        const double urgency = m.lot_priority[l] *
            (1.0 + 3600.0 / std::max(m.lot_slack_s[l], 60.0));
        total += max_cost * urgency;
    }
    return total;
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

Lot mk(const std::string& id, const std::string& recipe,
       const std::string& reticle = "", double prio = 1.0,
       double slack = 3600.0, const std::string& prod = "AUTOMOTIVE_MCU_01") {
    return Lot{id, prod, recipe, reticle, 25, prio, slack};
}

// ---------------------------------------------------------------------------
// SUITE 1 — per-configuration unit tests
// ---------------------------------------------------------------------------

void test_single_wafer() {
    t::suite("SingleWaferTool");
    SingleWaferTool tool("E1", "ETCH", {"POLY_ETCH", "METAL_ETCH"}, 45.0, 600.0);

    auto a = tool.evaluate(mk("L1", "POLY_ETCH"));
    t::check(bool(a), "qualified recipe is eligible");
    t::eq(a.process_s, 45.0 * 25, "process time = sec_per_wafer x wafers");

    t::check(!tool.evaluate(mk("L2", "NITRIDE")), "unqualified recipe rejected");
    t::eq(int(tool.evaluate(mk("L2", "NITRIDE")).reason),
          int(Rejection::RecipeNotQualified), "rejection reason is recipe");

    t::check(tool.admit(mk("L1", "POLY_ETCH")), "admit succeeds when free");
    t::eq(tool.free_capacity(), 0, "capacity consumed after admit");
    t::check(!tool.evaluate(mk("L3", "POLY_ETCH")), "busy tool rejects");
    t::eq(int(tool.evaluate(mk("L3", "POLY_ETCH")).reason),
          int(Rejection::NoCapacity), "rejection reason is capacity");

    // setup only on recipe CHANGE
    tool.release("L1");
    t::eq(tool.evaluate(mk("L4", "POLY_ETCH")).setup_s, 0.0,
          "no setup when recipe unchanged");
    t::eq(tool.evaluate(mk("L5", "METAL_ETCH")).setup_s, 600.0,
          "setup charged on recipe change");

    tool.set_online(false);
    t::eq(int(tool.evaluate(mk("L6", "POLY_ETCH")).reason),
          int(Rejection::ToolDown), "offline tool reports ToolDown");
}

void test_batch_furnace() {
    t::suite("BatchFurnace");
    BatchFurnace f("F1", "DIFF", {"GATE_OX"}, 4, 6, 7200.0, 1800.0);

    t::eq(f.evaluate(mk("L1", "GATE_OX")).process_s, 7200.0,
          "process time independent of batch size");
    t::eq(f.free_capacity(), 6, "empty furnace has max_batch free");

    f.admit(mk("L1", "GATE_OX"));
    t::eq(f.free_capacity(), 5, "staging consumes capacity");

    // THE constraint: a batch must be single-recipe.
    t::eq(int(f.evaluate(mk("L2", "ANNEAL")).reason),
          int(Rejection::RecipeNotQualified),
          "unqualified recipe rejected before batch check");

    for (int i = 2; i <= 6; ++i) f.admit(mk("L" + std::to_string(i), "GATE_OX"));
    t::eq(f.free_capacity(), 0, "full at max_batch");
    t::eq(int(f.evaluate(mk("L9", "GATE_OX")).reason),
          int(Rejection::NoCapacity), "full furnace rejects");

    // fire policy
    BatchFurnace g("F2", "DIFF", {"GATE_OX"}, 4, 6, 7200.0, 1800.0);
    g.admit(mk("A", "GATE_OX"));
    g.admit(mk("B", "GATE_OX"));
    t::check(!g.should_fire(0.0, 1e9), "below min_batch does not fire");
    t::check(g.should_fire(2000.0, 1e9), "fires after max_hold even if partial");
    t::check(g.should_fire(0.0, 100.0), "fires partial when Q-time is critical");
    g.admit(mk("C", "GATE_OX"));
    g.admit(mk("D", "GATE_OX"));
    t::check(g.should_fire(0.0, 1e9), "fires once min_batch reached");
}

void test_cluster() {
    t::suite("ClusterTool");
    ClusterTool c("C1", "TF", {
        {"A", {"NITRIDE", "OXIDE"}, true, ""},
        {"B", {"NITRIDE"}, true, ""},
        {"C", {"OXIDE"}, true, ""}}, 30.0);

    t::eq(c.free_capacity(), 3, "all chambers free initially");
    t::check(bool(c.evaluate(mk("L1", "NITRIDE"))), "qualified chamber found");
    t::eq(int(c.evaluate(mk("L1", "TUNGSTEN")).reason),
          int(Rejection::ChamberUnqualified), "no chamber qualified for recipe");

    c.admit(mk("L1", "NITRIDE"));
    c.admit(mk("L2", "NITRIDE"));
    t::eq(c.free_capacity(), 1, "two chambers busy");
    t::eq(int(c.evaluate(mk("L3", "NITRIDE")).reason),
          int(Rejection::NoCapacity),
          "no NITRIDE chamber left (C is OXIDE-only)");
    t::check(bool(c.evaluate(mk("L4", "OXIDE"))), "OXIDE still runs on chamber C");

    // A dead chamber degrades capacity; it does not take the tool offline.
    c.set_chamber_online("C", false);
    t::eq(c.free_capacity(), 0, "downed chamber removes capacity");
    t::check(c.online(), "tool stays online with a dead chamber");
}

void test_litho_reticle() {
    t::suite("LithoScanner / reticle exclusivity");
    ReticlePool pool;
    LithoScanner s3("LITHO_03", "LITHO", pool, {"M1_EXPOSE", "M2_EXPOSE"}, 22.0, 300.0);
    LithoScanner s4("LITHO_04", "LITHO", pool, {"M1_EXPOSE"}, 24.0, 300.0);

    Lot a = mk("A", "M1_EXPOSE", "RET_77");
    t::check(bool(s3.evaluate(a)), "scanner 3 eligible before reticle claimed");
    t::check(bool(s4.evaluate(a)), "scanner 4 eligible before reticle claimed");

    t::check(s3.admit(a), "scanner 3 admits and claims the reticle");

    Lot b = mk("B", "M1_EXPOSE", "RET_77");
    t::eq(int(s4.evaluate(b).reason), int(Rejection::ReticleUnavailable),
          "SECOND SCANNER BLOCKED: reticle is exclusive");

    // A different reticle is unaffected.
    Lot c = mk("C", "M1_EXPOSE", "RET_88");
    t::check(bool(s4.evaluate(c)), "different reticle still runs on scanner 4");

    t::eq(s3.evaluate(mk("D", "M1_EXPOSE", "RET_77")).setup_s, 0.0,
          "no swap cost for the already-loaded reticle");
}

void test_probe_tester() {
    t::suite("ProbeTester");
    ProbeTester p("P1", "SORT", {"PC_MCU_A"}, {"SORT_HOT", "SORT_AMB"},
                  4, 18.0, 1200.0, 900.0);
    p.set_card_for_product("AUTOMOTIVE_MCU_01", "PC_MCU_A");
    p.set_card_for_product("COMMODITY_LOGIC_09", "PC_LOGIC_B");   // card NOT installed
    p.set_temp_for_program("SORT_HOT", TestTemp::Hot);

    auto e = p.evaluate(mk("L1", "SORT_HOT", "", 1.0, 3600.0, "AUTOMOTIVE_MCU_01"));
    t::check(bool(e), "matching probe card is eligible");
    t::eq(e.setup_s, 1200.0 + 900.0, "first lot pays card change + hot soak");
    t::eq(e.process_s, 18.0 * 25 / 4, "parallel sites divide process time");

    t::eq(int(p.evaluate(mk("L2", "SORT_HOT", "", 1.0, 3600.0,
                            "COMMODITY_LOGIC_09")).reason),
          int(Rejection::ChamberUnqualified),
          "product with no installed probe card is rejected");

    p.admit(mk("L1", "SORT_HOT", "", 1.0, 3600.0, "AUTOMOTIVE_MCU_01"));
    p.release("L1");
    t::eq(p.evaluate(mk("L3", "SORT_HOT", "", 1.0, 3600.0,
                        "AUTOMOTIVE_MCU_01")).setup_s, 0.0,
          "no setup for same card and same temperature");
    t::eq(p.evaluate(mk("L4", "SORT_AMB", "", 1.0, 3600.0,
                        "AUTOMOTIVE_MCU_01")).setup_s, 900.0,
          "temperature change costs a soak");
}

void test_metrology() {
    t::suite("MetrologyStation");
    MetrologyStation m("M1", "METRO", {"CD_MEASURE"}, 0.20, 2, 480.0);
    Lot l = mk("L1", "CD_MEASURE");

    // Determinism matters more than the sampling rate itself: the plan must be
    // reproducible across restarts or it cannot be audited.
    const bool first = m.measurement_required(l, false, false);
    for (int i = 0; i < 50; ++i)
        if (m.measurement_required(l, false, false) != first) {
            t::check(false, "sampling decision is deterministic"); return;
        }
    t::check(true, "sampling decision is deterministic across repeats");
    t::check(m.measurement_required(l, true, false), "SPC alarm forces measurement");
    t::check(m.measurement_required(l, false, true), "post-PM forces measurement");
    t::eq(m.free_capacity(), 2, "parallel slots reported");
}

// ---------------------------------------------------------------------------
// SUITE 2 — property tests. These catch the class of bug that unit tests miss.
// ---------------------------------------------------------------------------

void test_evaluate_admit_consistency() {
    t::suite("PROPERTY: evaluate() and admit() never disagree");

    ToolRegistry reg;
    ReticlePool pool;
    auto issues = ToolFactory::instance().load("config/fab_tools.json", reg, pool);
    bool loaded = true;
    for (const auto& i : issues)
        if (i.severity == ConfigIssue::Severity::Error) loaded = false;
    if (!loaded) {
        t::check(false, "config loaded", "config/fab_tools.json failed to load");
        return;
    }

    std::mt19937 rng(42);
    std::vector<std::string> recipes = {"POLY_ETCH","METAL_ETCH","GATE_OX",
                                        "NITRIDE","OXIDE","M1_EXPOSE",
                                        "CD_MEASURE","SORT_HOT"};
    std::uniform_int_distribution<int> pick(0, (int)recipes.size()-1);
    std::uniform_real_distribution<double> unit(0.0, 1.0);

    int violations = 0, admitted = 0, checked = 0;
    for (int iter = 0; iter < 4000; ++iter) {
        const std::string rc = recipes[pick(rng)];
        Lot lot = mk("L" + std::to_string(iter), rc,
                     rc == "M1_EXPOSE" ? "RET_M1_77" : "");

        for (auto* tool : reg.all()) {
            // Randomly perturb tool state so we exercise edge conditions.
            if (unit(rng) < 0.02) tool->set_online(!tool->online());

            const Eligibility e = tool->evaluate(lot);
            ++checked;
            if (e) {
                // THE INVARIANT: if evaluate() says yes, admit() must succeed.
                // A disagreement means the fast path can hand a vehicle to a
                // tool that then refuses it.
                if (!tool->admit(lot)) { ++violations; }
                else { ++admitted; tool->release(lot.lot_id); }
            }
        }
    }
    t::eq(violations, 0, "evaluate()==true implies admit() succeeds");
    t::check(admitted > 100, "property test actually exercised admits",
             "only " + std::to_string(admitted) + " admits over " +
             std::to_string(checked) + " evaluations");
}

void test_admitted_never_violates() {
    t::suite("PROPERTY: admitted lots never exceed capacity");
    SingleWaferTool tool("E1", "ETCH", {"POLY_ETCH"}, 45.0, 600.0);
    int over = 0;
    for (int i = 0; i < 100; ++i)
        if (tool.admit(mk("L" + std::to_string(i), "POLY_ETCH"))) {
            if (tool.free_capacity() < 0) ++over;
        }
    t::eq(over, 0, "capacity never goes negative under admit pressure");
    t::eq(tool.free_capacity(), 0, "exactly one lot held");
}

// ---------------------------------------------------------------------------
// SUITE 3 — the objective. This is where the silent, expensive bug lives.
// ---------------------------------------------------------------------------

AssignmentModel synthetic(int n_lots, int n_tools, unsigned seed = 1);

void test_objective_scaling() {
    t::suite("OBJECTIVE: idling must never be cheaper than producing");

    auto m = synthetic(120, 30);

    double max_cost = 1.0;
    for (const auto& e : m.entries) max_cost = std::max(max_cost, e.cost);

    // The bug that shipped OPTIMAL and an idle fab: a fixed unassignment
    // penalty smaller than the cost of running the lot. The solver then
    // correctly prefers to run nothing.
    double min_penalty = 1e18;
    for (std::size_t l = 0; l < m.lot_ids.size(); ++l) {
        const double urgency = m.lot_priority[l] *
            (1.0 + 3600.0 / std::max(m.lot_slack_s[l], 60.0));
        min_penalty = std::min(min_penalty, max_cost * urgency);
    }
    t::check(min_penalty >= max_cost,
             "cheapest unassignment penalty >= most expensive assignment",
             "min_penalty=" + std::to_string(min_penalty) +
             " max_cost=" + std::to_string(max_cost));

    // An empty plan must score strictly worse than any real plan.
    GreedySolver g;
    SolveParams sp;
    auto r = g.solve(m, {}, sp);
    const double empty_score = score(m, {});
    const double greedy_score = score(m, r.assignment);
    t::check(greedy_score < empty_score,
             "any assignment scores better than assigning nothing",
             "greedy=" + std::to_string(greedy_score) +
             " empty=" + std::to_string(empty_score));
}

// ---------------------------------------------------------------------------
// SUITE 4 — solver contract. Whatever backend is linked must obey these.
// ---------------------------------------------------------------------------

void test_solver_contract() {
    t::suite("SOLVER CONTRACT: every result must be feasible");

    for (const char* name : {"greedy", "cpsat"}) {
        auto backend = make_solver(name);
        auto m = synthetic(150, 40);
        SolveParams sp;
        sp.time_limit_s = 2.0;
        auto r = backend->solve(m, {}, sp);

        const auto v = audit_solution(m, r.assignment);
        std::string detail;
        for (std::size_t i = 0; i < v.size() && i < 3; ++i)
            detail += (i ? "; " : "") + v[i];
        t::check(v.empty(), std::string(name) + ": solution has 0 violations",
                 detail);

        // A solver must never assign a lot twice, and never to a pair the
        // eligibility engine rejected.
        std::set<int> seen;
        bool dup = false;
        for (const auto& [l, tl] : r.assignment)
            if (!seen.insert(l).second) dup = true;
        t::check(!dup, std::string(name) + ": no lot assigned twice");
    }
}

void test_determinism() {
    t::suite("DETERMINISM: identical input gives identical output");
    auto m = synthetic(150, 40);
    SolveParams sp;
    sp.time_limit_s = 2.0;
    sp.deterministic = true;

    auto a = make_solver("greedy")->solve(m, {}, sp);
    auto b = make_solver("greedy")->solve(m, {}, sp);
    t::check(a.assignment == b.assignment,
             "greedy is bit-identical across runs");
    // NOTE: for CP-SAT this only holds with interleave_search + fixed seed AND
    // a deadline that is not hit. A timed-out solve is NOT reproducible; if you
    // need replayability, log the incumbent, not the inputs.
}

// ---------------------------------------------------------------------------
// Synthetic model generator + benchmark
// ---------------------------------------------------------------------------

AssignmentModel synthetic(int n_lots, int n_tools, unsigned seed) {
    std::mt19937 rng(seed);
    std::uniform_real_distribution<double> unit(0.0, 1.0);
    std::uniform_real_distribution<double> slack(300.0, 9000.0);
    std::uniform_real_distribution<double> prio(0.8, 5.0);
    std::uniform_real_distribution<double> proc(500.0, 7200.0);

    const std::vector<RecipeId> recipes =
        {"POLY_ETCH","METAL_ETCH","GATE_OX","NITRIDE","M1_EXPOSE"};

    AssignmentModel m;
    for (int tl = 0; tl < n_tools; ++tl) {
        m.tool_ids.push_back("T" + std::to_string(tl));
        const int kind = tl % 5;
        if (kind == 2) {                       // batch furnace
            m.tool_kinds.push_back("BATCH_FURNACE");
            m.tool_capacity.push_back(6);
            m.tool_min_batch.push_back(4);
            m.tool_max_batch.push_back(6);
        } else if (kind == 4) {                // litho scanner
            m.tool_kinds.push_back("LITHO_SCANNER");
            m.tool_capacity.push_back(1);
            m.tool_min_batch.push_back(0);
            m.tool_max_batch.push_back(1);
            m.scanner_tools.push_back(tl);
        } else {
            m.tool_kinds.push_back("SINGLE_WAFER");
            m.tool_capacity.push_back(1);
            m.tool_min_batch.push_back(0);
            m.tool_max_batch.push_back(1);
        }
    }

    const std::set<int> scanners(m.scanner_tools.begin(), m.scanner_tools.end());
    std::uniform_int_distribution<int> pick_r(0, (int)recipes.size()-1);
    std::uniform_int_distribution<int> pick_t(0, n_tools-1);
    std::uniform_int_distribution<int> n_elig(5, 15);

    for (int l = 0; l < n_lots; ++l) {
        const RecipeId rc = recipes[pick_r(rng)];
        m.lot_ids.push_back("L" + std::to_string(l));
        m.lot_recipe.push_back(rc);
        m.lot_reticle.push_back(rc == "M1_EXPOSE"
            ? (unit(rng) < 0.34 ? "RET_A" : unit(rng) < 0.5 ? "RET_B" : "RET_C")
            : "");
        m.lot_slack_s.push_back(slack(rng));
        m.lot_priority.push_back(prio(rng));
    }

    for (int l = 0; l < n_lots; ++l) {
        const RecipeId& rc = m.lot_recipe[l];
        const int k = n_elig(rng);
        std::set<int> chosen;
        for (int i = 0; i < k * 3 && (int)chosen.size() < k; ++i) {
            const int tl = pick_t(rng);
            const bool is_scanner = scanners.count(tl) > 0;
            const bool is_batch   = m.tool_min_batch[tl] > 0;
            if (rc == "M1_EXPOSE" && !is_scanner) continue;
            if (rc != "M1_EXPOSE" && is_scanner)  continue;
            if (rc == "GATE_OX"  && !is_batch)    continue;
            if (rc != "GATE_OX"  && is_batch)     continue;
            chosen.insert(tl);
        }
        for (int tl : chosen) {
            const double p = proc(rng);
            const double s = unit(rng) < 0.4 ? 0.0 : (unit(rng) < 0.5 ? 300.0 : 600.0);
            m.entries.push_back({l, tl, (s + p) / std::max(m.lot_priority[l], 0.01),
                                 s, p});
        }
    }
    return m;
}

// Version of a backend as actually linked. Returns nullopt when the backend
// is absent -- callers must render that as "unavailable", never as a default.
// An unversioned solver row is not reproducible and must not look like one.
static std::optional<std::string> backend_version(const std::string& name) {
#ifdef FAB_HAVE_ORTOOLS
    if (name == "cpsat")
        return operations_research::OrToolsVersionString();
#endif
    if (name == "greedy") return std::string("in-tree");
    return std::nullopt;
}

void bench(double budget) {
    // Say plainly which backends are real. Two rows that agree because one
    // silently fell back to the other looks like a tie and is not one.
    std::cout << "\n== BACKENDS ==\n";
    bool any_missing = false;
    for (const char* name : {"greedy", "cpsat", "gurobi", "highs"}) {
        auto b = make_solver(name);
        const bool ok = b->available();
        any_missing |= !ok;
        const auto ver = backend_version(name);
        std::cout << "  " << std::setw(8) << std::left << name << std::right
                  << (ok ? "linked" : "NOT LINKED -> falls back to greedy");
        if (ok)
            std::cout << "  version " << (ver ? *ver : std::string("UNAVAILABLE"));
        std::cout << "\n";
    }
    if (any_missing)
        std::cout << "\n  Rows for an unlinked backend are greedy's numbers "
                     "wearing its name.\n  Rebuild with -DFAB_HAVE_ORTOOLS "
                     "before drawing any conclusion.\n";

    // Reproducibility metadata. Solver performance moves between releases, so
    // a table of numbers without the version, thread count, time limit and
    // stopping criterion cannot be compared against anything -- including a
    // later run of this same file.
    {
        SolveParams ref;
        ref.time_limit_s = budget;
        const auto cv = backend_version("cpsat");
        std::cout << "\n== RUN CONFIG ==\n"
                  << "  cpsat version    " << (cv ? *cv : std::string("unavailable (not linked)")) << "\n"
                  << "  threads          " << ref.threads << "\n"
                  << "  time limit       " << ref.time_limit_s << " s per solve\n"
                  << "  relative gap     " << ref.relative_gap << "\n"
                  << "  deterministic    " << (ref.deterministic ? "yes" : "no") << "\n"
                  << "  stopping         first of: proven optimal, gap <= "
                  << ref.relative_gap << ", or time limit\n";
        if (!cv)
            std::cout << "  >>> Quote no CP-SAT number from this run: the "
                         "backend is not linked.\n";
    }

    std::cout << "\n== BENCHMARK ==\n"
              << std::setw(7) << "lots" << std::setw(7) << "tools"
              << std::setw(8) << "pairs"  << std::setw(11) << "solver"
              << std::setw(10) << "assigned" << std::setw(14) << "objective"
              << std::setw(10) << "solve" << "  viol\n"
              << std::string(78, '-') << "\n";

    for (auto [nl, nt] : std::vector<std::pair<int,int>>{
             {50,20},{100,25},{200,50},{400,100},{800,200}}) {
        auto m = synthetic(nl, nt);
        for (const char* name : {"greedy", "cpsat"}) {
            auto backend = make_solver(name);
            SolveParams sp;
            sp.time_limit_s = budget;
            const auto t0 = std::chrono::steady_clock::now();
            auto r = backend->solve(m, {}, sp);
            const double ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - t0).count();
            const auto v = audit_solution(m, r.assignment);
            std::cout << std::setw(7) << nl << std::setw(7) << nt
                      << std::setw(8) << m.entries.size()
                      << std::setw(11) << name
                      << std::setw(10) << r.assignment.size()
                      << std::setw(14) << std::fixed << std::setprecision(0)
                      << score(m, r.assignment)
                      << std::setw(8) << std::setprecision(1) << ms << "ms"
                      << "  " << v.size() << "\n";
        }
    }
    std::cout << "\nviol = hard-constraint violations. ANY nonzero value means "
                 "that solver's\nassigned-count is fiction. Compare objectives "
                 "only between rows with viol=0.\n";
}

// ---------------------------------------------------------------------------

int main(int argc, char** argv) {
    std::string mode = argc > 1 ? argv[1] : "";

    if (mode == "--bench") {
        bench(argc > 2 ? std::atof(argv[2]) : 2.0);
        return 0;
    }

    std::cout << "fabdisp test suite\n";

    test_single_wafer();
    test_batch_furnace();
    test_cluster();
    test_litho_reticle();
    test_probe_tester();
    test_metrology();

    if (mode != "--audit-only") {
        test_evaluate_admit_consistency();
        test_admitted_never_violates();
    }

    test_objective_scaling();
    test_solver_contract();
    test_determinism();

    const int rc = t::summary();
    if (rc == 0) std::cout << "\nRun ./fabtest --bench for the solver comparison.\n";
    return rc;
}
