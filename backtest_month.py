"""sos-stock-radar :: backtest_month
One-off replay of the v1.5 engine over the last ~22 trading sessions using
Kite historical 5m candles (requires the Historical Data add-on, which this
account has).

Run on Railway as a temporary start command:  python backtest_month.py
Results: summary to Telegram + detailed trade list in deploy logs.

Fidelity notes (also printed in the report):
- Aggressor-delta gate skipped (tick data doesn't exist historically)
- Results-calendar catalyst override not replayed
- OI quadrant treated as neutral-pass when historical OI is missing
- Entries at bar close; no slippage modeled -> results are a CEILING
"""
import time
import logging
import datetime as dt
from collections import defaultdict

import pandas as pd
from kiteconnect import KiteConnect

import config, db, sectors, universe as unimod, indicators as ind
from telegram_alert import send

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backtest")

LOOKBACK_CAL_DAYS = 46      # pull window (need ~20 warmup days for RVOL baseline)
TEST_SESSIONS = 22          # sessions actually tested (the most recent ones)
SLOTS_PER_DAY = 75          # 5m bars 09:15-15:30

def kite_client():
    import token_sync
    token_sync.refresh()
    k = KiteConnect(api_key=config.KITE_API_KEY)
    k.set_access_token(config.KITE_ACCESS_TOKEN)
    return k

def pull_5m(kite, token, oi=False):
    to_d = dt.datetime.now()
    from_d = to_d - dt.timedelta(days=LOOKBACK_CAL_DAYS)
    for attempt in range(3):
        try:
            data = kite.historical_data(token, from_d, to_d, "5minute", oi=oi)
            df = pd.DataFrame(data)
            if df.empty:
                return None
            df["date"] = pd.to_datetime(df["date"])
            df["d"] = df["date"].dt.date
            df["hm"] = df["date"].dt.strftime("%H:%M")
            return df
        except Exception as e:
            if "Too many requests" in str(e):
                time.sleep(1.5); continue
            log.warning(f"pull failed token {token}: {e}")
            return None
    return None

def load_daily(sym):
    q = f"SELECT * FROM daily WHERE symbol='{sym}' ORDER BY d ASC"
    return pd.read_sql(q, db.engine)

def levels_asof(daily_df, day):
    """Compute the Module C level set using ONLY data before `day` (no lookahead)."""
    df = daily_df[daily_df["d"] < day].tail(300)
    if len(df) < 60:
        return None
    df = df.reset_index(drop=True)
    wk = df.tail(5); mo = df.tail(22)
    lv = {
        "close": float(df["c"].iloc[-1]),
        "pdh": float(df["h"].iloc[-1]), "pdl": float(df["l"].iloc[-1]),
        "pwh": float(wk["h"].max()), "pwl": float(wk["l"].min()),
        "pmh": float(mo["h"].max()), "pml": float(mo["l"].min()),
        "hi_52w": float(df["h"].tail(250).max()),
        "lo_52w": float(df["l"].tail(250).min()),
        "adr21": ind.adr_pct(df), "ret_12w": ind.ret_pct(df, 60),
        "ema20": float(ind.ema(df["c"], 20).iloc[-1]),
        "trendlines": ind.trendlines(df),
    }
    return lv

def simulate():
    kite = kite_client()
    uni = unimod.load()
    log.info(f"universe: {len(uni)} names — pulling 5m data (spot+fut), ~2-4 min")

    spot5, fut5, daily_all = {}, {}, {}
    for _, u in uni.iterrows():
        sym = u["symbol"]
        s = pull_5m(kite, int(u["spot_token"]))
        f = pull_5m(kite, int(u["fut_token"]), oi=True)
        if s is None:
            continue
        spot5[sym] = s; fut5[sym] = f
        daily_all[sym] = load_daily(sym)
        time.sleep(0.4)  # rate-limit courtesy

    # sector + nifty 5m
    nse = kite.instruments("NSE")
    idx_lookup = {i["tradingsymbol"]: i["instrument_token"]
                  for i in nse if i["segment"] == "INDICES"}
    idx5 = {}
    needed = {"NIFTY 50"} | {sectors.sector_index_symbol(s) for s in uni["sector"]}
    for name in needed:
        tok = idx_lookup.get(name)
        if tok:
            d = pull_5m(kite, tok)
            if d is not None:
                idx5[name] = d
            time.sleep(0.4)

    all_days = sorted({d for df in spot5.values() for d in df["d"].unique()})
    test_days = all_days[-TEST_SESSIONS:]
    log.info(f"testing {len(test_days)} sessions: {test_days[0]} → {test_days[-1]}")

    trades = []
    for day in test_days:
        trades += simulate_day(day, uni, spot5, fut5, idx5, daily_all, all_days)

    report(trades, test_days)

