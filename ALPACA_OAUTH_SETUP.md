# Trip's — Alpaca OAuth connection

Trip's now supports Alpaca's authorization-code OAuth path for a persistent broker connection.

## One-time operator setup
1. Register Trip's as an OAuth application in the Alpaca dashboard.
2. Configure a HTTPS callback URL controlled by Trip's.
3. Put ALPACA_OAUTH_CLIENT_ID and ALPACA_OAUTH_CLIENT_SECRET only in the deployment secret store.
4. Set ALPACA_OAUTH_REDIRECT_URI to the exact registered callback.
5. Start authorization with env=live and scope=trading.
6. The callback MUST verify the one-time state value before exchanging the authorization code.
7. Store the returned access token encrypted at rest; never log it or commit it.

## Execution boundary
OAuth grants transport authorization only. It does not bypass Trip's execution gates, reconciliation, limits, kill switch, frozen-core verification, or explicit live activation.

## Current blocker
A real authorization URL cannot be generated until Alpaca issues Trip's OAuth client_id and client_secret and a deployed HTTPS callback exists. Never substitute fabricated values.
