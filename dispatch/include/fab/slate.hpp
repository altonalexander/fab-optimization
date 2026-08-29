#pragma once
// fab/slate.hpp — the immutable plan handed from the planner to the fast path.
//
// This is the C++ equivalent of Rust's ArcSwap: the planning thread builds a
// fresh Slate, then swaps a shared_ptr. Readers take a copy of the pointer and
// are guaranteed the object stays alive for as long as they hold it. No locks,
// no reader/writer contention, no allocation on the read path.

#include "fab/machine_config.hpp"

#include <atomic>
#include <chrono>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace fab {

// Pre-computed instruction for one lot. POD-ish and cheap to copy.
struct RouteToken {
    ToolId primary;
    ToolId alternate;
    uint32_t rank = 0;
    double  expected_process_s = 0.0;
};

struct Slate {
    uint64_t cycle_id = 0;
    std::chrono::steady_clock::time_point built_at{};
    std::unordered_map<std::string, RouteToken> tokens;   // lot_id -> route
    std::unordered_map<ToolId, DispatchSlice>   tools;    // flattened tool view

    double age_s() const {
        return std::chrono::duration<double>(
            std::chrono::steady_clock::now() - built_at).count();
    }
};

using SlatePtr = std::shared_ptr<const Slate>;

class SlatePublisher {
public:
    SlatePublisher() {
        auto empty = std::make_shared<Slate>();
        empty->built_at = std::chrono::steady_clock::now();
        std::atomic_store(&current_, SlatePtr(empty));
    }

    // Planner thread only.
    void publish(std::shared_ptr<Slate> s) {
        s->built_at = std::chrono::steady_clock::now();
        std::atomic_store(&current_, SlatePtr(std::move(s)));
        published_.fetch_add(1, std::memory_order_relaxed);
    }

    // Fast path. One atomic load + refcount bump.
    SlatePtr get() const { return std::atomic_load(&current_); }

    uint64_t publish_count() const {
        return published_.load(std::memory_order_relaxed);
    }

private:
    // NOTE: std::atomic_load/store on shared_ptr is deprecated in C++20 in
    // favour of std::atomic<std::shared_ptr<T>>. Kept here for wide toolchain
    // support; switch when your fab build image is on a new enough libstdc++.
    SlatePtr              current_;
    std::atomic<uint64_t> published_{0};
};

} // namespace fab
