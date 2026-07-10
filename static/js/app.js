// ===== Crypto Analyser frontend =====

const state = {
  theme: localStorage.getItem("ca-theme") || "dark",
  symbolUniverse: [], // [{symbol, name}]
  batchSelected: new Set(["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
  liveSelected: new Set(["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
  batchResults: {}, // key "SYM_INTERVAL" -> result
  liveResults: {}, // symbol -> result
  lastRunSettings: {},
  chartMode: "equity",
  selectedRowKey: null,
  charts: {},
};

// ---------- theme ----------
function applyTheme(name) {
  state.theme = name;
  document.documentElement.setAttribute("data-theme", name);
  localStorage.setItem("ca-theme", name);
  const moonIcon = document.getElementById("themeIconMoon");
  moonIcon.innerHTML = name === "dark"
    ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
    : '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
  redrawAllCharts();
}
document.getElementById("themeToggle").addEventListener("click", () => {
  applyTheme(state.theme === "dark" ? "light" : "dark");
});

// ---------- tooltip (for elements using data-tip) ----------
const tooltipEl = document.getElementById("globalTooltip");
document.body.addEventListener("mouseover", (e) => {
  const target = e.target.closest("[data-tip]");
  if (!target) return;
  tooltipEl.textContent = target.getAttribute("data-tip");
  tooltipEl.style.display = "block";
});
document.body.addEventListener("mousemove", (e) => {
  if (tooltipEl.style.display === "block") {
    tooltipEl.style.left = Math.min(e.clientX + 14, window.innerWidth - 280) + "px";
    tooltipEl.style.top = e.clientY + 18 + "px";
  }
});
document.body.addEventListener("mouseout", (e) => {
  if (e.target.closest("[data-tip]")) tooltipEl.style.display = "none";
});

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  });
});

// ---------- symbol picker component ----------
function initPicker(prefix, selectedSet, onChange) {
  const root = document.getElementById(prefix + "Picker");
  const btn = document.getElementById(prefix + "PickerBtn");
  const label = document.getElementById(prefix + "PickerLabel");
  const popup = document.getElementById(prefix + "PickerPopup");
  const search = document.getElementById(prefix + "PickerSearch");
  const listEl = document.getElementById(prefix + "PickerList");

  function refreshLabel() {
    const n = selectedSet.size;
    label.textContent = n ? `Select symbols… (${n} selected)` : "Select symbols…";
  }

  function renderList(filterText) {
    listEl.innerHTML = "";
    const filt = filterText.trim().toLowerCase();
    const shown = state.symbolUniverse.filter(
      (s) => !filt || s.symbol.toLowerCase().includes(filt) || s.name.toLowerCase().includes(filt)
    );
    if (!shown.length) {
      const empty = document.createElement("div");
      empty.className = "picker-empty";
      empty.textContent = "No matches — try Refresh list if this coin is new";
      listEl.appendChild(empty);
      return;
    }
    for (const s of shown) {
      const row = document.createElement("label");
      row.className = "picker-row";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selectedSet.has(s.symbol);
      cb.addEventListener("change", () => {
        if (cb.checked) selectedSet.add(s.symbol);
        else selectedSet.delete(s.symbol);
        refreshLabel();
        onChange && onChange();
      });
      const txt = document.createElement("span");
      txt.innerHTML = `${s.symbol} <span class="pname">(${s.name})</span>`;
      row.appendChild(cb);
      row.appendChild(txt);
      listEl.appendChild(row);
    }
  }

  btn.addEventListener("click", () => {
    root.classList.toggle("open");
    if (root.classList.contains("open")) {
      renderList(search.value);
      search.focus();
    }
  });

  document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) root.classList.remove("open");
  });

  search.addEventListener("input", () => renderList(search.value));

  popup.querySelector('[data-action="select-all"]').addEventListener("click", () => {
    const filt = search.value.trim().toLowerCase();
    state.symbolUniverse
      .filter((s) => !filt || s.symbol.toLowerCase().includes(filt) || s.name.toLowerCase().includes(filt))
      .forEach((s) => selectedSet.add(s.symbol));
    refreshLabel();
    renderList(search.value);
    onChange && onChange();
  });
  popup.querySelector('[data-action="clear-all"]').addEventListener("click", () => {
    selectedSet.clear();
    refreshLabel();
    renderList(search.value);
    onChange && onChange();
  });
  popup.querySelector('[data-action="refresh"]').addEventListener("click", async () => {
    await loadSymbolUniverse(true);
  });

  refreshLabel();
  return { renderList, refreshLabel };
}

