"""Strategy library for the backtest lab.

Each strategy declares its parameters (type, default, bounds) so the UI can build
input forms and optimisation grids automatically, and implements
`signal(ctx, params) -> np.ndarray` returning the desired direction (-1, 0, +1)
decided at each bar's close. The engine executes it at the NEXT bar's open, so a
strategy can never trade on information it would not have had.

Adding a strategy = one function + one `Strategy(...)` entry in STRATEGIES.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

MODE = {"name": "mode", "label": "Direction", "type": "choice", "default": "long_short",
        "choices": ["long_short", "long_only", "short_only"]}


@dataclass
class Context:
    """Everything a strategy may look at, aligned to the daily price index (no look-ahead)."""
    df: pd.DataFrame                  # date, open, high, low, close
    cot_net: pd.Series | None = None  # managed-money net, available from report release date
    inv_z: pd.Series | None = None    # inventory deviation z-score vs 5y norm, from release date

    @property
    def close(self) -> np.ndarray:
        return self.df["close"].to_numpy(float)


@dataclass
class Strategy:
    key: str
    name: str
    family: str                       # Trend / Mean reversion / Quantamental
    description: str
    params: list[dict]
    fn: Callable = field(repr=False)
    requires: str = ""                # "cot" | "inventory" | ""

    def defaults(self) -> dict:
        return {p["name"]: p["default"] for p in self.params}

    def spec(self) -> dict:
        d = asdict(self)
        d.pop("fn")
        return d

    def signal(self, ctx: Context, params: dict) -> np.ndarray:
        p = {**self.defaults(), **(params or {})}
        for spec in self.params:  # coerce types coming from JSON/UI
            if spec["type"] == "int":
                p[spec["name"]] = int(round(float(p[spec["name"]])))
            elif spec["type"] == "float":
                p[spec["name"]] = float(p[spec["name"]])
        raw = np.nan_to_num(np.asarray(self.fn(ctx, p), dtype=float))
        mode = p.get("mode", "long_short")
        if mode == "long_only":
            raw = np.clip(raw, 0, 1)
        elif mode == "short_only":
            raw = np.clip(raw, -1, 0)
        return np.sign(raw)


def _int(name, label, default, lo, hi, step=1):
    return {"name": name, "label": label, "type": "int", "default": default, "min": lo, "max": hi, "step": step}


def _float(name, label, default, lo, hi, step):
    return {"name": name, "label": label, "type": "float", "default": default, "min": lo, "max": hi, "step": step}


def _hold_state(enter_long: np.ndarray, enter_short: np.ndarray, exit_long: np.ndarray,
                exit_short: np.ndarray, max_hold: int = 0) -> np.ndarray:
    """Turn entry/exit event arrays into a held position, optionally with a time stop."""
    n = len(enter_long)
    out = np.zeros(n)
    pos, held = 0, 0
    for i in range(n):
        if pos == 1 and (exit_long[i] or (max_hold and held >= max_hold)):
            pos = 0
        elif pos == -1 and (exit_short[i] or (max_hold and held >= max_hold)):
            pos = 0
        if pos == 0:
            if enter_long[i]:
                pos, held = 1, 0
            elif enter_short[i]:
                pos, held = -1, 0
        elif pos == 1 and enter_short[i]:
            pos, held = -1, 0
        elif pos == -1 and enter_long[i]:
            pos, held = 1, 0
        held += 1 if pos else 0
        out[i] = pos
    return out


# ---------------------------------------------------------------------- trend
def sma_cross(ctx: Context, p: dict) -> np.ndarray:
    c = pd.Series(ctx.close)
    fast, slow = c.rolling(p["fast"]).mean(), c.rolling(p["slow"]).mean()
    sig = np.where(fast > slow, 1.0, -1.0)
    sig[: p["slow"]] = 0
    if p["fast"] >= p["slow"]:
        return np.zeros(len(c))
    return sig


def donchian(ctx: Context, p: dict) -> np.ndarray:
    h, l, c = ctx.df["high"], ctx.df["low"], ctx.df["close"].to_numpy(float)
    up = h.rolling(p["entry"]).max().shift(1).to_numpy()
    dn = l.rolling(p["entry"]).min().shift(1).to_numpy()
    ex_up = h.rolling(p["exit"]).max().shift(1).to_numpy()
    ex_dn = l.rolling(p["exit"]).min().shift(1).to_numpy()
    return _hold_state(c > up, c < dn, c < ex_dn, c > ex_up)


def tsmom(ctx: Context, p: dict) -> np.ndarray:
    lr = np.log(pd.Series(ctx.close))
    lb = p["lookback"]
    vol = lr.diff().rolling(60).std()
    t = (lr - lr.shift(lb)) / (vol * np.sqrt(lb))
    sig = np.where(t > p["threshold"], 1.0, np.where(t < -p["threshold"], -1.0, 0.0))
    sig[: lb + 1] = 0
    return sig


# ---------------------------------------------------------------------- mean reversion
def rsi_reversion(ctx: Context, p: dict) -> np.ndarray:
    c = pd.Series(ctx.close)
    d = c.diff()
    a = 1 / p["period"]
    up = d.clip(lower=0).ewm(alpha=a, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=a, adjust=False).mean()
    r = (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy().copy()
    r[: p["period"]] = 50
    return _hold_state(r < p["lower"], r > p["upper"], r > p["exit_level"], r < p["exit_level"], p["max_hold"])


def bollinger(ctx: Context, p: dict) -> np.ndarray:
    c = pd.Series(ctx.close)
    m, s = c.rolling(p["window"]).mean(), c.rolling(p["window"]).std()
    cv, mv = c.to_numpy(), m.to_numpy()
    lo, hi = (m - p["k"] * s).to_numpy(), (m + p["k"] * s).to_numpy()
    return _hold_state(cv < lo, cv > hi, cv >= mv, cv <= mv, p["max_hold"])


# ---------------------------------------------------------------------- quantamental
def _cot_index(ctx: Context, weeks: int) -> np.ndarray:
    net = ctx.cot_net
    if net is None:
        return np.full(len(ctx.df), 50.0)
    # weekly report forward-filled daily -> a window of `weeks` reports ~= weeks*5 trading days
    win = weeks * 5
    lo, hi = net.rolling(win, min_periods=win // 2).min(), net.rolling(win, min_periods=win // 2).max()
    return ((net - lo) / (hi - lo).replace(0, np.nan) * 100).fillna(50).to_numpy()


def cot_contrarian(ctx: Context, p: dict) -> np.ndarray:
    idx = _cot_index(ctx, p["lookback_weeks"])
    return _hold_state(idx <= p["lower"], idx >= p["upper"], idx >= p["exit_level"], idx <= p["exit_level"],
                       p["max_hold"])


def trend_cot_filter(ctx: Context, p: dict) -> np.ndarray:
    """Trend following, but stand aside when speculators are already crowded the same way."""
    base = tsmom(ctx, {"lookback": p["lookback"], "threshold": 0.0})
    idx = _cot_index(ctx, p["lookback_weeks"])
    base[(base > 0) & (idx >= p["crowded"])] = 0
    base[(base < 0) & (idx <= 100 - p["crowded"])] = 0
    return base


def inventory_sd(ctx: Context, p: dict) -> np.ndarray:
    """Long when stocks are tight vs the 5y norm, short when loose; exit as balance normalises."""
    if ctx.inv_z is None:
        return np.zeros(len(ctx.df))
    z = ctx.inv_z.to_numpy(float)
    z = np.nan_to_num(z)
    return _hold_state(z <= -p["z_entry"], z >= p["z_entry"], z >= -p["z_exit"], z <= p["z_exit"], p["max_hold"])


STRATEGIES: dict[str, Strategy] = {s.key: s for s in [
    Strategy("sma_cross", "Moving-average crossover", "Trend",
             "Long when the fast SMA is above the slow SMA, short when below.",
             [_int("fast", "Fast SMA", 20, 5, 100, 5), _int("slow", "Slow SMA", 100, 20, 300, 10), MODE], sma_cross),
    Strategy("donchian", "Donchian channel breakout", "Trend",
             "Turtle-style: enter on an N-day high/low breakout, exit on an M-day opposite extreme.",
             [_int("entry", "Entry channel", 55, 10, 120, 5), _int("exit", "Exit channel", 20, 5, 60, 5), MODE],
             donchian),
    Strategy("tsmom", "Time-series momentum", "Trend",
             "Long/short on the sign of the vol-normalised N-day return, with a dead-band threshold.",
             [_int("lookback", "Lookback days", 120, 20, 260, 10),
              _float("threshold", "t-stat threshold", 0.25, 0.0, 2.0, 0.25), MODE], tsmom),
    Strategy("rsi_reversion", "RSI mean reversion", "Mean reversion",
             "Buy oversold / sell overbought RSI, exit when RSI crosses back through the exit level.",
             [_int("period", "RSI period", 14, 2, 30, 1), _int("lower", "Oversold", 30, 5, 45, 5),
              _int("upper", "Overbought", 70, 55, 95, 5), _int("exit_level", "Exit level", 50, 35, 65, 5),
              _int("max_hold", "Max hold days (0=off)", 15, 0, 60, 5), MODE], rsi_reversion),
    Strategy("bollinger", "Bollinger band reversion", "Mean reversion",
             "Fade closes outside the k-sigma bands, exit at the moving average.",
             [_int("window", "Window", 20, 10, 100, 5), _float("k", "Band width (σ)", 2.0, 1.0, 3.5, 0.25),
              _int("max_hold", "Max hold days (0=off)", 20, 0, 60, 5), MODE], bollinger),
    Strategy("cot_contrarian", "COT contrarian", "Quantamental",
             "Fade managed-money extremes: long when the COT index is crowded short, short when crowded long.",
             [_int("lookback_weeks", "COT lookback (weeks)", 156, 26, 260, 26),
              _int("lower", "Crowded short ≤", 10, 1, 40, 5), _int("upper", "Crowded long ≥", 90, 60, 99, 5),
              _int("exit_level", "Exit at index", 50, 30, 70, 5),
              _int("max_hold", "Max hold days (0=off)", 40, 0, 120, 10), MODE], cot_contrarian, "cot"),
    Strategy("trend_cot_filter", "Trend + COT crowding filter", "Quantamental",
             "Time-series momentum that stands aside when speculators are already crowded in the trend direction.",
             [_int("lookback", "Momentum lookback", 120, 20, 260, 10),
              _int("lookback_weeks", "COT lookback (weeks)", 156, 26, 260, 26),
              _int("crowded", "Crowded threshold", 85, 60, 99, 5), MODE], trend_cot_filter, "cot"),
    Strategy("inventory_sd", "Inventory supply/demand", "Quantamental",
             "Energy only: long when inventories are tight vs the 5-yr norm, short when loose.",
             [_float("z_entry", "Entry |z|", 1.0, 0.25, 3.0, 0.25), _float("z_exit", "Exit |z|", 0.25, 0.0, 1.5, 0.25),
              _int("max_hold", "Max hold days (0=off)", 0, 0, 120, 10), MODE], inventory_sd, "inventory"),
]}
