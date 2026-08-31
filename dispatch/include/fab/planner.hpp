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

#include "fab/family_tool.hpp"
#include "fab/machine_config.hpp"
#include "fab/slate.hpp"
#include "fab/solver.hpp"

#include <map>
#include <memory>
#include <set>
#include <string>

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

    // -----------------------------------------------------------------------
    // Per-family decomposition. See docs/adr/0009 for why this is a
    // PRECONDITION rather than an optimization: on LVHM a whole-fab solve is
    // ~2,500 lots against 1,313 machines, where CP-SAT sits on its time limit
    // and returns a best incumbent. Split by station family it is ~25 lots
    // against ~12 machines -- roughly 300 variables -- which closes in
    // milliseconds. Over ~1M rebuilds in a 730-day run that is the difference
    // between "runs" and "does not run".
    //
    // The split is EXACT, not a heuristic. FamilyTool::evaluate() rejects any
    // lot whose family differs, so no feasible pair spans two blocks and the
    // union of the per-family optima is the global optimum of this model. That
    // holds only while the model has no cross-family constraint; the one
    // candidate, reticle exclusivity, is confined to litho. Adding a genuine
    // cross-family constraint later invalidates this and the ADR says so.
    //
    // `dirty` is lazy invalidation: when non-null, only families named in it
    // are re-solved and the rest are carried over from the previous slate.
    // Most families are quiet in any given cycle.
    PlanResult plan_by_family(const ToolRegistry& reg,
                              const std::vector<Lot>& lots,
                              uint64_t cycle_id,
                              const PlannerConfig& cfg,
                              const std::set<std::string>* dirty = nullptr) {
        PlanResult r;
        r.ready  = static_cast<int>(lots.size());
        r.slate  = std::make_shared<Slate>();
        r.slate->cycle_id = cycle_id;
        r.status = SolveStatus::NoIncumbent;

        // Bucket tools by family. A tool that is not a FamilyTool has no family
        // to decompose on, so it goes in one shared bucket keyed "" and is
        // solved together -- that preserves the old behaviour for the
        // recipe/reticle tool classes rather than silently dropping them.
        std::map<std::string, std::vector<MachineConfiguration*>> tools_by_family;
        for (auto* t : reg.all()) {
            if (auto* ft = dynamic_cast<FamilyTool*>(t))
                tools_by_family[ft->family()].push_back(t);
            else
                tools_by_family[""].push_back(t);
        }

        std::map<std::string, std::vector<Lot>> lots_by_family;
        for (const auto& l : lots) lots_by_family[l.family].push_back(l);

        const auto t0 = std::chrono::steady_clock::now();
        uint32_t rank = 0;
        int families_solved = 0, families_skipped = 0;

        for (auto& [fam, flots] : lots_by_family) {
            auto it = tools_by_family.find(fam);
            if (it == tools_by_family.end() || it->second.empty()) continue;

            if (dirty && !dirty->count(fam)) {
                // Carry this family forward untouched. Tokens for its lots came
                // from a previous solve and are still the best we know.
                if (last_slate_) {
                    for (const auto& l : flots) {
                        auto tk = last_slate_->tokens.find(l.lot_id);
                        if (tk != last_slate_->tokens.end()) {
                            r.slate->tokens[l.lot_id] = tk->second;
                            r.assigned++;
                        }
                    }
                }
                families_skipped++;
                continue;
            }

            AssignmentModel m = SolverExporter::build(it->second, flots);
            r.variables += static_cast<int>(m.entries.size());
            if (m.entries.empty()) continue;

            SolveParams sp;
            sp.time_limit_s = cfg.solve_budget_s;
            sp.relative_gap = cfg.relative_gap;
            sp.threads      = cfg.threads;
            // No warm start across families: hints are indexed by position
            // within one model, so a hint from a different family would be
            // meaningless at best and misleading at worst.
            backend_->set_hint({});

            SolveResult sr = backend_->solve(m, flots, sp);
            if (!sr.usable()) continue;
            families_solved++;
            r.objective += sr.objective;

            for (const auto& [li, ti] : sr.assignment) {
                RouteToken tok;
                tok.primary = m.tool_ids[ti];
                tok.alternate = tok.primary;

                double proc = 0.0, best_alt = 1e18;
                for (const auto& e : m.entries) {
                    if (e.lot_index != li) continue;
                    if (e.tool_index == ti) { proc = e.process_s; continue; }
                    if (e.cost < best_alt) {
                        best_alt = e.cost;
                        tok.alternate = m.tool_ids[e.tool_index];
                    }
                }
                tok.expected_process_s = proc;
                tok.rank = rank++;
                r.slate->tokens[m.lot_ids[li]] = tok;
                r.assigned++;
            }
        }

        r.solve_time_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - t0).count();
        if (r.assigned > 0) r.status = SolveStatus::Feasible;
        r.detail = "families solved=" + std::to_string(families_solved) +
                   " skipped=" + std::to_string(families_skipped);

        for (auto* t : reg.all()) r.slate->tools[t->id()] = t->slice();
        last_slate_ = r.slate;
        return r;
    }

private:
    std::unique_ptr<SolverBackend> backend_;
    std::unordered_map<int, int>   last_assignment_;
    std::shared_ptr<Slate>         last_slate_;
};

} // namespace fab
