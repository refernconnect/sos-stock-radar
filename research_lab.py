"""sos-stock-radar :: research_lab   (paper research only; changes nothing live)

Pull once, test many. Saves every F&O stock's 5-minute bars (about six months)
plus NIFTY 50 to /tmp/lab_bars.pkl, then runs any rules file against them.

RUN (Railway console; not between 08:40 and 15:35 IST)
  /app/.venv/bin/python research_lab.py cache              pull bars (~7 min)
  /app/.venv/bin/python research_lab.py run lab_rules3.py  test a rules file
  python research_lab.py --selftest                        made-up data

A rules file defines:
  NAME     : str
  PASS_T   : float (pre-registered threshold)
  RULES    : dict name -> function(g, ctx) -> (side, raw_return) or None
             g   = that stock's 5-minute bars for the day (hm, open, high, low,
                   close, volume, vwap), 09:15 first
             ctx = dict(o, pc, adr, atr, rvol, rvol5, nifty (Nifty bars that
                   day or None), sigma (per-minute noise band, Series))
  KEY      : optional dict name -> selection key ("rvol" default)
Every rule is also run with the SAME exits but a coin-flip side (baseline),
so an edge must come from the side, not from the exit shape.

Cost 0.10% round trip charged by the engine; rules put 0.05% slippage into
their entry price (and stop fills) with SLIP, as walk() does for stops.
Unit: ADR (prior 20-session average daily range). Top 10 per day.
PASS = mean > 0, t >= PASS_T, both halves > 0, positive before AND from
3 Aug 2026, and mean above the coin-flip baseline.
"""
import sys
import time
import pickle
import logging
import importlib.util
import datetime as dt

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("research_lab")

CACHE = "/tmp/lab_bars.pkl"
COST, SLIP, TOP_N = 0.0010, 0.0005, 10
SPLIT = dt.date(2026, 8, 3)
EXIT_HM = "15:00"


def _ist_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).replace(tzinfo=None)


# ---------------------------------------------------------------- data
def _prep(df):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.sort_values("date").drop_duplicates("date")
    df["d"] = df["date"].dt.date
    df["hm"] = df["date"].dt.strftime("%H:%M")
    df = df[(df["hm"] >= "09:15") & (df["hm"] <= "15:25")]
    return df[["d", "hm", "open", "high", "low", "close", "volume"]]


def cache():
    from kiteconnect import KiteConnect
    import config, token_sync
    token_sync.refresh()
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(config.KITE_ACCESS_TOKEN)
    nfo = pd.DataFrame(kite.instruments("NFO"))
    fut = set(nfo[nfo["instrument_type"] == "FUT"]["name"].unique())
    nse = pd.DataFrame(kite.instruments("NSE"))
    eq = nse[(nse["segment"] == "NSE") & (nse["instrument_type"] == "EQ") & nse["tradingsymbol"].isin(fut)]
    idx = nse[nse["tradingsymbol"] == "NIFTY 50"]
    todo = [(r["tradingsymbol"], int(r["instrument_token"])) for _, r in eq.iterrows()]
    todo += [("NIFTY 50", int(idx["instrument_token"].iloc[0]))] if len(idx) else []
    today, out = _ist_now().date(), {}
    for i, (sym, tok) in enumerate(todo, 1):
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
                    break
            end = start
            time.sleep(0.35)
        if parts:
            df = pd.concat(parts)
            if not df.empty:
                df = _prep(df)
                out[sym] = df[df["d"] < today]
        if i % 25 == 0:
            log.info(f"pulled {i}/{len(todo)}")
    with open(CACHE, "wb") as f:
        pickle.dump(out, f)
    log.info(f"cached {len(out)} symbols to {CACHE}")


