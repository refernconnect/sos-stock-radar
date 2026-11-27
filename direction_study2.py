"""sos-stock-radar :: direction_study2   (one-off research run, changes nothing)

QUESTION (loop 2)
  direction_study.py (27 Nov 2026) found no simple direction rule (first 15
  min, VWAP side, gap, vs Nifty, 15-minute ORB break, with or without stop)
  that beats costs on the day's top-10 opening-volume F&O stocks. This run
  tests the two PUBLISHED direction methods as written, on the same top-10:

  P1  5-minute ORB (Stocks in Play paper): first 5-minute candle up -> buy stop
      at its high; down -> sell stop at its low; doji -> no trade. Stop 10% of
      the 14-day ATR from entry. Exit 15:00 IST.
  P2  Noise-band momentum (Beat the Market paper, adapted to stocks): band =
      day's reference (max/min of open and previous close) +/- the average
      absolute move from the open to that same minute over the last 14 days.
      Long on a 5-minute close above the upper band and above VWAP; short on a
      close below the lower band and below VWAP; exit when price closes back
      inside the band or across VWAP, or 15:00. Checked on 30-minute marks
      (10:00, 10:30, ... 14:30) as in the paper. One trade a day.

WRITTEN BEFORE ANY RESULT WAS SEEN (do not edit after a run)
  Universe : full F&O list; each day the top 10 by 09:15-09:30 volume vs the
             prior 14 sessions (as in selection_study.py). For P1 the
             ranking uses the first 5-minute candle only (09:15-09:20).
  Cost     : 0.10% round trip + 0.05% slippage on entry and on stop exits.
  Unit     : return in ADR units.
  Periods  : reported for all days, before 3 Aug 2026 and from 3 Aug 2026
             (NSE closing-auction change for F&O stocks).
  PASS     : mean > 0, t >= 2.3 (5% split over 2 rules), both halves > 0,
             and positive in BOTH session periods.
  5-minute bars: a stop touched inside a bar fills at the stop (or the bar
  open if it gapped past); entry and stop in the same bar count as a stop.
  Paper research only.
"""
import sys
import time
import logging
import datetime as dt

import numpy as np
import pandas as pd

import direction_study as ds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("direction_study2")

EXIT_HM = "15:00"
COST, SLIP = 0.0010, 0.0005
PASS_T = 2.3
CHECKS = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30"]
SPLIT = dt.date(2026, 8, 3)
RULES = ["P1 5-min ORB", "P2 noise band"]


def stock_days(df5):
    df = ds.days_of(df5)
    days = [(d, g.reset_index(drop=True)) for d, g in df.groupby("d", sort=True)
            if len(g) >= 60 and g["hm"].iloc[0] == "09:15"]
    if len(days) < 25:
        return pd.DataFrame()
    rows = []
    for i, (d, g) in enumerate(days):
        o = g["open"].iloc[0]
        rows.append(dict(d=d, o=o, h=g["high"].max(), l=g["low"].min(), c=g["close"].iloc[-1],
                         v5=g["volume"].iloc[0], v15=g["volume"].iloc[:3].sum(),
                         mv=(g.set_index("hm")["close"] / o - 1).abs()))
    t = pd.DataFrame([{k: v for k, v in r.items() if k != "mv"} for r in rows])
    tr = pd.concat([t["h"] - t["l"], (t["h"] - t["c"].shift(1)).abs(), (t["l"] - t["c"].shift(1)).abs()], axis=1).max(axis=1)
    t["atr"] = tr.shift(1).rolling(14).mean()
    t["adr"] = ((t["h"] - t["l"]) / t["c"]).shift(1).rolling(20).mean()
    t["rvol"] = t["v15"] / t["v15"].shift(1).rolling(14).mean()
    t["rvol5"] = t["v5"] / t["v5"].shift(1).rolling(14).mean()
    t["pc"] = t["c"].shift(1)
    mv = pd.DataFrame([r["mv"] for r in rows])               # day x minute abs move
    sigma = mv.shift(1).rolling(14, min_periods=10).mean()   # prior 14 days only
    p1, p2 = [], []
    for i, (d, g) in enumerate(days):
        if i < 15 or np.isnan(t.loc[i, "atr"]) or np.isnan(t.loc[i, "adr"]):
            p1.append(np.nan); p2.append(np.nan); continue
        p1.append(trade_orb5(g, t.loc[i, "atr"]))
        p2.append(trade_band(g, t.loc[i, "o"], t.loc[i, "pc"], sigma.iloc[i]))
    t["P1 5-min ORB"] = np.array(p1, dtype=float) / t["adr"]
    t["P2 noise band"] = np.array(p2, dtype=float) / t["adr"]
    return t.dropna(subset=["adr", "rvol"]).reset_index(drop=True)


