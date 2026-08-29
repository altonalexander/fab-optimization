// hsms_sim_main.cpp — ZONE 0. AMHS controller + equipment simulator.
//
// A REAL HSMS server: real TCP, real SEMI E37 framing, real SECS-II bodies.
// It exists so the transport path is exercised rather than mocked. If the
// adapter can talk to this, it can talk to a controller.
//
// Speaks:
//   <- Select.req         responds Select.rsp
//   <- Linktest.req       responds Linktest.rsp
//   <- S2F41 remote cmd   responds S2F42 (ack), then later S6F11 completion
//   -> S6F11 event report  carrier arrived / departed / tool state / complete
//
// >>> PLACEHOLDER: a real controller's CEIDs and report variable IDs come from
//     its GEM compliance statement. The ones here are ours. When you get the
//     vendor list, change them HERE and in hsms.hpp — nowhere else.

#include "fab/hsms.hpp"
#include "fab/secs2.hpp"

#include <arpa/inet.h>
#include <atomic>
#include <csignal>
#include <cstring>
#include <iostream>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <random>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

using namespace fab;

namespace {

std::atomic<bool> g_stop{false};
void on_sig(int) { g_stop = true; }

std::string arg(int c, char** v, const std::string& k, const std::string& d) {
    const std::string p = k + "=";
    for (int i = 1; i < c; ++i) {
        std::string a = v[i];
        if (a.rfind(p, 0) == 0) return a.substr(p.size());
        if (a == k && i + 1 < c) return v[i + 1];
    }
    return d;
}

// ---- HSMS frame helpers ---------------------------------------------------

void put32(std::vector<uint8_t>& v, uint32_t x) {
    v.push_back((x >> 24) & 0xFF); v.push_back((x >> 16) & 0xFF);
    v.push_back((x >> 8) & 0xFF);  v.push_back(x & 0xFF);
}

std::vector<uint8_t> frame(uint8_t b2, uint8_t b3, hsms::SType st,
                           uint32_t sysbytes, const std::vector<uint8_t>& body) {
    std::vector<uint8_t> f;
    put32(f, static_cast<uint32_t>(10 + body.size()));
    f.push_back(0); f.push_back(0);          // session id
    f.push_back(b2); f.push_back(b3);
    f.push_back(0);                          // PType = SECS-II
    f.push_back(static_cast<uint8_t>(st));
    put32(f, sysbytes);
    f.insert(f.end(), body.begin(), body.end());
    return f;
}

std::vector<uint8_t> data_frame(uint8_t stream, uint8_t function, bool wbit,
                                uint32_t sysbytes,
                                const secs2::ItemPtr& item) {
    auto body = item ? secs2::encode(item) : std::vector<uint8_t>{};
    return frame(static_cast<uint8_t>(stream | (wbit ? 0x80 : 0)), function,
                 hsms::SType::DataMessage, sysbytes, body);
}

bool send_all(int fd, const std::vector<uint8_t>& b) {
    std::size_t sent = 0;
    while (sent < b.size()) {
        const ssize_t n = ::send(fd, b.data() + sent, b.size() - sent, MSG_NOSIGNAL);
        if (n <= 0) return false;
        sent += static_cast<std::size_t>(n);
    }
    return true;
}

// ---- the simulated fab floor ---------------------------------------------

struct Sim {
    std::mt19937 rng{7};
    uint32_t     sysbytes = 1000;
    int          lot_seq  = 0;

    std::vector<std::string> tools = {
        "ETCH_11","ETCH_12","ETCH_13","FURN_02","FURN_03","CVD_07","CVD_08",
        "LITHO_03","LITHO_04","CD_SEM_01","PROBE_21","PROBE_22"};
    std::vector<std::pair<std::string,std::string>> mix = {
        {"AUTOMOTIVE_MCU_01","M1_EXPOSE"}, {"AUTOMOTIVE_MCU_01","POLY_ETCH"},
        {"AUTOMOTIVE_MCU_01","GATE_OX"},   {"AUTOMOTIVE_MCU_01","SORT_HOT"},
        {"COMMODITY_LOGIC_09","GATE_OX"},  {"COMMODITY_LOGIC_09","NITRIDE"},
        {"COMMODITY_LOGIC_09","CD_MEASURE"}};

