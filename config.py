"""sos-stock-radar :: config
All tunables live here. Values load from environment (Railway) with .env fallback.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- credentials ---
KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")
KITE_HISTORICAL = os.getenv("KITE_HISTORICAL", "false").lower() == "true"
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- mode ---
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"

# --- universe / watchlist ---
UNIVERSE_SIZE = int(os.getenv("UNIVERSE_SIZE", 40))
WATCHLIST_SIZE = int(os.getenv("WATCHLIST_SIZE", 8))

# --- gates (v1.3) ---
RVOL_MIN = float(os.getenv("RVOL_MIN", 2.5))
OI_DELTA_MIN = float(os.getenv("OI_DELTA_MIN", 8))          # % futures OI change, 15m window
MAX_ALERTS_PER_DAY = int(os.getenv("MAX_ALERTS_PER_DAY", 3))
MOMENTUM_12W_MIN = float(os.getenv("MOMENTUM_12W_MIN", 30))
MOMENTUM_12W_SHORT = float(os.getenv("MOMENTUM_12W_SHORT", -20))
RR_MIN = float(os.getenv("RR_MIN", 3.0))
RR_HOLD_T2 = float(os.getenv("RR_HOLD_T2", 5.0))
ADR_STOP_MAX_PCT = float(os.getenv("ADR_STOP_MAX_PCT", 120))
GAP_SKIP_PCT = float(os.getenv("GAP_SKIP_PCT", 5.0))
GAP_HOLD_PCT = 1.0            # gaps above this must hold midpoint
RS_SECTOR_MIN = 1.5           # stock move must be >=1.5x sector move
KILL_SWITCH_SL_COUNT = int(os.getenv("KILL_SWITCH_SL_COUNT", 2))

# --- session clock (IST) ---
TZ = "Asia/Kolkata"
MARKET_OPEN = "09:15"
OBSERVE_UNTIL = "09:45"       # no entries before day-type classification
NO_NEW_ALERTS_AFTER = os.getenv("NO_NEW_ALERTS_AFTER", "14:00")
HARD_EXIT_TIME = os.getenv("HARD_EXIT_TIME", "14:45")

# --- targets ---
T1_PCT = 2.0
T2_PCT = 4.0

# --- structure engine ---
BASE_MIN_SESSIONS = 20        # base must be >= 20 sessions
BASE_MAX_RANGE_PCT = 8.0      # base high-low range <= 8%
TRENDLINE_MIN_TOUCHES = 3
TRENDLINE_TOL_PCT = 0.5
DAILY_LOOKBACK = 400          # daily candles kept per symbol

# --- RVOL baseline ---
RVOL_LOOKBACK_DAYS = 20
RVOL_MIN_BASELINE_DAYS = 5    # bootstrap mode: suppress RVOL gate until this many days captured

# --- sector move ranking ---
SECTOR_TOP_N = 2              # top/bottom sectors considered "in play"

# --- market internals (Cook-style breadth, TICK proxy for India) ---
BREADTH_MIN_LONG = 50.0       # % of universe above prev close required for longs
BREADTH_MAX_SHORT = 50.0      # % must be at/below this for shorts

SOS_KITE_URL = os.getenv("SOS_KITE_URL", "")
TOKEN_SHARE_SECRET = os.getenv("TOKEN_SHARE_SECRET", "")
