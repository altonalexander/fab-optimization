#pragma once
// fab/machine_config.hpp
// Object-oriented machine (tool) configuration model for the fab dispatcher.
//
// Layering rule that matches the multi-horizon architecture:
//   - Polymorphic classes live in the PLANNING layer (solver snapshot, eligibility,
//     capacity, batch formation). Virtual calls are fine here.
//   - The sub-millisecond dispatch path never touches these objects. Each tool
//     flattens itself into a POD "DispatchSlice" that the fast path reads.
//
// Build: g++ -std=c++20 -O2 machine_config.cpp -o machine_config

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>
#include <iostream>
#include <sstream>

namespace fab {

// ---------------------------------------------------------------------------
// 1. Value types shared by every configuration
// ---------------------------------------------------------------------------

using ToolId    = std::string;
using RecipeId  = std::string;
using ReticleId = std::string;

struct Lot {
    std::string lot_id;
    std::string product_id;
    RecipeId    recipe;
    ReticleId   reticle;              // empty for non-litho steps
    int         wafer_count   = 25;
    double      priority      = 1.0;  // from the tactical urgency vector
    double      qtime_slack_s = 1e9;  // seconds before Q-time violation

    // --- SMT2020 / route semantics (see docs/adr/0009) ---------------------
    // Added for the PySCFabSim bridge. Every field is defaulted, so the tool
    // classes that predate them (SingleWaferTool, BatchFurnace, ClusterTool,
    // LithoScanner, ProbeTester) and their tests are unaffected: only
    // FamilyTool reads them.
    //
    // family is the station family the CURRENT step routes to. It is what makes
    // the assignment problem block-diagonal, and therefore what Planner
    // decomposes on.
    std::string family;
    // setup_group is the SETUP value of the current step. Distinct from recipe:
    // two recipes can share a setup group and incur no changeover between them.
    // Empty means "this step imposes no setup", which is NOT the same as a
    // setup group that happens to be named "" on the tool.
    std::string setup_group;
    // Absolute due date, simulator seconds. -1 means "no due date", which is
    // how a lot with no order deadline must be scored rather than as due now.
    double      due_s         = -1.0;
    // Batch window for the current step, in LOTS (PySCFabSim has already
    // divided the wafer counts through). 1/1 means the step does not batch.
    int         batch_min     = 1;
    int         batch_max     = 1;
    // Seconds this lot has been queued at its current step. Feeds the ageing
    // term so a lot cannot be starved indefinitely by a stream of urgent work.
    double      waiting_s     = 0.0;
    // Expected processing seconds for the current step at nominal tool speed.
    // In SMT2020 process time is a property of the STEP, not of the machine —
    // machines within a family differ only by `speed` — so the caller supplies
    // it and FamilyTool divides by its own speed. 0 means "not supplied", and
    // FamilyTool then falls back to its configured default.
    double      step_process_s = 0.0;
};

// Why a tool refused a lot. Never throw on the eligibility path.
enum class Rejection {
    None, ToolDown, RecipeNotQualified, NoCapacity,
    ReticleUnavailable, BatchIncompatible, ChamberUnqualified
};

struct Eligibility {
    bool      ok        = false;
    Rejection reason    = Rejection::None;
    double    setup_s   = 0.0;   // changeover cost if this lot goes next
    double    process_s = 0.0;   // expected processing time
    explicit operator bool() const { return ok; }
};

// Flat, cache-friendly snapshot handed to the real-time loop.
struct DispatchSlice {
    ToolId  tool_id;
    bool    online          = false;
    int     free_slots      = 0;
    double  utilization     = 0.0;
    double  ready_in_s      = 0.0;
    uint64_t qualified_hash = 0;  // bitset/hash of qualified recipes
};

// ---------------------------------------------------------------------------
// 2. Abstract base: the contract every machine configuration must satisfy
// ---------------------------------------------------------------------------

class MachineConfiguration {
public:
    MachineConfiguration(ToolId id, std::string area)
        : id_(std::move(id)), area_(std::move(area)) {}
    virtual ~MachineConfiguration() = default;

    MachineConfiguration(const MachineConfiguration&)            = delete;
    MachineConfiguration& operator=(const MachineConfiguration&) = delete;

    // --- Identity / common state (non-virtual: same for all tool classes) ---
    const ToolId&      id()   const noexcept { return id_; }
    const std::string& area() const noexcept { return area_; }
    bool   online()      const noexcept { return online_; }
    void   set_online(bool v)  noexcept { online_ = v; }
    double utilization() const noexcept { return utilization_; }

