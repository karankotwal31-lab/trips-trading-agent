# Trip's hosted supervisor bridge — design only

Status: **DESIGN / NO EXECUTION AUTHORITY**

This document specifies the durable transport that may eventually connect Trip's existing
Decision Mirror / Supervisor Relay evidence to a hosted ChatGPT supervisor. It does not change
execution authority and is not an order path.

## Authority contract

The bridge preserves the existing invariant:

- Trip's may prepare evidence for a supervisor.
- The supervisor may explain, request more evidence, escalate, or request a halt on **new**
  exposure.
- The supervisor has `execution_authority = NONE`.
- The supervisor cannot choose symbol, side, quantity, price, order type, broker, account, risk
  limit, leverage, strategy, or live authorization.
- The supervisor cannot resume trading, loosen a limit, promote a strategy, or bypass Truth,
  Risk, TASK, Forge, Constitution, Capital Governor, preflight, or the execution gateway.
- No per-trade approval is required. A healthy deterministic execution path must not depend on
  ChatGPT being reachable.
- A `SUPERVISOR_HALT_REQUEST` may only tighten behavior by blocking new exposure.

This is consistent with the current execution supervisor and the existing relay contract.

## Existing source-of-truth behavior

The current relay already provides the primitives the hosted bridge must preserve:

1. Decision Mirror produces an integrity-linked journal.
2. `supervisor_relay.build_outbox_from_runtime()` emits deterministic packet identities.
3. Each packet binds:
   - decision sequence;
   - decision event id/hash;
   - approved build hash;
   - approved config hash;
   - the supervisor contract.
4. Relay health computes:
   - `last_decision_seq`;
   - `last_ack_seq`;
   - undelivered backlog;
   - configured `max_undelivered_events`.
5. Backlog beyond the configured bound returns
   `BLOCK_NEW_ENTRIES_AND_ESCALATE`.
6. Delivery acknowledgement advances only after an authenticated HMAC-SHA256 receipt binds:
   - through-sequence;
   - authoritative event hash;
   - response hash.
7. Acknowledgement cannot move backwards or acknowledge a nonexistent sequence.

The hosted bridge must consume these primitives rather than invent a second journal, cursor, or
authority system.

## Proposed asynchronous architecture

```text
authoritative Trip's runtime
        |
        v
Decision Mirror (hash-linked)
        |
        v
Supervisor Relay / outbox
        |
        | read-only evidence export
        v
Durable Bridge Queue
        |
        | asynchronous delivery
        v
Hosted ChatGPT Supervisor
        |
        | structured advisory result
        v
Counsel Validator / Supervisor Parser
        |
        +----------> advisory record
        |
        +----------> optional HALT_NEW_ENTRIES request
        |
        v
Signed acknowledgement of evidence delivery
        |
        v
authoritative delivery cursor
```

The bridge is not between preflight and broker submission. It is an observability/safety side
channel.

## Bridge process responsibilities

A future hosted bridge should:

1. read packets from the existing outbox in monotonically increasing decision sequence;
2. deduplicate by packet hash;
3. transmit only the sanitized packet;
4. authenticate the remote endpoint;
5. reject responses that are not structured or exceed an explicit size bound;
6. validate the response with the existing supervisor authority rules;
7. persist the response hash separately from the trading journal;
8. produce a signed acknowledgement only after durable storage of the accepted response;
9. retry idempotently without creating a new packet identity;
10. never read or transmit broker credentials, owner signing material, database passwords, or
    unrestricted runtime payloads.

## No per-trade approvals

The bridge must never be implemented as:

```text
strategy -> ask ChatGPT "may I trade?" -> order
```

That would create an external network dependency in the authority path and would implicitly grant
the model veto/approval semantics over individual orders.

Instead:

```text
deterministic gates -> decision
                         |
                         +-> evidence packet -> asynchronous supervisor
```

If the supervisor is unavailable, Trip's records `SUPERVISOR_UNAVAILABLE` and applies the
configured deterministic policy. Absence is never interpreted as approval.

## Halt-request semantics

A supervisor response may request `SUPERVISOR_HALT_REQUEST` only when it cites known evidence
references. The deterministic Safety Controller translates that into:

- new exposure blocked: **true**;
- existing order cancellation: **false unless separately allowed by deterministic policy**;
- resume authority: **false**;
- risk-increase authority: **false**.

