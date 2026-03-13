#!/usr/bin/env python3
"""
Mr. Martingale Local Trading Console (Bloomberg-style)

Run:
  cd Personal/Financial/Portfolio/HighRisk/Quant
  python3 -m streamlit run execution/console_app.py
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution import config as cfg
from execution import grid_state as gs_mod
from execution import hl_client as hl
from execution import command_bus


# Hyperliquid-ish palette
H_BG = "#07090c"
H_PANEL = "#0d1219"
H_GRID = "#161d28"
H_TEXT = "#d6deea"
H_MUTED = "#7e8da4"
H_UP = "#00c087"
H_DOWN = "#f6465d"
H_BLUE = "#2f80ed"
H_PURPLE = "#9b51e0"
H_TEAL = "#00c7d4"


# ─────────────────────────────────────────────────────────────────────────────
# Styling / page
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mr. Martingale Console",
    page_icon="💹",
    layout="wide",
)

st.markdown(
    """
    <style>
      :root {
        --bg: #07090c;
        --bg2: #0b0f14;
        --panel: #0d1219;
        --border: #1a2130;
        --text: #d6deea;
        --muted: #7e8da4;
        --green: #00c087;
        --red: #f6465d;
        --blue: #2f80ed;
        --purple: #9b51e0;
      }
      .stApp {
        background: var(--bg);
        color: var(--text);
      }
      [data-testid="stAppViewContainer"] {
        background: var(--bg);
      }
      [data-testid="stHeader"] {
        background: transparent;
      }
      .block-container {
        padding-top: 0.8rem;
        padding-bottom: 0.8rem;
      }
      .main-title {
        font-weight: 700;
        letter-spacing: 0.2px;
        font-size: 1.2rem;
        color: var(--text);
        margin-bottom: 0.15rem;
      }
      .subtle {
        color: var(--muted);
        font-size: 0.86rem;
      }
      div[data-testid="metric-container"] {
        background: var(--panel);
        border: 1px solid var(--border);
        padding: 0.55rem 0.65rem;
        border-radius: 8px;
      }
      div[data-testid="metric-container"] label {
        color: var(--muted) !important;
      }
      div[data-testid="stDataFrame"] {
        border: 1px solid var(--border);
        border-radius: 8px;
        overflow: hidden;
      }
      div[data-testid="stJson"] {
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--panel);
        padding: 0.25rem;
      }
      .stButton > button {
        border-radius: 7px;
        border: 1px solid var(--border);
        background: var(--panel);
        color: var(--text);
      }
      .stButton > button:hover {
        border-color: #2d3748;
        background: #111826;
      }
      [data-testid="stSidebar"] {
        background: var(--bg2);
        border-left: 1px solid var(--border);
      }
      #MainMenu {visibility: hidden;}
      footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# Parsers / data access
# ─────────────────────────────────────────────────────────────────────────────

LOG_PATH = cfg.STATE_FILE.parent / "grid_bot.log"

POLL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+grid_bot\s+INFO\s+"
    r"BTC \$(?P<price>[0-9,\.]+) \| ↓EMA34 (?P<dema>[+-][0-9\.]+)% ↓SMA14 (?P<dsma>[+-][0-9\.]+)% \| "
    r"↑EMA34 (?P<uema>[+-][0-9\.]+)% ↑SMA14 (?P<usma>[+-][0-9\.]+)% \| "
    r"Long: (?P<long_state>\w+) \| Short: (?P<short_state>\w+)"
)

TRIGGER_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+grid_bot\s+INFO\s+"
    r"TRIGGER (?P<side>LONG|SHORT): BTC \$(?P<price>[0-9,\.]+) \| EMA34 \$(?P<ema>[0-9,\.]+) \| SMA14 \$(?P<sma>[0-9,\.]+)"
)

TP_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+grid_bot\s+INFO\s+"
    r"(?P<side>LONG|SHORT) TP HIT @ \$(?P<price>[0-9,\.]+) \| ~\$(?P<pnl>[+-][0-9\.]+)"
)

LVL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+grid_bot\s+INFO\s+"
    r"(?P<side>LONG|SHORT) L(?P<level>\d+) filled @ \$(?P<price>[0-9,\.]+)"
)

ERR_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+grid_bot\s+ERROR\s+(?P<msg>.*)$"
)