    // --- The polymorphic surface ---------------------------------------------
    virtual std::string_view kind() const noexcept = 0;

    // Can this tool run this lot right now, and at what cost?
    virtual Eligibility evaluate(const Lot& lot) const = 0;

    // Slots (wafers, lots, or chambers) still available this instant.
    virtual int free_capacity() const noexcept = 0;

    // Commit a lot. Returns false if the tool changed state since evaluate().
    virtual bool admit(const Lot& lot) = 0;

    // Release a finished lot.
    virtual void release(const std::string& lot_id) = 0;

    // Flatten into the POD the sub-millisecond path reads. Called by the
    // planning thread only; result is published via atomic pointer swap.
    virtual DispatchSlice slice() const;

protected:
    ToolId      id_;
    std::string area_;
    bool        online_      = true;
    double      utilization_ = 0.0;
    std::vector<std::string> in_process_;

    bool qualified(const std::vector<RecipeId>& list, const RecipeId& r) const {
        return std::find(list.begin(), list.end(), r) != list.end();
    }
};

DispatchSlice MachineConfiguration::slice() const {
    DispatchSlice s;
    s.tool_id     = id_;
    s.online      = online_;
    s.free_slots  = free_capacity();
    s.utilization = utilization_;
    return s;
}

// ---------------------------------------------------------------------------
// 3. Configuration #1 — Single-wafer serial tool (etch, CMP, implant)
//    One lot at a time, recipe-dependent process time, setup on recipe change.
// ---------------------------------------------------------------------------

class SingleWaferTool : public MachineConfiguration {
public:
    SingleWaferTool(ToolId id, std::string area,
                    std::vector<RecipeId> qualified_recipes,
                    double sec_per_wafer, double changeover_s)
        : MachineConfiguration(std::move(id), std::move(area)),
          recipes_(std::move(qualified_recipes)),
          sec_per_wafer_(sec_per_wafer), changeover_s_(changeover_s) {}

    std::string_view kind() const noexcept override { return "SINGLE_WAFER"; }

    Eligibility evaluate(const Lot& lot) const override {
        Eligibility e;
        if (!online_)                        { e.reason = Rejection::ToolDown; return e; }
        if (!qualified(recipes_, lot.recipe)){ e.reason = Rejection::RecipeNotQualified; return e; }
        if (!in_process_.empty())            { e.reason = Rejection::NoCapacity; return e; }
        e.ok        = true;
        e.setup_s   = (lot.recipe == current_recipe_) ? 0.0 : changeover_s_;
        e.process_s = sec_per_wafer_ * lot.wafer_count;
        return e;
    }

    int free_capacity() const noexcept override { return in_process_.empty() ? 1 : 0; }

    bool admit(const Lot& lot) override {
        if (!evaluate(lot)) return false;
        in_process_.push_back(lot.lot_id);
        current_recipe_ = lot.recipe;
        utilization_    = 1.0;
        return true;
    }

    void release(const std::string& lot_id) override {
        std::erase(in_process_, lot_id);
        utilization_ = in_process_.empty() ? 0.0 : 1.0;
    }

private:
    std::vector<RecipeId> recipes_;
    double   sec_per_wafer_;
    double   changeover_s_;
    RecipeId current_recipe_;
};

// ---------------------------------------------------------------------------
// 4. Configuration #2 — Batch furnace (diffusion, anneal, wet bench)
//    Fixed process time regardless of load; needs min batch to fire; all lots
//    in the batch must share a recipe. This is where most dispatchers lose money.
// ---------------------------------------------------------------------------

class BatchFurnace : public MachineConfiguration {
public:
    BatchFurnace(ToolId id, std::string area,
                 std::vector<RecipeId> qualified_recipes,
                 int min_lots, int max_lots,
                 double fixed_process_s, double max_hold_s)
        : MachineConfiguration(std::move(id), std::move(area)),
          recipes_(std::move(qualified_recipes)),
          min_lots_(min_lots), max_lots_(max_lots),
          fixed_process_s_(fixed_process_s), max_hold_s_(max_hold_s) {}

    std::string_view kind() const noexcept override { return "BATCH_FURNACE"; }

