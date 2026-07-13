# Chartink Verification Scans (paper-phase cross-check only)

Purpose: every morning after the 08:45 digest, run these on chartink.com and
compare. If Chartink shows a strong name the digest missed, note it — 3+ misses
in a week means a Module A threshold needs review. Chartink is NOT in the
pipeline: no API, delayed intraday data. This is an eyeball mirror.

## Scan 1 — Long pool (momentum + compression)
Segment: Futures & Options
Clause:
( {cash} ( latest close > 1 month ago close * 1.08
and latest close > 3 month ago close * 1.30
and latest close > latest ema( latest close , 20 )
and latest "close - 1 candle ago close / 1 candle ago close * 100" > -2
and latest volume > latest sma( latest volume , 20 ) ) )

## Scan 2 — Prev-day volume surge (VOL2X tag mirror)
( {cash} ( 1 day ago volume > 2 * latest sma( latest volume , 20 )
and latest close > 1 day ago close ) )

## Scan 3 — Near 52-week high / base breakout zone
( {cash} ( latest close >= latest max( 250 , latest high ) * 0.99
and latest close > 3 month ago close * 1.30 ) )

## Scan 4 — Short pool mirror
( {cash} ( latest close < 3 month ago close * 0.80
and latest close < latest ema( latest close , 20 )
and 1 day ago volume > 1.5 * latest sma( latest volume , 20 ) ) )

## Scan 5 — NR7 / inside-day coil on F&O names
( {cash} ( latest "high - low" <= latest min( 7 , latest "high - low" )
and latest close > 3 month ago close * 1.30 ) )

Daily routine: digest arrives 08:45 → open Chartink → run Scans 1–5 →
symbols overlap should be high on the top names. Log any strong-looking
Chartink name absent from the digest, with reason found later.
