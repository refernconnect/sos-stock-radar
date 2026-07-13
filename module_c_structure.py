"""sos-stock-radar :: module_c_structure
Nightly (18:30 IST) structure engine. For each universe symbol, computes the
levels table Module A scores against and Module B gates against.

Daily data source:
  - Appends today's bar from CM bhavcopy + delivery% + futures OI (FO bhavcopy).
  - One-time backfill available via backfill_daily(days) looping bhavcopies —
    free, no Kite historical subscription needed (run once, ~30-40 min).
"""
import json
import logging
import datetime as dt
import pandas as pd

import config, db, nse_data, universe, indicators as ind

log = logging.getLogger("module_c")

# ------------------------------------------------------------- daily upkeep
def append_today():
    today = dt.date.today()
    cm = nse_data.cm_bhavcopy(today)
    if cm is None:
        log.warning("append_today: no CM bhavcopy (holiday or URL change)")
        return
    dl = nse_data.delivery_data(today)
    fo = nse_data.fo_bhavcopy(today)
    uni = universe.load()
    fo_oi = {}
    if fo is not None:
        stf = fo[fo["FinInstrmTp"] == "STF"].copy()
        stf["XpryDt"] = pd.to_datetime(stf["XpryDt"]).dt.date
        near = stf.sort_values("XpryDt").groupby("TckrSymb").first()
        fo_oi = near["OpnIntrst"].to_dict()
    rows = []
    for sym in uni["symbol"]:
        r = cm[cm["TckrSymb"] == sym] if "TckrSymb" in cm.columns else cm[cm["SYMBOL"] == sym]
        if r.empty:
            continue
        r = r.iloc[0]
        deliv = None
        if dl is not None:
            m = dl[dl["SYMBOL"] == sym]
            if not m.empty:
                try:
                    deliv = float(m.iloc[0]["DELIV_PER"])
                except Exception:
                    deliv = None
        rows.append(dict(symbol=sym, d=today,
                         o=float(r.get("OpnPric", r.get("OPEN"))),
                         h=float(r.get("HghPric", r.get("HIGH"))),
                         l=float(r.get("LwPric", r.get("LOW"))),
                         c=float(r.get("ClsPric", r.get("CLOSE"))),
                         v=float(r.get("TtlTradgVol", r.get("TOTTRDQTY"))),
                         delivery_pct=deliv,
                         fut_oi=float(fo_oi.get(sym)) if sym in fo_oi else None))
    db.upsert(db.daily, rows, keys=["symbol", "d"])
    log.info(f"append_today: {len(rows)} bars stored")

def backfill_daily(days=400):
    """One-time historical backfill from bhavcopies. Run: python -c
    'import module_c_structure as m; m.backfill_daily(400)'"""
    d = dt.date.today()
    done = 0
    while done < days:
        d -= dt.timedelta(days=1)
        if d.weekday() >= 5:
            continue
        cm = nse_data.cm_bhavcopy(d)
        if cm is None:
            continue
        uni = universe.load()
        rows = []
        for sym in uni["symbol"]:
            r = cm[cm["TckrSymb"] == sym] if "TckrSymb" in cm.columns else pd.DataFrame()
            if r.empty:
                continue
            r = r.iloc[0]
            rows.append(dict(symbol=sym, d=d,
                             o=float(r["OpnPric"]), h=float(r["HghPric"]),
                             l=float(r["LwPric"]), c=float(r["ClsPric"]),
                             v=float(r["TtlTradgVol"]), delivery_pct=None, fut_oi=None))
        db.upsert(db.daily, rows, keys=["symbol", "d"])
        done += 1
        if done % 20 == 0:
            log.info(f"backfill: {done}/{days} sessions")
    log.info("backfill complete")

def _load_daily(sym) -> pd.DataFrame:
    q = f"SELECT * FROM daily WHERE symbol='{sym}' ORDER BY d ASC"
    df = pd.read_sql(q, db.engine)
    return df.tail(config.DAILY_LOOKBACK)

