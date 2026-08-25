from __future__ import annotations

import logging
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import re

import discogs_client
import requests
from discogs_client.utils import update_qs
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from database import (
    clear_releases,
    clear_wantlist,
    get_all_releases,
    get_conn,
    get_existing_listing_enrichment,
    get_sync_meta,
    get_wantlist,
    load_enrichment_cache,
    save_enrichment_entries,
    update_sync_meta,
    upsert_release,
    upsert_suggestion,
    upsert_wantlist_item,
)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

_RATE_SLEEP = 1.1   # seconds between Discogs API calls (safe under 60 req/min)
_PAGE_SIZE  = 100

# Reuse a single TCP/TLS connection across all plain requests.get() calls.
_session = requests.Session()


def _make_client() -> discogs_client.Client:
    token    = os.environ.get("DISCOGS_TOKEN", "")
    app_name = os.environ.get("DISCOGS_APP_NAME", "VinylCatalogApp/1.0")
    if not token:
        raise EnvironmentError("DISCOGS_TOKEN is not set. Add it to vinyl/.env")
    return discogs_client.Client(app_name, user_token=token)


def make_client(token: str, app_name: str | None = None) -> discogs_client.Client:
    """Public factory: build a Discogs client from a token."""
    _app = app_name or os.environ.get("DISCOGS_APP_NAME", "VinylCatalogApp/1.0")
    return discogs_client.Client(_app, user_token=token)


def _get_username(client: discogs_client.Client, discogs_username: str | None = None) -> str:
    username = discogs_username or os.environ.get("DISCOGS_USERNAME", "")
    if not username:
        username = client.identity().username
    return username


def _extract_cover_url(release) -> str | None:
    try:
        images = release.images
        if images:
            return images[0].get("uri150") or images[0].get("uri")
    except Exception:
        pass
    return None


def _extract_format(item) -> str:
    try:
        formats = item.release.formats
        if formats:
            names = [f.get("name", "") for f in formats]
            return ", ".join(n for n in names if n)
    except Exception:
        pass
    return ""


def _extract_pressing_details(item) -> tuple[list[str], str]:
    """Return (descriptions_list, color_text) from a release's formats field."""
    all_descs: list[str] = []
    color_parts: list[str] = []
    try:
        formats = item.release.formats
        if formats:
            for fmt in formats:
                all_descs.extend(fmt.get("descriptions") or [])
                t = (fmt.get("text") or "").strip()
                if t:
                    color_parts.append(t)
    except Exception:
        pass
    return all_descs, " / ".join(color_parts)


