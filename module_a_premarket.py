"""sos-stock-radar :: module_a_premarket
08:45 IST digest. Applies v1.3 hard filters + v1.5 catalyst override,
scores the universe, publishes the day's watchlist to Telegram and DB.

HARD FILTERS (name excluded entirely):
  - F&O ban list
  - Momentum pool: ret_12w >= MOMENTUM_12W_MIN -> LONG pool;
                   ret_12w <= MOMENTUM_12W_SHORT -> SHORT pool;
                   RESULTS today -> AUTO pool (catalyst override, direction
                   decided live by tape in module_b);
                   else out
  - Regime gate: suspends LONG/SHORT pools against the tape; AUTO exempt
"""
import json
import logging
import datetime as dt
import pandas as pd
from kiteconnect import KiteConnect

import config, db, nse_data, universe, sectors, module_c_structure as mc
from telegram_alert import send

log = logging.getLogger("module_a")

def _kite():
    k = KiteConnect(api_key=config.KITE_API_KEY)
    k.set_access_token(config.KITE_ACCESS_TOKEN)
    return k

def _regime(kite) -> dict:
    out = {"nifty_above_ema20": None, "vix": None, "label": "UNKNOWN"}
    try:
        q = kite.quote(["NSE:NIFTY 50", "NSE:INDIA VIX"])
        nifty = q["NSE:NIFTY 50"]["last_price"]
        out["vix"] = q["NSE:INDIA VIX"]["last_price"]
        hist = db.get_state("nifty_closes", [])
        hist = (hist + [nifty])[-60:]
        db.set_state("nifty_closes", hist)
        if len(hist) >= 20:
            s = pd.Series(hist)
            ema20 = s.ewm(span=20, adjust=False).mean().iloc[-1]
            out["nifty_above_ema20"] = bool(nifty > ema20)
            out["label"] = "RISK-ON" if nifty > ema20 else "RISK-OFF"
    except Exception as e:
        log.warning(f"regime check failed: {e}")
    return out

def run():
    import token_sync
    token_sync.refresh()
    today = dt.date.today()
    kite = _kite()
    uni = universe.load()
    bans = set(nse_data.ban_list())
    results = nse_data.results_today()
    regime = _regime(kite)
    mw = nse_data.mwpl()
    mwpl_pct = {}
    if mw is not None:
        for _, r in mw.iterrows():
            try:
                sym = str(r.get("Symbol", r.get("SYMBOL", ""))).strip()
                pct = float(r.get("MWPL", r.get("Open Interest as % of MWPL", 0)))
                if sym:
                    mwpl_pct[sym] = pct
            except Exception:
                continue

    scored, unmapped = [], []
    for _, u in uni.iterrows():
        sym = u["symbol"]
        if sym in bans:
            continue
        lv = mc.latest(sym)
        if not lv:
            continue
        ret12 = lv.get("ret_12w", 0)
        if ret12 >= config.MOMENTUM_12W_MIN:
            pool = "LONG"
        elif ret12 <= config.MOMENTUM_12W_SHORT:
            pool = "SHORT"
        elif sym in results:
            pool = "AUTO"          # v1.5 catalyst override: news first (SMB)
        else:
            continue
        # regime suspension applies to directional pools only
        if regime["nifty_above_ema20"] is True and pool == "SHORT":
            continue
        if regime["nifty_above_ema20"] is False and pool == "LONG":
            continue

        score, tags = 0, []
        if sym in results:
            score += 3
            tags.append("RESULTS-OVR" if pool == "AUTO" else "RESULTS")
        if lv.get("prev_vol") and lv.get("avg_vol20") and lv["prev_vol"] > 2 * lv["avg_vol20"]:
            score += 2; tags.append("VOL2X")
        if lv.get("oi_chg_pct") and abs(lv["oi_chg_pct"]) > config.OI_DELTA_MIN:
            score += 2; tags.append(f"OI{lv['oi_chg_pct']:+.0f}%")
        if lv.get("delivery_pct") and lv["delivery_pct"] > 60:
            score += 2; tags.append(f"DLV{lv['delivery_pct']:.0f}")
        near_level = _near_major_level(lv)
        if near_level or lv.get("retest"):
            score += 2; tags.append("RETEST" if lv.get("retest") else f"@{near_level}")
        for flag in ("vcp", "nr7", "inside"):
            if lv.get(flag):
                score += 1; tags.append(flag.upper())
        c8020 = lv.get("c8020")
        if (c8020 == "bull" and pool == "LONG") or (c8020 == "bear" and pool == "SHORT"):
            score += 1; tags.append("80-20")
        if lv.get("base"):
            score += 1; tags.append(f"BASE{lv['base']['sessions']}d")
        sq = mwpl_pct.get(sym)
        if sq and 80 <= sq <= 92:
            tags.append("🔥SQZ")
        if u["sector"] == "NIFTY50":
            unmapped.append(sym)
        scored.append(dict(symbol=sym, pool=pool, score=score, tags=tags,
                           sector=u["sector"], adr21=lv.get("adr21"),
                           fut_token=int(u["fut_token"]),
                           spot_token=int(u["spot_token"]),
                           fut_symbol=u["fut_symbol"], lot=int(u["lot_size"]),
                           levels=lv))
    scored.sort(key=lambda x: x["score"], reverse=True)
    watch = scored[:config.WATCHLIST_SIZE]
    payload = {"date": today.isoformat(), "regime": regime,
               "bans_seen": len(bans), "watch": watch}
    db.upsert(db.watchlist, [dict(d=today, payload=json.dumps(payload))], keys=["d"])
    if unmapped:
        log.warning(f"unmapped sectors: {set(unmapped)}")

    lines = [f"📋 [STOCK] Digest {today:%d-%b} | Regime: {regime['label']}"
             f" | VIX {regime['vix'] or '?'}"]
    if not bans:
        lines.append("⚠ Ban list fetch empty — verify manually before trading")
    for w in watch:
        dot = "🟢" if w["pool"] == "LONG" else "🔴" if w["pool"] == "SHORT" else "🟡"
        lines.append(f"{dot} {w['symbol']} [{w['score']}] "
                     f"{' '.join(w['tags']) or '—'} | {w['sector']}")
    if not watch:
        lines.append("No names cleared hard filters today. Correct position: none.")
    lines.append("— full-universe scan arms at " + config.FULL_SCAN_AFTER)
    send("\n".join(lines))
    log.info(f"module_a: watchlist {len(watch)} names published")
    return payload

def _near_major_level(lv) -> str | None:
    c = lv.get("close")
    if not c:
        return None
    for name in ("hi_52w", "pmh", "pml", "lo_52w"):
        val = lv.get(name)
        if val and abs(c - val) / val * 100 <= 1.0:
            return name.upper()
    for tl in lv.get("trendlines", []):
        if abs(c - tl["value"]) / tl["value"] * 100 <= 1.0:
            return f"TL-{tl['kind'].upper()}"
    return None
