"""
Potawatomi line scanner -- Streamlit dashboard (with diagnostics).

The diagnostic panel shows exactly where the pipeline stands: how many
games loaded on each side, how many matched, and every comparison found
(including negative-EV and sub-threshold ones). If you get "0 signals",
the diagnostic tells you WHY -- no matches (a naming/time problem) vs.
matches exist but none are +EV right now (normal).
"""

import os
import traceback

import streamlit as st

from app.kambi_client import fetch_kambi, parse_kambi
from app.odds_client import fetch_odds, parse_odds, DEFAULT_BOOKS
from app.matcher import match, kambi_event_index, odds_event_index

st.set_page_config(page_title="Potawatomi Line Scanner", layout="wide")

VERDICT_STYLE = {
    "EXTREME VERIFIED": "P", "MAJOR OUTLIER": "G", "EDGE": "B",
    "QUARANTINE": "Q", "NONE": "-",
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
    min_ev = st.number_input("Min EV % to display", 0.0, 50.0,
                             float(os.getenv("MIN_EV_MAIN_PCT", "1")))
    show_diag = st.checkbox("Show diagnostics", value=True)
    st.markdown("---")
    st.caption(
        "Before betting any signal, confirm on the kiosk: exact teams, date, "
        "full-game (not first-5 / half), spread or total number, and American "
        "odds. A large apparent edge is far more often a data mismatch than "
        "real value."
    )

if st.button("Scan now", type="primary"):
    if not api_key:
        st.error("Enter your The Odds API key (or set ODDS_API_KEY).")
        st.stop()

    kambi_outcomes = []
    try:
        raw = fetch_kambi(league)
        kambi_outcomes = parse_kambi(raw, league)
        st.success(f"Loaded {len(kambi_outcomes)} full-game Potawatomi outcomes.")
    except Exception as e:
        st.error(f"Kambi fetch failed: {type(e).__name__}: {e}")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())
        st.stop()

    odds_outcomes = []
    try:
        odds_raw = fetch_odds(league, api_key, books)
        odds_outcomes = parse_odds(odds_raw)
    except Exception as e:
        st.error(f"The Odds API fetch failed: {type(e).__name__}: {e}")
        st.stop()

    all_signals = match(kambi_outcomes, odds_outcomes, min_sources=int(min_sources))

    k_idx = kambi_event_index(kambi_outcomes)
    o_idx = odds_event_index(odds_outcomes)
    matched_keys = set(k_idx) & set(o_idx)

    if show_diag:
        st.markdown("### Diagnostics")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Kambi outcomes", len(kambi_outcomes))
        c2.metric("Kambi games", len(k_idx))
        c3.metric("Odds API games", len(o_idx))
        c4.metric("Matched games", len(matched_keys))
        c5.metric("Comparisons", len(all_signals))

        if len(matched_keys) == 0:
            st.error(
                "0 games matched between the two feeds. This is a team-name or "
                "schedule mismatch, not a 'no value' situation. Compare the two "
                "lists below -- the same game should read identically on both sides."
            )
            cc1, cc2 = st.columns(2)
            with cc1:
                st.markdown("**Kambi games**")
                for away, home in sorted(k_idx.values()):
                    st.write(f"{away} @ {home}")
            with cc2:
                st.markdown("**Odds API games**")
                for away, home in sorted(o_idx.values()):
                    st.write(f"{away} @ {home}")
        else:
            st.caption(
                f"{len(matched_keys)} games matched. Every comparison found is "
                "listed below (including negative EV). If these look sane and "
                "just aren't positive, that's a normal 'no value right now'."
            )
            rows = []
            for s in all_signals:
                line = "" if s.line is None else (f"{s.line:+g}" if s.market_type == "spread" else f"{s.line:g}")
                rows.append({
                    "matchup": f"{s.away_team} @ {s.home_team}",
                    "market": s.market_type,
                    "side": s.side,
                    "line": line,
                    "kambi": f"{s.kambi_american:+d}",
                    "fair%": f"{s.fair_prob:.1%}",
                    "EV%": s.ev_pct,
                    "books": s.n_sources,
                    "verdict": s.verdict,
                })
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.warning(
                    "Games matched, but no market-level comparisons were built. "
                    "Likely the spread/total numbers differ between books, or "
                    "fewer than 'Min comparison books' had both sides. Try "
                    "lowering Min comparison books to 1."
                )
        st.markdown("---")

    shown = [s for s in all_signals if s.ev_pct >= min_ev and s.verdict != "NONE"]
    st.subheader(f"{len(shown)} candidate signal(s)")
    if not shown:
        st.info("No divergences above your threshold.")
    for s in shown:
        icon = VERDICT_STYLE.get(s.verdict, "")
        line_txt = "" if s.line is None else (f" {s.line:+g}" if s.market_type == "spread" else f" {s.line:g}")
        with st.container(border=True):
            a, b, c = st.columns([3, 2, 2])
            a.markdown(
                f"**[{icon}] {s.verdict}** -- {s.away_team} @ {s.home_team}  \n"
                f"{s.market_type.title()} - **{s.side.title()}{line_txt}** - "
                f"Kambi {s.kambi_american:+d}"
            )
            b.metric("Est. EV", f"{s.ev_pct:+.1f}%")
            b.caption(f"fair {s.fair_prob:.1%}")
            c.caption(f"{s.n_sources} books ({s.n_sharp} sharp)  \n" + "  \n".join(s.reasons))
            if s.verdict == "QUARANTINE":
                a.warning("Do not bet on this alone -- flagged as likely artifact.")
else:
    st.info("Set your key and press **Scan now**.")