def sync_collection(
    force: bool = False,
    log_fn: Optional[Callable[[str], None]] = None,
    client: Optional[discogs_client.Client] = None,
    discogs_username: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Sync the Discogs collection into the local SQLite cache.

    Skips if last sync was < 24 h ago, unless force=True.
    Returns the number of records synced.
    """
    def _log(msg: str):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    with get_conn(db_path) as conn:
        meta = get_sync_meta(conn)
        last = meta.get("last_full_sync")
        if not force and last:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
            if age < timedelta(hours=24):
                _log("Cache is fresh (< 24 h old) — skipping sync.")
                return 0

    _log("Connecting to Discogs API…")
    client   = client or _make_client()
    username = _get_username(client, discogs_username)
    _log(f"Authenticated as: {username}")
    user     = client.user(username)

    # Fetch total collection value cheaply
    try:
        cval = user.collection_value
        total_value = float(cval.median.replace("$", "").replace(",", "")) if cval and cval.median else None
        if total_value:
            _log(f"Collection median value: ${total_value:,.2f}")
    except Exception:
        total_value = None

    # Paginate through collection
    page   = 1
    synced = 0
    all_items: list[dict] = []
    start_time = time.time()

    while True:
        _log(f"Fetching page {page}…")
        releases_page = user.collection_folders[0].releases.page(page)
        if not releases_page:
            break

        for item in releases_page:
            release     = item.release
            release_id  = release.id
            instance_id = item.id

            try:
                genres = list(release.genres or [])
            except Exception:
                genres = []
            try:
                styles = list(release.styles or [])
            except Exception:
                styles = []

            cover_url = _extract_cover_url(release)
            fmt       = _extract_format(item)
            fmt_descs, fmt_text = _extract_pressing_details(item)

            try:
                year = int(release.year) if release.year else None
            except (ValueError, TypeError):
                year = None

            try:
                artists = release.artists
                artist  = ", ".join(a.name for a in artists) if artists else ""
            except Exception:
                artist = ""

            title = getattr(release, "title", "")
            synced += 1
            _log(f"  [{synced}] {artist} — {title} ({year or '?'})")

            all_items.append({
                "release_id":          release_id,
                "instance_id":         instance_id,
                "title":               title,
                "artist":              artist,
                "year":                year,
                "discogs_genres":      genres,
                "discogs_styles":      styles,
                "cover_url":           cover_url,
                "format":              fmt,
                "format_descriptions": fmt_descs,
                "format_text":         fmt_text,
                "last_synced":         datetime.utcnow().isoformat(),
            })

            time.sleep(_RATE_SLEEP)

        if len(releases_page) < _PAGE_SIZE:
            break
        page += 1

    _log(f"Writing {synced} records to database…")
    with get_conn(db_path) as conn:
        clear_releases(conn)
        for item in all_items:
            upsert_release(conn, item)
        update_sync_meta(
            conn,
            last_full_sync=datetime.utcnow().isoformat(),
            total_collection_value=total_value,
        )

    elapsed = time.time() - start_time
    _log(f"Sync complete: {synced} records in {elapsed:.1f}s")
    return synced


def fetch_suggestions(
    force: bool = False,
    log_fn: Optional[Callable[[str], None]] = None,
    client: Optional[discogs_client.Client] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Search Discogs for records based on the top genres in your collection.
    Uses a 7-day TTL unless force=True.
    Returns the number of suggestions stored.
    """
    def _log(msg: str):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    with get_conn(db_path) as conn:
        meta = get_sync_meta(conn)
        last = meta.get("last_suggestions_fetch")
        if not force and last:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
            if age < timedelta(days=7):
                _log("Suggestions cache is fresh (< 7 days) — skipping.")
                return 0

        releases = get_all_releases(conn)

    if not releases:
        _log("No collection data found — run a collection sync first.")
        return 0

    # Find top 5 genres across collection
    genre_counts: Counter = Counter()
    for r in releases:
        for g in r.get("discogs_genres", []):
            genre_counts[g] += 1
    top_genres = [g for g, _ in genre_counts.most_common(5)]
    _log(f"Top genres in collection: {', '.join(top_genres)}")

    owned_ids            = {r["release_id"] for r in releases}
    client               = client or _make_client()
    stored               = 0
    seen_ids: set[int]   = set()
    suggestions_to_write: list[dict] = []
    start_time           = time.time()

    for genre in top_genres:
        _log(f"Searching Discogs for genre: {genre}…")
        try:
            results = client.search(type="release", genre=genre)
            for page_num in range(1, 4):
                page = results.page(page_num)
                if not page:
                    break
                for release in page:
                    rid = release.id
                    if rid in owned_ids or rid in seen_ids:
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
                        year = int(release.year) if release.year else None
                    except (ValueError, TypeError):
                        year = None

                    try:
                        artists = release.artists
                        artist  = ", ".join(a.name for a in artists) if artists else ""
                    except Exception:
                        artist = getattr(release, "title", "")

                    title = getattr(release, "title", "")
                    stored += 1
                    _log(f"  [{stored}] {artist} — {title} ({year or '?'})")

                    suggestion = {
                        "release_id":   rid,
                        "title":        title,
                        "artist":       artist,
                        "year":         year,
                        "genres":       genres,
                        "styles":       styles,
                        "cover_url":    cover_url,
                        "last_fetched": datetime.utcnow().isoformat(),
                    }

                    suggestions_to_write.append(suggestion)
                    time.sleep(_RATE_SLEEP)

                if len(page) < _PAGE_SIZE:
                    break

        except Exception as exc:
            _log(f"  Error fetching genre '{genre}': {exc}")
            logger.exception("fetch_suggestions error for genre %s", genre)
            continue

    with get_conn(db_path) as conn:
        for _s in suggestions_to_write:
            upsert_suggestion(conn, _s)
        update_sync_meta(conn, last_suggestions_fetch=datetime.utcnow().isoformat())

    elapsed = time.time() - start_time
    _log(f"Suggestions complete: {stored} new records found in {elapsed:.1f}s")
    return stored


def sync_wantlist(
    force: bool = False,
    log_fn: Optional[Callable[[str], None]] = None,
    client: Optional[discogs_client.Client] = None,
    discogs_username: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Sync the Discogs wantlist into the local SQLite cache.
    Uses a 6-hour TTL unless force=True.
    Returns the number of items synced.
    """
    def _log(msg: str):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    with get_conn(db_path) as conn:
        meta = get_sync_meta(conn)
        last = meta.get("last_wantlist_sync")
        if not force and last:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
            if age < timedelta(hours=6):
                _log("Wantlist cache is fresh (< 6 h) — skipping sync.")
                return 0

    _log("Connecting to Discogs API…")
    client   = client or _make_client()
    username = _get_username(client, discogs_username)
    _log(f"Authenticated as: {username}")
    user     = client.user(username)

    _log("Fetching wantlist…")
    items: list[dict] = []
    count = 0

    try:
        for item in user.wantlist:
            release = item.release
            try:
                genres = list(release.genres or [])
            except Exception:
                genres = []
            try:
                artists = release.artists
                artist  = ", ".join(a.name for a in artists) if artists else ""
            except Exception:
                artist = ""
            try:
                year = int(release.year) if release.year else None
            except (ValueError, TypeError):
                year = None

            cover_url = _extract_cover_url(release)
            fmt       = _extract_format(item)
            fmt_descs, fmt_text = _extract_pressing_details(item)
            title     = getattr(release, "title", "")
            notes     = getattr(item, "notes", "") or ""

            count += 1
            _log(f"  [{count}] {artist} — {title} ({year or '?'})")
            items.append({
                "release_id":          release.id,
                "title":               title,
                "artist":              artist,
                "year":                year,
                "genres":              genres,
                "cover_url":           cover_url,
                "format":              fmt,
                "format_descriptions": fmt_descs,
                "format_text":         fmt_text,
                "notes":               notes,
                "last_fetched":        datetime.utcnow().isoformat(),
            })
            time.sleep(_RATE_SLEEP)
    except Exception as exc:
        _log(f"Error reading wantlist: {exc}")
        raise

    with get_conn(db_path) as conn:
        clear_wantlist(conn)
        for it in items:
            upsert_wantlist_item(conn, it)
        update_sync_meta(conn, last_wantlist_sync=datetime.utcnow().isoformat())

    _log(f"Wantlist synced: {count} items.")
    return count


def fetch_marketplace_info(
    release_id: int,
    log_fn: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Fetch marketplace stats and price suggestions for a single release.
    Returns a dict with keys: for_sale, lowest_price, currency, prices.
    'prices' is a dict mapping condition label → float (or None).
    """
    def _log(msg: str):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    client = _make_client()
    result: dict = {
        "for_sale":    None,
        "lowest_price": None,
        "currency":    "USD",
        "prices":      {},
    }

    try:
        release = client.release(release_id)
        stats   = release.marketplace_stats
        result["for_sale"] = getattr(stats, "num_for_sale", None)
        lp = getattr(stats, "lowest_price", None)
        if lp is not None:
            result["lowest_price"] = getattr(lp, "value", None)
            result["currency"]     = getattr(lp, "currency", "USD") or "USD"
        time.sleep(_RATE_SLEEP)
    except Exception as exc:
        _log(f"Could not fetch marketplace stats: {exc}")

    _CONDITIONS = [
        ("mint",           "Mint (M)"),
        ("near_mint",      "Near Mint (NM or M-)"),
        ("very_good_plus", "Very Good Plus (VG+)"),
        ("very_good",      "Very Good (VG)"),
        ("good_plus",      "Good Plus (G+)"),
        ("good",           "Good (G)"),
        ("fair",           "Fair (F)"),
        ("poor",           "Poor (P)"),
    ]
    try:
        suggestions = release.price_suggestions
        for attr, label in _CONDITIONS:
            p = getattr(suggestions, attr, None)
            if p is not None:
                result["prices"][label] = getattr(p, "value", None)
        time.sleep(_RATE_SLEEP)
    except Exception as exc:
        _log(f"Could not fetch price suggestions: {exc}")

    n    = result["for_sale"]
    low  = result["lowest_price"]
    cur  = result["currency"]
    low_str = f" · Lowest: {low:.2f} {cur}" if low is not None else ""
    _log(f"Release {release_id}: {n if n is not None else '?'} for sale{low_str}")
    return result


def fetch_release_listings(
    release_id: int,
    per_page: int = 100,
    curr_abbr: str = "USD",
    log_fn: Optional[Callable[[str], None]] = None,
    token: Optional[str] = None,
    db_path: str | None = None,
) -> list[dict]:
    """
    Scrape per-listing marketplace data for a release from the Discogs sell page.

    Primary path: scrapes https://www.discogs.com/sell/list?release_id={id} using
    cloudscraper (Cloudflare bypass) + BeautifulSoup. Returns one dict per listing
    with keys: listing_id, seller, condition, sleeve, ships_from, price, currency, url.

    Fallback: if scraping is unavailable or blocked, calls
    GET /marketplace/stats/{release_id} and returns a single aggregate summary dict
    (keys: for_sale, price, currency, url).
    """
    def _log(msg: str):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    # ── Scraping path ────────────────────────────────────────────────────────
    try:
        import cloudscraper
        from bs4 import BeautifulSoup, NavigableString
    except ImportError as exc:
        _log(f"cloudscraper/beautifulsoup4 not installed ({exc}); using stats fallback")
        return _fetch_listing_stats_fallback(release_id, curr_abbr, _log)

    _SCRAPE_RETRIES = 5
    _scrape_backoff = 5
    listings: list[dict] = []

    for _attempt in range(1, _SCRAPE_RETRIES + 1):
        # Recreate the scraper on every attempt — Cloudflare challenges require
        # a fresh session to bypass.
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )

        listings = []
        url: Optional[str] = (
            f"https://www.discogs.com/sell/list?release_id={release_id}&sort=price%2Casc"
        )
        page_num = 0

        while url and len(listings) < per_page:
            page_num += 1
            _log(
                f"Release {release_id}: fetching page {page_num}"
                + (f" (attempt {_attempt}/{_SCRAPE_RETRIES})" if _attempt > 1 else "")
            )
            try:
                resp = scraper.get(url, timeout=20)
                time.sleep(_RATE_SLEEP)
            except Exception as exc:
                _log(f"Release {release_id}: request error on page {page_num}: {exc}")
                break

            if resp.status_code != 200:
                _log(f"Release {release_id}: HTTP {resp.status_code} on page {page_num}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.shortcut_navigable")
            if not rows:
                _log(f"Release {release_id}: no listing rows on page {page_num}")
                break

            for row in rows:
                if len(listings) >= per_page:
                    break
                try:
                    price_el    = row.select_one("span.price[data-pricevalue]")
                    price_val   = float(price_el["data-pricevalue"]) if price_el and price_el.get("data-pricevalue") else None
                    currency    = price_el.get("data-currency", curr_abbr) if price_el else curr_abbr

                    link_el     = row.select_one("a.item_description_title")
                    href        = link_el.get("href", "") if link_el else ""
                    m           = re.search(r"/sell/item/(\d+)", href)
                    listing_id  = int(m.group(1)) if m else None
                    listing_url = f"https://www.discogs.com{href}" if href else None

                    cond_span   = row.select_one("p.item_condition > span:not(.mplabel)")
                    first_text  = next(
                        (c for c in cond_span.contents if isinstance(c, NavigableString)),
                        None,
                    ) if cond_span else None
                    condition   = first_text.strip() if first_text else None

                    sleeve_el   = row.select_one("span.item_sleeve_condition")
                    sleeve      = sleeve_el.get_text(strip=True) if sleeve_el else None

                    seller_el   = row.select_one("td.seller_info strong a")
                    seller      = seller_el.get_text(strip=True) if seller_el else None

                    ships_from  = None
                    seller_td   = row.select_one("td.seller_info")
                    if seller_td:
                        for li in seller_td.select("li"):
                            li_text = li.get_text(" ", strip=True)
                            if "Ships From:" in li_text:
                                ships_from = li_text.split("Ships From:", 1)[1].strip()
                                break

                    listings.append({
                        "listing_id": listing_id,
                        "seller":     seller,
                        "condition":  condition,
                        "sleeve":     sleeve,
                        "ships_from": ships_from,
                        "price":      price_val,
                        "currency":   currency,
                        "url":        listing_url,
                    })
                except Exception as exc:
                    _log(f"Release {release_id}: parse error in row: {exc}")

            next_el  = soup.select_one("a.pagination_next")
            next_href = next_el["href"] if next_el and next_el.get("href") else None
            url = (
                f"https://www.discogs.com{next_href}"
                if next_href and next_href.startswith("/")
                else next_href
            )

        if listings:
            break  # scrape succeeded — skip remaining retries

        if _attempt < _SCRAPE_RETRIES:
            _log(
                f"Release {release_id}: no listings on attempt {_attempt}/{_SCRAPE_RETRIES};"
                f" retrying in {_scrape_backoff}s..."
            )
            time.sleep(_scrape_backoff)
            _scrape_backoff = min(_scrape_backoff * 2, 60)
        else:
            _log(f"Release {release_id}: all {_SCRAPE_RETRIES} scrape attempts exhausted")

    # ── Enrich: USD price + ships-to-US via per-listing API ──────────────────
    if listings:
        _api_hdrs = {
            "Authorization": f"Discogs token={token or os.environ.get('DISCOGS_TOKEN', '')}",
            "User-Agent":    os.environ.get("DISCOGS_APP_NAME", "VinylCatalogApp/1.0"),
            "Accept":        "application/json",
        }
        # Load persisted enrichment cache — skip API calls for recently-seen listings.
        _existing_enrichment: dict[int, dict] = {}
        _ecache: dict[int, dict] = {}
        if db_path:
            try:
                with get_conn(db_path) as _ec:
                    _existing_enrichment = get_existing_listing_enrichment(_ec, release_id)
                    _ecache = load_enrichment_cache(_ec)
            except Exception:
                pass
        _new_enrichments: list[dict] = []

        for _lst in listings:
            _lid = _lst.get("listing_id")
            # US seller already priced in USD — no API call needed
            if _lst.get("ships_from") == "United States" and _lst.get("currency") == "USD":
                _lst["price_usd"]      = _lst.get("price")
                _lst["ships_to_us"]    = 1
                _lst["shipping_notes"] = None
                continue
            if not _lid:
                _lst["price_usd"]      = _lst.get("price") if _lst.get("currency") == "USD" else None
                _lst["ships_to_us"]    = None
                _lst["shipping_notes"] = None
                continue
            _existing = _existing_enrichment.get(_lid)
            if (
                _existing
                and _existing["price"] == _lst.get("price")
                and _existing["currency"] == _lst.get("currency")
            ):
                _lst["price_usd"] = _existing["price_usd"]
                _lst["ships_to_us"] = _existing["ships_to_us"]
                _lst["shipping_notes"] = _existing["shipping_notes"]
                continue
            # Cache hit: reuse enrichment data without an API call
            if _lid in _ecache:
                _cached = _ecache[_lid]
                _lst["price_usd"]      = _cached["price_usd"]
                _lst["ships_to_us"]    = _cached["ships_to_us"]
                _lst["shipping_notes"] = _cached["shipping_notes"]
                continue
            try:
                _r = _session.get(
                    f"https://api.discogs.com/marketplace/listings/{_lid}",
                    params={"curr_abbr": "USD"},
                    headers=_api_hdrs,
                    timeout=10,
                )
                time.sleep(_RATE_SLEEP)
                if _r.status_code == 200:
                    _d = _r.json()
                    _p = _d.get("price") or {}
                    _lst["price_usd"] = _p.get("value") if _p.get("currency") == "USD" else _lst.get("price")
                    _notes = (_d.get("seller") or {}).get("shipping") or ""
                    _lst["shipping_notes"] = _notes
                    if _d.get("shipping_is_blocked"):
                        _lst["ships_to_us"] = 0
                    elif any(kw in _notes.lower() for kw in
                             ("united states", "worldwide", "international", " us ", "u.s.", "(us)")):
                        _lst["ships_to_us"] = 1
                    elif _lst.get("ships_from") == "United States":
                        _lst["ships_to_us"] = 1
                    else:
                        _lst["ships_to_us"] = None
                    _new_enrichments.append({
                        "listing_id":    _lid,
                        "price_usd":     _lst["price_usd"],
                        "ships_to_us":   _lst["ships_to_us"],
                        "shipping_notes": _lst["shipping_notes"],
                    })
                else:
                    _log(f"Listing {_lid}: enrichment API returned {_r.status_code}")
                    _lst["price_usd"]      = _lst.get("price") if _lst.get("currency") == "USD" else None
                    _lst["ships_to_us"]    = 1 if _lst.get("ships_from") == "United States" else None
                    _lst["shipping_notes"] = None
            except Exception as _exc:
                _log(f"Listing {_lid}: enrichment error: {_exc}")
                _lst["price_usd"]      = _lst.get("price") if _lst.get("currency") == "USD" else None
                _lst["ships_to_us"]    = 1 if _lst.get("ships_from") == "United States" else None
                _lst["shipping_notes"] = None

        # Persist new enrichments so the next run can skip these API calls
        if _new_enrichments and db_path:
            try:
                with get_conn(db_path) as _ec:
                    save_enrichment_entries(_ec, _new_enrichments)
            except Exception:
                pass

    if listings:
        _log(f"Release {release_id}: {len(listings)} listing(s) fetched across {page_num} page(s)")
        return listings

    _log(f"Release {release_id}: scraping returned no results; using stats fallback")
    return _fetch_listing_stats_fallback(release_id, curr_abbr, _log, token=token)


def _fetch_listing_stats_fallback(
    release_id: int,
    curr_abbr: str,
    _log: Callable[[str], None],
    token: Optional[str] = None,
) -> list[dict]:
    """Fallback: return aggregate stats from the Discogs marketplace/stats endpoint."""
    _token   = token or os.environ.get("DISCOGS_TOKEN", "")
    app_name = os.environ.get("DISCOGS_APP_NAME", "VinylCatalogApp/1.0")
    headers  = {
        "Authorization": f"Discogs token={_token}",
        "User-Agent":    app_name,
        "Accept":        "application/json",
    }
    try:
        resp = _session.get(
            f"https://api.discogs.com/marketplace/stats/{release_id}",
            headers=headers, timeout=10,
        )
        time.sleep(_RATE_SLEEP)
        if resp.status_code != 200:
            _log(f"Release {release_id}: stats fallback returned {resp.status_code}")
            return []
        data = resp.json()
        if data.get("blocked_from_sale"):
            _log(f"Release {release_id}: blocked from sale")
            return []
        num_for_sale = data.get("num_for_sale") or 0
        lp           = data.get("lowest_price") or {}
        lowest_price = lp.get("value") if isinstance(lp, dict) else None
        currency     = lp.get("currency", curr_abbr) if isinstance(lp, dict) else curr_abbr
        _log(f"Release {release_id} (stats fallback): {num_for_sale} for sale"
             + (f", lowest {lowest_price:.2f} {currency}" if lowest_price else ""))
        return [{"for_sale": num_for_sale, "price": lowest_price, "currency": currency,
                 "url": f"https://www.discogs.com/sell/list?release_id={release_id}"}]
    except Exception as exc:
        _log(f"Release {release_id}: stats fallback error: {exc}")
        return []