    // S6F11: L{ DATAID, CEID, L{ L{ RPTID, L{ variables... } } } }
    // This is the standard event-report shape. Keep it: real controllers
    // follow it, and the adapter's decoder is written against it.
    std::vector<uint8_t> carrier_arrived() {
        std::uniform_int_distribution<int> pick(0, (int)mix.size() - 1);
        std::uniform_real_distribution<double> pr(0.8, 5.0), sl(300.0, 9000.0);
        const auto& [prod, recipe] = mix[pick(rng)];
        const std::string lot = "LOT_" + std::to_string(2000 + lot_seq++);
        const std::string ret = (recipe == "M1_EXPOSE") ? "RET_M1_77" : "";

        auto rpt = secs2::Item::L({
            secs2::Item::U4(1),                        // RPTID
            secs2::Item::L({
                secs2::Item::A(lot),                   // CARRIERID
                secs2::Item::A(prod),                  // PRODUCTID
                secs2::Item::A(recipe),                // PPID
                secs2::Item::A(ret),                   // RETICLEID
                secs2::Item::U4(25),                   // WAFERCOUNT
                secs2::Item::U4((uint32_t)(pr(rng) * 100)),   // PRIORITY x100
                secs2::Item::U4((uint32_t)sl(rng)),          // QTIMESLACK sec
            }),
        });
        auto body = secs2::Item::L({
            secs2::Item::U4(1),
            secs2::Item::U4(hsms::sf::kCeidCarrierArrived),
            secs2::Item::L({rpt}),
        });
        return data_frame(6, 11, true, ++sysbytes, body);
    }

    std::vector<uint8_t> tool_state(const std::string& tool, bool online) {
        auto rpt = secs2::Item::L({
            secs2::Item::U4(3),
            secs2::Item::L({secs2::Item::A(tool), secs2::Item::U1(online ? 1 : 0)}),
        });
        auto body = secs2::Item::L({
            secs2::Item::U4(1),
            secs2::Item::U4(hsms::sf::kCeidToolStateChange),
            secs2::Item::L({rpt}),
        });
        return data_frame(6, 11, true, ++sysbytes, body);
    }

