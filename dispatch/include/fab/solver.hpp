#pragma once
// fab/solver.hpp — pluggable optimization backend.
//
// The planner builds an AssignmentModel from MachineConfiguration::evaluate()
// and hands it to a SolverBackend. Every backend consumes the SAME model, so
// swapping solvers is a one-line change and an apples-to-apples benchmark.
//
//   GreedySolver  — always available. Fallback + baseline the others must beat.
//   CpSatSolver   — OR-Tools. Native scheduling constraints. Recommended for
//                   the tactical layer. -DFAB_HAVE_ORTOOLS
//   GurobiSolver  — commercial MILP. -DFAB_HAVE_GUROBI
//   HighsSolver   — free MILP, reads the LP text SolverExporter already emits.
//                   -DFAB_HAVE_HIGHS
//
// Selection is by name at runtime (--solver cpsat), so ops can fall back
// without a redeploy.

#include "fab/machine_config.hpp"

// Third-party solver headers must be included at FILE SCOPE. They were
// previously inside `namespace fab`, which nests all of abseil and protobuf --
// and libstdc++ -- inside fab::, so <utility> stops seeing std::. The CP-SAT
// path therefore never compiled, let alone linked.
#ifdef FAB_HAVE_ORTOOLS
#include "ortools/sat/cp_model.h"
#include "ortools/sat/cp_model_solver.h"
#endif

#include <chrono>
#include <memory>
#include <string>
#include <set>
#include <unordered_map>
#include <vector>

namespace fab {

struct SolveParams {
    double time_limit_s   = 5.0;
    double relative_gap   = 0.02;
    int    threads        = 8;
    bool   deterministic  = true;   // replayability beats a few % of speed
};

enum class SolveStatus { Optimal, Feasible, Infeasible, NoIncumbent, Error };

struct SolveResult {
    SolveStatus status = SolveStatus::NoIncumbent;
    // lot_index -> tool_index. Absent means the lot was left unassigned.
    std::unordered_map<int, int> assignment;
    double objective   = 0.0;
    double gap         = 1.0;
    double solve_time_s = 0.0;
    std::string detail;

    bool usable() const {
        return status == SolveStatus::Optimal || status == SolveStatus::Feasible;
    }
};

class SolverBackend {
public:
    virtual ~SolverBackend() = default;
    virtual const char* name() const = 0;
    virtual bool available() const = 0;
    virtual SolveResult solve(const AssignmentModel& m,
                              const std::vector<Lot>& lots,
                              const SolveParams& p) = 0;
    // Warm start from the previous cycle. Between two 10s cycles the fab has
    // barely moved, so this is a large win for the MIP backends.
    virtual void set_hint(const std::unordered_map<int, int>&) {}
};

// ---------------------------------------------------------------------------
// Greedy: urgency-ordered, cheapest-feasible-tool assignment.
// Not a stub — this is the timeout fallback and the benchmark baseline.
// ---------------------------------------------------------------------------

class GreedySolver : public SolverBackend {
    static inline const ReticleId kNoReticle{};
public:
    const char* name() const override { return "greedy"; }
    bool available() const override { return true; }

