#!/bin/bash
# Topic provisioning. Partition counts and retention are per-topic decisions,
# not defaults — each one below is deliberate.
set -e
K=/usr/bin/kafka-topics
B=kafka:9092

# Lot events: partitioned by lot_id so per-lot ordering is guaranteed.
# 12 partitions = headroom for 12 parallel ingest consumers.
$K --bootstrap-server $B --create --if-not-exists \
   --topic fab.lot.events --partitions 12 --replication-factor 1 \
   --config retention.ms=604800000 --config compression.type=lz4

# Tool events: partitioned by tool_id. Low volume, ordering critical.
$K --bootstrap-server $B --create --if-not-exists \
   --topic fab.tool.events --partitions 6 --replication-factor 1 \
   --config retention.ms=604800000

# Decision log: the audit trail. "Why did this lot miss Q-time" is answered
# from here, so retention is long and it is never compacted.
$K --bootstrap-server $B --create --if-not-exists \
   --topic fab.dispatch.decisions --partitions 12 --replication-factor 1 \
   --config retention.ms=7776000000

# Tool state snapshot: compacted, so a restarting consumer gets current state
# without replaying a week of history.
$K --bootstrap-server $B --create --if-not-exists \
   --topic fab.tool.state --partitions 6 --replication-factor 1 \
   --config cleanup.policy=compact

echo "--- topics ---"
$K --bootstrap-server $B --list
