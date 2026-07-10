# Crypto Analyser

A local research tool for Binance: batch backtesting, live setup scanning,
charts, and CSV export — running as a small web app on your own machine.

This is a research/screening aid, not a prediction engine. Nothing here
promises a win rate, and it should not be treated as financial advice.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Then open the URL it prints — by default:

```
http://127.0.0.1:5055
```

Leave the terminal window open while you use the app; closing it stops the
local server.

## Notes

- All Binance API calls happen from the Python backend, not your browser,
  so there's no CORS issue and no API key is needed (public endpoints only).
- The full list of Binance USDT trading pairs is cached to
  `symbols_cache.json` next to `app.py`, refreshed automatically every 3
  days, or on demand via the "⟳ Refresh list" button in the symbol picker.
- The API weight gauge reflects Binance's rolling per-minute request cap
  (not a daily quota — there isn't one for these endpoints).
- Batch backtests and live scans stream results row-by-row as they complete
  (Server-Sent Events), so you see progress rather than waiting on one big
  request.
