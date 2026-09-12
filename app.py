from __future__ import annotations

import logging
import os
import random
import sys
from datetime import datetime, timedelta, timezone

import bcrypt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from charts import _make_grid, _plotly_bar, _calendar_heatmap, _plotly_hbar
from styles import _inject_css
from utils import _cover, _decade, _format_last_sync, _pressing_badge

# Make sure imports resolve when running from the vinyl/ directory
sys.path.insert(0, os.path.dirname(__file__))

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

import database as db
import discogs_api as api
import vinyl_api_client

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

st.set_page_config(
    page_title="Vinyl Catalog",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Init DB ───────────────────────────────────────────────────────────────────

db.init_db()

# ── Login gate ────────────────────────────────────────────────────────────────

def _show_login() -> None:
    # ── Login background: dimly lit record store / crate digging ─────────────
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background-image: url("https://images.unsplash.com/photo-1601643157091-ce5c665179ab?w=1600&q=80&auto=format&fit=crop");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        }
        [data-testid="stAppViewContainer"]::before {
            content: "";
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.60);
            z-index: 0;
        }
        /* Lift login card above the overlay */
        [data-testid="stVerticalBlock"] {
            position: relative;
            z-index: 1;
        }
        /* Semi-transparent frosted card behind the form */
        [data-testid="stForm"] {
            background: rgba(22, 22, 22, 0.82) !important;
            border: 1px solid rgba(232, 184, 75, 0.25) !important;
            border-radius: 12px !important;
            padding: 24px !important;
        }
        /* Login page title */
        h2 { color: #e8b84b !important; letter-spacing: 0.04em; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<h2 style='text-align:center;margin-top:3rem'>Vinyl Catalog</h2>",
        unsafe_allow_html=True,
    )

    col_l, col_c, col_r = st.columns([2, 3, 2])
    with col_c:
        tab_signin, tab_register = st.tabs(["Sign In", "Create Account"])

        # ── Sign In ──────────────────────────────────────────────────────────
        with tab_signin:
            with st.form("login_form", clear_on_submit=False):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign In", use_container_width=True)
            if submitted:
                with db.get_master_conn() as _conn:
                    _row = db.get_user_by_username(_conn, username.strip())
                try:
                    _password_ok = _row and bcrypt.checkpw(
                        password.encode(), _row["password_hash"].encode()
                    )
                except ValueError:
                    # A malformed/non-bcrypt password_hash (e.g. from a
                    # provisioning script bug) must not crash the login form.
                    _password_ok = False
                if _password_ok:
                    st.session_state["current_user"] = dict(_row)
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

        # ── Create Account ───────────────────────────────────────────────────
        with tab_register:
            with st.form("register_form", clear_on_submit=False):
                reg_username        = st.text_input("Username")
                reg_password        = st.text_input("Password", type="password",
                                                     help="Minimum 8 characters")
                reg_password_confirm = st.text_input("Confirm Password", type="password")
                reg_discogs_token   = st.text_input("Discogs Token",
                                                     help="Settings → Developers on discogs.com")
                reg_discogs_username = st.text_input("Discogs Username",
                                                      help="Your username on discogs.com")
                reg_email           = st.text_input(
                    "Alert Email (optional)",
                    help="Where daily new-listing alerts are sent",
                )
                reg_code            = st.text_input("Registration Code", type="password")
                reg_submitted       = st.form_submit_button("Create Account",
                                                             use_container_width=True)

            if reg_submitted:
                _expected_code = os.environ.get("REGISTRATION_CODE", "")
                _errors: list[str] = []

                if not reg_username.strip():
                    _errors.append("Username is required.")
                if len(reg_password) < 8:
                    _errors.append("Password must be at least 8 characters.")
                if reg_password != reg_password_confirm:
                    _errors.append("Passwords do not match.")
                if not reg_discogs_token.strip():
                    _errors.append("Discogs Token is required.")
                if not reg_discogs_username.strip():
                    _errors.append("Discogs Username is required.")
                if not _expected_code:
                    _errors.append("Registration is not currently open.")
                elif reg_code != _expected_code:
                    _errors.append("Invalid registration code.")

                if not _errors:
                    with db.get_master_conn() as _conn:
                        _existing = db.get_user_by_username(_conn, reg_username.strip())
                    if _existing:
                        _errors.append(f"Username '{reg_username.strip()}' is already taken.")

                if _errors:
                    for _e in _errors:
                        st.error(_e)
                else:
                    _pw_hash  = bcrypt.hashpw(reg_password.encode(), bcrypt.gensalt()).decode()
                    _db_path  = f"vinyl_{reg_username.strip()}.db"
                    with db.get_master_conn() as _conn:
                        db.create_user(
                            _conn,
                            reg_username.strip(),
                            _pw_hash,
                            reg_discogs_token.strip(),
                            reg_discogs_username.strip(),
                            _db_path,
                            reg_email.strip() or None,
                        )
                    db.init_user_db(_db_path)
                    with db.get_master_conn() as _conn:
                        _new_user = db.get_user_by_username(_conn, reg_username.strip())
                    st.session_state["current_user"] = dict(_new_user)
                    st.rerun()


if "current_user" not in st.session_state:
    _show_login()
    st.stop()

# ── User context (set once per session after login) ───────────────────────────

_user      = st.session_state["current_user"]
_user_db   = _user["db_path"]   # filename resolved by db.get_conn()
_user_token = _user["discogs_token"]
_user_dname = _user["discogs_username"]

# ── CSS Injection ─────────────────────────────────────────────────────────────

_inject_css()

# ── Data loader (defined early so .clear() is callable anywhere) ─────────────

