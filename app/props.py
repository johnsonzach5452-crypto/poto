"""
props.py -- pitcher prop parsing (Kambi side) + normalization shared with
the Odds API side. Scope: MLB pitcher strikeouts and outs, Over/Under only.

The trap this guards against: Kambi lists an Over/Under strikeout market
AND a ladder of "2+ / 3+ / ... Strikeouts" markets. Only the Over/Under
"Player Occurrence Line" is comparable to a sharp O/U line. Everything
with a "+" threshold is a different bet and is rejected.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import requests

from .kambi_client import KAMBI_HOST, DEFAULT_PARAMS, HEADERS, _parse_start

# Exact criterion prefixes we accept (Over/Under player lines only).
STRIKEOUTS_PREFIX = "Strikeouts thrown by the Player"
OUTS_PREFIX = "Total Outs Recorded by the Player"

# stat key -> (kambi criterion prefix, odds api market key)
PROP_STATS = {
    "strikeouts": (STRIKEOUTS_PREFIX, "pitcher_strikeouts"),
    "outs": (OUTS_PREFIX, "pitcher_outs"),
}


@dataclass
class PropOutcome:
    event_id: int
    event_name: str
    player: str            # display name, e.g. "Martin Pérez"
    player_key: str        # normalized for matching, e.g. "martin perez"
    stat: str              # 'strikeouts' | 'outs'
    side: str              # 'over' | 'under'
    line: float
    american: int
    decimal: float
    status: str
    source: str            # 'kambi' | book key


def norm_player(name: str) -> str:
    """Lowercase, strip accents, drop suffixes/punctuation for matching.
    'Martin Pérez' -> 'martin perez'; 'Luis Robert Jr.' -> 'luis robert'."""
    if not name:
        return ""
    # strip accents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    low = ascii_name.lower()
    low = re.sub(r"[^a-z0-9 ]", " ", low)
    low = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", low)
    return re.sub(r"\s+", " ", low).strip()


# Words that sometimes trail a player name in a book's description field.
_STOP_TOKENS = {"strikeouts", "thrown", "outs", "recorded", "total", "player",
                "pitcher", "the", "by"}


def player_match_key(name: str) -> str:
    """A tolerant key for matching the SAME pitcher across feeds that spell
    the first name differently ('Matt' vs 'Matthew', 'Mike' vs 'Michael').

    Strategy: strip accents/suffixes/parentheticals, then key on
    first-initial + last-name. 'Matt Liberatore' and 'Matthew Liberatore'
    both -> 'm liberatore'; 'David Peterson (CHC)' -> 'd peterson'.

    Collision risk (same initial + surname, same game) is negligible for
    starting pitchers, and matching is already scoped per event.
    """
    if not name:
        return ""
    n = re.sub(r"\(.*?\)", " ", name)             # drop "(CHC)" etc.
    n = norm_player(n)                            # accents, punct, suffixes
    tokens = [t for t in n.split() if t not in _STOP_TOKENS]
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]
    return f"{tokens[0][0]} {tokens[-1]}"


def _accepted_stat(label: str) -> Optional[str]:
    # Reject the "N+" ladder outright.
    if re.search(r"\d\+", label):
        return None
    if label.startswith(STRIKEOUTS_PREFIX):
        return "strikeouts"
    if label.startswith(OUTS_PREFIX):
        return "outs"
    return None


def _american(s):
    if s is None:
        return None
    s = str(s).strip().replace("+", "")
    if s == "" or s.lower() == "evens":
        return 100
    try:
        return int(s)
    except ValueError:
        return None


def fetch_event_props(event_id: int, timeout: int = 15) -> dict:
    """Per-event Kambi feed that contains the prop betOffers."""
    url = f"{KAMBI_HOST}/potawuswirl/betoffer/event/{event_id}.json"
    resp = requests.get(url, params=DEFAULT_PARAMS, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def parse_props(raw: dict) -> list[PropOutcome]:
    """Parse pitcher O/U props from a per-event betOffer payload."""
    out: list[PropOutcome] = []
    events = raw.get("events", [])
    ev = events[0] if events else {}
    event_id = ev.get("id")
    event_name = ev.get("name", "")

    for bo in raw.get("betOffers", []):
        crit = bo.get("criterion", {}) or {}
        label = crit.get("englishLabel") or crit.get("label") or ""
        stat = _accepted_stat(label)
        if stat is None:
            continue
        botype = (bo.get("betOfferType", {}) or {}).get("englishName", "")
        if botype != "Player Occurrence Line":
            continue
        for oc in bo.get("outcomes", []):
            if oc.get("status") != "OPEN":
                continue
            t = oc.get("type", "")
            side = "over" if t == "OT_OVER" else "under" if t == "OT_UNDER" else None
            if side is None:
                continue
            american = _american(oc.get("oddsAmerican"))
            odds_milli = oc.get("odds")
            line = oc.get("line")
            if american is None or odds_milli is None or line is None:
                continue
            player = oc.get("participant", "")
            out.append(PropOutcome(
                event_id=event_id, event_name=event_name,
                player=player, player_key=player_match_key(player),
                stat=stat, side=side, line=round(line / 1000.0, 2),
                american=american, decimal=round(odds_milli / 1000.0, 4),
                status="OPEN", source="kambi",
            ))
    return out
