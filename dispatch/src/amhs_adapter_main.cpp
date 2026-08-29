// amhs_adapter_main.cpp — ZONE 0<->1 BOUNDARY PROCESS
//
// Runs on the equipment network AND the real-time network. It is the only
// process permitted on both, and it exists so that nothing above it ever
// parses a vendor byte.
//
// southbound: HSMS/TCP  <-> amhs-controller (zone 0)
// northbound: ZeroMQ     <-> dispatcher      (zone 1)

#include "fab/amhs_adapter.hpp"
#include "fab/zmq_transport.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <unistd.h>

#include <atomic>
#include <csignal>
#include <sys/socket.h>
#include <iostream>
#include <thread>

using namespace fab;

namespace {
std::atomic<bool> g_stop{false};
void on_signal(int) { g_stop = true; }
std::string arg(int c, char** v, const std::string& k, const std::string& d) {
    const std::string pfx = k + "=";
    for (int i = 1; i < c; ++i) {
        std::string a = v[i];
        if (a.rfind(pfx, 0) == 0) return a.substr(pfx.size());
        if (a == k && i + 1 < c)  return v[i + 1];
    }
    return d;
}
bool has(int c, char** v, const std::string& k) {
    for (int i = 1; i < c; ++i) if (k == v[i]) return true;
    return false;
}
} // namespace

int main(int argc, char** argv) {
    if (has(argc, argv, "--healthcheck")) {
        // >>> PLACEHOLDER: probe the HSMS session state; healthy means
        //     SELECTED, not merely "process is alive".
        return 0;
    }

    const std::string hsms_ep = arg(argc, argv, "--hsms",    "amhs-controller:5000");
    const std::string zmq_pub = arg(argc, argv, "--zmq-pub", "tcp://0.0.0.0:5562");
    const std::string zmq_sub = arg(argc, argv, "--zmq-sub", "tcp://0.0.0.0:5561");
    const double t3 = std::atof(
        std::getenv("HSMS_T3_SECONDS") ? std::getenv("HSMS_T3_SECONDS") : "45");

    std::signal(SIGINT,  on_signal);
    std::signal(SIGTERM, on_signal);

    std::cout << "amhs-adapter  zone=boundary-0-1\n"
              << "  southbound HSMS  " << hsms_ep << "  (zone 0, equipment)\n"
              << "  northbound  PUB  " << zmq_pub << "  (zone 1, realtime)\n"
              << "  northbound  SUB  " << zmq_sub << "  (zone 1, realtime)\n"
              << "  T3 reply timeout " << t3 << "s\n";

    hsms::Timeouts to;
    to.t3 = t3;

    // Northbound transport. Falls back to the in-memory bus when ZMQ is not
    // linked, so the adapter is testable without a broker or a controller.
    InMemoryBus fallback_bus;
    std::unique_ptr<Producer> north;
#ifdef FAB_HAVE_ZMQ
    static ZmqContext zctx;
    north = std::make_unique<ZmqProducer>(zctx, zmq_pub, /*bind=*/true);
    std::cout << "  transport: zeromq\n";
#else
    north = std::make_unique<InMemoryProducer>(fallback_bus);
    std::cout << "  transport: in-memory (rebuild -DFAB_HAVE_ZMQ for zeromq)\n";
#endif

    AmhsAdapter adapter(*north, to);
    long ticks = 0;

    // ---- HSMS TCP client (active entity) --------------------------------
    // VERIFY WITH VENDOR: does the controller PUSH events (S6F11), or is it
    // POLL-ONLY? If poll-only, the poll interval becomes the system's latency
    // floor and the real-time design needs revisiting. This client assumes
    // push, which is what GEM-compliant controllers normally do.
    const auto colon = hsms_ep.rfind(':');
    const std::string host = hsms_ep.substr(0, colon);
    const int port = std::atoi(hsms_ep.substr(colon + 1).c_str());

    int fd = -1;
    auto disconnect = [&] {
        if (fd >= 0) { ::close(fd); fd = -1; }
        adapter.session().on_disconnect();
    };
    adapter.set_wire([&](const std::vector<uint8_t>& f) {
        if (fd < 0) return;
        std::size_t sent = 0;
        while (sent < f.size()) {
            const ssize_t n = ::send(fd, f.data() + sent, f.size() - sent, MSG_NOSIGNAL);
            if (n <= 0) { disconnect(); return; }
            sent += static_cast<std::size_t>(n);
        }
    });

    auto last_retry = std::chrono::steady_clock::now() - std::chrono::hours(1);

    while (!g_stop.load()) {
        // Reconnect after T5 separation, never in a tight loop: a reconnect
        // storm against a struggling controller makes an outage worse.
        if (fd < 0) {
            const auto now = std::chrono::steady_clock::now();
            if (std::chrono::duration<double>(now - last_retry).count() < to.t5) {
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                continue;
            }
            last_retry = now;
            fd = ::socket(AF_INET, SOCK_STREAM, 0);
            sockaddr_in a{};
            a.sin_family = AF_INET;
            a.sin_port   = htons(port);
            a.sin_addr.s_addr = inet_addr(
                host == "localhost" ? "127.0.0.1" : host.c_str());
            if (::connect(fd, (sockaddr*)&a, sizeof(a)) < 0) {
                ::close(fd); fd = -1;
                std::cout << "  hsms connect failed, retry in " << to.t5 << "s\n";
                continue;
            }
            int one = 1;
            setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            std::cout << "  hsms connected -> " << hsms_ep << "\n";
            adapter.session().on_connect();       // sends Select.req
        }

        uint8_t buf[16384];
        struct timeval tv { 0, 100 * 1000 };
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        const ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
        if (n == 0) { std::cout << "  hsms peer closed\n"; disconnect(); continue; }
        if (n > 0)  adapter.session().on_bytes(buf, static_cast<std::size_t>(n));

        adapter.session().on_tick(
            std::chrono::duration<double>(
                std::chrono::steady_clock::now().time_since_epoch()).count());

        if (++ticks % 40 == 0)
            std::cout << "  [" << hsms::to_string(adapter.session().state())
                      << "] secs_in=" << adapter.stats().secs_in.load()
                      << " out=" << adapter.stats().envelopes_out.load()
                      << " decode_fail=" << adapter.stats().decode_failures.load()
                      << "\n";
    }
    disconnect();

    const auto& s = adapter.stats();
    std::cout << "\nadapter stats\n"
              << "  secs in          " << s.secs_in.load()          << "\n"
              << "  envelopes out    " << s.envelopes_out.load()    << "\n"
              << "  moves out        " << s.moves_out.load()        << "\n"
              << "  decode failures  " << s.decode_failures.load()  << "\n"
              << "  session drops    " << s.session_drops.load()    << "\n"
              << "  T3 timeouts      " << s.t3_timeouts.load()      << "\n";
    return 0;
}
