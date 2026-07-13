"""sos-stock-radar :: module_b_live
Live engine, 09:10–15:00 IST. Subscribes spot+futures for the watchlist plus
NIFTY 50 and relevant sector indices. Evaluates the v1.3 gate stack on each
completed 5m bar and fires at most MAX_ALERTS_PER_DAY alerts.

GATES (all must pass unless noted):
  G1 gap integrity        G2 RVOL >= RVOL_MIN (time-of-day normalized)
  G3 VWAP side            G4 OI quadrant (Fresh Buying/Selling; SC tagged ⚠)
  G5 DT trigger (sweep-reclaim) or ORB15 break+retest
  G6 room: RR >= RR_MIN to nearest opposing structure; stop < ADR_STOP_MAX_PCT of ADR
  G7 RS vs sector >= RS_SECTOR_MIN and RS vs NIFTY positive
  G8 aggressor delta agrees (no divergence at trigger)
  G9 structure behind trade (Module C level within stop distance)
LIMITS: observe till 09:45; none after NO_NEW_ALERTS_AFTER; 1/name; kill-switch
after KILL_SWITCH_SL_COUNT paper SL hits.
WebSocket reconnect: kiteconnect auto-reconnect enabled from day one.
"""
import json
import logging
import datetime as dt
import threading
from collections import defaultdict, deque

import pytz
from kiteconnect import KiteTicker, KiteConnect

import config, db, sectors
from telegram_alert import send
import paper_log

log = logging.getLogger("module_b")
IST = pytz.timezone(config.TZ)

SLOT_MINUTES = 5

class SymState:
    def __init__(self, meta):
        self.meta = meta                       # watchlist entry incl. levels
        self.bars = deque(maxlen=100)          # completed 5m bars (spot)
        self.cur = None                        # forming bar
        self.cum_vol = 0.0
        self.pv = 0.0                          # Σ price*qty for session VWAP
        self.vq = 0.0                          # Σ qty
        self.delta = 0.0                       # cumulative aggressor delta
        self.delta_at_bar = deque(maxlen=100)
        self.last_price = None
        self.prev_close = meta["levels"]["close"]
        self.day_open = None
        self.fut_oi = deque(maxlen=60)         # (ts, oi)
        self.alerted = False

