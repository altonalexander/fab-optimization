#pragma once
// fab/amhs_adapter.hpp — the zone boundary.
//
//   equipment-net (HSMS/TCP)  ──[ AmhsAdapter ]──  rt-net (ZMQ, Envelope)
//
// Everything vendor-specific lives on the left. Everything above sees only
// fab::Envelope. This is the standardization win: swapping Murata for Daifuku,
// or adding a second fab, is a new adapter binary and zero dispatcher changes.
//
// The adapter is a SEPARATE PROCESS on a SEPARATE NETWORK for a reason: SECS-II
// decode bugs, vendor session quirks, and reconnect storms must not be able to
// crash or stall the thing holding the 1ms budget.

#include "fab/events.hpp"
#include "fab/hsms.hpp"
#include "fab/secs2.hpp"
#include "fab/transport.hpp"

#include <atomic>
#include <chrono>
#include <string>

namespace fab {

struct AdapterStats {
    std::atomic<uint64_t> secs_in{0};
    std::atomic<uint64_t> envelopes_out{0};
    std::atomic<uint64_t> moves_out{0};
    std::atomic<uint64_t> decode_failures{0};
    std::atomic<uint64_t> session_drops{0};
    std::atomic<uint64_t> t3_timeouts{0};
};

class AmhsAdapter {
public:
    AmhsAdapter(Producer& northbound, hsms::Timeouts to)
        : north_(northbound),
          session_(to, [this](const std::vector<uint8_t>& f) { to_wire(f); }) {
        session_.set_data_handler([this](const hsms::SecsMessage& m) { on_secs(m); });
        session_.set_state_handler([this](hsms::SessionState o, hsms::SessionState n) {
            if (o == hsms::SessionState::Selected) stats_.session_drops++;
            // >>> PLACEHOLDER: publish adapter health northbound so the
            //     dispatcher can mark the whole AMHS zone degraded rather
            //     than silently seeing zero events.
        });
    }

    hsms::Session& session() { return session_; }
    const AdapterStats& stats() const { return stats_; }

    // ---- northbound: SECS event -> Envelope -----------------------------
    void on_secs(const hsms::SecsMessage& m) {
        stats_.secs_in++;

        Envelope e;
        e.source = "amhs";
        e.seq    = ++seq_;
        e.ts_ns  = now_ns();

        if (m.stream != hsms::sf::kStreamEquipmentStatus || m.function != 11) {
            stats_.decode_failures++;
            return;
        }

        // S6F11 = L{ DATAID, CEID, L{ L{ RPTID, L{ vars... } } } }
        secs2::ItemPtr root;
        try {
            root = secs2::decode(m.body);
        } catch (const secs2::DecodeError&) {
            stats_.decode_failures++;
            return;
        }
        if (!root || root->size() < 3) { stats_.decode_failures++; return; }

        const uint32_t ceid = static_cast<uint32_t>(root->at(1)->as_uint());
        const auto& reports = root->at(2);
        if (reports->size() < 1) { stats_.decode_failures++; return; }
        const auto& rpt = reports->at(0);
        if (rpt->size() < 2) { stats_.decode_failures++; return; }
        const auto& vars = rpt->at(1);

        switch (ceid) {
        case hsms::sf::kCeidCarrierArrived: {
            if (vars->size() < 7) { stats_.decode_failures++; return; }
            e.type          = EventType::LotReady;
            e.lot_id        = vars->at(0)->as_ascii();
            e.product_id    = vars->at(1)->as_ascii();
            e.recipe        = vars->at(2)->as_ascii();
            e.reticle       = vars->at(3)->as_ascii();
            e.wafer_count   = static_cast<int>(vars->at(4)->as_uint(25));
            e.priority      = vars->at(5)->as_uint(100) / 100.0;
            e.qtime_slack_s = static_cast<double>(vars->at(6)->as_uint(3600));
            break;
        }
        case hsms::sf::kCeidProcessComplete: {
            if (vars->size() < 2) { stats_.decode_failures++; return; }
            e.type    = EventType::LotComplete;
            e.lot_id  = vars->at(0)->as_ascii();
            e.tool_id = vars->at(1)->as_ascii();
            break;
        }
        case hsms::sf::kCeidToolStateChange: {
            if (vars->size() < 2) { stats_.decode_failures++; return; }
            e.type    = EventType::ToolStatus;
            e.tool_id = vars->at(0)->as_ascii();
            e.online  = vars->at(1)->as_uint(1) != 0;
            break;
        }
        default:
            stats_.decode_failures++;
            return;   // unknown CEID: drop and count, never guess
        }

        north_.send(topics::kLotEvents, e.lot_id.empty() ? e.tool_id : e.lot_id,
                    e.encode());
        stats_.envelopes_out++;
    }

    // ---- southbound: move command -> SECS --------------------------------
    // Fire-and-forget. If this is lost the vehicle re-requests; we do NOT
    // retry, because a stale move command is worse than a missing one.
    void send_move(const std::string& lot_id, const std::string& tool_id) {
        hsms::SecsMessage m;
        m.stream       = hsms::sf::kStreamControl;   // S2F41 remote command
        m.function     = 41;
        m.w_bit        = true;
        m.system_bytes = ++txn_;
        // S2F41 = L{ RCMD, L{ L{CPNAME,CPVAL}, ... } }
        m.body = secs2::encode(secs2::Item::L({
            secs2::Item::A("TRANSFER"),
            secs2::Item::L({
                secs2::Item::L({secs2::Item::A("CARRIERID"), secs2::Item::A(lot_id)}),
                secs2::Item::L({secs2::Item::A("DEST"),      secs2::Item::A(tool_id)}),
            }),
        }));
        session_.send(m);
        stats_.moves_out++;
        // >>> PLACEHOLDER: register txn_ with a T3 deadline. A timed-out move
        //     must surface as a dispatch failure, not vanish.
    }

private:
    static uint64_t now_ns() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    }

    void to_wire(const std::vector<uint8_t>& f) {
        if (wire_) wire_(f);   // injected by the TCP client; testable without one
    }

public:
    using WireFn = std::function<void(const std::vector<uint8_t>&)>;
    void set_wire(WireFn w) { wire_ = std::move(w); }

private:
    WireFn wire_;

    Producer&      north_;
    hsms::Session  session_;
    AdapterStats   stats_;
    uint64_t       seq_ = 0;
    uint32_t       txn_ = 0;
};

} // namespace fab
