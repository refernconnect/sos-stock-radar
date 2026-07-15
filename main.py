"""sos-stock-radar :: main
Railway worker entry. IST schedule:
  Sun 10:00  universe build
  Mon-Fri 08:40  ban list + regime refresh happens inside module_a
  Mon-Fri 08:45  Module A digest -> watchlist
  Mon-Fri 09:10  Module B live engine (runs till 15:31)
  Mon-Fri 15:45  expectancy report
  Mon-Fri 18:30  Module C structure engine (appends today's bar, rebuilds levels)
"""
import logging
import threading
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

import config, db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

def _universe():
    import universe
    universe.build()

def _module_a():
    import module_a_premarket
    module_a_premarket.run()

def _module_b():
    import module_b_live
    threading.Thread(target=module_b_live.run, daemon=True).start()

def _module_c():
    import module_c_structure
    module_c_structure.run()

def _report():
    import paper_log
    paper_log.daily_report()

def _fvg_cpr():
    import fvg_cpr_scanner
    fvg_cpr_scanner.run()


if __name__ == "__main__":
    db.init()
    tz = pytz.timezone(config.TZ)
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(_universe, "cron", day_of_week="sun", hour=10, minute=0)
    sched.add_job(_module_a, "cron", day_of_week="mon-fri", hour=8, minute=45)
    sched.add_job(_module_b, "cron", day_of_week="mon-fri", hour=9, minute=10)
    sched.add_job(_fvg_cpr, "cron", day_of_week="mon-fri", hour=9, minute=35)
    sched.add_job(_fvg_cpr, "cron", day_of_week="mon-fri", hour=14, minute=30)
    sched.add_job(_report,   "cron", day_of_week="mon-fri", hour=15, minute=45)
    sched.add_job(_module_c, "cron", day_of_week="mon-fri", hour=18, minute=30)
    log.info("sos-stock-radar scheduler started (IST)")
    sched.start()
