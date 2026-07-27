"""
diagnose_kambi.py -- confirm the Potawatomi feed loads server-side and show
what came back. Run this FIRST, before trusting the dashboard.

    python diagnose_kambi.py --league MLB
    python diagnose_kambi.py --league WNBA
"""
import argparse
import sys

from app.kambi_client import fetch_kambi, parse_kambi, KAMBI_HOST


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="MLB", choices=["MLB", "WNBA"])
    args = ap.parse_args()

    print("=" * 64)
    print(f"Kambi diagnostic | host={KAMBI_HOST} | league={args.league}")
    print("=" * 64)
    try:
        raw = fetch_kambi(args.league)
    except Exception as e:
        print(f"FETCH FAILED: {type(e).__name__}: {e}")
        print("\nIf 403/connection error: the feed is likely gated to the browser")
        print("session. A plain server request won't work; you'd need to capture")
        print("the JSON from the page's Network tab (see README).")
        sys.exit(1)

    n_events = len(raw.get("events", []))
    outcomes = parse_kambi(raw, args.league)
    print(f"events in feed: {n_events}")
    print(f"full-game outcomes parsed (first-5/half excluded): {len(outcomes)}")
    print("\nsample:")
    for o in outcomes[:12]:
        line = "" if o.line is None else f" {o.line:+g}"
        print(f"  {o.away_team} @ {o.home_team} | {o.market_type} "
              f"{o.side}{line} | {o.american:+d}")
    if not outcomes:
        print("  (none) -- feed loaded but no full-game markets parsed. Check labels.")
    print("\nNext: open the kiosk app and confirm these teams/lines/odds match.")


if __name__ == "__main__":
    main()
