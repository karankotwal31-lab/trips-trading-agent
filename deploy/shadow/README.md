# Durable shadow deployment

WP3 is intentionally a **durable-host** workload, not a GitHub Actions cron. The journal and
heartbeat must survive process restarts and runner replacement.

## Host layout

```text
/opt/trips-trading-agent/                 read-only checked-out code
/var/lib/trips-shadow/data/daily/         owner-managed CSVs + manifest.sha256
/var/lib/trips-shadow/state/              persistent journal/heartbeat/forward report
```

The data-refresh mechanism is deliberately separate. It may copy owner-approved CSV files into the
data directory, but this repository stores no provider credentials or secrets.

## systemd

1. Create a locked-down `trips-shadow` OS user.
2. Copy the supplied service and timer to `/etc/systemd/system/`.
3. Ensure only the state/data tree is writable by that user.
4. Enable the timer:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now trips-shadow.timer
systemctl list-timers trips-shadow.timer
```

The service is `oneshot` and updates the shadow report after every attempted cycle. Invalid or
stale data yields a logged `NO_ENTRY`; it never becomes an order.

## Container option

Build from repository root:

```sh
docker build -f deploy/shadow/Dockerfile -t trips-shadow:local .
docker run --rm \
  -v /srv/trips-shadow/data:/data:ro \
  -v /srv/trips-shadow/state:/state \
  trips-shadow:local
```

The image runs as UID 10001 and contains no secrets. Back up the state directory as an append-only
audit artifact. A journal-chain verification failure is a hard stop until the owner investigates.
