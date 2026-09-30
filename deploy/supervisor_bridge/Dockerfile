FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN useradd --uid 10001 --create-home trips-supervisor
USER 10001

ENTRYPOINT ["python", "deploy/supervisor_bridge/bridge.py"]