    Eligibility evaluate(const Lot& lot) const override {
        Eligibility e;
        if (!online_)                         { e.reason = Rejection::ToolDown; return e; }
        if (!qualified(recipes_, lot.recipe)) { e.reason = Rejection::RecipeNotQualified; return e; }
        if (free_capacity() <= 0)             { e.reason = Rejection::NoCapacity; return e; }
        if (!staged_.empty() && staged_recipe_ != lot.recipe) {
            e.reason = Rejection::BatchIncompatible; return e;
        }
        e.ok        = true;
        e.setup_s   = 0.0;
        e.process_s = fixed_process_s_;   // independent of batch size
        return e;
    }

    int free_capacity() const noexcept override {
        return firing_ ? 0 : max_lots_ - static_cast<int>(staged_.size());
    }

    bool admit(const Lot& lot) override {
        if (!evaluate(lot)) return false;
        if (staged_.empty()) staged_recipe_ = lot.recipe;
        staged_.push_back(lot.lot_id);
        return true;
    }

    void release(const std::string& lot_id) override {
        std::erase(in_process_, lot_id);
        if (in_process_.empty()) { firing_ = false; utilization_ = 0.0; }
    }

    // Batch-specific policy the dispatcher calls each planning cycle.
    // Fire early when a staged lot is about to burn its Q-time.
    bool should_fire(double oldest_wait_s, double min_qtime_slack_s) const {
        if (firing_ || staged_.empty()) return false;
        if (static_cast<int>(staged_.size()) >= min_lots_) return true;
        return oldest_wait_s >= max_hold_s_ ||
               min_qtime_slack_s <= fixed_process_s_ * 1.2;   // partial batch
    }

    int min_batch() const noexcept { return min_lots_; }
    int max_batch() const noexcept { return max_lots_; }

    void fire() {
        firing_ = true;
        in_process_ = std::move(staged_);
        staged_.clear();
        utilization_ = 1.0;
    }

private:
    std::vector<RecipeId>    recipes_;
    std::vector<std::string> staged_;
    RecipeId staged_recipe_;
    int      min_lots_, max_lots_;
    double   fixed_process_s_, max_hold_s_;
    bool     firing_ = false;
};

// ---------------------------------------------------------------------------
// 5. Configuration #3 — Cluster tool (multi-chamber, parallel, per-chamber qual)
// ---------------------------------------------------------------------------

class ClusterTool : public MachineConfiguration {
public:
    struct Chamber {
        std::string           name;
        std::vector<RecipeId> recipes;
        bool                  online = true;
        std::string           busy_with;
    };

    ClusterTool(ToolId id, std::string area,
                std::vector<Chamber> chambers, double sec_per_wafer)
        : MachineConfiguration(std::move(id), std::move(area)),
          chambers_(std::move(chambers)), sec_per_wafer_(sec_per_wafer) {}

    std::string_view kind() const noexcept override { return "CLUSTER"; }

    Eligibility evaluate(const Lot& lot) const override {
        Eligibility e;
        if (!online_) { e.reason = Rejection::ToolDown; return e; }
        const Chamber* c = pick_chamber(lot.recipe);
        if (!c) {
            e.reason = any_qualified(lot.recipe) ? Rejection::NoCapacity
                                                 : Rejection::ChamberUnqualified;
            return e;
        }
        e.ok        = true;
        e.process_s = sec_per_wafer_ * lot.wafer_count;
        return e;
    }

    int free_capacity() const noexcept override {
        return static_cast<int>(std::count_if(chambers_.begin(), chambers_.end(),
            [](const Chamber& c){ return c.online && c.busy_with.empty(); }));
    }

    bool admit(const Lot& lot) override {
        Chamber* c = const_cast<Chamber*>(pick_chamber(lot.recipe));
        if (!c) return false;
        c->busy_with = lot.lot_id;
        in_process_.push_back(lot.lot_id);
        recompute_utilization();
        return true;
    }

    void release(const std::string& lot_id) override {
        for (auto& c : chambers_)
            if (c.busy_with == lot_id) c.busy_with.clear();
        std::erase(in_process_, lot_id);
        recompute_utilization();
    }