let batchPickerHandle, livePickerHandle;

async function loadSymbolUniverse(forceRefresh) {
  try {
    const res = await fetch(`/api/symbols${forceRefresh ? "?refresh=1" : ""}`);
    const data = await res.json();
    if (data.symbols && data.symbols.length) {
      state.symbolUniverse = data.symbols;
      batchPickerHandle && batchPickerHandle.renderList(document.getElementById("batchPickerSearch").value);
      livePickerHandle && livePickerHandle.renderList(document.getElementById("livePickerSearch").value);
      batchPickerHandle && batchPickerHandle.refreshLabel();
      livePickerHandle && livePickerHandle.refreshLabel();
    }
  } catch (e) {
    console.error("symbol load failed", e);
  }
}

// ---------- API usage gauge ----------
async function pollUsage() {
  try {
    const res = await fetch("/api/usage");
    const usage = await res.json();
    const gauge = document.getElementById("usageGauge");
    const gaugeValue = document.getElementById("usageGaugeValue");
    const detail = document.getElementById("usageDetail");
    if (usage.used_1m == null) {
      gauge.style.setProperty("--pct", 0);
      gaugeValue.textContent = "—";
      detail.textContent = `API weight: not yet measured (${usage.interval_label})`;
    } else {
      const pct = Math.min((usage.used_1m / usage.limit_1m) * 100, 100);
      gauge.style.setProperty("--pct", pct.toFixed(1));
      gaugeValue.textContent = Math.round(pct) + "%";
      detail.textContent = `API weight: ${usage.used_1m} / ${usage.limit_1m} (${usage.interval_label})`;
    }
  } catch (e) { /* silent */ }
  setTimeout(pollUsage, 2000);
}

// ---------- ticker tape ----------
function updateTicker() {
  const track = document.getElementById("tickerTrack");
  const results = Object.values(state.liveResults);
  if (!results.length) return;
  const itemsHtml = results
    .map((r) => {
      const dir = r.change >= 0 ? "up" : "down";
      const arrow = r.change >= 0 ? "▲" : "▼";
      return `<span class="ticker-item"><span class="sym">${r.symbol}</span><span class="${dir}">${arrow} ${r.change.toFixed(2)}%</span></span>`;
    })
    .join("");
  track.innerHTML = itemsHtml + itemsHtml; // duplicate for seamless loop
}

// ---------- batch backtest ----------
function evalTags(container) {
  return container;
}

document.getElementById("runBatchBtn").addEventListener("click", runBatch);

function runBatch() {
  const symbols = Array.from(state.batchSelected);
  const intervals = Array.from(document.querySelectorAll("#intervalChips input:checked")).map((i) => i.value);
  if (!symbols.length || !intervals.length) {
    alert("Pick at least one symbol and one interval.");
    return;
  }
  const days = parseInt(document.getElementById("daysInput").value, 10);
  const threshold = parseInt(document.getElementById("thresholdInput").value, 10);
  const stopAtr = parseFloat(document.getElementById("stopInput").value);
  const targetAtr = parseFloat(document.getElementById("targetInput").value);
  if ([days, threshold, stopAtr, targetAtr].some((v) => Number.isNaN(v))) {
    alert("Days/threshold/stop/target must be numbers.");
    return;
  }

  state.batchResults = {};
  state.lastRunSettings = { days, threshold, stop_atr: stopAtr, target_atr: targetAtr };
  state.selectedRowKey = null;
  renderBatchTable();
  document.getElementById("batchSummary").textContent = "";
  showChartPlaceholder("batch", "Running…");

  const runBtn = document.getElementById("runBatchBtn");
  runBtn.disabled = true;
  const statusEl = document.getElementById("batchStatus");

  const total = symbols.length * intervals.length;
  let doneCount = 0;
  statusEl.textContent = `0 / ${total} done`;

  const params = new URLSearchParams({
    symbols: symbols.join(","),
    intervals: intervals.join(","),
    days, threshold, stop_atr: stopAtr, target_atr: targetAtr,
  });
  const es = new EventSource(`/api/backtest/stream?${params.toString()}`);

  es.addEventListener("row", (e) => {
    const result = JSON.parse(e.data);
    const key = `${result.symbol}_${result.interval}`;
    state.batchResults[key] = result;
    renderBatchTable();
  });
  es.addEventListener("row_error", (e) => {
    const err = JSON.parse(e.data);
    const key = `${err.symbol}_${err.interval}`;
    state.batchResults[key] = { symbol: err.symbol, interval: err.interval, n_trades: 0, error: err.error };
    renderBatchTable();
  });
  es.addEventListener("progress", (e) => {
    const p = JSON.parse(e.data);
    doneCount = p.done;
    statusEl.textContent = `${p.done} / ${p.total} done`;
  });
  es.addEventListener("done", () => {
    es.close();
    runBtn.disabled = false;
    statusEl.textContent = "Done";
    updateBatchSummary();
    redrawBatchChart();
  });
  es.onerror = () => {
    es.close();
    runBtn.disabled = false;
    statusEl.textContent = "Stream ended";
  };
}