def cum_profile(df_day):
    df_day = df_day.sort_values("date")
    df_day["cumvol"] = df_day["volume"].cumsum()
    tp = (df_day["high"] + df_day["low"] + df_day["close"]) / 3
    df_day["cum_pv"] = (tp * df_day["volume"]).cumsum()
    df_day["vwap"] = df_day["cum_pv"] / df_day["cumvol"].replace(0, 1)
    return df_day.reset_index(drop=True)

def simulate_day(day, uni, spot5, fut5, idx5, daily_all, all_days):
    # ---- build per-day context
    prior_days = [d for d in all_days if d < day]
    ctx = {}
    for _, u in uni.iterrows():
        sym = u["symbol"]
        if sym not in spot5:
            continue
        sdf = spot5[sym]
        dday = sdf[sdf["d"] == day]
        if dday.empty:
            continue
        lv = levels_asof(daily_all[sym], day)
        if not lv:
            continue
        prev_close = lv["close"]
        # RVOL baseline: mean cumvol per slot over prior (up to 20) days
        base = {}
        pd_days = prior_days[-20:]
        hist = sdf[sdf["d"].isin(pd_days)]
        if not hist.empty:
            hist = hist.sort_values("date")
            hist["cv"] = hist.groupby("d")["volume"].cumsum()
            slot_means = hist.groupby("hm")["cv"].mean()
            base = slot_means.to_dict()
        ret12 = lv["ret_12w"]
        if ret12 >= config.MOMENTUM_12W_MIN:
            pool = "LONG"
        elif ret12 <= config.MOMENTUM_12W_SHORT:
            pool = "SHORT"
        else:
            pool = "AUTO"
        ctx[sym] = dict(bars=cum_profile(dday.copy()), lv=lv, prev=prev_close,
                        pool=pool, base=base, sector=u["sector"],
                        fut=fut5.get(sym))
    if not ctx:
        return []

    # simplified watchlist: top 8 momentum-pool names by structure score
    def wscore(c):
        s = 0; lv = c["lv"]
        for tl in lv["trendlines"]:
            if abs(lv["close"] - tl["value"]) / tl["value"] * 100 <= 1.0:
                s += 2
        if abs(lv["close"] - lv["hi_52w"]) / lv["hi_52w"] * 100 <= 1.0:
            s += 2
        return s
    pool_names = [s for s, c in ctx.items() if c["pool"] in ("LONG", "SHORT")]
    watch = set(sorted(pool_names, key=lambda s: wscore(ctx[s]), reverse=True)[:8])

    idx_day = {n: cum_profile(df[df["d"] == day].copy())
               for n, df in idx5.items() if not df[df["d"] == day].empty}
    idx_prev = {}
    for n, df in idx5.items():
        pr = df[df["d"] < day]
        if not pr.empty:
            idx_prev[n] = float(pr.iloc[-1]["close"])

    trades, alerts_today, sl_hits, fired = [], 0, 0, set()
    n_bars = max(len(c["bars"]) for c in ctx.values())

    for bi in range(3, n_bars):
        if alerts_today >= config.MAX_ALERTS_PER_DAY or sl_hits >= 2:
            break
        # breadth at this bar
        ups = tot = 0
        for c in ctx.values():
            if bi < len(c["bars"]):
                tot += 1
                if c["bars"]["close"].iloc[bi] > c["prev"]:
                    ups += 1
        breadth = ups / tot * 100 if tot else 50

        for sym, c in ctx.items():
            if sym in fired or alerts_today >= config.MAX_ALERTS_PER_DAY:
                continue
            b = c["bars"]
            if bi >= len(b):
                continue
            hm = b["hm"].iloc[bi]
            if hm < config.OBSERVE_UNTIL or hm >= config.NO_NEW_ALERTS_AFTER:
                continue
            deferred = sym not in watch
            if deferred and hm < config.FULL_SCAN_AFTER:
                continue
            lp = float(b["close"].iloc[bi]); vwap = float(b["vwap"].iloc[bi])
            prev = c["prev"]; chg = (lp / prev - 1) * 100
            pool = c["pool"] if sym in watch and c["pool"] != "AUTO" else None
            if pool is None:
                if lp > vwap and chg >= config.AUTO_DIR_MIN_CHG: pool = "LONG"
                elif lp < vwap and chg <= -config.AUTO_DIR_MIN_CHG: pool = "SHORT"
                else: continue
            direction = pool; sign = 1 if direction == "LONG" else -1
            lv = c["lv"]; day_open = float(b["open"].iloc[0])
            # G1 gap
            gap = (day_open / prev - 1) * 100
            if abs(gap) > config.GAP_SKIP_PCT: continue
            if abs(gap) > config.GAP_HOLD_PCT:
                mid = (day_open + prev) / 2
                if (sign == 1 and lp < mid) or (sign == -1 and lp > mid): continue
            # G2 RVOL
            baseline = c["base"].get(hm)
            if baseline and float(b["cumvol"].iloc[bi]) / baseline < config.RVOL_MIN:
                continue
            # G3 VWAP side
            if (sign == 1 and lp < vwap) or (sign == -1 and lp > vwap): continue
            # G4 OI quadrant (neutral-pass if missing)
            quad_ok = True
            f = c["fut"]
            if f is not None and "oi" in f.columns:
                fd = f[f["d"] == day]
                if len(fd) > bi and bi >= 3:
                    oi_now = fd["oi"].iloc[min(bi, len(fd)-1)]
                    oi_old = fd["oi"].iloc[max(min(bi, len(fd)-1) - 3, 0)]
                    if oi_old:
                        oichg = (oi_now / oi_old - 1) * 100
                        px_up = lp > float(b["close"].iloc[bi-3])
                        if sign == 1:
                            quad_ok = (oichg > 0 and px_up) or (oichg < 0 and px_up)
                        else:
                            quad_ok = oichg > 0 and not px_up
            if not quad_ok: continue
            # G5 trigger
            trig, sl = trigger(b, bi, lv, day_open, direction)
            if not trig: continue
            risk = abs(lp - sl)
            if risk <= 0: continue
            adr = lv["adr21"] or 2.0
            if risk / lp * 100 > adr * config.ADR_STOP_MAX_PCT / 100: continue
            # G6 RR
            t2 = lp * (1 + sign * config.T2_PCT / 100)
            rr = abs(t2 - lp) / risk
            if rr < config.RR_MIN: continue
            # G7 RS
            nchg = idx_chg(idx_day, idx_prev, "NIFTY 50", bi)
            schg = idx_chg(idx_day, idx_prev, sectors.sector_index_symbol(c["sector"]), bi)
            if schg is None: schg = nchg or 0
            if nchg is None: nchg = 0
            if sign * chg <= 0: continue
            if sign * (chg - nchg) <= 0: continue
            # G10 breadth
            if sign == 1 and breadth < config.BREADTH_MIN_LONG: continue
            if sign == -1 and breadth > config.BREADTH_MAX_SHORT: continue
            # ---- FIRE + simulate outcome
            outcome, r, exit_hm = walk_outcome(b, bi, lp, sl, sign)
            trades.append(dict(day=str(day), sym=sym, dir=direction, trig=trig,
                               src="WATCH" if sym in watch else "SCAN",
                               entry=round(lp, 1), sl=round(sl, 1),
                               hm=hm, outcome=outcome, r=round(r, 2)))
            fired.add(sym); alerts_today += 1
            if outcome == "SL": sl_hits += 1
    return trades