@st.cache_data(ttl=300)
def _load_collection_data(db_path: str) -> tuple[list[dict], dict[int, dict], dict[int, dict]]:
    with db.get_conn(db_path) as conn:
        releases    = db.get_all_releases(conn)
        play_stats  = db.get_play_stats(conn)
        custom_tags = db.get_all_custom_tags(conn)
    return releases, play_stats, custom_tags


def _get_ct(release_id: int) -> dict:
    return _custom_tags_cache.get(
        release_id,
        {"release_id": release_id, "custom_tags": [], "notes": "", "decade_override": None},
    )


# ── Startup sync check ────────────────────────────────────────────────────────

if "startup_sync_done" not in st.session_state:
    st.session_state["startup_sync_done"] = True
    if _user_token:
        try:
            with db.get_conn(_user_db) as conn:
                meta = db.get_sync_meta(conn)
            with db.get_conn(_user_db) as conn:
                releases = db.get_all_releases(conn)
            if not releases:
                st.session_state["show_empty_warning"] = True
            else:
                last = meta.get("last_full_sync")
                if last:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
                    if age >= timedelta(hours=24):
                        st.session_state["auto_sync_needed"] = True
        except Exception:
            pass

# ── Refresh All dialog ────────────────────────────────────────────────────────

@st.dialog("Refresh All", width="large")
def _run_refresh_all(user_token: str, user_dname: str, user_db: str, vinyl_items: list) -> None:

    # ── Step 1: Sync Collection ───────────────────────────────────────────────
    st.markdown("**Step 1 / 4 — Sync Collection**")
    _p1 = st.progress(0)
    _n1 = st.empty()
    _n1.caption("Syncing collection from Discogs…")
    try:
        _client = api.make_client(user_token)
        _count = api.sync_collection(
            force=True, log_fn=lambda _m: None,
            client=_client, discogs_username=user_dname, db_path=user_db,
        )
        _load_collection_data.clear()
        st.session_state.pop("show_empty_warning", None)
        st.session_state.pop("auto_sync_needed", None)
    except Exception as e:
        _p1.progress(100)
        st.error(f"Step 1 (Sync Collection) failed: {e}")
        return
    _p1.progress(100)
    _n1.caption(f"✓ {_count} records synced")

    # ── Step 2: Sync Wantlist ─────────────────────────────────────────────────
    st.markdown("**Step 2 / 4 — Sync Wantlist**")
    _p2 = st.progress(0)
    _n2 = st.empty()
    _n2.caption("Syncing wantlist from Discogs…")
    try:
        _client = api.make_client(user_token)
        _n = api.sync_wantlist(
            force=True, log_fn=lambda _m: None,
            client=_client, discogs_username=user_dname, db_path=user_db,
        )
    except Exception as e:
        _p2.progress(100)
        st.error(f"Step 2 (Sync Wantlist) failed: {e}")
        return
    _p2.progress(100)
    _n2.caption(f"✓ {_n} wantlist items synced")

    # ── Step 3: Fetch Listings ────────────────────────────────────────────────
    st.markdown("**Step 3 / 4 — Fetch Listings**")
    if not vinyl_items:
        st.progress(100)
        st.caption("⚠ No vinyl items in wantlist — skipped.")
    else:
        _p3 = st.progress(0)
        _n3 = st.empty()
        try:
            _ts = datetime.utcnow().isoformat()
            with db.get_conn(user_db) as _conn:
                for _i, _w in enumerate(vinyl_items):
                    _pct = int(100 * _i / len(vinyl_items))
                    _p3.progress(_pct)
                    _n3.caption(f"[{_i + 1}/{len(vinyl_items)}] {_w['artist']} — {_w['title']}")
                    _fetched = api.fetch_release_listings(_w["release_id"], token=user_token, db_path=user_db)
                    _rows = [
                        {
                            "listing_id":       _lst.get("listing_id"),
                            "release_id":       _w["release_id"],
                            "price":            _lst.get("price"),
                            "currency":         _lst.get("currency"),
                            "condition":        _lst.get("condition"),
                            "sleeve_condition": _lst.get("sleeve"),
                            "ships_from":       _lst.get("ships_from"),
                            "seller":           _lst.get("seller"),
                            "listing_url":      _lst.get("url"),
                            "last_fetched":     _ts,
                            "price_usd":        _lst.get("price_usd"),
                            "ships_to_us":      _lst.get("ships_to_us"),
                            "shipping_notes":   _lst.get("shipping_notes"),
                        }
                        for _lst in _fetched if _lst.get("listing_id") is not None
                    ]
                    db.bulk_upsert_wantlist_listings(_conn, _w["release_id"], _rows)
        except Exception as e:
            _p3.progress(100)
            st.error(f"Step 3 (Fetch Listings) failed: {e}")
            return
        _p3.progress(100)
        _n3.caption(f"✓ Listings fetched for {len(vinyl_items)} item(s)")

    # ── Step 4: Refresh Suggestions ───────────────────────────────────────────
    st.markdown("**Step 4 / 4 — Refresh Suggestions**")
    _p4 = st.progress(0)
    _n4 = st.empty()
    _n4.caption("Searching Discogs for suggestions…")
    try:
        _client = api.make_client(user_token)
        _sug_count = api.fetch_suggestions(
            force=True, log_fn=lambda _m: None,
            client=_client, db_path=user_db,
        )
    except Exception as e:
        _p4.progress(100)
        st.error(f"Step 4 (Refresh Suggestions) failed: {e}")
        return
    _p4.progress(100)
    _n4.caption(f"✓ {_sug_count} suggestions found")
    st.rerun()


# ── Main controls ─────────────────────────────────────────────────────────────

with db.get_conn(_user_db) as _conn:
    _meta = db.get_sync_meta(_conn)

with db.get_conn(_user_db) as _wl_conn:
    _wantlist = db.get_wantlist(_wl_conn)

