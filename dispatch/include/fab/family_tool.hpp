#pragma once
// fab/family_tool.hpp — the SMT2020 station-family configuration.
//
// Why this class exists (docs/adr/0009): the tool classes in machine_config.hpp
// model a fab in terms of RECIPES and RETICLES. SMT2020 and PySCFabSim model
// one in terms of STATION FAMILIES, SETUP GROUPS and MINIMUM RUN LENGTHS.
// Forcing the latter through the former loses exactly the information that
// decides dispatch quality:
//
//   * setup was `(recipe == current) ? 0 : flat_changeover` — but SMT2020's
//     setup.txt is an ASYMMETRIC matrix of pair-dependent times,
//   * minimum run length had no representation at all, though greedy.py:120
//     calls the rule that enforces it "extrem wichtig",
//   * batches keyed on recipe alone, where the simulator keys them on
//     step + part.
//
// FamilyTool closes those three. It is additive: nothing here changes the
// behaviour of SingleWaferTool, BatchFurnace, ClusterTool, LithoScanner or
// ProbeTester, and their tests are untouched.
//
// Field mapping from the simulator, fixed here so both sides agree:
//
//     Lot::family       <- step.family        (STNFAM)
//     Lot::setup_group  <- step.setup_needed  (SETUP)
//     Lot::recipe       <- step.step_name     (DESC)   -- batch key, part 1
//     Lot::product_id   <- lot.part_name      (PART)   -- batch key, part 2
//     Lot::batch_min/max<- step.batch_min/max (already divided to LOTS)
//     Lot::step_process_s <- step.processing_time.avg()

#include "fab/machine_config.hpp"

#include <string>
#include <unordered_map>
#include <vector>

namespace fab {

// ---------------------------------------------------------------------------
// Asymmetric setup matrix. SMT2020's setup.txt gives a time for an ORDERED
// pair: going A->B need not cost the same as B->A, and a dispatcher that
// assumes symmetry will systematically mis-order a sequence of changeovers.
// ---------------------------------------------------------------------------
class SetupMatrix {
public:
    void set(const std::string& from, const std::string& to, double seconds) {
        t_[key(from, to)] = seconds;
    }

    // Default applies when the pair is not listed at all. A fab with a sparse
    // matrix means "unlisted pairs are free", so 0 is the right default; set it
    // explicitly if your dataset means otherwise.
    void set_default(double seconds) { default_s_ = seconds; }

    // Resolution order, most specific first:
    //   1. no setup imposed by the step, or already in that setup -> free
    //   2. the exact ordered pair
    //   3. the wildcard source ("", to) -- "coming from anywhere costs this"
    //   4. the configured default
    double lookup(const std::string& from, const std::string& to) const {
        if (to.empty() || from == to) return 0.0;
        auto it = t_.find(key(from, to));
        if (it != t_.end()) return it->second;
        it = t_.find(key("", to));
        if (it != t_.end()) return it->second;
        return default_s_;
    }

    std::size_t size() const noexcept { return t_.size(); }

private:
    // \x1f is a unit separator: it cannot appear in a SETUP name, so this is
    // collision-free without paying for a pair hash.
    static std::string key(const std::string& a, const std::string& b) {
        return a + '\x1f' + b;
    }
    std::unordered_map<std::string, double> t_;
    double default_s_ = 0.0;
};

// ---------------------------------------------------------------------------
// One machine in a station family.
//
// Capacity is in LOTS. A batch furnace in this model is a FamilyTool with
// capacity > 1 whose staged lots must share a batch key; a single-lot tool is
// the same class with capacity 1. That keeps one eligibility path rather than
// two, which matters because the solver and the fast path both go through it.
// ---------------------------------------------------------------------------
class FamilyTool : public MachineConfiguration {
public:
    FamilyTool(ToolId id, std::string area, std::string family,
               const SetupMatrix* setups,
               int capacity = 1,
               double speed = 1.0,
               double default_process_s = 0.0)
        : MachineConfiguration(std::move(id), std::move(area)),
          family_(std::move(family)), setups_(setups),
          capacity_(capacity < 1 ? 1 : capacity),
          speed_(speed <= 0.0 ? 1.0 : speed),
          default_process_s_(default_process_s) {}

    std::string_view kind() const noexcept override { return "FAMILY_TOOL"; }

    const std::string& family() const noexcept { return family_; }
    const std::string& current_setup() const noexcept { return current_setup_; }
    int  capacity() const noexcept { return capacity_; }

    // --- minimum run length -------------------------------------------------
    // After a changeover the tool owes the setup a minimum number of lots.
    // PySCFabSim expresses this as (min_runs_left, min_runs_setup) and treats a
    // mismatch as a HARD block (greedy.py:122 sets lots=None), not a
    // preference, so evaluate() rejects rather than merely penalising. Modelling
    // it as a soft cost would let the solver buy its way out of a constraint the
    // simulator will not actually let it break.
    void set_min_run_length(int lots) { min_run_length_ = lots < 0 ? 0 : lots; }
    int  min_run_length() const noexcept { return min_run_length_; }