    // Degrade instead of failing: a dead chamber lowers capacity, not availability.
    void set_chamber_online(const std::string& name, bool up) {
        for (auto& c : chambers_) if (c.name == name) c.online = up;
        recompute_utilization();
    }

private:
    const Chamber* pick_chamber(const RecipeId& r) const {
        for (const auto& c : chambers_)
            if (c.online && c.busy_with.empty() && qualified(c.recipes, r)) return &c;
        return nullptr;
    }
    bool any_qualified(const RecipeId& r) const {
        return std::any_of(chambers_.begin(), chambers_.end(),
            [&](const Chamber& c){ return qualified(c.recipes, r); });
    }
    void recompute_utilization() {
        const auto total = std::count_if(chambers_.begin(), chambers_.end(),
            [](const Chamber& c){ return c.online; });
        utilization_ = total ? static_cast<double>(in_process_.size()) / total : 0.0;
    }

    std::vector<Chamber> chambers_;
    double sec_per_wafer_;
};

// ---------------------------------------------------------------------------
// 6. Configuration #4 — Litho scanner (reticle-constrained, heavy setup)
//    Adds a shared external resource: the reticle can only be on one tool.
// ---------------------------------------------------------------------------

class ReticlePool {
public:
    bool available(const ReticleId& r, const ToolId& tool) const {
        auto it = holder_.find(r);
        return it == holder_.end() || it->second == tool;
    }
    void assign(const ReticleId& r, const ToolId& tool) { holder_[r] = tool; }
private:
    std::unordered_map<ReticleId, ToolId> holder_;
};

class LithoScanner : public MachineConfiguration {
public:
    LithoScanner(ToolId id, std::string area, ReticlePool& pool,
                 std::vector<RecipeId> recipes,
                 double sec_per_wafer, double reticle_swap_s)
        : MachineConfiguration(std::move(id), std::move(area)),
          pool_(pool), recipes_(std::move(recipes)),
          sec_per_wafer_(sec_per_wafer), reticle_swap_s_(reticle_swap_s) {}

    std::string_view kind() const noexcept override { return "LITHO_SCANNER"; }

    Eligibility evaluate(const Lot& lot) const override {
        Eligibility e;
        if (!online_)                         { e.reason = Rejection::ToolDown; return e; }
        if (!qualified(recipes_, lot.recipe)) { e.reason = Rejection::RecipeNotQualified; return e; }
        if (!in_process_.empty())             { e.reason = Rejection::NoCapacity; return e; }
        if (!pool_.available(lot.reticle, id_)){ e.reason = Rejection::ReticleUnavailable; return e; }
        e.ok        = true;
        e.setup_s   = (lot.reticle == loaded_reticle_) ? 0.0 : reticle_swap_s_;
        e.process_s = sec_per_wafer_ * lot.wafer_count;
        return e;
    }

    int free_capacity() const noexcept override { return in_process_.empty() ? 1 : 0; }

    bool admit(const Lot& lot) override {
        if (!evaluate(lot)) return false;
        pool_.assign(lot.reticle, id_);
        loaded_reticle_ = lot.reticle;
        in_process_.push_back(lot.lot_id);
        utilization_ = 1.0;
        return true;
    }

    void release(const std::string& lot_id) override {
        std::erase(in_process_, lot_id);
        utilization_ = in_process_.empty() ? 0.0 : 1.0;
    }

private:
    ReticlePool&          pool_;
    std::vector<RecipeId> recipes_;
    double                sec_per_wafer_, reticle_swap_s_;
    ReticleId             loaded_reticle_;
};

// ---------------------------------------------------------------------------
// 6b. Configuration #5 — Metrology station (sampled, not every lot measured)
//     The interesting behavior is that skipping is a legitimate outcome: the
//     dispatcher must be able to ask "does this lot even need me?" before
//     spending a slot. Sampling is per (product, recipe) with forced measurement
//     after a tool excursion or an SPC alarm.
// ---------------------------------------------------------------------------

class MetrologyStation : public MachineConfiguration {
public:
    MetrologyStation(ToolId id, std::string area,
                     std::vector<RecipeId> recipes,
                     double sample_rate,          // 0.0 - 1.0
                     int    parallel_slots,
                     double sec_per_lot)
        : MachineConfiguration(std::move(id), std::move(area)),
          recipes_(std::move(recipes)), sample_rate_(sample_rate),
          slots_(parallel_slots), sec_per_lot_(sec_per_lot) {}

    std::string_view kind() const noexcept override { return "METROLOGY"; }

    Eligibility evaluate(const Lot& lot) const override {
        Eligibility e;
        if (!online_)                         { e.reason = Rejection::ToolDown; return e; }
        if (!qualified(recipes_, lot.recipe)) { e.reason = Rejection::RecipeNotQualified; return e; }
        if (free_capacity() <= 0)             { e.reason = Rejection::NoCapacity; return e; }
        e.ok        = true;
        e.process_s = sec_per_lot_;
        return e;
    }

