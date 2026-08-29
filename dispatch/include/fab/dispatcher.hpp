#pragma once
// fab/dispatcher.hpp — the sub-millisecond path.
//
// Everything here is a hash lookup and a branch. No locks, no allocation on
// the happy path, no solver call, no I/O. If you are ever tempted to add a
// database read or a log flush to this function, don't.

#include "fab/slate.hpp"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <optional>
#include <vector>

namespace fab {

enum class DispatchOutcome {
    Primary,        // took the planned tool
    Alternate,      // primary down, used pre-computed backup
    NoToken,        // lot not in the current slate (arrived after planning)
    BothDown,       // both planned tools unavailable -> hold the vehicle
};

struct DispatchDecision {
    DispatchOutcome outcome;
    ToolId          tool;
    uint64_t        cycle_id;
    double          slate_age_s;
};

// Lock-free latency histogram: fixed log-ish buckets in nanoseconds.
class LatencyHistogram {
public:
    void record(uint64_t ns) {
        std::size_t b = 0;
        uint64_t edge = 100;                       // 100ns first bucket
        while (b + 1 < kBuckets && ns > edge) { edge *= 2; ++b; }
        buckets_[b].fetch_add(1, std::memory_order_relaxed);
        count_.fetch_add(1, std::memory_order_relaxed);
    }

    uint64_t percentile_ns(double p) const {
        const uint64_t total = count_.load(std::memory_order_relaxed);
        if (!total) return 0;
        const uint64_t target = static_cast<uint64_t>(total * p);
        uint64_t seen = 0, edge = 100;
        for (std::size_t b = 0; b < kBuckets; ++b) {
            seen += buckets_[b].load(std::memory_order_relaxed);
            if (seen >= target) return edge;
            edge *= 2;
        }
        return edge;
    }

    uint64_t count() const { return count_.load(std::memory_order_relaxed); }

private:
    static constexpr std::size_t kBuckets = 24;
    mutable std::atomic<uint64_t> buckets_[kBuckets]{};
    std::atomic<uint64_t> count_{0};
};

struct DispatchMetrics {
    std::atomic<uint64_t> primary{0}, alternate{0}, no_token{0}, both_down{0};
    LatencyHistogram      latency;
};

class Dispatcher {
public:
    Dispatcher(SlatePublisher& pub, DispatchMetrics& m) : pub_(pub), m_(m) {}

    // THE HOT FUNCTION. Called once per OHT move request.
    DispatchDecision decide(const std::string& lot_id) {
        const auto t0 = std::chrono::steady_clock::now();

        SlatePtr s = pub_.get();                       // 1 atomic load
        DispatchDecision d{DispatchOutcome::NoToken, {}, s->cycle_id, s->age_s()};

        auto it = s->tokens.find(lot_id);
        if (it == s->tokens.end()) {
            m_.no_token.fetch_add(1, std::memory_order_relaxed);
        } else {
            const RouteToken& tok = it->second;
            if (tool_up(*s, tok.primary)) {
                d.outcome = DispatchOutcome::Primary;
                d.tool    = tok.primary;
                m_.primary.fetch_add(1, std::memory_order_relaxed);
            } else if (tool_up(*s, tok.alternate)) {
                d.outcome = DispatchOutcome::Alternate;
                d.tool    = tok.alternate;
                m_.alternate.fetch_add(1, std::memory_order_relaxed);
            } else {
                d.outcome = DispatchOutcome::BothDown;
                m_.both_down.fetch_add(1, std::memory_order_relaxed);
                // >>> PLACEHOLDER: escalate to the AMHS hold queue and raise
                //     an alarm. A held vehicle blocks track; it is not a
                //     silent condition.
            }
        }

        m_.latency.record(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - t0).count());
        return d;
    }

private:
    static bool tool_up(const Slate& s, const ToolId& id) {
        auto it = s.tools.find(id);
        return it != s.tools.end() && it->second.online && it->second.free_slots > 0;
    }

    SlatePublisher&  pub_;
    DispatchMetrics& m_;
};

} // namespace fab
