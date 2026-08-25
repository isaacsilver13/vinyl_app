"""
probe_listings.py
-----------------
Standalone script: fetches the first album in the Discogs wantlist, then
keeps retrying until for-sale listing data is returned.

No database dependency. Run from the repo root:
    python vinyl/probe_listings.py
"""

from __future__ import annotations

import os
import re
import sys
import time

from dotenv import load_dotenv

# ── Load credentials from vinyl/.env ─────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_HERE, ".env"))

DISCOGS_TOKEN    = os.environ.get("DISCOGS_TOKEN", "")
DISCOGS_USERNAME = os.environ.get("DISCOGS_USERNAME", "")
APP_NAME         = os.environ.get("DISCOGS_APP_NAME", "VinylCatalogApp/1.0")

if not DISCOGS_TOKEN:
    sys.exit("ERROR: DISCOGS_TOKEN is not set. Add it to vinyl/.env")

_RATE_SLEEP  = 1.1   # seconds between API calls
_MAX_RETRIES = 10    # give up after this many failed attempts per listing fetch
_BACKOFF_CAP = 60    # maximum seconds to wait between retries

_API_HEADERS = {
    "Authorization": f"Discogs token={DISCOGS_TOKEN}",
    "User-Agent":    APP_NAME,
    "Accept":        "application/json",
}


# ── Step 1: Get the first wantlist item ───────────────────────────────────────

def get_first_wantlist_item() -> dict:
    """
    Connects to Discogs and returns basic info about the first item
    in the authenticated user's wantlist.
    """
    import discogs_client

    print("Connecting to Discogs API…")
    client = discogs_client.Client(APP_NAME, user_token=DISCOGS_TOKEN)

    username = DISCOGS_USERNAME
    if not username:
        print("Resolving username via identity endpoint…")
        username = client.identity().username
        time.sleep(_RATE_SLEEP)
    print(f"Authenticated as: {username}")

    print("Fetching first wantlist item…")
    user = client.user(username)
    for item in user.wantlist:
        release = item.release
        try:
            artists = release.artists
            artist  = ", ".join(a.name for a in artists) if artists else ""
        except Exception:
            artist = ""
        title      = getattr(release, "title", "")
        release_id = release.id
        try:
            year = int(release.year) if release.year else None
        except (ValueError, TypeError):
            year = None
        print(f"\nFirst wantlist item:")
        print(f"  Release ID : {release_id}")
        print(f"  Artist     : {artist}")
        print(f"  Title      : {title}")
        print(f"  Year       : {year or '?'}")
        return {
            "release_id": release_id,
            "artist":     artist,
            "title":      title,
            "year":       year,
        }

    sys.exit("Wantlist is empty — nothing to probe.")


# ── Step 2a: Scrape the sell/list page ───────────────────────────────────────

