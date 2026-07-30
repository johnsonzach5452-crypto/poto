"""
Potawatomi line scanner -- Streamlit dashboard.
Two modes: Game lines (moneyline/spread/total) and Pitcher props (K's, outs).
Both use the same de-vig + quarantine discipline and a diagnostics panel.
"""
import os
import traceback

import streamlit as st

from app.kambi_client import fetch_kambi, parse_kambi, SPORT_PATHS
from app.odds_client import fetch_odds, parse_odds, DEFAULT_BOOKS
from app.matcher import match, kambi_event_index, odds_event_index
from app.props import fetch_event_props, parse_props, norm_player
from app.props_odds import (
    list_events, fetch_event_props_odds, parse_props_odds, match_props,
)
from app import propline

st.set_page_config(page_title="Potawatomi Line Scanner", layout="wide")
st.title("Potawatomi Line Scanner")

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Scan mode", ["Game lines", "Pitcher props"])
    league = st.selectbox("League", ["MLB", "WNBA"])
    api_key = st.text_input("The Odds API key", value=os.getenv("ODDS_API_KEY", ""),
                            type="password")
    books = st.text_input("Books", value=os.getenv("ODDS_API_BOOKMAKERS", DEFAULT_BOOKS))
    min_sources = st.number_input("Min comparison books", 1, 10,
                                  int(os.getenv("MIN_SOURCES", "3")))
    min_ev = st.number_input("Min EV % to display", 0.0, 50.0,
                             float(os.getenv("MIN_EV_MAIN_PCT", "1")))
    prop_source = st.selectbox("Prop odds source", ["PropLine (free)", "The Odds API"])
    propline_key = st.text_input("PropLine API key", value=os.getenv("PROPLINE_API_KEY", ""), type="password")
    show_diag = st.checkbox("Show diagnostics", value=True)
    st.markdown("---")
    st.caption(
        "Confirm every signal on the kiosk before betting: exact player/teams, "
        "full-game, the number, and the odds. Props suspend fast -- a big prop "
        "edge is usually a stale or about-to-move line, not free money."
    )

def verdict_row(container, header, ev_pct, fair_prob, n_sources, n_sharp, reasons, verdict):
    a, b, c = container.columns([3, 2, 2])
    a.markdown(header)
    b.metric("Est. EV", f"{ev_pct:+.1f}%")
    b.caption(f"fair {fair_prob:.1%}")
    c.caption(f"{n_sources} books ({n_sharp} sharp)  \n" + "  \n".join(reasons))
    if verdict == "QUARANTINE":
        a.warning("Do not bet on this alone -- flagged as likely artifact.")