# ---------------------------------------------------------------- engine
def build(bars):
    """bars: dict sym -> prepared frame. Returns list of stock-day records."""
    nifty = {}
    if "NIFTY 50" in bars:
        for d, g in bars["NIFTY 50"].groupby("d", sort=True):
            nifty[d] = g.reset_index(drop=True)
    recs = []
    for sym, df in bars.items():
        if sym == "NIFTY 50":
            continue
        days = [(d, g.reset_index(drop=True)) for d, g in df.groupby("d", sort=True)
                if len(g) >= 60 and g["hm"].iloc[0] == "09:15"]
        if len(days) < 25:
            continue
        o = np.array([g["open"].iloc[0] for _, g in days])
        h = np.array([g["high"].max() for _, g in days])
        l = np.array([g["low"].min() for _, g in days])
        c = np.array([g["close"].iloc[-1] for _, g in days])
        v15 = np.array([g["volume"].iloc[:3].sum() for _, g in days], dtype=float)
        v5 = np.array([g["volume"].iloc[0] for _, g in days], dtype=float)
        pc = np.r_[np.nan, c[:-1]]
        tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
        atr = pd.Series(tr).shift(1).rolling(14).mean().values
        adr = pd.Series((h - l) / c).shift(1).rolling(20).mean().values
        rvol = v15 / pd.Series(v15).shift(1).rolling(14).mean().values
        rvol5 = v5 / pd.Series(v5).shift(1).rolling(14).mean().values
        mv = pd.DataFrame([(g.set_index("hm")["close"] / g["open"].iloc[0] - 1).abs() for _, g in days])
        sig = mv.shift(1).rolling(14, min_periods=10).mean()
        for i, (d, g) in enumerate(days):
            if i < 20 or np.isnan(adr[i]) or np.isnan(atr[i]) or np.isnan(rvol[i]):
                continue
            tp = (g["high"] + g["low"] + g["close"]) / 3
            cv = g["volume"].cumsum().replace(0, np.nan)
            g = g.assign(vwap=((tp * g["volume"]).cumsum() / cv).fillna(g["close"]))
            recs.append(dict(sym=sym, d=d, g=g, ctx=dict(o=o[i], pc=pc[i], adr=adr[i], atr=atr[i],
                        rvol=rvol[i], rvol5=rvol5[i], nifty=nifty.get(d), sigma=sig.iloc[i])))
    return recs


def net(side, raw):
    return raw - COST          # slippage is already in the rule's entry/stop prices


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


def evaluate(recs, mod, seed=7):
    rs = np.random.default_rng(seed)
    keys = getattr(mod, "KEY", {})
    out = {}
    for name, fn in mod.RULES.items():
        key = keys.get(name, "rvol")
        by_day = {}
        for r in recs:
            k = r["ctx"].get(key)
            if k is None or np.isnan(k):
                continue
            by_day.setdefault(r["d"], []).append((k, r))
        real, base, trades, wins = {}, {}, 0, 0
        for d, lst in sorted(by_day.items()):
            lst.sort(key=lambda z: -z[0])
            rv, bv = [], []
            for _, r in lst[:TOP_N]:
                res = fn(r["g"], dict(r["ctx"], side_override=None))
                if res is None:
                    continue
                side, raw = res
                x = net(side, raw) / r["ctx"]["adr"]
                rv.append(x); trades += 1; wins += x > 0
                flip = int(rs.choice((-1, 1)))
                rb = fn(r["g"], dict(r["ctx"], side_override=flip))
                if rb is not None:
                    bv.append(net(*rb) / r["ctx"]["adr"])
            if rv:
                real[d] = np.mean(rv)
            if bv:
                base[d] = np.mean(bv)
        s = pd.Series(real).sort_index()
        b = pd.Series(base).sort_index()
        out[name] = dict(all=stats(s.values),
                         pre=stats(s[[d < SPLIT for d in s.index]].values),
                         post=stats(s[[d >= SPLIT for d in s.index]].values),
                         base=float(b.mean()) if len(b) else np.nan,
                         trades=trades, win=100 * wins / trades if trades else np.nan)
    days = sorted({r["d"] for r in recs})
    return out, days[0], days[-1], len(days), len({r["sym"] for r in recs})


def passed(x, pass_t):
    a, pre, post = x["all"], x["pre"], x["post"]
    return bool(a and pre and post and a["mean"] > 0 and a["t"] >= pass_t and a["h1"] > 0
                and a["h2"] > 0 and pre["mean"] > 0 and post["mean"] > 0 and a["mean"] > x["base"])


