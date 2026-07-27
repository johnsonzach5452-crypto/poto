# Potawatomi Line Scanner

Compares full-game lines from the Potawatomi (Kambi BYOD) feed against sharp
sportsbook lines from The Odds API, and flags where they diverge.

## What it does and does not do

**Does:** pull full-game Moneyline / Run Line (spread) / Total markets from the
Kambi feed, pull the same markets from your chosen books via The Odds API, match
them strictly (same game, same market, same side, same number), de-vig the sharp
books to a fair price, and rank Kambi's edge.

**Does not:** compare a full-game line to a First-5-Innings or First-Half line
(these are filtered out — they are the main source of fake edges), place bets, or
promise that a flagged edge is real.

**The scanner surfaces candidates. You confirm the exact ticket at the kiosk
before betting.** A large apparent edge is far more often a data mismatch (stale
price, wrong period, suspended market) than genuine value. Size small and keep a
closing-line record before trusting any of it.

## Step 1 — Confirm the feed actually loads (do this first)

The Kambi feed may be gated to the browser session. Before anything else:

```
pip install -r requirements.txt
python diagnose_kambi.py --league MLB
```

- If it prints events with teams/lines/odds: good. Open the kiosk app on your
  phone and confirm a few of them match exactly (same teams, same total, same
  odds, full game). If they don't match, stop — matching downstream is
  meaningless until they do.
- If it prints **403 / connection failed**: a plain server-side request is being
  blocked. The dashboard's server-side fetch will fail too. See "Browser-capture
  fallback" below.

## Step 2 — Run locally

```
cp .env.example .env      # then put your real Odds API key in .env
streamlit run dashboard.py
```

Enter your key in the sidebar (or via the `ODDS_API_KEY` env var), pick a league,
press **Scan now**.

## Step 3 — Deploy to GitHub + Railway

1. Create a **private** GitHub repo (private because it's your personal tool).
   Do **not** upload a `.env` file — the `.gitignore` already excludes it.
2. Push these files to the repo.
3. In Railway: **New Project → Deploy from GitHub repo →** pick the repo.
4. Railway auto-detects the config and runs the Streamlit start command.
5. In the service **Variables** tab, add:
   - `ODDS_API_KEY` = your real key
   - optionally `ODDS_API_BOOKMAKERS`, `MIN_SOURCES`, `MIN_EV_MAIN_PCT`
6. **Settings → Networking → Generate Domain** to get a URL.

Updates: commit to `main`, Railway redeploys automatically.

### Important: server-side fetch may be blocked

Railway requests to Kambi come from a datacenter IP and may be 403'd or
geo-blocked even if the feed works from your home browser. If the dashboard shows
a Kambi fetch error, that's this. There is no clean fix from a server — the
honest options are to run the scanner locally on your own network, or use the
browser-capture fallback below.

## Browser-capture fallback (when server fetch is blocked)

If `diagnose_kambi.py` can't reach the feed but your browser can:

1. Open the BYOD page in Chrome, open DevTools → Network → Fetch/XHR.
2. Reload, click into MLB (or WNBA).
3. Find the `matches.json?...` request, right-click → Save response, and place it
   at `data/kambi_capture.json`.
4. The parser can read that file directly (it's the same structure as the live
   feed). This is manual and goes stale the moment lines move — it's a diagnostic
   aid, not a live pipeline.

## Tests

```
python tests/test_core.py
```

These verify parsing against a real captured payload, confirm First-5 markets are
excluded, check the odds math, and check that extreme edges with book
disagreement get quarantined rather than shown as bets.

## Reality check

Books running on Kambi move and suspend lines fast. The lines most likely to look
wildly mispriced are the ones most likely to be stale or about to suspend — i.e.
the ones that won't be there (or won't be honored) by the time you scan the
ticket. Treat the "extreme" bucket with more suspicion than the modest one, not
less.
