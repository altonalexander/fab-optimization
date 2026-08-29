#pragma once
// fab/producer_sim.hpp — the data producer.
//
// Stands in for the real sources: MES lot tracking, SECS/GEM equipment
// messages, and the AMHS controller. Emits the same Envelope schema onto the
// same topics, so swapping in real feeds means deleting this file, not
// rewriting the consumer.
//
// Rates are calibrated to a representative 300mm fab: ~53k moves/week
// ~= 0.09/s average, arriving in bursts of 30-40.
//
// >>> PLACEHOLDER: real producers also emit lot hold/release, rework, scrap,
//     and E10 state transitions. Add those event types before go-live.

#include "fab/events.hpp"
#include "fab/transport.hpp"

#include <atomic>
#include <chrono>
#include <random>
#include <string>
#include <thread>
#include <vector>

namespace fab {

struct SimConfig {
    int    lots_per_burst     = 12;
    int    burst_count        = 8;
    int    burst_interval_ms  = 250;
    double tool_down_chance   = 0.15;   // per burst
    unsigned seed             = 42;     // deterministic: replayable runs
};

class FabEventProducer {
public:
    FabEventProducer(Producer& p, SimConfig cfg)
        : p_(p), cfg_(cfg), rng_(cfg.seed) {}

    void run() {
        const std::vector<std::pair<std::string, std::string>> mix = {
            // {product, recipe}
            {"AUTOMOTIVE_MCU_01",  "M1_EXPOSE"},
            {"AUTOMOTIVE_MCU_01",  "POLY_ETCH"},
            {"AUTOMOTIVE_MCU_01",  "GATE_OX"},
            {"AUTOMOTIVE_MCU_01",  "SORT_HOT"},
            {"COMMODITY_LOGIC_09", "GATE_OX"},
            {"COMMODITY_LOGIC_09", "NITRIDE"},
            {"COMMODITY_LOGIC_09", "CD_MEASURE"},
        };
        const std::vector<std::string> tools = {
            "ETCH_11", "ETCH_12", "FURN_02", "CVD_07",
            "LITHO_03", "LITHO_04", "CD_SEM_01", "PROBE_21"};

        std::uniform_int_distribution<int>    pick_mix(0, (int)mix.size() - 1);
        std::uniform_int_distribution<int>    pick_tool(0, (int)tools.size() - 1);
        std::uniform_real_distribution<double> unit(0.0, 1.0);
        std::uniform_real_distribution<double> slack(300.0, 9000.0);
        std::uniform_real_distribution<double> prio(0.8, 5.0);

        for (int b = 0; b < cfg_.burst_count && !stop_; ++b) {
            // --- burst of ready lots (this is the 30-40 vehicle arrival) ---
            for (int i = 0; i < cfg_.lots_per_burst; ++i) {
                const auto& [prod, recipe] = mix[pick_mix(rng_)];
                Envelope e;
                e.type          = EventType::LotReady;
                e.source        = "mes";
                e.seq           = ++seq_;
                e.ts_ns         = now_ns();
                e.lot_id        = "LOT_" + std::to_string(1000 + lot_counter_++);
                e.product_id    = prod;
                e.recipe        = recipe;
                e.reticle       = (recipe == "M1_EXPOSE") ? "RET_M1_77" : "";
                e.wafer_count   = 25;
                e.priority      = prio(rng_);
                e.qtime_slack_s = slack(rng_);
                p_.send(topics::kLotEvents, e.lot_id, e.encode());
                emitted_++;
            }

            // --- occasional equipment event ---
            if (unit(rng_) < cfg_.tool_down_chance) {
                Envelope e;
                e.type   = EventType::ToolStatus;
                e.source = "mes";
                e.seq    = ++seq_;
                e.ts_ns  = now_ns();
                e.tool_id = tools[pick_tool(rng_)];
                e.online  = false;
                p_.send(topics::kToolEvents, e.tool_id, e.encode());
                emitted_++;
                down_.push_back(e.tool_id);
            }

            // --- bring something back up ---
            if (!down_.empty() && unit(rng_) < 0.5) {
                Envelope e;
                e.type    = EventType::ToolStatus;
                e.source  = "mes";
                e.seq     = ++seq_;
                e.ts_ns   = now_ns();
                e.tool_id = down_.front();
                e.online  = true;
                down_.erase(down_.begin());
                p_.send(topics::kToolEvents, e.tool_id, e.encode());
                emitted_++;
            }

            std::this_thread::sleep_for(
                std::chrono::milliseconds(cfg_.burst_interval_ms));
        }
        p_.flush();
    }

    void stop() { stop_ = true; }
    uint64_t emitted() const { return emitted_; }

private:
    static uint64_t now_ns() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    }

    Producer&                p_;
    SimConfig                cfg_;
    std::mt19937             rng_;
    std::atomic<bool>        stop_{false};
    uint64_t                 seq_ = 0;
    uint64_t                 emitted_ = 0;
    int                      lot_counter_ = 0;
    std::vector<std::string> down_;
};

} // namespace fab