    // Skip decision. Deterministic hash keeps the sample plan reproducible
    // across dispatcher restarts — never use rand() here, auditors will ask.
    bool measurement_required(const Lot& lot, bool spc_alarm, bool post_maintenance) const {
        if (spc_alarm || post_maintenance) return true;
        if (sample_rate_ >= 1.0) return true;
        if (sample_rate_ <= 0.0) return false;
        const auto h = std::hash<std::string>{}(lot.lot_id + lot.recipe);
        return static_cast<double>(h % 1000) / 1000.0 < sample_rate_;
    }

    int free_capacity() const noexcept override {
        return slots_ - static_cast<int>(in_process_.size());
    }

    bool admit(const Lot& lot) override {
        if (!evaluate(lot)) return false;
        in_process_.push_back(lot.lot_id);
        utilization_ = static_cast<double>(in_process_.size()) / slots_;
        return true;
    }

    void release(const std::string& lot_id) override {
        std::erase(in_process_, lot_id);
        utilization_ = static_cast<double>(in_process_.size()) / slots_;
    }

    double sample_rate() const noexcept { return sample_rate_; }

private:
    std::vector<RecipeId> recipes_;
    double sample_rate_;
    int    slots_;
    double sec_per_lot_;
};

// ---------------------------------------------------------------------------
// 6c. Configuration #6 — Wafer probe / final tester
//     Two extra constraints that no front-end tool has: a physical probe card
//     (or load board) that must match the product, and a temperature soak that
//     makes hot/cold changeover far more expensive than a recipe change.
// ---------------------------------------------------------------------------

enum class TestTemp { Ambient, Hot, Cold };

class ProbeTester : public MachineConfiguration {
public:
    ProbeTester(ToolId id, std::string area,
                std::vector<std::string> installed_probe_cards,
                std::vector<RecipeId> test_programs,
                int    parallel_sites,
                double sec_per_wafer,
                double card_change_s,
                double temp_soak_s)
        : MachineConfiguration(std::move(id), std::move(area)),
          cards_(std::move(installed_probe_cards)),
          programs_(std::move(test_programs)),
          sites_(parallel_sites), sec_per_wafer_(sec_per_wafer),
          card_change_s_(card_change_s), temp_soak_s_(temp_soak_s) {}

    std::string_view kind() const noexcept override { return "PROBE_TESTER"; }

    // Product -> probe card mapping comes from the MES; injected, not hardcoded.
    void set_card_for_product(const std::string& product, const std::string& card) {
        product_card_[product] = card;
    }
    void set_temp_for_program(const RecipeId& program, TestTemp t) {
        program_temp_[program] = t;
    }

    Eligibility evaluate(const Lot& lot) const override {
        Eligibility e;
        if (!online_)                          { e.reason = Rejection::ToolDown; return e; }
        if (!qualified(programs_, lot.recipe)) { e.reason = Rejection::RecipeNotQualified; return e; }
        if (free_capacity() <= 0)              { e.reason = Rejection::NoCapacity; return e; }

        auto pc = product_card_.find(lot.product_id);
        if (pc == product_card_.end() ||
            std::find(cards_.begin(), cards_.end(), pc->second) == cards_.end()) {
            e.reason = Rejection::ChamberUnqualified;   // no matching probe card
            return e;
        }

        double setup = 0.0;
        if (pc->second != loaded_card_) setup += card_change_s_;
        const TestTemp want = temp_of(lot.recipe);
        if (want != current_temp_)      setup += temp_soak_s_;

        e.ok        = true;
        e.setup_s   = setup;
        // Parallel sites divide the per-wafer time.
        e.process_s = sec_per_wafer_ * lot.wafer_count / std::max(sites_, 1);
        return e;
    }

    int free_capacity() const noexcept override { return in_process_.empty() ? 1 : 0; }

    bool admit(const Lot& lot) override {
        if (!evaluate(lot)) return false;
        loaded_card_  = product_card_.at(lot.product_id);
        current_temp_ = temp_of(lot.recipe);
        in_process_.push_back(lot.lot_id);
        utilization_ = 1.0;
        return true;
    }