_VINYL_FORMATS = {"Vinyl", "LP", '7"', '10"', '12"'}
_vinyl_items = [
    w for w in _wantlist
    if not w.get("format") or any(v in (w.get("format") or "") for v in _VINYL_FORMATS)
]

_h1, _h2, _h3 = st.columns([5, 2, 1])
with _h1:
    st.markdown("### Vinyl Catalog")
    st.caption(f"Signed in as **{_user['username']}**")
with _h2:
    if st.button("Refresh All", use_container_width=True, type="primary"):
        _run_refresh_all(_user_token, _user_dname, _user_db, _vinyl_items)
with _h3:
    if st.button("Logout", use_container_width=True):
        del st.session_state["current_user"]
        st.session_state.pop("startup_sync_done", None)
        _load_collection_data.clear()
        st.rerun()

st.caption(
    f"Collection: {_format_last_sync(_meta.get('last_full_sync'))}  |  "
    f"Wantlist: {_format_last_sync(_meta.get('last_wantlist_sync'))}  |  "
    f"Suggestions: {_format_last_sync(_meta.get('last_suggestions_fetch'))}"
)
if st.session_state.get("auto_sync_needed"):
    st.info("Collection data is > 24h old.")
st.divider()

# ── Load data ─────────────────────────────────────────────────────────────────

_all_releases, _play_stats, _custom_tags_cache = _load_collection_data(_user_db)



# ── Empty state warning ───────────────────────────────────────────────────────

if st.session_state.get("show_empty_warning"):
    st.warning(
        "Your collection is empty. Click **Refresh All** above to import "
        "your Discogs collection. Make sure `DISCOGS_TOKEN` and `DISCOGS_USERNAME` "
        "are set in `vinyl/.env`."
    )

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_browse, tab_wantlist, tab_playlog, tab_stats, tab_discover = st.tabs(
    ["Browse", "Wantlist Listings", "Play Log", "Stats", "Discover"]
)

# ═════════════════════════════════════════════════════════════════════════════
# TAB: BROWSE
# ═════════════════════════════════════════════════════════════════════════════

