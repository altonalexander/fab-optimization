// e2e_main.cpp — the whole pipeline in one process, closed loop.
//
//   [producer]    MES/AMHS stand-in -> LOT_READY, TOOL_STATUS
//        |
//   [ingest]      SINGLE WRITER -> FabState
//        |
//   [planner]     every N sec -> AssignmentModel -> SolverBackend -> Slate
//        |
//   [dispatch]    move request -> Slate lookup (<1ms) -> decision topic
//        |
//   [equipment]   LOT_STARTED -> (process time) -> LOT_COMPLETE
//        |
//        +--------> back to ingest: frees tool capacity
//
// Usage:
//   ./fabdisp [--config config/fab_tools.json] [--solver greedy|cpsat|gurobi|highs]
//             [--brokers host:9092]

#include "fab/dispatcher.hpp"
#include "fab/equipment_sim.hpp"
#include "fab/planner.hpp"
#include "fab/producer_sim.hpp"
#include "fab/state.hpp"
#include "fab/tool_factory.hpp"
#include "fab/transport.hpp"

#include <iomanip>
#include <iostream>
#include <set>
#include <thread>

using namespace fab;

namespace {
std::atomic<bool> g_shutdown{false};
void header(const char* s) { std::cout << "\n=== " << s << " ===\n"; }
std::string arg(int c, char** v, const std::string& k, const std::string& d) {
    for (int i = 1; i < c - 1; ++i) if (k == v[i]) return v[i + 1];
    return d;
}
} // namespace