    void release(const std::string& lot_id) override {
        std::erase(in_process_, lot_id);
        utilization_ = in_process_.empty() ? 0.0 : 1.0;
    }

private:
    TestTemp temp_of(const RecipeId& program) const {
        auto it = program_temp_.find(program);
        return it == program_temp_.end() ? TestTemp::Ambient : it->second;
    }

    std::vector<std::string> cards_;
    std::vector<RecipeId>    programs_;
    std::unordered_map<std::string, std::string> product_card_;
    std::unordered_map<RecipeId, TestTemp>       program_temp_;
    int         sites_;
    double      sec_per_wafer_, card_change_s_, temp_soak_s_;
    std::string loaded_card_;
    TestTemp    current_temp_ = TestTemp::Ambient;
};

// ---------------------------------------------------------------------------
// 7. Registry + factory: add a machine configuration without touching the
//    dispatcher. Register a builder keyed by the "kind" string in your config.
// ---------------------------------------------------------------------------

class ToolRegistry {
public:
    void add(std::unique_ptr<MachineConfiguration> t) {
        by_id_[t->id()] = t.get();
        tools_.push_back(std::move(t));
    }

    MachineConfiguration* find(const ToolId& id) const {
        auto it = by_id_.find(id);
        return it == by_id_.end() ? nullptr : it->second;
    }

    // Planning-layer scan: rank every eligible tool for a lot.
    struct Candidate { MachineConfiguration* tool; Eligibility e; double score; };

    std::vector<Candidate> rank(const Lot& lot) const {
        std::vector<Candidate> out;
        for (const auto& t : tools_) {
            Eligibility e = t->evaluate(lot);
            if (!e) continue;
            // Lower is better: setup + processing, discounted by lot priority.
            const double score = (e.setup_s + e.process_s) / std::max(lot.priority, 0.01);
            out.push_back({t.get(), e, score});
        }
        std::sort(out.begin(), out.end(),
                  [](const Candidate& a, const Candidate& b){ return a.score < b.score; });
        return out;
    }

    // Publish the flat view consumed by the sub-millisecond loop.
    std::vector<DispatchSlice> snapshot() const {
        std::vector<DispatchSlice> v;
        v.reserve(tools_.size());
        for (const auto& t : tools_) v.push_back(t->slice());
        return v;
    }

    std::vector<MachineConfiguration*> all() const {
        std::vector<MachineConfiguration*> v;
        v.reserve(tools_.size());
        for (const auto& t : tools_) v.push_back(t.get());
        return v;
    }

private:
    std::vector<std::unique_ptr<MachineConfiguration>>   tools_;
    std::unordered_map<ToolId, MachineConfiguration*>    by_id_;
};

// ---------------------------------------------------------------------------
// 9. Solver bridge: the same class hierarchy feeds the MILP.
//
//     evaluate() is the single source of truth for eligibility. The solver does
//     NOT get its own copy of the routing rules — it gets a sparse matrix built
//     by calling the exact same virtual method the fast path's planning layer
//     calls. One rule engine, two consumers. This is what keeps the Gurobi model
//     and the dispatcher from silently diverging after a qualification change.
//
//     Model:  binary x[l][t] = 1 if lot l is assigned to tool t this horizon
//       min  sum over (l,t) of cost[l][t] * x[l][t]
//       s.t. sum over t of x[l][t] <= 1                  (each lot at most once)
//            sum over l of x[l][t] <= capacity[t]        (tool capacity)
//            batch tools: sum over l of x[l][t] >= min_batch[t] * y[t]
// ---------------------------------------------------------------------------

struct AssignmentEntry {
    int    lot_index;
    int    tool_index;
    double cost;         // objective coefficient
    double setup_s;
    double process_s;
};

struct AssignmentModel {
    std::vector<std::string>     lot_ids;
    std::vector<ToolId>          tool_ids;
    std::vector<std::string>     tool_kinds;
    std::vector<int>             tool_capacity;
    std::vector<int>             tool_min_batch;   // 0 for non-batch tools
    std::vector<int>             tool_max_batch;   // == capacity for non-batch
    std::vector<AssignmentEntry> entries;          // sparse eligibility matrix