with tab_browse:
    if not _all_releases:
        st.info("No records found. Use Refresh All above to import your collection.")
    else:
        from collections import Counter as _Counter

        genre_counter:  _Counter = _Counter()
        decade_counter: _Counter = _Counter()
        tag_counter:    _Counter = _Counter()

        for r in _all_releases:
            for g in r["discogs_genres"]:
                genre_counter[g] += 1
            ct = _get_ct(r["release_id"])
            d  = _decade(r["year"], ct.get("decade_override"))
            if d:
                decade_counter[d] += 1
            for t in ct.get("custom_tags", []):
                tag_counter[t] += 1

        all_genres      = set(genre_counter)
        all_decades     = set(decade_counter)
        all_custom_tags = set(tag_counter)

        genre_opts  = sorted(f"{g} ({genre_counter[g]})"  for g in all_genres)
        decade_opts = sorted(f"{d} ({decade_counter[d]})" for d in all_decades)
        tag_opts    = sorted(f"{t} ({tag_counter[t]})"    for t in all_custom_tags)

        def _strip_count(label: str) -> str:
            return label.rsplit(" (", 1)[0]

        # ── Inline filters ────────────────────────────────────────────────────
        with st.expander("Filters", expanded=False):
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                sel_genre_labels  = st.multiselect("Genre",  genre_opts,  key="br_genres")
            with fc2:
                sel_decade_labels = st.multiselect("Decade", decade_opts, key="br_decades")
            with fc3:
                sel_tag_labels    = st.multiselect("Tag",    tag_opts,    key="br_tags")

        sel_genres  = [_strip_count(l) for l in sel_genre_labels]
        sel_decades = [_strip_count(l) for l in sel_decade_labels]
        sel_tags    = [_strip_count(l) for l in sel_tag_labels]

        # ── Search + controls row ─────────────────────────────────────────────
        sc1, sc2, sc3, sc4, sc5 = st.columns([5, 2, 1, 1, 1])
        with sc1:
            search_query = st.text_input(
                "Search",
                placeholder="Search title, artist, or tags…",
                label_visibility="collapsed",
                key="browse_search",
            )
        with sc2:
            _SORT_OPTIONS = [
                "Artist A→Z", "Artist Z→A",
                "Year (oldest)", "Year (newest)",
                "Most played", "Most played this month", "Recently played", "Never played first",
            ]
            sel_sort = st.selectbox(
                "Sort", _SORT_OPTIONS, key="browse_sort", label_visibility="collapsed"
            )
        with sc3:
            n_cols = st.select_slider(
                "Cols", options=[1, 2, 3, 4], value=3, key="browse_cols",
                label_visibility="collapsed",
            )
        with sc4:
            _view_mode = st.session_state.get("view_mode", "Grid")
            if st.button(
                "List" if _view_mode == "Grid" else "Grid",
                use_container_width=True,
            ):
                st.session_state["view_mode"] = "List" if _view_mode == "Grid" else "Grid"
                st.rerun()
        with sc5:
            if st.button("Random", use_container_width=True, help="Pick a random album"):
                st.session_state["trigger_random"] = True
        view_mode = st.session_state.get("view_mode", "Grid")

        # ── Filter + sort ─────────────────────────────────────────────────────
        filtered = []
        for r in _all_releases:
            ct     = _get_ct(r["release_id"])
            decade = _decade(r["year"], ct.get("decade_override"))
            if search_query:
                q = search_query.lower()
                haystack = " ".join([
                    r.get("title") or "",
                    r.get("artist") or "",
                    " ".join(ct.get("custom_tags", [])),
                ]).lower()
                if q not in haystack:
                    continue
            if sel_genres  and not any(g in r["discogs_genres"]      for g in sel_genres):
                continue
            if sel_decades and decade not in sel_decades:
                continue
            if sel_tags    and not any(t in ct.get("custom_tags", []) for t in sel_tags):
                continue
            filtered.append((r, ct))

        if sel_sort == "Artist A→Z":
            filtered.sort(key=lambda x: (x[0].get("artist") or "").lower())
        elif sel_sort == "Artist Z→A":
            filtered.sort(key=lambda x: (x[0].get("artist") or "").lower(), reverse=True)
        elif sel_sort == "Year (oldest)":
            filtered.sort(key=lambda x: x[0].get("year") or 0)
        elif sel_sort == "Year (newest)":
            filtered.sort(key=lambda x: x[0].get("year") or 0, reverse=True)
        elif sel_sort == "Most played":
            filtered.sort(
                key=lambda x: _play_stats.get(x[0]["release_id"], {}).get("count", 0),
                reverse=True,
            )
        elif sel_sort == "Recently played":
            filtered.sort(
                key=lambda x: _play_stats.get(x[0]["release_id"], {}).get("last_played", ""),
                reverse=True,
            )
        elif sel_sort == "Most played this month":
            _month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            with db.get_conn(_user_db) as conn:
                _monthly_stats = db.get_play_stats_since(conn, _month_start)
            filtered.sort(
                key=lambda x: _monthly_stats.get(x[0]["release_id"], {}).get("count", 0),
                reverse=True,
            )
        elif sel_sort == "Never played first":
            filtered.sort(key=lambda x: (
                1 if x[0]["release_id"] in _play_stats else 0,
                (x[0].get("artist") or "").lower(),
            ))

        # ── Random pick ───────────────────────────────────────────────────────
        if st.session_state.pop("trigger_random", False) and filtered:
            st.session_state["random_pick"] = random.choice(filtered)[0]["release_id"]

        if st.session_state.get("random_pick"):
            _pick_id    = st.session_state["random_pick"]
            _pick_match = next(
                ((r, ct) for r, ct in filtered if r["release_id"] == _pick_id), None
            )
            if _pick_match:
                r_p, ct_p = _pick_match
                with st.container(border=True):
                    pc1, pc2, pc3 = st.columns([1, 6, 2])
                    with pc1:
                        _cover(r_p.get("cover_url"), width=80)
                    with pc2:
                        st.markdown(f"**Random Pick:** {r_p['title']} — {r_p['artist']}")
                        st.caption(f"{r_p['year'] or '?'} · {', '.join(r_p['discogs_genres']) or '—'}")
                    with pc3:
                        if st.button("Log Play", key="random_log_play", use_container_width=True):
                            with db.get_conn(_user_db) as conn:
                                db.log_play(conn, r_p["release_id"])
                            try:
                                vinyl_api_client.log_play(
                                    _user["user_id"], r_p["release_id"],
                                    played_at=datetime.utcnow().isoformat(), source="streamlit",
                                )
                            except Exception:
                                logging.getLogger(__name__).warning("Failed to POST play to Vinyl API")
                            st.session_state.pop("random_pick", None)
                            _load_collection_data.clear()
                            st.rerun()
                        if st.button("Dismiss", key="random_dismiss", use_container_width=True):
                            st.session_state.pop("random_pick", None)
                            st.rerun()
            else:
                st.session_state.pop("random_pick", None)

        st.caption(f"Showing {len(filtered)} of {len(_all_releases)} records")

        # ── List view ─────────────────────────────────────────────────────────
        if view_mode == "List":
            for r, ct in filtered:
                stats       = _play_stats.get(r["release_id"], {})
                play_count  = stats.get("count", 0)
                last_played = stats.get("last_played", "")
                decade      = _decade(r["year"], ct.get("decade_override"))
                genres_str  = ", ".join(r["discogs_genres"]) or "—"
                custom_str  = " · ".join(ct.get("custom_tags", []))
                lc1, lc2, lc3, lc4 = st.columns([1, 5, 4, 1])
                with lc1:
                    _cover(r.get("cover_url"), width=55)
                with lc2:
                    st.markdown(f"**{r['title']}**")
                    st.caption(r.get("artist", ""))
                with lc3:
                    st.caption(f"{r['year'] or '?'} · {decade or '?'} · {genres_str}")
                    if custom_str:
                        st.caption(f"{custom_str}")
                    _badge = _pressing_badge(r)
                    if _badge:
                        st.caption(_badge)
                    if play_count:
                        lp_str = last_played[:10] if last_played else ""
                        st.caption(f"{play_count}x · last {lp_str}")
                with lc4:
                    if st.button("Log", key=f"qplay_list_{r['release_id']}_{r['instance_id']}", help="Log play"):
                        with db.get_conn(_user_db) as conn:
                            db.log_play(conn, r["release_id"])
                        try:
                            vinyl_api_client.log_play(
                                _user["user_id"], r["release_id"],
                                played_at=datetime.utcnow().isoformat(), source="streamlit",
                            )
                        except Exception:
                            logging.getLogger(__name__).warning("Failed to POST play to Vinyl API")
                        _load_collection_data.clear()
                        st.rerun()
                    with st.expander("Edit"):
                        _existing = ct.get("custom_tags", [])
                        _new_tags = st.multiselect(
                            "Tags",
                            options=sorted(all_custom_tags | set(_existing)),
                            default=_existing,
                            key=f"tags_list_{r['release_id']}_{r['instance_id']}",
                            accept_new_options=True,
                        )
                        if st.button("Save", key=f"save_list_{r['release_id']}_{r['instance_id']}"):
                            with db.get_conn(_user_db) as conn:
                                db.save_custom_tags(
                                    conn, r["release_id"], _new_tags,
                                    ct.get("notes", ""), ct.get("decade_override"),
                                )
                            _load_collection_data.clear()
                            st.rerun()

        # ── Grid view ─────────────────────────────────────────────────────────
        else:
            for row_start in range(0, len(filtered), n_cols):
                cols = st.columns(n_cols)
                for col_idx, col in enumerate(cols):
                    idx = row_start + col_idx
                    if idx >= len(filtered):
                        break
                    r, ct = filtered[idx]
                    stats       = _play_stats.get(r["release_id"], {})
                    play_count  = stats.get("count", 0)
                    last_played = stats.get("last_played", "")
                    decade      = _decade(r["year"], ct.get("decade_override"))
                    genres_str  = ", ".join(r["discogs_genres"]) or "—"
                    custom_str  = " · ".join(ct.get("custom_tags", []))

                    with col:
                        _cover(r.get("cover_url"))
                        st.markdown(f"**{r['title']}**")
                        st.caption(f"{r['artist']} · {r['year'] or '?'} · {decade or '?'}")
                        st.caption(genres_str)
                        _badge = _pressing_badge(r)
                        if _badge:
                            st.caption(_badge)
                        if custom_str:
                            st.markdown(f"{custom_str}")
                        if play_count:
                            lp_str = last_played[:10] if last_played else ""
                            st.caption(f"{play_count}x · last {lp_str}")
                        if st.button(
                            "Log Play",
                            key=f"qplay_{r['release_id']}_{r['instance_id']}",
                            use_container_width=True,
                        ):
                            with db.get_conn(_user_db) as conn:
                                db.log_play(conn, r["release_id"])
                            try:
                                vinyl_api_client.log_play(
                                    _user["user_id"], r["release_id"],
                                    played_at=datetime.utcnow().isoformat(), source="streamlit",
                                )
                            except Exception:
                                logging.getLogger(__name__).warning("Failed to POST play to Vinyl API")
                            _load_collection_data.clear()
                            st.rerun()
                        with st.expander("Edit tags"):
                            st.caption("Discogs: " + (", ".join(r["discogs_genres"]) or "None"))
                            st.caption("Styles:  " + (", ".join(r["discogs_styles"])  or "None"))
                            existing_tags = ct.get("custom_tags", [])
                            new_tags = st.multiselect(
                                "Custom tags",
                                options=sorted(all_custom_tags | set(existing_tags)),
                                default=existing_tags,
                                key=f"tags_{r['release_id']}_{r['instance_id']}",
                                accept_new_options=True,
                            )
                            decade_choices = [""] + sorted(all_decades | ({decade} if decade else set()))
                            current_override = str(ct.get("decade_override") or "")
                            decade_override_str = st.selectbox(
                                "Decade override",
                                options=decade_choices,
                                index=decade_choices.index(current_override) if current_override in decade_choices else 0,
                                key=f"dec_{r['release_id']}_{r['instance_id']}",
                            )
                            notes = st.text_area(
                                "Notes",
                                value=ct.get("notes", ""),
                                key=f"notes_{r['release_id']}_{r['instance_id']}",
                                height=80,
                            )
                            if st.button("Save", key=f"save_{r['release_id']}_{r['instance_id']}"):
                                decade_override_val = (
                                    int(decade_override_str.rstrip("s")) * 10
                                    if decade_override_str else None
                                )
                                with db.get_conn(_user_db) as conn:
                                    db.save_custom_tags(
                                        conn, r["release_id"], new_tags, notes, decade_override_val
                                    )
                                _load_collection_data.clear()
                                st.success("Saved.")
                                st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# TAB: PLAY LOG
