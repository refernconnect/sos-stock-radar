"""sos-stock-radar :: direction_study   (one-off research run, changes nothing)

QUESTION
  The selection study (27 Nov 2026) showed that the top 10 F&O stocks by
  opening volume ratio at 09:30 burst far more often than the rest, but going
  with the first-15-minute direction did not make money after cost.
  Which DIRECTION rule, applied to those same 10 stocks, does?

RUN (Railway console, after the morning Kite login, not between 08:40-15:35)
  /app/.venv/bin/python direction_study.py
  python direction_study.py --selftest      (made-up data, no Kite)

WRITTEN BEFORE ANY RESULT WAS SEEN (do not edit after a run)
  Universe      : full F&O stock list; each day the top 10 by opening volume
                  ratio (09:15-09:30 volume vs its own prior 14 sessions).
  Entry         : 09:30 close (D1-D4) or the close of the first 5-minute
                  candle that closes outside the 09:15-09:30 range (D5, D6),
                  entries allowed until 13:00.
  Exit          : 14:45 close (D1-D5); D6 = D5 with a stop at the opposite
                  side of the opening range.
  Cost          : 0.10% round trip, plus 0.05% extra slippage on entry.
  Unit          : return in ADR units (prior 20-session average daily range).
  Rules
    D1 first 15 min : side of 09:30 close vs 09:15 open
    D2 VWAP side    : side of 09:30 close vs 09:15-09:30 VWAP
    D3 gap          : side of 09:15 open vs previous close
    D4 vs Nifty     : side of stock 09:15-09:30 return minus Nifty's
    D5 ORB break    : direction of the first close outside the opening range
    D6 ORB + stop   : D5 with the opposite range edge as stop
  Statistic     : per-day mean of the 10 trades, then mean / standard error
                  across days (t), and each half of the period.
  PASS RULE     : mean > 0, t >= 2.6 (5% split across 6 rules), both halves > 0.

LIMITS
  Spot candles only; no OI, no order flow, no options. 5-minute bars, so a
  stop and the exit in the same bar count as the stop. Paper research only.
"""
import sys
import time
import logging
import datetime as dt

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("direction_study")

ORB_END, EXIT_HM, LAST_ENTRY = "09:30", "14:45", "13:00"
RVOL_DAYS, ADR_DAYS, TOP_N = 14, 20, 10
COST, SLIP = 0.0010, 0.0005
PASS_T = 2.6
RULES = ["D1 first 15 min", "D2 VWAP side", "D3 gap", "D4 vs Nifty", "D5 ORB break", "D6 ORB + stop"]


def _ist_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).replace(tzinfo=None)


def days_of(df5):
    df = df5.copy()
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.sort_values("date").drop_duplicates("date")
    df["d"] = df["date"].dt.date
    df["hm"] = df["date"].dt.strftime("%H:%M")
    return df