def _parse_ts(ts: str) -> pd.Timestamp:
    return pd.Timestamp(ts).tz_localize("America/Denver", ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")


@st.cache_data(ttl=60)
def infer_bot_start_ts() -> pd.Timestamp:
    """Infer Mr. Martingale live-start timestamp from first relevant log line."""
    if not LOG_PATH.exists():
        return pd.Timestamp.utcnow() - pd.Timedelta(days=30)

    lines = LOG_PATH.read_text(errors="ignore").splitlines()
    ts_pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")
    for line in lines:
        if "Mr Martingale" in line or "TRIGGER " in line or "TP HIT" in line:
            m = ts_pat.search(line)
            if m:
                return _parse_ts(m.group(1))

    return pd.Timestamp.utcnow() - pd.Timedelta(days=30)


@st.cache_data(ttl=20)
def load_poll_df(max_rows: int = 6000) -> pd.DataFrame:
    if not LOG_PATH.exists():
        return pd.DataFrame()

    rows: List[dict] = []
    lines = LOG_PATH.read_text(errors="ignore").splitlines()[-max_rows:]
    for line in lines:
        m = POLL_RE.search(line)
        if not m:
            continue
        gd = m.groupdict()
        rows.append({
            "ts": _parse_ts(gd["ts"]),
            "price": float(gd["price"].replace(",", "")),
            "down_ema": float(gd["dema"]),
            "down_sma": float(gd["dsma"]),
            "up_ema": float(gd["uema"]),
            "up_sma": float(gd["usma"]),
            "long_state": gd["long_state"],
            "short_state": gd["short_state"],
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).drop_duplicates(subset=["ts"], keep="last").sort_values("ts")
    return df.reset_index(drop=True)


@st.cache_data(ttl=20)
def load_event_df(max_rows: int = 12000) -> pd.DataFrame:
    if not LOG_PATH.exists():
        return pd.DataFrame()

    events: List[dict] = []
    lines = LOG_PATH.read_text(errors="ignore").splitlines()[-max_rows:]

    for line in lines:
        m = TRIGGER_RE.search(line)
        if m:
            gd = m.groupdict()
            events.append({
                "ts": _parse_ts(gd["ts"]),
                "type": "trigger",
                "side": gd["side"].lower(),
                "price": float(gd["price"].replace(",", "")),
                "detail": "trigger",
            })
            continue

        m = TP_RE.search(line)
        if m:
            gd = m.groupdict()
            events.append({
                "ts": _parse_ts(gd["ts"]),
                "type": "tp",
                "side": gd["side"].lower(),
                "price": float(gd["price"].replace(",", "")),
                "pnl": float(gd["pnl"]),
                "detail": "tp_hit",
            })
            continue

        m = LVL_RE.search(line)
        if m:
            gd = m.groupdict()
            events.append({
                "ts": _parse_ts(gd["ts"]),
                "type": "level_fill",
                "side": gd["side"].lower(),
                "price": float(gd["price"].replace(",", "")),
                "level": int(gd["level"]),
                "detail": f"L{gd['level']} fill",
            })
            continue

        m = ERR_RE.search(line)
        if m:
            gd = m.groupdict()
            events.append({
                "ts": _parse_ts(gd["ts"]),
                "type": "error",
                "side": "",
                "price": np.nan,
                "detail": gd["msg"],
            })

    if not events:
        return pd.DataFrame()

    return pd.DataFrame(events).sort_values("ts").reset_index(drop=True)


@st.cache_data(ttl=15)
def get_market_candles(n: int = 220) -> pd.DataFrame:
    candles = hl.get_candles(cfg.COIN, cfg.CANDLE_INTERVAL, n=n)
    rows = []
    for c in candles:
        # Hyperliquid candle schema uses short keys.
        t = c.get("t") or c.get("time") or c.get("open_time_ms")
        o = c.get("o") or c.get("open")
        h = c.get("h") or c.get("high")
        l = c.get("l") or c.get("low")
        cl = c.get("c") or c.get("close")
        v = c.get("v") or c.get("volume") or 0
        if t is None:
            continue
        rows.append({
            "ts": pd.to_datetime(int(float(t)), unit="ms", utc=True),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(cl),
            "volume": float(v),
        })

    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    if df.empty:
        return df

    df["ema34"] = df["close"].ewm(span=cfg.EMA_SPAN, adjust=False).mean()
    df["sma14"] = df["close"].rolling(cfg.MA_PERIOD).mean()
    df["long_trigger_px"] = np.minimum(
        df["ema34"] * (1 - cfg.LONG_TRIGGER_PCT / 100.0),
        df["sma14"] * (1 - cfg.LONG_TRIGGER_PCT / 100.0),
    )
    df["short_trigger_px"] = np.maximum(
        df["ema34"] * (1 + cfg.SHORT_TRIGGER_PCT / 100.0),
        df["sma14"] * (1 + cfg.SHORT_TRIGGER_PCT / 100.0),
    )
    return df


@st.cache_data(ttl=20)
def get_fills_df() -> pd.DataFrame:
    fills = hl.info_client.user_fills(cfg.HL_MAIN_ADDRESS)
    if not fills:
        return pd.DataFrame()

    out = pd.DataFrame(fills)
    if out.empty:
        return out

    out = out[out["coin"] == cfg.COIN].copy()
    if out.empty:
        return out

    out["ts"] = pd.to_datetime(out["time"].astype(np.int64), unit="ms", utc=True)
    out["px"] = out["px"].astype(float)
    out["sz"] = out["sz"].astype(float)
    out["fee"] = out["fee"].astype(float)
    out["closedPnl"] = out["closedPnl"].astype(float)
    out["netPnl"] = out["closedPnl"] - out["fee"]
    out["dir"] = out["dir"].fillna("")

    start_ts = infer_bot_start_ts() - pd.Timedelta(hours=1)
    out = out[out["ts"] >= start_ts]

    return out.sort_values("ts").reset_index(drop=True)


@st.cache_data(ttl=60)
def get_funding_df(days: int = 90) -> pd.DataFrame:
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_from_days = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    start_from_bot = infer_bot_start_ts() - pd.Timedelta(hours=1)
    start_ts = max(start_from_days, start_from_bot)
    start_ms = int(start_ts.timestamp() * 1000)
    rows = hl.info_client.user_funding_history(cfg.HL_MAIN_ADDRESS, startTime=start_ms, endTime=end_ms)
    if not rows:
        return pd.DataFrame()

    parsed = []
    for r in rows:
        delta = r.get("delta", {})
        if delta.get("type") != "funding":
            continue
        if delta.get("coin") != cfg.COIN:
            continue
        usdc = float(delta.get("usdc", 0.0))
        parsed.append({
            "ts": pd.to_datetime(int(r["time"]), unit="ms", utc=True),
            "usdc": usdc,
            "expense": max(0.0, -usdc),
            "credit": max(0.0, usdc),
        })

    if not parsed:
        return pd.DataFrame()

    return pd.DataFrame(parsed).sort_values("ts").reset_index(drop=True)


@dataclass
class PerfStats:
    realized_gross: float
    fees: float
    funding_expense: float
    funding_credit: float
    realized_net: float
    expense_ratio: float
    win_rate: float                 # legacy aggregate win rate (all close fills)
    trade_count: int                # legacy aggregate close-fill count
    strategy_win_rate: float
    strategy_trade_count: int
    strategy_wins: int
    strategy_losses: int
    strategy_net: float
    operational_count: int
    operational_net: float


def classify_close_fills(close_fills: pd.DataFrame, all_fills: pd.DataFrame) -> pd.DataFrame:
    """
    Separate true strategy closes from operational micro-roundtrip artifacts.

    Heuristic for operational scratch:
    - close fill has matching same-size open fill on same side
    - open happened within <= 90s before close
    - absolute price move <= $2
    - absolute closedPnl <= $0.05

    This preserves real strategy outcomes while isolating startup/process churn artifacts.
    """
    if close_fills.empty:
        out = close_fills.copy()
        out["category"] = []
        out["note"] = []
        out["hold_sec"] = []
        return out

    out = close_fills.copy()
    out["category"] = "strategy"
    out["note"] = ""
    out["hold_sec"] = np.nan

    opens = all_fills[all_fills["dir"].str.contains("Open", case=False, na=False)].copy()
    if opens.empty:
        return out

    opens = opens.sort_values("ts")

    for idx, row in out.iterrows():
        d = str(row.get("dir", "")).lower()
        if "close long" in d:
            open_mask = opens["dir"].str.contains("Open Long", case=False, na=False)
        elif "close short" in d:
            open_mask = opens["dir"].str.contains("Open Short", case=False, na=False)
        else:
            continue

        candidates = opens[open_mask].copy()
        if candidates.empty:
            continue

        ts = row["ts"]
        candidates = candidates[(candidates["ts"] <= ts) & (candidates["ts"] >= ts - pd.Timedelta(seconds=120))]
        if candidates.empty:
            continue

        sz = float(row.get("sz", 0.0) or 0.0)
        candidates = candidates[(candidates["sz"] - sz).abs() <= 1e-8]
        if candidates.empty:
            continue

        opn = candidates.sort_values("ts").iloc[-1]
        hold_sec = float((ts - opn["ts"]).total_seconds())
        px_move = abs(float(row.get("px", 0.0) or 0.0) - float(opn.get("px", 0.0) or 0.0))
        cp = abs(float(row.get("closedPnl", 0.0) or 0.0))

        if hold_sec <= 90 and px_move <= 2.0 and cp <= 0.05:
            out.at[idx, "category"] = "operational"
            out.at[idx, "note"] = "micro round-trip"
            out.at[idx, "hold_sec"] = hold_sec

    return out


@st.cache_data(ttl=20)
def compute_perf_stats() -> Tuple[pd.DataFrame, PerfStats]:
    fills = get_fills_df()
    funding = get_funding_df(days=180)

    if fills.empty:
        empty_curve = pd.DataFrame(columns=["ts", "equity", "category", "note", "hold_sec"])
        stats = PerfStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        return empty_curve, stats

    close_fills = fills[(fills["closedPnl"] != 0) | (fills["dir"].str.contains("Close", case=False, na=False))].copy()
    if close_fills.empty:
        equity_curve = pd.DataFrame({
            "ts": [pd.Timestamp.utcnow()],
            "equity": [cfg.INITIAL_EQUITY_USD],
            "category": ["none"],
            "note": [""],
            "hold_sec": [np.nan],
        })
        stats = PerfStats(0, float(fills["fee"].sum()), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        return equity_curve, stats

    close_fills = close_fills.sort_values("ts").copy()
    close_fills = classify_close_fills(close_fills, fills)
    close_fills["equity"] = cfg.INITIAL_EQUITY_USD + close_fills["netPnl"].cumsum()

    # Aggregate (legacy all close fills)
    fees = float(close_fills["fee"].sum())
    realized_gross = float(close_fills["closedPnl"].sum())
    funding_expense = float(funding["expense"].sum()) if not funding.empty else 0.0
    funding_credit = float(funding["credit"].sum()) if not funding.empty else 0.0

    realized_net = realized_gross - fees - funding_expense + funding_credit
    gross_profit_only = float(close_fills.loc[close_fills["closedPnl"] > 0, "closedPnl"].sum())
    expense_ratio = (fees + funding_expense) / gross_profit_only if gross_profit_only > 0 else 0.0

    wins_all = int((close_fills["netPnl"] > 0).sum())
    trade_count_all = int(len(close_fills))
    win_rate_all = wins_all / trade_count_all if trade_count_all else 0.0

    # Strategy-only vs operational scratches
    strategy = close_fills[close_fills["category"] != "operational"].copy()
    ops = close_fills[close_fills["category"] == "operational"].copy()

    strategy_wins = int((strategy["netPnl"] > 0).sum()) if not strategy.empty else 0
    strategy_losses = int((strategy["netPnl"] <= 0).sum()) if not strategy.empty else 0
    strategy_count = int(len(strategy))
    strategy_win_rate = (strategy_wins / strategy_count) if strategy_count else 0.0
    strategy_net = float(strategy["netPnl"].sum()) if not strategy.empty else 0.0

    operational_count = int(len(ops))
    operational_net = float(ops["netPnl"].sum()) if not ops.empty else 0.0

    stats = PerfStats(
        realized_gross=realized_gross,
        fees=fees,
        funding_expense=funding_expense,
        funding_credit=funding_credit,
        realized_net=realized_net,
        expense_ratio=expense_ratio,
        win_rate=win_rate_all,
        trade_count=trade_count_all,
        strategy_win_rate=strategy_win_rate,
        strategy_trade_count=strategy_count,
        strategy_wins=strategy_wins,
        strategy_losses=strategy_losses,
        strategy_net=strategy_net,
        operational_count=operational_count,
        operational_net=operational_net,
    )

    keep_cols = ["ts", "equity", "closedPnl", "fee", "netPnl", "dir", "px", "sz", "oid", "category", "note", "hold_sec"]
    for c in keep_cols:
        if c not in close_fills.columns:
            close_fills[c] = np.nan

    return close_fills[keep_cols], stats


@st.cache_data(ttl=20)
def project_equity(equity_curve: pd.DataFrame, horizon_days: int = 60) -> pd.DataFrame:
    if equity_curve.empty:
        return pd.DataFrame()

    fills = get_fills_df()
    funding = get_funding_df(days=180)
    close_fills = fills[(fills["closedPnl"] != 0) | (fills["dir"].str.contains("Close", case=False, na=False))].copy()

    if close_fills.empty:
        return pd.DataFrame()

    close_fills["date"] = close_fills["ts"].dt.floor("D")
    daily = close_fills.groupby("date", as_index=False).agg(
        gross_close=("closedPnl", "sum"),
        fees=("fee", "sum"),
    )

    if funding.empty:
        daily["funding_usdc"] = 0.0
    else:
        f = funding.copy()
        f["date"] = f["ts"].dt.floor("D")
        f_daily = f.groupby("date", as_index=False).agg(funding_usdc=("usdc", "sum"))
        daily = daily.merge(f_daily, on="date", how="left")
        daily["funding_usdc"] = daily["funding_usdc"].fillna(0.0)

    daily["expense"] = daily["fees"] + (-daily["funding_usdc"]).clip(lower=0.0)
    daily["net"] = daily["gross_close"] - daily["fees"] + daily["funding_usdc"]
    daily = daily.sort_values("date")

    lookback = min(30, len(daily))
    recent = daily.tail(lookback)
    avg_daily_net = float(recent["net"].mean()) if lookback else 0.0
    avg_daily_expense = float(recent["expense"].mean()) if lookback else 0.0
    avg_daily_gross = avg_daily_net + avg_daily_expense

    base_equity = float(equity_curve["equity"].iloc[-1])
    start_day = pd.Timestamp.now(tz="UTC").floor("D") + pd.Timedelta(days=1)
    proj_dates = [start_day + pd.Timedelta(days=i) for i in range(horizon_days)]

    net_vals = []
    gross_vals = []
    e_net = base_equity
    e_gross = base_equity

    for _ in proj_dates:
        e_net += avg_daily_net
        e_gross += avg_daily_gross
        net_vals.append(e_net)
        gross_vals.append(e_gross)

    proj = pd.DataFrame({
        "ts": proj_dates,
        "proj_net_equity": net_vals,
        "proj_gross_equity": gross_vals,
        "avg_daily_net": avg_daily_net,
        "avg_daily_expense": avg_daily_expense,
    })
    return proj


# ─────────────────────────────────────────────────────────────────────────────
# Charts
# ─────────────────────────────────────────────────────────────────────────────

def make_price_chart(candles: pd.DataFrame, fills: pd.DataFrame, open_orders: List[dict]) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.78, 0.22],
    )

    if not candles.empty:
        fig.add_trace(
            go.Candlestick(
                x=candles["ts"],
                open=candles["open"],
                high=candles["high"],
                low=candles["low"],
                close=candles["close"],
                name="Price",
                increasing_line_color=H_UP,
                increasing_fillcolor=H_UP,
                decreasing_line_color=H_DOWN,
                decreasing_fillcolor=H_DOWN,
                whiskerwidth=0.2,
                opacity=0.9,
            ),
            row=1,
            col=1,
        )

        fig.add_trace(
            go.Scatter(
                x=candles["ts"],
                y=candles["ema34"],
                mode="lines",
                name="EMA34",
                line=dict(color=H_BLUE, width=1.2),
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=candles["ts"],
                y=candles["sma14"],
                mode="lines",
                name="SMA14",
                line=dict(color=H_PURPLE, width=1.2),
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=candles["ts"],
                y=candles["long_trigger_px"],
                mode="lines",
                name="Long trigger",
                line=dict(color=H_UP, width=1.0, dash="dot"),
                opacity=0.5,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=candles["ts"],
                y=candles["short_trigger_px"],
                mode="lines",
                name="Short trigger",
                line=dict(color=H_DOWN, width=1.0, dash="dot"),
                opacity=0.5,
            ),
            row=1,
            col=1,
        )

    if fills is not None and not fills.empty and not candles.empty:
        min_ts = candles["ts"].min()
        max_ts = candles["ts"].max()
        f = fills[(fills["ts"] >= min_ts) & (fills["ts"] <= max_ts)].copy()
        if not f.empty:
            open_long = f[f["dir"].str.contains("Open Long", case=False, na=False)]
            open_short = f[f["dir"].str.contains("Open Short", case=False, na=False)]
            close_fills = f[(f["closedPnl"] != 0) | (f["dir"].str.contains("Close", case=False, na=False))].copy()

            if not open_long.empty:
                fig.add_trace(
                    go.Scatter(
                        x=open_long["ts"],
                        y=open_long["px"],
                        mode="markers",
                        name="Long entry",
                        marker=dict(symbol="circle", size=5, color=H_UP),
                    ),
                    row=1,
                    col=1,
                )
            if not open_short.empty:
                fig.add_trace(
                    go.Scatter(
                        x=open_short["ts"],
                        y=open_short["px"],
                        mode="markers",
                        name="Short entry",
                        marker=dict(symbol="circle", size=5, color=H_DOWN),
                    ),
                    row=1,
                    col=1,
                )

            if not close_fills.empty:
                close_fills["bar_color"] = np.where(close_fills["netPnl"] >= 0, H_UP, H_DOWN)
                fig.add_trace(
                    go.Bar(
                        x=close_fills["ts"],
                        y=close_fills["netPnl"],
                        marker_color=close_fills["bar_color"],
                        name="Net PnL",
                        opacity=0.75,
                    ),
                    row=2,
                    col=1,
                )

    # Active orders (minimal horizontal guides)
    if open_orders and not candles.empty:
        x0 = candles["ts"].min()
        x1 = candles["ts"].max()
        for o in open_orders:
            px = float(o.get("limitPx", 0.0))
            side = "BUY" if o.get("is_buy") else "SELL"
            reduce_only = o.get("reduce_only", False)
            color = H_TEAL if side == "BUY" else "#f39c12"
            dash = "dash" if reduce_only else "dot"
            tag = "TP" if reduce_only else "L"
            fig.add_trace(
                go.Scatter(
                    x=[x0, x1],
                    y=[px, px],
                    mode="lines",
                    line=dict(color=color, width=0.8, dash=dash),
                    name=f"{side} {tag}",
                    showlegend=False,
                    opacity=0.65,
                ),
                row=1,
                col=1,
            )

    fig.update_layout(
        template="plotly_dark",
        height=700,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=H_BG,
        plot_bgcolor=H_BG,
        font=dict(color=H_TEXT, size=11),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=10, color=H_MUTED),
        ),
        dragmode="pan",
    )

    fig.update_xaxes(
        rangeslider_visible=False,
        showgrid=True,
        gridcolor=H_GRID,
        zeroline=False,
        showline=False,
        tickfont=dict(color=H_MUTED),
        row=1,
        col=1,
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor=H_GRID,
        zeroline=False,
        showline=False,
        tickfont=dict(color=H_MUTED),
        row=2,
        col=1,
    )
    fig.update_yaxes(
        title_text="",
        showgrid=True,
        gridcolor=H_GRID,
        zeroline=False,
        tickfont=dict(color=H_MUTED),
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="",
        showgrid=True,
        gridcolor=H_GRID,
        zeroline=True,
        zerolinecolor=H_GRID,
        tickfont=dict(color=H_MUTED),
        row=2,
        col=1,
    )

    return fig


