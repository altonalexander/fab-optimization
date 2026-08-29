#pragma once
// fab/zmq_transport.hpp — ZeroMQ transport for the real-time zone.
//
// ZONE: rt-net (dispatcher <-> AMHS adapter). NOT the durable path.
//
// Why ZMQ here and Kafka there:
//   Kafka  = durable, replayable, ms-scale. Correct for MES/lot history.
//   ZMQ    = fire-and-forget, us-scale, no broker. Correct for move commands.
//
// Deliberate design choice: move commands are LOSSY BY DESIGN. If a command
// is dropped the vehicle re-requests. Adding delivery guarantees here adds
// latency, and a stale move command is worse than a missing one.
//
// Socket patterns:
//   PUB/SUB       telemetry (tool status). Lossy is correct — the next
//                 update supersedes the last one.
//   DEALER/ROUTER move request/reply. Async and correlated, which matches
//                 HSMS transactions. NEVER REQ/REP: it is lockstep, and one
//                 slow reply stalls every vehicle behind it.
//
// Build: -DFAB_HAVE_ZMQ, link -lzmq. Without it, everything below falls back
// to the in-memory bus so the demo still runs end to end.

#include "fab/transport.hpp"

#include <string>

namespace fab {

// Endpoint conventions. inproc/ipc when co-located (single-digit us),
// tcp only when the adapter genuinely lives on another host.
namespace endpoints {
inline constexpr const char* kMoveCommands = "ipc:///var/run/fab/moves.ipc";
inline constexpr const char* kTelemetry    = "ipc:///var/run/fab/telemetry.ipc";
// Cross-host variant, used when AMHS_ADAPTER_HOST is set:
inline constexpr const char* kMoveCommandsTcp = "tcp://amhs-adapter:5561";
inline constexpr const char* kTelemetryTcp    = "tcp://amhs-adapter:5562";
}

#ifdef FAB_HAVE_ZMQ
#include <zmq.h>

class ZmqContext {
public:
    ZmqContext() : ctx_(zmq_ctx_new()) {
        // One IO thread is plenty at our message rate, and pinning it keeps
        // it off the dispatcher's core.
        zmq_ctx_set(ctx_, ZMQ_IO_THREADS, 1);
    }
    ~ZmqContext() { if (ctx_) zmq_ctx_destroy(ctx_); }
    void* get() const { return ctx_; }
private:
    void* ctx_;
};

// PUB side: telemetry out. Never blocks.
class ZmqProducer : public Producer {
public:
    ZmqProducer(ZmqContext& ctx, const std::string& endpoint, bool bind)
        : sock_(zmq_socket(ctx.get(), ZMQ_PUB)) {
        // Bounded queue + drop-on-full. A backed-up subscriber must never
        // apply backpressure to the dispatcher.
        int hwm = 1000;
        zmq_setsockopt(sock_, ZMQ_SNDHWM, &hwm, sizeof(hwm));
        int linger = 0;
        zmq_setsockopt(sock_, ZMQ_LINGER, &linger, sizeof(linger));
        if (bind) zmq_bind(sock_, endpoint.c_str());
        else      zmq_connect(sock_, endpoint.c_str());
    }
    ~ZmqProducer() { if (sock_) zmq_close(sock_); }

    void send(const std::string& topic, const std::string& key,
              const std::string& payload) override {
        // Multipart: [topic][key][payload] — subscribers filter on frame 0.
        zmq_send(sock_, topic.data(),   topic.size(),   ZMQ_SNDMORE | ZMQ_DONTWAIT);
        zmq_send(sock_, key.data(),     key.size(),     ZMQ_SNDMORE | ZMQ_DONTWAIT);
        const int rc = zmq_send(sock_, payload.data(), payload.size(), ZMQ_DONTWAIT);
        if (rc < 0) dropped_++;   // EAGAIN at HWM: correct to drop, count it
    }

    uint64_t dropped() const { return dropped_; }

private:
    void*    sock_;
    uint64_t dropped_ = 0;
};

class ZmqConsumer : public Consumer {
public:
    ZmqConsumer(ZmqContext& ctx, const std::string& endpoint,
                const std::string& topic_filter, bool bind)
        : sock_(zmq_socket(ctx.get(), ZMQ_SUB)) {
        zmq_setsockopt(sock_, ZMQ_SUBSCRIBE,
                       topic_filter.data(), topic_filter.size());
        int hwm = 1000;
        zmq_setsockopt(sock_, ZMQ_RCVHWM, &hwm, sizeof(hwm));
        if (bind) zmq_bind(sock_, endpoint.c_str());
        else      zmq_connect(sock_, endpoint.c_str());
    }
    ~ZmqConsumer() { if (sock_) zmq_close(sock_); }

    bool poll(std::string& topic_out, std::string& payload_out,
              int timeout_ms) override {
        zmq_setsockopt(sock_, ZMQ_RCVTIMEO, &timeout_ms, sizeof(timeout_ms));
        char buf[4096];
        int n = zmq_recv(sock_, buf, sizeof(buf), 0);
        if (n < 0) return false;
        topic_out.assign(buf, n);
        n = zmq_recv(sock_, buf, sizeof(buf), 0);   // key, discarded here
        n = zmq_recv(sock_, buf, sizeof(buf), 0);
        if (n < 0) return false;
        payload_out.assign(buf, n);
        return true;
    }

private:
    void* sock_;
};

#endif // FAB_HAVE_ZMQ

} // namespace fab