def stock_days(df5, idx_r15=None):
    """Per day: selection inputs and the six rule trades (raw % returns)."""
    df = days_of(df5)
    rows = []
    for d, g in df.groupby("d", sort=True):
        pre = g[g["hm"] < ORB_END]
        post = g[(g["hm"] >= ORB_END) & (g["hm"] < EXIT_HM)]
        if len(pre) < 3 or len(post) < 30 or pre["hm"].iloc[0] != "09:15":
            continue
        o, p0 = pre["open"].iloc[0], pre["close"].iloc[-1]
        tp = (pre["high"] + pre["low"] + pre["close"]) / 3
        vol = pre["volume"].sum()
        vwap = (tp * pre["volume"]).sum() / vol if vol > 0 else p0
        hi, lo = pre["high"].max(), pre["low"].min()
        exit_px = post["close"].iloc[-1]
        # D5 / D6: first close outside the opening range before LAST_ENTRY
        brk = post[((post["close"] > hi) | (post["close"] < lo)) & (post["hm"] <= LAST_ENTRY)]
        d5 = d6 = np.nan
        if len(brk):
            b = brk.iloc[0]
            side = 1 if b["close"] > hi else -1
            entry = b["close"] * (1 + side * SLIP)
            d5 = side * (exit_px / entry - 1) - COST
            after = post[post["date"] > b["date"]]
            stop = lo if side == 1 else hi
            hit = after[(after["low"] <= stop) if side == 1 else (after["high"] >= stop)]
            out = stop if len(hit) else exit_px
            d6 = side * (out / entry - 1) - COST
        rows.append(dict(
            d=d, o=o, p0=p0, vwap=vwap, vol_open=vol, exit=exit_px, d5=d5, d6=d6,
            hi_d=g["high"].max(), lo_d=g["low"].min(), c=g["close"].iloc[-1],
        ))
    t = pd.DataFrame(rows)
    if len(t) < ADR_DAYS + 5:
        return pd.DataFrame()
    t["adr"] = ((t["hi_d"] - t["lo_d"]) / t["c"] * 100).shift(1).rolling(ADR_DAYS).mean() / 100
    t["rvol"] = t["vol_open"] / t["vol_open"].shift(1).rolling(RVOL_DAYS).mean()
    t["pc"] = t["c"].shift(1)
    hold = t["exit"] / (t["p0"] * (1 + SLIP)) - 1          # long from 09:30
    hold_s = -(t["exit"] / (t["p0"] * (1 - SLIP)) - 1)      # short from 09:30

    def side_trade(sign):
        return np.where(sign > 0, hold - COST, np.where(sign < 0, hold_s - COST, np.nan))

    r15 = t["p0"] / t["o"] - 1
    t["D1 first 15 min"] = side_trade(np.sign(r15))
    t["D2 VWAP side"] = side_trade(np.sign(t["p0"] - t["vwap"]))
    t["D3 gap"] = side_trade(np.sign(t["o"] - t["pc"]))
    if idx_r15 is not None:
        rel = r15 - t["d"].map(idx_r15).astype(float)
        t["D4 vs Nifty"] = side_trade(np.sign(rel))
    else:
        t["D4 vs Nifty"] = np.nan
    t["D5 ORB break"] = t["d5"]
    t["D6 ORB + stop"] = t["d6"]
    for r in RULES:
        t[r] = t[r] / t["adr"]
    return t.dropna(subset=["adr", "rvol"]).reset_index(drop=True)


def evaluate(tab):
    tab = tab.copy()
    tab["rank"] = tab.groupby("d")["rvol"].rank(ascending=False, method="first")
    top = tab[tab["rank"] <= TOP_N]
    res = {}
    for r in RULES:
        daily = top.groupby("d")[r].mean().dropna()
        n = len(daily)
        if n < 10:
            res[r] = None
            continue
        x = daily.values
        se = x.std(ddof=1) / np.sqrt(n)
        h = n // 2
        res[r] = dict(n=n, trades=int(top[r].notna().sum()), mean=float(x.mean()),
                      t=float(x.mean() / se) if se > 0 else np.nan,
                      h1=float(x[:h].mean()), h2=float(x[h:].mean()),
                      win=float((top[r].dropna() > 0).mean() * 100))
    return res, tab["d"].min(), tab["d"].max(), tab["d"].nunique(), tab["sym"].nunique()


def passed(s):
    return s is not None and s["mean"] > 0 and s["t"] >= PASS_T and s["h1"] > 0 and s["h2"] > 0


def report(res, first, last, days, names):
    m = (f"DIRECTION STUDY (times IST)\n{first} to {last}\n"
         f"{days} sessions, {names} F&O stocks; top {TOP_N} by opening volume each day\n"
         f"Exit 14:45; cost 0.10% + 0.05% slippage; result in ADR per trade\n"
         f"PASS = mean > 0, t >= {PASS_T}, both halves > 0\n\n")
    for r in RULES:
        s = res.get(r)
        if s is None:
            m += f"{r}: not enough data\n\n"
            continue
        m += (f"{r}: {s['mean']:+.3f} ADR, t {s['t']:+.1f}, win {s['win']:.0f}%, {s['trades']} trades\n"
              f"   halves {s['h1']:+.3f} / {s['h2']:+.3f} -> {'PASS' if passed(s) else 'NOT DETECTED'}\n\n")
    m += "Paper research only."
    return m