    SolveResult solve(const AssignmentModel& m,
                      const std::vector<Lot>& lots,
                      const SolveParams&) override {
        const auto t0 = std::chrono::steady_clock::now();
        SolveResult r;

        // Cheapest entry first, subject to remaining capacity.
        std::vector<const AssignmentEntry*> sorted;
        sorted.reserve(m.entries.size());
        for (const auto& e : m.entries) sorted.push_back(&e);
        std::sort(sorted.begin(), sorted.end(),
                  [](const AssignmentEntry* a, const AssignmentEntry* b) {
                      return a->cost < b->cost;
                  });

        std::vector<int> used(m.tool_ids.size(), 0);
        // Reticle exclusivity. Greedy MUST enforce this: without it the
        // assignments it returns are physically impossible and its "assigned"
        // count is fiction. Benchmarking against CP-SAT exposed this.
        std::unordered_map<ReticleId, int> reticle_on;
        const std::set<int> scanners(m.scanner_tools.begin(), m.scanner_tools.end());

        for (const auto* e : sorted) {
            if (r.assignment.count(e->lot_index)) continue;          // lot taken
            if (used[e->tool_index] >= m.tool_capacity[e->tool_index]) continue;

            const ReticleId& ret = m.lot_reticle.empty()
                                 ? kNoReticle : m.lot_reticle[e->lot_index];
            const bool needs_reticle = !ret.empty() && scanners.count(e->tool_index);
            if (needs_reticle) {
                auto held = reticle_on.find(ret);
                if (held != reticle_on.end() && held->second != e->tool_index)
                    continue;                    // reticle is on another scanner
            }

            r.assignment[e->lot_index] = e->tool_index;
            used[e->tool_index]++;
            if (needs_reticle) reticle_on[ret] = e->tool_index;
            r.objective += e->cost;
        }

        // Batch minimum: unwind furnace loads that never reached min_batch,
        // otherwise we occupy a furnace with a batch it cannot legally fire.
        for (std::size_t t = 0; t < m.tool_ids.size(); ++t) {
            if (m.tool_min_batch[t] <= 0) continue;
            if (used[t] > 0 && used[t] < m.tool_min_batch[t]) {
                for (auto it = r.assignment.begin(); it != r.assignment.end(); )
                    it = (it->second == (int)t) ? r.assignment.erase(it) : ++it;
                used[t] = 0;
            }
        }

        r.status = SolveStatus::Feasible;
        r.gap    = 1.0;   // greedy proves nothing
        r.solve_time_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - t0).count();
        r.detail = "greedy cost-ordered";
        return r;
    }
};

// ---------------------------------------------------------------------------
// CP-SAT (OR-Tools). THE tactical solver.
//
// Why CP-SAT and not MILP, expressed as code rather than argument:
//
//   BATCH FIRE. The LP exporter emits big-M indicator rows:
//       x1+x2+x3 - 4y >= 0 ;  x1+x2+x3 - 6y <= 0
//   In LP relaxation y goes fractional -- 0.4 of a furnace fires -- and the
//   bound is worthless. Below it is a reified constraint that PROPAGATES:
//   fewer than min_batch staged => y is false, immediately, no branching.
//
//   RETICLE EXCLUSIVITY. In MILP this is time-indexed, which is where models
//   go to die. Here it is AddAtMostOne over the scanners sharing a reticle.
//
//   BATCH RECIPE COMPATIBILITY. A furnace fires one recipe. Expressing "all
//   lots in this batch agree" in MILP needs a binary per (tool,recipe) plus
//   linking rows. Here it is one BoolVar per group with implications.
//
// Build: -DFAB_HAVE_ORTOOLS, link ortools.
// ---------------------------------------------------------------------------

class CpSatSolver : public SolverBackend {
public:
    const char* name() const override { return "cpsat"; }
    bool available() const override {
#ifdef FAB_HAVE_ORTOOLS
        return true;
#else
        return false;
#endif
    }

    void set_hint(const std::unordered_map<int, int>& h) override { hint_ = h; }

