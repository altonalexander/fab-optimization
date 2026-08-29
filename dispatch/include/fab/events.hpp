#pragma once
// fab/events.hpp — the wire contract between the fab and the dispatcher.
//
// Serialization here is a deliberately dumb key=value line format so the
// end-to-end demo has zero dependencies. It is NOT what you ship.
//
// >>> PLACEHOLDER: replace with FlatBuffers or Protobuf. <<<
//     Reasons: (a) zero-copy decode matters on the ingestion thread,
//     (b) you need a schema registry so MES/SECS-GEM producers and this
//         consumer can evolve independently without a coordinated deploy.
//     Keep Envelope's field set identical when you migrate; everything
//     downstream reads the structs, not the wire bytes.

#include <string>
#include <sstream>
#include <unordered_map>
#include <cstdint>
#include <optional>

namespace fab {

enum class EventType {
    LotReady,        // lot arrived at a stocker / is dispatchable
    LotStarted,      // tool accepted the lot and began processing
    LotComplete,     // tool finished a lot
    ToolStatus,      // up / down
    ChamberStatus,   // cluster tool chamber up / down
    RecipeQual,      // qualification added or removed
    MoveRequest,     // OHT vehicle wants a destination NOW (fast path)
    Unknown
};

inline const char* to_string(EventType t) {
    switch (t) {
        case EventType::LotReady:      return "LOT_READY";
        case EventType::LotStarted:    return "LOT_STARTED";
        case EventType::LotComplete:   return "LOT_COMPLETE";
        case EventType::ToolStatus:    return "TOOL_STATUS";
        case EventType::ChamberStatus: return "CHAMBER_STATUS";
        case EventType::RecipeQual:    return "RECIPE_QUAL";
        case EventType::MoveRequest:   return "MOVE_REQUEST";
        default:                       return "UNKNOWN";
    }
}

inline EventType event_type_from(const std::string& s) {
    if (s == "LOT_READY")      return EventType::LotReady;
    if (s == "LOT_STARTED")    return EventType::LotStarted;
    if (s == "LOT_COMPLETE")   return EventType::LotComplete;
    if (s == "TOOL_STATUS")    return EventType::ToolStatus;
    if (s == "CHAMBER_STATUS") return EventType::ChamberStatus;
    if (s == "RECIPE_QUAL")    return EventType::RecipeQual;
    if (s == "MOVE_REQUEST")   return EventType::MoveRequest;
    return EventType::Unknown;
}

// One flat envelope for every event. Sparse by design: a ToolStatus event
// simply leaves the lot fields empty.
struct Envelope {
    EventType   type = EventType::Unknown;
    std::string source;            // producer identity; seq is scoped to this
    uint64_t    seq  = 0;          // monotonic per (source, key), for gap detection
    uint64_t    ts_ns = 0;

    // Lot fields
    std::string lot_id;
    std::string product_id;
    std::string recipe;
    std::string reticle;
    int         wafer_count   = 25;
    double      priority      = 1.0;
    double      qtime_slack_s = 1e9;

    // Tool fields
    std::string tool_id;
    std::string chamber;
    bool        online = true;

    std::string encode() const {
        std::ostringstream o;
        o << "type=" << to_string(type)
          << ";src=" << source
          << ";seq=" << seq << ";ts=" << ts_ns
          << ";lot=" << lot_id << ";prod=" << product_id
          << ";recipe=" << recipe << ";reticle=" << reticle
          << ";wafers=" << wafer_count << ";prio=" << priority
          << ";slack=" << qtime_slack_s
          << ";tool=" << tool_id << ";chamber=" << chamber
          << ";online=" << (online ? 1 : 0);
        return o.str();
    }

    static std::optional<Envelope> decode(const std::string& s) {
        Envelope e;
        std::unordered_map<std::string, std::string> kv;
        std::istringstream in(s);
        std::string tok;
        while (std::getline(in, tok, ';')) {
            auto eq = tok.find('=');
            if (eq == std::string::npos) continue;
            kv[tok.substr(0, eq)] = tok.substr(eq + 1);
        }
        auto get = [&](const char* k) -> std::string {
            auto it = kv.find(k);
            return it == kv.end() ? std::string{} : it->second;
        };
        if (get("type").empty()) return std::nullopt;   // malformed: drop + count
        e.type          = event_type_from(get("type"));
        e.source        = get("src");
        e.seq           = std::strtoull(get("seq").c_str(),  nullptr, 10);
        e.ts_ns         = std::strtoull(get("ts").c_str(),   nullptr, 10);
        e.lot_id        = get("lot");
        e.product_id    = get("prod");
        e.recipe        = get("recipe");
        e.reticle       = get("reticle");
        e.wafer_count   = std::atoi(get("wafers").c_str());
        e.priority      = std::atof(get("prio").c_str());
        e.qtime_slack_s = std::atof(get("slack").c_str());
        e.tool_id       = get("tool");
        e.chamber       = get("chamber");
        e.online        = get("online") != "0";
        return e;
    }
};

// Topic names. Partition lot events by lot_id and tool events by tool_id so
// per-key ordering is guaranteed — the ingestion thread depends on it.
namespace topics {
inline constexpr const char* kLotEvents  = "fab.lot.events";
inline constexpr const char* kToolEvents = "fab.tool.events";
inline constexpr const char* kDecisions  = "fab.dispatch.decisions";
}

} // namespace fab
