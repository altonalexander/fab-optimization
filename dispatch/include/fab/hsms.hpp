#pragma once
// fab/hsms.hpp — HSMS (SEMI E37) session layer + SECS-II (E5) decode.
//
// ZONE: equipment network only. Nothing above the AmhsAdapter ever sees an
// HSMS byte or a stream/function number. That is the entire point of this file
// existing separately: vendor quirks stay on one side of a process boundary.
//
// HSMS is a STATEFUL session protocol, not a message stream:
//   TCP connect -> Select.req/rsp -> [Data messages] -> Deselect/Separate
//   with T3 (reply timeout), T5 (connect separation), T6 (control timeout),
//   T7 (not-selected timeout), T8 (network intercharacter timeout).
//
// >>> PLACEHOLDER: this is a state machine skeleton with the wire codec
//     stubbed. Before touching a real controller you need:
//       - the vendor's supported SECS message subset (ask for the GEM
//         compliance statement / SEMI E30 checklist)
//       - their enforced T3 value; it sets your reply deadline
//       - whether they PUSH unsolicited events (S6F11) or are POLL-ONLY.
//         If poll-only, the poll interval becomes your latency floor and the
//         whole real-time design changes. VERIFY THIS FIRST.

#include <cstdint>
#include <chrono>
#include <functional>
#include <string>
#include <vector>

namespace fab::hsms {

// ---- HSMS message types (SEMI E37 header byte SType) ----------------------
enum class SType : uint8_t {
    DataMessage   = 0,
    SelectReq     = 1, SelectRsp    = 2,
    DeselectReq   = 3, DeselectRsp  = 4,
    LinktestReq   = 5, LinktestRsp  = 6,
    RejectReq     = 7,
    SeparateReq   = 9,
};

enum class SessionState { NotConnected, Connected, Selected, Retrying };

inline const char* to_string(SessionState s) {
    switch (s) {
        case SessionState::NotConnected: return "NOT_CONNECTED";
        case SessionState::Connected:    return "CONNECTED";
        case SessionState::Selected:     return "SELECTED";
        case SessionState::Retrying:     return "RETRYING";
    }
    return "?";
}

// A decoded SECS-II message, stream/function addressed.
struct SecsMessage {
    uint8_t  stream   = 0;      // S
    uint8_t  function = 0;      // F
    bool     w_bit    = false;  // reply expected
    uint32_t system_bytes = 0;  // transaction id, for reply correlation
    std::vector<uint8_t> body;  // SECS-II item tree, still encoded

    std::string sf() const {
        return "S" + std::to_string(stream) + "F" + std::to_string(function);
    }
};

// Timers, in the units the standard specifies (seconds).
struct Timeouts {
    double t3 = 45.0;   // reply timeout      <- ASK THE VENDOR, drives our SLA
    double t5 = 10.0;   // connect separation
    double t6 = 5.0;    // control transaction
    double t7 = 10.0;   // not selected
    double t8 = 5.0;    // network intercharacter
    double linktest_interval = 30.0;
};

// ---------------------------------------------------------------------------
// Session state machine. Transport-agnostic so it can be unit tested without
// a socket: feed it on_connect/on_bytes/on_tick and observe the emissions.
// ---------------------------------------------------------------------------

class Session {
public:
    using Emit    = std::function<void(const std::vector<uint8_t>&)>;
    using OnData  = std::function<void(const SecsMessage&)>;
    using OnState = std::function<void(SessionState, SessionState)>;

    Session(Timeouts t, Emit emit) : to_(t), emit_(std::move(emit)) {}

    void set_data_handler(OnData h)   { on_data_  = std::move(h); }
    void set_state_handler(OnState h) { on_state_ = std::move(h); }

    SessionState state() const { return state_; }

    void on_connect() {
        transition(SessionState::Connected);
        // Active entity sends Select.req immediately.
        send_control(SType::SelectReq);
        // >>> PLACEHOLDER: start T7. If no Select.rsp before it fires, drop
        //     the TCP connection and re-dial after T5.
    }

    void on_disconnect() { transition(SessionState::NotConnected); }

    // Feed raw bytes off the socket.
    void on_bytes(const uint8_t* data, std::size_t len) {
        buf_.insert(buf_.end(), data, data + len);
        // HSMS framing: 4-byte big-endian length, then a 10-byte header.
        while (buf_.size() >= 4) {
            const uint32_t plen = (buf_[0] << 24) | (buf_[1] << 16) |
                                  (buf_[2] << 8)  |  buf_[3];
            if (plen < 10 || buf_.size() < 4 + plen) break;   // wait for more
            dispatch_frame(&buf_[4], plen);
            buf_.erase(buf_.begin(), buf_.begin() + 4 + plen);
        }
        // >>> PLACEHOLDER: enforce T8 between partial frames; a stalled
        //     mid-message peer must be dropped, not waited on forever.
    }

    // Called on a timer; drives linktest and reply timeouts.
    void on_tick(double now_s) {
        if (state_ == SessionState::Selected &&
            now_s - last_linktest_ > to_.linktest_interval) {
            send_control(SType::LinktestReq);
            last_linktest_ = now_s;
        }
        // >>> PLACEHOLDER: sweep outstanding transactions for T3 expiry.
        //     A T3 timeout on a move command must surface as a dispatch
        //     failure, not a silent drop.
    }

