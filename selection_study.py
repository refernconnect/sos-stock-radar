"""sos-stock-radar :: selection_study   (one-off research run, changes nothing)

QUESTION
  At 09:30 IST, which facts about a stock tell us it will move more than
  usual for the rest of the day? Tested on the FULL F&O stock list.

HOW TO RUN (Railway, after the morning Kite login)
  Temporary Start Command:  python selection_study.py
  Wait for the Telegram report (about 5-8 minutes), then restore:
  python main.py
  Local check on made-up data (no Kite needed):
  python selection_study.py --selftest

WRITTEN BEFORE ANY RESULT WAS SEEN (do not edit after a run)
  Decision time : 09:30 IST (first three 5-minute candles complete)
  Measured over : 09:30 to 14:45 IST
  ADR           : average daily range % of the prior 20 sessions
  MOVE          : larger of the up-move and down-move from the 09:30
                  price, divided by ADR
  BURST         : MOVE of at least 0.75 ADR
  NAIVE TRADE   : go with the first-15-minute direction at 09:30, exit
                  14:45, minus 0.10% round-trip cost, in ADR units
  Selection     : each day, the top 10 names by one fact, against all
                  the other names that day (flags: flagged vs not)
  Statistic     : per-day difference, then mean / standard error
                  across days (t). Reported for each half of the period.
  PRIMARY TESTS (only these three count as a result):
    P1  top 10 by opening volume ratio -> higher BURST rate
    P2  top 10 by opening volume ratio -> NAIVE TRADE above zero
    P3  radar universe (top 40 by turnover) -> BURST rate vs the rest
  PASS RULE     : |t| >= 2.0 AND the same sign in both halves.
  Everything else in the report is SECONDARY: a lead, not a result.

LIMITS
  - Spot candles only. No futures OI, delivery %, results calendar or
    order flow in this run.
  - "Radar universe" here = top 40 by spot turnover (the live radar
    ranks by futures turnover).
  - Costs are a flat 0.10%; slippage at the open is not modelled.
  - Stocks that left the F&O list during the period are missing.
"""
import sys
import time
import logging
import datetime as dt

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("selection_study")

# ---------------- fixed design (see header) ----------------
DECISION_HM = "09:30"
EXIT_HM = "14:45"
OPEN_BARS = 3
RVOL_DAYS = 14
ADR_DAYS = 20
TOP_N = 10
RADAR_N = 40
BURST_ADR = 0.75
COST_PCT = 0.10
CHUNKS = 2                 # 2 x 95 calendar days of 5-minute candles
CHUNK_DAYS = 95
MIN_NAMES_PER_DAY = 30
PASS_T = 2.0


# ---------------- per-stock day table ----------------
def day_table(df5: pd.DataFrame) -> pd.DataFrame:
    """One row per session with facts known at 09:30 and the outcome after it.
    Every 'known at 09:30' column uses only earlier sessions (shifted)."""
    df = df5.sort_values("date").copy()
    df["d"] = df["date"].dt.date
    df["hm"] = df["date"].dt.strftime("%H:%M")
    rows = []
    for d, g in df.groupby("d", sort=True):
        pre = g[g["hm"] < DECISION_HM]
        post = g[(g["hm"] >= DECISION_HM) & (g["hm"] < EXIT_HM)]
        if len(pre) < OPEN_BARS or len(post) < 30:
            continue
        rows.append(dict(
            d=d,
            open=float(pre["open"].iloc[0]),
            p0=float(pre["close"].iloc[-1]),
            vol_open=float(pre["volume"].sum()),
            orb_hi=float(pre["high"].max()),
            orb_lo=float(pre["low"].min()),
            post_hi=float(post["high"].max()),
            post_lo=float(post["low"].min()),
            exit=float(post["close"].iloc[-1]),
            hi=float(g["high"].max()),
            lo=float(g["low"].min()),
            close=float(g["close"].iloc[-1]),
            vol=float(g["volume"].sum()),
        ))
    t = pd.DataFrame(rows)
    if len(t) < ADR_DAYS + 5:
        return pd.DataFrame()

    rng = (t["hi"] - t["lo"]) / t["close"] * 100.0
    prev_rng = rng.shift(1)
    t["adr"] = prev_rng.rolling(ADR_DAYS).mean()
    t["prev_close"] = t["close"].shift(1)
    t["gap_abs"] = ((t["open"] / t["prev_close"] - 1.0) * 100.0).abs()
    t["nr7"] = prev_rng <= prev_rng.rolling(7).min()
    t["prev_vol2x"] = t["vol"].shift(1) > 2.0 * t["vol"].shift(2).rolling(20).mean()
    t["rvol_open"] = t["vol_open"] / t["vol_open"].shift(1).rolling(RVOL_DAYS).mean()
    t["turn5"] = (t["close"] * t["vol"]).shift(1).rolling(5).mean()
    t["ret12w_abs"] = ((t["close"].shift(1) / t["close"].shift(61) - 1.0) * 100.0).abs()
    t["orb_adr"] = (t["orb_hi"] - t["orb_lo"]) / t["p0"] * 100.0 / t["adr"]
    t["first15_adr"] = (t["p0"] / t["open"] - 1.0).abs() * 100.0 / t["adr"]

    up = (t["post_hi"] - t["p0"]) / t["p0"] * 100.0
    dn = (t["p0"] - t["post_lo"]) / t["p0"] * 100.0
    t["move_adr"] = np.maximum(up, dn) / t["adr"]
    t["burst"] = (t["move_adr"] >= BURST_ADR).astype(float)
    direction = np.sign(t["p0"] - t["open"])
    raw = direction * (t["exit"] / t["p0"] - 1.0) * 100.0 - COST_PCT
    t["naive_adr"] = np.where(direction != 0, raw / t["adr"], np.nan)

    return t.dropna(subset=["adr", "rvol_open", "turn5"]).reset_index(drop=True)


