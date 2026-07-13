"""sos-stock-radar :: indicators
Pure functions on daily OHLCV DataFrames (columns: d,o,h,l,c,v ascending)."""
import numpy as np
import pandas as pd
import config

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def adr_pct(df: pd.DataFrame, n=21) -> float:
    rng = (df["h"] - df["l"]) / df["c"] * 100
    return float(rng.tail(n).mean())

def ret_pct(df: pd.DataFrame, sessions: int) -> float:
    if len(df) <= sessions:
        return 0.0
    return float((df["c"].iloc[-1] / df["c"].iloc[-1 - sessions] - 1) * 100)

def fractal_pivots(df: pd.DataFrame, k=2):
    h, l = df["h"].values, df["l"].values
    ph, pl = [], []
    for i in range(k, len(df) - k):
        if h[i] == max(h[i - k:i + k + 1]):
            ph.append(i)
        if l[i] == min(l[i - k:i + k + 1]):
            pl.append(i)
    return ph, pl

def nr7_inside(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    rng = df["h"] - df["l"]
    return {
        "nr7": bool(rng.iloc[-1] == rng.tail(7).min()),
        "inside": bool(last["h"] <= df["h"].iloc[-2] and last["l"] >= df["l"].iloc[-2]),
    }

def base_detect(df: pd.DataFrame) -> dict | None:
    for n in range(60, config.BASE_MIN_SESSIONS - 1, -5):
        if len(df) < n:
            continue
        w = df.tail(n)
        hi, lo = w["h"].max(), w["l"].min()
        if lo <= 0:
            continue
        if (hi / lo - 1) * 100 <= config.BASE_MAX_RANGE_PCT:
            return {"sessions": n, "hi": float(hi), "lo": float(lo)}
    return None

def vcp_detect(df: pd.DataFrame) -> bool:
    ph, pl = fractal_pivots(df.tail(120).reset_index(drop=True), k=2)
    if len(ph) < 3 or len(pl) < 3:
        return False
    w = df.tail(120).reset_index(drop=True)
    pulls = []
    for i in range(len(ph) - 1):
        hi_i = ph[i]
        lows_after = [j for j in pl if j > hi_i and (i + 1 >= len(ph) or j < ph[i + 1])]
        if lows_after:
            depth = (w["h"].iloc[hi_i] - w["l"].iloc[lows_after[0]]) / w["h"].iloc[hi_i] * 100
            pulls.append(depth)
    if len(pulls) < 3:
        return False
    last3 = pulls[-3:]
    return bool(last3[0] > last3[1] > last3[2] and last3[2] < 6.0)

def trendlines(df: pd.DataFrame) -> list[dict]:
    out = []
    w = df.tail(180).reset_index(drop=True)
    ph, pl = fractal_pivots(w, k=2)
    for kind, piv, col in (("res", ph, "h"), ("sup", pl, "l")):
        pts = [(i, w[col].iloc[i]) for i in piv]
        n = len(pts)
        best = None
        for a in range(n):
            for b in range(a + 1, n):
                x1, y1 = pts[a]; x2, y2 = pts[b]
                if x2 == x1:
                    continue
                m = (y2 - y1) / (x2 - x1)
                touches = 0
                last_touch = -1
                for (xi, yi) in pts:
                    line = y1 + m * (xi - x1)
                    if abs(yi - line) / line * 100 <= config.TRENDLINE_TOL_PCT:
                        touches += 1
                        last_touch = max(last_touch, xi)
                if touches >= config.TRENDLINE_MIN_TOUCHES and last_touch >= len(w) - 15:
                    val = y1 + m * (len(w) - 1 - x1)
                    cand = {"kind": kind, "value": float(val), "touches": touches}
                    if best is None or touches > best["touches"]:
                        best = cand
        if best:
            out.append(best)
    return out

def anchored_vwap(df: pd.DataFrame, anchor_idx: int) -> float | None:
    w = df.iloc[anchor_idx:]
    if w.empty or w["v"].sum() == 0:
        return None
    tp = (w["h"] + w["l"] + w["c"]) / 3
    return float((tp * w["v"]).sum() / w["v"].sum())
