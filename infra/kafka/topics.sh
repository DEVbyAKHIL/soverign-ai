#!/bin/bash
# Idempotent topic creation (run by the one-shot kafka-init service).
# retention.bytes is per partition. Caps keep the local broker's disk use bounded:
#   raw 6 x 256 MiB, valid 6 x 256 MiB, dead-letter 1 x 256 MiB  (~3.25 GiB worst case)
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka:9092}"
TOPICS=/opt/kafka/bin/kafka-topics.sh
MIB256=268435456

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
  # --if-not-exists keeps existing topics; apply the configured retention to them as well.
  /opt/kafka/bin/kafka-configs.sh --bootstrap-server "$BOOTSTRAP" --alter --entity-type topics \
    --entity-name "$name" --add-config "retention.ms=$retention_ms" > /dev/null
  echo "topic $name ready ($actual partitions, retention ${retention_ms} ms)"
}

HOUR=3600000
create governance.events.raw 6 $(( ${KAFKA_RAW_RETENTION_HOURS:-168} * HOUR ))
create governance.events.valid 6 $(( ${KAFKA_VALID_RETENTION_HOURS:-72} * HOUR ))
create governance.events.dead-letter 1 $(( ${KAFKA_DLQ_RETENTION_HOURS:-336} * HOUR ))
