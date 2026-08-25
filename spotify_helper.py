"""
spotify_helper.py
-----------------
Spotify OAuth (PKCE) + data-fetching helpers for the Vinyl Catalog app.

Uses the Authorization Code + PKCE flow — no client secret required.

Environment variables required (in vinyl/.env):
    SPOTIFY_CLIENT_ID     — from your Spotify Developer app
    SPOTIFY_REDIRECT_URI  — must match what's set in Spotify dashboard
                            (default: http://localhost:8501)
"""
from __future__ import annotations

import logging
import os
import secrets
import hashlib
import base64
import time
from datetime import datetime, timezone

import discogs_client as discogs
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

logger = logging.getLogger(__name__)

_SCOPES = "user-top-read"
_RATE_SLEEP = 1.1  # seconds between Discogs API calls


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _client_id() -> str:
    v = os.environ.get("SPOTIFY_CLIENT_ID", "")
    if not v:
        raise EnvironmentError("SPOTIFY_CLIENT_ID is not set. Add it to vinyl/.env")
    return v


def _redirect_uri() -> str:
    return os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8501")


def _generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    code_verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def get_auth_url() -> str:
    """
    Generate a PKCE pair, persist the verifier to the DB, and return
    the Spotify authorisation URL to redirect the user to.
    """
    import urllib.parse
    from database import get_conn, save_pkce_verifier

    code_verifier, code_challenge = _generate_pkce_pair()
    with get_conn() as conn:
        save_pkce_verifier(conn, code_verifier)

    params = {
        "client_id":             _client_id(),
        "response_type":         "code",
        "redirect_uri":          _redirect_uri(),
        "scope":                 _SCOPES,
        "code_challenge_method": "S256",
        "code_challenge":        code_challenge,
        "show_dialog":           "false",
    }
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)


def exchange_code(code: str, code_verifier: str) -> dict:
    """
    Exchange an authorisation code + PKCE verifier for access + refresh tokens.
    No client secret is needed (PKCE flow).
    """
    import urllib.request
    import urllib.parse
    import json

    body = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  _redirect_uri(),
        "client_id":     _client_id(),
        "code_verifier": code_verifier,
    }).encode()

    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    if "access_token" not in data:
        raise RuntimeError(f"Spotify token exchange failed: {data}")
    return data


def refresh_access_token(refresh_token: str) -> dict:
    """
    Use a refresh token to get a new access token (PKCE — no client secret).
    """
    import urllib.request
    import urllib.parse
    import json

    body = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     _client_id(),
    }).encode()

    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    if "access_token" not in data:
        raise RuntimeError(f"Spotify token refresh failed: {data}")
    return data


def handle_spotify_callback(code: str) -> None:
    """
    Exchange a Spotify auth code + stored PKCE verifier for tokens and
    persist them to the DB.  Call this when st.query_params contains 'code'.
    """
    from database import get_conn, get_pkce_verifier, save_spotify_tokens

    with get_conn() as conn:
        code_verifier = get_pkce_verifier(conn)

    if not code_verifier:
        raise RuntimeError(
            "PKCE verifier not found in DB. The authorisation session may have "
            "expired — please click 'Connect Spotify' again."
        )

    token_data = exchange_code(code, code_verifier)
    expires_at = int(time.time()) + token_data.get("expires_in", 3600)

    with get_conn() as conn:
        save_spotify_tokens(
            conn,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            scope=token_data.get("scope"),
        )
    logger.info("Spotify tokens saved to DB.")


# ── Spotify API client ────────────────────────────────────────────────────────

def _spotify_get(access_token: str, url: str, params: dict | None = None) -> dict:
    """Minimal authenticated GET against the Spotify Web API."""
    import json
    import urllib.parse
    import urllib.request

    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_valid_access_token() -> str | None:
    """
    Return a valid Spotify access token, refreshing if necessary.
    Returns None if no tokens are stored or SPOTIFY_CLIENT_ID is missing.
    """
    from database import get_conn, save_spotify_tokens, get_spotify_tokens

    try:
        _client_id()
    except EnvironmentError:
        return None

    with get_conn() as conn:
        tokens = get_spotify_tokens(conn)

    if not tokens or not tokens.get("access_token"):
        return None

    # Refresh if within 60 s of expiry
    if tokens["expires_at"] and time.time() >= tokens["expires_at"] - 60:
        if not tokens.get("refresh_token"):
            return None
        try:
            new_data = refresh_access_token(tokens["refresh_token"])
            expires_at = int(time.time()) + new_data.get("expires_in", 3600)
            with get_conn() as conn:
                save_spotify_tokens(
                    conn,
                    access_token=new_data["access_token"],
                    refresh_token=new_data.get("refresh_token", tokens["refresh_token"]),
                    expires_at=expires_at,
                    scope=new_data.get("scope", tokens.get("scope")),
                )
            return new_data["access_token"]
        except Exception as exc:
            logger.warning("Spotify token refresh failed: %s", exc)
            return None

    return tokens["access_token"]


