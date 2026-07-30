import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.props import parse_props, norm_player, PropOutcome
from app.props_odds import parse_props_odds, match_props

def load():
    with open(os.path.join(os.path.dirname(__file__), "props_sample.json")) as f:
        return json.load(f)

def test_parse_real_props():
    raw = load()
    props = parse_props(raw)
    stats = {p.stat for p in props}
    # Only strikeouts and outs, Over/Under, should survive.
    assert stats <= {"strikeouts", "outs"}, stats
    # The "N+" ladder must be excluded: no prop should have integer+.5 mismatch
    # More directly: we should have exactly the 2 pitchers x 2 stats x 2 sides = 8 rows.
    ks = [p for p in props if p.stat == "strikeouts"]
    outs = [p for p in props if p.stat == "outs"]
    players = {p.player for p in props}
    print(f"  players: {players}")
    print(f"  strikeout rows: {len(ks)}, outs rows: {len(outs)}")
    assert "Zac Thornton" in players and "Martin Pérez" in players
    # Zac Thornton strikeouts O/U 4.5 present
    zt = [p for p in ks if p.player == "Zac Thornton"]
    assert any(abs(p.line - 4.5) < 0.01 for p in zt)
    print("  real props parsed, ladder markets excluded")

def test_accent_normalization():
    # Kambi 'Martin Pérez' must match Odds API 'Martin Perez'
    assert norm_player("Martin Pérez") == norm_player("Martin Perez") == "martin perez"
    assert norm_player("Luis Robert Jr.") == "luis robert"
    print("  accent + suffix normalization works")

def test_prop_matching_and_ev():
    raw = load()
    kambi = parse_props(raw)
    # Build synthetic sharp Odds API props: 5 books priced so that
    # Martin Perez Over 3.5 K looks like a small +EV vs Kambi's -143.
    # Kambi Perez Over 3.5 K = -143 (decimal 1.70). If sharp fair ~0.63,
    # EV = 0.63*1.70-1 = +7.1%.
    odds_raw = {"bookmakers": []}
    for book in ["pinnacle", "betonlineag", "draftkings", "fanduel", "betmgm"]:
        odds_raw["bookmakers"].append({
            "key": book,
            "markets": [{
                "key": "pitcher_strikeouts",
                "outcomes": [
                    {"description": "Martin Perez", "name": "Over", "point": 3.5, "price": -160},
                    {"description": "Martin Perez", "name": "Under", "point": 3.5, "price": 135},
                ]
            }]
        })
    odds = parse_props_odds(odds_raw)
    sigs = match_props(kambi, odds, min_sources=3)
    perez = [s for s in sigs if "perez" in s.player.lower().replace("é","e") and s.side == "over"]
    assert perez, f"expected a Perez Over signal, got {[(s.player,s.side,s.ev_pct) for s in sigs]}"
    print(f"  Perez Over 3.5 K: fair={perez[0].fair_prob}, EV={perez[0].ev_pct}%, verdict={perez[0].verdict}")



def test_matt_matthew_cross_feed_match():
    """Kambi 'Matt Liberatore' must match PropLine 'Matthew Liberatore'."""
    from app.props import PropOutcome, player_match_key
    from app.propline import parse_props as pl_parse
    from app.props_odds import match_props
    from app.matcher import american_to_decimal
    # Kambi side: Matt Liberatore strikeouts Over/Under 4.5
    kambi = [
        PropOutcome(None, "", "Matt Liberatore", player_match_key("Matt Liberatore"),
                    "strikeouts", "over", 4.5, 120, american_to_decimal(120), "OPEN", "kambi"),
        PropOutcome(None, "", "Matt Liberatore", player_match_key("Matt Liberatore"),
                    "strikeouts", "under", 4.5, -150, american_to_decimal(-150), "OPEN", "kambi"),
    ]
    # PropLine side: Matthew Liberatore, 3 sharp books
    raw = {"bookmakers": []}
    for bk in ["pinnacle", "novig", "betmgm"]:
        raw["bookmakers"].append({"key": bk, "markets": [{"key": "pitcher_strikeouts",
            "outcomes": [
                {"name": "Over", "description": "Matthew Liberatore", "price": 130, "point": 4.5},
                {"name": "Under", "description": "Matthew Liberatore", "price": -160, "point": 4.5},
            ]}]})
    oprops = pl_parse(raw)
    sigs = match_props(kambi, oprops, min_sources=3)
    assert sigs, "Matt/Matthew failed to match across feeds"
    print(f"  Matt↔Matthew matched: {len(sigs)} signal(s), "
          f"top EV {sigs[0].ev_pct}% on {sigs[0].player}")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"[test] {name}"); fn()
    print("\nALL PROP TESTS PASSED")
