# SOS STOCK RADAR v1.3 — Deploy Guide

Railway worker. Same pattern as sos-kite/sos-oi-dashboard. ~45 min total.

---

## STEP 1 — GitHub repo (5 min)
1. github.com → New repository → `sos-stock-radar` (private)
2. Upload ALL files from this package (drag-drop on repo page → Commit)

## STEP 2 — Railway service (5 min)
1. Railway → New Project → Deploy from GitHub repo → select `sos-stock-radar`
2. It detects Python + Procfile automatically. Service type: worker (no domain needed)
3. OPTIONAL but recommended: add the Postgres plugin (Railway → New → Database
   → PostgreSQL). It injects DATABASE_URL automatically. Without it, SQLite is
   used — which WIPES on redeploy. Postgres = data survives. Use Postgres.

## STEP 3 — Environment variables (5 min)
Railway → service → Variables → add from `.env.example`:
- KITE_API_KEY, KITE_ACCESS_TOKEN — same values as sos-kite
- TG_BOT_TOKEN, TG_CHAT_ID — reuse existing bot; recommend a NEW group/channel
  for stock alerts so index and stock streams stay separate. Get chat_id the
  same way as before (@userinfobot or getUpdates)
- PAPER_MODE=true  ← do not change until Step 8 criteria met
- Leave all thresholds at defaults

## STEP 4 — Kite token daily routine (already known)
KITE_ACCESS_TOKEN expires nightly. Add to your existing morning token flow:
update the Railway variable before 08:45 (digest) — same token as sos-kite,
so one refresh covers both services. If token is stale, module_a fails and
logs it; digest simply won't arrive — that's your signal.

## STEP 5 — First run sequence (one-time, ~40 min mostly unattended)
Open Railway → service → Settings → temporarily change Start Command to run
each of these once (or use Railway shell if available):

1. Build universe:
   python -c "import db; db.init(); import universe; universe.build()"
   → logs should show "universe built: ~40 names"

2. Backfill 400 days of daily candles from NSE bhavcopies (free, no Kite
   historical needed; takes 30–40 min):
   python -c "import db; db.init(); import module_c_structure as m; m.backfill_daily(400)"

3. Build first levels table:
   python -c "import db; db.init(); import module_c_structure as m; m.run()"

4. Restore Start Command to: python main.py → redeploy. Scheduler takes over.

## STEP 6 — VERIFY checklist (before trusting anything)
- [ ] 08:45 digest arrives on Telegram with regime line + names
- [ ] Ban list line: if digest says "Ban list fetch empty — verify manually",
      check nseindia.com ban list by hand that day
- [ ] NSE URLs: if backfill returned 0 rows, NSE changed bhavcopy format —
      all URLs are isolated in nse_data.py, send me the error log
- [ ] Cross-check digest vs chartink_scans.md (Scans 1–5) for 3 mornings
- [ ] Sector warnings in logs: send me "unmapped sectors" list, I extend sectors.py
- [ ] 09:10 log line "ws connected: N tokens FULL mode"
- [ ] RVOL shows "BOOT" in early alerts — expected. Baseline needs 5 sessions
      of live capture before the RVOL gate arms (or set KITE_HISTORICAL=true
      if you activate the ₹2,000/mo Kite historical add-on — then I ship the
      historical warm-start patch and the gate arms day one)

## STEP 7 — Paper phase (15 sessions minimum, non-negotiable)
- Alerts arrive tagged [PAPER]; every one is logged with outcome
- 15:45 daily expectancy report: trades, win rate, avg R, total R
- Do NOT trade these alerts with money yet. The RenTec rule applies:
  gates that don't earn their place get deleted based on this log

## STEP 8 — Go-live criteria (all three, no exceptions)
1. ≥15 sessions of paper data
2. Positive expectancy (avg R > 0) across ≥10 closed paper trades
3. RVOL baseline armed (no more "BOOT" in alerts)
Then: PAPER_MODE=false. Alerts lose the [PAPER] tag. Execution stays manual —
this system alerts, you fire. Auto-execution is a separate decision, later.

---

## Architecture recap
- module_a_premarket: 08:45 digest — hard filters (ban, 12-week momentum pool,
  regime) + scoring (results/volume/OI/delivery/structure/SQZ)
- module_c_structure: 18:30 nightly — levels, trendlines (objective, 3+ touch),
  bases, VCP, NR7/inside, anchored VWAPs, retest flags
- module_b_live: 09:10–15:31 — 9-gate stack, DT-sweep + ORB-retest triggers,
  kill-switch after 2 SL, max 3 alerts/day, hard exit 14:45
- paper_log: every alert tracked to SL/T1/T2/TIME; T1 hit moves stop to
  breakeven; daily expectancy report

## Standing rules (baked in, not advisory)
- No SL widening, no averaging — the alert says so and the tracker enforces it
- Two SL hits = scanner mutes till next session
- No alerts before 09:45 (observation window) or after 14:00
- Negative expectancy in paper = we fix gates, not hopes
