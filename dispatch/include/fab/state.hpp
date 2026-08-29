#pragma once
// fab/state.hpp — single-writer mirror of the fab.
//
// Threading contract (this is the important part):
//   * ONE ingestion thread mutates FabState. Nobody else. Ever.
//   * The planner thread reads it under a shared lock to build a Slate.
//   * The fast path NEVER touches FabState — it reads the published Slate.
//
// Everything the dispatcher believes about the fab enters through apply().

#include "fab/events.hpp"
#include "fab/machine_config.hpp"

#include <mutex>
#include <shared_mutex>
#include <unordered_map>
#include <vector>

namespace fab {

struct IngestStats {
    uint64_t applied = 0;
    uint64_t malformed = 0;
    uint64_t unknown_tool = 0;
    uint64_t seq_gaps = 0;
    uint64_t started = 0;
    uint64_t completed = 0;
    uint64_t start_rejected = 0;     // tool filled between plan and arrival
    uint64_t start_unknown_lot = 0;
    uint64_t orphan_complete = 0;   // completion for a lot we never admitted
};

class FabState {
public:
    explicit FabState(ToolRegistry& reg) : reg_(reg) {}

    // ---- ingestion thread only -------------------------------------------
    void apply(const Envelope& e) {
        std::unique_lock lk(mu_);

        // Gap detection per producer key. A gap means we may have missed a
        // LotComplete and are now holding phantom capacity.
        // >>> PLACEHOLDER: on gap, trigger a full state resync from the MES
        //     rather than limping. Currently we only count it.
        // seq is monotonic per SOURCE stream (Kafka gives per-partition
        // ordering; each producer owns its own sequence space).
        if (!e.source.empty() && e.seq > 0) {
            auto it = last_seq_.find(e.source);
            if (it != last_seq_.end() && e.seq > it->second + 1)
                stats_.seq_gaps += (e.seq - it->second - 1);
            if (it == last_seq_.end() || e.seq > it->second)
                last_seq_[e.source] = e.seq;
        }

        switch (e.type) {
        case EventType::LotReady: {
            Lot l{e.lot_id, e.product_id, e.recipe, e.reticle,
                  e.wafer_count, e.priority, e.qtime_slack_s};
            ready_[e.lot_id] = l;
            break;
        }
        case EventType::LotStarted: {
            // Tool physically accepted the lot: consume capacity and take the
            // lot out of the ready pool. admit() re-checks eligibility, so a
            // stale decision against a now-busy tool is rejected here rather
            // than corrupting tool state.
            auto* t = reg_.find(e.tool_id);
            if (!t) { stats_.unknown_tool++; break; }
            auto it = ready_.find(e.lot_id);
            if (it == ready_.end()) { stats_.start_unknown_lot++; break; }
            if (t->admit(it->second)) {
                in_flight_[e.lot_id] = e.tool_id;
                ready_.erase(it);
                stats_.started++;
            } else {
                stats_.start_rejected++;   // tool filled up between plan and move
            }
            break;
        }
        case EventType::LotComplete: {
            if (auto* t = reg_.find(e.tool_id)) {
                t->release(e.lot_id);
                // Only count a completion for a lot we actually admitted.
                // Equipment emits COMPLETE for anything it was told to run,
                // including lots FabState rejected at start.
                if (in_flight_.erase(e.lot_id)) stats_.completed++;
                else stats_.orphan_complete++;
            } else stats_.unknown_tool++;
            ready_.erase(e.lot_id);
            break;
        }
        case EventType::ToolStatus: {
            if (auto* t = reg_.find(e.tool_id)) t->set_online(e.online);
            else stats_.unknown_tool++;
            break;
        }
        case EventType::ChamberStatus: {
            auto* t = reg_.find(e.tool_id);
            if (auto* c = dynamic_cast<ClusterTool*>(t))
                c->set_chamber_online(e.chamber, e.online);
            else stats_.unknown_tool++;
            break;
        }
        case EventType::RecipeQual:
            // >>> PLACEHOLDER: mutate the tool's qualified-recipe list.
            //     Needs a virtual add_qual/remove_qual on MachineConfiguration.
            //     Until then, qualification changes require a config reload.
            break;
        default:
            stats_.malformed++;
            return;
        }
        stats_.applied++;
    }

    // ---- planner thread ---------------------------------------------------
    std::vector<Lot> ready_lots() const {
        std::shared_lock lk(mu_);
        std::vector<Lot> v;
        v.reserve(ready_.size());
        for (const auto& [id, l] : ready_) v.push_back(l);
        return v;
    }

    // Held for the duration of a planning pass so evaluate() sees a consistent
    // snapshot. Planning is ~hundreds of ms; ingestion blocks briefly.
    // >>> PLACEHOLDER: for a real fab, deep-copy the tool states into a
    //     snapshot object instead, so ingestion never blocks on the solver.
    std::shared_lock<std::shared_mutex> read_lock() const {
        return std::shared_lock<std::shared_mutex>(mu_);
    }

    IngestStats stats() const {
        std::shared_lock lk(mu_);
        return stats_;
    }

    std::size_t ready_count() const {
        std::shared_lock lk(mu_);
        return ready_.size();
    }

    std::size_t in_flight_count() const {
        std::shared_lock lk(mu_);
        return in_flight_.size();
    }

    bool is_ready(const std::string& lot_id) const {
        std::shared_lock lk(mu_);
        return ready_.count(lot_id) > 0;
    }

private:
    ToolRegistry&                              reg_;
    mutable std::shared_mutex                  mu_;
    std::unordered_map<std::string, Lot>       ready_;
    std::unordered_map<std::string, ToolId>    in_flight_;
    std::unordered_map<std::string, uint64_t>  last_seq_;
    IngestStats                                stats_;
};

} // namespace fab
