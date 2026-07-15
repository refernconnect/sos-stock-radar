"""sos-stock-radar :: paper_log
Every alert becomes a logged trade (paper or live-shadow). Outcomes tracked
tick-by-tick: SL / T1 / T2 / TIME (hard exit). Expectancy report at 15:45,
now including the v1.5 misses audit (day's top movers vs what we did).
This log is the only scoreboard — gates live or die by it.
"""
import json
import logging
import datetime as dt
import pytz

import config, db
from telegram_alert import send

log = logging.getLogger("paper")
IST = pytz.timezone(config.TZ)

engine_ref = None          # set by module_b for kill-switch callback
_open: dict[str, dict] = {}

def open_trade(symbol, direction, entry, sl, t1, t2, day_type, gates):
    row = dict(ts=dt.datetime.now(IST).replace(tzinfo=None), symbol=symbol,
               direction=direction, entry_lo=entry, entry_hi=entry, sl=sl,
               t1=t1, t2=t2, day_type=day_type, gates=json.dumps(gates, default=str),
               outcome="OPEN", exit_price=None, r_multiple=None,
               paper=1 if config.PAPER_MODE else 0)
    with db.engine.begin() as cx:
        cx.execute(db.alerts.insert().values(**row))
    _open[symbol] = {**row, "t1_done": False}

def monitor(prices: dict):
    """Called from module_b on every tick batch with {symbol: last_price}."""
    now = dt.datetime.now(IST).strftime("%H:%M")
    for sym in list(_open.keys()):
        tr = _open[sym]
        lp = prices.get(sym)
        if lp is None:
            continue
        sign = 1 if tr["direction"] == "LONG" else -1
        risk = abs(tr["entry_lo"] - tr["sl"]) or 1e-9
        if sign * (lp - tr["sl"]) <= 0:
            _close(sym, "SL", lp, -1.0)
            if engine_ref:
                engine_ref.on_sl_hit()
        elif sign * (lp - tr["t2"]) >= 0:
            _close(sym, "T2", lp, sign * (lp - tr["entry_lo"]) / risk)
        elif not tr["t1_done"] and sign * (lp - tr["t1"]) >= 0:
            tr["t1_done"] = True
            tr["sl"] = tr["entry_lo"]           # move stop to breakeven at T1
        elif now >= config.HARD_EXIT_TIME:
            _close(sym, "TIME", lp, sign * (lp - tr["entry_lo"]) / risk)

def _close(sym, outcome, price, r):
    tr = _open.pop(sym, None)
    if not tr:
        return
    with db.engine.begin() as cx:
        cx.exec_driver_sql(
            "UPDATE alerts SET outcome=:o, exit_price=:p, r_multiple=:r "
            "WHERE symbol=:s AND outcome='OPEN'",
            {"o": outcome, "p": price, "r": round(r, 2), "s": sym})
    send(f"📕 [STOCK]{' [PAPER]' if config.PAPER_MODE else ''} {sym} closed: "
         f"{outcome} @ {price:.1f} ({r:+.1f}R)")

def daily_report():
    with db.engine.begin() as cx:
        rows = cx.exec_driver_sql(
            "SELECT outcome, r_multiple FROM alerts "
            "WHERE r_multiple IS NOT NULL").fetchall()
    if not rows:
        send("📊 [STOCK] Expectancy: no closed trades yet.")
    else:
        rs = [r[1] for r in rows]
        wins = [r for r in rs if r > 0]
        n = len(rs)
        exp = sum(rs) / n
        send(f"📊 [STOCK] Expectancy report\n"
             f"Trades: {n} | Win rate: {len(wins)/n*100:.0f}%\n"
             f"Avg R: {exp:+.2f} | Best {max(rs):+.1f}R | Worst {min(rs):+.1f}R\n"
             f"Total: {sum(rs):+.1f}R\n"
             f"{'✅ Positive expectancy' if exp > 0 else '⚠ Negative — do not go live'}")
    _misses_report()

def _misses_report():
    """v1.5: day's top universe movers vs what the system watched/alerted."""
    m = db.get_state("day_movers")
    if not m or m.get("date") != dt.date.today().isoformat():
        return
    watch = set(m.get("watch", []))
    alerted = set(m.get("alerted", []))
    lines = ["🔍 [STOCK] Misses audit — top movers vs system:"]
    for sym, pct in (m.get("up", []) + m.get("down", [])):
        if sym in alerted:
            status = "✅ ALERTED"
        elif sym in watch:
            status = "👁 watched, gates never aligned"
        else:
            status = "scan pool (armed 10:00+), gates never aligned"
        lines.append(f"{sym} {pct:+.1f}% — {status}")
    lines.append("Pattern to watch: repeated big movers stuck at one gate = "
                 "that gate needs review after 10 sessions of data.")
    send("\n".join(lines))