function renderBatchTable() {
  const tbody = document.getElementById("batchTableBody");
  tbody.innerHTML = "";
  const rows = Object.entries(state.batchResults);
  if (!rows.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="8">No results yet — configure and run a batch backtest above.</td></tr>`;
    return;
  }
  for (const [key, r] of rows) {
    const tr = document.createElement("tr");
    tr.dataset.key = key;
    if (r.error || r.n_trades === 0) {
      tr.innerHTML = `<td>${r.symbol}</td><td>${r.interval}</td><td>0</td>
        <td colspan="5" style="color:var(--muted)">${r.error ? "error: " + r.error : "no trades triggered"}</td>`;
    } else {
      tr.classList.add(r.expectancy > 0 ? "pos" : "neg");
      tr.innerHTML = `
        <td>${r.symbol}</td><td>${r.interval}</td><td>${r.n_trades}</td>
        <td>${r.win_rate.toFixed(1)}</td>
        <td class="${r.avg_win >= 0 ? "num-pos" : "num-neg"}">${r.avg_win.toFixed(2)}</td>
        <td class="num-neg">${r.avg_loss.toFixed(2)}</td>
        <td class="${r.expectancy >= 0 ? "num-pos" : "num-neg"}">${r.expectancy.toFixed(3)}</td>
        <td class="${r.total_pnl >= 0 ? "num-pos" : "num-neg"}">${r.total_pnl.toFixed(2)}</td>`;
    }
    if (key === state.selectedRowKey) tr.classList.add("selected");
    tr.addEventListener("click", () => {
      state.selectedRowKey = key;
      state.chartMode = "equity";
      document.querySelector('input[name="chartMode"][value="equity"]').checked = true;
      renderBatchTable();
      redrawBatchChart();
    });
    tbody.appendChild(tr);
  }
}

function updateBatchSummary() {
  const valid = Object.values(state.batchResults).filter((r) => r.n_trades > 0);
  const summaryEl = document.getElementById("batchSummary");
  if (!valid.length) {
    summaryEl.textContent = "No trades triggered across the tested combos — try a lower threshold.";
    summaryEl.style.color = "var(--muted)";
    return;
  }
  const best = valid.reduce((a, b) => (b.expectancy > a.expectancy ? b : a));
  summaryEl.textContent = `Best combo: ${best.symbol} ${best.interval} — expectancy ${best.expectancy >= 0 ? "+" : ""}${best.expectancy.toFixed(3)}%/trade, win rate ${best.win_rate.toFixed(1)}%, ${best.n_trades} trades`;
  summaryEl.style.color = best.expectancy > 0 ? "var(--green)" : "var(--red)";
}

// sortable columns
document.querySelectorAll("#batchTable th[data-key]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    const entries = Object.entries(state.batchResults);
    entries.sort((a, b) => {
      const av = a[1][key], bv = b[1][key];
      if (typeof av === "number" && typeof bv === "number") return bv - av;
      return String(bv ?? "").localeCompare(String(av ?? ""));
    });
    state.batchResults = Object.fromEntries(entries);
    renderBatchTable();
  });
});

document.querySelectorAll('input[name="chartMode"]').forEach((r) => {
  r.addEventListener("change", (e) => {
    state.chartMode = e.target.value;
    redrawBatchChart();
  });
});

// ---------- charts ----------
function themeColors() {
  const styles = getComputedStyle(document.documentElement);
  return {
    text: styles.getPropertyValue("--text").trim(),
    muted: styles.getPropertyValue("--muted").trim(),
    border: styles.getPropertyValue("--border").trim(),
    green: styles.getPropertyValue("--green").trim(),
    red: styles.getPropertyValue("--red").trim(),
    panel: styles.getPropertyValue("--panel").trim(),
  };
}

function showChartPlaceholder(which, text) {
  const el = document.getElementById(which + "ChartPlaceholder");
  const canvas = document.getElementById(which + "Chart");
  if (text) {
    el.textContent = text;
    el.style.display = "block";
    canvas.style.display = "none";
  } else {
    el.style.display = "none";
    canvas.style.display = "block";
  }
}

function redrawBatchChart() {
  if (state.chartMode === "equity") drawEquityChart();
  else drawOverviewChart();
}

function drawEquityChart() {
  const key = state.selectedRowKey;
  const result = key ? state.batchResults[key] : null;
  if (!result || !result.trades || !result.trades.length) {
    showChartPlaceholder("batch", "Run a backtest, then select a row to view its equity curve.");
    return;
  }
  showChartPlaceholder("batch", null);
  const c = themeColors();
  let cum = 0;
  const data = [0].concat(result.trades.map((t) => (cum += t.pnl_pct)));
  const color = cum >= 0 ? c.green : c.red;

  if (state.charts.batch) state.charts.batch.destroy();
  const ctx = document.getElementById("batchChart").getContext("2d");
  state.charts.batch = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map((_, i) => i),
      datasets: [{
        data, borderColor: color, backgroundColor: color + "26",
        fill: true, tension: 0.15, pointRadius: 0, borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        title: {
          display: true,
          text: `${result.symbol} ${result.interval} — cumulative return over ${result.trades.length} trades (%)`,
          color: c.text, font: { family: "Space Grotesk", size: 13 },
        },
      },
      scales: {
        x: { ticks: { color: c.muted, font: { family: "IBM Plex Mono", size: 10 } }, grid: { color: c.border } },
        y: { ticks: { color: c.muted, font: { family: "IBM Plex Mono", size: 10 } }, grid: { color: c.border } },
      },
    },
  });
}

function drawOverviewChart() {
  const valid = Object.values(state.batchResults).filter((r) => r.n_trades > 0);
  if (!valid.length) {
    showChartPlaceholder("batch", "Run a backtest to see the win-rate overview.");
    return;
  }
  showChartPlaceholder("batch", null);
  const c = themeColors();
  const labels = valid.map((r) => `${r.symbol} ${r.interval}`);
  const winRates = valid.map((r) => r.win_rate);
  const colors = valid.map((r) => (r.expectancy > 0 ? c.green : c.red));

  if (state.charts.batch) state.charts.batch.destroy();
  const ctx = document.getElementById("batchChart").getContext("2d");
  state.charts.batch = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data: winRates, backgroundColor: colors, borderRadius: 4 }] },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        title: {
          display: true, text: "Win rate by symbol/interval (green = positive expectancy)",
          color: c.text, font: { family: "Space Grotesk", size: 13 },
        },
      },
      scales: {
        x: { ticks: { color: c.muted, font: { family: "IBM Plex Mono", size: 9 } }, grid: { display: false } },
        y: { ticks: { color: c.muted, font: { family: "IBM Plex Mono", size: 10 } }, grid: { color: c.border }, suggestedMax: 100 },
      },
    },
  });
}

function drawLiveChart() {
  const results = Object.values(state.liveResults);
  if (!results.length) {
    showChartPlaceholder("live", "Run a scan to see the bull/bear lean by symbol.");
    return;
  }
  showChartPlaceholder("live", null);
  const c = themeColors();
  const labels = results.map((r) => r.symbol);
  const leans = results.map((r) => r.bull_pct);
  const colors = results.map((r) => (r.bull_pct >= 50 ? c.green : c.red));

  if (state.charts.live) state.charts.live.destroy();
  const ctx = document.getElementById("liveChart").getContext("2d");
  state.charts.live = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data: leans, backgroundColor: colors, borderRadius: 4 }] },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        title: { display: true, text: "Current signal lean by symbol", color: c.text, font: { family: "Space Grotesk", size: 13 } },
      },
      scales: {
        x: { ticks: { color: c.muted, font: { family: "IBM Plex Mono", size: 10 } }, grid: { display: false } },
        y: { min: 0, max: 100, ticks: { color: c.muted, font: { family: "IBM Plex Mono", size: 10 } }, grid: { color: c.border } },
      },
    },
  });
}

function redrawAllCharts() {
  if (Object.keys(state.batchResults).length) redrawBatchChart();
  if (Object.keys(state.liveResults).length) drawLiveChart();
}

// ---------- live scan ----------
document.getElementById("runLiveBtn").addEventListener("click", runLive);

function runLive() {
  const symbols = Array.from(state.liveSelected);
  if (!symbols.length) {
    alert("Pick at least one symbol.");
    return;
  }
  const interval = document.getElementById("liveIntervalSelect").value;
  state.liveResults = {};
  renderLiveTable();
  showChartPlaceholder("live", "Scanning…");

  const runBtn = document.getElementById("runLiveBtn");
  runBtn.disabled = true;
  const statusEl = document.getElementById("liveStatus");
  statusEl.textContent = "scanning…";

  const params = new URLSearchParams({ symbols: symbols.join(","), interval });
  const es = new EventSource(`/api/live/stream?${params.toString()}`);

  es.addEventListener("row", (e) => {
    const r = JSON.parse(e.data);
    state.liveResults[r.symbol] = r;
    renderLiveTable();
    updateTicker();
  });
  es.addEventListener("row_error", (e) => {
    const err = JSON.parse(e.data);
    const tbody = document.getElementById("liveTableBody");
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${err.symbol}</td><td colspan="4" style="color:var(--muted)">error: ${err.error}</td>`;
    tbody.appendChild(tr);
  });
  es.addEventListener("done", () => {
    es.close();
    runBtn.disabled = false;
    statusEl.textContent = "Done";
    drawLiveChart();
  });
  es.onerror = () => {
    es.close();
    runBtn.disabled = false;
    statusEl.textContent = "Stream ended";
  };
}