    // --- fields the CP-SAT model needs that an LP formulation cannot use ---
    // Reticle each lot requires (empty = none). Drives NoOverlap: one reticle
    // cannot be on two scanners at once.
    std::vector<ReticleId>       lot_reticle;
    // Recipe per lot. Batch tools may only fire a single-recipe batch, so the
    // model needs this to build per-(tool,recipe) batch groups.
    std::vector<RecipeId>        lot_recipe;
    // Q-time slack, seconds. Used to weight lateness in the objective.
    std::vector<double>          lot_slack_s;
    std::vector<double>          lot_priority;
    // Which tool indices are litho scanners, so reticle constraints only apply
    // where they mean something.
    std::vector<int>             scanner_tools;

    // Variable name the solver will see, so LP files stay human-readable
    // when someone has to debug an infeasible horizon at 3am.
    std::string var_name(const AssignmentEntry& e) const {
        return "x_" + lot_ids[e.lot_index] + "_" + tool_ids[e.tool_index];
    }
};

class SolverExporter {
public:
    // Cost model. Keep this in one place: the fast path's dot product should be
    // the linearized version of exactly this expression.
    static double cost(const Lot& lot, const Eligibility& e) {
        const double time_cost   = e.setup_s + e.process_s;
        const double urgency     = std::max(lot.priority, 0.01);
        // Q-time pressure: as slack collapses, the penalty for NOT running grows,
        // which the solver sees as a discount on running this pair.
        const double qtime_boost = 1.0 + 600.0 / std::max(lot.qtime_slack_s, 60.0);
        return time_cost / (urgency * qtime_boost);
    }

    static AssignmentModel build(const ToolRegistry& reg, const std::vector<Lot>& lots) {
        return build(reg.all(), lots);
    }

    // Overload over an explicit tool set. Per-family decomposition (see
    // docs/adr/0009) needs to build a model for a SUBSET of the registry, and
    // the block-diagonal structure makes that exact rather than approximate:
    // a lot is only ever eligible for tools in its own station family, so
    // splitting the matrix along family lines drops no feasible pair.
    static AssignmentModel build(const std::vector<MachineConfiguration*>& tools,
                                 const std::vector<Lot>& lots) {
        AssignmentModel m;

        for (const auto& l : lots) m.lot_ids.push_back(l.lot_id);
        for (auto* t : tools) {
            m.tool_ids.push_back(t->id());
            m.tool_kinds.emplace_back(t->kind());
            m.tool_capacity.push_back(t->free_capacity());
            // Batch minimums are configuration-specific: ask the object, don't
            // switch on a type tag.
            if (auto* f = dynamic_cast<BatchFurnace*>(t)) {
                m.tool_min_batch.push_back(f->min_batch());
                m.tool_max_batch.push_back(f->max_batch());
            } else {
                m.tool_min_batch.push_back(0);
                m.tool_max_batch.push_back(t->free_capacity());
            }
            if (t->kind() == std::string_view("LITHO_SCANNER"))
                m.scanner_tools.push_back(static_cast<int>(m.tool_ids.size() - 1));
        }

        for (const auto& l : lots) {
            m.lot_reticle.push_back(l.reticle);
            m.lot_recipe.push_back(l.recipe);
            m.lot_slack_s.push_back(l.qtime_slack_s);
            m.lot_priority.push_back(l.priority);
        }

        for (std::size_t li = 0; li < lots.size(); ++li) {
            for (std::size_t ti = 0; ti < tools.size(); ++ti) {
                Eligibility e = tools[ti]->evaluate(lots[li]);
                if (!e) continue;   // infeasible pair: no variable is created
                m.entries.push_back({static_cast<int>(li), static_cast<int>(ti),
                                     cost(lots[li], e), e.setup_s, e.process_s});
            }
        }
        return m;
    }

