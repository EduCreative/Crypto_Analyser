"""
Crypto Analyser — local web app for Binance research.
Backend does all the network calls (so there's no browser CORS problem),
streams batch backtest / live scan progress via Server-Sent Events, and
caches the full symbol list to disk so you're not re-fetching it every run.

Install once:
    pip install flask requests

Run:
    python app.py

Then open the URL it prints (default http://127.0.0.1:5055).
"""

import os
import json
import time
import threading
from datetime import datetime, timedelta

from flask import Flask, Response, request, jsonify, render_template, stream_with_context
import requests

app = Flask(__name__)

BASE_URL = "https://api.binance.com/api/v3"
INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symbols_cache.json")
CACHE_MAX_AGE = timedelta(days=3)

# ---- API weight tracking (rolling per-minute, not a daily quota) ----
_usage_lock = threading.Lock()
_api_usage = {"used_1m": None, "limit_1m": 1200, "interval_label": "per minute"}


def _record_usage(headers):
    used = headers.get("X-MBX-USED-WEIGHT-1M")
    if used is not None:
        with _usage_lock:
            _api_usage["used_1m"] = int(used)


def get_api_usage():
    with _usage_lock:
        return dict(_api_usage)


# ==================== Binance data ====================

def fetch_klines(symbol, interval, limit=500, start_time=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = int(start_time)
    r = requests.get(f"{BASE_URL}/klines", params=params, timeout=10)
    r.raise_for_status()
    _record_usage(r.headers)
    data = r.json()
    return [
        {"open_time": k[0], "open": float(k[1]), "high": float(k[2]),
         "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
        for k in data
    ]


def fetch_klines_range(symbol, interval, days):
    interval_ms = INTERVAL_MS[interval]
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    all_candles = []
    cursor = start
    while cursor < end:
        batch = fetch_klines(symbol, interval, limit=1000, start_time=cursor)
        if not batch:
            break
        all_candles.extend(batch)
        last_time = batch[-1]["open_time"]
        if last_time <= cursor:
            break
        cursor = last_time + interval_ms
        if len(batch) < 1000:
            break
        time.sleep(0.1)
    return [c for c in all_candles if c["open_time"] >= start]


def fetch_ticker(symbol):
    r = requests.get(f"{BASE_URL}/ticker/24hr", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    _record_usage(r.headers)
    return r.json()


def fetch_usdt_symbols():
    r = requests.get(f"{BASE_URL}/exchangeInfo", timeout=10)
    r.raise_for_status()
    _record_usage(r.headers)
    data = r.json()
    out = []
    for s in data.get("symbols", []):
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
            out.append({"symbol": s["symbol"], "name": s["baseAsset"]})
    out.sort(key=lambda x: x["symbol"])
    for rl in data.get("rateLimits", []):
        if rl.get("rateLimitType") == "REQUEST_WEIGHT":
            with _usage_lock:
                _api_usage["limit_1m"] = rl.get("limit", 1200)
                interval_num = rl.get("intervalNum", 1)
                interval = rl.get("interval", "MINUTE")
                _api_usage["interval_label"] = (f"per {interval_num} {interval.lower()}"
                                                 if interval_num != 1 else f"per {interval.lower()}")
            break
    return out


def load_cached_symbols():
    try:
        with open(CACHE_PATH, "r") as f:
            payload = json.load(f)
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        is_fresh = (datetime.now() - fetched_at) < CACHE_MAX_AGE
        return payload["symbols"], is_fresh, fetched_at
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None, False, None


def save_cached_symbols(symbols):
    payload = {"fetched_at": datetime.now().isoformat(), "symbols": symbols}
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(payload, f)
    except OSError:
        pass


# ==================== indicators ====================

def ema(values, period):
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values, period=14):
    out = [None] * len(values)
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    gains /= period
    losses /= period
    out[period] = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        g, l = max(d, 0), max(-d, 0)
        gains = (gains * (period - 1) + g) / period
        losses = (losses * (period - 1) + l) / period
        out[i] = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
    return out


def atr(highs, lows, closes, period=14):
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    out = [None] * len(tr)
    s = sum(tr[:period])
    out[period - 1] = s / period
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def macd_hist(values):
    e12, e26 = ema(values, 12), ema(values, 26)
    line = [a - b for a, b in zip(e12, e26)]
    signal = ema(line, 9)
    return [a - b for a, b in zip(line, signal)]


def score_bar(closes, highs, lows, i):
    if i < 55:
        return None
    wc, wh, wl = closes[: i + 1], highs[: i + 1], lows[: i + 1]
    r = rsi(wc, 14)[-1]
    hist = macd_hist(wc)
    last_hist, prev_hist = hist[-1], hist[-2]
    e20, e50 = ema(wc, 20)[-1], ema(wc, 50)[-1]
    last_close = wc[-1]
    a = atr(wh, wl, wc, 14)[-1]

    bull, bear = 0, 0
    if r < 30:
        bull += 1
    elif r > 70:
        bear += 1
    if last_hist > 0 and prev_hist <= 0:
        bull += 1
    elif last_hist < 0 and prev_hist >= 0:
        bear += 1
    if e20 > e50:
        bull += 1
    else:
        bear += 1
    if last_close > e20:
        bull += 1
    else:
        bear += 1

    total = bull + bear
    bull_pct = round((bull / total) * 100) if total else 50
    return {"bull_pct": bull_pct, "atr": a, "close": last_close}


def backtest_core(symbol, interval, days, threshold=75, stop_atr=1.5, target_atr=2.0):
    candles = fetch_klines_range(symbol, interval, days)
    if len(candles) < 100:
        raise ValueError("Not enough historical data returned")

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    trades = []
    position = None

    for i in range(55, len(closes) - 1):
        result = score_bar(closes, highs, lows, i)
        if result is None or result["atr"] is None:
            continue

        if position:
            hi, lo = highs[i], lows[i]
            if position["side"] == "long":
                if lo <= position["stop"]:
                    trades.append({"side": "long", "pnl_pct": (position["stop"] / position["entry"] - 1) * 100})
                    position = None
                elif hi >= position["target"]:
                    trades.append({"side": "long", "pnl_pct": (position["target"] / position["entry"] - 1) * 100})
                    position = None
            else:
                if hi >= position["stop"]:
                    trades.append({"side": "short", "pnl_pct": (1 - position["stop"] / position["entry"]) * 100})
                    position = None
                elif lo <= position["target"]:
                    trades.append({"side": "short", "pnl_pct": (1 - position["target"] / position["entry"]) * 100})
                    position = None

        if not position:
            if result["bull_pct"] >= threshold:
                entry = closes[i]
                a = result["atr"]
                position = {"side": "long", "entry": entry,
                            "stop": entry - stop_atr * a, "target": entry + target_atr * a}
            elif result["bull_pct"] <= (100 - threshold):
                entry = closes[i]
                a = result["atr"]
                position = {"side": "short", "entry": entry,
                            "stop": entry + stop_atr * a, "target": entry - target_atr * a}

    if not trades:
        return {"symbol": symbol, "interval": interval, "trades": [], "n_trades": 0,
                "win_rate": None, "avg_win": None, "avg_loss": None,
                "expectancy": None, "total_pnl": None}

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    total_pnl = sum(t["pnl_pct"] for t in trades)
    expectancy = total_pnl / len(trades)

    return {
        "symbol": symbol, "interval": interval, "trades": trades, "n_trades": len(trades),
        "win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss,
        "expectancy": expectancy, "total_pnl": total_pnl,
    }


def live_scan_one(symbol, interval):
    candles = fetch_klines(symbol, interval, limit=100)
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    result = score_bar(closes, highs, lows, len(closes) - 1)
    ticker = fetch_ticker(symbol)
    change = float(ticker["priceChangePercent"])
    return {"symbol": symbol, "price": result["close"], "change": change,
            "bull_pct": result["bull_pct"], "atr": result["atr"]}


# ==================== routes ====================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/symbols")
def api_symbols():
    force = request.args.get("refresh") == "1"
    cached, is_fresh, fetched_at = load_cached_symbols()
    if cached and is_fresh and not force:
        return jsonify({"symbols": cached, "source": "cache", "fetched_at": fetched_at.isoformat()})
    try:
        symbols = fetch_usdt_symbols()
        save_cached_symbols(symbols)
        return jsonify({"symbols": symbols, "source": "live", "fetched_at": datetime.now().isoformat()})
    except Exception as e:
        if cached:
            return jsonify({"symbols": cached, "source": "stale_cache", "error": str(e),
                             "fetched_at": fetched_at.isoformat() if fetched_at else None})
        return jsonify({"symbols": [], "source": "error", "error": str(e)}), 502


@app.route("/api/usage")
def api_usage():
    return jsonify(get_api_usage())


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route("/api/backtest/stream")
def api_backtest_stream():
    symbols = request.args.get("symbols", "").split(",")
    intervals = request.args.get("intervals", "").split(",")
    symbols = [s for s in symbols if s]
    intervals = [i for i in intervals if i]
    days = int(request.args.get("days", 60))
    threshold = int(request.args.get("threshold", 75))
    stop_atr = float(request.args.get("stop_atr", 1.5))
    target_atr = float(request.args.get("target_atr", 2.0))

    def generate():
        combos = [(s, iv) for s in symbols for iv in intervals]
        for done, (symbol, interval) in enumerate(combos, start=1):
            try:
                result = backtest_core(symbol, interval, days, threshold, stop_atr, target_atr)
                yield _sse("row", result)
            except Exception as e:
                yield _sse("row_error", {"symbol": symbol, "interval": interval, "error": str(e)})
            yield _sse("progress", {"done": done, "total": len(combos)})
        yield _sse("done", {})

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/live/stream")
def api_live_stream():
    symbols = request.args.get("symbols", "").split(",")
    symbols = [s for s in symbols if s]
    interval = request.args.get("interval", "15m")

    def generate():
        for symbol in symbols:
            try:
                r = live_scan_one(symbol, interval)
                yield _sse("row", r)
            except Exception as e:
                yield _sse("row_error", {"symbol": symbol, "error": str(e)})
        yield _sse("done", {})

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5055"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"\nCrypto Analyser running — open http://{host}:{port} in your browser\n")
    app.run(host=host, port=port, debug=False, threaded=True)