# ---------------- cross-section tests ----------------
def add_selectors(tab: pd.DataFrame) -> pd.DataFrame:
    def top(col, n=TOP_N, asc=False):
        return tab.groupby("d")[col].rank(ascending=asc, method="first") <= n
    tab = tab.copy()
    tab["S_rvol"] = top("rvol_open")
    tab["S_radar40"] = top("turn5", RADAR_N)
    tab["S_gap"] = top("gap_abs")
    tab["S_first15"] = top("first15_adr")
    tab["S_orb_wide"] = top("orb_adr")
    tab["S_orb_tight"] = top("orb_adr", asc=True)
    tab["S_ret12w"] = top("ret12w_abs")
    tab["S_nr7"] = tab["nr7"].astype(bool)
    tab["S_prevvol2x"] = tab["prev_vol2x"].astype(bool)
    return tab


def _stat(x: np.ndarray):
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return dict(n=n, mean=np.nan, se=np.nan, t=np.nan, h1=np.nan, h2=np.nan)
    se = x.std(ddof=1) / np.sqrt(n)
    half = n // 2
    return dict(n=n, mean=float(x.mean()), se=float(se),
                t=float(x.mean() / se) if se > 0 else np.nan,
                h1=float(x[:half].mean()), h2=float(x[half:].mean()))


def diff_test(tab, sel, out):
    """Per-day (selected mean - rest mean) of `out`, then stats across days."""
    diffs, sel_means, rest_means = [], [], []
    for _, g in tab.groupby("d", sort=True):
        s = g.loc[g[sel], out].dropna()
        r = g.loc[~g[sel], out].dropna()
        if len(s) < 3 or len(r) < 10:
            continue
        diffs.append(s.mean() - r.mean())
        sel_means.append(s.mean())
        rest_means.append(r.mean())
    st = _stat(np.array(diffs, dtype=float))
    st["sel"] = float(np.mean(sel_means)) if sel_means else np.nan
    st["rest"] = float(np.mean(rest_means)) if rest_means else np.nan
    return st


def level_test(tab, sel, out):
    """Per-day mean of `out` for the selected names only, tested against zero."""
    vals = []
    for _, g in tab.groupby("d", sort=True):
        s = g.loc[g[sel], out].dropna()
        if len(s) >= 3:
            vals.append(s.mean())
    return _stat(np.array(vals, dtype=float))


def passed(st):
    return (not np.isnan(st["t"]) and abs(st["t"]) >= PASS_T
            and np.sign(st["h1"]) == np.sign(st["h2"]) == np.sign(st["mean"]))


