#pragma once
// fab/transport.hpp — event bus abstraction.
//
// Two implementations behind one interface:
//   * KafkaProducer / KafkaConsumer  — real librdkafka, compiled when
//     FAB_HAVE_RDKAFKA is defined (-DFAB_HAVE_RDKAFKA, link -lrdkafka++).
//   * InMemoryBus                    — same semantics, single process.
//     Lets the whole pipeline run in CI and on a laptop with no broker.
//
// The dispatcher only ever sees the interface, so the fast path is identical
// either way.

#include "fab/events.hpp"

#include <atomic>
#include <condition_variable>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace fab {

class Producer {
public:
    virtual ~Producer() = default;
    // key drives partitioning; MUST be lot_id or tool_id to preserve ordering.
    virtual void send(const std::string& topic,
                      const std::string& key,
                      const std::string& payload) = 0;
    virtual void flush() {}
};

class Consumer {
public:
    virtual ~Consumer() = default;
    // Blocking poll with timeout. Returns false on timeout/shutdown.
    virtual bool poll(std::string& topic_out,
                      std::string& payload_out,
                      int timeout_ms) = 0;
};

// ---------------------------------------------------------------------------
// In-memory bus: a broker stand-in. Multi-producer, multi-consumer, FIFO.
// ---------------------------------------------------------------------------

class InMemoryBus {
public:
    struct Record { std::string topic, key, payload; };

    void publish(Record r) {
        {
            std::lock_guard<std::mutex> lk(m_);
            q_.push_back(std::move(r));
        }
        cv_.notify_one();
    }

    bool take(Record& out, int timeout_ms) {
        std::unique_lock<std::mutex> lk(m_);
        if (!cv_.wait_for(lk, std::chrono::milliseconds(timeout_ms),
                          [&]{ return !q_.empty() || closed_; }))
            return false;
        if (q_.empty()) return false;
        out = std::move(q_.front());
        q_.pop_front();
        return true;
    }

    void close() {
        { std::lock_guard<std::mutex> lk(m_); closed_ = true; }
        cv_.notify_all();
    }

    std::size_t depth() {
        std::lock_guard<std::mutex> lk(m_);
        return q_.size();
    }

private:
    std::mutex              m_;
    std::condition_variable cv_;
    std::deque<Record>      q_;
    bool                    closed_ = false;
};

class InMemoryProducer : public Producer {
public:
    explicit InMemoryProducer(InMemoryBus& bus) : bus_(bus) {}
    void send(const std::string& topic, const std::string& key,
              const std::string& payload) override {
        bus_.publish({topic, key, payload});
    }
private:
    InMemoryBus& bus_;
};

class InMemoryConsumer : public Consumer {
public:
    explicit InMemoryConsumer(InMemoryBus& bus) : bus_(bus) {}
    bool poll(std::string& topic_out, std::string& payload_out,
              int timeout_ms) override {
        InMemoryBus::Record r;
        if (!bus_.take(r, timeout_ms)) return false;
        topic_out   = std::move(r.topic);
        payload_out = std::move(r.payload);
        return true;
    }
private:
    InMemoryBus& bus_;
};

// ---------------------------------------------------------------------------
// Kafka implementation.
//
// >>> PLACEHOLDER: bodies are sketched against the librdkafka C++ API but are
//     compiled out by default. To enable:
//       apt install librdkafka-dev
//       g++ -DFAB_HAVE_RDKAFKA ... -lrdkafka++ -lrdkafka
//
//     Production settings you MUST set and that are not defaulted here:
//       enable.idempotence=true        (no duplicate lot events on retry)
//       acks=all
//       compression.type=lz4
//       max.in.flight.requests.per.connection=5
//       linger.ms=1                    (latency over throughput on this path)
//       isolation.level=read_committed (consumer)
//       enable.auto.commit=false       (commit after state is applied, not before)
//     Plus SASL/SSL config for the fab network zone.
// ---------------------------------------------------------------------------

#ifdef FAB_HAVE_RDKAFKA
#include <librdkafka/rdkafkacpp.h>

class KafkaProducer : public Producer {
public:
    KafkaProducer(const std::string& brokers) {
        std::string err;
        std::unique_ptr<RdKafka::Conf> conf(
            RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL));
        conf->set("bootstrap.servers", brokers, err);
        conf->set("enable.idempotence", "true", err);
        conf->set("acks", "all", err);
        conf->set("linger.ms", "1", err);
        conf->set("compression.type", "lz4", err);
        p_.reset(RdKafka::Producer::create(conf.get(), err));
        if (!p_) throw std::runtime_error("kafka producer: " + err);
    }

    void send(const std::string& topic, const std::string& key,
              const std::string& payload) override {
        RdKafka::ErrorCode ec = p_->produce(
            topic, RdKafka::Topic::PARTITION_UA,
            RdKafka::Producer::RK_MSG_COPY,
            const_cast<char*>(payload.data()), payload.size(),
            key.data(), key.size(), 0, nullptr);
        // >>> PLACEHOLDER: on ERR__QUEUE_FULL, apply backpressure rather than
        //     dropping. Dropping a LotComplete silently leaks tool capacity.
        (void)ec;
        p_->poll(0);
    }

    void flush() override { p_->flush(5000); }

private:
    std::unique_ptr<RdKafka::Producer> p_;
};

class KafkaConsumer : public Consumer {
public:
    KafkaConsumer(const std::string& brokers, const std::string& group,
                  const std::vector<std::string>& topics) {
        std::string err;
        std::unique_ptr<RdKafka::Conf> conf(
            RdKafka::Conf::create(RdKafka::Conf::CONF_GLOBAL));
        conf->set("bootstrap.servers", brokers, err);
        conf->set("group.id", group, err);
        conf->set("enable.auto.commit", "false", err);
        conf->set("isolation.level", "read_committed", err);
        conf->set("auto.offset.reset", "latest", err);   // fab state is "now"
        c_.reset(RdKafka::KafkaConsumer::create(conf.get(), err));
        if (!c_) throw std::runtime_error("kafka consumer: " + err);
        c_->subscribe(topics);
    }

    bool poll(std::string& topic_out, std::string& payload_out,
              int timeout_ms) override {
        std::unique_ptr<RdKafka::Message> msg(c_->consume(timeout_ms));
        if (!msg || msg->err() != RdKafka::ERR_NO_ERROR) return false;
        topic_out   = msg->topic_name();
        payload_out.assign(static_cast<const char*>(msg->payload()), msg->len());
        return true;
    }

    // Commit only AFTER the event has been applied to FabState.
    void commit() { c_->commitSync(); }

private:
    std::unique_ptr<RdKafka::KafkaConsumer> c_;
};
#endif // FAB_HAVE_RDKAFKA

} // namespace fab
