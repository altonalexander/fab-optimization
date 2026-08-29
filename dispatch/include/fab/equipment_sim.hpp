#pragma once
// fab/equipment_sim.hpp — closes the feedback loop.
//
// Stands in for the physical tools + SECS/GEM. Consumes dispatch decisions,
// emits LOT_STARTED immediately, then LOT_COMPLETE after the processing time,
// which frees the tool's capacity in FabState.
//
// Without this the ready pool grows without bound and the planner saturates —
// which is exactly what the first end-to-end run showed.
//
// >>> PLACEHOLDER: replace with the real SECS/GEM equipment interface. Real
//     tools also emit process-abort, out-of-spec, and E10 state changes; a lot
//     that goes in does not always come out clean. Add those before go-live.

#include "fab/events.hpp"
#include "fab/transport.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <mutex>
#include <string>
#include <vector>

namespace fab {

// A dispatch decision as it goes on the wire, so equipment can act on it.
struct DecisionMsg {
    std::string lot_id;
    std::string tool_id;
    double      process_s = 0.0;

    std::string encode() const {
        return "lot=" + lot_id + ";tool=" + tool_id +
               ";proc=" + std::to_string(process_s);
    }

    static DecisionMsg decode(const std::string& s) {
        DecisionMsg d;
        std::size_t p = 0;
        while (p < s.size()) {
            auto semi = s.find(';', p);
            auto tok  = s.substr(p, semi == std::string::npos ? std::string::npos : semi - p);
            auto eq   = tok.find('=');
            if (eq != std::string::npos) {
                auto k = tok.substr(0, eq), v = tok.substr(eq + 1);
                if      (k == "lot")  d.lot_id  = v;
                else if (k == "tool") d.tool_id = v;
                else if (k == "proc") d.process_s = std::atof(v.c_str());
            }
            if (semi == std::string::npos) break;
            p = semi + 1;
        }
        return d;
    }
};

class EquipmentSimulator {
public:
    // time_scale compresses fab time: 0.001 means a 7200s furnace bake
    // completes in 7.2s of wall clock, so a demo run exercises the full cycle.
    EquipmentSimulator(Consumer& decisions, Producer& events, double time_scale)
        : in_(decisions), out_(events), scale_(time_scale) {}

    void run(std::atomic<bool>& shutdown) {
        std::string topic, payload;
        while (!shutdown.load()) {
            // 1. Pick up new dispatch decisions and start those lots.
            if (in_.poll(topic, payload, 20)) {
                auto d = DecisionMsg::decode(payload);
                if (!d.lot_id.empty() && !d.tool_id.empty()) start(d);
            }
            // 2. Complete anything whose processing time has elapsed.
            complete_due();
        }
        // Drain: let in-flight lots finish so the final counts balance.
        for (int i = 0; i < 200 && !pending_.empty(); ++i) {
            complete_due();
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    }

    uint64_t started()   const { return started_.load(); }
    uint64_t completed() const { return completed_.load(); }
    std::size_t in_flight() {
        std::lock_guard<std::mutex> lk(m_);
        return pending_.size();
    }

private:
    struct Pending {
        std::string lot_id, tool_id;
        std::chrono::steady_clock::time_point done_at;
    };

    void start(const DecisionMsg& d) {
        Envelope e;
        e.type    = EventType::LotStarted;
        e.source  = "equipment";
        e.seq     = ++seq_;
        e.ts_ns   = now_ns();
        e.lot_id  = d.lot_id;
        e.tool_id = d.tool_id;
        out_.send(topics::kLotEvents, e.lot_id, e.encode());
        started_.fetch_add(1);

        const auto dur = std::chrono::milliseconds(
            std::max<long long>(1, (long long)(d.process_s * scale_ * 1000)));
        std::lock_guard<std::mutex> lk(m_);
        pending_.push_back({d.lot_id, d.tool_id,
                            std::chrono::steady_clock::now() + dur});
    }

    void complete_due() {
        const auto now = std::chrono::steady_clock::now();
        std::vector<Pending> due;
        {
            std::lock_guard<std::mutex> lk(m_);
            auto it = std::partition(pending_.begin(), pending_.end(),
                                     [&](const Pending& p) { return p.done_at > now; });
            due.assign(it, pending_.end());
            pending_.erase(it, pending_.end());
        }
        for (const auto& p : due) {
            Envelope e;
            e.type    = EventType::LotComplete;
            e.source  = "equipment";
            e.seq     = ++seq_;
            e.ts_ns   = now_ns();
            e.lot_id  = p.lot_id;
            e.tool_id = p.tool_id;
            out_.send(topics::kLotEvents, e.lot_id, e.encode());
            completed_.fetch_add(1);
        }
    }

    static uint64_t now_ns() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    }

    Consumer&             in_;
    Producer&             out_;
    double                scale_;
    std::mutex            m_;
    std::vector<Pending>  pending_;
    std::atomic<uint64_t> started_{0}, completed_{0};
    uint64_t              seq_ = 0;
};

} // namespace fab
