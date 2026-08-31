// slate_capi.cpp — the C ABI that PySCFabSim's slate_rule calls through.
//
// Why a C ABI and not pybind11 (docs/adr/0009): this environment has neither
// pybind11 nor Python development headers, and ctypes needs neither. The
// boundary is COARSE -- one call per planning cycle carrying flat POD arrays,
// not one call per decision point -- so pybind11's ergonomics would buy very
// little for a build dependency. Revisit if the surface stops being flat.
//
// The 16 million decision points in a 730-day run do NOT cross this boundary.
// They read the snapshot returned by the last fabslate_plan().
//
// Build:
//   g++ -std=c++20 -O2 -fPIC -shared -Iinclude src/slate_capi.cpp \
//       -o libfabslate.so            [-DFAB_HAVE_ORTOOLS -lortools]

#include "fab/family_tool.hpp"
#include "fab/machine_config.hpp"
#include "fab/planner.hpp"
#include "fab/solver.hpp"

#include <cstring>
#include <memory>
#include <set>
#include <string>
#include <vector>

using namespace fab;

namespace {

// Fixed-width char arrays keep the struct trivially copyable from ctypes with
// no pointer ownership crossing the boundary in either direction. Sizes are
// generous against SMT2020's longest identifiers; copy_in truncates safely
// rather than overrunning if a dataset ever exceeds them.
constexpr int ID   = 48;
constexpr int NAME = 72;

template <int N>
std::string str_of(const char (&buf)[N]) {
    return std::string(buf, ::strnlen(buf, N));
}

} // namespace

