"""
odds_client.py -- fetch and parse sharp-book lines from The Odds API.

The Odds API's default h2h/spreads/totals markets are full-game, which
lines up cleanly with the full-game-only filter on the Kambi side. We do
NOT request period markets here, so there is no full-vs-partial mismatch
risk from this source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS = {
    "MLB": "baseball_mlb",
    "WNBA": "basketball_wnba",
}

# Books we treat as sharp / reference, in rough weight order.
SHARP_BOOKS = ["pinnacle", "betonlineag", "circa", "novig", "matchbook", "smarkets", "polymarket"]
DEFAULT_BOOKS = "pinnacle,betonlineag,bovada,draftkings,fanduel,betmgm,caesars"


@dataclass
class OddsOutcome:
    away_team: str
    home_team: str
    start: datetime
    book: str
    market_type: str        # 'moneyline' | 'spread' | 'total'
    side: str               # normalized team name, or 'over'/'under'
    line: Optional[float]
    american: int


_MARKET_MAP = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}


def fetch_odds(league: str, api_key: str, books: str = DEFAULT_BOOKS,
               timeout: int = 20) -> list:
    sport = SPORT_KEYS[league]
    url = f"{BASE}/sports/{sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us,us2,eu",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
        "bookmakers": books,
    }
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def parse_odds(raw: list) -> list[OddsOutcome]:
    out: list[OddsOutcome] = []
    for ev in raw:
        away = ev.get("away_team", "")
        home = ev.get("home_team", "")
        start = _parse_time(ev.get("commence_time"))
        if start is None:
            continue
        for bk in ev.get("bookmakers", []):
            book = bk.get("key", "")
            for mk in bk.get("markets", []):
                mtype = _MARKET_MAP.get(mk.get("key"))
                if mtype is None:
                    continue
                for oc in mk.get("outcomes", []):
                    american = oc.get("price")
                    if american is None:
                        continue
                    name = oc.get("name", "")
                    point = oc.get("point")
                    if mtype == "total":
                        side = name.lower()  # "Over"/"Under"
                        line = float(point) if point is not None else None
                    elif mtype == "spread":
                        side = _norm_team(name)
                        line = float(point) if point is not None else None
                    else:
                        side = _norm_team(name)
                        line = None
                    out.append(OddsOutcome(
                        away_team=away, home_team=home, start=start,
                        book=book, market_type=mtype, side=side,
                        line=line, american=int(american),
                    ))
    return out


def _norm_team(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _parse_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