def analyse(tab: pd.DataFrame) -> dict:
    counts = tab.groupby("d")["sym"].count()
    good_days = counts[counts >= MIN_NAMES_PER_DAY].index
    tab = add_selectors(tab[tab["d"].isin(good_days)])
    out = dict(days=len(good_days), names=int(tab["sym"].nunique()),
               rows=len(tab), first=min(good_days), last=max(good_days),
               base_burst=float(tab["burst"].mean() * 100.0),
               base_move=float(tab["move_adr"].mean()))
    out["P1"] = diff_test(tab, "S_rvol", "burst")
    out["P2"] = level_test(tab, "S_rvol", "naive_adr")
    out["P3"] = diff_test(tab, "S_radar40", "burst")
    out["P1_move"] = diff_test(tab, "S_rvol", "move_adr")
    sec = {}
    for name, sel in [("Gap size (top 10)", "S_gap"),
                      ("First 15 min move (top 10)", "S_first15"),
                      ("Opening range wide (top 10)", "S_orb_wide"),
                      ("Opening range tight (bottom 10)", "S_orb_tight"),
                      ("12-week return extreme (top 10)", "S_ret12w"),
                      ("Narrow previous day (flag)", "S_nr7"),
                      ("Previous day volume 2x (flag)", "S_prevvol2x")]:
        sec[name] = diff_test(tab, sel, "burst")
    out["secondary"] = sec
    return out


# ---------------- report ----------------
def _line_diff(st, unit="%", scale=100.0):
    if np.isnan(st["t"]):
        return "not enough data"
    return (f"picked {st['sel']*scale:.1f}{unit} vs rest {st['rest']*scale:.1f}{unit}\n"
            f"   difference {st['mean']*scale:+.1f}, t {st['t']:+.1f}, days {st['n']}\n"
            f"   half 1 {st['h1']*scale:+.1f} | half 2 {st['h2']*scale:+.1f}")


def build_report(res: dict) -> list:
    p1, p2, p3 = res["P1"], res["P2"], res["P3"]
    m1 = (f"SELECTION STUDY (times IST)\n"
          f"{res['first']} to {res['last']}\n"
          f"{res['days']} sessions, {res['names']} F&O stocks, {res['rows']} stock-days\n"
          f"Decision 09:30, measured to 14:45\n"
          f"All stocks: burst rate {res['base_burst']:.1f}%, "
          f"average move {res['base_move']:.2f} ADR\n\n"
          f"PRIMARY TESTS (pass = |t| >= 2 and both halves same sign)\n\n"
          f"P1 Top 10 by opening volume ratio -> burst rate\n"
          f"   {_line_diff(p1)}\n"
          f"   {'PASS' if passed(p1) else 'NOT DETECTED'}\n\n"
          f"P2 Same 10, trade with first-15-min direction, after cost\n")
    if np.isnan(p2["t"]):
        m1 += "   not enough data\n\n"
    else:
        m1 += (f"   average {p2['mean']:+.3f} ADR per trade, t {p2['t']:+.1f}, days {p2['n']}\n"
               f"   half 1 {p2['h1']:+.3f} | half 2 {p2['h2']:+.3f}\n"
               f"   {'PASS' if passed(p2) and p2['mean'] > 0 else 'NOT DETECTED'}\n\n")
    m1 += (f"P3 Radar universe (top 40 turnover) -> burst rate\n"
           f"   {_line_diff(p3)}\n"
           f"   {'DIFFERENT FROM REST' if passed(p3) else 'NO DIFFERENCE DETECTED'}\n")

    m2 = "SECONDARY (leads only, not results)\nBurst rate, picked vs rest:\n\n"
    for name, st in res["secondary"].items():
        if np.isnan(st["t"]):
            m2 += f"{name}\n   not enough data\n"
        else:
            m2 += (f"{name}\n   {st['sel']*100:.1f}% vs {st['rest']*100:.1f}% "
                   f"(t {st['t']:+.1f}; halves {st['h1']*100:+.1f} / {st['h2']*100:+.1f})\n")
    pm = res["P1_move"]
    if not np.isnan(pm["t"]):
        m2 += (f"\nTop 10 opening volume, average move:\n"
               f"   {pm['sel']:.2f} vs {pm['rest']:.2f} ADR (t {pm['t']:+.1f})\n")
    m2 += ("\nNOT IN THIS RUN: futures OI, delivery %, results days, order flow, "
           "slippage at the open.\nPaper research only.")
    return [m1, m2]


# ---------------- Kite data ----------------
def pull(kite, token, interval, days_back_from, days_back_to):
    to_d = dt.datetime.now() - dt.timedelta(days=days_back_to)
    from_d = dt.datetime.now() - dt.timedelta(days=days_back_from)
    for _ in range(3):
        try:
            data = kite.historical_data(token, from_d, to_d, interval)
            return pd.DataFrame(data)
        except Exception as e:
            if "Too many requests" in str(e):
                time.sleep(1.5)
                continue
            log.warning(f"pull failed token {token}: {e}")
            return pd.DataFrame()
    return pd.DataFrame()