    SolveResult solve(const AssignmentModel& m,
                      const std::vector<Lot>& lots,
                      const SolveParams& p) override {
#ifdef FAB_HAVE_ORTOOLS
        using namespace operations_research::sat;
        const auto t0 = std::chrono::steady_clock::now();
        SolveResult r;

        CpModelBuilder cp;
        const int nL = static_cast<int>(m.lot_ids.size());
        const int nT = static_cast<int>(m.tool_ids.size());

        // --- variables: one BoolVar per FEASIBLE pair only ------------------
        // evaluate() already pruned the rest, so infeasible pairs never become
        // variables. This is why the model stays small as the horizon grows.
        std::map<std::pair<int,int>, BoolVar> x;
        std::vector<std::vector<BoolVar>> by_lot(nL), by_tool(nT);
        for (const auto& e : m.entries) {
            BoolVar v = cp.NewBoolVar().WithName(
                "x_" + m.lot_ids[e.lot_index] + "_" + m.tool_ids[e.tool_index]);
            x[{e.lot_index, e.tool_index}] = v;
            by_lot[e.lot_index].push_back(v);
            by_tool[e.tool_index].push_back(v);
        }

        // --- each lot runs at most once -------------------------------------
        for (int l = 0; l < nL; ++l)
            if (!by_lot[l].empty()) cp.AddAtMostOne(by_lot[l]);

        // --- tool capacity ---------------------------------------------------
        for (int t = 0; t < nT; ++t)
            if (!by_tool[t].empty())
                cp.AddLessOrEqual(LinearExpr::Sum(by_tool[t]), m.tool_capacity[t]);

        // --- BATCH FURNACES: reified fire + single-recipe batches ------------
        // Group the eligible lots on each batch tool by recipe. A furnace may
        // fire at most one group, and a fired group must reach min_batch.
        for (int t = 0; t < nT; ++t) {
            if (m.tool_min_batch[t] <= 0) continue;

            std::map<std::string, std::vector<BoolVar>> groups;
            for (const auto& e : m.entries)
                if (e.tool_index == t)
                    groups[m.lot_recipe[e.lot_index]]
                        .push_back(x.at({e.lot_index, t}));

            std::vector<BoolVar> fire_flags;
            for (auto& [recipe, vars] : groups) {
                BoolVar fire = cp.NewBoolVar().WithName(
                    "fire_" + m.tool_ids[t] + "_" + recipe);
                fire_flags.push_back(fire);

                // fire  => size in [min_batch, max_batch]
                cp.AddGreaterOrEqual(LinearExpr::Sum(vars), m.tool_min_batch[t])
                  .OnlyEnforceIf(fire);
                cp.AddLessOrEqual(LinearExpr::Sum(vars), m.tool_max_batch[t])
                  .OnlyEnforceIf(fire);
                // !fire => nothing loaded. This is the constraint big-M cannot
                // express tightly, and it is where MILP loses on this problem.
                cp.AddEquality(LinearExpr::Sum(vars), 0).OnlyEnforceIf(Not(fire));
            }
            // One recipe per fire: no mixed-recipe batches, ever.
            if (fire_flags.size() > 1) cp.AddAtMostOne(fire_flags);
        }

        // --- RETICLE EXCLUSIVITY --------------------------------------------
        // One reticle cannot be mounted on two scanners simultaneously. Group
        // the scanner assignments by reticle; at most one may be active.
        std::map<std::string, std::vector<BoolVar>> by_reticle;
        for (const auto& e : m.entries) {
            const std::string& ret = m.lot_reticle[e.lot_index];
            if (ret.empty()) continue;
            const bool is_scanner =
                std::find(m.scanner_tools.begin(), m.scanner_tools.end(),
                          e.tool_index) != m.scanner_tools.end();
            if (is_scanner)
                by_reticle[ret].push_back(x.at({e.lot_index, e.tool_index}));
        }
        for (auto& [ret, vars] : by_reticle)
            if (vars.size() > 1) cp.AddAtMostOne(vars);

        // --- objective -------------------------------------------------------
        // SCALING IS LOAD-BEARING, and getting it wrong is silent.
        //
        // The penalty for leaving a lot unrun must EXCEED the cost of running
        // it on the worst eligible tool. Anchor it to max_cost and that holds
        // by construction. With a fixed constant instead, the optimizer
        // correctly concludes that the cheapest plan is to run nothing -- it
        // returns OPTIMAL, every constraint is satisfied, and the fab idles.
        // Benchmarking caught this; nothing in the type system would have.
        double max_cost = 1.0;
        for (const auto& e : m.entries) max_cost = std::max(max_cost, e.cost);

        LinearExpr obj;
        for (const auto& e : m.entries)
            obj += static_cast<int64_t>(e.cost * 100)
                 * x.at({e.lot_index, e.tool_index});

        for (int l = 0; l < nL; ++l) {
            if (by_lot[l].empty()) continue;
            BoolVar unassigned = cp.NewBoolVar();
            cp.AddEquality(LinearExpr::Sum(by_lot[l]), 0).OnlyEnforceIf(unassigned);
            cp.AddEquality(LinearExpr::Sum(by_lot[l]), 1).OnlyEnforceIf(Not(unassigned));
            // Urgency then orders WHICH lots win when capacity is short.
            //
            // Floored at 1.0, and that floor is load-bearing. Anchoring to
            // max_cost only guarantees "running beats idling" while the
            // multiplier is >= 1. Priority below 1 is legal -- producer_sim
            // draws from 0.8 -- and with generous slack the 3600/slack term
            // decays to ~0, leaving urgency ~= priority < 1. The penalty then
            // falls BELOW the worst-tool cost and the solver correctly prefers
            // to leave that lot unrun: the same silent idle the max_cost anchor
            // above was introduced to eliminate, surviving in a narrow band.
            const double urgency = std::max(1.0, m.lot_priority[l] *
                (1.0 + 3600.0 / std::max(m.lot_slack_s[l], 60.0)));
            obj += static_cast<int64_t>(max_cost * 100 * urgency) * unassigned;
        }
        cp.Minimize(obj);

        // --- warm start from the previous cycle ------------------------------
        // Between two 10s cycles the fab has barely moved, so this is a large
        // win in practice.
        if (!hint_.empty()) {
            for (const auto& [l, t] : hint_) {
                auto it = x.find({l, t});
                if (it != x.end()) cp.AddHint(it->second, true);
            }
        }

        // --- solve under a HARD deadline --------------------------------------
        SatParameters params;
        params.set_max_time_in_seconds(p.time_limit_s);
        params.set_num_search_workers(p.threads);
        params.set_relative_gap_limit(p.relative_gap);
        if (p.deterministic) {
            // Bit-identical output for identical input. When a lot misses
            // Q-time and someone asks why, you must be able to replay it.
            params.set_interleave_search(true);
            params.set_random_seed(1);
        }

        Model model;
        model.Add(NewSatParameters(params));
        const CpSolverResponse resp = SolveCpModel(cp.Build(), &model);

        r.solve_time_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - t0).count();