def idx_chg(idx_day, idx_prev, name, bi):
    d = idx_day.get(name); p = idx_prev.get(name)
    if d is None or p is None or bi >= len(d):
        return None
    return (float(d["close"].iloc[bi]) / p - 1) * 100

def trigger(b, bi, lv, day_open, direction):
    last_c = float(b["close"].iloc[bi]); last_h = float(b["high"].iloc[bi])
    last_l = float(b["low"].iloc[bi])
    day_lo = float(b["low"].iloc[:bi+1].min()); day_hi = float(b["high"].iloc[:bi+1].max())
    if direction == "LONG" and lv.get("pdl") and day_open < lv["pdl"] and last_c > lv["pdl"]:
        return "OOPS", day_lo
    if direction == "SHORT" and lv.get("pdh") and day_open > lv["pdh"] and last_c < lv["pdh"]:
        return "OOPS", day_hi
    for i in range(bi - 2, max(bi - 14, 1), -1):
        if direction == "LONG":
            if b["low"].iloc[i] < b["low"].iloc[i-1] and b["low"].iloc[i] < b["low"].iloc[i+1]:
                sw = float(b["low"].iloc[i])
                if last_l < sw and last_c > sw:
                    return "DT-SWEEP", last_l
                break
        else:
            if b["high"].iloc[i] > b["high"].iloc[i-1] and b["high"].iloc[i] > b["high"].iloc[i+1]:
                sw = float(b["high"].iloc[i])
                if last_h > sw and last_c < sw:
                    return "DT-SWEEP", last_h
                break
    if bi >= 3:
        orb_hi = float(b["high"].iloc[:3].max()); orb_lo = float(b["low"].iloc[:3].min())
        if direction == "LONG" and last_c > orb_hi and last_l <= orb_hi * 1.002:
            return "ORB-RETEST", orb_hi * 0.995
        if direction == "SHORT" and last_c < orb_lo and last_h >= orb_lo * 0.998:
            return "ORB-RETEST", orb_lo * 1.005
    return None, None