def load_real() -> pd.DataFrame:
    from kiteconnect import KiteConnect
    import config
    import token_sync
    token_sync.refresh()
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(config.KITE_ACCESS_TOKEN)

    nfo = pd.DataFrame(kite.instruments("NFO"))
    fut_names = set(nfo[nfo["instrument_type"] == "FUT"]["name"].unique())
    nse = pd.DataFrame(kite.instruments("NSE"))
    eq = nse[(nse["segment"] == "NSE") & (nse["instrument_type"] == "EQ")
             & (nse["tradingsymbol"].isin(fut_names))]
    log.info(f"F&O stocks mapped: {len(eq)}")

    today = dt.date.today()
    frames = []
    for i, (_, r) in enumerate(eq.iterrows(), 1):
        parts = []
        for c in range(CHUNKS):
            d = pull(kite, int(r["instrument_token"]), "5minute",
                     CHUNK_DAYS * (c + 1), CHUNK_DAYS * c)
            if not d.empty:
                parts.append(d)
            time.sleep(0.35)
        if not parts:
            continue
        df = pd.concat(parts).drop_duplicates(subset=["date"])
        df["date"] = pd.to_datetime(df["date"])
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_localize(None)
        df = df[df["date"].dt.date < today]          # drop today's partial session
        t = day_table(df)
        if not t.empty:
            t["sym"] = r["tradingsymbol"]
            frames.append(t)
        if i % 25 == 0:
            log.info(f"pulled {i}/{len(eq)}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------- self-test on made-up data ----------------
def _fake(n_sym=60, n_days=90, planted=True, seed=1) -> pd.DataFrame:
    rs = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    hms = pd.date_range("09:15", "15:25", freq="5min").strftime("%H:%M")
    frames = []
    for s in range(n_sym):
        price = 100.0 + 400.0 * rs.random()
        sig = 0.0012 + 0.001 * rs.random()
        base_vol = 5e4 * (1 + 5 * rs.random())
        recs = []
        for d in days:
            hot = rs.random() < 0.08
            vmult = 3.0 if hot else 1.0
            smult = 2.0 if (hot and planted) else 1.0
            price *= 1 + rs.normal(0, 0.006)
            drift = 0.0
            for k, hm in enumerate(hms):
                o = price
                ret = rs.normal(drift, sig * smult)
                c = o * (1 + ret)
                h = max(o, c) * (1 + abs(rs.normal(0, sig / 3)))
                l = min(o, c) * (1 - abs(rs.normal(0, sig / 3)))
                v = base_vol * vmult * (2.0 if k < 6 else 1.0) * (0.5 + rs.random())
                recs.append((pd.Timestamp(f"{d.date()} {hm}"), o, h, l, c, v))
                price = c
                if k == 2 and hot and planted:
                    first = recs[-3][1]
                    drift = np.sign(c - first) * sig * 0.25
        df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
        t = day_table(df)
        if not t.empty:
            t["sym"] = f"S{s:03d}"
            frames.append(t)
    return pd.concat(frames, ignore_index=True)


def selftest():
    planted = analyse(_fake(planted=True, seed=1))
    null = analyse(_fake(planted=False, seed=2))
    for label, res in (("PLANTED EFFECT", planted), ("NO EFFECT", null)):
        print(f"\n===== SELF-TEST: {label} =====")
        for m in build_report(res):
            print(m)
    ok1 = passed(planted["P1"]) and planted["P1"]["mean"] > 0
    ok2 = passed(planted["P2"]) and planted["P2"]["mean"] > 0
    ok3 = not passed(null["P1"])
    print(f"\nplanted P1 detected: {ok1} | planted P2 detected: {ok2} | "
          f"null P1 stays quiet: {ok3}")
    return ok1 and ok2 and ok3


# ---------------- entry ----------------
def main():
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    from telegram_alert import send
    tab = load_real()
    if tab.empty:
        send("SELECTION STUDY: no data pulled. Check the Kite login / "
             "historical data add-on, then run again.")
        return
    res = analyse(tab)
    for m in build_report(res):
        log.info("\n" + m)
        send(m)
    log.info("selection study complete")


if __name__ == "__main__":
    main()
