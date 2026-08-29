#pragma once
// fab/bootstrap.hpp — cold start.
//
// THE PROBLEM you spotted: on startup the dispatcher knows nothing. Tools have
// unknown state, WIP is invisible, and `auto.offset.reset=latest` means we skip
// everything that happened before we booted. If we start dispatching in that
// condition we will confidently route lots to tools that are down and admit
// lots to tools that are already full.
//
// A dispatcher that is WRONG is worse than one that is SLOW. So we gate.
//
// Four-phase bootstrap:
//
//   1. TOOL STATE   read the compacted topic `fab.tool.state` to its end.
//                   Compaction means this is O(tools), not O(history) — a
//                   restart costs seconds, not a replay of a week.
//   2. WIP SNAPSHOT pull open lots from the MES (request/response, not the
//                   stream). The event log tells you about CHANGES; only a
//                   snapshot tells you the current set.
//   3. RECONCILE    replay live events buffered during 1-2, applying only
//                   those newer than the snapshot watermark.
//   4. READY        begin dispatching.
//
// Until phase 4 the fast path returns Hold for every request. Vehicles wait a
// few seconds at startup. That is the correct trade.

#include "fab/events.hpp"
#include "fab/machine_config.hpp"
#include "fab/state.hpp"

#include <atomic>
#include <chrono>
#include <string>
#include <vector>

namespace fab {

enum class BootPhase { ToolState, WipSnapshot, Reconcile, Ready, Failed };

inline const char* to_string(BootPhase p) {
    switch (p) {
        case BootPhase::ToolState:   return "TOOL_STATE";
        case BootPhase::WipSnapshot: return "WIP_SNAPSHOT";
        case BootPhase::Reconcile:   return "RECONCILE";
        case BootPhase::Ready:       return "READY";
        case BootPhase::Failed:      return "FAILED";
    }
    return "?";
}

struct BootConfig {
    double tool_state_timeout_s = 30.0;
    double wip_snapshot_timeout_s = 60.0;
    double max_total_s = 120.0;
    // Refuse to start if fewer than this fraction of configured tools reported
    // a state. Dispatching against a mostly-unknown fab is not safe.
    double min_tool_coverage = 0.80;
};

struct BootReport {
    BootPhase phase = BootPhase::ToolState;
    int    tools_configured = 0;
    int    tools_known      = 0;
    int    wip_lots         = 0;
    int    buffered_replayed = 0;
    double elapsed_s        = 0.0;
    std::string detail;

    double coverage() const {
        return tools_configured ? double(tools_known) / tools_configured : 0.0;
    }
};

class Bootstrapper {
public:
    Bootstrapper(ToolRegistry& reg, FabState& state, BootConfig cfg)
        : reg_(reg), state_(state), cfg_(cfg) {}

    BootPhase phase() const { return phase_.load(); }
    bool ready() const { return phase_.load() == BootPhase::Ready; }
    const BootReport& report() const { return report_; }

    // Events arriving during phases 1-3 are buffered, not applied, so a live
    // update cannot overwrite a snapshot that is still loading.
    void buffer(const Envelope& e) { buffered_.push_back(e); }

    // ---- phase 1 ---------------------------------------------------------
    // Feed each record from the compacted fab.tool.state topic.
    void apply_tool_state(const Envelope& e) {
        if (auto* t = reg_.find(e.tool_id)) {
            t->set_online(e.online);
            known_tools_.insert(e.tool_id);
        }
    }

    bool finish_tool_state() {
        report_.tools_configured = static_cast<int>(reg_.all().size());
        report_.tools_known      = static_cast<int>(known_tools_.size());
        if (report_.coverage() < cfg_.min_tool_coverage) {
            // Name the tools we never heard from. "Coverage 62%" is useless
            // at 3am; "we never heard from LITHO_03, FURN_02" is actionable.
            std::string missing;
            for (auto* t : reg_.all())
                if (!known_tools_.count(t->id()))
                    missing += (missing.empty() ? "" : ", ") + t->id();
            report_.detail = "insufficient tool coverage; no state for: " + missing;
            phase_ = BootPhase::Failed;
            return false;
        }
        phase_ = BootPhase::WipSnapshot;
        return true;
    }

    // ---- phase 2 ---------------------------------------------------------
    // >>> PLACEHOLDER: the real snapshot is an MES query (SQL, or a REST call
    //     to the lot-tracking service) returning every open lot with its
    //     current step and Q-time clock. The event stream CANNOT supply this:
    //     it carries changes, not current membership.
    void apply_wip_snapshot(const std::vector<Lot>& lots, uint64_t watermark_seq) {
        for (const auto& l : lots) {
            Envelope e;
            e.type          = EventType::LotReady;
            e.source        = "mes-snapshot";
            e.lot_id        = l.lot_id;
            e.product_id    = l.product_id;
            e.recipe        = l.recipe;
            e.reticle       = l.reticle;
            e.wafer_count   = l.wafer_count;
            e.priority      = l.priority;
            e.qtime_slack_s = l.qtime_slack_s;
            state_.apply(e);
        }
        report_.wip_lots = static_cast<int>(lots.size());
        watermark_ = watermark_seq;
        phase_ = BootPhase::Reconcile;
    }

    // ---- phase 3 ---------------------------------------------------------
    void reconcile() {
        for (const auto& e : buffered_) {
            // Only events newer than the snapshot. Replaying older ones would
            // resurrect lots the snapshot already knows are gone.
            if (e.source == "mes" && e.seq <= watermark_) continue;
            state_.apply(e);
            report_.buffered_replayed++;
        }
        buffered_.clear();
        phase_ = BootPhase::Ready;
        report_.phase = BootPhase::Ready;
    }

    // Convenience for the demo path, where there is no MES to query.
    void fast_path_ready(const std::string& why) {
        report_.detail = why;
        report_.tools_configured = static_cast<int>(reg_.all().size());
        report_.tools_known = report_.tools_configured;
        phase_ = BootPhase::Ready;
        report_.phase = BootPhase::Ready;
    }

private:
    ToolRegistry&          reg_;
    FabState&              state_;
    BootConfig             cfg_;
    std::atomic<BootPhase> phase_{BootPhase::ToolState};
    BootReport             report_;
    std::vector<Envelope>  buffered_;
    std::set<ToolId>       known_tools_;
    uint64_t               watermark_ = 0;
};

} // namespace fab
