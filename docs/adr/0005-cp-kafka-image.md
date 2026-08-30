# 0005 — cp-kafka rather than apache/kafka

**Status:** Accepted, 2026-08-30. Small decision, recorded because rediscovering
it costs an hour.

---

## 1. What happened

The compose stack pinned `apache/kafka:3.9.0`. It had never been started. When
it finally was, it died at its storage-format step:

```
java.lang.IllegalArgumentException: requirement failed:
advertised.listeners cannot use the nonroutable meta-address 0.0.0.0
```

The compose configuration was not at fault. It reproduced on a bare
`docker run` with a fully routable `KAFKA_ADVERTISED_LISTENERS`, a single
combined broker+controller node, and nothing else — the image was not honouring
the environment at format time. `confluentinc/cp-kafka:7.7.1` accepts the
identical environment and starts.

## 2. The decision

Pin `confluentinc/cp-kafka:7.7.1`. Two things move with it:

- Binaries: `/opt/kafka/bin/kafka-topics.sh` becomes `/usr/bin/kafka-topics`,
  in both the healthcheck and `create-topics.sh`.
- cp-kafka requires an explicit `CLUSTER_ID` in KRaft mode; apache/kafka
  defaulted one. It must stay stable across restarts or the formatted log
  directory is rejected.

Verified: broker healthy, all four topics created with their intended partition
counts, and the real wire format round-trips producer → broker → consumer on
`data-net`.

## 3. What this assumes

That the fault is the image and not our configuration. The bare `docker run`
reproduction is the evidence. The underlying cause inside the image's
`configure` script was not chased to ground — it did not need to be, once a
working alternative accepted the same inputs.

## 4. How to know whether it is right

- **It is wrong if** the same failure appears on cp-kafka, which would mean the
  configuration was at fault after all and we changed the wrong variable.
- **Revisit if** a later `apache/kafka` tag is wanted for some other reason.
  The test is cheap: one `docker run` with the environment from
  `docker-compose.yml`, and see whether it formats.

## 5. Consequences, and the one that surprised us

`data-net` is declared `internal: true`. That is the zone model working — zone
2 has no egress and no ingress — and Docker enforces it by disabling external
connectivity on the network **entirely, including port publishing**.

A `ports:` entry on a service that is only on an internal network is accepted
into `HostConfig.PortBindings` and then silently never applied:
`NetworkSettings.Ports` stays empty, `docker port` prints nothing, and the host
gets connection refused with no error anywhere. That silence cost an hour and
was initially misdiagnosed as a Docker Desktop / WSL2 quirk.

Reaching the broker from the host therefore needs a second, non-internal
network, which `docker-compose.dev.yml` creates. That file is a real hole in
the isolation and stays out of the base compose for that reason; `make verify`
will flag the published port while it is in use, and it is right to.

In production the producer runs *inside* the data zone, so none of this exists
there.