def _scrape_listings(release_id: int) -> list[dict]:
    """
    Scrape https://www.discogs.com/sell/list?release_id={id} using
    cloudscraper + BeautifulSoup. Returns a list of listing dicts.
    """
    try:
        import cloudscraper
        from bs4 import BeautifulSoup, NavigableString
    except ImportError as exc:
        print(f"  [scrape] cloudscraper/beautifulsoup4 not available: {exc}")
        return []

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    url = (
        f"https://www.discogs.com/sell/list"
        f"?release_id={release_id}&sort=price%2Casc"
    )
    listings: list[dict] = []

    resp = scraper.get(url, timeout=20)
    time.sleep(_RATE_SLEEP)

    if resp.status_code != 200:
        print(f"  [scrape] HTTP {resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("tr.shortcut_navigable")
    if not rows:
        print("  [scrape] No listing rows found in page HTML")
        return []

    for row in rows:
        try:
            price_el  = row.select_one("span.price[data-pricevalue]")
            price_val = float(price_el["data-pricevalue"]) if price_el and price_el.get("data-pricevalue") else None
            currency  = price_el.get("data-currency", "USD") if price_el else "USD"

            link_el    = row.select_one("a.item_description_title")
            href       = link_el.get("href", "") if link_el else ""
            m          = re.search(r"/sell/item/(\d+)", href)
            listing_id = int(m.group(1)) if m else None
            listing_url = f"https://www.discogs.com{href}" if href else None

            cond_span  = row.select_one("p.item_condition > span:not(.mplabel)")
            first_text = next(
                (c for c in cond_span.contents if isinstance(c, NavigableString)),
                None,
            ) if cond_span else None
            condition = first_text.strip() if first_text else None

            sleeve_el = row.select_one("span.item_sleeve_condition")
            sleeve    = sleeve_el.get_text(strip=True) if sleeve_el else None

            seller_el = row.select_one("td.seller_info strong a")
            seller    = seller_el.get_text(strip=True) if seller_el else None

            ships_from = None
            seller_td  = row.select_one("td.seller_info")
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
            print(f"  [scrape] Row parse error: {exc}")

    return listings


# ── Step 2b: Stats API fallback ───────────────────────────────────────────────

def _stats_fallback(release_id: int) -> list[dict]:
    """
    Hit GET /marketplace/stats/{release_id} for aggregate data when
    scraping is unavailable or blocked.
    """
    import requests

    try:
        resp = requests.get(
            f"https://api.discogs.com/marketplace/stats/{release_id}",
            headers=_API_HEADERS,
            timeout=10,
        )
        time.sleep(_RATE_SLEEP)
        if resp.status_code != 200:
            print(f"  [stats API] HTTP {resp.status_code}")
            return []
        data = resp.json()
        if data.get("blocked_from_sale"):
            print("  [stats API] Release is blocked from sale")
            return []
        num_for_sale = data.get("num_for_sale") or 0
        lp           = data.get("lowest_price") or {}
        lowest_price = lp.get("value") if isinstance(lp, dict) else None
        currency     = lp.get("currency", "USD") if isinstance(lp, dict) else "USD"
        return [{
            "for_sale":  num_for_sale,
            "price":     lowest_price,
            "currency":  currency,
            "url":       f"https://www.discogs.com/sell/list?release_id={release_id}",
        }]
    except Exception as exc:
        print(f"  [stats API] Error: {exc}")
        return []


# ── Step 2c: Per-listing enrichment (ships_to_us, price_usd) ─────────────────

def _enrich_listings(listings: list[dict]) -> None:
    """
    Calls GET /marketplace/listings/{id} for each scraped listing to add:
      ships_to_us    — 1 = ships to US, 0 = blocked, None = unknown
      price_usd      — price converted to USD
      shipping_notes — seller's raw shipping text
    Mutates listings in place.
    """
    import requests

    print(f"  [enrich] Fetching per-listing data for {len(listings)} listing(s)\u2026")
    for lst in listings:
        lid = lst.get("listing_id")
        # US sellers already priced in USD — skip the API call
        if lst.get("ships_from") == "United States" and lst.get("currency") == "USD":
            lst["price_usd"]      = lst.get("price")
            lst["ships_to_us"]    = 1
            lst["shipping_notes"] = None
            continue
        if not lid:
            lst["price_usd"]      = lst.get("price") if lst.get("currency") == "USD" else None
            lst["ships_to_us"]    = None
            lst["shipping_notes"] = None
            continue
        try:
            r = requests.get(
                f"https://api.discogs.com/marketplace/listings/{lid}",
                params={"curr_abbr": "USD"},
                headers=_API_HEADERS,
                timeout=10,
            )
            time.sleep(_RATE_SLEEP)
            if r.status_code == 200:
                d = r.json()
                p = d.get("price") or {}
                lst["price_usd"] = p.get("value") if p.get("currency") == "USD" else lst.get("price")
                notes = (d.get("seller") or {}).get("shipping") or ""
                lst["shipping_notes"] = notes
                if d.get("shipping_is_blocked"):
                    lst["ships_to_us"] = 0
                elif any(kw in notes.lower() for kw in
                         ("united states", "worldwide", "international", " us ", "u.s.", "(us)")):
                    lst["ships_to_us"] = 1
                elif lst.get("ships_from") == "United States":
                    lst["ships_to_us"] = 1
                else:
                    lst["ships_to_us"] = None
            else:
                lst["price_usd"]      = lst.get("price") if lst.get("currency") == "USD" else None
                lst["ships_to_us"]    = 1 if lst.get("ships_from") == "United States" else None
                lst["shipping_notes"] = None
        except Exception as exc:
            print(f"  [enrich] Listing {lid}: error: {exc}")
            lst["price_usd"]      = lst.get("price") if lst.get("currency") == "USD" else None
            lst["ships_to_us"]    = 1 if lst.get("ships_from") == "United States" else None
            lst["shipping_notes"] = None


# ── Step 3: Retry loop ────────────────────────────────────────────────────────

def fetch_with_retry(release_id: int) -> list[dict]:
    """
    Try scraping, then the stats fallback. If both return nothing, wait
    with exponential backoff and retry until data arrives or max retries
    is exhausted.
    """
    backoff = 5

    for attempt in range(1, _MAX_RETRIES + 1):
        print(f"\n--- Attempt {attempt}/{_MAX_RETRIES} ---")

        # Try A: scrape
        print("Trying cloudscraper scrape…")
        listings = _scrape_listings(release_id)
        if listings:
            print(f"Scrape succeeded: {len(listings)} listing(s) found.")
            _enrich_listings(listings)
            return listings

        # Try B: stats API
        print("Scrape returned nothing. Trying stats API fallback…")
        listings = _stats_fallback(release_id)
        if listings:
            print("Stats API fallback succeeded.")
            return listings

        # Both failed
        if attempt < _MAX_RETRIES:
            print(f"Both methods returned no data. Waiting {backoff}s before retry…")
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP)
        else:
            print("Max retries reached.")

    return []