if st.button("Scan now", type="primary"):
    if not api_key:
        st.error("Enter your The Odds API key."); st.stop()

    # ============ GAME LINES ============
    if mode == "Game lines":
        try:
            raw = fetch_kambi(league)
            kambi_outcomes = parse_kambi(raw, league)
            st.success(f"Loaded {len(kambi_outcomes)} full-game Potawatomi outcomes.")
        except Exception as e:
            st.error(f"Kambi fetch failed: {type(e).__name__}: {e}")
            with st.expander("Traceback"): st.code(traceback.format_exc())
            st.stop()
        try:
            odds_outcomes = parse_odds(fetch_odds(league, api_key, books))
        except Exception as e:
            st.error(f"Odds API fetch failed: {type(e).__name__}: {e}"); st.stop()

        all_signals = match(kambi_outcomes, odds_outcomes, min_sources=int(min_sources))
        k_idx, o_idx = kambi_event_index(kambi_outcomes), odds_event_index(odds_outcomes)
        matched = set(k_idx) & set(o_idx)

        if show_diag:
            st.markdown("### Diagnostics")
            cols = st.columns(5)
            cols[0].metric("Kambi outcomes", len(kambi_outcomes))
            cols[1].metric("Kambi games", len(k_idx))
            cols[2].metric("Odds games", len(o_idx))
            cols[3].metric("Matched games", len(matched))
            cols[4].metric("Comparisons", len(all_signals))
            if not matched:
                st.error("0 games matched -- name/schedule mismatch. Lists below:")
                d1, d2 = st.columns(2)
                d1.markdown("**Kambi**"); [d1.write(f"{a} @ {h}") for a, h in sorted(k_idx.values())]
                d2.markdown("**Odds API**"); [d2.write(f"{a} @ {h}") for a, h in sorted(o_idx.values())]
            else:
                rows = [{"matchup": f"{s.away_team} @ {s.home_team}", "market": s.market_type,
                         "side": s.side, "line": "" if s.line is None else f"{s.line:g}",
                         "kambi": f"{s.kambi_american:+d}", "fair%": f"{s.fair_prob:.1%}",
                         "EV%": s.ev_pct, "books": s.n_sources, "verdict": s.verdict}
                        for s in all_signals]
                if rows: st.dataframe(rows, use_container_width=True, hide_index=True)
            st.markdown("---")

        shown = [s for s in all_signals if s.ev_pct >= min_ev and s.verdict != "NONE"]
        st.subheader(f"{len(shown)} candidate signal(s)")
        if not shown: st.info("No divergences above your threshold.")
        for s in shown:
            lt = "" if s.line is None else (f" {s.line:+g}" if s.market_type == "spread" else f" {s.line:g}")
            with st.container(border=True):
                verdict_row(st, f"**{s.verdict}** -- {s.away_team} @ {s.home_team}  \n"
                            f"{s.market_type.title()} - **{s.side.title()}{lt}** - Kambi {s.kambi_american:+d}",
                            s.ev_pct, s.fair_prob, s.n_sources, s.n_sharp, s.reasons, s.verdict)

    # ============ PITCHER PROPS ============
    else:
        # 1) which Kambi games are on the board (reuse game-line feed for ids+names)
        try:
            raw = fetch_kambi(league)
        except Exception as e:
            st.error(f"Kambi fetch failed: {type(e).__name__}: {e}"); st.stop()
        kambi_events = raw.get("events", [])
        # Map Kambi event id -> normalized team set, for pairing with Odds events.
        import re as _re
        def _teamset(name):
            parts = [p.strip() for p in name.replace("@", "-").split("-")]
            return frozenset(_re.sub(r"[^a-z0-9 ]", "", p.lower()).strip() for p in parts if p)

        # 2) Prop-source events (cheap) to get event ids for prop calls
        use_propline = prop_source.startswith("PropLine")
        prop_key = propline_key if use_propline else api_key
        if use_propline and not prop_key:
            st.error("Enter your PropLine API key in the sidebar."); st.stop()
        try:
            oa_events = (propline.list_events(league, prop_key)
                         if use_propline else list_events(league, api_key))
        except Exception as e:
            st.error(f"{prop_source} events failed: {type(e).__name__}: {e}"); st.stop()

        # match Kambi <-> Odds events by full team name AND nearest date.
        # The same matchup recurs on many dates; keying on teams alone would
        # grab a future game (no props yet). We keep every candidate and pick
        # the one whose start time is closest to the Kambi game's start.
        from datetime import datetime, timezone
        def _pt(s):
            try:
                return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                return None

        oa_by_key = {}  # team-key -> list of (id, commence_dt)
        for e in oa_events:
            key = frozenset({norm_player(e.get("home_team","")), norm_player(e.get("away_team",""))})
            oa_by_key.setdefault(key, []).append((e.get("id"), _pt(e.get("commence_time"))))

        pairs = []
        for wrapper in kambi_events:
            ev = wrapper.get("event", {})
            eng = ev.get("englishName", "")
            if " - " not in eng:
                continue
            home_full, away_full = [x.strip() for x in eng.split(" - ", 1)]
            key = frozenset({norm_player(home_full), norm_player(away_full)})
            cands = oa_by_key.get(key)
            if not cands:
                continue
            kstart = _pt(ev.get("start"))
            # choose candidate closest in time to the Kambi start (within 18h)
            best_id, best_gap = None, None
            for cid, cdt in cands:
                if kstart is None or cdt is None:
                    gap = 0
                else:
                    gap = abs((cdt - kstart).total_seconds())
                if best_gap is None or gap < best_gap:
                    best_id, best_gap = cid, gap
            if best_id is not None and (best_gap is None or best_gap <= 18 * 3600):
                pairs.append((ev.get("id"), best_id, f"{away_full} @ {home_full}"))

        st.success(f"{len(pairs)} games matched for prop scanning "
                   f"({len(kambi_events)} Kambi / {len(oa_events)} PropLine).")
        if not pairs:
            st.error(
                "0 games matched between Kambi and the prop source. Compare the "
                "two lists below — the same game should read identically. If a "
                "team name differs (or a Kambi game has no englishName), that's "
                "the mismatch to fix."
            )
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Kambi games (from feed)**")
                for wrapper in kambi_events:
                    ev = wrapper.get("event", {})
                    eng = ev.get("englishName", "")
                    nm = ev.get("name", "")
                    st.write(f"{nm}  \n<sub>englishName: {eng or '(none)'}</sub>",
                             unsafe_allow_html=True)
            with d2:
                st.markdown("**PropLine games (non-live, next 24h)**")
                shown = 0
                for e in oa_events:
                    if e.get("live"):
                        continue
                    st.write(f"{e.get('away_team')} @ {e.get('home_team')}  "
                             f"<sub>{e.get('commence_time','')}</sub>",
                             unsafe_allow_html=True)
                    shown += 1
                    if shown >= 20:
                        break
            st.stop()

        all_prop_signals = []
        prop_diag = []
        calls_used = 0
        for kambi_id, oa_id, label in pairs:
            try:
                kraw = fetch_event_props(kambi_id)
                kprops = parse_props(kraw)
            except Exception as e:
                prop_diag.append((label, f"kambi err: {type(e).__name__}", 0, 0))
                continue
            try:
                if use_propline:
                    oraw = propline.fetch_event_props(league, oa_id, prop_key)
                    oprops = propline.parse_props(oraw)
                else:
                    oraw = fetch_event_props_odds(league, oa_id, api_key, books)
                    oprops = parse_props_odds(oraw)
                calls_used += 1
            except Exception as e:
                prop_diag.append((label, f"odds err: {type(e).__name__}", len(kprops), 0))
                continue
            sigs = match_props(kprops, oprops, min_sources=int(min_sources))
            all_prop_signals.extend(sigs)
            prop_diag.append((label, "ok", len(kprops), len(oprops)))

        all_prop_signals.sort(key=lambda s: s.ev_pct, reverse=True)

        if show_diag:
            st.markdown("### Diagnostics")
            st.caption(f"Prop calls used this scan: {calls_used} "
                       f"(each costs more quota than a game-line scan).")
            st.dataframe(
                [{"game": g, "status": stt, "kambi props": kp, "odds props": op}
                 for g, stt, kp, op in prop_diag],
                use_container_width=True, hide_index=True)
            if all_prop_signals:
                st.dataframe(
                    [{"player": s.player, "stat": s.stat, "side": s.side, "line": s.line,
                      "kambi": f"{s.kambi_american:+d}", "fair%": f"{s.fair_prob:.1%}",
                      "EV%": s.ev_pct, "books": s.n_sources, "verdict": s.verdict}
                     for s in all_prop_signals],
                    use_container_width=True, hide_index=True)
            st.markdown("---")

        shown = [s for s in all_prop_signals if s.ev_pct >= min_ev and s.verdict != "NONE"]
        st.subheader(f"{len(shown)} candidate prop signal(s)")
        if not shown: st.info("No prop divergences above your threshold.")
        for s in shown:
            with st.container(border=True):
                verdict_row(st, f"**{s.verdict}** -- {s.player}  \n"
                            f"{s.stat.title()} - **{s.side.title()} {s.line:g}** - Kambi {s.kambi_american:+d}",
                            s.ev_pct, s.fair_prob, s.n_sources, s.n_sharp, s.reasons, s.verdict)
else:
    st.info("Pick a mode, set your key, and press **Scan now**.")
