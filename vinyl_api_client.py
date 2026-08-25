"""Simple HTTP client for the local Vinyl API used by scheduled scripts.

This is intentionally minimal: it reads `VINYL_API_URL` and `VINYL_API_KEY` from
the environment and POSTs to `/listings/bulk` for demo purposes.
"""
from __future__ import annotations

import os
import requests
from typing import List, Dict, Any

def _api_url() -> str:
    return os.environ.get("VINYL_API_URL", "http://127.0.0.1:8000")


def _api_key() -> str | None:
    return os.environ.get("VINYL_API_KEY")


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    api_key = _api_key()
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def post_listings_bulk(release_id: int, listings: List[Dict[str, Any]]) -> Dict[str, Any]:
    url = f"{_api_url().rstrip('/')}/listings/bulk"
    # strip any fields not in the allowed ListingIn schema to satisfy strict validation
    allowed = {
        "listing_id",
        "price",
        "currency",
        "condition",
        "sleeve",
        "sleeve_condition",
        "ships_from",
        "seller",
        "url",
        "listing_url",
        "last_fetched",
        "price_usd",
        "ships_to_us",
        "shipping_notes",
    }
    cleaned = []
    for lst in listings:
        # coerce mapping-like rows (e.g., SQL row mapping) to dict
        if not isinstance(lst, dict):
            try:
                lst = dict(lst)
            except Exception:
                continue

        cl = {k: v for k, v in lst.items() if k in allowed}

        # ensure listing_id exists - skip otherwise
        if not cl.get("listing_id"):
            # try common alternatives
            if lst.get("id"):
                cl["listing_id"] = str(lst.get("id"))
            elif lst.get("listing_url"):
                cl["listing_id"] = str(hash(lst.get("listing_url")))
            else:
                # skip entries without an identifier
                continue

        # normalize last_fetched to ISO str if datetime
        lf = cl.get("last_fetched")
        if lf is not None:
            import datetime as _dt
            if isinstance(lf, _dt.datetime):
                cl["last_fetched"] = lf.isoformat()

        cleaned.append(cl)
    payload = {"release_id": release_id, "listings": cleaned}
    resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def log_play(user_id: int, release_id: int, played_at: str | None = None, source: str | None = None, notes: str | None = None) -> Dict[str, Any]:
    url = f"{_api_url().rstrip('/')}/users/{user_id}/plays"
    payload = {"release_id": release_id}
    if played_at:
        payload["played_at"] = played_at
    if source:
        payload["source"] = source
    if notes:
        payload["notes"] = notes

    resp = requests.post(url, json=payload, headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()