        if (resp.status() == CpSolverStatus::OPTIMAL ||
            resp.status() == CpSolverStatus::FEASIBLE) {
            r.status = resp.status() == CpSolverStatus::OPTIMAL
                     ? SolveStatus::Optimal : SolveStatus::Feasible;
            for (const auto& e : m.entries)
                if (SolutionBooleanValue(resp, x.at({e.lot_index, e.tool_index})))
                    r.assignment[e.lot_index] = e.tool_index;
            r.objective = resp.objective_value() / 100.0;
            r.gap = resp.best_objective_bound() > 0
                  ? std::abs(resp.objective_value() - resp.best_objective_bound())
                    / std::max(1.0, std::abs(resp.objective_value())) : 0.0;
            r.detail = "cpsat " + std::string(
                resp.status() == CpSolverStatus::OPTIMAL ? "optimal" : "feasible");
            return r;
        }

        if (resp.status() == CpSolverStatus::INFEASIBLE) {
            // Do NOT fall back on infeasible: the model says no assignment
            // exists, and greedy would happily invent one that violates a
            // batch minimum or double-books a reticle.
            r.status = SolveStatus::Infeasible;
            r.detail = "cpsat proved infeasible";
            return r;
        }

        // No incumbent within budget -> greedy, so the fab keeps moving.
        auto g = fallback_.solve(m, lots, p);
        g.detail = "cpsat no incumbent in " +
                   std::to_string(p.time_limit_s) + "s -> greedy";
        return g;
#else
        auto r = fallback_.solve(m, lots, p);
        r.detail = "cpsat not linked -> greedy fallback";
        return r;
#endif
    }

