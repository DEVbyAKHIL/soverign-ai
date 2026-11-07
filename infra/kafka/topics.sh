#!/bin/bash
# Idempotent topic creation (run by the one-shot kafka-init service).
# retention.bytes is per partition. Caps keep the local broker's disk use bounded:
#   raw 6 x 256 MiB, valid 6 x 256 MiB, dead-letter 1 x 256 MiB  (~3.25 GiB worst case)
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka:9092}"
TOPICS=/opt/kafka/bin/kafka-topics.sh
MIB256=268435456
DAY=86400000

create() {
  local name="$1" partitions="$2" retention_ms="$3"
  "$TOPICS" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
    --topic "$name" --partitions "$partitions" --replication-factor 1 \
    --config "retention.ms=$retention_ms" \
    --config "retention.bytes=$MIB256" \
    --config segment.bytes=67108864 \
    --config cleanup.policy=delete \
    --config min.insync.replicas=1
  local actual
  actual=$("$TOPICS" --bootstrap-server "$BOOTSTRAP" --describe --topic "$name" \
    | sed -n 's/.*PartitionCount: *\([0-9]*\).*/\1/p' | head -1)
  if [ "${actual:-0}" -lt "$partitions" ]; then
    echo "topic $name has $actual partitions, expected >= $partitions" >&2
    exit 1
  fi
  echo "topic $name ready ($actual partitions)"
}

create governance.events.raw 6 $((7 * DAY))
create governance.events.valid 6 $((3 * DAY))
create governance.events.dead-letter 1 $((14 * DAY))
