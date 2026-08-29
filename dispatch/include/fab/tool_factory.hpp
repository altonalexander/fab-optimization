#pragma once
// fab/tool_factory.hpp — build the tool registry from a JSON tool master.
//
// Adding a new machine configuration is now two steps and touches nothing else:
//   1. write the MachineConfiguration subclass
//   2. ToolFactory::register_kind("MY_KIND", [](const json::Value& j){...});
// The dispatcher, planner, and solver are all unaware.

#include "fab/json.hpp"
#include "fab/machine_config.hpp"

#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <set>
#include <sstream>

namespace fab {

struct ConfigIssue {
    enum class Severity { Warning, Error } severity;
    std::string message;
};

class ToolFactory {
public:
    using Builder = std::function<std::unique_ptr<MachineConfiguration>(
        const json::Value&, ReticlePool&)>;

    static ToolFactory& instance() {
        static ToolFactory f;
        return f;
    }

    void register_kind(const std::string& kind, Builder b) {
        builders_[kind] = std::move(b);
    }

    bool knows(const std::string& kind) const { return builders_.count(kind) > 0; }

    // Load a tool master into the registry. Returns issues; caller decides
    // whether to proceed. An Error means the fab config is unsafe to dispatch.
    std::vector<ConfigIssue> load(const std::string& path,
                                  ToolRegistry& reg,
                                  ReticlePool& pool) const {
        std::ifstream f(path);
        if (!f) return {{ConfigIssue::Severity::Error, "cannot open " + path}};
        std::stringstream ss;
        ss << f.rdbuf();
        return load_text(ss.str(), reg, pool);
    }

    std::vector<ConfigIssue> load_text(const std::string& text,
                                       ToolRegistry& reg,
                                       ReticlePool& pool) const {
        std::vector<ConfigIssue> issues;
        json::Value root;
        try {
            root = json::parse(text);
        } catch (const std::exception& e) {
            return {{ConfigIssue::Severity::Error, e.what()}};
        }

        const auto& tools = root["tools"];
        if (!tools.is_array())
            return {{ConfigIssue::Severity::Error, "missing 'tools' array"}};

        std::set<ToolId>   seen;
        std::set<RecipeId> qualified_anywhere;

        for (const auto& j : tools.as_array()) {
            const std::string kind = j["kind"].as_string();
            const std::string id   = j["id"].as_string();

            if (id.empty()) {
                issues.push_back({ConfigIssue::Severity::Error, "tool with no id"});
                continue;
            }
            if (!seen.insert(id).second) {
                issues.push_back({ConfigIssue::Severity::Error,
                                  "duplicate tool id: " + id});
                continue;
            }
            auto it = builders_.find(kind);
            if (it == builders_.end()) {
                issues.push_back({ConfigIssue::Severity::Error,
                                  "unknown tool kind '" + kind + "' for " + id});
                continue;
            }
            try {
                reg.add(it->second(j, pool));
            } catch (const std::exception& e) {
                issues.push_back({ConfigIssue::Severity::Error,
                                  std::string("building ") + id + ": " + e.what()});
                continue;
            }
            for (const auto& r : j["recipes"].string_list())
                qualified_anywhere.insert(r);
            for (const auto& c : j["chambers"].as_array())
                for (const auto& r : c["recipes"].string_list())
                    qualified_anywhere.insert(r);
            for (const auto& r : j["test_programs"].string_list())
                qualified_anywhere.insert(r);
        }

        // Validation pass that actually catches production incidents: a recipe
        // in the active route with zero qualified tools means every lot hitting
        // that step silently stalls. Fail the config, not the shift.
        for (const auto& r : root["active_recipes"].string_list())
            if (!qualified_anywhere.count(r))
                issues.push_back({ConfigIssue::Severity::Error,
                                  "active recipe '" + r + "' has no qualified tool"});

        // >>> PLACEHOLDER: hot reload. Watch the file (inotify) and rebuild the
        //     registry into a new object, then swap — never mutate in place
        //     while the planner is reading.
        return issues;
    }

private:
    ToolFactory() { register_builtins(); }
    void register_builtins();

    std::unordered_map<std::string, Builder> builders_;
};

// ---------------------------------------------------------------------------
// Built-in builders, one per configuration class.
// ---------------------------------------------------------------------------

inline void ToolFactory::register_builtins() {
    register_kind("SINGLE_WAFER", [](const json::Value& j, ReticlePool&) {
        return std::make_unique<SingleWaferTool>(
            j["id"].as_string(), j["area"].as_string(),
            j["recipes"].string_list(),
            j["sec_per_wafer"].as_double(45.0),
            j["changeover_s"].as_double(600.0));
    });

    register_kind("BATCH_FURNACE", [](const json::Value& j, ReticlePool&) {
        return std::make_unique<BatchFurnace>(
            j["id"].as_string(), j["area"].as_string(),
            j["recipes"].string_list(),
            j["min_batch"].as_int(4), j["max_batch"].as_int(6),
            j["fixed_process_s"].as_double(7200.0),
            j["max_hold_s"].as_double(1800.0));
    });

    register_kind("CLUSTER", [](const json::Value& j, ReticlePool&) {
        std::vector<ClusterTool::Chamber> chambers;
        for (const auto& c : j["chambers"].as_array())
            chambers.push_back({c["name"].as_string(), c["recipes"].string_list(),
                                c["online"].as_bool(true), ""});
        return std::make_unique<ClusterTool>(
            j["id"].as_string(), j["area"].as_string(),
            std::move(chambers), j["sec_per_wafer"].as_double(30.0));
    });

    register_kind("LITHO_SCANNER", [](const json::Value& j, ReticlePool& pool) {
        return std::make_unique<LithoScanner>(
            j["id"].as_string(), j["area"].as_string(), pool,
            j["recipes"].string_list(),
            j["sec_per_wafer"].as_double(22.0),
            j["reticle_swap_s"].as_double(300.0));
    });

    register_kind("METROLOGY", [](const json::Value& j, ReticlePool&) {
        return std::make_unique<MetrologyStation>(
            j["id"].as_string(), j["area"].as_string(),
            j["recipes"].string_list(),
            j["sample_rate"].as_double(1.0),
            j["slots"].as_int(1),
            j["sec_per_lot"].as_double(480.0));
    });

    register_kind("PROBE_TESTER", [](const json::Value& j, ReticlePool&) {
        auto t = std::make_unique<ProbeTester>(
            j["id"].as_string(), j["area"].as_string(),
            j["probe_cards"].string_list(),
            j["test_programs"].string_list(),
            j["parallel_sites"].as_int(1),
            j["sec_per_wafer"].as_double(18.0),
            j["card_change_s"].as_double(1200.0),
            j["temp_soak_s"].as_double(900.0));
        for (const auto& [prod, card] : j["product_cards"].as_object())
            t->set_card_for_product(prod, card.as_string());
        for (const auto& [prog, temp] : j["program_temps"].as_object()) {
            const std::string s = temp.as_string();
            t->set_temp_for_program(prog, s == "hot"  ? TestTemp::Hot
                                        : s == "cold" ? TestTemp::Cold
                                                      : TestTemp::Ambient);
        }
        return t;
    });
}

} // namespace fab
