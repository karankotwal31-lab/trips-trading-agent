# Owner-supplied daily data

No market data is committed here.

Place one CSV per symbol in this directory, named `SYMBOL.csv`, with the exact columns:

```text
date,open,high,low,close,adj_close,volume
```

Also provide `manifest.sha256` with one line per CSV:

```text
<64-lowercase-hex-sha256>  SYMBOL.csv
```

The harness verifies hashes before parsing. Missing/mismatched manifests, malformed rows, non-finite
or non-positive prices, negative volume, duplicate/non-increasing dates, invalid OHLC geometry, an
insufficient valid universe, or insufficient common history fail closed. Fewer than 10 valid assets
or fewer than 15 years of common qualifying history returns `INSUFFICIENT_DATA`.
