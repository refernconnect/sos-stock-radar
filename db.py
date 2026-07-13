"""sos-stock-radar :: db
SQLAlchemy Core. Uses Railway Postgres when DATABASE_URL is set, else local SQLite.
"""
import json
import datetime as dt
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, Float,
                        String, Text, Date, DateTime, UniqueConstraint, text)
import config

_url = config.DATABASE_URL or "sqlite:///radar.db"
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)
engine = create_engine(_url, pool_pre_ping=True)
meta = MetaData()

universe = Table("universe", meta,
    Column("symbol", String(32), primary_key=True),
    Column("spot_token", Integer),
    Column("fut_token", Integer),
    Column("fut_symbol", String(48)),
    Column("lot_size", Integer),
    Column("avg_turnover_cr", Float),
    Column("sector", String(32)),
    Column("updated", DateTime),
)

daily = Table("daily", meta,
    Column("symbol", String(32)),
    Column("d", Date),
    Column("o", Float), Column("h", Float), Column("l", Float),
    Column("c", Float), Column("v", Float),
    Column("delivery_pct", Float),
    Column("fut_oi", Float),
    UniqueConstraint("symbol", "d", name="uq_daily"),
)

levels = Table("levels", meta,
    Column("symbol", String(32)),
    Column("d", Date),
    Column("payload", Text),                     # JSON from module_c
    UniqueConstraint("symbol", "d", name="uq_levels"),
)

rvol_baseline = Table("rvol_baseline", meta,
    Column("symbol", String(32)),
    Column("slot", Integer),                     # 5-min slot index from 09:15 (0..74)
    Column("avg_cum_vol", Float),
    Column("days", Integer),
    UniqueConstraint("symbol", "slot", name="uq_rvol"),
)

watchlist = Table("watchlist", meta,
    Column("d", Date),
    Column("payload", Text),                     # JSON digest from module_a
    UniqueConstraint("d", name="uq_watch"),
)

alerts = Table("alerts", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime),
    Column("symbol", String(32)),
    Column("direction", String(8)),
    Column("entry_lo", Float), Column("entry_hi", Float),
    Column("sl", Float), Column("t1", Float), Column("t2", Float),
    Column("day_type", String(16)),
    Column("gates", Text),                       # JSON gate values at fire time
    Column("outcome", String(16)),               # OPEN/T1/T2/SL/TIME
    Column("exit_price", Float),
    Column("r_multiple", Float),
    Column("paper", Integer),
)

state = Table("state", meta,
    Column("k", String(64), primary_key=True),
    Column("v", Text),
)

def init():
    meta.create_all(engine)

def set_state(k, v):
    with engine.begin() as cx:
        cx.execute(text("DELETE FROM state WHERE k=:k"), {"k": k})
        cx.execute(state.insert().values(k=k, v=json.dumps(v)))

def get_state(k, default=None):
    with engine.begin() as cx:
        r = cx.execute(text("SELECT v FROM state WHERE k=:k"), {"k": k}).fetchone()
    return json.loads(r[0]) if r else default

def upsert(table, rows, keys):
    """Naive upsert portable across SQLite/Postgres: delete matching keys then insert."""
    if not rows:
        return
    with engine.begin() as cx:
        for r in rows:
            cond = " AND ".join(f"{k}=:{k}" for k in keys)
            cx.execute(text(f"DELETE FROM {table.name} WHERE {cond}"),
                       {k: r[k] for k in keys})
        cx.execute(table.insert(), rows)

def now_ist():
    import pytz
    return dt.datetime.now(pytz.timezone(config.TZ))