def make_equity_chart(equity_curve: pd.DataFrame, proj: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    if not equity_curve.empty:
        fig.add_trace(
            go.Scatter(
                x=equity_curve["ts"],
                y=equity_curve["equity"],
                mode="lines",
                name="History",
                line=dict(color=H_UP, width=2.2),
            )
        )

    if proj is not None and not proj.empty:
        fig.add_trace(
            go.Scatter(
                x=proj["ts"],
                y=proj["proj_net_equity"],
                mode="lines",
                name="Projected net",
                line=dict(color=H_BLUE, width=2.0, dash="dash"),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=proj["ts"],
                y=proj["proj_gross_equity"],
                mode="lines",
                name="Projected gross",
                line=dict(color=H_MUTED, width=1.2, dash="dot"),
            )
        )

    fig.update_layout(
        template="plotly_dark",
        height=320,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=H_BG,
        plot_bgcolor=H_BG,
        font=dict(color=H_TEXT, size=11),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=10, color=H_MUTED),
        ),
    )
    fig.update_xaxes(showgrid=True, gridcolor=H_GRID, tickfont=dict(color=H_MUTED), title_text="")
    fig.update_yaxes(showgrid=True, gridcolor=H_GRID, tickfont=dict(color=H_MUTED), title_text="")
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Live snapshot
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=10)
def get_live_snapshot() -> Dict[str, object]:
    price = hl.get_mid_price(cfg.COIN)
    bal = hl.get_account_balance()
    pos = hl.get_position(cfg.COIN)
    orders = hl.get_open_orders(cfg.COIN)

    candles = get_market_candles(n=80)
    ema = float(candles["ema34"].dropna().iloc[-1]) if not candles.empty else np.nan
    sma = float(candles["sma14"].dropna().iloc[-1]) if not candles.empty else np.nan

    long_trigger_px = min(ema * (1 - cfg.LONG_TRIGGER_PCT / 100.0), sma * (1 - cfg.LONG_TRIGGER_PCT / 100.0)) if np.isfinite(ema) and np.isfinite(sma) else np.nan
    short_trigger_px = max(ema * (1 + cfg.SHORT_TRIGGER_PCT / 100.0), sma * (1 + cfg.SHORT_TRIGGER_PCT / 100.0)) if np.isfinite(ema) and np.isfinite(sma) else np.nan

    return {
        "price": price,
        "balance": bal,
        "position": pos,
        "open_orders": orders,
        "ema34": ema,
        "sma14": sma,
        "long_trigger_px": long_trigger_px,
        "short_trigger_px": short_trigger_px,
    }


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("<div class='main-title'>Mr. Martingale</div>", unsafe_allow_html=True)
st.markdown(f"<div class='subtle'>Hyperliquid-style local terminal • v{cfg.BOT_VERSION} • {cfg.COIN}-USDC</div>", unsafe_allow_html=True)

