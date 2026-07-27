"""
propline.py -- PropLine adapter (prop-line.com), an alternative prop-odds
source to The Odds API. Same per-event response shape, so parsing reuses
parse_props_odds. Key differences handled here:

  * Base URL + auth (apiKey query param).
  * markets=pitcher_strikeouts returns ALT lines too (the 3+/5+/6+ ladder).
    We keep only primary half-point Over/Under lines and drop the ladder.
  * Prop books skew sharp (Pinnacle + exchanges), so fewer sources/prop.
"""
from __future__ import annotations

import requests

from .odds_client import SPORT_KEYS
from .props import PROP_STATS, PropOutcome, norm_player
from .matcher import american_to_decimal

BASE = "https://api.prop-line.com/v1"


def list_events(league: str, api_key: str, timeout: int = 20) -> list:
    sport = SPORT_KEYS[league]
    r = requests.get(f"{BASE}/sports/{sport}/events",
                     params={"apiKey": api_key}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_event_props(league: str, event_id: str, api_key: str,
                      books: str = "", timeout: int = 20) -> dict:
    sport = SPORT_KEYS[league]
    markets = ",".join(m for _, m in PROP_STATS.values())
    params = {"apiKey": api_key, "markets": markets}
    if books:
        params["bookmakers"] = books
    r = requests.get(f"{BASE}/sports/{sport}/events/{event_id}/odds",
                     params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def parse_props(raw: dict) -> list[PropOutcome]:
    """Parse PropLine per-event props. Drops DFS pick'em (PrizePicks) and
    the whole-number '+' ladder; keeps primary half-point O/U lines."""
    out = []
    market_to_stat = {m: s for s, (_, m) in PROP_STATS.items()}
    for bk in raw.get("bookmakers", []):
        book = bk.get("key", "")
        # PrizePicks uses synthetic +100/+100 pricing -> not real odds.
        if book in ("prizepicks",):
            continue
        for mk in bk.get("markets", []):
            stat = market_to_stat.get(mk.get("key"))
            if stat is None:
                continue
            for oc in mk.get("outcomes", []):
                side = oc.get("name", "").lower()
                if side not in ("over", "under"):
                    continue
                point = oc.get("point")
                price = oc.get("price")
                if point is None or price is None:
                    continue
                # Keep only primary half-point lines; the +N ladder is
                # whole numbers (3+, 5+, 6+) and must not be compared to
                # a half-point O/U.
                if abs(float(point) * 2 - round(float(point) * 2)) > 1e-9:
                    continue
                if float(point) == int(float(point)):
                    continue  # whole number -> ladder, skip
                player = oc.get("description", "")
                out.append(PropOutcome(
                    event_id=None, event_name="", player=player,
                    player_key=norm_player(player), stat=stat, side=side,
                    line=float(point), american=int(price),
                    decimal=american_to_decimal(int(price)),
                    status="OPEN", source=book,
                ))
    return out