function renderLiveTable() {
  const tbody = document.getElementById("liveTableBody");
  tbody.innerHTML = "";
  const rows = Object.values(state.liveResults);
  if (!rows.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5">No scan yet — pick symbols and click Scan now.</td></tr>`;
    return;
  }
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.classList.add(r.bull_pct >= 50 ? "bull" : "bear");
    const priceStr = r.price < 1 ? r.price.toFixed(6) : r.price.toFixed(2);
    const atrStr = r.atr < 1 ? r.atr.toFixed(6) : r.atr.toFixed(2);
    tr.innerHTML = `
      <td>${r.symbol}</td>
      <td>${priceStr}</td>
      <td class="${r.change >= 0 ? "num-pos" : "num-neg"}">${r.change >= 0 ? "+" : ""}${r.change.toFixed(2)}</td>
      <td class="${r.bull_pct >= 50 ? "lean-long" : "lean-short"}">${r.bull_pct}% ${r.bull_pct >= 50 ? "LONG" : "SHORT"}</td>
      <td>${atrStr}</td>`;
    tbody.appendChild(tr);
  }
}

// ---------- CSV export ----------
function downloadCsv(filename, rows) {
  const csv = rows.map((row) => row.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

document.getElementById("exportBatchBtn").addEventListener("click", () => {
  const results = Object.values(state.batchResults);
  if (!results.length) {
    alert("Run a batch backtest first — nothing to export yet.");
    return;
  }
  const settings = state.lastRunSettings;
  const rows = [
    ["exported_at", new Date().toISOString()],
    ["days", settings.days], ["entry_threshold_pct", settings.threshold],
    ["stop_atr_multiple", settings.stop_atr], ["target_atr_multiple", settings.target_atr],
    [],
    ["symbol", "interval", "trades", "win_rate_pct", "avg_win_pct", "avg_loss_pct", "expectancy_pct_per_trade", "sum_return_pct"],
  ];
  for (const r of results) {
    if (r.n_trades === 0) rows.push([r.symbol, r.interval, 0, "", "", "", "", ""]);
    else rows.push([r.symbol, r.interval, r.n_trades, r.win_rate.toFixed(2), r.avg_win.toFixed(3),
                     r.avg_loss.toFixed(3), r.expectancy.toFixed(4), r.total_pnl.toFixed(3)]);
  }
  downloadCsv(`crypto_analyser_backtest_${Date.now()}.csv`, rows);
});

document.getElementById("exportLiveBtn").addEventListener("click", () => {
  const results = Object.values(state.liveResults);
  if (!results.length) {
    alert("Run a scan first — nothing to export yet.");
    return;
  }
  const rows = [
    ["scanned_at", new Date().toISOString()],
    ["interval", document.getElementById("liveIntervalSelect").value],
    [],
    ["symbol", "price", "change_24h_pct", "bull_lean_pct", "side", "atr"],
  ];
  for (const r of results) {
    rows.push([r.symbol, r.price, r.change.toFixed(2), r.bull_pct, r.bull_pct >= 50 ? "LONG" : "SHORT", r.atr]);
  }
  downloadCsv(`crypto_analyser_livescan_${Date.now()}.csv`, rows);
});

// ---------- init ----------
applyTheme(state.theme);
batchPickerHandle = initPicker("batch", state.batchSelected);
livePickerHandle = initPicker("live", state.liveSelected);
renderBatchTable();
renderLiveTable();
loadSymbolUniverse(false);
pollUsage();
