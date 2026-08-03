# Binance fixtures — provenance

- `klines_btcusdt_h1.json`: shape per Binance spot `GET /api/v3/klines` docs; values modeled on real BTCUSDT H1 candles (2024-01-01 00:00–03:00 UTC), hand-checked for OHLC sanity. No PII; sizes bounded (S0.1 fixture rules).
- `exchange_info.json`: minimal `GET /api/v3/exchangeInfo` slice — one non-USDT pair (filtered out) and one non-TRADING status (maps to non-trading) to exercise both filters.

Recorded fixtures added later must note capture date + endpoint here.
