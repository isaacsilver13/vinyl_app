"""
fetch_listings.py
-----------------
Standalone script: fetches for-sale marketplace listings for every vinyl item
in the wantlist and persists them to the local SQLite database.

The Streamlit app reads directly from the database, so run this script
independently (manually or on a schedule) to keep listing data fresh.

Usage:
    # Fetch all wantlist items (skip releases updated within 24 h)
    python vinyl/fetch_listings.py

    # Force-refresh everything regardless of age
    python vinyl/fetch_listings.py --force

    # Fetch a single release by ID
    python vinyl/fetch_listings.py --release-id 36526810

    # Combine: force-refresh a single release
    python vinyl/fetch_listings.py --release-id 36526810 --force
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# ── Resolve the vinyl/ directory so relative imports work ────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(_HERE, ".env"))

import database as db
import discogs_api as api
import vinyl_api_client

# ── Config ────────────────────────────────────────────────────────────────────
_LISTING_TTL_HOURS = 24          # skip a release if listings were fetched this recently
_VINYL_FORMATS     = {"Vinyl", "LP", '7"', '10"', '12"'}


def _is_vinyl(fmt: str) -> bool:
    return not fmt or any(v in fmt for v in _VINYL_FORMATS)


def _map_to_db(release_id: int, lst: dict, ts: str) -> dict:
    """Map a fetch_release_listings row to the wantlist_listings DB schema."""
    return {
        "listing_id":       lst.get("listing_id"),
        "release_id":       release_id,
        "price":            lst.get("price"),
        "currency":         lst.get("currency"),
        "condition":        lst.get("condition"),
        "sleeve_condition": lst.get("sleeve"),
        "ships_from":       lst.get("ships_from"),
        "seller":           lst.get("seller"),
        "listing_url":      lst.get("url"),
        "last_fetched":     ts,
        "price_usd":        lst.get("price_usd"),
        "ships_to_us":      lst.get("ships_to_us"),
        "shipping_notes":   lst.get("shipping_notes"),
    }


def _is_stale(conn, release_id: int, ttl_hours: int) -> bool:
    """Return True if listings for this release are missing or older than ttl_hours."""
    row = conn.execute(
        "SELECT last_fetched FROM wantlist_listings WHERE release_id = ? LIMIT 1",
        (release_id,),
    ).fetchone()
    if not row or not row["last_fetched"]:
        return True
    try:
        fetched_at = datetime.fromisoformat(row["last_fetched"]).replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - fetched_at
        return age > timedelta(hours=ttl_hours)
    except ValueError:
        return True


def fetch_all(release_id_filter: int | None = None, force: bool = False) -> None:
    db.init_db()

    with db.get_conn() as conn:
        wantlist = db.get_wantlist(conn)

    vinyl_items = [w for w in wantlist if _is_vinyl(w.get("format") or "")]
    if not vinyl_items:
        print("No vinyl items found in the wantlist. Run sync_wantlist first.")
        return

    if release_id_filter is not None:
        vinyl_items = [w for w in vinyl_items if w["release_id"] == release_id_filter]
        if not vinyl_items:
            print(f"Release ID {release_id_filter} not found in wantlist.")
            return

    total        = len(vinyl_items)
    skipped      = 0
    fetched      = 0
    total_saved  = 0

    print(f"Processing {total} vinyl item(s)…\n")

    for i, w in enumerate(vinyl_items, 1):
        rid    = w["release_id"]
        label  = f"{w['artist']} — {w['title']}"
        prefix = f"[{i}/{total}]"

        with db.get_conn() as conn:
            if not force and not _is_stale(conn, rid, _LISTING_TTL_HOURS):
                print(f"{prefix} SKIP  {label}  (fetched within {_LISTING_TTL_HOURS}h)")
                skipped += 1
                continue

        print(f"{prefix} FETCH {label}")
        listings = api.fetch_release_listings(
            rid,
            log_fn=lambda msg: print(f"       {msg}"),
            db_path=db.DB_PATH,
        )

        ts          = datetime.utcnow().isoformat()
        db_rows     = [
            _map_to_db(rid, lst, ts)
            for lst in listings
            if lst.get("listing_id") is not None
        ]
        with db.get_conn() as conn:
            saved_count = db.bulk_upsert_wantlist_listings(conn, rid, db_rows)

        # Demo: POST listings to a running Vinyl API if configured
        try:
            if db_rows:
                # the API expects fields matching the ListingIn schema; our db_rows map matches keys
                vinyl_api_client.post_listings_bulk(rid, db_rows)
                print(f"       → POSTed {len(db_rows)} listing(s) to Vinyl API")
        except Exception as exc:  # pragmatic: don't fail the whole run if API is unavailable
            print(f"       → warning: failed to POST listings to Vinyl API: {exc}")

        fetched     += 1
        total_saved += saved_count
        print(f"       → {saved_count} listing(s) saved\n")

    print("─" * 50)
    print(
        f"Done.  {fetched} fetched · {skipped} skipped · {total_saved} total listings in DB"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Discogs marketplace listings for wantlist vinyl items."
    )
    parser.add_argument(
        "--release-id", type=int, default=None,
        help="Only fetch this specific release ID.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help=f"Bypass the {_LISTING_TTL_HOURS}h TTL and re-fetch everything.",
    )
    args = parser.parse_args()

    fetch_all(release_id_filter=args.release_id, force=args.force)


if __name__ == "__main__":
    main()
