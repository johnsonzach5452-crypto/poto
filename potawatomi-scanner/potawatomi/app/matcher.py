"""
matcher.py -- match Kambi full-game outcomes to sharp-book lines and
compute a no-vig expected-value estimate.

Design rules that keep this honest:
  * Only compare the SAME market_type, SAME side, and SAME line.
    A 9.5 total is never compared to an 8.5 total. A +1.5 is never
    compared to a -1.5. Moneyline (no line) matches moneyline.
  * "Fair" probability comes from de-vigging the sharp books' two-way
    market, then taking the consensus (median) across books.
  * Require a minimum number of sharp sources before trusting a fair prob.
  * Anything with a large apparent edge that fails validation is not
    hidden -- it is flagged QUARANTINE so the user sees it AND sees why
    not to trust it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from .kambi_client import KambiOutcome
from .odds_client import OddsOutcome, SHARP_BOOKS

# Alias map for teams where Kambi's english name might differ from The Odds
# API. Extend as you find mismatches. Keys and values are normalized.
TEAM_ALIASES: dict[str, str] = {
    # "arizona diamondbacks": "arizona diamondbacks",  # example passthrough
}


@dataclass
class Signal:
    away_team: str
    home_team: str
    market_type: str
    side: str
    line: Optional[float]
    kambi_american: int
    kambi_decimal: float
    fair_prob: float
    ev_pct: float
    n_sources: int
    n_sharp: int
    book_probs: list = field(default_factory=list)
    verdict: str = ""       # 'MAJOR OUTLIER' | 'EXTREME VERIFIED' | 'QUARANTINE' | 'EDGE'
    reasons: list = field(default_factory=list)


def american_to_prob(a: int) -> float:
    if a > 0:
        return 100.0 / (a + 100.0)
    return abs(a) / (abs(a) + 100.0)


def american_to_decimal(a: int) -> float:
    if a > 0:
        return 1.0 + a / 100.0
    return 1.0 + 100.0 / abs(a)


def _norm_alias(team: str) -> str:
    return TEAM_ALIASES.get(team, team)


def _event_key(away: str, home: str) -> frozenset:
    return frozenset({_norm_alias(away.lower()), _norm_alias(home.lower())})


def _devig_two_way(p_a: float, p_b: float) -> tuple[float, float]:
    s = p_a + p_b
    if s <= 0:
        return 0.0, 0.0
    return p_a / s, p_b / s


def build_fair_probs(odds: list[OddsOutcome], time_tol_min: int = 40) -> dict:
    """
    Returns nested dict:
      fair[(event_key, market_type, line)][book][side] = fair_prob
    Only two-way markets that have both sides from the same book are devigged.
    """
    # group outcomes by (event_key, market_type, line, book)
    grouped: dict = {}
    events_time: dict = {}
    for o in odds:
        ek = _event_key(o.away_team, o.home_team)
        events_time[ek] = o.start
        key = (ek, o.market_type, _line_key(o.line))
        grouped.setdefault(key, {}).setdefault(o.book, {})[o.side] = o

    fair: dict = {}
    for (ek, mtype, lkey), books in grouped.items():
        for book, sides in books.items():
            if len(sides) != 2:
                continue  # need both sides to devig
            (side_a, oc_a), (side_b, oc_b) = list(sides.items())
            pa = american_to_prob(oc_a.american)
            pb = american_to_prob(oc_b.american)
            fpa, fpb = _devig_two_way(pa, pb)
            fair.setdefault((ek, mtype, lkey), {})[book] = {side_a: fpa, side_b: fpb}
    return fair, events_time


def _line_key(line: Optional[float]):
    return None if line is None else round(float(line), 2)


def match(kambi: list[KambiOutcome], odds: list[OddsOutcome],
          min_sources: int = 3, time_tol_min: int = 40) -> list[Signal]:
    fair, ev_times = build_fair_probs(odds, time_tol_min)
    signals: list[Signal] = []

    for k in kambi:
        ek = _event_key(k.away_team, k.home_team)

        # time sanity: skip if sharp event time is wildly different
        st = ev_times.get(ek)
        if st is not None and abs((st - k.start).total_seconds()) > time_tol_min * 60:
            continue

        # For spread, Kambi side line is signed to that team; The Odds API
        # spread outcome 'point' is also signed per team, so line matches
        # directly. For totals, line is the shared number.
        lkey = _line_key(k.line)
        market_fair = fair.get((ek, k.market_type, lkey))
        if not market_fair:
            continue

        # collect this side's fair prob across books
        per_book = []
        for book, sides in market_fair.items():
            if k.side in sides:
                per_book.append((book, sides[k.side]))
        if len(per_book) < min_sources:
            continue

        probs = [p for _, p in per_book]
        fair_prob = statistics.median(probs)
        n_sharp = sum(1 for b, _ in per_book if b in SHARP_BOOKS)

        ev = fair_prob * k.decimal - 1.0
        ev_pct = round(ev * 100, 2)

        sig = Signal(
            away_team=k.away_team, home_team=k.home_team,
            market_type=k.market_type, side=k.side, line=k.line,
            kambi_american=k.american, kambi_decimal=k.decimal,
            fair_prob=round(fair_prob, 4), ev_pct=ev_pct,
            n_sources=len(per_book), n_sharp=n_sharp,
            book_probs=[(b, round(p, 4)) for b, p in per_book],
        )
        _classify(sig, probs)
        signals.append(sig)

    signals.sort(key=lambda s: s.ev_pct, reverse=True)
    return signals


def kambi_event_index(kambi: list[KambiOutcome]) -> dict:
    """Map normalized event key -> (away, home) display names, Kambi side."""
    idx = {}
    for k in kambi:
        idx[_event_key(k.away_team, k.home_team)] = (k.away_team, k.home_team)
    return idx


def odds_event_index(odds: list[OddsOutcome]) -> dict:
    """Map normalized event key -> (away, home) display names, Odds side."""
    idx = {}
    for o in odds:
        idx[_event_key(o.away_team, o.home_team)] = (o.away_team, o.home_team)
    return idx


def match_all(kambi: list[KambiOutcome], odds: list[OddsOutcome],
              min_sources: int = 1, time_tol_min: int = 40) -> list[Signal]:
    """Like match() but returns EVERY matched comparison, including negative
    EV, with no verdict filtering. For diagnostics."""
    return match(kambi, odds, min_sources=min_sources, time_tol_min=time_tol_min)


def _classify(sig: Signal, probs: list[float]):
    """Assign a verdict. Big edges must clear extra checks or get quarantined."""
    reasons = []
    spread = (max(probs) - min(probs)) if len(probs) > 1 else 0.0
    disagree = spread > 0.06  # sharp books disagreeing a lot = suspect

    if sig.ev_pct < 1.0:
        sig.verdict = "NONE"
        return

    if sig.ev_pct >= 12.0:
        # Extreme. Demand strong validation or quarantine.
        ok = (sig.n_sources >= 3 and sig.n_sharp >= 2 and not disagree)
        if ok:
            sig.verdict = "EXTREME VERIFIED"
            reasons.append("12%+ EV, 3+ books, 2+ sharp, books agree")
        else:
            sig.verdict = "QUARANTINE"
            if sig.n_sources < 3:
                reasons.append("fewer than 3 comparison books")
            if sig.n_sharp < 2:
                reasons.append("fewer than 2 sharp sources")
            if disagree:
                reasons.append(f"sharp books disagree ({spread:.1%} prob spread)")
            reasons.append("huge edge like this is usually a data mismatch, not value")
    elif sig.ev_pct >= 8.0:
        if disagree:
            sig.verdict = "QUARANTINE"
            reasons.append(f"sharp books disagree ({spread:.1%} prob spread)")
        else:
            sig.verdict = "MAJOR OUTLIER"
            reasons.append("8%+ EV with book agreement")
    else:
        sig.verdict = "EDGE"
        reasons.append("modest edge (the reliable kind)")

    sig.reasons = reasons
