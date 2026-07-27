"""
Potawatomi line scanner -- Streamlit dashboard.

Pulls full-game lines from the Potawatomi (Kambi BYOD) feed, pulls sharp
book lines from The Odds API, matches like-for-like markets, and shows
where Kambi diverges from the no-vig consensus.

Read the caveats in the sidebar before betting anything. The scanner's
job is to surface candidates; YOUR job is to confirm the exact ticket at
the kiosk before wagering.
"""

import os
import traceback

import streamlit as st

from app.kambi_client import fetch_kambi, parse_kambi
from app.odds_client import fetch_odds, parse_odds, DEFAULT_BOOKS
from app.matcher import match

st.set_page_config(page_title="Potawatomi Line Scanner", layout="wide")

VERDICT_STYLE = {
    "EXTREME VERIFIED": "🟣",
    "MAJOR OUTLIER": "🟢",
    "EDGE": "🔵",
    "QUARANTINE": "🟠",
    "NONE": "⚪",
}

st.title("Potawatomi Line Scanner")

with st.sidebar:
    st.header("Settings")
    league = st.selectbox("League", ["MLB", "WNBA"])
    api_key = st.text_input("The Odds API key", value=os.getenv("ODDS_API_KEY", ""),
                            type="password")
    books = st.text_input("Sharp/comparison books",
                          value=os.getenv("ODDS_API_BOOKMAKERS", DEFAULT_BOOKS))
    min_sources = st.number_input("Min comparison books", 1, 10,
                                  int(os.getenv("MIN_SOURCES", "3")))
    min_ev = st.number_input("Min EV %% to display", 0.0, 50.0,
                             float(os.getenv("MIN_EV_MAIN_PCT", "1")))
    st.markdown("---")
    st.caption(
        "Before betting any signal, confirm on the kiosk: exact teams, date, "
        "full-game (not first-5 / half), spread or total number, and American "
        "odds. A large apparent edge is far more often a data mismatch than "
        "real value. Size small; keep a closing-line record before trusting it."
    )

col_a, col_b = st.columns([1, 1])
scan = col_a.button("Scan now", type="primary")

if scan:
    if not api_key:
        st.error("Enter your The Odds API key (or set ODDS_API_KEY).")
        st.stop()

    kambi_outcomes = []
    kambi_status = ""
    try:
        raw = fetch_kambi(league)
        kambi_outcomes = parse_kambi(raw, league)
        kambi_status = f"Loaded {len(kambi_outcomes)} full-game Potawatomi outcomes."
    except Exception as e:
        st.error(f"Kambi fetch failed: {type(e).__name__}: {e}")
        st.caption(
            "If this is a 403 or connection error, the feed is likely gated to "
            "the browser session and a server-side request won't work directly. "
            "See README for the browser-capture fallback."
        )
        with st.expander("Traceback"):
            st.code(traceback.format_exc())

    odds_outcomes = []
    if kambi_outcomes:
        try:
            odds_raw = fetch_odds(league, api_key, books)
            odds_outcomes = parse_odds(odds_raw)
        except Exception as e:
            st.error(f"The Odds API fetch failed: {type(e).__name__}: {e}")

    if kambi_outcomes:
        st.success(kambi_status)

    if kambi_outcomes and odds_outcomes:
        signals = match(kambi_outcomes, odds_outcomes, min_sources=int(min_sources))
        shown = [s for s in signals if s.ev_pct >= min_ev and s.verdict != "NONE"]

        st.subheader(f"{len(shown)} candidate signal(s)")
        if not shown:
            st.info("No divergences above your threshold. That's normal and healthy.")

        for s in shown:
            icon = VERDICT_STYLE.get(s.verdict, "")
            line_txt = "" if s.line is None else f" {s.line:+g}" if s.market_type == "spread" else f" {s.line:g}"
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 2, 2])
                c1.markdown(
                    f"**{icon} {s.verdict}** — {s.away_team} @ {s.home_team}  \n"
                    f"{s.market_type.title()} · **{s.side.title()}{line_txt}** · "
                    f"Kambi {s.kambi_american:+d}"
                )
                c2.metric("Est. EV", f"{s.ev_pct:+.1f}%")
                c2.caption(f"fair {s.fair_prob:.1%}")
                c3.caption(
                    f"{s.n_sources} books ({s.n_sharp} sharp)  \n"
                    + "  \n".join(s.reasons)
                )
                if s.verdict == "QUARANTINE":
                    c1.warning("Do not bet on this alone — flagged as likely artifact.")
else:
    st.info("Set your key and press **Scan now**.")
