"""
props_odds.py -- fetch pitcher props from The Odds API and match them to
Kambi props with the same de-vig + quarantine discipline as game lines.

Odds API serves player props PER EVENT (costs more quota), so we only
fetch events that overlap the Kambi slate.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

import requests

from .odds_client import SPORT_KEYS, SHARP_BOOKS
from .matcher import american_to_prob, american_to_decimal
from .props import PropOutcome, norm_player, PROP_STATS

BASE = "https://api.the-odds-api.com/v4"


@dataclass
class PropSignal:
    player: str
    stat: str
    side: str
    line: float
    kambi_american: int
    kambi_decimal: float
    fair_prob: float
    ev_pct: float
    n_sources: int
    n_sharp: int
    verdict: str = ""
    reasons: list = field(default_factory=list)


def list_events(league: str, api_key: str, timeout: int = 20) -> list:
    sport = SPORT_KEYS[league]
    r = requests.get(f"{BASE}/sports/{sport}/events",
                     params={"apiKey": api_key}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_event_props_odds(league: str, event_id: str, api_key: str,
                           books: str, timeout: int = 20) -> dict:
    sport = SPORT_KEYS[league]
    markets = ",".join(m for _, m in PROP_STATS.values())
    r = requests.get(
        f"{BASE}/sports/{sport}/events/{event_id}/odds",
        params={"apiKey": api_key, "regions": "us,us2,eu",
                "markets": markets, "oddsFormat": "american",
                "bookmakers": books},
        timeout=timeout)
    r.raise_for_status()
    return r.json()


def parse_props_odds(raw: dict) -> list[PropOutcome]:
    """Flatten an Odds API per-event prop payload into PropOutcome rows."""
    out = []
    market_to_stat = {m: s for s, (_, m) in PROP_STATS.items()}
    for bk in raw.get("bookmakers", []):
        book = bk.get("key", "")
        for mk in bk.get("markets", []):
            stat = market_to_stat.get(mk.get("key"))
            if stat is None:
                continue
            for oc in mk.get("outcomes", []):
                # Odds API props: 'description' = player, 'name' = Over/Under,
                # 'point' = line, 'price' = american.
                player = oc.get("description", "")
                side = oc.get("name", "").lower()
                if side not in ("over", "under"):
                    continue
                point = oc.get("point")
                price = oc.get("price")
                if point is None or price is None:
                    continue
                out.append(PropOutcome(
                    event_id=None, event_name="", player=player,
                    player_key=norm_player(player), stat=stat, side=side,
                    line=float(point), american=int(price),
                    decimal=american_to_decimal(int(price)),
                    status="OPEN", source=book,
                ))
    return out


def match_props(kambi: list[PropOutcome], odds: list[PropOutcome],
                min_sources: int = 3) -> list[PropSignal]:
    # index sharp side: (player_key, stat, side, line) -> {book: american}
    # de-vig needs both sides at the same line for a book.
    by_book_market = {}
    for o in odds:
        key = (o.player_key, o.stat, round(o.line, 1))
        by_book_market.setdefault(key, {}).setdefault(o.source, {})[o.side] = o.american

    fair = {}  # (player_key, stat, line) -> {book: {side: fair_prob}}
    for key, books in by_book_market.items():
        for book, sides in books.items():
            if len(sides) != 2:
                continue
            (sa, aa), (sb, ab) = list(sides.items())
            pa, pb = american_to_prob(aa), american_to_prob(ab)
            s = pa + pb
            if s <= 0:
                continue
            fair.setdefault(key, {})[book] = {sa: pa / s, sb: pb / s}

    signals = []
    for k in kambi:
        key = (k.player_key, k.stat, round(k.line, 1))
        market_fair = fair.get(key)
        if not market_fair:
            continue
        per_book = [(b, sides[k.side]) for b, sides in market_fair.items()
                    if k.side in sides]
        if len(per_book) < min_sources:
            continue
        probs = [p for _, p in per_book]
        fair_prob = statistics.median(probs)
        n_sharp = sum(1 for b, _ in per_book if b in SHARP_BOOKS)
        ev = fair_prob * k.decimal - 1.0
        sig = PropSignal(
            player=k.player, stat=k.stat, side=k.side, line=k.line,
            kambi_american=k.american, kambi_decimal=k.decimal,
            fair_prob=round(fair_prob, 4), ev_pct=round(ev * 100, 2),
            n_sources=len(per_book), n_sharp=n_sharp,
        )
        _classify_prop(sig, probs)
        signals.append(sig)
    signals.sort(key=lambda s: s.ev_pct, reverse=True)
    return signals


def _classify_prop(sig: PropSignal, probs):
    spread = (max(probs) - min(probs)) if len(probs) > 1 else 0.0
    disagree = spread > 0.06
    reasons = []
    if sig.ev_pct < 1.0:
        sig.verdict = "NONE"; return
    if sig.ev_pct >= 12.0:
        if sig.n_sources >= 3 and sig.n_sharp >= 2 and not disagree:
            sig.verdict = "EXTREME VERIFIED"
            reasons.append("12%+ EV, 3+ books, 2+ sharp, books agree")
        else:
            sig.verdict = "QUARANTINE"
            if sig.n_sources < 3: reasons.append("fewer than 3 books")
            if sig.n_sharp < 2: reasons.append("fewer than 2 sharp books")
            if disagree: reasons.append(f"books disagree ({spread:.1%})")
            reasons.append("props suspend fast; huge edges are usually stale")
    elif sig.ev_pct >= 8.0:
        if disagree:
            sig.verdict = "QUARANTINE"; reasons.append(f"books disagree ({spread:.1%})")
        else:
            sig.verdict = "MAJOR OUTLIER"; reasons.append("8%+ EV, books agree")
    else:
        sig.verdict = "EDGE"; reasons.append("modest edge")
    sig.reasons = reasons
