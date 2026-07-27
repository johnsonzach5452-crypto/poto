"""
kambi_client.py -- fetch and parse the Kambi BYOD offering feed.

The one job that matters most here: only emit FULL-GAME markets. The feed
mixes full-game lines with "First 5 Innings" (and, in other sports, "First
Half") markets. Comparing a full-game sharp line against a Kambi first-5
line is the #1 source of fake edges, so we filter on the criterion label
and refuse anything that isn't a clean full-game market.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

# The real endpoint, confirmed from a live browser network capture of the
# Potawatomi BYOD page. Host/channel/client differ from older guesses.
KAMBI_HOST = "https://eu.offering-api.kambicdn.com/offering/v2018"

DEFAULT_PARAMS = {
    "channel_id": "7",
    "client_id": "200",
    "lang": "en_US",
    "market": "US",
    "useCombined": "true",
    "useCombinedLive": "true",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://retail.kambicdn.com",
    "Referer": "https://retail.kambicdn.com/",
}

# Sport path segment used in the listView URL.
SPORT_PATHS = {
    "MLB": "baseball/mlb",
    "WNBA": "basketball/wnba",
}

# We ONLY accept these exact criterion labels as full-game markets.
# Anything containing "First 5", "First Half", "1st", "Innings", "Half",
# "Quarter", "Period", team-total wording, etc. is rejected.
FULL_GAME_CRITERIA = {
    "Moneyline": "moneyline",
    "Run Line": "spread",
    "Total Runs": "total",
    # basketball / WNBA full-game equivalents:
    "Point Spread": "spread",
    "Total Points": "total",
    "Total": "total",
}

# Substrings that, if present in a criterion label, mean it is NOT full game.
PARTIAL_MARKERS = (
    "first 5", "first five", "1st 5", "f5",
    "first half", "1st half", "second half", "2nd half",
    "quarter", "1st", "2nd", "3rd", "4th",
    "inning", "innings", "period", "team total", "each team",
)


@dataclass
class KambiOutcome:
    event_id: int
    event_name: str          # display, e.g. "ARI Diamondbacks @ PIT Pirates"
    away_team: str           # full english name
    home_team: str           # full english name
    start: datetime
    market_type: str         # 'moneyline' | 'spread' | 'total'
    side: str                # canonical: team full name, or 'over'/'under'
    line: Optional[float]    # None for moneyline
    american: int
    decimal: float
    status: str              # 'OPEN' etc.


def _american_from_string(s: str) -> Optional[int]:
    if s is None:
        return None
    s = s.strip().replace("+", "")
    if s == "" or s.lower() == "evens":
        return 100
    try:
        return int(s)
    except ValueError:
        return None


def _decimal_from_milli(odds_milli: Optional[int]) -> Optional[float]:
    if odds_milli is None:
        return None
    return round(odds_milli / 1000.0, 4)


def _is_full_game(label: str) -> bool:
    low = label.lower()
    if any(m in low for m in PARTIAL_MARKERS):
        return False
    return label in FULL_GAME_CRITERIA


def fetch_kambi(league: str, timeout: int = 15) -> dict:
    """Server-side GET of the listView feed. Returns parsed JSON or raises."""
    sport_path = SPORT_PATHS[league]
    url = f"{KAMBI_HOST}/potawuswirl/listView/{sport_path}/all/all/matches.json"
    resp = requests.get(url, params=DEFAULT_PARAMS, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def parse_kambi(raw: dict, league: str = "MLB") -> list[KambiOutcome]:
    """Turn a listView payload into a flat list of full-game outcomes only."""
    out: list[KambiOutcome] = []
    for wrapper in raw.get("events", []):
        ev = wrapper.get("event", {})
        offers = wrapper.get("betOffers", []) or []
        home = ev.get("homeName", "")
        away = ev.get("awayName", "")
        # Prefer full english team names from outcome englishLabel when present.
        start = _parse_start(ev.get("start"))
        if start is None:
            continue

        for offer in offers:
            crit = offer.get("criterion", {}) or {}
            label = crit.get("englishLabel") or crit.get("label") or ""
            if not _is_full_game(label):
                continue
            market_type = FULL_GAME_CRITERIA[label]

            for oc in offer.get("outcomes", []):
                status = oc.get("status", "")
                if status != "OPEN":
                    continue
                american = _american_from_string(oc.get("oddsAmerican"))
                decimal = _decimal_from_milli(oc.get("odds"))
                if american is None or decimal is None:
                    continue

                line = None
                if "line" in oc and oc["line"] is not None:
                    line = round(oc["line"] / 1000.0, 3)

                side = _canonical_side(market_type, oc)
                if side is None:
                    continue

                # Full english team names for reliable matching.
                oc_team = oc.get("englishLabel")
                out.append(KambiOutcome(
                    event_id=ev.get("id"),
                    event_name=ev.get("name", ""),
                    away_team=_full_team(away, oc_team, market_type, "away"),
                    home_team=_full_team(home, oc_team, market_type, "home"),
                    start=start,
                    market_type=market_type,
                    side=side,
                    line=line,
                    american=american,
                    decimal=decimal,
                    status=status,
                ))
    return _attach_full_team_names(out, raw)


def _canonical_side(market_type: str, oc: dict) -> Optional[str]:
    if market_type == "total":
        t = oc.get("type", "")
        if t == "OT_OVER":
            return "over"
        if t == "OT_UNDER":
            return "under"
        return None
    # moneyline / spread -> side is the team full english name
    return _norm_team(oc.get("englishLabel") or oc.get("label") or "")


def _full_team(short, oc_team, market_type, which):
    # Best-effort: use english outcome label when it's a team market.
    return oc_team or short


def _attach_full_team_names(outcomes: list[KambiOutcome], raw: dict) -> list[KambiOutcome]:
    """Fill away/home with full english names using the event englishName.

    Kambi event.englishName is "Home Full - Away Full". We use it so event
    matching against The Odds API (which uses full names) is reliable.
    """
    names_by_id = {}
    for wrapper in raw.get("events", []):
        ev = wrapper.get("event", {})
        eng = ev.get("englishName", "")
        if " - " in eng:
            home_full, away_full = eng.split(" - ", 1)
            names_by_id[ev.get("id")] = (away_full.strip(), home_full.strip())
    for o in outcomes:
        if o.event_id in names_by_id:
            o.away_team, o.home_team = names_by_id[o.event_id]
    return outcomes


def _norm_team(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _parse_start(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
