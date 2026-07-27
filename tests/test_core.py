import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.kambi_client import parse_kambi
from app.odds_client import OddsOutcome
from app.matcher import match, american_to_prob, american_to_decimal


def load_sample():
    p = os.path.join(os.path.dirname(__file__), "kambi_sample.json")
    with open(p) as f:
        return json.load(f)


def test_kambi_parses_full_game_only():
    raw = load_sample()
    outcomes = parse_kambi(raw, "MLB")
    labels = {(o.market_type, o.side, o.line) for o in outcomes}
    # The injected First-5 total (line 4.5) must NOT appear.
    assert not any(o.line == 4.5 for o in outcomes), "First-5 market leaked through!"
    # Full-game total 9.5 for ARI@PIT must appear.
    assert any(o.market_type == "total" and o.line == 9.5 for o in outcomes)
    # Moneyline present with full team names.
    ml = [o for o in outcomes if o.market_type == "moneyline"]
    assert any(o.side == "pittsburgh pirates" for o in ml)
    print(f"  parsed {len(outcomes)} full-game outcomes, First-5 correctly excluded")


def test_odds_conversion():
    assert abs(american_to_prob(-110) - 0.5238) < 0.001
    assert abs(american_to_prob(150) - 0.4) < 0.001
    assert abs(american_to_decimal(-200) - 1.5) < 0.001
    assert abs(american_to_decimal(150) - 2.5) < 0.001
    print("  american odds conversions correct")


def test_full_game_teams_resolved():
    raw = load_sample()
    outcomes = parse_kambi(raw, "MLB")
    # englishName gives full names; away/home should be full english.
    ari = [o for o in outcomes if o.event_id == 1024787930][0]
    assert ari.home_team == "Pittsburgh Pirates"
    assert ari.away_team == "Arizona Diamondbacks"
    print("  full team names resolved from englishName")


def test_match_produces_ev_and_quarantines_extreme():
    raw = load_sample()
    kambi = parse_kambi(raw, "MLB")
    start = datetime(2026, 7, 27, 22, 40, tzinfo=timezone.utc)

    # Build synthetic sharp lines for ARI@PIT moneyline.
    # Make sharp books imply PIT ~ -130 (fair ~0.55), so Kambi's -113
    # (decimal 1.885) looks like a small +EV. And create an EXTREME fake:
    # sharp books strongly favor UNDER 9.5 but Kambi Over 9.5 at -105.
    odds = []
    for book in ["pinnacle", "betonlineag", "draftkings", "fanduel", "betmgm"]:
        # Moneyline: PIT -135, ARI +125  (two-way, devig -> PIT ~0.556)
        odds.append(OddsOutcome("Arizona Diamondbacks", "Pittsburgh Pirates", start,
                                book, "moneyline", "pittsburgh pirates", None, -135))
        odds.append(OddsOutcome("Arizona Diamondbacks", "Pittsburgh Pirates", start,
                                book, "moneyline", "arizona diamondbacks", None, 125))
        # Total 9.5: sharp Over +140 / Under -160 -> Over fair ~0.42
        odds.append(OddsOutcome("Arizona Diamondbacks", "Pittsburgh Pirates", start,
                                book, "total", "over", 9.5, 140))
        odds.append(OddsOutcome("Arizona Diamondbacks", "Pittsburgh Pirates", start,
                                book, "total", "under", 9.5, -160))

    signals = match(kambi, odds, min_sources=3)
    assert signals, "expected at least one signal"

    # Kambi Over 9.5 at -105 vs sharp fair ~0.42 -> big NEGATIVE ev, not a bet.
    # Kambi PIT ML -113 vs fair ~0.556 -> small positive ev.
    pit_ml = [s for s in signals if s.market_type == "moneyline"
              and s.side == "pittsburgh pirates"]
    assert pit_ml, "expected PIT moneyline signal"
    print(f"  PIT ML: fair={pit_ml[0].fair_prob}, EV={pit_ml[0].ev_pct}%, "
          f"verdict={pit_ml[0].verdict}")

    # Now flip to manufacture an EXTREME with disagreement -> must QUARANTINE.
    odds2 = []
    disagree_lines = [-105, 140, -105, 140, -105]  # wildly inconsistent
    for book, aml in zip(["pinnacle", "betonlineag", "draftkings"], disagree_lines):
        odds2.append(OddsOutcome("Arizona Diamondbacks", "Pittsburgh Pirates", start,
                                 book, "moneyline", "pittsburgh pirates", None, aml))
        odds2.append(OddsOutcome("Arizona Diamondbacks", "Pittsburgh Pirates", start,
                                 book, "moneyline", "arizona diamondbacks", None, -aml if aml > 0 else abs(aml)))
    sigs2 = match(kambi, odds2, min_sources=3)
    extreme = [s for s in sigs2 if s.ev_pct >= 12.0]
    for s in extreme:
        assert s.verdict == "QUARANTINE", f"extreme with disagreement should quarantine, got {s.verdict}"
    print(f"  extreme signals with book disagreement correctly quarantined ({len(extreme)})")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"[test] {name}")
            fn()
    print("\nALL TESTS PASSED")
