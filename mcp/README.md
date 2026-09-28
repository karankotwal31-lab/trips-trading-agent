# Trip's + Alpaca hosted MCP

Alpaca now publishes OAuth-protected hosted Trading MCP endpoints:
- paper: https://paper-api.alpaca.markets/mcp
- live: https://api.alpaca.markets/mcp

This is preferable to exposing a self-hosted unauthenticated MCP service. Trip's remains the policy/risk boundary.

## Rollout
1. Connect and validate PAPER first.
2. Read account/positions/orders/market data and reconcile.
3. Exercise duplicate-order, cancel, disconnect, stale-data, daily-loss and kill-switch tests.
4. Only after all readiness checks pass may an operator separately authorize live connectivity.
5. Never commit Alpaca credentials or OAuth tokens.

## Authority
MCP transport does not grant Trip's research/student/evolution/supervisor engines execution authority. Any order-producing path must still pass the existing executor gates and reconciliation policy.

## ChatGPT availability
Alpaca's current official agentic repository documents hosted OAuth MCP support for Cursor, Claude Code and Codex and says support for additional clients is being worked on. Do not claim direct ChatGPT connectivity until the ChatGPT client exposes/accepts this Alpaca hosted MCP endpoint.
