#pragma once
// fab/planner.hpp — the tactical layer.
//
// Responsibilities, in order:
//   1. snapshot ready lots
//   2. build the AssignmentModel from evaluate()  (single source of truth)
//   3. hand it to whichever SolverBackend is configured
//   4. compute an ALTERNATE tool per lot so the fast path survives a mid-cycle
//      tool failure without re-solving
//   5. flatten to a Slate and publish
//
// Deadline-driven, never optimality-driven. If the solver returns nothing
// usable, keep serving the previous Slate — that is correct behavior, not an
// error path.

#include "fab/machine_config.hpp"
#include "fab/slate.hpp"
#include "fab/solver.hpp"

#include <memory>

namespace fab {

struct PlannerConfig {
    double cycle_seconds        = 10.0;
    double solve_budget_s       = 5.0;
    double relative_gap         = 0.02;
    int    threads              = 8;
    double stale_slate_alarm_s  = 60.0;
    int    stale_cycles_alarm   = 3;
};

struct PlanResult {
    std::shared_ptr<Slate> slate;
    SolveStatus status      = SolveStatus::NoIncumbent;
    double      solve_time_s = 0.0;
    double      objective   = 0.0;
    int         assigned    = 0;
    int         variables   = 0;
    int         ready       = 0;
    std::string detail;

    bool usable() const {
        return slate && assigned > 0 &&
               (status == SolveStatus::Optimal || status == SolveStatus::Feasible);
    }
};

class Planner {
public:
    explicit Planner(std::unique_ptr<SolverBackend> backend)
        : backend_(std::move(backend)) {}

    const char* solver_name() const { return backend_->name(); }
    bool solver_available() const   { return backend_->available(); }

    PlanResult plan(const ToolRegistry& reg,
                    const std::vector<Lot>& lots,
                    uint64_t cycle_id,
                    const PlannerConfig& cfg) {
        PlanResult r;
        r.ready = static_cast<int>(lots.size());
        r.slate = std::make_shared<Slate>();
        r.slate->cycle_id = cycle_id;

        // (2) One model, built from the same evaluate() the fast path's rules
        // derive from. The solver cannot disagree with the tool objects.
        AssignmentModel model = SolverExporter::build(reg, lots);
        r.variables = static_cast<int>(model.entries.size());

        if (model.entries.empty()) {
            r.detail = "no feasible lot/tool pair";
            return r;
        }

        // (3) Warm start from last cycle, then solve under a hard deadline.
        SolveParams sp;
        sp.time_limit_s = cfg.solve_budget_s;
        sp.relative_gap = cfg.relative_gap;
        sp.threads      = cfg.threads;
        backend_->set_hint(last_assignment_);

        SolveResult sr = backend_->solve(model, lots, sp);
        r.status       = sr.status;
        r.solve_time_s = sr.solve_time_s;
        r.objective    = sr.objective;
        r.detail       = sr.detail;

        if (!sr.usable()) return r;
        last_assignment_ = sr.assignment;

        // (4) Alternates. For each assigned lot, find the next-best eligible
        // tool that isn't the primary. Costs one extra rank() pass per lot at
        // planning cadence and buys failover with zero fast-path work.
        uint32_t rank = 0;
        for (const auto& [li, ti] : sr.assignment) {
            const Lot& lot = lots[li];
            RouteToken tok;
            tok.primary = model.tool_ids[ti];

            double proc = 0.0;
            for (const auto& e : model.entries)
                if (e.lot_index == li && e.tool_index == ti) proc = e.process_s;
            tok.expected_process_s = proc;

            tok.alternate = tok.primary;   // degenerate default
            double best_alt = 1e18;
            for (const auto& e : model.entries) {
                if (e.lot_index != li || e.tool_index == ti) continue;
                if (e.cost < best_alt) {
                    best_alt = e.cost;
                    tok.alternate = model.tool_ids[e.tool_index];
                }
            }

            tok.rank = rank++;
            r.slate->tokens[lot.lot_id] = tok;
            r.assigned++;
        }

        // (5) Flatten tool state for the fast path.
        for (auto* t : reg.all()) r.slate->tools[t->id()] = t->slice();
        return r;
    }

private:
    std::unique_ptr<SolverBackend> backend_;
    std::unordered_map<int, int>   last_assignment_;
};

} // namespace fab
