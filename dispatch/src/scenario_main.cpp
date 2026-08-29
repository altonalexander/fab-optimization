// scenario_main.cpp — offline what-if runner.
//
// Reads a scenario on stdin as JSON, runs the REAL planner against a cloned
// fab state, writes the resulting slate to stdout as JSON.
//
// This exists so the API never reimplements dispatch logic in Python. A
// scenario answer that diverges from what the dispatcher would actually do is
// worse than no answer at all, so the same MachineConfiguration::evaluate()
// and the same SolverBackend serve both paths.
//
// Usage:  echo '{"lots":[...],"tool_overrides":[...]}' | ./fab_scenario
//
// ZONE: runs in zone 2 (data), invoked by the API. Never touches zone 1.

#include "fab/json.hpp"
#include "fab/planner.hpp"
#include "fab/tool_factory.hpp"

#include <iostream>
#include <sstream>

using namespace fab;

namespace {

std::string esc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') o += '\\';
        o += c;
    }
    return o;
}

std::string arg(int c, char** v, const std::string& k, const std::string& d) {
    const std::string pfx = k + "=";
    for (int i = 1; i < c; ++i) {
        std::string a = v[i];
        if (a.rfind(pfx, 0) == 0) return a.substr(pfx.size());
        if (a == k && i + 1 < c)  return v[i + 1];
    }
    return d;
}

} // namespace

int main(int argc, char** argv) {
    const std::string cfg = arg(argc, argv, "--config", "config/fab_tools.json");
    const std::string sol = arg(argc, argv, "--solver", "cpsat");

    std::stringstream in;
    in << std::cin.rdbuf();

    json::Value scenario;
    try {
        scenario = json::parse(in.str());
    } catch (const std::exception& e) {
        std::cout << "{\"error\":\"" << esc(e.what()) << "\"}\n";
        return 1;
    }

    // Fresh registry per scenario. This IS the clone: the live dispatcher's
    // state is never touched, only the same config re-instantiated.
    ToolRegistry reg;
    ReticlePool  pool;
    auto issues = ToolFactory::instance().load(cfg, reg, pool);
    for (const auto& i : issues)
        if (i.severity == ConfigIssue::Severity::Error) {
            std::cout << "{\"error\":\"config: " << esc(i.message) << "\"}\n";
            return 1;
        }

    // Apply the what-if: take tools down, take chambers down.
    std::vector<std::string> downed;
    for (const auto& o : scenario["tool_overrides"].as_array()) {
        const std::string id = o["tool_id"].as_string();
        auto* t = reg.find(id);
        if (!t) continue;
        if (o.contains("online") && !o["online"].as_bool(true)) {
            t->set_online(false);
            downed.push_back(id);
        }
        const std::string ch = o["chamber"].as_string();
        if (!ch.empty())
            if (auto* c = dynamic_cast<ClusterTool*>(t))
                c->set_chamber_online(ch, o["online"].as_bool(true));
    }

    // Load the lot set.
    std::vector<Lot> lots;
    for (const auto& l : scenario["lots"].as_array()) {
        Lot lot;
        lot.lot_id        = l["lot_id"].as_string();
        lot.product_id    = l["product_id"].as_string();
        lot.recipe        = l["recipe"].as_string();
        lot.reticle       = l["reticle"].as_string();
        lot.wafer_count   = l["wafer_count"].as_int(25);
        lot.priority      = l["priority"].as_double(1.0);
        lot.qtime_slack_s = l["qtime_slack_s"].as_double(3600.0);
        lots.push_back(lot);
    }
    if (lots.empty()) {
        std::cout << "{\"error\":\"no lots in scenario\"}\n";
        return 1;
    }

    PlannerConfig pcfg;
    pcfg.solve_budget_s = scenario["solve_budget_s"].as_double(5.0);
    Planner planner(make_solver(sol));
    auto r = planner.plan(reg, lots, 1, pcfg);

    // Emit the slate plus enough diagnostics to explain the outcome. The
    // unassigned list with reasons is the useful part: "why didn't this run"
    // is the question people actually ask a what-if tool.
    std::ostringstream o;
    o << "{\"solver\":\"" << planner.solver_name() << "\","
      << "\"solver_linked\":" << (planner.solver_available() ? "true" : "false") << ","
      << "\"ready\":" << r.ready << ",\"assigned\":" << r.assigned
      << ",\"variables\":" << r.variables
      << ",\"objective\":" << r.objective
      << ",\"solve_ms\":" << r.solve_time_s * 1000.0
      << ",\"tools_down\":[";
    for (std::size_t i = 0; i < downed.size(); ++i)
        o << (i ? "," : "") << "\"" << esc(downed[i]) << "\"";
    o << "],\"assignments\":[";
    bool first = true;
    for (const auto& [lot_id, tok] : r.slate->tokens) {
        o << (first ? "" : ",") << "{\"lot_id\":\"" << esc(lot_id)
          << "\",\"tool\":\"" << esc(tok.primary)
          << "\",\"alternate\":\"" << esc(tok.alternate)
          << "\",\"process_s\":" << tok.expected_process_s << "}";
        first = false;
    }
    o << "],\"unassigned\":[";
    first = true;
    for (const auto& lot : lots) {
        if (r.slate->tokens.count(lot.lot_id)) continue;
        // Re-evaluate to report WHY, per tool.
        std::string reason = "no eligible tool";
        for (auto* t : reg.all()) {
            auto e = t->evaluate(lot);
            if (e) { reason = "capacity or batch minimum"; break; }
        }
        o << (first ? "" : ",") << "{\"lot_id\":\"" << esc(lot.lot_id)
          << "\",\"recipe\":\"" << esc(lot.recipe)
          << "\",\"reason\":\"" << esc(reason) << "\"}";
        first = false;
    }
    o << "],\"tools\":[";
    first = true;
    for (auto* t : reg.all()) {
        o << (first ? "" : ",") << "{\"id\":\"" << esc(t->id())
          << "\",\"kind\":\"" << esc(std::string(t->kind()))
          << "\",\"area\":\"" << esc(t->area())
          << "\",\"online\":" << (t->online() ? "true" : "false")
          << ",\"free\":" << t->free_capacity() << "}";
        first = false;
    }
    o << "]}";
    std::cout << o.str() << "\n";
    return 0;
}