def _exit_px(g, k_from, side, stop):
    """Walk bars after index k_from; return exit price (stop or 15:00 close)."""
    for k in range(k_from, len(g)):
        bar = g.iloc[k]
        if bar["hm"] >= EXIT_HM:
            return bar["open"], False
        if side == 1 and bar["low"] <= stop:
            return min(stop, bar["open"]), True
        if side == -1 and bar["high"] >= stop:
            return max(stop, bar["open"]), True
    return g["close"].iloc[-1], False


def trade_orb5(g, atr):
    first = g.iloc[0]
    if first["close"] == first["open"]:
        return np.nan
    side = 1 if first["close"] > first["open"] else -1
    trig = first["high"] if side == 1 else first["low"]
    for k in range(1, len(g)):
        bar = g.iloc[k]
        if bar["hm"] >= EXIT_HM:
            return np.nan
        hit = bar["high"] >= trig if side == 1 else bar["low"] <= trig
        if hit:
            entry = max(trig, bar["open"]) if side == 1 else min(trig, bar["open"])
            entry *= 1 + side * SLIP
            stop = entry - side * 0.10 * atr
            same = bar["low"] <= stop if side == 1 else bar["high"] >= stop
            if same:
                px, st = stop, True
            else:
                px, st = _exit_px(g, k + 1, side, stop)
            px *= 1 - side * SLIP if st else 1
            return side * (px / entry - 1) - COST
    return np.nan


def trade_band(g, o, pc, sigma):
    up_ref, dn_ref = max(o, pc), min(o, pc)
    tp = (g["high"] + g["low"] + g["close"]) / 3
    cv = g["volume"].cumsum().replace(0, np.nan)
    vwap = (tp * g["volume"]).cumsum() / cv
    g = g.assign(vwap=vwap.fillna(g["close"]))
    for k in range(len(g)):
        bar = g.iloc[k]
        if bar["hm"] not in CHECKS or bar["hm"] not in sigma.index or np.isnan(sigma[bar["hm"]]):
            continue
        ub, lb = up_ref * (1 + sigma[bar["hm"]]), dn_ref * (1 - sigma[bar["hm"]])
        side = 1 if (bar["close"] > ub and bar["close"] > bar["vwap"]) else \
               -1 if (bar["close"] < lb and bar["close"] < bar["vwap"]) else 0
        if side == 0:
            continue
        if k + 1 >= len(g):
            return np.nan
        entry = g["open"].iloc[k + 1] * (1 + side * SLIP)
        for j in range(k + 1, len(g)):
            b = g.iloc[j]
            if b["hm"] >= EXIT_HM:
                return side * (b["open"] / entry - 1) - COST
            if b["hm"] in CHECKS and b["hm"] in sigma.index and not np.isnan(sigma[b["hm"]]):
                ub2, lb2 = up_ref * (1 + sigma[b["hm"]]), dn_ref * (1 - sigma[b["hm"]])
                out = (b["close"] < max(ub2, b["vwap"])) if side == 1 else (b["close"] > min(lb2, b["vwap"]))
                if out and j + 1 < len(g):
                    return side * (g["open"].iloc[j + 1] / entry - 1) - COST
        return side * (g["close"].iloc[-1] / entry - 1) - COST
    return np.nan