int main(int argc, char** argv) {
    const std::string cfg_path = arg(argc, argv, "--config", "config/fab_tools.json");
    const std::string solver   = arg(argc, argv, "--solver", "cpsat");
    const std::string brokers  = arg(argc, argv, "--brokers", "");

    // ---------------------------------------------------------------- config
    header("CONFIG");
    ToolRegistry registry;
    ReticlePool  reticles;
    auto issues = ToolFactory::instance().load(cfg_path, registry, reticles);
    bool fatal = false;
    for (const auto& i : issues) {
        const bool err = i.severity == ConfigIssue::Severity::Error;
        fatal |= err;
        std::cout << (err ? "  ERROR   " : "  warning ") << i.message << "\n";
    }
    if (fatal) {
        std::cerr << "config invalid; refusing to dispatch\n";
        return 1;
    }
    std::cout << "loaded " << registry.all().size() << " tools from " << cfg_path << "\n";

    // ------------------------------------------------------------- transport
    InMemoryBus event_bus;      // fab -> dispatcher
    InMemoryBus decision_bus;   // dispatcher -> equipment
    std::unique_ptr<Producer> ev_prod, dec_prod;
    std::unique_ptr<Consumer> ev_cons, dec_cons;

#ifdef FAB_HAVE_RDKAFKA
    if (!brokers.empty()) {
        ev_prod  = std::make_unique<KafkaProducer>(brokers);
        dec_prod = std::make_unique<KafkaProducer>(brokers);
        ev_cons  = std::make_unique<KafkaConsumer>(brokers, "fab-dispatcher",
                     std::vector<std::string>{topics::kLotEvents, topics::kToolEvents});
        dec_cons = std::make_unique<KafkaConsumer>(brokers, "fab-equipment",
                     std::vector<std::string>{topics::kDecisions});
        std::cout << "transport: kafka @ " << brokers << "\n";
    }
#endif
    if (!ev_prod) {
        ev_prod  = std::make_unique<InMemoryProducer>(event_bus);
        dec_prod = std::make_unique<InMemoryProducer>(decision_bus);
        ev_cons  = std::make_unique<InMemoryConsumer>(event_bus);
        dec_cons = std::make_unique<InMemoryConsumer>(decision_bus);
        std::cout << "transport: in-memory bus"
                  << (brokers.empty() ? "" : "  (rebuild -DFAB_HAVE_RDKAFKA for kafka)")
                  << "\n";
    }

    // ----------------------------------------------------------------- wiring
    FabState        state(registry);
    SlatePublisher  publisher;
    DispatchMetrics metrics;
    Dispatcher      dispatcher(publisher, metrics);

    PlannerConfig pcfg;
    pcfg.cycle_seconds  = 0.4;    // compressed for the demo; 10-30s in prod
    pcfg.solve_budget_s = 0.2;
    Planner planner(make_solver(solver));

    std::cout << "solver: " << planner.solver_name()
              << (planner.solver_available() ? " (linked)"
                                             : " (NOT linked -> greedy fallback)")
              << "\n";

    // -------------------------------------------------- thread 1: fab events
    SimConfig scfg;
    scfg.burst_count       = 14;
    scfg.lots_per_burst    = 8;
    scfg.burst_interval_ms = 200;
    FabEventProducer sim(*ev_prod, scfg);
    std::thread t_producer([&] { sim.run(); });

    // ------------------------------------------- thread 2: ingest (1 writer)
    std::thread t_ingest([&] {
        std::string topic, payload;
        while (!g_shutdown.load()) {
            if (!ev_cons->poll(topic, payload, 50)) continue;
            if (auto env = Envelope::decode(payload)) state.apply(*env);
            // >>> PLACEHOLDER (kafka): commit offset HERE, after apply().
        }
    });

    // ------------------------------------------------- thread 3: equipment
    EquipmentSimulator equipment(*dec_cons, *ev_prod, /*time_scale=*/0.0004);
    std::thread t_equipment([&] { equipment.run(g_shutdown); });

    // --------------------------------------------------- thread 4: planner
    uint64_t cycle = 0, stale_cycles = 0;
    std::thread t_planner([&] {
        while (!g_shutdown.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(
                (int)(pcfg.cycle_seconds * 1000)));
            std::vector<Lot> lots;
            { auto lk = state.read_lock(); lots = state.ready_lots(); }
            if (lots.empty()) continue;

            auto r = planner.plan(registry, lots, ++cycle, pcfg);
            if (r.usable()) {
                publisher.publish(r.slate);
                stale_cycles = 0;
                std::cout << "  [plan] c=" << cycle
                          << " ready=" << r.ready
                          << " assigned=" << r.assigned
                          << " vars=" << r.variables
                          << " obj=" << std::fixed << std::setprecision(0) << r.objective
                          << " solve=" << std::setprecision(2) << r.solve_time_s * 1000
                          << "ms\n";
            } else {
                stale_cycles++;
                std::cout << "  [plan] c=" << cycle << " no incumbent ("
                          << r.detail << "), serving slate age "
                          << std::setprecision(1) << publisher.get()->age_s() << "s\n";
                if (stale_cycles >= (uint64_t)pcfg.stale_cycles_alarm)
                    std::cout << "  [ALARM] " << stale_cycles
                              << " cycles without a usable plan\n";
            }
        }
    });

    // ------------------------------- thread 5: dispatch loop (the fast path)
    // Each pass: take the current slate, issue moves for lots still ready.
    std::thread t_dispatch([&] {
        uint64_t seen_cycle = 0;
        std::set<std::string> issued;   // de-dupe within a slate cycle
        while (!g_shutdown.load()) {
            auto slate = publisher.get();
            if (slate->cycle_id != seen_cycle) {   // new plan: reset de-dupe
                seen_cycle = slate->cycle_id;
                issued.clear();
            }
            for (const auto& [lot_id, tok] : slate->tokens) {
                if (!state.is_ready(lot_id)) continue;   // already started
                if (!issued.insert(lot_id).second) continue;  // already moved
                auto d = dispatcher.decide(lot_id);
                if (d.outcome == DispatchOutcome::Primary ||
                    d.outcome == DispatchOutcome::Alternate) {
                    DecisionMsg m{lot_id, d.tool, tok.expected_process_s};
                    dec_prod->send(topics::kDecisions, lot_id, m.encode());
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
    });

    // ------------------------------------------------------- latency profile
    std::this_thread::sleep_for(std::chrono::milliseconds(1200));
    header("FAST PATH: 40-vehicle burst + sustained load");
    {
        auto slate = publisher.get();
        std::vector<std::string> ids;
        for (const auto& [k, v] : slate->tokens) ids.push_back(k);
        if (!ids.empty()) {
            const auto t0 = std::chrono::steady_clock::now();
            for (int i = 0; i < 40; ++i) dispatcher.decide(ids[i % ids.size()]);
            const double us = std::chrono::duration<double, std::micro>(
                std::chrono::steady_clock::now() - t0).count();
            std::cout << "40-vehicle burst: " << std::fixed << std::setprecision(1)
                      << us << " us total ("  << us / 40 << " us/decision)\n";
            for (int i = 0; i < 200000; ++i) dispatcher.decide(ids[i % ids.size()]);
        }
        std::cout << "decisions " << metrics.latency.count()
                  << "  p50 "  << metrics.latency.percentile_ns(0.50) << "ns"
                  << "  p99 "  << metrics.latency.percentile_ns(0.99) << "ns"
                  << "  p999 " << metrics.latency.percentile_ns(0.999) << "ns\n";
    }

    // ------------------------------------------------------------- shut down
    t_producer.join();
    std::this_thread::sleep_for(std::chrono::milliseconds(2500));  // let WIP drain
    g_shutdown = true;
    event_bus.close();
    decision_bus.close();
    t_ingest.join(); t_planner.join(); t_dispatch.join(); t_equipment.join();

    // ---------------------------------------------------------------- report
    const auto st = state.stats();
    header("PIPELINE");
    std::cout << "lots created        " << sim.emitted()      << " events emitted\n"
              << "events applied      " << st.applied         << "\n"
              << "  malformed         " << st.malformed       << "\n"
              << "  unknown tool      " << st.unknown_tool    << "\n"
              << "  seq gaps          " << st.seq_gaps        << "\n"
              << "  orphan completes  " << st.orphan_complete  << "\n";

    header("CLOSED LOOP");
    std::cout << "lots started        " << st.started         << "\n"
              << "lots completed      " << st.completed       << "\n"
              << "start rejected      " << st.start_rejected
              << "   (tool filled between plan and arrival)\n"
              << "still ready         " << state.ready_count()<< "\n"
              << "still in flight     " << state.in_flight_count() << "\n"
              << "slates published    " << publisher.publish_count() << "\n";

    header("DISPATCH OUTCOMES");
    std::cout << "primary             " << metrics.primary.load()   << "\n"
              << "alternate failover  " << metrics.alternate.load() << "\n"
              << "no token            " << metrics.no_token.load()  << "\n"
              << "both down (held)    " << metrics.both_down.load() << "\n";

    std::cout << "\nremaining work: grep -rn PLACEHOLDER include/ src/\n";
    return 0;
}