# ---------------------------------------------------------------- Kite
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
    idx = nse[(nse["segment"] == "INDICES") & (nse["tradingsymbol"] == "NIFTY 50")]

    def pull(tok):
        parts, end = [], _ist_now()
        for _ in range(2):
            start = end - dt.timedelta(days=95)
            for _try in range(3):
                try:
                    parts.append(pd.DataFrame(kite.historical_data(tok, start, end, "5minute")))
                    break
                except Exception as e:
                    if "Too many" in str(e):
                        time.sleep(1.5)
                        continue
                    log.warning(f"pull failed {tok}: {e}")
                    break
            end = start
            time.sleep(0.35)
        return pd.concat(parts) if parts else pd.DataFrame()

    idx_r15 = None
    if len(idx):
        nd = days_of(pull(int(idx.iloc[0]["instrument_token"])))
        nd = nd[nd["date"].dt.date < _ist_now().date()]
        g = nd.groupby("d")
        idx_r15 = (g.apply(lambda x: x[x["hm"] < ORB_END]["close"].iloc[-1] / x["open"].iloc[0] - 1
                           if (x["hm"] < ORB_END).sum() >= 3 else np.nan)).to_dict()
    frames = []
    for i, (_, r) in enumerate(eq.iterrows(), 1):
        df = pull(int(r["instrument_token"]))
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"])
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df = df[df["date"].dt.date < _ist_now().date()]
        t = stock_days(df, idx_r15)
        if not t.empty:
            t["sym"] = r["tradingsymbol"]
            frames.append(t)
        if i % 25 == 0:
            log.info(f"pulled {i}/{len(eq)}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------- self-test
def _fake(n_sym=60, n_days=80, planted=True, seed=3):
    rs = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    hms = pd.date_range("09:15", "15:25", freq="5min").strftime("%H:%M")
    frames, idx = [], {}
    for s in range(n_sym):
        price, recs = 100 + 400 * rs.random(), []
        for d in days:
            hot = rs.random() < 0.15
            price *= 1 + rs.normal(0, 0.006)
            side, drift = rs.choice((-1, 1)), 0.0
            for k, hm in enumerate(hms):
                o = price
                if k < 3 and hot and planted:
                    drift = side * 0.0012                   # opening push ...
                elif k >= 3 and hot and planted:
                    drift = side * 0.0006                   # ... that keeps going
                c = o * (1 + drift + rs.normal(0, 0.0018))
                recs.append((pd.Timestamp(f"{d.date()} {hm}"), o, max(o, c) * 1.0006,
                             min(o, c) * 0.9994, c, (3 if hot else 1) * rs.integers(4000, 9000)))
                price = c
        df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
        t = stock_days(df, None)
        t["sym"] = f"S{s}"
        frames.append(t)
    return pd.concat(frames, ignore_index=True)


def selftest():
    ok = True
    for planted in (True, False):
        res, *meta = evaluate(_fake(planted=planted, seed=3 if planted else 4))
        print(f"\n===== {'PLANTED' if planted else 'NO'} EFFECT =====")
        print(report(res, *meta))
        if planted:
            ok &= passed(res["D1 first 15 min"]) and passed(res["D5 ORB break"])
        else:
            ok &= not any(passed(res[r]) for r in RULES if res[r] is not None)
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


def main():
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    from telegram_alert import send
    tab = load_real()
    if tab.empty:
        send("DIRECTION STUDY: no data pulled. Check the Kite login, then run again.")
        return
    msg = report(*evaluate(tab))
    log.info("\n" + msg)
    send(msg)
    log.info("direction study complete")


if __name__ == "__main__":
    main()