def stats(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return None
    se = x.std(ddof=1) / np.sqrt(n)
    h = n // 2
    return dict(n=n, mean=float(x.mean()), t=float(x.mean() / se) if se > 0 else np.nan,
                h1=float(x[:h].mean()), h2=float(x[h:].mean()))


def evaluate(tab):
    res = {}
    for r in RULES:
        key = "rvol5" if r.startswith("P1") else "rvol"
        tb = tab.dropna(subset=[key]).copy()
        tb["rank"] = tb.groupby("d")[key].rank(ascending=False, method="first")
        top = tb[tb["rank"] <= ds.TOP_N]
        daily = top.groupby("d")[r].mean().dropna()
        pre = daily[[d < SPLIT for d in daily.index]]
        post = daily[[d >= SPLIT for d in daily.index]]
        res[r] = dict(all=stats(daily.values), pre=stats(pre.values), post=stats(post.values),
                      trades=int(top[r].notna().sum()),
                      win=float((top[r].dropna() > 0).mean() * 100) if top[r].notna().any() else np.nan)
    return res, tab["d"].min(), tab["d"].max(), tab["d"].nunique(), tab["sym"].nunique()


def passed(r):
    a, pre, post = r["all"], r["pre"], r["post"]
    return (a is not None and a["mean"] > 0 and a["t"] >= PASS_T and a["h1"] > 0 and a["h2"] > 0
            and pre is not None and post is not None and pre["mean"] > 0 and post["mean"] > 0)


def report(res, first, last, days, names):
    m = (f"DIRECTION STUDY 2 (times IST)\n{first} to {last}\n"
         f"{days} sessions, {names} F&O stocks; top 10 by opening volume each day\n"
         f"Exit 15:00; cost 0.10% + 0.05% slippage; ADR per trade\n"
         f"PASS = mean > 0, t >= {PASS_T}, both halves > 0, positive before AND after 3 Aug\n\n")

    def line(s):
        return "n/a" if s is None else f"{s['mean']:+.3f} (t {s['t']:+.1f}, {s['n']} days)"

    for r in RULES:
        x = res[r]
        a = x["all"]
        m += (f"{r}: {x['trades']} trades, win {x['win']:.0f}%\n"
              f"   all {line(a)}\n"
              + (f"   halves {a['h1']:+.3f} / {a['h2']:+.3f}\n" if a else "")
              + f"   before 3 Aug {line(x['pre'])}\n"
              f"   from 3 Aug {line(x['post'])}\n"
              f"   -> {'PASS' if passed(x) else 'NOT DETECTED'}\n\n")
    m += "Paper research only."
    return m


def load_real():
    from kiteconnect import KiteConnect
    import config, token_sync
    token_sync.refresh()
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(config.KITE_ACCESS_TOKEN)
    nfo = pd.DataFrame(kite.instruments("NFO"))
    fut_names = set(nfo[nfo["instrument_type"] == "FUT"]["name"].unique())
    nse = pd.DataFrame(kite.instruments("NSE"))
    eq = nse[(nse["segment"] == "NSE") & (nse["instrument_type"] == "EQ")
             & (nse["tradingsymbol"].isin(fut_names))]
    frames, today = [], ds._ist_now().date()
    for i, (_, r) in enumerate(eq.iterrows(), 1):
        parts, end = [], ds._ist_now()
        for _ in range(2):
            start = end - dt.timedelta(days=95)
            for _try in range(3):
                try:
                    parts.append(pd.DataFrame(kite.historical_data(int(r["instrument_token"]), start, end, "5minute")))
                    break
                except Exception as e:
                    if "Too many" in str(e):
                        time.sleep(1.5)
                        continue
                    break
            end = start
            time.sleep(0.35)
        if not parts:
            continue
        df = pd.concat(parts)
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"])
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df = df[df["date"].dt.date < today]
        t = stock_days(df)
        if not t.empty:
            t["sym"] = r["tradingsymbol"]
            frames.append(t)
        if i % 25 == 0:
            log.info(f"pulled {i}/{len(eq)}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _fake(planted, seed, n_sym=40, n_days=70):
    rs = np.random.default_rng(seed)
    days = pd.bdate_range("2026-06-01", periods=n_days)
    hms = pd.date_range("09:15", "15:25", freq="5min").strftime("%H:%M")
    frames = []
    for s in range(n_sym):
        price, recs = 100 + 400 * rs.random(), []
        for d in days:
            hot = rs.random() < 0.2
            side = rs.choice((-1, 1))
            price *= 1 + rs.normal(0, 0.006)
            for k, hm in enumerate(hms):
                o = price
                drift = side * 0.0008 if (hot and planted) else 0.0
                c = o * (1 + drift + rs.normal(0, 0.0018))
                recs.append((pd.Timestamp(f"{d.date()} {hm}"), o, max(o, c) * 1.0006,
                             min(o, c) * 0.9994, c, (3 if hot else 1) * rs.integers(4000, 9000)))
                price = c
        t = stock_days(pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"]))
        t["sym"] = f"S{s}"
        frames.append(t)
    return pd.concat(frames, ignore_index=True)


def selftest():
    ok = True
    for planted in (True, False):
        res, *meta = evaluate(_fake(planted, 11 if planted else 12))
        print(f"\n===== {'PLANTED' if planted else 'NO'} EFFECT =====\n" + report(res, *meta))
        if planted:
            ok &= passed(res["P2 noise band"]) and res["P1 5-min ORB"]["all"]["mean"] > 0
        else:
            ok &= not any(passed(res[r]) for r in RULES)
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


def main():
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    from telegram_alert import send
    tab = load_real()
    if tab.empty:
        send("DIRECTION STUDY 2: no data pulled. Check the Kite login, then run again.")
        return
    msg = report(*evaluate(tab))
    log.info("\n" + msg)
    send(msg)
    log.info("direction study 2 complete")


if __name__ == "__main__":
    main()