class LiveEngine:
    def __init__(self):
        self.states: dict[int, SymState] = {}     # spot_token -> state
        self.fut_map: dict[int, int] = {}         # fut_token -> spot_token
        self.index_change: dict[str, float] = {}  # index name -> %chg from prev close
        self.index_prev: dict[int, float] = {}
        self.index_tokens: dict[int, str] = {}
        self.alerts_today = 0
        self.sl_hits = 0
        self.muted = False
        self.day_type: dict[int, str] = {}
        self.orb: dict[int, dict] = {}            # token -> {hi, lo} first 15m
        self.breadth_prev: dict[int, float] = {}  # universe spot token -> prev close
        self.breadth_last: dict[int, float] = {}  # universe spot token -> last price
        self.lock = threading.Lock()

    # ---------------------------------------------------------- setup
    def load_watchlist(self):
        today = dt.date.today()
        with db.engine.begin() as cx:
            r = cx.exec_driver_sql(
                f"SELECT payload FROM watchlist WHERE d='{today}'").fetchone()
        if not r:
            log.error("no watchlist for today — module_a did not run?")
            return False
        payload = json.loads(r[0])
        self.regime = payload["regime"]
        kite = KiteConnect(api_key=config.KITE_API_KEY)
        kite.set_access_token(config.KITE_ACCESS_TOKEN)
        nse = kite.instruments("NSE")
        idx_lookup = {i["tradingsymbol"]: i["instrument_token"]
                      for i in nse if i["segment"] == "INDICES"}
        needed = {"NIFTY 50"}
        for w in payload["watch"]:
            st = SymState(w)
            self.states[w["spot_token"]] = st
            self.fut_map[w["fut_token"]] = w["spot_token"]
            needed.add(sectors.sector_index_symbol(w["sector"]))
        for name in needed:
            tok = idx_lookup.get(name)
            if tok:
                self.index_tokens[tok] = name
        q = kite.quote([f"NSE:{n}" for n in needed])
        for name in needed:
            d = q.get(f"NSE:{name}")
            if d:
                tok = idx_lookup.get(name)
                self.index_prev[tok] = d["ohlc"]["close"]
        # Cook-style breadth: subscribe ALL universe spots (LTP is enough)
        import universe as unimod, module_c_structure as mc
        uni = unimod.load()
        for _, u in uni.iterrows():
            lvl = mc.latest(u["symbol"])
            if lvl and lvl.get("close"):
                self.breadth_prev[int(u["spot_token"])] = lvl["close"]
        return len(self.states) > 0

    def breadth(self) -> float | None:
        pairs = [(self.breadth_last.get(t), pc) for t, pc in self.breadth_prev.items()]
        pairs = [(lp, pc) for lp, pc in pairs if lp]
        if len(pairs) < 10:
            return None
        up = sum(1 for lp, pc in pairs if lp > pc)
        return up / len(pairs) * 100

    # ---------------------------------------------------------- ticks
    def on_ticks(self, ws, ticks):
        now = dt.datetime.now(IST)
        for t in ticks:
            tok = t["instrument_token"]
            if tok in self.index_tokens:
                prev = self.index_prev.get(tok)
                if prev:
                    self.index_change[self.index_tokens[tok]] = \
                        (t["last_price"] / prev - 1) * 100
                continue
            if tok in self.fut_map:
                st = self.states.get(self.fut_map[tok])
                if st is not None and "oi" in t:
                    st.fut_oi.append((now, t["oi"]))
                continue
            if tok in self.breadth_prev:
                self.breadth_last[tok] = t["last_price"]
            st = self.states.get(tok)
            if st is None:
                continue
            self._ingest_spot(st, t, now, tok)
        paper_log.monitor({self.states[k].meta["symbol"]: self.states[k].last_price
                           for k in self.states if self.states[k].last_price})

    def _ingest_spot(self, st, t, now, tok):
        lp = t["last_price"]
        qty = t.get("last_traded_quantity", t.get("last_quantity", 0)) or 0
        st.last_price = lp
        if st.day_open is None:
            st.day_open = t.get("ohlc", {}).get("open") or lp
        # session VWAP + cumulative volume
        st.pv += lp * qty
        st.vq += qty
        st.cum_vol = t.get("volume_traded", st.cum_vol + qty)
        # aggressor delta (tick rule vs best bid/ask when available)
        depth = t.get("depth")
        side = 0
        if depth and depth.get("buy") and depth.get("sell"):
            bid = depth["buy"][0]["price"]; ask = depth["sell"][0]["price"]
            if lp >= ask: side = 1
            elif lp <= bid: side = -1
        if side == 0 and st.bars:
            side = 1 if lp > st.bars[-1]["c"] else (-1 if lp < st.bars[-1]["c"] else 0)
        st.delta += side * qty
        # 5m bar building
        slot = now.replace(second=0, microsecond=0,
                           minute=(now.minute // SLOT_MINUTES) * SLOT_MINUTES)
        if st.cur is None or st.cur["ts"] != slot:
            if st.cur is not None:
                st.bars.append(st.cur)
                st.delta_at_bar.append(st.delta)
                self._on_bar_close(st, tok)
            st.cur = {"ts": slot, "o": lp, "h": lp, "l": lp, "c": lp, "v": 0}
        b = st.cur
        b["h"] = max(b["h"], lp); b["l"] = min(b["l"], lp); b["c"] = lp; b["v"] += qty

    # ---------------------------------------------------------- evaluation
    def _on_bar_close(self, st, tok):
        now = dt.datetime.now(IST)
        hm = now.strftime("%H:%M")
        # ORB capture
        if tok not in self.orb and hm >= "09:30" and len(st.bars) >= 3:
            first3 = list(st.bars)[:3]
            self.orb[tok] = {"hi": max(b["h"] for b in first3),
                             "lo": min(b["l"] for b in first3)}
        # day-type at 09:45
        if tok not in self.day_type and hm >= "09:45":
            self.day_type[tok] = self._classify_day(st, tok)
        if hm < config.OBSERVE_UNTIL or hm >= config.NO_NEW_ALERTS_AFTER:
            return
        if self.muted or st.alerted or self.alerts_today >= config.MAX_ALERTS_PER_DAY:
            return
        direction = st.meta["pool"]              # LONG/SHORT from Module A
        g = self._gates(st, tok, direction)
        if g["all_pass"]:
            self._fire(st, tok, direction, g)

    def _classify_day(self, st, tok) -> str:
        gap = (st.day_open / st.prev_close - 1) * 100 if st.prev_close else 0
        orb = self.orb.get(tok)
        adr = st.meta.get("adr21") or 2.0
        if orb and abs(gap) >= 1.0:
            rng = orb["hi"] - orb["lo"]
            if rng > 0:
                pos = (st.day_open - orb["lo"]) / rng
                fr30 = rng / st.prev_close * 100 if st.prev_close else 0
                if (pos >= 0.8 or pos <= 0.2) and fr30 >= 0.6 * adr:
                    return "TREND"
        return "NORMAL"

    def _rvol(self, st) -> float | None:
        now = dt.datetime.now(IST)
        slot = ((now.hour - 9) * 60 + now.minute - 15) // SLOT_MINUTES
        with db.engine.begin() as cx:
            r = cx.exec_driver_sql(
                "SELECT avg_cum_vol, days FROM rvol_baseline "
                f"WHERE symbol='{st.meta['symbol']}' AND slot={int(slot)}").fetchone()
        if not r or r[1] < config.RVOL_MIN_BASELINE_DAYS or not r[0]:
            return None                            # bootstrap: gate reports UNKNOWN
        return st.cum_vol / r[0]

    def _oi_quadrant(self, st, direction) -> tuple[str, float]:
        if len(st.fut_oi) < 2:
            return "UNKNOWN", 0.0
        now = dt.datetime.now(IST)
        old = [x for x in st.fut_oi if (now - x[0]).seconds >= 15 * 60]
        base = old[-1][1] if old else st.fut_oi[0][1]
        cur = st.fut_oi[-1][1]
        if not base:
            return "UNKNOWN", 0.0
        oi_chg = (cur / base - 1) * 100
        px_up = st.last_price > (st.bars[-3]["c"] if len(st.bars) >= 3 else st.day_open)
        if oi_chg > 0 and px_up: quad = "FRESH_BUYING"
        elif oi_chg > 0 and not px_up: quad = "WRITING_PRESSURE"
        elif oi_chg < 0 and px_up: quad = "SHORT_COVERING"
        else: quad = "UNWINDING"
        return quad, oi_chg

    def _gates(self, st, tok, direction) -> dict:
        g = {"all_pass": False}
        lp = st.last_price
        lv = st.meta["levels"]
        # G1 gap
        gap = (st.day_open / st.prev_close - 1) * 100 if st.prev_close else 0
        g["gap"] = round(gap, 2)
        if abs(gap) > config.GAP_SKIP_PCT:
            return g
        if abs(gap) > config.GAP_HOLD_PCT:
            mid = (st.day_open + st.prev_close) / 2
            if (direction == "LONG" and lp < mid) or (direction == "SHORT" and lp > mid):
                return g
        # G2 RVOL
        rvol = self._rvol(st)
        g["rvol"] = round(rvol, 2) if rvol else "BOOT"
        if rvol is not None and rvol < config.RVOL_MIN:
            return g
        # G3 VWAP
        vwap = st.pv / st.vq if st.vq else None
        g["vwap"] = round(vwap, 2) if vwap else None
        if vwap is None or (direction == "LONG" and lp < vwap) \
                        or (direction == "SHORT" and lp > vwap):
            return g
        # G4 OI quadrant
        quad, oichg = self._oi_quadrant(st, direction)
        g["oi"] = f"{quad} {oichg:+.1f}%"
        ok_quad = (direction == "LONG" and quad == "FRESH_BUYING") or \
                  (direction == "SHORT" and quad in ("FRESH_BUYING", "WRITING_PRESSURE")
                   and oichg > 0 and quad == "WRITING_PRESSURE")
        g["sc_flag"] = quad == "SHORT_COVERING"
        if not ok_quad and not g["sc_flag"]:
            return g
        # G5 trigger
        trig, sl_ref = self._trigger(st, tok, direction)
        g["trigger"] = trig
        if not trig:
            return g
        # G6 room / RR / ADR
        sl = sl_ref
        risk = abs(lp - sl)
        adr = st.meta.get("adr21") or 2.0
        if risk / lp * 100 > adr * config.ADR_STOP_MAX_PCT / 100:
            return g
        t_full = lp * (1 + config.T2_PCT / 100) if direction == "LONG" \
                 else lp * (1 - config.T2_PCT / 100)
        opposing = self._nearest_opposing(lv, lp, direction)
        room_edge = min(t_full, opposing) if direction == "LONG" else max(t_full, opposing)
        rr = abs(room_edge - lp) / risk if risk else 0
        g["rr"] = round(rr, 1)
        if rr < config.RR_MIN:
            return g
        # G7 RS
        nifty_chg = self.index_change.get("NIFTY 50", 0)
        sec_name = sectors.sector_index_symbol(st.meta["sector"])
        sec_chg = self.index_change.get(sec_name, nifty_chg)
        stock_chg = (lp / st.prev_close - 1) * 100 if st.prev_close else 0
        g["rs"] = f"stk{stock_chg:+.1f} sec{sec_chg:+.1f} nif{nifty_chg:+.1f}"
        sign = 1 if direction == "LONG" else -1
        if sign * stock_chg <= 0:
            return g
        if abs(sec_chg) > 0.05 and sign * stock_chg / abs(sec_chg) < config.RS_SECTOR_MIN \
                and sign * sec_chg <= 0:
            return g
        if sign * (stock_chg - nifty_chg) <= 0:
            return g
        # G8 delta agreement
        if len(st.delta_at_bar) >= 3:
            d_now, d_prev = st.delta_at_bar[-1], st.delta_at_bar[-3]
            if direction == "LONG" and d_now <= d_prev:
                return g
            if direction == "SHORT" and d_now >= d_prev:
                return g
        # G9 structure behind
        if not self._structure_behind(lv, sl, lp, direction):
            return g
        # G10 market internals (Cook breadth proxy)
        br = self.breadth()
        g["breadth"] = round(br, 0) if br is not None else "BOOT"
        if br is not None:
            if direction == "LONG" and br < config.BREADTH_MIN_LONG:
                return g
            if direction == "SHORT" and br > config.BREADTH_MAX_SHORT:
                return g
        g["sl"] = sl
        g["all_pass"] = True
        return g

    def _trigger(self, st, tok, direction):
        """DT Radar port: sweep of prior 5m swing then close back through;
        or ORB15 break with retest hold."""
        bars = list(st.bars)
        if len(bars) < 6:
            return None, None
        last = bars[-1]
        # swings (1 bar each side on 5m)
        for i in range(len(bars) - 3, max(len(bars) - 15, 1), -1):
            if direction == "LONG" and bars[i]["l"] < bars[i-1]["l"] and bars[i]["l"] < bars[i+1]["l"]:
                sw = bars[i]["l"]
                if last["l"] < sw and last["c"] > sw:
                    return "DT-SWEEP", last["l"]
                break
            if direction == "SHORT" and bars[i]["h"] > bars[i-1]["h"] and bars[i]["h"] > bars[i+1]["h"]:
                sw = bars[i]["h"]
                if last["h"] > sw and last["c"] < sw:
                    return "DT-SWEEP", last["h"]
                break
        # Williams OOPS: gap beyond prior day's extreme, then reclaim of it
        lv = st.meta["levels"]
        day_lo = min(b["l"] for b in bars)
        day_hi = max(b["h"] for b in bars)
        if direction == "LONG" and lv.get("pdl") and st.day_open < lv["pdl"] \
                and last["c"] > lv["pdl"]:
            return "OOPS", day_lo
        if direction == "SHORT" and lv.get("pdh") and st.day_open > lv["pdh"] \
                and last["c"] < lv["pdh"]:
            return "OOPS", day_hi
        orb = self.orb.get(tok)
        if orb:
            if direction == "LONG" and last["c"] > orb["hi"] \
                    and last["l"] <= orb["hi"] * 1.002:
                return "ORB-RETEST", orb["hi"] * 0.995
            if direction == "SHORT" and last["c"] < orb["lo"] \
                    and last["h"] >= orb["lo"] * 0.998:
                return "ORB-RETEST", orb["lo"] * 1.005
        return None, None

    def _nearest_opposing(self, lv, lp, direction):
        cands = []
        keys = ("pdh", "pwh", "pmh", "hi_52w") if direction == "LONG" \
               else ("pdl", "pwl", "pml", "lo_52w")
        for k in keys:
            v = lv.get(k)
            if v and ((direction == "LONG" and v > lp) or (direction == "SHORT" and v < lp)):
                cands.append(v)
        for tl in lv.get("trendlines", []):
            v = tl["value"]
            if (direction == "LONG" and tl["kind"] == "res" and v > lp) or \
               (direction == "SHORT" and tl["kind"] == "sup" and v < lp):
                cands.append(v)
        if not cands:
            return lp * (1 + (config.T2_PCT + 2) / 100) if direction == "LONG" \
                   else lp * (1 - (config.T2_PCT + 2) / 100)
        return min(cands) if direction == "LONG" else max(cands)

    def _structure_behind(self, lv, sl, lp, direction):
        span = abs(lp - sl) * 1.5
        keys = ("pdl", "pwl", "pml", "avwap_earnings", "avwap_base", "ema20") \
               if direction == "LONG" else ("pdh", "pwh", "pmh")
        for k in keys:
            v = lv.get(k)
            if v and abs(lp - v) <= span and \
               ((direction == "LONG" and v <= lp) or (direction == "SHORT" and v >= lp)):
                return True
        for tl in lv.get("trendlines", []):
            if abs(lp - tl["value"]) <= span:
                return True
        return False

    # ---------------------------------------------------------- alert
    def _fire(self, st, tok, direction, g):
        lp = st.last_price
        sl = g["sl"]
        sign = 1 if direction == "LONG" else -1
        vwap = st.pv / st.vq
        entry_lo = min(lp, vwap if sign == 1 else lp)
        entry_hi = max(lp, vwap if sign == -1 else lp)
        t1 = lp * (1 + sign * config.T1_PCT / 100)
        t2 = lp * (1 + sign * config.T2_PCT / 100)
        dtp = self.day_type.get(tok, "NORMAL")
        hold_t2 = g["rr"] >= config.RR_HOLD_T2 and dtp == "TREND"
        tag = "⚠SC " if g.get("sc_flag") else ""
        msg = (f"{'🟢' if sign==1 else '🔴'} [STOCK]{' [PAPER]' if config.PAPER_MODE else ''} "
               f"{st.meta['symbol']} FUT | {direction} | {g['trigger']}\n"
               f"Entry: {entry_lo:.1f}–{entry_hi:.1f}\n"
               f"SL: {sl:.1f} (no widening, no averaging)\n"
               f"T1: {t1:.1f} ({config.T1_PCT}%) | "
               f"T2: {t2:.1f} ({config.T2_PCT}%){' — HOLD' if hold_t2 else ' — book T1'}\n"
               f"{tag}RVOL {g['rvol']} | OI {g['oi']} | Breadth {g.get('breadth','?')}%\n"
               f"RS {g['rs']} | RR {g['rr']}:1 | "
               f"Day: {dtp}\nLot {st.meta['lot']} | Hard exit {config.HARD_EXIT_TIME}")
        send(msg)
        paper_log.open_trade(st.meta["symbol"], direction, lp, sl, t1, t2, dtp, g)
        st.alerted = True
        self.alerts_today += 1

    def on_sl_hit(self):
        with self.lock:
            self.sl_hits += 1
            if self.sl_hits >= config.KILL_SWITCH_SL_COUNT and not self.muted:
                self.muted = True
                send("🛑 [STOCK] Kill-switch: 2 SL hits. Scanner muted till tomorrow.")

    # ---------------------------------------------------------- run
    def run(self):
        if not self.load_watchlist():
            return
        kws = KiteTicker(config.KITE_API_KEY, config.KITE_ACCESS_TOKEN,
                         reconnect=True, reconnect_max_tries=50,
                         reconnect_max_delay=30)
        tokens = list(set(list(self.states.keys()) + list(self.fut_map.keys())
                  + list(self.index_tokens.keys()) + list(self.breadth_prev.keys())))
        def on_connect(ws, resp):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)
            log.info(f"ws connected: {len(tokens)} tokens FULL mode")
        def on_close(ws, code, reason):
            log.warning(f"ws closed: {code} {reason}")
        kws.on_ticks = self.on_ticks
        kws.on_connect = on_connect
        kws.on_close = on_close
        paper_log.engine_ref = self
        kws.connect(threaded=True)
        # save RVOL baseline at close
        import time
        while True:
            now = dt.datetime.now(IST)
            if now.strftime("%H:%M") >= "15:31":
                self._save_rvol_baseline()
                kws.close()
                break
            time.sleep(30)

    def _save_rvol_baseline(self):
        """Bootstrap accumulation: fold today's per-slot cumulative volume into
        the rolling baseline."""
        rows = []
        for st in self.states.values():
            cum = 0
            for i, b in enumerate(st.bars):
                cum += b["v"]
                with db.engine.begin() as cx:
                    r = cx.exec_driver_sql(
                        "SELECT avg_cum_vol, days FROM rvol_baseline "
                        f"WHERE symbol='{st.meta['symbol']}' AND slot={i}").fetchone()
                if r:
                    days = min(r[1] + 1, config.RVOL_LOOKBACK_DAYS)
                    avg = r[0] + (cum - r[0]) / days
                else:
                    days, avg = 1, cum
                rows.append(dict(symbol=st.meta["symbol"], slot=i,
                                 avg_cum_vol=avg, days=days))
        db.upsert(db.rvol_baseline, rows, keys=["symbol", "slot"])
        log.info("rvol baseline updated")

def run():
    LiveEngine().run()
