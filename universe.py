"""sos-stock-radar :: universe
Weekly (Sunday) build of the tradeable universe.

Ranking source: NSE F&O bhavcopy, near-month stock futures (FinInstrmTp=STF),
averaged turnover over last 5 trading days -> top UNIVERSE_SIZE names.
Token mapping: Kite instruments dump (NSE spot token + current-month NFO fut).
Exclusions applied here: unmapped tokens. Ban-list exclusion is done daily in
module_a (bans change daily, universe is weekly).
"""
import logging
import datetime as dt
import pandas as pd
from kiteconnect import KiteConnect

import config, db, nse_data, sectors

log = logging.getLogger("universe")

def _last_trading_days(n=5):
    out, d = [], dt.date.today()
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:                      # naive; NSE holidays fail soft
            out.append(d)
    return out

def build():
    frames = []
    for d in _last_trading_days(5):
        df = nse_data.fo_bhavcopy(d)
        if df is None:
            continue
        stf = df[df["FinInstrmTp"] == "STF"].copy()
        if stf.empty:
            continue
        stf["XpryDt"] = pd.to_datetime(stf["XpryDt"]).dt.date
        near = stf.sort_values("XpryDt").groupby("TckrSymb").first().reset_index()
        frames.append(near[["TckrSymb", "TtlTrfVal"]])
    if not frames:
        log.error("universe build: no bhavcopy data — keeping previous universe")
        return False

    alld = pd.concat(frames)
    rank = (alld.groupby("TckrSymb")["TtlTrfVal"].mean()
                 .sort_values(ascending=False)
                 .head(config.UNIVERSE_SIZE * 2))     # extra slack pre-mapping

    kite = KiteConnect(api_key=config.KITE_API_KEY)
    kite.set_access_token(config.KITE_ACCESS_TOKEN)
    nse = pd.DataFrame(kite.instruments("NSE"))
    nfo = pd.DataFrame(kite.instruments("NFO"))
    fut = nfo[nfo["instrument_type"] == "FUT"].copy()
    fut["expiry"] = pd.to_datetime(fut["expiry"]).dt.date
    fut = fut[fut["expiry"] >= dt.date.today()]
    fut = fut.sort_values("expiry").groupby("name").first().reset_index()

    rows, count = [], 0
    for sym, turn in rank.items():
        if count >= config.UNIVERSE_SIZE:
            break
        spot = nse[(nse["tradingsymbol"] == sym) & (nse["segment"] == "NSE")]
        f = fut[fut["name"] == sym]
        if spot.empty or f.empty:
            log.warning(f"universe: token mapping missing for {sym}, skipped")
            continue
        rows.append(dict(
            symbol=sym,
            spot_token=int(spot.iloc[0]["instrument_token"]),
            fut_token=int(f.iloc[0]["instrument_token"]),
            fut_symbol=f.iloc[0]["tradingsymbol"],
            lot_size=int(f.iloc[0]["lot_size"]),
            avg_turnover_cr=round(float(turn) / 1e7, 1),
            sector=sectors.sector_of(sym),
            updated=db.now_ist().replace(tzinfo=None),
        ))
        count += 1

    if len(rows) < config.UNIVERSE_SIZE * 0.6:
        log.error("universe build: too few mapped names — keeping previous")
        return False

    with db.engine.begin() as cx:
        cx.execute(db.universe.delete())
        cx.execute(db.universe.insert(), rows)
    log.info(f"universe built: {len(rows)} names")
    unmapped = [r["symbol"] for r in rows if r["sector"] == "NIFTY50"]
    if unmapped:
        log.warning(f"sector map fallback (NIFTY50) for: {unmapped}")
    return True

def load() -> pd.DataFrame:
    return pd.read_sql(db.universe.select(), db.engine)