# ── Spotify data fetching ─────────────────────────────────────────────────────

def fetch_top_artists(access_token: str, limit: int = 20,
                      time_range: str = "medium_term") -> list[str]:
    """
    Fetch the user's top artists from Spotify.
    time_range: 'short_term' (~4 weeks), 'medium_term' (~6 months), 'long_term' (years)
    Returns a list of artist name strings.
    """
    data = _spotify_get(
        access_token,
        "https://api.spotify.com/v1/me/top/artists",
        {"limit": limit, "time_range": time_range},
    )
    return [item["name"] for item in data.get("items", [])]


# ── Discogs search seeded by Spotify artists ──────────────────────────────────

def _make_discogs_client() -> discogs.Client:
    token    = os.environ.get("DISCOGS_TOKEN", "")
    app_name = os.environ.get("DISCOGS_APP_NAME", "VinylCatalogApp/1.0")
    if not token:
        raise EnvironmentError("DISCOGS_TOKEN is not set.")
    return discogs.Client(app_name, user_token=token)


def _extract_cover_url(release) -> str | None:
    try:
        images = release.images
        if images:
            return images[0].get("uri150") or images[0].get("uri")
    except Exception:
        pass
    return None


def fetch_spotify_vinyl_suggestions(
    access_token: str,
    owned_ids: set[int],
    log_fn=None,
    limit_artists: int = 20,
) -> tuple[list[str], list[dict]]:
    """
    1. Fetches top artists from Spotify.
    2. For each artist, searches Discogs for their vinyl releases.
    3. Filters out releases already owned.
    4. Returns (top_artists, suggestions_list).

    Each suggestion dict:
        release_id, title, artist, year, cover_url,
        source_artist, genres, styles, last_fetched
    """
    def _log(msg: str):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    top_artists = fetch_top_artists(access_token, limit=limit_artists)
    if not top_artists:
        _log("No top artists returned from Spotify.")
        return [], []

    _log(f"Spotify top artists: {', '.join(top_artists[:10])}{'…' if len(top_artists) > 10 else ''}")

    client   = _make_discogs_client()
    now      = datetime.now(timezone.utc).isoformat()
    seen_ids: set[int] = set(owned_ids)
    results: list[dict] = []

    for artist_name in top_artists:
        _log(f"Searching Discogs for vinyl by: {artist_name}…")
        try:
            search = client.search(
                artist=artist_name,
                type="release",
                format="vinyl",
            )
            page = search.page(1)
            time.sleep(_RATE_SLEEP)
        except Exception as exc:
            _log(f"  ⚠ Discogs search failed for '{artist_name}': {exc}")
            continue

        for release in page:
            rid = release.id
            if rid in seen_ids:
                continue
            seen_ids.add(rid)

            try:
                genres = list(release.genres or [])
            except Exception:
                genres = []
            try:
                styles = list(release.styles or [])
            except Exception:
                styles = []

            cover_url = _extract_cover_url(release)

            try:
                year = str(release.year) if release.year else None
            except Exception:
                year = None

            try:
                title = release.title or ""
            except Exception:
                title = ""

            try:
                r_artist = release.artists[0].name if release.artists else artist_name
            except Exception:
                r_artist = artist_name

            results.append({
                "release_id":   rid,
                "title":        title,
                "artist":       r_artist,
                "year":         year,
                "cover_url":    cover_url,
                "source_artist": artist_name,
                "genres":       genres,
                "styles":       styles,
                "last_fetched": now,
            })

        _log(f"  → {len([r for r in results if r['source_artist'] == artist_name])} vinyls found")

    return top_artists, results