    void set_min_runs(int left, std::string setup) {
        min_runs_left_  = left < 0 ? 0 : left;
        min_runs_setup_ = std::move(setup);
    }
    int  min_runs_left() const noexcept { return min_runs_left_; }
    const std::string& min_runs_setup() const noexcept { return min_runs_setup_; }

    // Direct state injection, used when mirroring a simulator machine rather
    // than driving one. Does not consume capacity.
    void set_current_setup(std::string s) { current_setup_ = std::move(s); }

    // --- eligibility --------------------------------------------------------
    Eligibility evaluate(const Lot& lot) const override {
        Eligibility e;
        if (!online_)                { e.reason = Rejection::ToolDown; return e; }
        // A lot only routes to its own station family. This is the property
        // that makes the assignment matrix block-diagonal, and hence the one
        // Planner decomposes on -- see docs/adr/0009.
        if (lot.family != family_)   { e.reason = Rejection::RecipeNotQualified; return e; }
        if (free_capacity() <= 0)    { e.reason = Rejection::NoCapacity; return e; }

        // Minimum-run gate. Hard, per the note above.
        if (min_runs_left_ > 0 && !lot.setup_group.empty() &&
            lot.setup_group != min_runs_setup_) {
            e.reason = Rejection::BatchIncompatible;
            return e;
        }

        // Batch compatibility: everything staged must share step + part.
        if (!staged_.empty() && batch_key(lot) != staged_key_) {
            e.reason = Rejection::BatchIncompatible;
            return e;
        }

        // A step that does not batch cannot join a tool that already has a lot
        // staged, even if the keys happen to match.
        if (!staged_.empty() && lot.batch_max <= 1) {
            e.reason = Rejection::BatchIncompatible;
            return e;
        }

        e.ok      = true;
        e.setup_s = setups_ ? setups_->lookup(current_setup_, lot.setup_group) : 0.0;
        // A batch runs in the time of one lot, not the sum: the furnace does not
        // care how full it is. Charging per-lot here is the classic way a
        // dispatcher talks itself out of filling a batch.
        const double base = lot.step_process_s > 0.0 ? lot.step_process_s
                                                     : default_process_s_;
        e.process_s = base / speed_;
        return e;
    }

    int free_capacity() const noexcept override {
        // Capacity is bounded by the tool AND by the batch window of whatever
        // is already staged: a step with batch_max 4 cannot fill a 6-lot furnace.
        const int by_tool  = capacity_ - static_cast<int>(staged_.size());
        if (staged_.empty()) return by_tool;
        const int by_batch = staged_batch_max_ - static_cast<int>(staged_.size());
        return by_tool < by_batch ? by_tool : by_batch;
    }

    bool admit(const Lot& lot) override {
        if (!evaluate(lot)) return false;

        const double setup = setups_ ? setups_->lookup(current_setup_, lot.setup_group)
                                     : 0.0;
        // A real changeover starts a new minimum run. Re-running the setup the
        // tool already holds does not, or a tool could never leave it.
        if (setup > 0.0 || (!lot.setup_group.empty() && lot.setup_group != current_setup_)) {
            current_setup_  = lot.setup_group;
            if (min_run_length_ > 0) {
                min_runs_left_  = min_run_length_;
                min_runs_setup_ = lot.setup_group;
            }
        }

        if (staged_.empty()) {
            staged_key_       = batch_key(lot);
            staged_batch_max_ = lot.batch_max < 1 ? 1 : lot.batch_max;
            staged_batch_min_ = lot.batch_min < 1 ? 1 : lot.batch_min;
        }
        staged_.push_back(lot.lot_id);
        in_process_.push_back(lot.lot_id);

        if (min_runs_left_ > 0) --min_runs_left_;

        utilization_ = static_cast<double>(staged_.size()) / capacity_;
        return true;
    }

    void release(const std::string& lot_id) override {
        std::erase(staged_, lot_id);
        std::erase(in_process_, lot_id);
        if (staged_.empty()) { staged_key_.clear(); staged_batch_max_ = 0; staged_batch_min_ = 0; }
        utilization_ = static_cast<double>(staged_.size()) / capacity_;
    }

    // True when the staged batch has reached its minimum and may legally fire.
    // The planner uses this to decide whether holding one more cycle is better
    // than firing a half-empty furnace.
    bool batch_ready() const noexcept {
        return !staged_.empty() &&
               static_cast<int>(staged_.size()) >= staged_batch_min_;
    }
    int staged_count() const noexcept { return static_cast<int>(staged_.size()); }

    static std::string batch_key(const Lot& lot) {
        // step + part, matching greedy.py:80's
        //   w.actual_step.step_name + '_' + w.part_name
        return lot.recipe + '\x1f' + lot.product_id;
    }

private:
    std::string        family_;
    const SetupMatrix* setups_;
    int                capacity_;
    double             speed_;
    double             default_process_s_;

    std::string current_setup_;
    int         min_run_length_ = 0;   // policy: lots owed after a changeover
    int         min_runs_left_  = 0;   // state: lots still owed
    std::string min_runs_setup_;

    std::vector<std::string> staged_;
    std::string staged_key_;
    int         staged_batch_max_ = 0;
    int         staged_batch_min_ = 0;
};

} // namespace fab
