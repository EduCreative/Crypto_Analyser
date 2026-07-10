"""
Crypto Analyser — desktop research tool for Binance: batch backtesting,
live scanning, cached full symbol list, light/dark theme.
No API key needed.

Install once:
    pip install requests matplotlib

Run:
    python scalp_desk_gui.py
"""

import os
import json
import threading
import queue
import time
import csv
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import requests

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

APP_NAME = "Crypto Analyser"
BASE_URL = "https://api.binance.com/api/v3"
INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_analyser_symbols_cache.json")
CACHE_MAX_AGE = timedelta(days=3)

AMBER = "#b8860b"

# ---- theme palettes ----
THEMES = {
    "light": {
        "bg": "#f4f5f7", "panel": "#ffffff", "fg": "#1a1a1a", "muted": "#666666",
        "entry_bg": "#ffffff", "tree_bg": "#ffffff", "tree_alt": "#f0f2f5",
        "accent": "#3a7bd5", "border": "#cccccc",
        "green": "#1b8a5a", "green_bg": "#d9f7e3",
        "red": "#c62828", "red_bg": "#fde2e2",
        "chart_bg": "#ffffff", "chart_grid": "#dddddd",
    },
    "dark": {
        "bg": "#1b1d23", "panel": "#24262e", "fg": "#e8e8ea", "muted": "#9a9ca3",
        "entry_bg": "#2c2f38", "tree_bg": "#24262e", "tree_alt": "#2b2e37",
        "accent": "#5b9bff", "border": "#3a3d47",
        "green": "#3ddc97", "green_bg": "#123d27",
        "red": "#ff6b6b", "red_bg": "#4a1f1f",
        "chart_bg": "#24262e", "chart_grid": "#3a3d47",
    },
}

# Fallback list used instantly on first-ever launch, before any cache/API data exists.
FALLBACK_SYMBOLS = [
    ("BTCUSDT", "Bitcoin"), ("ETHUSDT", "Ethereum"), ("BNBUSDT", "BNB"),
    ("SOLUSDT", "Solana"), ("XRPUSDT", "XRP"), ("ADAUSDT", "Cardano"),
    ("DOGEUSDT", "Dogecoin"), ("DOTUSDT", "Polkadot"), ("LTCUSDT", "Litecoin"),
    ("LINKUSDT", "Chainlink"), ("AVAXUSDT", "Avalanche"), ("TRXUSDT", "TRON"),
    ("ATOMUSDT", "Cosmos"), ("UNIUSDT", "Uniswap"), ("ETCUSDT", "Ethereum Classic"),
    ("XLMUSDT", "Stellar"), ("ICPUSDT", "Internet Computer"), ("FILUSDT", "Filecoin"),
    ("APTUSDT", "Aptos"), ("ARBUSDT", "Arbitrum"), ("OPUSDT", "Optimism"),
    ("NEARUSDT", "NEAR Protocol"), ("INJUSDT", "Injective"), ("SUIUSDT", "Sui"),
    ("SHIBUSDT", "Shiba Inu"), ("PEPEUSDT", "Pepe"), ("SANDUSDT", "The Sandbox"),
    ("MANAUSDT", "Decentraland"), ("AAVEUSDT", "Aave"), ("MKRUSDT", "Maker"),
    ("ALGOUSDT", "Algorand"), ("VETUSDT", "VeChain"), ("HBARUSDT", "Hedera"),
    ("EGLDUSDT", "MultiversX"), ("FTMUSDT", "Fantom"), ("GRTUSDT", "The Graph"),
    ("THETAUSDT", "Theta Network"), ("EOSUSDT", "EOS"), ("XTZUSDT", "Tezos"),
    ("ZECUSDT", "Zcash"),
]

# ---- API weight tracking ----
# Binance's public API does not use a daily request quota. Each endpoint costs
# a "weight" and your IP has a rolling per-minute weight cap. We read the
# weight Binance reports back on every response header and track it here.
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


def fetch_rate_limit_config():
    r = requests.get(f"{BASE_URL}/exchangeInfo", timeout=10)
    r.raise_for_status()
    _record_usage(r.headers)
    data = r.json()
    for rl in data.get("rateLimits", []):
        if rl.get("rateLimitType") == "REQUEST_WEIGHT":
            with _usage_lock:
                _api_usage["limit_1m"] = rl.get("limit", 1200)
                interval_num = rl.get("intervalNum", 1)
                interval = rl.get("interval", "MINUTE")
                _api_usage["interval_label"] = (f"per {interval_num} {interval.lower()}"
                                                 if interval_num != 1 else f"per {interval.lower()}")
            break


# ---- symbol list caching ----

def load_cached_symbols():
    """Return (symbols, is_fresh) from disk cache, or (None, False) if unavailable."""
    try:
        with open(CACHE_PATH, "r") as f:
            payload = json.load(f)
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        symbols = [tuple(pair) for pair in payload["symbols"]]
        is_fresh = (datetime.now() - fetched_at) < CACHE_MAX_AGE
        return symbols, is_fresh
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None, False


def save_cached_symbols(symbols):
    payload = {"fetched_at": datetime.now().isoformat(), "symbols": symbols}
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(payload, f)
    except OSError:
        pass  # non-fatal — app still works without a writable cache file


# ==================== tooltips ====================