    // CPLEX LP format — readable by Gurobi, CBC, and OR-Tools' MPSolver.
    // Writing LP text keeps the fab dispatcher free of a hard Gurobi link
    // dependency; swap this for gurobi_c++.h addVar/addConstr calls if you
    // want the in-process API instead.
    static std::string to_lp(const AssignmentModel& m) {
        std::string s = "\\ Fab lot-to-tool assignment, generated from MachineConfiguration::evaluate()\n";
        s += "Minimize\n obj: ";
        for (std::size_t i = 0; i < m.entries.size(); ++i) {
            const auto& e = m.entries[i];
            if (i) s += " + ";
            s += fmt(e.cost) + " " + m.var_name(e);
        }
        s += "\nSubject To\n";

        // Each lot assigned at most once.
        for (std::size_t li = 0; li < m.lot_ids.size(); ++li) {
            std::string row;
            for (const auto& e : m.entries)
                if (e.lot_index == static_cast<int>(li))
                    row += (row.empty() ? "" : " + ") + m.var_name(e);
            if (!row.empty())
                s += " lot_" + m.lot_ids[li] + ": " + row + " <= 1\n";
        }

        // Tool capacity.
        for (std::size_t ti = 0; ti < m.tool_ids.size(); ++ti) {
            std::string row;
            for (const auto& e : m.entries)
                if (e.tool_index == static_cast<int>(ti))
                    row += (row.empty() ? "" : " + ") + m.var_name(e);
            if (!row.empty())
                s += " cap_" + m.tool_ids[ti] + ": " + row + " <= "
                   + std::to_string(m.tool_capacity[ti]) + "\n";
        }

        // Batch minimum: either fire a full-enough batch or load nothing.
        for (std::size_t ti = 0; ti < m.tool_ids.size(); ++ti) {
            if (m.tool_min_batch[ti] <= 0) continue;
            std::string row;
            for (const auto& e : m.entries)
                if (e.tool_index == static_cast<int>(ti))
                    row += (row.empty() ? "" : " + ") + m.var_name(e);
            if (row.empty()) continue;
            const std::string y = "y_" + m.tool_ids[ti];
            s += " batchmin_" + m.tool_ids[ti] + ": " + row + " - "
               + std::to_string(m.tool_min_batch[ti]) + " " + y + " >= 0\n";
            s += " batchlink_" + m.tool_ids[ti] + ": " + row + " - "
               + std::to_string(m.tool_capacity[ti]) + " " + y + " <= 0\n";
        }

        s += "Binaries\n";
        for (const auto& e : m.entries) s += " " + m.var_name(e) + "\n";
        for (std::size_t ti = 0; ti < m.tool_ids.size(); ++ti)
            if (m.tool_min_batch[ti] > 0) s += " y_" + m.tool_ids[ti] + "\n";
        s += "End\n";
        return s;
    }

    // Emit the model as JSON. This is the contract between the eligibility
    // engine and ANY solver: the C++ CP-SAT backend, the Python reference
    // implementation, and the benchmark harness all read this exact structure,
    // so a formulation bug shows up as a disagreement rather than silently.
    static std::string to_json(const AssignmentModel& m) {
        std::ostringstream o;
        auto arr_s = [&](const std::vector<std::string>& v) {
            o << "[";
            for (std::size_t i = 0; i < v.size(); ++i)
                o << (i ? "," : "") << "\"" << v[i] << "\"";
            o << "]";
        };
        auto arr_i = [&](const std::vector<int>& v) {
            o << "[";
            for (std::size_t i = 0; i < v.size(); ++i) o << (i ? "," : "") << v[i];
            o << "]";
        };
        auto arr_d = [&](const std::vector<double>& v) {
            o << "[";
            for (std::size_t i = 0; i < v.size(); ++i) o << (i ? "," : "") << v[i];
            o << "]";
        };

        o << "{\"lot_ids\":";      arr_s(m.lot_ids);
        o << ",\"tool_ids\":";     arr_s(m.tool_ids);
        o << ",\"tool_kinds\":";   arr_s(m.tool_kinds);
        o << ",\"tool_capacity\":";  arr_i(m.tool_capacity);
        o << ",\"tool_min_batch\":"; arr_i(m.tool_min_batch);
        o << ",\"tool_max_batch\":"; arr_i(m.tool_max_batch);
        o << ",\"scanner_tools\":";  arr_i(m.scanner_tools);
        o << ",\"lot_reticle\":";  arr_s(m.lot_reticle);
        o << ",\"lot_recipe\":";   arr_s(m.lot_recipe);
        o << ",\"lot_slack_s\":";  arr_d(m.lot_slack_s);
        o << ",\"lot_priority\":"; arr_d(m.lot_priority);
        o << ",\"entries\":[";
        for (std::size_t i = 0; i < m.entries.size(); ++i) {
            const auto& e = m.entries[i];
            o << (i ? "," : "")
              << "{\"lot\":" << e.lot_index << ",\"tool\":" << e.tool_index
              << ",\"cost\":" << e.cost
              << ",\"setup_s\":" << e.setup_s
              << ",\"process_s\":" << e.process_s << "}";
        }
        o << "]}";
        return o.str();
    }

private:
    static std::string fmt(double v) {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "%.4f", v);
        return buf;
    }

};

} // namespace fab

