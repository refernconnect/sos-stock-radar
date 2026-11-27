"""Direction loop 3 - written 27 Nov 2026 07:58 IST, BEFORE direction_study2's
result was seen. Do not edit after a run.

Selection: top 10 a day by 09:15-09:30 volume vs own 14-day average (proven).
Three rules, PASS_T 2.4 (5% split over 3, two-sided ~2.39).

R1 Holy Grail 5m (Raschke): 10:00-13:30. ADX(14) > 30 and above its value 3
   bars ago; +DI > -DI -> long, else short. First bar whose low (long) / high
   (short) touches EMA20 after ADX qualified; enter on a break of that bar's
   high/low within the next 3 bars. Stop: the touch bar's other extreme.
   Exit 15:00.
R2 Late momentum: at the 14:00 bar close, side = sign of move from the
   open if |move| >= 0.5 ADR. Enter 14:05 open, exit 15:00 open. No stop.
R3 First-hour trend with market: at the 10:15 bar close, price beyond the
   09:15-10:15 extreme in the direction of the open-to-now move, on the
   same side of VWAP, and Nifty's open-to-now move has the same sign.
   Enter next open; stop VWAP at entry (fixed); exit 15:00.
"""
import numpy as np
import pandas as pd
from research_lab import walk, SLIP

NAME = "DIRECTION LOOP 3"
PASS_T = 2.4


def _side(ctx, natural):
    return ctx["side_override"] or natural


def _adx(g, n=14):
    h, l, c = g["high"], g["low"], g["close"]
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=g.index).ewm(alpha=a, adjust=False).mean() / atr
    mdi = 100 * pd.Series(mdm, index=g.index).ewm(alpha=a, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean(), pdi, mdi


def r1(g, ctx):
    adx, pdi, mdi = _adx(g)
    ema = g["close"].ewm(span=20, adjust=False).mean()
    for k in range(9, len(g) - 4):
        hm = g["hm"].iloc[k]
        if hm < "10:00":
            continue
        if hm > "13:30":
            return None
        if not (adx.iloc[k] > 30 and adx.iloc[k] > adx.iloc[k - 3]):
            continue
        nat = 1 if pdi.iloc[k] > mdi.iloc[k] else -1
        touch = g["low"].iloc[k] <= ema.iloc[k] if nat == 1 else g["high"].iloc[k] >= ema.iloc[k]
        if not touch:
            continue
        side = _side(ctx, nat)
        trig = g["high"].iloc[k] if side == 1 else g["low"].iloc[k]
        stop = g["low"].iloc[k] if side == 1 else g["high"].iloc[k]
        for j in range(k + 1, min(k + 4, len(g))):
            b = g.iloc[j]
            if b["hm"] >= "15:00":
                return None
            hit = b["high"] >= trig if side == 1 else b["low"] <= trig
            if hit:
                entry = (max(trig, b["open"]) if side == 1 else min(trig, b["open"])) * (1 + side * SLIP)
                same = b["low"] <= stop if side == 1 else b["high"] >= stop
                if same:
                    return side, side * (stop * (1 - side * SLIP) / entry - 1)
                return side, walk(g, j + 1, side, entry, stop)
        return None
    return None


def r2(g, ctx):
    k = g.index[g["hm"] == "14:00"]
    if len(k) == 0:
        return None
    k = int(k[0])
    mv = g["close"].iloc[k] / ctx["o"] - 1
    if abs(mv) < 0.5 * ctx["adr"] or k + 1 >= len(g):
        return None
    side = _side(ctx, 1 if mv > 0 else -1)
    entry = g["open"].iloc[k + 1] * (1 + side * SLIP)
    return side, walk(g, k + 1, side, entry)


def r3(g, ctx):
    nf = ctx["nifty"]
    k = g.index[g["hm"] == "10:15"]
    if nf is None or len(k) == 0:
        return None
    k = int(k[0])
    nk = nf.index[nf["hm"] == "10:15"]
    if len(nk) == 0 or k + 1 >= len(g):
        return None
    c = g["close"].iloc[k]
    mv = c / ctx["o"] - 1
    nmv = nf["close"].iloc[int(nk[0])] / nf["open"].iloc[0] - 1
    first = g.iloc[:k]
    if mv > 0 and c > first["high"].max() and c > g["vwap"].iloc[k] and nmv > 0:
        nat = 1
    elif mv < 0 and c < first["low"].min() and c < g["vwap"].iloc[k] and nmv < 0:
        nat = -1
    else:
        return None
    side = _side(ctx, nat)
    entry = g["open"].iloc[k + 1] * (1 + side * SLIP)
    vw = g["vwap"].iloc[k]
    stop = vw if (side == 1 and vw < entry) or (side == -1 and vw > entry) else None
    return side, walk(g, k + 1, side, entry, stop)


RULES = {"R1 Holy Grail 5m": r1, "R2 late momentum": r2, "R3 trend + Nifty": r3}