# ------------------------------------------------------------- levels build
def run():
    append_today()
    uni = universe.load()
    today = dt.date.today()
    results_hist = db.get_state("last_result_date", {})   # sym -> iso date
    for sym in nse_data.results_today():
        results_hist[sym] = today.isoformat()
    db.set_state("last_result_date", results_hist)

    out_rows = []
    for sym in uni["symbol"]:
        df = _load_daily(sym)
        if len(df) < 30:
            log.warning(f"levels: {sym} insufficient daily history ({len(df)})")
            continue
        last = df.iloc[-1]
        wk = df.tail(6).iloc[:-1]     # prior week approx (5 sessions before today)
        mo = df.tail(23).iloc[:-1]
        lv = {
            "close": float(last["c"]),
            "pdh": float(df["h"].iloc[-2]) if len(df) > 1 else None,
            "pdl": float(df["l"].iloc[-2]) if len(df) > 1 else None,
            "pwh": float(wk["h"].max()) if len(wk) else None,
            "pwl": float(wk["l"].min()) if len(wk) else None,
            "pmh": float(mo["h"].max()) if len(mo) else None,
            "pml": float(mo["l"].min()) if len(mo) else None,
            "hi_52w": float(df["h"].tail(250).max()),
            "lo_52w": float(df["l"].tail(250).min()),
            "adr21": ind.adr_pct(df),
            "ret_12w": ind.ret_pct(df, 60),
            "ema20": float(ind.ema(df["c"], 20).iloc[-1]),
            "prev_vol": float(df["v"].iloc[-2]) if len(df) > 1 else None,
            "avg_vol20": float(df["v"].tail(21).iloc[:-1].mean()),
            "delivery_pct": float(last["delivery_pct"]) if pd.notna(last["delivery_pct"]) else None,
            "oi_chg_pct": None,
            "trendlines": ind.trendlines(df),
            "base": ind.base_detect(df),
            "vcp": ind.vcp_detect(df),
            "c8020": _eighty_twenty(df),
        }
        lv.update(ind.nr7_inside(df))
        if len(df) > 1 and pd.notna(df["fut_oi"].iloc[-1]) and pd.notna(df["fut_oi"].iloc[-2]) \
                and df["fut_oi"].iloc[-2]:
            lv["oi_chg_pct"] = float((df["fut_oi"].iloc[-1] / df["fut_oi"].iloc[-2] - 1) * 100)
        # anchored VWAPs
        anchor = results_hist.get(sym)
        if anchor:
            idx = df.index[df["d"].astype(str) >= anchor]
            if len(idx):
                lv["avwap_earnings"] = ind.anchored_vwap(df, df.index.get_loc(idx[0]))
        if lv["base"]:
            base_end = len(df) - lv["base"]["sessions"]
            lv["avwap_base"] = ind.anchored_vwap(df.reset_index(drop=True), max(base_end, 0))
        # retest flag: broke a major level within last 3 sessions & pulled back into it
        lv["retest"] = _retest_flag(df, lv)
        out_rows.append(dict(symbol=sym, d=today, payload=json.dumps(lv)))
    db.upsert(db.levels, out_rows, keys=["symbol", "d"])
    log.info(f"module_c: levels stored for {len(out_rows)} symbols")

def _eighty_twenty(df) -> str | None:
    """Raschke 80-20: latest bar opens in one 20% extreme of its range and
    closes in the opposite 20% -> continuation bias next session."""
    b = df.iloc[-1]
    rng = b["h"] - b["l"]
    if rng <= 0:
        return None
    o_pos = (b["o"] - b["l"]) / rng
    c_pos = (b["c"] - b["l"]) / rng
    if o_pos >= 0.8 and c_pos <= 0.2:
        return "bear"
    if o_pos <= 0.2 and c_pos >= 0.8:
        return "bull"
    return None

def _retest_flag(df, lv):
    """Break of pmh/base-hi (long) or pml/base-lo (short) in last 3 sessions,
    with today's low/high pulling back within 1% of the broken level."""
    if len(df) < 5:
        return None
    last = df.iloc[-1]
    cands = []
    if lv.get("pmh"): cands.append(("long", lv["pmh"]))
    if lv.get("base"): cands.append(("long", lv["base"]["hi"]))
    if lv.get("pml"): cands.append(("short", lv["pml"]))
    if lv.get("base"): cands.append(("short", lv["base"]["lo"]))
    for side, level in cands:
        if not level:
            continue
        broke = any(
            (df["c"].iloc[-i] > level if side == "long" else df["c"].iloc[-i] < level)
            for i in range(2, 5) if len(df) > i
        )
        near = abs((last["l"] if side == "long" else last["h"]) - level) / level * 100 <= 1.0
        held = last["c"] > level if side == "long" else last["c"] < level
        if broke and near and held:
            return {"side": side, "level": float(level)}
    return None

def latest(sym) -> dict | None:
    q = f"SELECT payload FROM levels WHERE symbol='{sym}' ORDER BY d DESC LIMIT 1"
    with db.engine.begin() as cx:
        r = cx.exec_driver_sql(q).fetchone()
    return json.loads(r[0]) if r else None