def report(res, first, last, ndays, nsym, mod):
    m = (f"{mod.NAME} (times IST)\n{first} to {last}\n{ndays} sessions, {nsym} F&O stocks, top 10 a day\n"
         f"Cost 0.10% + 0.05% slip; ADR per trade\n"
         f"PASS = mean > 0, t >= {mod.PASS_T}, halves > 0, both periods > 0, beats coin-flip\n\n")

    def ln(s):
        return "n/a" if s is None else f"{s['mean']:+.3f} (t {s['t']:+.1f}, {s['n']}d)"
    for name, x in res.items():
        a = x["all"]
        m += (f"{name}: {x['trades']} trades, win {x['win']:.0f}%\n"
              f"   all {ln(a)}\n"
              + (f"   halves {a['h1']:+.3f} / {a['h2']:+.3f}\n" if a else "")
              + f"   before 3 Aug {ln(x['pre'])}\n   from 3 Aug {ln(x['post'])}\n"
              f"   coin-flip same exits {x['base']:+.3f}\n"
              f"   -> {'PASS' if passed(x, mod.PASS_T) else 'NOT DETECTED'}\n\n")
    return m + "Paper research only."


def load_rules(path):
    spec = importlib.util.spec_from_file_location("rules", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- helpers for rules
def walk(g, k0, side, entry, stop=None, exit_hm=EXIT_HM, trail=None):
    """From bar k0 on: stop (touch, gap-aware) or exit at exit_hm open.
    trail(g, k, side, stop) -> new stop or None. Returns raw return."""
    for k in range(k0, len(g)):
        b = g.iloc[k]
        if b["hm"] >= exit_hm:
            return side * (b["open"] / entry - 1)
        if stop is not None:
            if side == 1 and b["low"] <= stop:
                return side * (min(stop, b["open"]) * (1 - SLIP) / entry - 1)
            if side == -1 and b["high"] >= stop:
                return side * (max(stop, b["open"]) * (1 + SLIP) / entry - 1)
        if trail is not None:
            ns = trail(g, k, side, stop)
            if ns is not None:
                stop = ns
    return side * (g["close"].iloc[-1] / entry - 1)


# ---------------------------------------------------------------- selftest
def _fake(planted, seed, n_sym=40, n_days=70):
    rs = np.random.default_rng(seed)
    days = pd.bdate_range("2026-06-01", periods=n_days)
    hms = pd.date_range("09:15", "15:25", freq="5min").strftime("%H:%M")
    bars = {}
    for s in range(n_sym):
        price, recs = 100 + 400 * rs.random(), []
        for d in days:
            hot, side = rs.random() < 0.2, rs.choice((-1, 1))
            price *= 1 + rs.normal(0, 0.006)
            for hm in hms:
                o = price
                c = o * (1 + (side * 0.0008 if hot and planted else 0) + rs.normal(0, 0.0018))
                recs.append((d.date(), hm, o, max(o, c) * 1.0006, min(o, c) * 0.9994, c,
                             (3 if hot else 1) * rs.integers(4000, 9000)))
                price = c
        bars[f"S{s}"] = pd.DataFrame(recs, columns=["d", "hm", "open", "high", "low", "close", "volume"])
    return bars


class _Demo:
    NAME, PASS_T = "LAB SELFTEST", 2.3

    @staticmethod
    def follow(g, ctx):
        k = 6
        side = ctx["side_override"] or (1 if g["close"].iloc[k - 1] > ctx["o"] else -1)
        entry = g["open"].iloc[k] * (1 + side * SLIP)
        return side, walk(g, k, side, entry)
    RULES = {"follow 09:45": follow.__func__}


def selftest():
    ok = True
    for planted in (True, False):
        res, *meta = evaluate(build(_fake(planted, 11 if planted else 12)), _Demo)
        print(f"\n===== {'PLANTED' if planted else 'NO'} EFFECT =====\n" + report(res, *meta, _Demo))
        p = passed(res["follow 09:45"], _Demo.PASS_T)
        ok &= p if planted else not p
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


def main():
    a = sys.argv[1:]
    if "--selftest" in a:
        sys.exit(0 if selftest() else 1)
    if a and a[0] == "cache":
        cache()
        return
    if len(a) >= 2 and a[0] == "run":
        with open(CACHE, "rb") as f:
            bars = pickle.load(f)
        mod = load_rules(a[1])
        msg = report(*evaluate(build(bars), mod), mod)
        log.info("\n" + msg)
        if "--send" in a:
            from telegram_alert import send
            send(msg)
        return
    print(__doc__)


if __name__ == "__main__":
    main()