def walk_outcome(b, bi, entry, sl, sign):
    risk = abs(entry - sl) or 1e-9
    t1 = entry * (1 + sign * config.T1_PCT / 100)
    t2 = entry * (1 + sign * config.T2_PCT / 100)
    stop = sl; t1_done = False
    for j in range(bi + 1, len(b)):
        hm = b["hm"].iloc[j]
        hi = float(b["high"].iloc[j]); lo = float(b["low"].iloc[j])
        cl = float(b["close"].iloc[j])
        if sign == 1:
            if lo <= stop:
                return ("BE" if t1_done else "SL"), (0.0 if t1_done else -1.0), hm
            if hi >= t2:
                return "T2", (t2 - entry) / risk, hm
            if not t1_done and hi >= t1:
                t1_done = True; stop = entry
        else:
            if hi >= stop:
                return ("BE" if t1_done else "SL"), (0.0 if t1_done else -1.0), hm
            if lo <= t2:
                return "T2", (entry - t2) / risk, hm
            if not t1_done and lo <= t1:
                t1_done = True; stop = entry
        if hm >= config.HARD_EXIT_TIME:
            return "TIME", sign * (cl - entry) / risk, hm
    cl = float(b["close"].iloc[-1])
    return "TIME", sign * (cl - entry) / risk, b["hm"].iloc[-1]

def report(trades, test_days):
    for t in trades:
        log.info(f"{t['day']} {t['hm']} {t['sym']} {t['dir']} {t['trig']} {t['src']} "
                 f"entry {t['entry']} sl {t['sl']} -> {t['outcome']} {t['r']:+}R")
    n = len(trades)
    if n == 0:
        send("🧪 [STOCK] Backtest: 0 trades fired over "
             f"{len(test_days)} sessions. Gates as configured are very strict — "
             "review RVOL/RR thresholds against logs.")
        return
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    days_traded = len({t["day"] for t in trades})
    by_trig = defaultdict(list)
    for t in trades:
        by_trig[t["trig"]].append(t["r"])
    trig_lines = [f"  {k}: {len(v)} trades, avg {sum(v)/len(v):+.2f}R"
                  for k, v in by_trig.items()]
    msg = (f"🧪 [STOCK] Backtest — last {len(test_days)} sessions "
           f"({test_days[0]} → {test_days[-1]})\n"
           f"Trades fired: {n} (on {days_traded} days)\n"
           f"Win rate: {len(wins)/n*100:.0f}% | Avg R: {sum(rs)/n:+.2f}\n"
           f"Total: {sum(rs):+.1f}R | Best {max(rs):+.1f}R | Worst {min(rs):+.1f}R\n"
           f"By trigger:\n" + "\n".join(trig_lines) + "\n\n"
           "⚠ Ceiling estimate: no delta gate, no slippage, bar-close entries, "
           "OI neutral-pass where history missing. Full trade list in Railway logs.")
    send(msg)
    log.info("backtest complete")

if __name__ == "__main__":
    db.init()
    simulate()