# Shared data load
snap = get_live_snapshot()
state = gs_mod.load()
fills_df = get_fills_df()
candles_df = get_market_candles(n=220)
equity_curve, perf = compute_perf_stats()
proj_df = project_equity(equity_curve, horizon_days=60)
oo = snap["open_orders"]

unreal = 0.0
pos_size = float(snap["position"].get("size", 0.0) or 0.0)
entry_px = float(snap["position"].get("entry_px", 0.0) or 0.0)
px = float(snap["price"])
if pos_size > 0:
    unreal = pos_size * (px - entry_px)
elif pos_size < 0:
    unreal = abs(pos_size) * (entry_px - px)

main_col, control_col = st.columns([4, 1.18], gap="medium")

with control_col:
    st.markdown("#### Manual")
    arm = st.toggle("Arm", value=False, help="Safety toggle required for live actions.")

    if st.button("Manual Long", type="primary", disabled=not arm, width="stretch"):
        p = command_bus.enqueue("manual_long", source="console_app")
        st.success(f"Queued: {p.name}")

    if st.button("Manual Short", type="primary", disabled=not arm, width="stretch"):
        p = command_bus.enqueue("manual_short", source="console_app")
        st.success(f"Queued: {p.name}")

    if st.button("Manual Close", disabled=not arm, width="stretch"):
        p = command_bus.enqueue("manual_close", source="console_app")
        st.warning(f"Queued: {p.name}")

    st.caption("Commands are executed by the live bot process.")

    pend = command_bus.list_pending()
    st.metric("Pending", len(pend))

    st.markdown("#### Trigger Levels")
    st.metric("Long", f"${snap['long_trigger_px']:,.1f}")
    st.metric("Short", f"${snap['short_trigger_px']:,.1f}")

    st.markdown("#### Position")
    st.metric("Size", f"{pos_size:+.5f}")
    st.metric("Entry", f"${entry_px:,.1f}")