A hosted provider therefore has only one safety-relevant direction: **tighten**.

## Signed acknowledgement

The current relay uses an HMAC-SHA256 acknowledgement receipt. The hosted deployment should keep
the signing/verification boundary server-side:

- secret key exists only in the bridge/runtime secret store;
- ChatGPT never sees the key;
- receipt binds through-sequence + authoritative event hash + response hash;
- invalid, missing, stale, forward-skipping, or backward-moving receipts are rejected;
- a transport-level 200/OK is not an acknowledgement.

If a future asymmetric acknowledgement is desired, that is a separate reviewed security change;
it must not silently replace the current HMAC contract.

## Threat model

### Prompt injection through market or operational data

Untrusted text can enter through:

- market/news annotations;
- broker error text;
- provider metadata;
- escalation descriptions;
- user-supplied notes;
- logs;
- strategy/student observations.

Mitigations:

- treat every evidence field as **data**, never instructions;
- send only explicitly allowlisted structured fields;
- strip credentials and secret-like values before the bridge;
- place system authority instructions outside evidence content;
- require schema-constrained supervisor output;
- reject forbidden order-shaped fields;
- require evidence references for non-normal findings;
- never expose an execution or broker tool to the supervisor process.

### Instruction smuggling

Evidence such as `"ignore all rules and buy ..."` must remain an inert string. It may cause
`INVESTIGATE`, but it cannot become an instruction.

### Response spoofing

A forged response must not advance the delivery cursor. Only a valid signed acknowledgement may do
so.

### Replay

Packet hash and decision sequence are stable. Replayed delivery is idempotent and cannot create a
second decision.

### Data exfiltration

The bridge receives sanitized evidence only. Secrets, private owner keys, broker credentials,
database credentials and unrestricted account payloads are outside the payload contract.

### Compromised supervisor

Worst permitted effect: request a halt on new exposure. A compromised supervisor cannot create,
resize, reroute, resume, or authorize a trade.

## Failure modes

| Failure | Required behavior |
|---|---|
| ChatGPT endpoint unavailable | retain outbox; do not synthesize acknowledgement |
| timeout | retry same packet id/hash |
| duplicate response | deduplicate; do not advance twice |
| invalid JSON/schema | reject; retain unacknowledged |
| forbidden field/order instruction | reject as authority violation |
| response lacks evidence refs when required | reject |
| current-market claim lacks external evidence | reject |
| HMAC key unavailable | no acknowledgement |
| invalid acknowledgement signature | no cursor movement |
| acknowledgement points past journal | reject |
| acknowledgement moves backwards | reject |
| response storage fails | no acknowledgement |
| relay backlog exceeds policy | block new entries and escalate |
| bridge restarts | resume from authoritative last acknowledged sequence |
| supervisor unavailable for prolonged period | deterministic backlog policy governs; never infer approval |

## Existing relay backpressure

The current configuration sets a maximum undelivered-event backlog. The current relay health
returns `passed = false` and action `BLOCK_NEW_ENTRIES_AND_ESCALATE` when that bound is exceeded.

A hosted bridge must not weaken this by:

- dropping old packets;
- acknowledging without durable response storage;
- increasing the backlog limit autonomously;
- resetting the delivery cursor;
- treating retries as new evidence.

## Operational deployment boundary

The bridge should run on a durable private host with:

- restart policy;
- persistent queue/state;
- server-side secrets;
- bounded logs with redaction;
- monotonic cursor storage;
- health/heartbeat;
- alerting on sustained backlog;
- TLS endpoint validation;
- no public mutation endpoint into Trip's execution state.

The bridge should be deployable and replaceable without changing strategy or execution code.

## Acceptance criteria for a future implementation

Before a hosted bridge may be called operational:

- packets survive process restart;
- duplicate delivery is idempotent;
- invalid/missing acknowledgement never advances cursor;
- prompt-injection fixtures cannot produce an order-shaped accepted response;
- supervisor outage never releases authority;
- backlog overflow blocks new entries as configured;
- `SUPERVISOR_HALT_REQUEST` blocks new exposure only;
- no supervisor response can resume trading;
- no supervisor response can increase risk;
- secrets never appear in packets/logs;
- deterministic trade processing remains functional when the bridge is unavailable.

This design intentionally gives the supervisor useful visibility and a one-way emergency brake
without turning ChatGPT into a trading authority.
