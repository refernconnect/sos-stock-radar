"""sos-stock-radar :: fvg_cpr_scanner (v1.5 module)
09:35 + 14:30 IST. Scans sector-leader F&O names for bounces off
weekly/monthly bullish FVGs and weekly virgin CPRs.
Zone tap + daily close reclaim within last 3 sessions, LTP <= 1.5%
above zone top. Alerts via telegram_alert.send. Self-contained:
builds own token map from Kite instruments dump, touches no other
module's state.
"""
import logging
import datetime as dt
import pandas as pd
from kiteconnect import KiteConnect
import config
from telegram_alert import send

log = logging.getLogger("fvg_cpr")

# ---------------- CONFIG ----------------
UNIVERSE = {
    "REALTY": ["DLF", "LODHA", "GODREJPROP", "PRESTIGE", "OBEROIRLTY", "PHOENIXLTD"],
    "PHARMA": ["SUNPHARMA", "CIPLA", "LUPIN", "AUROPHARMA", "DRREDDY", "DIVISLAB"],
    "AUTO":   ["M&M", "TVSMOTOR", "EICHERMOT", "BAJAJ-AUTO"],
    "BANKS":  ["ICICIBANK", "HDFCBANK", "AXISBANK"],
}
PROX_MAX_PCT    = 1.5
BOUNCE_LOOKBACK = 3
DAILY_LOOKBACK  = 1000
MAX_LEVELS      = 5

_alerted = set()   # (symbol, kind, zone_top, date) dedupe, resets on redeploy

# ---------------- SETUP ----------------
def _kite():
    k = KiteConnect(api_key=config.KITE_API_KEY)
    k.set_access_token(config.KITE_ACCESS_TOKEN)
    return k

def _token_map(kite):
    want = {s for syms in UNIVERSE.values() for s in syms}
    out = {}
    for row in kite.instruments("NSE"):
        ts = row["tradingsymbol"]
        if ts in want and row["instrument_type"] == "EQ":
            out[ts] = row["instrument_token"]
    missing = want - set(out)
    if missing:
        log.warning("token resolution missing: %s", missing)
    return out

# ---------------- DATA ----------------
def _daily_df(kite, token):
    to = dt.date.today()
    frm = to - dt.timedelta(days=DAILY_LOOKBACK)
    candles = kite.historical_data(token, frm, to, "day")
    df = pd.DataFrame(candles)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.set_index("date")[["open", "high", "low", "close"]]

def _resample(df, rule):
    return df.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()

# ---------------- LEVELS ----------------
def _bullish_fvgs(tf_df, daily_df):
    out = []
    h, l = tf_df["high"].values, tf_df["low"].values
    idx = tf_df.index
    for i in range(2, len(tf_df)):
        if h[i - 2] < l[i]:
            bot, top = h[i - 2], l[i]
            after = daily_df[daily_df.index > idx[i]]
            if not after.empty and after["low"].min() < bot:
                continue
            out.append({"bottom": bot, "top": top, "formed": idx[i].date()})
    return out[-MAX_LEVELS:]

def _virgin_weekly_cprs(weekly_df, daily_df):
    out = []
    for i in range(len(weekly_df) - 1):
        H = weekly_df["high"].iloc[i]
        L = weekly_df["low"].iloc[i]
        C = weekly_df["close"].iloc[i]
        p  = (H + L + C) / 3.0
        bc = (H + L) / 2.0
        tc = 2 * p - bc
        lo, hi = min(bc, tc), max(bc, tc)
        wk_end = weekly_df.index[i + 1]
        wk = daily_df[(daily_df.index > weekly_df.index[i]) & (daily_df.index <= wk_end)]
        if wk.empty:
            continue
        if ((wk["low"] <= hi) & (wk["high"] >= lo)).any():
            continue  # touched in its own week -> not virgin
        out.append({"bottom": lo, "top": hi, "formed": wk_end.date()})
    return out[-MAX_LEVELS:]

# ---------------- BOUNCE ----------------
def _bounce(zone, daily_df, ltp):
    top = zone["top"]
    if ltp <= top:
        return None
    dist = (ltp - top) / top * 100
    if dist > PROX_MAX_PCT:
        return None
    recent = daily_df.tail(BOUNCE_LOOKBACK)
    if not ((recent["low"] <= top) & (recent["close"] > top)).any():
        return None
    return round(dist, 2)

# ---------------- ENTRY ----------------
def run():
    log.info("fvg_cpr scan start")
    try:
        kite = _kite()
        tokens = _token_map(kite)
    except Exception as e:
        log.error("setup failed: %s", e)
        return
    today = dt.date.today().isoformat()
    hits = 0
    for sector, symbols in UNIVERSE.items():
        for sym in symbols:
            token = tokens.get(sym)
            if not token:
                continue
            try:
                d = _daily_df(kite, token)
                if d is None or len(d) < 60:
                    continue
                ltp = float(d["close"].iloc[-1])
                w = _resample(d, "W-FRI")
                m = _resample(d, "MS")
                levels = (
                    [("W-FVG", z) for z in _bullish_fvgs(w, d)]
                    + [("M-FVG", z) for z in _bullish_fvgs(m, d)]
                    + [("W-vCPR", z) for z in _virgin_weekly_cprs(w, d)]
                )
                for kind, z in levels:
                    dist = _bounce(z, d, ltp)
                    if dist is None:
                        continue
                    key = (sym, kind, round(z["top"], 1), today)
                    if key in _alerted:
                        continue
                    _alerted.add(key)
                    hits += 1
                    send(
                        f"🎯 {kind} BOUNCE | {sym} ({sector})\n"
                        f"Zone: {z['bottom']:.1f}-{z['top']:.1f} (formed {z['formed']})\n"
                        f"LTP: {ltp:.1f} | {dist}% above zone\n"
                        f"Tap+reclaim in last {BOUNCE_LOOKBACK} sessions"
                    )
            except Exception as e:
                log.warning("%s failed: %s", sym, e)
    log.info("fvg_cpr scan done, %d alerts", hits)
