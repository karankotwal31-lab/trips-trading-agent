FROM python:3.12-slim

RUN useradd --create-home --uid 10001 trips-shadow
WORKDIR /app

COPY research /app/research

RUN mkdir -p /state /data/daily \
    && chown -R trips-shadow:trips-shadow /state /data /app

USER trips-shadow

VOLUME ["/state", "/data"]
CMD ["python", "research/shadow/runner.py", "--data-dir", "/data/daily", "--journal", "/state/journal.jsonl", "--heartbeat", "/state/heartbeat.json"]