# ═════════════════════════════════════════════════════════════════════════════

with tab_playlog:
    st.subheader("Play Log")

    with db.get_conn(_user_db) as conn:
        recent               = db.get_recent_plays(conn, limit=200)
        _play_counts_by_date = db.get_play_counts_by_date(conn, days=365)

    if not recent:
        st.info("No plays logged yet. Use the Log Play button on any record.")
    else:
        # ── Play log visualizations ────────────────────────────────────────────────────────
        _pl_window_opts = {"All time": "1900-01-01", "Last year": 365, "Last 90d": 90, "Last 30d": 30}
        _pl_window_label = st.radio(
            "Top albums window",
            list(_pl_window_opts.keys()),
            horizontal=True,
            index=0,
            key="pl_window",
            label_visibility="collapsed",
        )
        _pl_since_raw = _pl_window_opts[_pl_window_label]
        _pl_since = (
            (datetime.now(timezone.utc) - timedelta(days=_pl_since_raw)).isoformat()
            if isinstance(_pl_since_raw, int)
            else _pl_since_raw
        )
        with db.get_conn(_user_db) as conn:
            _top_albums = db.get_top_albums_since(conn, _pl_since, limit=10)

        vcol1, vcol2 = st.columns([3, 2])
        with vcol1:
            st.markdown("**Listening Activity — Past Year**")
            if _play_counts_by_date:
                st.plotly_chart(
                    _calendar_heatmap(_play_counts_by_date),
                    use_container_width=True,
                    key="playlog_heatmap",
                )
            else:
                st.caption("Your listening calendar will appear here once you log plays.")
        with vcol2:
            st.markdown(f"**Top Albums · {_pl_window_label}**")
            if _top_albums:
                _plotly_hbar(
                    labels=[
                        f"{a['artist']} — {a['title']}"[:45]
                        if a.get("title") else (a.get("artist") or "Unknown")
                        for a in _top_albums
                    ],
                    values=[a["cnt"] for a in _top_albums],
                    height=200,
                )
            else:
                st.caption("No plays in this window.")

        st.divider()

        # ── Due-for-a-spin ─────────────────────────────────────────────────────────────
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        stale = [
            r for r in _all_releases
            if r["release_id"] not in _play_stats
            or datetime.fromisoformat(
                _play_stats[r["release_id"]]["last_played"]
            ).replace(tzinfo=timezone.utc) < cutoff
        ]
        if stale:
            stale_sorted = sorted(
                stale,
                key=lambda r: _play_stats.get(r["release_id"], {}).get("last_played", "0000"),
            )
            top_picks = stale_sorted[:3]

            with st.expander(
                f"Due for a spin — {len(stale)} record(s) not played in 30+ days",
                expanded=True,
            ):
                _sc1, _sc2 = st.columns([5, 1])
                with _sc2:
                    if st.button("Surprise me", key="stale_surprise_btn", use_container_width=True):
                        _pool    = stale_sorted[:min(20, len(stale_sorted))]
                        _weights = [2 if r["release_id"] not in _play_stats else 1 for r in _pool]
                        st.session_state["stale_surprise_pick"] = random.choices(
                            _pool, weights=_weights, k=1
                        )[0]["release_id"]
                        st.rerun()

                _surprise_id = st.session_state.get("stale_surprise_pick")
                if _surprise_id:
                    _s_match = next((r for r in stale if r["release_id"] == _surprise_id), None)
                    if _s_match:
                        with st.container(border=True):
                            _spc1, _spc2, _spc3 = st.columns([1, 5, 2])
                            with _spc1:
                                _cover(_s_match.get("cover_url"), width=60)
                            with _spc2:
                                st.markdown(f"**{_s_match['title']}** — {_s_match['artist']}")
                                _ps = _play_stats.get(_s_match["release_id"], {})
                                _lp = _ps.get("last_played", "")
                                st.caption(f"Last played: {_lp[:10] if _lp else 'never'}")
                            with _spc3:
                                if st.button("Log Play", key="stale_surprise_log", use_container_width=True):
                                    with db.get_conn(_user_db) as conn:
                                        db.log_play(conn, _s_match["release_id"])
                                    try:
                                        vinyl_api_client.log_play(
                                            _user["user_id"], _s_match["release_id"],
                                            played_at=datetime.utcnow().isoformat(), source="streamlit",
                                        )
                                    except Exception:
                                        logging.getLogger(__name__).warning("Failed to POST play to Vinyl API")
                                    st.session_state.pop("stale_surprise_pick", None)
                                    _load_collection_data.clear()
                                    st.rerun()
                        st.divider()

                pp_cols = st.columns(min(3, len(top_picks)))
                for _pi, _pr in enumerate(top_picks):
                    with pp_cols[_pi]:
                        _cover(_pr.get("cover_url"), width=80)
                        st.markdown(f"**{_pr['title']}**")
                        st.caption(_pr.get("artist", ""))
                        _ps = _play_stats.get(_pr["release_id"], {})
                        _lp = _ps.get("last_played", "")
                        st.caption(f"Last: {_lp[:10] if _lp else 'never'}")
                        if st.button(
                            "Log", key=f"stale_log_{_pr['release_id']}", use_container_width=True
                        ):
                            with db.get_conn(_user_db) as conn:
                                db.log_play(conn, _pr["release_id"])
                            st.session_state.pop("stale_surprise_pick", None)
                            _load_collection_data.clear()
                            st.rerun()

                if len(stale_sorted) > 3:
                    with st.expander(f"See all {len(stale_sorted)} overdue records"):
                        for r in stale_sorted[3:23]:
                            st.write(f"• **{r['title']}** — {r['artist']}")
                        if len(stale_sorted) > 23:
                            st.caption(f"…and {len(stale_sorted) - 23} more")

        st.divider()
        st.caption(
            f"{len(recent)} recent plays — select rows then click Delete to remove accidental entries."
        )

        df_plays = pd.DataFrame([{
            "_id":    p["id"],
            "Date":   (p["played_at"] or "")[:16].replace("T", " "),
            "Title":  p.get("title") or "Unknown",
            "Artist": p.get("artist") or "",
            "Notes":  p.get("notes") or "",
        } for p in recent])

        play_grid = _make_grid(
            df_plays,
            height=500,
            hidden_cols=["_id"],
            selection_mode="multiple",
            key="play_log_grid",
        )

        sel_rows = play_grid["selected_rows"]
        if sel_rows is not None and len(sel_rows) > 0:
            rows_to_delete = (
                sel_rows.to_dict("records")
                if hasattr(sel_rows, "to_dict")
                else list(sel_rows)
            )
            if st.button(
                f"Delete {len(rows_to_delete)} selected",
                type="primary",
                key="delete_plays_btn",
            ):
                with db.get_conn(_user_db) as conn:
                    for row in rows_to_delete:
                        db.delete_play(conn, row["_id"])
                _load_collection_data.clear()
                st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# TAB: STATS