with main_col:
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("BTC", f"${snap['price']:,.1f}")
    m2.metric("Equity", f"${snap['balance']:,.2f}")
    m3.metric("Realized", f"${perf.realized_net:+,.2f}")
    m4.metric("Unrealized", f"${unreal:+,.2f}")
    m5.metric("Strategy Win %", f"{perf.strategy_win_rate*100:.1f}%", delta=f"ops scratches: {perf.operational_count}")
    m6.metric("Expense %", f"{perf.expense_ratio*100:.2f}%")

    price_fig = make_price_chart(candles_df, fills_df, oo)
    st.plotly_chart(price_fig, width="stretch")

    eq_fig = make_equity_chart(equity_curve, proj_df)
    st.plotly_chart(eq_fig, width="stretch")

# Detail tabs
orders_tab, trades_tab, analytics_tab, system_tab = st.tabs(["Orders", "Trades", "Analytics", "System"])

with orders_tab:
    c1, c2 = st.columns([1.2, 1], gap="medium")
    with c1:
        st.markdown("##### Strategy State")
        st.json({
            "long_active": state.long_grid.active,
            "short_active": state.short_grid.active,
            "long_grid": {
                "blended": state.long_grid.blended_entry,
                "tp": state.long_grid.tp_price,
                "qty": state.long_grid.total_qty,
                "max_level": state.long_grid.max_level_hit() if state.long_grid.active else 0,
            },
            "short_grid": {
                "blended": state.short_grid.blended_entry,
                "tp": state.short_grid.tp_price,
                "qty": state.short_grid.total_qty,
                "max_level": state.short_grid.max_level_hit() if state.short_grid.active else 0,
            },
        }, expanded=False)
    with c2:
        st.markdown("##### Active Orders")
        if oo:
            odf = pd.DataFrame(oo)
            show_cols = [c for c in ["oid", "coin", "is_buy", "reduce_only", "limitPx", "sz"] if c in odf.columns]
            st.dataframe(odf[show_cols], width="stretch", hide_index=True)
        else:
            st.info("No open orders")