    std::vector<uint8_t> process_complete(const std::string& lot,
                                          const std::string& tool) {
        auto rpt = secs2::Item::L({
            secs2::Item::U4(4),
            secs2::Item::L({secs2::Item::A(lot), secs2::Item::A(tool)}),
        });
        auto body = secs2::Item::L({
            secs2::Item::U4(1),
            secs2::Item::U4(hsms::sf::kCeidProcessComplete),
            secs2::Item::L({rpt}),
        });
        return data_frame(6, 11, true, ++sysbytes, body);
    }
};

// Lots the controller has been told to move, pending completion.
struct Pending {
    std::string lot, tool;
    std::chrono::steady_clock::time_point done_at;
};

void serve(int fd, double event_hz, double time_scale) {
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    Sim sim;
    bool selected = false;
    std::vector<uint8_t> rx;
    std::vector<Pending> pending;
    auto last_event = std::chrono::steady_clock::now();
    auto last_down  = last_event;
    std::uniform_int_distribution<int> pick_tool(0, (int)sim.tools.size() - 1);
    std::vector<std::string> downed;

    std::cout << "[hsms-sim] client connected\n";

    while (!g_stop.load()) {
        // --- read whatever is available -----------------------------------
        uint8_t buf[8192];
        struct timeval tv { 0, 50 * 1000 };
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        const ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
        if (n == 0) { std::cout << "[hsms-sim] client closed\n"; break; }
        if (n > 0) rx.insert(rx.end(), buf, buf + n);

        // --- frame reassembly ---------------------------------------------
        while (rx.size() >= 4) {
            const uint32_t plen = (rx[0] << 24) | (rx[1] << 16) | (rx[2] << 8) | rx[3];
            if (plen < 10 || rx.size() < 4 + plen) break;
            const uint8_t* h = &rx[4];
            const auto st = static_cast<hsms::SType>(h[5]);
            const uint32_t sb = (h[6] << 24) | (h[7] << 16) | (h[8] << 8) | h[9];

            if (st == hsms::SType::SelectReq) {
                send_all(fd, frame(0, 0, hsms::SType::SelectRsp, sb, {}));
                selected = true;
                std::cout << "[hsms-sim] SELECTED\n";
            } else if (st == hsms::SType::LinktestReq) {
                send_all(fd, frame(0, 0, hsms::SType::LinktestRsp, sb, {}));
            } else if (st == hsms::SType::DataMessage) {
                const uint8_t s = h[2] & 0x7F, f = h[3];
                if (s == 2 && f == 41) {          // remote command: TRANSFER
                    std::string lot, dest;
                    try {
                        std::vector<uint8_t> body(h + 10, h + plen);
                        auto it = secs2::decode(body);
                        // L{ RCMD, L{ L{CPNAME,CPVAL}, L{CPNAME,CPVAL} } }
                        if (it && it->size() >= 2) {
                            const auto& params = it->at(1);
                            for (std::size_t i = 0; i < params->size(); ++i) {
                                const auto& kv = params->at(i);
                                if (kv->size() < 2) continue;
                                const std::string k = kv->at(0)->as_ascii();
                                const std::string v = kv->at(1)->as_ascii();
                                if (k == "CARRIERID") lot = v;
                                if (k == "DEST")      dest = v;
                            }
                        }
                    } catch (const secs2::DecodeError& e) {
                        std::cout << "[hsms-sim] decode error: " << e.what() << "\n";
                    }
                    // S2F42: HCACK = 0 (accepted)
                    send_all(fd, data_frame(2, 42, false, sb,
                                            secs2::Item::L({secs2::Item::U1(0)})));
                    if (!lot.empty() && !dest.empty()) {
                        std::cout << "[hsms-sim] S2F41 TRANSFER " << lot
                                  << " -> " << dest << "\n";
                        pending.push_back({lot, dest,
                            std::chrono::steady_clock::now() +
                            std::chrono::milliseconds((int)(600 * time_scale * 1000))});
                    }
                }
            }
            rx.erase(rx.begin(), rx.begin() + 4 + plen);
        }

        if (!selected) continue;
        const auto now = std::chrono::steady_clock::now();

        // --- push carrier-arrived events ----------------------------------
        if (std::chrono::duration<double>(now - last_event).count() > 1.0 / event_hz) {
            send_all(fd, sim.carrier_arrived());
            last_event = now;
        }

        // --- occasional tool up/down --------------------------------------
        if (std::chrono::duration<double>(now - last_down).count() > 8.0) {
            if (downed.empty()) {
                const std::string t = sim.tools[pick_tool(sim.rng)];
                downed.push_back(t);
                send_all(fd, sim.tool_state(t, false));
                std::cout << "[hsms-sim] tool DOWN " << t << "\n";
            } else {
                send_all(fd, sim.tool_state(downed.front(), true));
                std::cout << "[hsms-sim] tool UP   " << downed.front() << "\n";
                downed.erase(downed.begin());
            }
            last_down = now;
        }

        // --- complete anything whose process time elapsed -------------------
        for (auto it = pending.begin(); it != pending.end(); ) {
            if (it->done_at <= now) {
                send_all(fd, sim.process_complete(it->lot, it->tool));
                it = pending.erase(it);
            } else ++it;
        }
    }
    ::close(fd);
}

} // namespace

int main(int argc, char** argv) {
    const std::string listen = arg(argc, argv, "--listen", "0.0.0.0:5000");
    const double hz    = std::atof(arg(argc, argv, "--event-hz", "3.0").c_str());
    const double scale = std::atof(arg(argc, argv, "--time-scale", "0.002").c_str());

    const auto colon = listen.rfind(':');
    const std::string host = listen.substr(0, colon);
    const int port = std::atoi(listen.substr(colon + 1).c_str());

    std::signal(SIGINT, on_sig);
    std::signal(SIGTERM, on_sig);
    std::signal(SIGPIPE, SIG_IGN);

    const int srv = ::socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = (host == "0.0.0.0") ? INADDR_ANY : inet_addr(host.c_str());
    if (::bind(srv, (sockaddr*)&a, sizeof(a)) < 0) {
        std::cerr << "bind failed on " << listen << "\n"; return 1;
    }
    ::listen(srv, 4);

    std::cout << "hsms-sim  zone=0 equipment\n"
              << "  listening " << listen << " (HSMS passive entity)\n"
              << "  carrier events " << hz << "/s, time scale " << scale << "\n";

    while (!g_stop.load()) {
        struct timeval tv { 1, 0 };
        setsockopt(srv, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        const int fd = ::accept(srv, nullptr, nullptr);
        if (fd < 0) continue;
        serve(fd, hz, scale);
    }
    ::close(srv);
    return 0;
}