# ═════════════════════════════════════════════════════════════════════════════

with tab_stats:
    st.subheader("Collection Stats")

    if not _all_releases:
        st.info("No records found. Use Refresh All above to import your collection.")
    else:
        total_value = _meta.get("total_collection_value")

        # ── Metric cards ──────────────────────────────────────────────────────
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Total Records",         len(_all_releases))
        mc2.metric("Unique Release IDs",    len({r["release_id"] for r in _all_releases}))
        mc3.metric("Total Plays",           sum(s["count"] for s in _play_stats.values()))
        mc4.metric("Est. Collection Value", f"${total_value:,.2f}" if total_value else "—")

        # ── Listening streak metrics ───────────────────────────────────────
        if _play_stats:
            from datetime import date as _date_cls, timedelta as _td_cls
            with db.get_conn(_user_db) as conn:
                _all_date_counts = db.get_play_counts_by_date(conn, days=3650)
            _today_d = _date_cls.today()
            # Current streak (consecutive days ending today)
            _cur_streak, _d = 0, _today_d
            while _d.isoformat() in _all_date_counts:
                _cur_streak += 1
                _d -= _td_cls(days=1)
            # Longest streak (all time)
            _lon_streak, _run, _prev_d = 0, 0, None
            for _ds in sorted(_all_date_counts):
                _cd = _date_cls.fromisoformat(_ds)
                _run = (_run + 1) if (_prev_d and (_cd - _prev_d).days == 1) else 1
                _lon_streak = max(_lon_streak, _run)
                _prev_d = _cd
            # Plays this week / this month
            _week_plays  = sum(_all_date_counts.get((_today_d - _td_cls(days=i)).isoformat(), 0) for i in range(7))
            _month_plays = sum(v for k, v in _all_date_counts.items() if k.startswith(_today_d.strftime("%Y-%m")))
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("Current Streak", f"{_cur_streak}d")
            sm2.metric("Longest Streak", f"{_lon_streak}d")
            sm3.metric("This Week",       _week_plays)
            sm4.metric("This Month",      _month_plays)

        st.divider()

        # ── Pre-build chart data ───────────────────────────────────────────────
        decade_counts: dict[str, int] = {}
        genre_counts:  dict[str, int] = {}
        fmt_counts:    dict[str, int] = {}

        for r in _all_releases:
            ct = _get_ct(r["release_id"])
            d  = _decade(r["year"], ct.get("decade_override"))
            if d:
                decade_counts[d] = decade_counts.get(d, 0) + 1
            for g in r["discogs_genres"]:
                genre_counts[g] = genre_counts.get(g, 0) + 1
            f = r.get("format") or "Unknown"
            fmt_counts[f] = fmt_counts.get(f, 0) + 1

        df_d = (
            pd.DataFrame.from_dict(decade_counts, orient="index", columns=["Count"])
            .sort_index()
        )
        df_g = (
            pd.DataFrame.from_dict(genre_counts, orient="index", columns=["Count"])
            .sort_values("Count", ascending=False)
        )
        df_f = (
            pd.DataFrame.from_dict(fmt_counts, orient="index", columns=["Count"])
            .sort_values("Count", ascending=False)
        )

        # Chart version key (incremented by Clear button to reset selections)
        _chart_ver = st.session_state.get("stats_chart_ver", 0)

        # ── Charts row 1 ──────────────────────────────────────────────────────
        ch1, ch2 = st.columns(2)
        with ch1:
            st.markdown("**Records by Decade**")
            sel_decade = _plotly_bar(
                list(df_d.index), list(df_d["Count"]),
                key=f"decade_chart_{_chart_ver}",
            )
        with ch2:
            st.markdown("**Records by Genre**")
            sel_genre = _plotly_bar(
                list(df_g.index), list(df_g["Count"]),
                key=f"genre_chart_{_chart_ver}",
            )

        # ── Charts row 2 ──────────────────────────────────────────────────────
        ch3, ch4 = st.columns(2)
        with ch3:
            st.markdown("**Records by Format**")
            sel_format = _plotly_bar(
                list(df_f.index), list(df_f["Count"]),
                key=f"format_chart_{_chart_ver}",
            )
        with ch4:
            st.markdown("**Most Played**")
            if _play_stats:
                top_played  = sorted(_play_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
                release_map = {r["release_id"]: r for r in _all_releases}
                rows_top    = [
                    {
                        "Title":  release_map.get(rid, {}).get("title", "Unknown"),
                        "Artist": release_map.get(rid, {}).get("artist", ""),
                        "Plays":  s["count"],
                    }
                    for rid, s in top_played
                ]
                _make_grid(pd.DataFrame(rows_top), height=300, key="most_played_grid")
            else:
                st.caption("No plays logged yet.")

        st.divider()

        # ── Active filter chips + clear ────────────────────────────────────────
        active_filters = [
            (label, val) for label, val in
            [("Decade", sel_decade), ("Genre", sel_genre), ("Format", sel_format)]
            if val
        ]
        if active_filters:
            frow1, frow2 = st.columns([8, 1])
            with frow1:
                chips = "  ·  ".join(f"**{lbl}:** {val}" for lbl, val in active_filters)
                st.markdown(f"Filtered by: {chips}")
            with frow2:
                if st.button("Clear", key="clear_stats_filters"):
                    st.session_state["stats_chart_ver"] = _chart_ver + 1
                    st.rerun()

        # ── Build never-played DataFrame ───────────────────────────────────────
        never_played = [r for r in _all_releases if r["release_id"] not in _play_stats]
        rows_np: list[dict] = []
        for r in never_played:
            ct         = _get_ct(r["release_id"])
            decade_val = _decade(r["year"], ct.get("decade_override")) or "?"
            genres_lst = r["discogs_genres"]
            rows_np.append({
                "Title":  r["title"],
                "Artist": r["artist"],
                "Year":   r["year"] or "",
                "Decade": decade_val,
                "Genre":  ", ".join(genres_lst) if genres_lst else "—",
                "Format": r.get("format") or "Unknown",
            })
        df_never = pd.DataFrame(rows_np)

        # Apply cross-filters (AND logic — each active selection narrows further)
        if sel_decade and not df_never.empty:
            df_never = df_never[df_never["Decade"] == sel_decade]
        if sel_genre and not df_never.empty:
            df_never = df_never[df_never["Genre"].str.contains(sel_genre, na=False)]
        if sel_format and not df_never.empty:
            df_never = df_never[df_never["Format"] == sel_format]

        st.subheader(f"Never Played ({len(df_never)})")
        if not df_never.empty:
            _make_grid(
                df_never,
                row_group_cols=["Decade"],
                height=420,
                key=f"never_played_grid_{sel_decade}_{sel_genre}_{sel_format}",
            )
        else:
            st.caption("No records match the current filter. Click **Clear** above to reset.")

# ═════════════════════════════════════════════════════════════════════════════
# TAB: DISCOVER
# ═════════════════════════════════════════════════════════════════════════════

with tab_discover:
    st.subheader("Discover")

    with db.get_conn(_user_db) as conn:
        suggestions = db.get_suggestions(conn)

    if not suggestions:
        st.info(
            "No suggestions yet. Use Refresh All above to search Discogs "
            "based on the genres in your collection."
        )
    else:
        sug_genres: set[str] = set()
        for s in suggestions:
            for g in s.get("genres", []):
                sug_genres.add(g)
        sel_sug_genres = st.multiselect("Filter by genre", sorted(sug_genres))

        filtered_sug = [
            s for s in suggestions
            if not sel_sug_genres or any(g in s.get("genres", []) for g in sel_sug_genres)
        ]

        st.caption(f"Showing {len(filtered_sug)} suggestions")

        COLS = 4
        for row_start in range(0, len(filtered_sug), COLS):
            cols = st.columns(COLS)
            for col_idx, col in enumerate(cols):
                idx = row_start + col_idx
                if idx >= len(filtered_sug):
                    break
                s = filtered_sug[idx]
                with col:
                    _cover(s.get("cover_url"))
                    st.markdown(f"**{s['title']}**")
                    st.caption(f"{s['artist']} · {s['year'] or '?'}")
                    st.caption(", ".join(s.get("genres", [])) or "—")

# ═════════════════════════════════════════════════════════════════════════════

# =============================================================================
# TAB: WANTLIST LISTINGS
# =============================================================================

with tab_wantlist:
    if not _vinyl_items:
        if not _wantlist:
            st.info("Wantlist is empty. Use **Refresh All** above to import from Discogs.")
        else:
            st.info("No vinyl items found in wantlist.")
    else:
        with db.get_conn(_user_db) as _pt_conn:
            _price_trends = db.get_all_price_trends(_pt_conn)
            _new_listing_ids = db.get_recent_new_listing_ids(_pt_conn, since_hours=24)

        _LISTING_COLS = ["New", "Artist", "Title", "Price (USD)", "Condition", "Sleeve", "Ships From", "Listing"]

        def _build_listing_rows(w: dict, listings: list) -> list[dict]:
            rows: list[dict] = []
            listings = [l for l in listings if l.get("ships_to_us") != 0]
            if listings and listings[0].get("listing_id") is not None:
                for lst in listings:
                    _raw = lst.get("price_usd")
                    _lid = lst.get("listing_id")
                    rows.append({
                        "New":         "NEW" if _lid in _new_listing_ids else "",
                        "Artist":      w["artist"],
                        "Title":       w["title"],
                        "Price (USD)": round(_raw, 2) if _raw is not None else None,
                        "Condition":   lst.get("condition"),
                        "Sleeve":      lst.get("sleeve_condition") or lst.get("sleeve"),
                        "Ships From":  lst.get("ships_from"),
                        "Listing":     lst.get("listing_url") or lst.get("url"),
                    })
            elif listings:
                s = listings[0]
                rows.append({
                    "New":         "",
                    "Artist":      w["artist"],
                    "Title":       w["title"],
                    "Price (USD)": None,
                    "Condition":   None,
                    "Sleeve":      None,
                    "Ships From":  None,
                    "Listing":     s.get("listing_url") or s.get("url"),
                })
            else:
                rows.append({
                    "New":         "",
                    "Artist":      w["artist"],
                    "Title":       w["title"],
                    "Price (USD)": None,
                    "Condition":   None,
                    "Sleeve":      None,
                    "Ships From":  None,
                    "Listing":     f"https://www.discogs.com/release/{w['release_id']}",
                })
            return rows

        # Load cached listings from DB
        _db_listings: dict[int, list] = {}
        with db.get_conn(_user_db) as _conn:
            for _w in _vinyl_items:
                _rows = db.get_wantlist_listings(_conn, _w["release_id"])
                if _rows:
                    _db_listings[_w["release_id"]] = _rows

        # Build and render
        _all_rows: list[dict] = []
        for _w in _vinyl_items:
            _all_rows.extend(_build_listing_rows(_w, _db_listings.get(_w["release_id"], [])))

        if _all_rows:
            _n_loaded = sum(1 for w in _vinyl_items if w["release_id"] in _db_listings)
            _any_row  = next((r for rows in _db_listings.values() for r in rows), None)
            _last_ts  = (_any_row or {}).get("last_fetched")
            _new_count = sum(1 for r in _all_rows if r.get("New") == "NEW")
            _cap = f"{len(_all_rows)} listing(s) across {_n_loaded} of {len(_vinyl_items)} record(s)"
            if _last_ts:
                _cap += f"  \xb7  Last updated: {_last_ts[:16].replace('T', ' ')}"
            if _new_count:
                _cap += f"  \xb7  {_new_count} new in last 24h"
            st.caption(_cap)
            from st_aggrid import JsCode as _JsCode
            _price_fmt = _JsCode(
                "function(p) { if (p.value == null) return '\u2014'; "
                "return '$' + parseFloat(p.value).toFixed(2); }"
            )
            _make_grid(
                pd.DataFrame(_all_rows)[_LISTING_COLS],
                height=600,
                link_cols=["Listing"],
                default_sort_col="Price (USD)",
                default_sort_asc=True,
                col_widths={
                    "New":          70,
                    "Artist":      160,
                    "Title":       220,
                    "Price (USD)": 110,
                    "Condition":   130,
                    "Sleeve":      130,
                    "Ships From":  130,
                    "Listing":      80,
                },
                col_formatters={"Price (USD)": _price_fmt},
                key="listings_grid",
            )
        else:
            st.info("No listings cached. Use **Refresh All** above to load.")