with trades_tab:
    st.markdown("##### Trade History")
    if not equity_curve.empty:
        th = equity_curve.copy().sort_values("ts", ascending=False)
        th["ts"] = th["ts"].dt.tz_convert("America/Denver").dt.strftime("%Y-%m-%d %H:%M:%S")
        st.dataframe(
            th[["ts", "dir", "px", "sz", "closedPnl", "fee", "netPnl", "category", "equity"]],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No closed trades yet")

with analytics_tab:
    col_a, col_b = st.columns([1, 1], gap="medium")
    with col_a:
        st.markdown("##### Expense Breakdown")
        exp_df = pd.DataFrame([
            {"Metric": "Trading Fees", "USDC": perf.fees},
            {"Metric": "Funding Expense", "USDC": perf.funding_expense},
            {"Metric": "Funding Credit", "USDC": perf.funding_credit},
            {"Metric": "Realized Gross PnL", "USDC": perf.realized_gross},
            {"Metric": "Realized Net PnL", "USDC": perf.realized_net},
            {"Metric": "Expense Ratio", "USDC": perf.expense_ratio * 100.0},
        ])
        st.dataframe(exp_df, width="stretch", hide_index=True)

        st.markdown("##### Outcome Classification")
        cls_df = pd.DataFrame([
            {"Metric": "Strategy closes", "Value": perf.strategy_trade_count},
            {"Metric": "Strategy wins", "Value": perf.strategy_wins},
            {"Metric": "Strategy losses", "Value": perf.strategy_losses},
            {"Metric": "Strategy win rate %", "Value": round(perf.strategy_win_rate * 100, 2)},
            {"Metric": "Strategy net PnL", "Value": round(perf.strategy_net, 6)},
            {"Metric": "Operational scratches", "Value": perf.operational_count},
            {"Metric": "Operational net", "Value": round(perf.operational_net, 6)},
        ])
        st.dataframe(cls_df, width="stretch", hide_index=True)

    with col_b:
        st.markdown("##### Projection Notes")
        if proj_df is not None and not proj_df.empty:
            avg_net = float(proj_df["avg_daily_net"].iloc[0])
            avg_exp = float(proj_df["avg_daily_expense"].iloc[0])
            st.markdown(
                f"- Avg projected net: **${avg_net:+.2f}/day**  \n"
                f"- Estimated expense drag: **${avg_exp:.2f}/day**  \n"
                f"- Dashed line = net projection after expenses"
            )
        else:
            st.write("Insufficient close-trade history for projection.")

        st.markdown("##### Next important features")
        st.markdown(
            "- Hard kill-switch + max daily loss\n"
            "- Heartbeat watchdog + failover alerts\n"
            "- Execution quality monitor (slippage / latency)\n"
            "- Regime panel (trend/chop/funding/OI)"
        )

with system_tab:
    st.markdown("##### Command Audit")
    recent_cmd = command_bus.recent_processed(limit=10)
    if recent_cmd:
        rc = pd.DataFrame(recent_cmd)
        keep = [c for c in ["processed_at", "action", "status", "message", "source"] if c in rc.columns]
        st.dataframe(rc[keep], width="stretch", hide_index=True)
    else:
        st.info("No processed commands yet")

    st.caption("This terminal is local-only and controls live trading. Use cautiously.")
