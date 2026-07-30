"""
Potawatomi Line Scanner -- Streamlit dashboard.

Two modes:
  * Game lines  -- moneyline / run line / total vs sharp-book no-vig fair.
  * Pitcher props -- strikeouts / outs vs sharp-book no-vig fair.

Both share the same discipline: compare only like-for-like (same market,
side, and number), de-vig the sharp books to a fair price, and quarantine
big edges that fail validation rather than presenting them as bets.
"""
import os
import traceback
from datetime import datetime, timezone

import streamlit as st

from app.kambi_client import fetch_kambi, parse_kambi
from app.odds_client import fetch_odds, parse_odds, DEFAULT_BOOKS
from app.matcher import match, kambi_event_index, odds_event_index
from app.props import fetch_event_props, parse_props, norm_player
from app.props_odds import (
    list_events, fetch_event_props_odds, parse_props_odds, match_props,
)
from app import propline

st.set_page_config(page_title="Potawatomi Line Scanner", page_icon="🎯",
                   layout="wide")

# ---------------------------------------------------------------- styling
st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 1200px; }
  #MainMenu, footer { visibility: hidden; }
  .pl-title { font-size: 1.9rem; font-weight: 800; letter-spacing:-.02em;
              margin-bottom:.1rem; }
  .pl-sub   { color:#6b7280; font-size:.9rem; margin-bottom:1.2rem; }
  .badge { display:inline-block; padding:2px 10px; border-radius:999px;
           font-size:.72rem; font-weight:700; letter-spacing:.03em;
           text-transform:uppercase; }
  .b-extreme  { background:#ede9fe; color:#6d28d9; }
  .b-major    { background:#dcfce7; color:#15803d; }
  .b-edge     { background:#dbeafe; color:#1d4ed8; }
  .b-quar     { background:#fef3c7; color:#b45309; }
  .card { border:1px solid #e5e7eb; border-radius:14px; padding:14px 16px;
          margin-bottom:10px; background:#fff; }
  .card h4 { margin:.1rem 0 .35rem 0; font-size:1.02rem; }
  .ev-pos { color:#15803d; font-weight:800; }
  .ev-neg { color:#b91c1c; font-weight:800; }
  .muted { color:#6b7280; font-size:.82rem; }
  .pill { display:inline-block; background:#f3f4f6; color:#374151;
          border-radius:8px; padding:1px 8px; font-size:.75rem; margin-right:6px;}
  .live { color:#b91c1c; font-weight:700; }
  .soon { color:#15803d; font-weight:700; }
</style>
""", unsafe_allow_html=True)

_BADGE = {"EXTREME VERIFIED": "b-extreme", "MAJOR OUTLIER": "b-major",
          "EDGE": "b-edge", "QUARANTINE": "b-quar"}


def badge(v):
    return f'<span class="badge {_BADGE.get(v,"b-edge")}">{v}</span>'


def ev_span(ev):
    cls = "ev-pos" if ev >= 0 else "ev-neg"
    return f'<span class="{cls}">{ev:+.1f}%</span>'


def _pt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def time_to_pitch(dt):
    """Human 'starts in' string + whether it's in the scannable window."""
    if dt is None:
        return "", False
    now = datetime.now(timezone.utc)
    mins = (dt - now).total_seconds() / 60
    if mins <= 0:
        return '<span class="live">● LIVE / started</span>', False
    if mins < 60:
        return f'<span class="soon">starts in {int(mins)}m</span>', True
    if mins < 180:
        return f'<span class="soon">starts in {int(mins//60)}h {int(mins%60)}m</span>', True
    h = mins / 60
    return f'starts in {h:.0f}h', False


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    mode = st.radio("Scan mode", ["Game lines", "Pitcher props"])
    league = st.selectbox("League", ["MLB", "WNBA"])

    use_propline = False
    if mode == "Pitcher props":
        prop_source = st.selectbox("Prop odds source",
                                   ["PropLine (free)", "The Odds API"])
        use_propline = prop_source.startswith("PropLine")

    # Only ask for the key that's actually needed.
    need_oddsapi = (mode == "Game lines") or (mode == "Pitcher props" and not use_propline)
    api_key = ""
    propline_key = ""
    if need_oddsapi:
        api_key = st.text_input("The Odds API key",
                                value=os.getenv("ODDS_API_KEY", ""), type="password")
    if mode == "Pitcher props" and use_propline:
        propline_key = st.text_input("PropLine API key",
                                     value=os.getenv("PROPLINE_API_KEY", ""), type="password")

    books = st.text_input("Comparison books",
                          value=os.getenv("ODDS_API_BOOKMAKERS", DEFAULT_BOOKS))
    default_min = 2 if (mode == "Pitcher props" and use_propline) else 3
    min_sources = st.number_input("Min comparison books", 1, 10, default_min)
    min_ev = st.number_input("Min EV % to display", 0.0, 50.0,
                             float(os.getenv("MIN_EV_MAIN_PCT", "1")), step=0.5)
    show_diag = st.checkbox("Show diagnostics", value=True)
    st.markdown("---")
    st.caption(
        "Confirm every signal on the kiosk before betting: exact player/teams, "
        "full-game, the number, and the odds. A big edge is far more often a "
        "stale or about-to-move line than free money."
    )

# ---------------------------------------------------------------- header
st.markdown('<div class="pl-title">🎯 Potawatomi Line Scanner</div>',
            unsafe_allow_html=True)
st.markdown(f'<div class="pl-sub">{mode} · {league} · comparing Potawatomi '
            f'against a no-vig sharp consensus</div>', unsafe_allow_html=True)

scan = st.button("Scan now", type="primary", use_container_width=False)


def render_signal_card(title_html, ev_pct, fair_prob, n_sources, n_sharp,
                       reasons, verdict, extra=""):
    reason_txt = " · ".join(reasons) if reasons else ""
    quar = ('<div class="muted" style="color:#b45309;margin-top:6px">'
            '⚠ Do not bet on this alone — flagged as a likely data artifact.</div>'
            if verdict == "QUARANTINE" else "")
    st.markdown(
        f'<div class="card">{badge(verdict)} {extra}'
        f'<h4>{title_html}</h4>'
        f'<div>{ev_span(ev_pct)} &nbsp; <span class="muted">fair {fair_prob:.1%} · '
        f'{n_sources} books ({n_sharp} sharp)</span></div>'
        f'<div class="muted" style="margin-top:4px">{reason_txt}</div>'
        f'{quar}</div>', unsafe_allow_html=True)


def summary_strip(counts: dict):
    cols = st.columns(len(counts))
    for c, (label, val) in zip(cols, counts.items()):
        c.metric(label, val)


# ================================================================ SCAN
if scan:
    # ---- key presence checks (only what this mode needs) ----
    if need_oddsapi and not api_key:
        st.error("Enter your The Odds API key in the sidebar."); st.stop()
    if mode == "Pitcher props" and use_propline and not propline_key:
        st.error("Enter your PropLine API key in the sidebar."); st.stop()

    # ============================================ GAME LINES
    if mode == "Game lines":
        try:
            kambi_outcomes = parse_kambi(fetch_kambi(league), league)
        except Exception as e:
            st.error(f"Kambi fetch failed: {type(e).__name__}: {e}")
            with st.expander("Details"): st.code(traceback.format_exc())
            st.stop()
        try:
            odds_outcomes = parse_odds(fetch_odds(league, api_key, books))
        except Exception as e:
            st.error(f"Odds API fetch failed: {type(e).__name__}: {e}"); st.stop()

        all_signals = match(kambi_outcomes, odds_outcomes, min_sources=int(min_sources))
        k_idx, o_idx = kambi_event_index(kambi_outcomes), odds_event_index(odds_outcomes)
        matched = set(k_idx) & set(o_idx)

        if show_diag:
            summary_strip({"Kambi outcomes": len(kambi_outcomes),
                           "Kambi games": len(k_idx), "Sharp games": len(o_idx),
                           "Matched": len(matched), "Comparisons": len(all_signals)})
            if not matched:
                st.error("0 games matched — a name/schedule mismatch. Lists below:")
                d1, d2 = st.columns(2)
                d1.markdown("**Kambi**"); [d1.write(f"{a} @ {h}") for a, h in sorted(k_idx.values())]
                d2.markdown("**Sharp books**"); [d2.write(f"{a} @ {h}") for a, h in sorted(o_idx.values())]
            else:
                with st.expander("All comparisons (including no-edge)", expanded=False):
                    st.dataframe(
                        [{"matchup": f"{s.away_team} @ {s.home_team}",
                          "market": s.market_type, "side": s.side,
                          "line": "" if s.line is None else f"{s.line:g}",
                          "kambi": f"{s.kambi_american:+d}", "fair%": f"{s.fair_prob:.1%}",
                          "EV%": s.ev_pct, "books": s.n_sources, "verdict": s.verdict}
                         for s in all_signals],
                        use_container_width=True, hide_index=True)
            st.markdown("")

        shown = [s for s in all_signals if s.ev_pct >= min_ev and s.verdict != "NONE"]
        st.subheader(f"{len(shown)} candidate signal(s)")
        if not shown:
            st.info("No divergences above your threshold right now — that's the "
                    "normal, healthy state. Value is the exception.")
        for s in shown:
            lt = "" if s.line is None else (f" {s.line:+g}" if s.market_type == "spread" else f" {s.line:g}")
            title = (f"{s.away_team} @ {s.home_team} — {s.market_type.title()} "
                     f"<b>{s.side.title()}{lt}</b> @ Kambi {s.kambi_american:+d}")
            render_signal_card(title, s.ev_pct, s.fair_prob, s.n_sources,
                               s.n_sharp, s.reasons, s.verdict)

    # ============================================ PITCHER PROPS
    else:
        try:
            raw = fetch_kambi(league)
        except Exception as e:
            st.error(f"Kambi fetch failed: {type(e).__name__}: {e}"); st.stop()
        kambi_events = raw.get("events", [])
        prop_key = propline_key if use_propline else api_key
        src_name = "PropLine" if use_propline else "The Odds API"

        try:
            oa_events = (propline.list_events(league, prop_key)
                         if use_propline else list_events(league, api_key))
        except Exception as e:
            st.error(f"{src_name} events failed: {type(e).__name__}: {e}"); st.stop()

        # index sharp events by team-key -> [(id, commence_dt)]
        oa_by_key = {}
        for e in oa_events:
            key = frozenset({norm_player(e.get("home_team", "")),
                             norm_player(e.get("away_team", ""))})
            oa_by_key.setdefault(key, []).append((e.get("id"), _pt(e.get("commence_time"))))

        # pair Kambi <-> sharp on team-key AND nearest start time; dedupe.
        pairs, seen = [], set()
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
            best, best_gap, best_dt = None, None, None
            for cid, cdt in cands:
                gap = 0 if (kstart is None or cdt is None) else abs((cdt - kstart).total_seconds())
                if best_gap is None or gap < best_gap:
                    best, best_gap, best_dt = cid, gap, cdt
            if best is None or (best_gap is not None and best_gap > 18 * 3600):
                continue
            if best in seen:        # dedupe doubleheader / duplicate rows
                continue
            seen.add(best)
            pairs.append((ev.get("id"), best, f"{away_full} @ {home_full}", best_dt or kstart))

        if not pairs:
            st.error("0 games matched between Kambi and the prop source. "
                     "Compare the two lists to spot a name mismatch:")
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Kambi games**")
                for w in kambi_events:
                    e2 = w.get("event", {})
                    st.write(f"{e2.get('name','')}  \n<span class='muted'>"
                             f"{e2.get('englishName','(none)')}</span>",
                             unsafe_allow_html=True)
            with d2:
                st.markdown("**Prop-source games (non-live)**")
                n = 0
                for e in oa_events:
                    if e.get("live"): continue
                    st.write(f"{e.get('away_team')} @ {e.get('home_team')}  "
                             f"<span class='muted'>{e.get('commence_time','')}</span>",
                             unsafe_allow_html=True)
                    n += 1
                    if n >= 25: break
            st.stop()

        # fetch + match each paired game
        all_sigs, diag, calls = [], [], 0
        prog = st.progress(0.0, text="Scanning games…")
        for i, (kambi_id, oa_id, label, gdt) in enumerate(pairs):
            tstr, _ = time_to_pitch(gdt)
            try:
                kprops = parse_props(fetch_event_props(kambi_id))
            except Exception as e:
                diag.append((label, tstr, f"kambi err: {type(e).__name__}", 0, 0)); continue
            try:
                if use_propline:
                    oprops = propline.parse_props(propline.fetch_event_props(league, oa_id, prop_key))
                else:
                    oprops = parse_props_odds(fetch_event_props_odds(league, oa_id, api_key, books))
                calls += 1
            except Exception as e:
                diag.append((label, tstr, f"src err: {type(e).__name__}", len(kprops), 0)); continue
            sigs = match_props(kprops, oprops, min_sources=int(min_sources))
            for s in sigs:
                s._game = label; s._when = gdt
            all_sigs.extend(sigs)
            diag.append((label, tstr, "ok", len(kprops), len(oprops)))
            prog.progress((i + 1) / len(pairs), text=f"Scanning… {label}")
        prog.empty()

        # dedupe signals by (player, stat, side, line) keeping best EV
        best_by = {}
        for s in all_sigs:
            k = (s.player.lower(), s.stat, s.side, round(s.line, 1))
            if k not in best_by or s.ev_pct > best_by[k].ev_pct:
                best_by[k] = s
        all_sigs = sorted(best_by.values(), key=lambda s: s.ev_pct, reverse=True)

        if show_diag:
            summary_strip({"Games matched": len(pairs), "Prop calls": calls,
                           "Comparisons": len(all_sigs),
                           "Scannable now": sum(1 for _, t, *_ in diag if "starts in" in t)})
            with st.expander("Per-game status & timing", expanded=not all_sigs):
                st.markdown(
                    "".join(f"<div style='padding:3px 0'>{g} &nbsp; {t} &nbsp; "
                            f"<span class='pill'>{stt}</span>"
                            f"<span class='muted'>kambi {kp} · src {op}</span></div>"
                            for g, t, stt, kp, op in diag),
                    unsafe_allow_html=True)
            if all_sigs:
                with st.expander("All comparisons (including no-edge)", expanded=False):
                    st.dataframe(
                        [{"player": s.player, "stat": s.stat, "side": s.side,
                          "line": s.line, "kambi": f"{s.kambi_american:+d}",
                          "fair%": f"{s.fair_prob:.1%}", "EV%": s.ev_pct,
                          "books": s.n_sources, "verdict": s.verdict}
                         for s in all_sigs], use_container_width=True, hide_index=True)
            st.markdown("")

        shown = [s for s in all_sigs if s.ev_pct >= min_ev and s.verdict != "NONE"]
        st.subheader(f"{len(shown)} candidate prop signal(s)")
        if not shown:
            st.info("No prop divergences above your threshold right now. Scan again "
                    "30–90 min before first pitch, when boards are up and sharp.")
        for s in shown:
            when, _ = time_to_pitch(getattr(s, "_when", None))
            game = getattr(s, "_game", "")
            extra = f'<span class="muted" style="float:right">{when}</span>'
            title = (f"{s.player} — {s.stat.title()} <b>{s.side.title()} {s.line:g}</b> "
                     f"@ Kambi {s.kambi_american:+d}<br>"
                     f"<span class='muted'>{game}</span>")
            render_signal_card(title, s.ev_pct, s.fair_prob, s.n_sources,
                               s.n_sharp, s.reasons, s.verdict, extra=extra)
else:
    st.markdown('<div class="pl-title">🎯 Potawatomi Line Scanner</div>',
                unsafe_allow_html=True)
    st.info("Pick a mode and league, add your key, and press **Scan now**. "
            "Tip: scan 30–90 minutes before first pitch for the best coverage.")