    // Send a SECS-II data message (e.g. a move command to the AMHS).
    void send(const SecsMessage& m) {
        if (state_ != SessionState::Selected) { dropped_++; return; }
        emit_(encode(m));
        sent_++;
    }

    uint64_t sent() const    { return sent_; }
    uint64_t received() const{ return recv_; }
    uint64_t dropped() const { return dropped_; }

private:
    void transition(SessionState s) {
        if (s == state_) return;
        const auto old = state_;
        state_ = s;
        if (on_state_) on_state_(old, s);
    }

    void dispatch_frame(const uint8_t* h, uint32_t len) {
        // HSMS header: [0..1] session id, [2] byte2, [3] byte3,
        //              [4] PType, [5] SType, [6..9] system bytes
        const SType st = static_cast<SType>(h[5]);
        const uint32_t sysbytes = (h[6] << 24) | (h[7] << 16) | (h[8] << 8) | h[9];

        switch (st) {
        case SType::SelectRsp:
            transition(SessionState::Selected);
            break;
        case SType::LinktestReq:
            send_control(SType::LinktestRsp, sysbytes);
            break;
        case SType::SeparateReq:
        case SType::DeselectReq:
            transition(SessionState::Connected);
            break;
        case SType::DataMessage: {
            SecsMessage m;
            m.stream       = h[2] & 0x7F;
            m.w_bit        = (h[2] & 0x80) != 0;
            m.function     = h[3];
            m.system_bytes = sysbytes;
            if (len > 10) m.body.assign(h + 10, h + len);
            recv_++;
            if (on_data_) on_data_(m);
            break;
        }
        default: break;
        }
    }

    void send_control(SType st, uint32_t sysbytes = 0) {
        std::vector<uint8_t> f(14, 0);
        const uint32_t plen = 10;
        f[0] = (plen >> 24) & 0xFF; f[1] = (plen >> 16) & 0xFF;
        f[2] = (plen >> 8)  & 0xFF; f[3] =  plen        & 0xFF;
        f[8] = 0;                                  // PType
        f[9] = static_cast<uint8_t>(st);           // SType
        f[10] = (sysbytes >> 24) & 0xFF; f[11] = (sysbytes >> 16) & 0xFF;
        f[12] = (sysbytes >> 8)  & 0xFF; f[13] =  sysbytes        & 0xFF;
        emit_(f);
    }

    std::vector<uint8_t> encode(const SecsMessage& m) {
        // >>> PLACEHOLDER: real SECS-II item encoding (L/A/U4/B formats).
        //     Body is passed through as-is here.
        const uint32_t plen = 10 + static_cast<uint32_t>(m.body.size());
        std::vector<uint8_t> f;
        f.reserve(4 + plen);
        f.push_back((plen >> 24) & 0xFF); f.push_back((plen >> 16) & 0xFF);
        f.push_back((plen >> 8)  & 0xFF); f.push_back( plen        & 0xFF);
        f.push_back(0); f.push_back(0);                        // session id
        f.push_back(m.stream | (m.w_bit ? 0x80 : 0));          // byte 2
        f.push_back(m.function);                               // byte 3
        f.push_back(0);                                        // PType
        f.push_back(static_cast<uint8_t>(SType::DataMessage)); // SType
        f.push_back((m.system_bytes >> 24) & 0xFF);
        f.push_back((m.system_bytes >> 16) & 0xFF);
        f.push_back((m.system_bytes >> 8)  & 0xFF);
        f.push_back( m.system_bytes        & 0xFF);
        f.insert(f.end(), m.body.begin(), m.body.end());
        return f;
    }

    Timeouts             to_;
    Emit                 emit_;
    OnData               on_data_;
    OnState              on_state_;
    SessionState         state_ = SessionState::NotConnected;
    std::vector<uint8_t> buf_;
    double               last_linktest_ = 0.0;
    uint64_t             sent_ = 0, recv_ = 0, dropped_ = 0;
};

// ---------------------------------------------------------------------------
// The GEM message subset we care about. Confirm every one of these against
// the vendor's compliance statement before writing the decoder.
// ---------------------------------------------------------------------------
namespace sf {
inline constexpr uint8_t kStreamEquipmentStatus = 6;   // S6F11 event report
inline constexpr uint8_t kStreamMaterial        = 16;  // S16Fx carrier/job
inline constexpr uint8_t kStreamControl         = 2;   // S2Fx remote command

// Events we expect the AMHS/equipment to push (S6F11 CEIDs are vendor-defined)
inline constexpr uint32_t kCeidCarrierArrived   = 1001;
inline constexpr uint32_t kCeidCarrierDeparted  = 1002;
inline constexpr uint32_t kCeidToolStateChange  = 1003;
inline constexpr uint32_t kCeidProcessComplete  = 1004;
// >>> PLACEHOLDER: these CEIDs are placeholders. They come from the vendor's
//     event list and differ per controller. Hardcoding guesses here would be
//     the single most likely source of a silent integration failure.
}

} // namespace fab::hsms
