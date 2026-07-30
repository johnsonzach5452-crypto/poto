# Potawatomi Line Scanner

Compares Potawatomi (Kambi BYOD) lines against a no-vig sharp-book consensus
and flags where they diverge — for game lines and pitcher props.

## What it does

- **Game lines**: moneyline, run line, and total, pulled from the Potawatomi
  feed and compared against sharp books via The Odds API.
- **Pitcher props**: strikeouts and outs, compared against sharp books via
  **PropLine** (free tier, includes a Pinnacle anchor) or The Odds API.

For every comparison it matches strictly (same game, same market, same side,
same number), de-vigs the sharp books to a fair price, and ranks Kambi's edge.
Full-game-only filtering excludes First-5-Innings / period markets; the prop
side excludes the "N+" strikeout ladder and DFS pick'em pricing. Player names
are matched on last name + first initial, so "Matt" and "Matthew" line up.

## What it does NOT do

Place bets, or promise a flagged edge is real. **The scanner surfaces
candidates; you confirm the exact ticket at the kiosk before betting.** A large
apparent edge is far more often a data artifact (stale line, wrong period,
about-to-suspend prop) than genuine value — big edges are quarantined, not
presented as bets.

## Setup (Railway)

1. Push these files to a private GitHub repo.
2. Railway → New Project → Deploy from GitHub repo → pick it.
3. In **Variables**, add the keys you'll use:
   - `ODDS_API_KEY` — for game lines (and props via The Odds API)
   - `PROPLINE_API_KEY` — for props via PropLine (free at prop-line.com)
4. Settings → Networking → **Generate Domain**, open the URL.

Run locally instead: `pip install -r requirements.txt && streamlit run dashboard.py`.

## Using it

- Pick **Game lines** or **Pitcher props**, and a league.
- Props: choose **PropLine (free)** as the source; it carries Pinnacle + exchanges.
- The sidebar only asks for the key the current mode needs.
- **Scan 30–90 minutes before first pitch.** The diagnostics show a
  "starts in …" time per game and a "Scannable now" count; games that are LIVE
  have pulled their props, and games hours out haven't posted them yet.
- When a signal appears, confirm the exact market on the kiosk before betting.
  Quarantined signals are likely artifacts — don't bet them on their own.

## Timing is everything

The lines most likely to look wildly mispriced are the ones most likely to be
stale or about to suspend — i.e. the ones that won't be there (or won't be
honored) by the time you scan the ticket. Treat the "extreme" bucket with more
suspicion than the modest one, not less. Paper-trade against closing lines
before sizing up.

## Tests

```
python tests/test_core.py
python tests/test_props.py
```

Verify parsing against real captured payloads, First-5 / ladder exclusion,
odds math, cross-feed name matching (Matt↔Matthew), and quarantine of
disagreeing extremes.

## Diagnose the Kambi connection

```
python diagnose_kambi.py --league MLB
```