# ── Step 4: Pretty-print results ─────────────────────────────────────────────

def _print_results(album: dict, listings: list[dict]) -> None:
    label = f"{album['artist']} — {album['title']}"
    if album.get("year"):
        label += f" ({album['year']})"
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Release ID: {album['release_id']}")
    print(f"  {len(listings)} listing(s) returned")
    print(f"{'='*60}")

    for i, lst in enumerate(listings, 1):
        # Aggregate (stats fallback) format
        if "for_sale" in lst:
            print(
                f"\n[Aggregate summary]"
                f"\n  For sale : {lst.get('for_sale')}"
                f"\n  Lowest   : {lst.get('price')} {lst.get('currency', '')}"
                f"\n  URL      : {lst.get('url')}"
            )
            continue

        # Per-listing (scrape) format
        price_str = (
            f"{lst['price']:.2f} {lst['currency']}" if lst.get("price") is not None else "N/A"
        )
        price_usd = lst.get("price_usd")
        price_usd_str = f"${price_usd:.2f} USD" if price_usd is not None else "N/A"
        ships_str = {1: "Yes", 0: "No"}.get(lst.get("ships_to_us"), "?")
        print(
            f"\n[{i}] {lst.get('condition', '?')} / Sleeve: {lst.get('sleeve', '?')}"
            f"\n  Seller      : {lst.get('seller', '?')}"
            f"\n  Ships from  : {lst.get('ships_from', '?')}"
            f"\n  Ships to US : {ships_str}"
            f"\n  Price       : {price_str}"
            f"\n  Price (USD) : {price_usd_str}"
            f"\n  URL         : {lst.get('url', '?')}"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    album    = get_first_wantlist_item()
    listings = fetch_with_retry(album["release_id"])

    if listings:
        _print_results(album, listings)
    else:
        print(
            f"\nCould not retrieve listing data for release {album['release_id']}"
            f" after {_MAX_RETRIES} attempts."
        )
        sys.exit(1)
