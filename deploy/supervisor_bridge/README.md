# Hosted supervisor sidecar

This directory implements the durable transport that was still missing between Trip's existing
Supervisor Relay outbox and a hosted ChatGPT supervisor.

## Authority boundary

The sidecar is **not** an order path.

It:

1. reads `supervisor_outbox.json`;
2. sends each already-sanitized evidence packet to the OpenAI Responses API;
3. requests strict JSON-schema counsel;
4. validates the counsel locally against Trip's existing `supervisor_counsel.py` rules;
5. writes the accepted counsel to durable storage;
6. only after durable storage, writes a signed acknowledgement **receipt**.

It does **not** apply the receipt to runtime. It has no broker adapter, no submit/cancel method, no
capital-release path, and no resume authority.

A separate authoritative Trip's process may later inspect and apply a valid receipt using the
existing Supervisor Relay acknowledgement contract.

## Required secrets

Supply secrets only through the host secret store:

- `OPENAI_API_KEY`
- `TRIPS_SUPERVISOR_MODEL` — explicit model ID; there is deliberately no guessed default
- `TRIPS_SUPERVISOR_ACK_KEY` — at least 32 characters; must match the authoritative relay

Never commit any of these values.

## Run

From repository root:

```sh
python deploy/supervisor_bridge/bridge.py \
  --outbox /var/lib/trips/runtime/supervisor_outbox.json \
  --state-dir /var/lib/trips/supervisor
```

Without `TRIPS_SUPERVISOR_ACK_KEY`, accepted counsel is still durably stored but no acknowledgement
receipt is created. Without the API key/model, delivery fails closed and the outbox remains pending.

## Container

```sh
docker build -f deploy/supervisor_bridge/Dockerfile -t trips-supervisor-sidecar .
docker run --rm \
  -e OPENAI_API_KEY \
  -e TRIPS_SUPERVISOR_MODEL \
  -e TRIPS_SUPERVISOR_ACK_KEY \
  -v /srv/trips/runtime:/runtime:ro \
  -v /srv/trips/supervisor:/state \
  trips-supervisor-sidecar \
  --outbox /runtime/supervisor_outbox.json --state-dir /state
```

The model is intentionally given no tools. Packet content is labelled untrusted data, structured
output is schema-constrained, current-market claims are refused, and any order-shaped counsel is
rejected locally.

## Validate

```sh
python -m unittest discover -s deploy/supervisor_bridge -p 'test_*.py' -v
```