extern "C" {

struct CTool {
    char   tool_id[ID];
    char   family[ID];
    char   current_setup[ID];
    int    capacity;
    int    online;              // 0/1
    double speed;
    int    min_run_length;      // policy: lots owed after a changeover
    int    min_runs_left;       // state: lots still owed (-1 = not in a run)
    char   min_runs_setup[ID];
};

struct CLot {
    char   lot_id[ID];
    char   family[ID];
    char   setup_group[ID];
    char   step[NAME];          // -> Lot::recipe   (batch key part 1)
    char   part[ID];            // -> Lot::product_id (batch key part 2)
    int    batch_min;
    int    batch_max;
    int    wafers;
    double priority;            // the tactical urgency vector, from Python
    double qtime_slack_s;
    double step_process_s;
    double due_s;
    double waiting_s;
};

struct CToken {
    int    lot_index;           // index into the CLot array that was passed in
    char   tool_id[ID];
    char   alternate[ID];
    int    rank;
    double expected_process_s;
};

struct CPlanStats {
    int    assigned;
    int    ready;
    int    variables;
    double solve_time_s;
    double objective;
    int    status;              // fab::SolveStatus
    char   detail[256];
};

struct FabSlateHandle {
    SetupMatrix               setups;
    ToolRegistry              reg;
    std::unique_ptr<Planner>  planner;
    std::vector<std::string>  tool_order;   // registry build order
    std::string               solver_name;
    bool                      built = false;
};

// --- lifecycle -------------------------------------------------------------

void* fabslate_new(const char* solver) {
    auto* h = new FabSlateHandle();
    h->solver_name = solver ? solver : "greedy";
    h->planner = std::make_unique<Planner>(make_solver(h->solver_name));
    return h;
}

void fabslate_free(void* handle) {
    delete static_cast<FabSlateHandle*>(handle);
}

const char* fabslate_solver_name(void* handle) {
    auto* h = static_cast<FabSlateHandle*>(handle);
    return h && h->planner ? h->planner->solver_name() : "";
}

int fabslate_solver_available(void* handle) {
    auto* h = static_cast<FabSlateHandle*>(handle);
    return h && h->planner && h->planner->solver_available() ? 1 : 0;
}

// --- setup matrix ----------------------------------------------------------
// Loaded once, before the first plan. SMT2020's setup.txt is asymmetric, so
// from/to order matters and callers must not collapse it.

void fabslate_set_setup(void* handle, const char* from, const char* to, double seconds) {
    auto* h = static_cast<FabSlateHandle*>(handle);
    if (h) h->setups.set(from ? from : "", to ? to : "", seconds);
}

void fabslate_set_setup_default(void* handle, double seconds) {
    auto* h = static_cast<FabSlateHandle*>(handle);
    if (h) h->setups.set_default(seconds);
}

// --- tools -----------------------------------------------------------------
// The tool SET is static for a run; only tool STATE moves. So the registry is
// built once and afterwards only mutated, which keeps ~1,300 allocations out of
// every one of ~1M planning cycles.

int fabslate_set_tools(void* handle, const CTool* tools, int n) {
    auto* h = static_cast<FabSlateHandle*>(handle);
    if (!h || !tools || n < 0) return -1;

    h->reg = ToolRegistry{};
    h->tool_order.clear();
    for (int i = 0; i < n; ++i) {
        const CTool& c = tools[i];
        auto t = std::make_unique<FamilyTool>(
            str_of(c.tool_id), "fab", str_of(c.family), &h->setups,
            c.capacity, c.speed, 0.0);
        t->set_min_run_length(c.min_run_length);
        t->set_online(c.online != 0);
        t->set_current_setup(str_of(c.current_setup));
        if (c.min_runs_left > 0) t->set_min_runs(c.min_runs_left, str_of(c.min_runs_setup));
        h->tool_order.push_back(str_of(c.tool_id));
        h->reg.add(std::move(t));
    }
    h->built = true;
    return n;
}

// Per-cycle state refresh. Positional: the caller must pass tools in the same
// order it registered them. Cheaper than a hash lookup per tool per cycle, and
// the ordering is trivially stable on the Python side.
int fabslate_update_tools(void* handle, const CTool* tools, int n) {
    auto* h = static_cast<FabSlateHandle*>(handle);
    if (!h || !h->built || !tools) return -1;
    if (n != static_cast<int>(h->tool_order.size())) return -2;

    for (int i = 0; i < n; ++i) {
        auto* t = dynamic_cast<FamilyTool*>(h->reg.find(h->tool_order[i]));
        if (!t) continue;
        const CTool& c = tools[i];
        t->set_online(c.online != 0);
        t->set_current_setup(str_of(c.current_setup));
        t->set_min_runs(c.min_runs_left > 0 ? c.min_runs_left : 0,
                        str_of(c.min_runs_setup));
    }
    return n;
}

// --- plan ------------------------------------------------------------------

int fabslate_plan(void* handle,
                  const CLot* lots, int n_lots,
                  double budget_s, double relative_gap, int threads,
                  const char* dirty_families,   // '\n'-separated; NULL = all
                  CToken* out, int out_cap,
                  CPlanStats* stats) {
    auto* h = static_cast<FabSlateHandle*>(handle);
    if (!h || !h->built || !lots || !out) return -1;

    std::vector<Lot> v;
    v.reserve(n_lots);
    for (int i = 0; i < n_lots; ++i) {
        const CLot& c = lots[i];
        Lot l;
        l.lot_id         = str_of(c.lot_id);
        l.family         = str_of(c.family);
        l.setup_group    = str_of(c.setup_group);
        l.recipe         = str_of(c.step);
        l.product_id     = str_of(c.part);
        l.batch_min      = c.batch_min;
        l.batch_max      = c.batch_max;
        l.wafer_count    = c.wafers;
        l.priority       = c.priority;
        l.qtime_slack_s  = c.qtime_slack_s;
        l.step_process_s = c.step_process_s;
        l.due_s          = c.due_s;
        l.waiting_s      = c.waiting_s;
        v.push_back(std::move(l));
    }

    PlannerConfig cfg;
    cfg.solve_budget_s = budget_s;
    cfg.relative_gap   = relative_gap;
    cfg.threads        = threads;

    std::set<std::string> dirty;
    const std::set<std::string>* dirty_p = nullptr;
    if (dirty_families) {
        const char* p = dirty_families;
        std::string cur;
        for (; *p; ++p) {
            if (*p == '\n') { if (!cur.empty()) dirty.insert(cur); cur.clear(); }
            else cur += *p;
        }
        if (!cur.empty()) dirty.insert(cur);
        dirty_p = &dirty;
    }

    static uint64_t cycle = 0;
    PlanResult r = h->planner->plan_by_family(h->reg, v, ++cycle, cfg, dirty_p);

    // Index lots by id so tokens can be reported positionally: the caller
    // indexes its own arrays, never our strings.
    std::unordered_map<std::string, int> idx;
    idx.reserve(v.size());
    for (int i = 0; i < static_cast<int>(v.size()); ++i) idx[v[i].lot_id] = i;

    int n = 0;
    if (r.slate) {
        for (const auto& [lot_id, tok] : r.slate->tokens) {
            if (n >= out_cap) break;
            auto it = idx.find(lot_id);
            if (it == idx.end()) continue;    // carried-over lot no longer ready
            CToken& o = out[n];
            std::memset(&o, 0, sizeof(o));
            o.lot_index = it->second;
            std::strncpy(o.tool_id,   tok.primary.c_str(),   ID - 1);
            std::strncpy(o.alternate, tok.alternate.c_str(), ID - 1);
            o.rank = static_cast<int>(tok.rank);
            o.expected_process_s = tok.expected_process_s;
            ++n;
        }
    }

    if (stats) {
        std::memset(stats, 0, sizeof(*stats));
        stats->assigned     = r.assigned;
        stats->ready        = r.ready;
        stats->variables    = r.variables;
        stats->solve_time_s = r.solve_time_s;
        stats->objective    = r.objective;
        stats->status       = static_cast<int>(r.status);
        std::strncpy(stats->detail, r.detail.c_str(), sizeof(stats->detail) - 1);
    }
    return n;
}

// Struct sizes, so the Python side can assert its ctypes layout matches this
// translation unit's instead of silently misreading every field after the
// first padding difference.
int fabslate_sizeof(int which) {
    switch (which) {
        case 0: return static_cast<int>(sizeof(CTool));
        case 1: return static_cast<int>(sizeof(CLot));
        case 2: return static_cast<int>(sizeof(CToken));
        case 3: return static_cast<int>(sizeof(CPlanStats));
        default: return -1;
    }
}

} // extern "C"