private:
    GreedySolver fallback_;
    std::unordered_map<int, int> hint_;
};

// ---------------------------------------------------------------------------
// Gurobi.
//
// >>> PLACEHOLDER: body compiled out until -DFAB_HAVE_GUROBI.
//
//   GRBEnv env(true);
//   env.set("LogFile", "");                       // no disk I/O on this thread
//   env.set(GRB_IntParam_OutputFlag, 0);
//   env.start();
//   GRBModel model(env);
//   std::vector<GRBVar> x;
//   for (auto& e : m.entries)
//       x.push_back(model.addVar(0,1,e.cost, GRB_BINARY, m.var_name(e)));
//   ... AtMostOne per lot, capacity per tool, batch min via indicator ...
//   model.set(GRB_DoubleParam_TimeLimit, p.time_limit_s);
//   model.set(GRB_DoubleParam_MIPGap,    p.relative_gap);
//   model.set(GRB_IntParam_Threads,      p.threads);
//   for (auto& [i,v] : hint_) x[i].set(GRB_DoubleAttr_Start, v);
//   model.optimize();
//
// NOTE: needs a floating license server reachable from the fab network zone.
// Budget for the license daemon being a single point of failure — which is
// exactly why the greedy fallback stays in the binary.
// ---------------------------------------------------------------------------

class GurobiSolver : public SolverBackend {
public:
    const char* name() const override { return "gurobi"; }
    bool available() const override {
#ifdef FAB_HAVE_GUROBI
        return true;
#else
        return false;
#endif
    }

    void set_hint(const std::unordered_map<int, int>& h) override { hint_ = h; }

    SolveResult solve(const AssignmentModel& m,
                      const std::vector<Lot>& lots,
                      const SolveParams& p) override {
#ifdef FAB_HAVE_GUROBI
        // ... model construction per the comment block above ...
#endif
        auto r = fallback_.solve(m, lots, p);
        r.detail = "gurobi unavailable -> greedy fallback";
        return r;
    }

private:
    GreedySolver fallback_;
    std::unordered_map<int, int> hint_;
};

// ---------------------------------------------------------------------------
// HiGHS — free MILP. Cheapest integration path of all: SolverExporter::to_lp()
// already emits CPLEX LP text, which HiGHS reads directly.
//
// >>> PLACEHOLDER: -DFAB_HAVE_HIGHS
//   Highs h;
//   h.setOptionValue("output_flag", false);
//   h.setOptionValue("time_limit", p.time_limit_s);
//   h.setOptionValue("mip_rel_gap", p.relative_gap);
//   h.setOptionValue("threads", p.threads);
//   h.readModel(lp_path);            // or build via passModel to skip disk
//   h.run();
//   // map h.getSolution().col_value back through m.entries order
// ---------------------------------------------------------------------------

class HighsSolver : public SolverBackend {
public:
    const char* name() const override { return "highs"; }
    bool available() const override {
#ifdef FAB_HAVE_HIGHS
        return true;
#else
        return false;
#endif
    }

    SolveResult solve(const AssignmentModel& m,
                      const std::vector<Lot>& lots,
                      const SolveParams& p) override {
#ifdef FAB_HAVE_HIGHS
        // const std::string lp = SolverExporter::to_lp(m);  // already available
#endif
        auto r = fallback_.solve(m, lots, p);
        r.detail = "highs unavailable -> greedy fallback";
        return r;
    }

private:
    GreedySolver fallback_;
};

// ---------------------------------------------------------------------------
// Runtime selection.
// ---------------------------------------------------------------------------

inline std::unique_ptr<SolverBackend> make_solver(const std::string& name) {
    if (name == "cpsat")  return std::make_unique<CpSatSolver>();
    if (name == "gurobi") return std::make_unique<GurobiSolver>();
    if (name == "highs")  return std::make_unique<HighsSolver>();
    return std::make_unique<GreedySolver>();
}

} // namespace fab