class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(self.tip, text=self.text, background="#ffffe0",
                          relief="solid", borderwidth=1, font=("Segoe UI", 9),
                          wraplength=300, justify="left", padx=6, pady=4)
        label.pack()

    def hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class TreeColumnTooltip:
    def __init__(self, tree, col_tips):
        self.tree = tree
        self.col_tips = col_tips
        self.tip = None
        tree.bind("<Motion>", self._on_motion)
        tree.bind("<Leave>", self._hide)

    def _on_motion(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "heading":
            self._hide()
            return
        col_id = self.tree.identify_column(event.x)
        try:
            idx = int(col_id.replace("#", "")) - 1
            col_name = self.tree["columns"][idx]
        except (ValueError, IndexError):
            self._hide()
            return
        text = self.col_tips.get(col_name)
        if not text:
            self._hide()
            return
        if self.tip is None:
            self.tip = tk.Toplevel(self.tree)
            self.tip.wm_overrideredirect(True)
            self.label = tk.Label(self.tip, text=text, background="#ffffe0",
                                   relief="solid", borderwidth=1, font=("Segoe UI", 9),
                                   wraplength=280, justify="left", padx=6, pady=4)
            self.label.pack()
        else:
            self.label.config(text=text)
        x = self.tree.winfo_rootx() + event.x + 12
        y = self.tree.winfo_rooty() + event.y + 16
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ==================== searchable multi-select symbol picker ====================

class SymbolPicker(ttk.Frame):
    """Button that opens a searchable, checkbox list of symbols, with a visible
    dropdown arrow, working scrollbar + mousewheel, and a manual refresh option."""

    def __init__(self, master, initial_selected=None, on_refresh=None):
        super().__init__(master)
        self.on_refresh = on_refresh
        self.all_symbols = list(FALLBACK_SYMBOLS)
        self.vars = {}
        for code, _name in self.all_symbols:
            self.vars[code] = tk.BooleanVar(value=(code in (initial_selected or [])))

        self.button = ttk.Button(self, text="Select symbols...  \u25BC", command=self._open_popup)
        self.button.pack(fill="x")
        self._popup = None
        self._refresh_button_text()

    def set_symbol_universe(self, symbols):
        existing_checked = {c for c, v in self.vars.items() if v.get()}
        self.all_symbols = symbols
        new_vars = {}
        for code, _name in symbols:
            new_vars[code] = tk.BooleanVar(value=(code in existing_checked))
        self.vars = new_vars
        self._refresh_button_text()
        if self._popup is not None:
            self._render_checklist(self._search_var.get())

    def get_selected(self):
        return [code for code, v in self.vars.items() if v.get()]

    def _refresh_button_text(self):
        n = len(self.get_selected())
        base = f"Select symbols... ({n} selected)" if n else "Select symbols..."
        self.button.config(text=f"{base}  \u25BC")

    def _open_popup(self):
        if self._popup is not None:
            self._popup.destroy()
        popup = tk.Toplevel(self)
        self._popup = popup
        popup.wm_title(f"{APP_NAME} — choose symbols")
        popup.geometry("340x460")
        popup.resizable(True, True)
        x = self.button.winfo_rootx()
        y = self.button.winfo_rooty() + self.button.winfo_height()
        popup.wm_geometry(f"+{x}+{y}")

        self._search_var = tk.StringVar()
        search_entry = ttk.Entry(popup, textvariable=self._search_var)
        search_entry.pack(fill="x", padx=6, pady=(6, 2))
        search_entry.focus_set()
        Tooltip(search_entry, "Type to filter, e.g. 'sol' or 'solana'. Works on ticker or full name.")

        btn_row = ttk.Frame(popup)
        btn_row.pack(fill="x", padx=6)
        ttk.Button(btn_row, text="Select all shown",
                   command=lambda: self._bulk_set(True, self._search_var.get())).pack(side="left")
        ttk.Button(btn_row, text="Clear all",
                   command=lambda: self._bulk_set(False, "")).pack(side="left", padx=4)
        if self.on_refresh:
            refresh_btn = ttk.Button(btn_row, text="\u27F3 Refresh list", command=self.on_refresh)
            refresh_btn.pack(side="right")
            Tooltip(refresh_btn, "Re-download the complete coin list from Binance, bypassing the "
                                  "local cache. Use this if a coin you trade is missing.")

        list_container = ttk.Frame(popup)
        list_container.pack(side="top", fill="both", expand=True, padx=(6, 0), pady=6)

        canvas = tk.Canvas(list_container, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        list_frame = ttk.Frame(canvas)
        list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            delta = event.delta
            if delta == 0:
                return
            canvas.yview_scroll(-1 if delta > 0 else 1, "units")

        def _on_mousewheel_linux(event):
            canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        canvas.bind("<Enter>", lambda e: (
            canvas.bind_all("<MouseWheel>", _on_mousewheel),
            canvas.bind_all("<Button-4>", _on_mousewheel_linux),
            canvas.bind_all("<Button-5>", _on_mousewheel_linux),
        ))
        canvas.bind("<Leave>", lambda e: (
            canvas.unbind_all("<MouseWheel>"),
            canvas.unbind_all("<Button-4>"),
            canvas.unbind_all("<Button-5>"),
        ))

        self._list_frame = list_frame
        self._render_checklist("")

        self._search_var.trace_add("write", lambda *_: self._render_checklist(self._search_var.get()))

        done_btn = ttk.Button(popup, text="Done", command=self._close_popup)
        done_btn.pack(fill="x", padx=6, pady=6)
        popup.protocol("WM_DELETE_WINDOW", self._close_popup)

    def _bulk_set(self, value, filter_text):
        filt = filter_text.lower().strip()
        for code, name in self.all_symbols:
            if filt and filt not in code.lower() and filt not in name.lower():
                continue
            self.vars[code].set(value)
        self._refresh_button_text()
        self._render_checklist(filter_text)

    def _render_checklist(self, filter_text):
        for w in self._list_frame.winfo_children():
            w.destroy()
        filt = filter_text.lower().strip()
        shown = [(c, n) for c, n in self.all_symbols
                 if not filt or filt in c.lower() or filt in n.lower()]
        for code, name in shown:
            cb = ttk.Checkbutton(self._list_frame, text=f"{code}  ({name})",
                                  variable=self.vars[code],
                                  command=self._refresh_button_text)
            cb.pack(anchor="w", padx=4, pady=1)
        if not shown:
            ttk.Label(self._list_frame, text="No matches — try Refresh list if this coin is new",
                      foreground="#888", wraplength=280).pack(padx=4, pady=4)

    def _close_popup(self):
        if self._popup:
            self._popup.destroy()
            self._popup = None
        self._refresh_button_text()


# ==================== data + indicators ====================

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
        time.sleep(0.15)
    return [c for c in all_candles if c["open_time"] >= start]


def fetch_ticker(symbol):
    r = requests.get(f"{BASE_URL}/ticker/24hr", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    _record_usage(r.headers)
    return r.json()


def fetch_usdt_symbols():
    """Live, complete list of all tradeable USDT pairs on Binance spot."""
    r = requests.get(f"{BASE_URL}/exchangeInfo", timeout=10)
    r.raise_for_status()
    _record_usage(r.headers)
    data = r.json()
    out = []
    for s in data.get("symbols", []):
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
            out.append((s["symbol"], s["baseAsset"]))
    out.sort(key=lambda x: x[0])
    return out


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


# ==================== GUI ====================

COLUMN_TIPS_BATCH = {
    "symbol": "The trading pair backtested, e.g. BTCUSDT.",
    "interval": "Candle timeframe used for signals (15m, 1h, 4h).",
    "trades": "How many simulated trades this setup produced over the tested window.",
    "win_rate": "% of trades that closed at a profit (hit target before stop).",
    "avg_win": "Average % return of winning trades.",
    "avg_loss": "Average % return of losing trades (negative).",
    "expectancy": "Average return per trade including both wins and losses. This is the single "
                   "best number to judge a setup by — positive means the rules had an edge here historically.",
    "total_pnl": "Sum of all trade returns, unweighted, no compounding, fees excluded.",
}

COLUMN_TIPS_LIVE = {
    "symbol": "The trading pair.",
    "price": "Last traded price.",
    "change": "24-hour price change percentage.",
    "lean": "Current rule-based bull/bear score (RSI, MACD cross, EMA trend). Not a prediction — "
            "a snapshot of which textbook signals currently agree.",
    "atr": "Average True Range: a volatility measure used to size stops/targets.",
}


class ScalpDeskApp:
    def __init__(self, root):
        self.root = root
        self.theme_name = "light"
        root.title(APP_NAME)
        root.geometry("1080x800")

        self.q = queue.Queue()
        self.batch_results = {}
        self.chart_mode = tk.StringVar(value="equity")

        self.style = ttk.Style()
        self.style.theme_use("clam")

        self._build_header()

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.batch_tab = ttk.Frame(notebook)
        self.live_tab = ttk.Frame(notebook)
        notebook.add(self.batch_tab, text="Batch Backtest")
        notebook.add(self.live_tab, text="Live Scan")

        self._build_batch_tab()
        self._build_live_tab()
        self._build_usage_bar()

        self.apply_theme("light")

        self.root.after(150, self._poll_queue)
        self.root.after(1000, self._tick_usage_meter)
        threading.Thread(target=self._startup_load_symbols, daemon=True).start()
        threading.Thread(target=self._load_rate_limit, daemon=True).start()

    # ---------- header ----------
    def _build_header(self):
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=10, pady=(10, 0))
        title = ttk.Label(header, text=f"\U0001FA99 {APP_NAME}", font=("Segoe UI", 16, "bold"))
        title.pack(side="left")
        subtitle = ttk.Label(header, text="Binance research: batch backtesting & live setup scoring",
                              font=("Segoe UI", 9))
        subtitle.pack(side="left", padx=10)

        self.theme_btn = ttk.Button(header, text="\u263D  Dark mode", command=self._toggle_theme)
        self.theme_btn.pack(side="right")
        Tooltip(self.theme_btn, "Switch between light and dark appearance.")

    def _toggle_theme(self):
        self.apply_theme("dark" if self.theme_name == "light" else "light")

    # ---------- startup ----------
    def _startup_load_symbols(self):
        cached, is_fresh = load_cached_symbols()
        if cached:
            self.q.put(("symbols_loaded", cached))
        if cached and is_fresh:
            return  # cache is recent enough, skip the network call entirely
        try:
            symbols = fetch_usdt_symbols()
            if symbols:
                save_cached_symbols(symbols)
                self.q.put(("symbols_loaded", symbols))
        except Exception:
            pass  # keep whatever we already loaded (cache or fallback)

    def _force_refresh_symbols(self):
        def worker():
            try:
                symbols = fetch_usdt_symbols()
                if symbols:
                    save_cached_symbols(symbols)
                    self.q.put(("symbols_loaded", symbols))
                    self.q.put(("symbols_refresh_result", "ok"))
            except Exception as e:
                self.q.put(("symbols_refresh_result", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _load_rate_limit(self):
        try:
            fetch_rate_limit_config()
        except Exception:
            pass

    # ---------- API weight meter ----------
    def _build_usage_bar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", side="bottom", padx=8, pady=(0, 8))
        self.usage_label = ttk.Label(bar, text="API weight: —", font=("Segoe UI", 9))
        self.usage_label.pack(side="left")
        Tooltip(self.usage_label,
                "Binance's public API doesn't use a daily quota — it caps total request "
                "'weight' on a rolling per-minute window per IP. This shows how much of "
                "that per-minute allowance you've used recently, so you can pace batch runs. "
                "It resets continuously, not at midnight.")
        self.usage_bar_canvas = tk.Canvas(bar, width=160, height=12, highlightthickness=1)
        self.usage_bar_canvas.pack(side="left", padx=8)
        self.cache_label = ttk.Label(bar, text="", font=("Segoe UI", 8))
        self.cache_label.pack(side="right")

    def _tick_usage_meter(self):
        usage = get_api_usage()
        used = usage["used_1m"]
        limit = usage["limit_1m"] or 1200
        label_suffix = usage["interval_label"]
        c = THEMES[self.theme_name]
        self.usage_bar_canvas.delete("all")
        if used is None:
            self.usage_label.config(text=f"API weight: not yet measured ({label_suffix})", foreground=c["fg"])
        else:
            pct = min(used / limit, 1.0)
            color = c["green"] if pct < 0.5 else (AMBER if pct < 0.8 else c["red"])
            self.usage_label.config(text=f"API weight: {used} / {limit} ({label_suffix})", foreground=color)
            self.usage_bar_canvas.create_rectangle(0, 0, 160 * pct, 12, fill=color, outline="")
        self.root.after(1000, self._tick_usage_meter)

    # ---------- batch tab ----------
    def _build_batch_tab(self):
        top = ttk.Frame(self.batch_tab)
        top.pack(fill="x", padx=6, pady=6)

        lbl = ttk.Label(top, text="Symbols:")
        lbl.grid(row=0, column=0, sticky="w")
        Tooltip(lbl, "Pick one or more coins to test. Click the button to search and check boxes.")
        self.symbol_picker = SymbolPicker(top, initial_selected=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                                           on_refresh=self._force_refresh_symbols)
        self.symbol_picker.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)

        lbl2 = ttk.Label(top, text="Intervals:")
        lbl2.grid(row=1, column=0, sticky="w", pady=(6, 0))
        Tooltip(lbl2, "Candle timeframe(s) to test. Shorter = more noise, more trades. Longer = fewer, slower signals.")
        self.iv_vars = {}
        iv_frame = ttk.Frame(top)
        iv_frame.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))
        for iv in ["15m", "1h", "4h"]:
            var = tk.BooleanVar(value=(iv in ("15m", "1h")))
            self.iv_vars[iv] = var
            cb = ttk.Checkbutton(iv_frame, text=iv, variable=var)
            cb.pack(side="left", padx=4)

        lbl3 = ttk.Label(top, text="Days:")
        lbl3.grid(row=2, column=0, sticky="w", pady=(6, 0))
        Tooltip(lbl3, "How many days of history to backtest over.")
        self.days_entry = ttk.Entry(top, width=6)
        self.days_entry.insert(0, "60")
        self.days_entry.grid(row=2, column=1, sticky="w", pady=(6, 0))

        lbl4 = ttk.Label(top, text="Entry threshold %:")
        lbl4.grid(row=2, column=2, sticky="e", pady=(6, 0))
        Tooltip(lbl4, "Minimum bull/bear signal agreement needed to open a simulated trade. "
                      "Higher = fewer, more selective trades.")
        self.threshold_entry = ttk.Entry(top, width=6)
        self.threshold_entry.insert(0, "75")
        self.threshold_entry.grid(row=2, column=3, sticky="w", pady=(6, 0))

        lbl5 = ttk.Label(top, text="Stop (x ATR):")
        lbl5.grid(row=3, column=0, sticky="w", pady=(6, 0))
        Tooltip(lbl5, "Stop-loss distance, as a multiple of ATR (volatility). E.g. 1.5 means "
                      "stop is placed 1.5x the average recent candle range away from entry.")
        self.stop_entry = ttk.Entry(top, width=6)
        self.stop_entry.insert(0, "1.5")
        self.stop_entry.grid(row=3, column=1, sticky="w", pady=(6, 0))

        lbl6 = ttk.Label(top, text="Target (x ATR):")
        lbl6.grid(row=3, column=2, sticky="e", pady=(6, 0))
        Tooltip(lbl6, "Take-profit distance, as a multiple of ATR. A higher target than stop "
                      "means you can be profitable even with a win rate below 50%.")
        self.target_entry = ttk.Entry(top, width=6)
        self.target_entry.insert(0, "2.0")
        self.target_entry.grid(row=3, column=3, sticky="w", pady=(6, 0))

        self.run_btn = ttk.Button(top, text="Run Batch Backtest", command=self._start_batch)
        self.run_btn.grid(row=0, column=4, rowspan=2, padx=12, sticky="ns")
        Tooltip(self.run_btn, "Runs the backtest for every symbol x interval combination selected above.")

        self.export_btn = ttk.Button(top, text="Export CSV", command=self._export_batch_csv)
        self.export_btn.grid(row=2, column=4, rowspan=2, padx=12, sticky="ns")
        Tooltip(self.export_btn, "Saves the full results table (including run settings) to a CSV file "
                                  "you can open in Excel/Sheets to track whether outcomes hold up over time.")

        self.status_label = ttk.Label(top, text="")
        self.status_label.grid(row=4, column=4, sticky="w")

        self.summary_label = ttk.Label(self.batch_tab, text="", font=("Segoe UI", 10, "bold"))
        self.summary_label.pack(anchor="w", padx=8, pady=(0, 4))

        cols = ("symbol", "interval", "trades", "win_rate", "avg_win", "avg_loss", "expectancy", "total_pnl")
        headers = {
            "symbol": "Symbol", "interval": "Interval", "trades": "Trades",
            "win_rate": "Win %", "avg_win": "Avg Win %", "avg_loss": "Avg Loss %",
            "expectancy": "Expectancy %/trade", "total_pnl": "Sum Return %",
        }
        table_frame = ttk.Frame(self.batch_tab)
        table_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=9)
        for c in cols:
            self.tree.heading(c, text=headers[c], command=lambda cc=c: self._sort_by(cc))
            self.tree.column(c, width=100, anchor="center")
        self.tree.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        TreeColumnTooltip(self.tree, COLUMN_TIPS_BATCH)

        ttk.Label(self.batch_tab, text="Green rows = positive expectancy · red = negative · click a "
                                        "column header to sort · hover a header for details",
                  foreground="#666").pack(anchor="w", padx=8)

        chart_toolbar = ttk.Frame(self.batch_tab)
        chart_toolbar.pack(fill="x", padx=6, pady=(6, 0))
        eq_btn = ttk.Radiobutton(chart_toolbar, text="Equity curve (selected row)",
                                  variable=self.chart_mode, value="equity", command=self._redraw_chart)
        eq_btn.pack(side="left")
        Tooltip(eq_btn, "Shows cumulative % return trade-by-trade for the row you select in the table.")
        ov_btn = ttk.Radiobutton(chart_toolbar, text="Win rate overview (all results)",
                                  variable=self.chart_mode, value="overview", command=self._redraw_chart)
        ov_btn.pack(side="left", padx=10)
        Tooltip(ov_btn, "Bar chart comparing win rate across every symbol/interval combo tested, "
                        "green if expectancy is positive, red if negative.")

        self.fig = Figure(figsize=(6, 2.8), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.batch_tab)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=(0, 6))

    def _sort_by(self, col):
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        def keyfn(pair):
            v = pair[0]
            try:
                return float(v)
            except ValueError:
                return v

        items.sort(key=keyfn, reverse=True)
        for idx, (_, k) in enumerate(items):
            self.tree.move(k, "", idx)

    def _start_batch(self):
        symbols = self.symbol_picker.get_selected()
        intervals = [iv for iv, v in self.iv_vars.items() if v.get()]
        if not symbols or not intervals:
            messagebox.showwarning(APP_NAME, "Pick at least one symbol and one interval.")
            return
        try:
            days = int(self.days_entry.get())
            threshold = int(self.threshold_entry.get())
            stop_atr = float(self.stop_entry.get())
            target_atr = float(self.target_entry.get())
        except ValueError:
            messagebox.showwarning(APP_NAME, "Days/threshold/stop/target must be numbers.")
            return

        for row in self.tree.get_children():
            self.tree.delete(row)
        self.batch_results.clear()
        self.summary_label.config(text="")
        self._draw_placeholder_chart()

        self.last_run_settings = {
            "days": days, "threshold": threshold, "stop_atr": stop_atr, "target_atr": target_atr,
        }

        self.run_btn.config(state="disabled")
        total = len(symbols) * len(intervals)
        self.status_label.config(text=f"0 / {total} done")

        thread = threading.Thread(
            target=self._batch_worker,
            args=(symbols, intervals, days, threshold, stop_atr, target_atr),
            daemon=True,
        )
        thread.start()

    def _batch_worker(self, symbols, intervals, days, threshold, stop_atr, target_atr):
        combos = [(s, iv) for s in symbols for iv in intervals]
        done = 0
        for symbol, interval in combos:
            try:
                result = backtest_core(symbol, interval, days, threshold, stop_atr, target_atr)
                self.q.put(("row", result))
            except Exception as e:
                self.q.put(("error", (symbol, interval, str(e))))
            done += 1
            self.q.put(("progress", (done, len(combos))))
        self.q.put(("done", None))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "row":
                    self._insert_row(payload)
                elif kind == "error":
                    symbol, interval, msg = payload
                    self.tree.insert("", "end", values=(symbol, interval, "err", msg, "", "", "", ""))
                elif kind == "progress":
                    done, total = payload
                    self.status_label.config(text=f"{done} / {total} done")
                elif kind == "done":
                    self.run_btn.config(state="normal")
                    self.status_label.config(text="Done")
                    self._update_summary()
                    if self.chart_mode.get() == "overview":
                        self._redraw_chart()
                elif kind == "live_row":
                    self._insert_live_row(payload)
                elif kind == "live_error":
                    symbol, msg = payload
                    self.live_tree.insert("", "end", values=(symbol, "error", msg, "", ""))
                elif kind == "live_done":
                    self.live_run_btn.config(state="normal")
                    self._redraw_live_chart()
                elif kind == "symbols_loaded":
                    symbols = payload
                    self.symbol_picker.set_symbol_universe(symbols)
                    self.live_symbol_picker.set_symbol_universe(symbols)
                    self.cache_label.config(text=f"Symbol list: {len(symbols)} pairs loaded")
                elif kind == "symbols_refresh_result":
                    if payload == "ok":
                        messagebox.showinfo(APP_NAME, "Symbol list refreshed from Binance.")
                    else:
                        messagebox.showerror(APP_NAME, f"Could not refresh symbol list: {payload}")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _insert_row(self, result):
        key = (result["symbol"], result["interval"])
        self.batch_results[key] = result
        if result["n_trades"] == 0:
            self.tree.insert("", "end", iid=f"{key[0]}_{key[1]}",
                              values=(result["symbol"], result["interval"], 0, "-", "-", "-", "-", "-"))
            return
        tag = "pos" if result["expectancy"] > 0 else "neg"
        self.tree.insert("", "end", iid=f"{key[0]}_{key[1]}", tags=(tag,), values=(
            result["symbol"], result["interval"], result["n_trades"],
            f"{result['win_rate']:.1f}", f"{result['avg_win']:.2f}",
            f"{result['avg_loss']:.2f}", f"{result['expectancy']:.3f}",
            f"{result['total_pnl']:.2f}",
        ))

    def _update_summary(self):
        valid = [r for r in self.batch_results.values() if r["n_trades"] > 0]
        if not valid:
            self.summary_label.config(text="No trades triggered across the tested combos — try a lower threshold.")
            return
        best = max(valid, key=lambda r: r["expectancy"])
        c = THEMES[self.theme_name]
        color = c["green"] if best["expectancy"] > 0 else c["red"]
        self.summary_label.config(
            text=f"Best combo: {best['symbol']} {best['interval']} — expectancy "
                 f"{best['expectancy']:+.3f}%/trade, win rate {best['win_rate']:.1f}%, "
                 f"{best['n_trades']} trades",
            foreground=color,
        )

    def _export_batch_csv(self):
        if not self.batch_results:
            messagebox.showinfo(APP_NAME, "Run a batch backtest first — nothing to export yet.")
            return
        default_name = f"crypto_analyser_backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save backtest results as...",
        )
        if not path:
            return
        settings = getattr(self, "last_run_settings", {})
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["exported_at", datetime.now().isoformat(timespec="seconds")])
                writer.writerow(["days", settings.get("days", "")])
                writer.writerow(["entry_threshold_pct", settings.get("threshold", "")])
                writer.writerow(["stop_atr_multiple", settings.get("stop_atr", "")])
                writer.writerow(["target_atr_multiple", settings.get("target_atr", "")])
                writer.writerow([])
                writer.writerow(["symbol", "interval", "trades", "win_rate_pct", "avg_win_pct",
                                  "avg_loss_pct", "expectancy_pct_per_trade", "sum_return_pct"])
                for r in self.batch_results.values():
                    if r["n_trades"] == 0:
                        writer.writerow([r["symbol"], r["interval"], 0, "", "", "", "", ""])
                    else:
                        writer.writerow([
                            r["symbol"], r["interval"], r["n_trades"],
                            round(r["win_rate"], 2), round(r["avg_win"], 3),
                            round(r["avg_loss"], 3), round(r["expectancy"], 4),
                            round(r["total_pnl"], 3),
                        ])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not save file: {e}")
            return
        messagebox.showinfo(APP_NAME, f"Saved to {path}")

    def _on_row_select(self, _event):
        self.chart_mode.set("equity")
        self._redraw_chart()

    def _redraw_chart(self):
        if self.chart_mode.get() == "equity":
            self._draw_equity_chart()
        else:
            self._draw_overview_chart()

    def _style_axes(self, ax):
        c = THEMES[self.theme_name]
        self.fig.patch.set_facecolor(c["chart_bg"])
        ax.set_facecolor(c["chart_bg"])
        ax.tick_params(colors=c["fg"], labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(c["chart_grid"])
        ax.title.set_color(c["fg"])
        ax.xaxis.label.set_color(c["fg"])
        ax.yaxis.label.set_color(c["fg"])

    def _draw_placeholder_chart(self):
        c = THEMES[self.theme_name]
        self.ax.clear()
        self._style_axes(self.ax)
        self.ax.set_title("Run a backtest, then select a row to view its equity curve", fontsize=10, color=c["muted"])
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas.draw()

    def _draw_equity_chart(self):
        sel = self.tree.selection()
        if not sel:
            self._draw_placeholder_chart()
            return
        vals = self.tree.item(sel[0], "values")
        key = (vals[0], vals[1])
        result = self.batch_results.get(key)
        if not result or not result["trades"]:
            self._draw_placeholder_chart()
            return

        equity = [0]
        for t in result["trades"]:
            equity.append(equity[-1] + t["pnl_pct"])

        c = THEMES[self.theme_name]
        self.ax.clear()
        self._style_axes(self.ax)
        color = c["green"] if equity[-1] >= 0 else c["red"]
        self.ax.plot(equity, color=color, linewidth=1.6)
        self.ax.fill_between(range(len(equity)), equity, 0, color=color, alpha=0.15)
        self.ax.axhline(0, color=c["chart_grid"], linewidth=0.8)
        self.ax.set_title(f"{key[0]} {key[1]} — cumulative return over {len(result['trades'])} trades (%)",
                           fontsize=10)
        self.ax.set_xlabel("trade #", fontsize=8)
        self.ax.set_ylabel("cum. return %", fontsize=8)
        self.canvas.draw()

    def _draw_overview_chart(self):
        valid = {k: r for k, r in self.batch_results.items() if r["n_trades"] > 0}
        if not valid:
            self._draw_placeholder_chart()
            return
        labels = [f"{s}\n{iv}" for (s, iv) in valid.keys()]
        win_rates = [r["win_rate"] for r in valid.values()]
        c = THEMES[self.theme_name]
        colors = [c["green"] if r["expectancy"] > 0 else c["red"] for r in valid.values()]

        self.ax.clear()
        self._style_axes(self.ax)
        bars = self.ax.bar(range(len(labels)), win_rates, color=colors)
        self.ax.axhline(50, color=c["chart_grid"], linewidth=0.8, linestyle="--")
        self.ax.set_xticks(range(len(labels)))
        self.ax.set_xticklabels(labels, fontsize=7)
        self.ax.set_ylabel("win rate %", fontsize=8)
        self.ax.set_title("Win rate by symbol/interval (green = positive expectancy)", fontsize=10)
        for bar, wr in zip(bars, win_rates):
            self.ax.text(bar.get_x() + bar.get_width() / 2, wr + 1, f"{wr:.0f}%",
                         ha="center", fontsize=7, color=c["fg"])
        self.canvas.draw()

    # ---------- live tab ----------
    def _build_live_tab(self):
        top = ttk.Frame(self.live_tab)
        top.pack(fill="x", padx=6, pady=6)

        lbl = ttk.Label(top, text="Symbols:")
        lbl.grid(row=0, column=0, sticky="w")
        Tooltip(lbl, "Pick coins to scan for their current signal snapshot.")
        self.live_symbol_picker = SymbolPicker(top, initial_selected=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                                                on_refresh=self._force_refresh_symbols)
        self.live_symbol_picker.grid(row=0, column=1, sticky="we", padx=4)

        lbl2 = ttk.Label(top, text="Interval:")
        lbl2.grid(row=0, column=2, sticky="e")
        Tooltip(lbl2, "Candle timeframe for the live scan.")
        self.live_interval = tk.StringVar(value="15m")
        interval_combo = ttk.Combobox(top, textvariable=self.live_interval, width=6,
                                       values=["1m", "5m", "15m", "1h", "4h", "1d"], state="readonly")
        interval_combo.grid(row=0, column=3, sticky="w")

        self.live_run_btn = ttk.Button(top, text="Scan Now", command=self._start_live)
        self.live_run_btn.grid(row=0, column=4, padx=12)
        Tooltip(self.live_run_btn, "Fetches current price, 24h change, and signal lean for each selected symbol.")

        self.live_export_btn = ttk.Button(top, text="Export CSV", command=self._export_live_csv)
        self.live_export_btn.grid(row=0, column=5, padx=(0, 4))
        Tooltip(self.live_export_btn, "Saves the current scan snapshot to CSV — run this periodically "
                                       "and compare files later to see if the lean matched what actually happened.")

        cols = ("symbol", "price", "change", "lean", "atr")
        headers = {"symbol": "Symbol", "price": "Price", "change": "24h %", "lean": "Lean", "atr": "ATR"}
        self.live_tree = ttk.Treeview(self.live_tab, columns=cols, show="headings", height=10)
        for c in cols:
            self.live_tree.heading(c, text=headers[c])
            self.live_tree.column(c, width=140, anchor="center")
        self.live_tree.pack(fill="both", expand=True, padx=6, pady=6)
        TreeColumnTooltip(self.live_tree, COLUMN_TIPS_LIVE)

        ttk.Label(self.live_tab, text="Green = bullish lean · red = bearish lean",
                  foreground="#666").pack(anchor="w", padx=8)

        self.live_fig = Figure(figsize=(6, 2.4), dpi=100)
        self.live_ax = self.live_fig.add_subplot(111)
        self.live_canvas = FigureCanvasTkAgg(self.live_fig, master=self.live_tab)
        self.live_canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)

    def _draw_live_placeholder(self):
        c = THEMES[self.theme_name]
        self.live_ax.clear()
        self.live_fig.patch.set_facecolor(c["chart_bg"])
        self.live_ax.set_facecolor(c["chart_bg"])
        self.live_ax.set_title("Run a scan to see the bull/bear lean by symbol", fontsize=10, color=c["muted"])
        self.live_ax.set_xticks([])
        self.live_ax.set_yticks([])
        self.live_canvas.draw()

    def _start_live(self):
        symbols = self.live_symbol_picker.get_selected()
        interval = self.live_interval.get()
        if not symbols:
            messagebox.showwarning(APP_NAME, "Pick at least one symbol.")
            return
        for row in self.live_tree.get_children():
            self.live_tree.delete(row)
        self._live_scan_data = {}
        self.live_run_btn.config(state="disabled")
        thread = threading.Thread(target=self._live_worker, args=(symbols, interval), daemon=True)
        thread.start()

    def _live_worker(self, symbols, interval):
        for symbol in symbols:
            try:
                r = live_scan_one(symbol, interval)
                self.q.put(("live_row", r))
            except Exception as e:
                self.q.put(("live_error", (symbol, str(e))))
        self.q.put(("live_done", None))

    def _insert_live_row(self, r):
        if not hasattr(self, "_live_scan_data"):
            self._live_scan_data = {}
        self._live_scan_data[r["symbol"]] = r
        tag = "bull" if r["bull_pct"] >= 50 else "bear"
        self.live_tree.insert("", "end", tags=(tag,), values=(
            r["symbol"], f"{r['price']:.6f}" if r["price"] < 1 else f"{r['price']:.2f}",
            f"{r['change']:+.2f}", f"{r['bull_pct']}% {'LONG' if r['bull_pct'] >= 50 else 'SHORT'}",
            f"{r['atr']:.6f}" if r["atr"] < 1 else f"{r['atr']:.2f}",
        ))

    def _export_live_csv(self):
        data = getattr(self, "_live_scan_data", {})
        if not data:
            messagebox.showinfo(APP_NAME, "Run a scan first — nothing to export yet.")
            return
        default_name = f"crypto_analyser_livescan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save live scan as...",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["scanned_at", datetime.now().isoformat(timespec="seconds")])
                writer.writerow(["interval", self.live_interval.get()])
                writer.writerow([])
                writer.writerow(["symbol", "price", "change_24h_pct", "bull_lean_pct", "side", "atr"])
                for r in data.values():
                    writer.writerow([
                        r["symbol"], r["price"], round(r["change"], 2), r["bull_pct"],
                        "LONG" if r["bull_pct"] >= 50 else "SHORT", r["atr"],
                    ])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not save file: {e}")
            return
        messagebox.showinfo(APP_NAME, f"Saved to {path}")

    def _redraw_live_chart(self):
        data = getattr(self, "_live_scan_data", {})
        if not data:
            self._draw_live_placeholder()
            return
        symbols = list(data.keys())
        leans = [data[s]["bull_pct"] for s in symbols]
        c = THEMES[self.theme_name]
        colors = [c["green"] if v >= 50 else c["red"] for v in leans]

        self.live_ax.clear()
        self.live_fig.patch.set_facecolor(c["chart_bg"])
        self.live_ax.set_facecolor(c["chart_bg"])
        self.live_ax.tick_params(colors=c["fg"], labelsize=8)
        for spine in self.live_ax.spines.values():
            spine.set_color(c["chart_grid"])
        bars = self.live_ax.bar(symbols, leans, color=colors)
        self.live_ax.axhline(50, color=c["chart_grid"], linewidth=0.8, linestyle="--")
        self.live_ax.set_ylim(0, 100)
        self.live_ax.set_ylabel("bull lean %", fontsize=8, color=c["fg"])
        self.live_ax.set_title("Current signal lean by symbol", fontsize=10, color=c["fg"])
        for bar, v in zip(bars, leans):
            self.live_ax.text(bar.get_x() + bar.get_width() / 2, v + 2, f"{v}%",
                              ha="center", fontsize=7, color=c["fg"])
        self.live_fig.autofmt_xdate(rotation=0)
        self.live_canvas.draw()

    # ---------- theming ----------
    def apply_theme(self, name):
        self.theme_name = name
        c = THEMES[name]
        s = self.style

        self.root.configure(bg=c["bg"])
        s.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["entry_bg"])
        s.configure("TFrame", background=c["bg"])
        s.configure("TLabel", background=c["bg"], foreground=c["fg"])
        s.configure("TButton", background=c["panel"], foreground=c["fg"])
        s.map("TButton", background=[("active", c["accent"])], foreground=[("active", "#ffffff")])
        s.configure("TCheckbutton", background=c["bg"], foreground=c["fg"])
        s.configure("TRadiobutton", background=c["bg"], foreground=c["fg"])
        s.configure("TEntry", fieldbackground=c["entry_bg"], foreground=c["fg"])
        s.configure("TCombobox", fieldbackground=c["entry_bg"], foreground=c["fg"])
        s.configure("TNotebook", background=c["bg"])
        s.configure("TNotebook.Tab", background=c["panel"], foreground=c["fg"])
        s.map("TNotebook.Tab", background=[("selected", c["accent"])],
              foreground=[("selected", "#ffffff")])
        s.configure("Treeview", background=c["tree_bg"], fieldbackground=c["tree_bg"], foreground=c["fg"])
        s.configure("Treeview.Heading", background=c["accent"], foreground="#ffffff")
        s.map("Treeview", background=[("selected", c["accent"])], foreground=[("selected", "#ffffff")])

        self.tree.tag_configure("pos", background=c["green_bg"])
        self.tree.tag_configure("neg", background=c["red_bg"])
        self.live_tree.tag_configure("bull", background=c["green_bg"])
        self.live_tree.tag_configure("bear", background=c["red_bg"])

        self.usage_bar_canvas.configure(bg=c["panel"], highlightbackground=c["border"])
        self.cache_label.configure(foreground=c["muted"])

        self.theme_btn.config(text=("\u2600  Light mode" if name == "dark" else "\u263D  Dark mode"))

        self._draw_placeholder_chart() if not self.batch_results else self._redraw_chart()
        self._redraw_live_chart() if getattr(self, "_live_scan_data", None) else self._draw_live_placeholder()


def main():
    root = tk.Tk()
    ScalpDeskApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
