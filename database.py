from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

_VINYL_DIR = Path(__file__).parent
# If VINYL_DATA_DIR is set (e.g. on Fly.io), store all DB files there so they
# land on the persistent volume.  Otherwise fall back to the source directory.
_DATA_DIR  = Path(os.environ.get("VINYL_DATA_DIR", _VINYL_DIR))
DB_PATH    = _DATA_DIR / "vinyl.db"


def get_conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection to the given DB file, defaulting to vinyl.db.

    db_path may be:
    - None              → uses DB_PATH (vinyl.db)
    - an absolute Path  → used directly
    - a bare filename   → resolved relative to the vinyl/ directory
    """
    if db_path is None:
        path = DB_PATH
    elif Path(db_path).is_absolute():
        path = Path(db_path)
    else:
        path = _DATA_DIR / db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-32000")  # 32 MB page cache
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def get_master_conn() -> sqlite3.Connection:
    """Connection to the master vinyl.db that holds the users table."""
    return get_conn(None)


# ── Users (multi-user auth) ───────────────────────────────────────────────────

def init_users_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username         TEXT    NOT NULL UNIQUE,
            password_hash    TEXT    NOT NULL,
            discogs_token    TEXT    NOT NULL,
            discogs_username TEXT    NOT NULL,
            db_path          TEXT    NOT NULL,
            email            TEXT
        );
    """)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    except Exception:
        pass


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


def get_all_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM users ORDER BY user_id").fetchall()
    return [dict(r) for r in rows]


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password_hash: str,
    discogs_token: str,
    discogs_username: str,
    db_path: str,
    email: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO users (username, password_hash, discogs_token, discogs_username, db_path, email)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (username, password_hash, discogs_token, discogs_username, db_path, email),
    )


def init_user_db(db_path: str | Path) -> None:
    """Scaffold all per-user data tables in a fresh (or existing) DB file."""
    with get_conn(db_path) as conn:
        _init_data_tables(conn)
        init_spotify_tables(conn)


# ── Core DB init ──────────────────────────────────────────────────────────────

def _init_data_tables(conn: sqlite3.Connection) -> None:
    """Create all per-user data tables (no users table)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS releases (
            release_id          INTEGER NOT NULL,
            instance_id         INTEGER NOT NULL,
            title               TEXT,
            artist              TEXT,
            year                INTEGER,
            discogs_genres      TEXT DEFAULT '[]',
            discogs_styles      TEXT DEFAULT '[]',
            cover_url           TEXT,
            format              TEXT,
            format_descriptions TEXT DEFAULT '[]',
            format_text         TEXT DEFAULT '',
            last_synced         TEXT,
            PRIMARY KEY (release_id, instance_id)
        );

        CREATE TABLE IF NOT EXISTS custom_tags (
            release_id      INTEGER PRIMARY KEY,
            custom_tags     TEXT DEFAULT '[]',
            notes           TEXT DEFAULT '',
            decade_override INTEGER,
            last_modified   TEXT
        );

        CREATE TABLE IF NOT EXISTS play_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id  INTEGER NOT NULL,
            played_at   TEXT NOT NULL,
            notes       TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS suggestions (
            release_id  INTEGER PRIMARY KEY,
            title       TEXT,
            artist      TEXT,
            year        INTEGER,
            genres      TEXT DEFAULT '[]',
            styles      TEXT DEFAULT '[]',
            cover_url   TEXT,
            last_fetched TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_meta (
            id                      INTEGER PRIMARY KEY CHECK (id = 1),
            last_full_sync          TEXT,
            last_suggestions_fetch  TEXT,
            total_collection_value  REAL
        );

        INSERT OR IGNORE INTO sync_meta (id) VALUES (1);

        CREATE TABLE IF NOT EXISTS wantlist (
            release_id          INTEGER PRIMARY KEY,
            title               TEXT,
            artist              TEXT,
            year                INTEGER,
            genres              TEXT DEFAULT '[]',
            cover_url           TEXT,
            format              TEXT,
            format_descriptions TEXT DEFAULT '[]',
            format_text         TEXT DEFAULT '',
            notes               TEXT DEFAULT '',
            last_fetched        TEXT
        );

        CREATE TABLE IF NOT EXISTS wantlist_listings (
            listing_id       INTEGER PRIMARY KEY,
            release_id       INTEGER NOT NULL,
            price            REAL,
            currency         TEXT,
            condition        TEXT,
            sleeve_condition TEXT,
            ships_from       TEXT,
            seller           TEXT,
            listing_url      TEXT,
            last_fetched     TEXT,
            first_seen       TEXT
        );

        CREATE TABLE IF NOT EXISTS listing_alerts (
            alert_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER,
            listing_id     INTEGER NOT NULL,
            release_id     INTEGER NOT NULL,
            alert_type     TEXT    NOT NULL DEFAULT 'new_listing',
            artist         TEXT,
            title          TEXT,
            price_usd      REAL,
            prev_price_usd REAL,
            change_pct     REAL,
            currency       TEXT,
            condition      TEXT,
            seller         TEXT,
            ships_from     TEXT,
            listing_url    TEXT,
            first_seen     TEXT,
            created_at     TEXT    NOT NULL,
            email_sent_at  TEXT,
            email_status   TEXT,
            email_error    TEXT,
            UNIQUE(user_id, listing_id, alert_type)
        );

        CREATE TABLE IF NOT EXISTS wantlist_price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id  INTEGER NOT NULL,
            release_id  INTEGER NOT NULL,
            price_usd   REAL,
            condition   TEXT,
            fetched_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS listing_enrichment_cache (
            listing_id      INTEGER PRIMARY KEY,
            price_usd       REAL,
            ships_to_us     INTEGER,
            shipping_notes  TEXT,
            fetched_at      TEXT NOT NULL
        );
    """)
    # Migrate existing databases: add columns if not already present
    for _sql in [
        "ALTER TABLE sync_meta ADD COLUMN last_wantlist_sync TEXT",
        "ALTER TABLE wantlist ADD COLUMN format TEXT",
        "ALTER TABLE wantlist ADD COLUMN format_descriptions TEXT DEFAULT '[]'",
        "ALTER TABLE wantlist ADD COLUMN format_text TEXT DEFAULT ''",
        "ALTER TABLE releases ADD COLUMN format_descriptions TEXT DEFAULT '[]'",
        "ALTER TABLE releases ADD COLUMN format_text TEXT DEFAULT ''",
        "ALTER TABLE wantlist_listings ADD COLUMN price_usd REAL",
        "ALTER TABLE wantlist_listings ADD COLUMN ships_to_us INTEGER",
        "ALTER TABLE wantlist_listings ADD COLUMN shipping_notes TEXT",
        "ALTER TABLE wantlist_listings ADD COLUMN first_seen TEXT",
        "ALTER TABLE listing_alerts ADD COLUMN user_id INTEGER",
        "ALTER TABLE listing_alerts ADD COLUMN prev_price_usd REAL",
        "ALTER TABLE listing_alerts ADD COLUMN change_pct REAL",
    ]:
        try:
            conn.execute(_sql)
        except Exception:
            pass
    # Backfill first_seen for existing rows where missing.
    try:
        conn.execute("""
            UPDATE wantlist_listings
            SET first_seen = COALESCE(first_seen, last_fetched)
            WHERE first_seen IS NULL
        """)
    except Exception:
        pass
    # Indexes for frequently-filtered foreign keys
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_play_log_release_id
            ON play_log(release_id);
        CREATE INDEX IF NOT EXISTS idx_wantlist_listings_release_id
            ON wantlist_listings(release_id);
        CREATE INDEX IF NOT EXISTS idx_price_history_listing
            ON wantlist_price_history(listing_id, fetched_at);
        CREATE INDEX IF NOT EXISTS idx_releases_artist_title
            ON releases(artist, title);
        CREATE INDEX IF NOT EXISTS idx_enrichment_cache_fetched
            ON listing_enrichment_cache(fetched_at);
        CREATE INDEX IF NOT EXISTS idx_wantlist_listings_first_seen
            ON wantlist_listings(first_seen);
        CREATE INDEX IF NOT EXISTS idx_listing_alerts_unsent
            ON listing_alerts(email_sent_at, alert_type);
        CREATE INDEX IF NOT EXISTS idx_listing_alerts_user
            ON listing_alerts(user_id, alert_type, email_sent_at);
    """)


def init_db():
    with get_conn() as conn:
        _init_data_tables(conn)
        init_users_table(conn)
        init_spotify_tables(conn)


# ── releases ─────────────────────────────────────────────────────────────────

def upsert_release(conn: sqlite3.Connection, r: dict):
    conn.execute("""
        INSERT INTO releases
            (release_id, instance_id, title, artist, year,
             discogs_genres, discogs_styles, cover_url, format,
             format_descriptions, format_text, last_synced)
        VALUES
            (:release_id, :instance_id, :title, :artist, :year,
             :discogs_genres, :discogs_styles, :cover_url, :format,
             :format_descriptions, :format_text, :last_synced)
        ON CONFLICT(release_id, instance_id) DO UPDATE SET
            title               = excluded.title,
            artist              = excluded.artist,
            year                = excluded.year,
            discogs_genres      = excluded.discogs_genres,
            discogs_styles      = excluded.discogs_styles,
            cover_url           = excluded.cover_url,
            format              = excluded.format,
            format_descriptions = excluded.format_descriptions,
            format_text         = excluded.format_text,
            last_synced         = excluded.last_synced
    """, {
        **r,
        "discogs_genres":      json.dumps(r.get("discogs_genres", [])),
        "discogs_styles":      json.dumps(r.get("discogs_styles", [])),
        "format_descriptions": json.dumps(r.get("format_descriptions", [])),
    })


def clear_releases(conn: sqlite3.Connection):
    conn.execute("DELETE FROM releases")


def get_all_releases(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM releases ORDER BY artist, title").fetchall()
    return [_decode_release(dict(r)) for r in rows]


def _decode_release(r: dict) -> dict:
    r["discogs_genres"]      = json.loads(r.get("discogs_genres") or "[]")
    r["discogs_styles"]      = json.loads(r.get("discogs_styles") or "[]")
    r["format_descriptions"] = json.loads(r.get("format_descriptions") or "[]")
    return r


# ── custom tags ───────────────────────────────────────────────────────────────

def get_custom_tags(conn: sqlite3.Connection, release_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM custom_tags WHERE release_id = ?", (release_id,)
    ).fetchone()
    if not row:
        return {"release_id": release_id, "custom_tags": [], "notes": "", "decade_override": None}
    d = dict(row)
    d["custom_tags"] = json.loads(d.get("custom_tags") or "[]")
    return d


def get_all_custom_tags(conn: sqlite3.Connection) -> dict[int, dict]:
    """Bulk-load all custom_tags rows in one query. Returns {release_id: row_dict}."""
    rows = conn.execute("SELECT * FROM custom_tags").fetchall()
    result: dict[int, dict] = {}
    for row in rows:
        d = dict(row)
        d["custom_tags"] = json.loads(d.get("custom_tags") or "[]")
        result[d["release_id"]] = d
    return result


def save_custom_tags(conn: sqlite3.Connection, release_id: int, tags: list[str],
                     notes: str, decade_override: int | None):
    conn.execute("""
        INSERT INTO custom_tags (release_id, custom_tags, notes, decade_override, last_modified)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
            custom_tags     = excluded.custom_tags,
            notes           = excluded.notes,
            decade_override = excluded.decade_override,
            last_modified   = excluded.last_modified
    """, (release_id, json.dumps(tags), notes, decade_override, datetime.utcnow().isoformat()))


# ── play log ──────────────────────────────────────────────────────────────────

def log_play(conn: sqlite3.Connection, release_id: int, notes: str = ""):
    conn.execute(
        "INSERT INTO play_log (release_id, played_at, notes) VALUES (?, ?, ?)",
        (release_id, datetime.utcnow().isoformat(), notes)
    )


def get_play_stats(conn: sqlite3.Connection) -> dict[int, dict]:
    """Returns {release_id: {count, last_played}} for all releases that have plays."""
    rows = conn.execute("""
        SELECT release_id,
               COUNT(*)    AS play_count,
               MAX(played_at) AS last_played
        FROM play_log
        GROUP BY release_id
    """).fetchall()
    return {r["release_id"]: {"count": r["play_count"], "last_played": r["last_played"]} for r in rows}


def get_recent_plays(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute("""
        SELECT p.id, p.release_id, p.played_at, p.notes,
               r.title, r.artist
        FROM play_log p
        LEFT JOIN releases r ON r.release_id = p.release_id
        ORDER BY p.played_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def delete_play(conn: sqlite3.Connection, play_id: int) -> None:
    conn.execute("DELETE FROM play_log WHERE id = ?", (play_id,))


def get_play_counts_by_date(conn: sqlite3.Connection, days: int = 365) -> dict[str, int]:
    """Returns {YYYY-MM-DD: count} for plays in the past `days` days."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    rows = conn.execute("""
        SELECT DATE(played_at) AS day, COUNT(*) AS cnt
        FROM play_log
        WHERE played_at >= ?
        GROUP BY day
    """, (cutoff,)).fetchall()
    return {r["day"]: r["cnt"] for r in rows}


def get_top_albums_since(conn: sqlite3.Connection, since_date: str, limit: int = 10) -> list[dict]:
    """Returns [{title, artist, cnt}] sorted by play count for plays since since_date."""
    rows = conn.execute("""
        SELECT r.title, r.artist, COUNT(*) AS cnt
        FROM play_log p
        LEFT JOIN releases r ON r.release_id = p.release_id
        WHERE p.played_at >= ?
        GROUP BY p.release_id
        ORDER BY cnt DESC
        LIMIT ?
    """, (since_date, limit)).fetchall()
    return [dict(r) for r in rows]


def get_play_stats_since(conn: sqlite3.Connection, since_date: str) -> dict[int, dict]:
    """Like get_play_stats() but restricted to plays on or after since_date."""
    rows = conn.execute("""
        SELECT release_id, COUNT(*) AS play_count, MAX(played_at) AS last_played
        FROM play_log
        WHERE played_at >= ?
        GROUP BY release_id
    """, (since_date,)).fetchall()
    return {r["release_id"]: {"count": r["play_count"], "last_played": r["last_played"]} for r in rows}


# ── duplicates ────────────────────────────────────────────────────────────────

def get_duplicate_groups(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """
    Returns two groups:
      'exact'    — same release_id, multiple instance_ids
      'pressing' — same artist+title, different release_ids
    """
    # Exact duplicates: same release_id, multiple instance_ids
    exact_rows = conn.execute("""
        SELECT * FROM releases
        WHERE release_id IN (
            SELECT release_id FROM releases
            GROUP BY release_id HAVING COUNT(*) > 1
        )
        ORDER BY release_id
    """).fetchall()
    exact: dict[int, list[dict]] = {}
    for row in exact_rows:
        r = _decode_release(dict(row))
        exact.setdefault(r["release_id"], []).append(r)

    # Pressing duplicates: same artist+title, different release_ids
    pressing_rows = conn.execute("""
        SELECT * FROM releases
        WHERE (artist, title) IN (
            SELECT artist, title FROM releases
            GROUP BY artist, title HAVING COUNT(DISTINCT release_id) > 1
        )
        ORDER BY artist, title, release_id
    """).fetchall()
    pressing: dict[str, list[dict]] = {}
    seen_release_ids: set[int] = set()
    for row in pressing_rows:
        r = _decode_release(dict(row))
        key = f"{r['artist']}|||{r['title']}"
        pressing.setdefault(key, [])
        if r["release_id"] not in seen_release_ids:
            pressing[key].append(r)
            seen_release_ids.add(r["release_id"])
    pressing_dupes = {k: v for k, v in pressing.items() if len(v) > 1}

    return {"exact": list(exact.values()), "pressing": list(pressing_dupes.values())}


# ── suggestions ───────────────────────────────────────────────────────────────

def upsert_suggestion(conn: sqlite3.Connection, s: dict):
    conn.execute("""
        INSERT INTO suggestions
            (release_id, title, artist, year, genres, styles, cover_url, last_fetched)
        VALUES
            (:release_id, :title, :artist, :year, :genres, :styles, :cover_url, :last_fetched)
        ON CONFLICT(release_id) DO UPDATE SET
            title        = excluded.title,
            artist       = excluded.artist,
            year         = excluded.year,
            genres       = excluded.genres,
            styles       = excluded.styles,
            cover_url    = excluded.cover_url,
            last_fetched = excluded.last_fetched
    """, {**s, "genres": json.dumps(s.get("genres", [])), "styles": json.dumps(s.get("styles", []))})


def get_suggestions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT * FROM suggestions
        WHERE release_id NOT IN (SELECT DISTINCT release_id FROM releases)
        ORDER BY artist, title
    """).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["genres"] = json.loads(d.get("genres") or "[]")
        d["styles"] = json.loads(d.get("styles") or "[]")
        result.append(d)
    return result


# ── sync meta ─────────────────────────────────────────────────────────────────

def get_sync_meta(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM sync_meta WHERE id = 1").fetchone()
    return dict(row) if row else {}


def update_sync_meta(conn: sqlite3.Connection, **kwargs):
    sets = ", ".join(f"{k} = :{k}" for k in kwargs)
    conn.execute(f"UPDATE sync_meta SET {sets} WHERE id = 1", kwargs)


# ── wantlist ──────────────────────────────────────────────────────────────────

def upsert_wantlist_item(conn: sqlite3.Connection, item: dict) -> None:
    conn.execute("""
        INSERT INTO wantlist
            (release_id, title, artist, year, genres, cover_url,
             format, format_descriptions, format_text, notes, last_fetched)
        VALUES
            (:release_id, :title, :artist, :year, :genres, :cover_url,
             :format, :format_descriptions, :format_text, :notes, :last_fetched)
        ON CONFLICT(release_id) DO UPDATE SET
            title               = excluded.title,
            artist              = excluded.artist,
            year                = excluded.year,
            genres              = excluded.genres,
            cover_url           = excluded.cover_url,
            format              = excluded.format,
            format_descriptions = excluded.format_descriptions,
            format_text         = excluded.format_text,
            notes               = excluded.notes,
            last_fetched        = excluded.last_fetched
    """, {
        **item,
        "genres":              json.dumps(item.get("genres", [])),
        "format_descriptions": json.dumps(item.get("format_descriptions", [])),
    })


def get_wantlist(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM wantlist ORDER BY artist, title").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["genres"]              = json.loads(d.get("genres") or "[]")
        d["format_descriptions"] = json.loads(d.get("format_descriptions") or "[]")
        result.append(d)
    return result


def clear_wantlist(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM wantlist")


def upsert_wantlist_listing(conn: sqlite3.Connection, listing: dict) -> None:
    _existing = conn.execute(
        "SELECT first_seen FROM wantlist_listings WHERE listing_id = ?",
        (listing.get("listing_id"),),
    ).fetchone()
    _first_seen = None
    if _existing and _existing["first_seen"]:
        _first_seen = _existing["first_seen"]
    else:
        _first_seen = listing.get("last_fetched") or datetime.utcnow().isoformat()

    conn.execute("""
        INSERT INTO wantlist_listings
            (listing_id, release_id, price, currency, condition, sleeve_condition,
             ships_from, seller, listing_url, last_fetched,
             price_usd, ships_to_us, shipping_notes, first_seen)
        VALUES
            (:listing_id, :release_id, :price, :currency, :condition, :sleeve_condition,
             :ships_from, :seller, :listing_url, :last_fetched,
             :price_usd, :ships_to_us, :shipping_notes, :first_seen)
        ON CONFLICT(listing_id) DO UPDATE SET
            price            = excluded.price,
            currency         = excluded.currency,
            condition        = excluded.condition,
            sleeve_condition = excluded.sleeve_condition,
            ships_from       = excluded.ships_from,
            seller           = excluded.seller,
            listing_url      = excluded.listing_url,
            last_fetched     = excluded.last_fetched,
            price_usd        = excluded.price_usd,
            ships_to_us      = excluded.ships_to_us,
            shipping_notes   = excluded.shipping_notes,
            first_seen       = wantlist_listings.first_seen
    """, {**listing, "first_seen": _first_seen})
    # Snapshot price for historical trend tracking
    if listing.get("listing_id") is not None and listing.get("price_usd") is not None:
        conn.execute("""
            INSERT INTO wantlist_price_history
                (listing_id, release_id, price_usd, condition, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            listing["listing_id"],
            listing["release_id"],
            listing["price_usd"],
            listing.get("condition"),
            listing.get("last_fetched") or datetime.utcnow().isoformat(),
        ))


def bulk_upsert_wantlist_listings(
    conn: sqlite3.Connection,
    release_id: int,
    rows: list[dict],
) -> int:
    """Delete existing listings for release_id and bulk-insert rows in one transaction.

    Each row must match the wantlist_listings schema (use _map_to_db() from callers).
    Also snapshots price history for any row with a non-null price_usd.
    Returns the count of listing rows inserted.
    """
    _existing = conn.execute(
        "SELECT listing_id, first_seen FROM wantlist_listings WHERE release_id = ?",
        (release_id,),
    ).fetchall()
    _first_seen_map = {
        r["listing_id"]: r["first_seen"]
        for r in _existing
        if r["listing_id"] is not None and r["first_seen"]
    }

    conn.execute("DELETE FROM wantlist_listings WHERE release_id = ?", (release_id,))
    if not rows:
        return 0

    _prepared_rows = []
    for r in rows:
        _lid = r.get("listing_id")
        _first_seen = _first_seen_map.get(_lid) or r.get("last_fetched") or datetime.utcnow().isoformat()
        _prepared_rows.append({**r, "first_seen": _first_seen})

    conn.executemany("""
        INSERT INTO wantlist_listings
            (listing_id, release_id, price, currency, condition, sleeve_condition,
             ships_from, seller, listing_url, last_fetched,
             price_usd, ships_to_us, shipping_notes, first_seen)
        VALUES
            (:listing_id, :release_id, :price, :currency, :condition, :sleeve_condition,
             :ships_from, :seller, :listing_url, :last_fetched,
             :price_usd, :ships_to_us, :shipping_notes, :first_seen)
        ON CONFLICT(listing_id) DO UPDATE SET
            price            = excluded.price,
            currency         = excluded.currency,
            condition        = excluded.condition,
            sleeve_condition = excluded.sleeve_condition,
            ships_from       = excluded.ships_from,
            seller           = excluded.seller,
            listing_url      = excluded.listing_url,
            last_fetched     = excluded.last_fetched,
            price_usd        = excluded.price_usd,
            ships_to_us      = excluded.ships_to_us,
            shipping_notes   = excluded.shipping_notes,
            first_seen       = wantlist_listings.first_seen
    """, _prepared_rows)
    history_rows = [
        (
            r["listing_id"],
            r["release_id"],
            r["price_usd"],
            r.get("condition"),
            r.get("last_fetched") or datetime.utcnow().isoformat(),
        )
        for r in _prepared_rows
        if r.get("listing_id") is not None and r.get("price_usd") is not None
    ]
    if history_rows:
        conn.executemany("""
            INSERT INTO wantlist_price_history
                (listing_id, release_id, price_usd, condition, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, history_rows)
    return len(_prepared_rows)


def load_enrichment_cache(
    conn: sqlite3.Connection,
    ttl_hours: int = 2,
) -> dict[int, dict]:
    """Return {listing_id: {price_usd, ships_to_us, shipping_notes}} for entries
    fetched within the last ttl_hours.  Used to skip redundant API enrichment calls.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=ttl_hours)).isoformat()
    rows = conn.execute(
        """
        SELECT listing_id, price_usd, ships_to_us, shipping_notes
        FROM listing_enrichment_cache
        WHERE fetched_at > ?
        """,
        (cutoff,),
    ).fetchall()
    return {
        r["listing_id"]: {
            "price_usd":      r["price_usd"],
            "ships_to_us":    r["ships_to_us"],
            "shipping_notes": r["shipping_notes"],
        }
        for r in rows
    }


def save_enrichment_entries(
    conn: sqlite3.Connection,
    entries: list[dict],
) -> None:
    """Bulk-persist listing enrichment results so future runs can skip API calls.

    Each entry: {listing_id, price_usd, ships_to_us, shipping_notes}.
    """
    if not entries:
        return
    ts = datetime.utcnow().isoformat()
    conn.executemany("""
        INSERT INTO listing_enrichment_cache
            (listing_id, price_usd, ships_to_us, shipping_notes, fetched_at)
        VALUES
            (:listing_id, :price_usd, :ships_to_us, :shipping_notes, :fetched_at)
        ON CONFLICT(listing_id) DO UPDATE SET
            price_usd      = excluded.price_usd,
            ships_to_us    = excluded.ships_to_us,
            shipping_notes = excluded.shipping_notes,
            fetched_at     = excluded.fetched_at
    """, [{**e, "fetched_at": ts} for e in entries])


def get_wantlist_listings(conn: sqlite3.Connection, release_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM wantlist_listings WHERE release_id = ? ORDER BY price ASC",
        (release_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_existing_listing_ids(conn: sqlite3.Connection, release_id: int) -> set[int]:
    rows = conn.execute(
        """
        SELECT listing_id
        FROM wantlist_listings
        WHERE release_id = ?
          AND listing_id IS NOT NULL
        """,
        (release_id,),
    ).fetchall()
    return {int(r["listing_id"]) for r in rows if r["listing_id"] is not None}


def get_wantlist_listings_first_seen_at(
    conn: sqlite3.Connection,
    release_id: int,
    first_seen_ts: str,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM wantlist_listings
        WHERE release_id = ? AND first_seen = ?
        ORDER BY price_usd ASC, listing_id ASC
        """,
        (release_id, first_seen_ts),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_new_listing_ids(conn: sqlite3.Connection, since_hours: int = 24) -> set[int]:
    cutoff = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
    rows = conn.execute(
        """
        SELECT listing_id
        FROM wantlist_listings
        WHERE listing_id IS NOT NULL
          AND first_seen IS NOT NULL
          AND first_seen >= ?
        """,
        (cutoff,),
    ).fetchall()
    return {int(r["listing_id"]) for r in rows if r["listing_id"] is not None}


def create_new_listing_alerts(
    conn: sqlite3.Connection,
    user_id: int,
    artist: str,
    title: str,
    new_rows: list[dict],
) -> int:
    if not new_rows:
        return 0
    now = datetime.utcnow().isoformat()
    payload = [
        {
            "user_id": user_id,
            "listing_id": r.get("listing_id"),
            "release_id": r.get("release_id"),
            "alert_type": "new_listing",
            "artist": artist,
            "title": title,
            "price_usd": r.get("price_usd"),
            "currency": r.get("currency"),
            "condition": r.get("condition"),
            "seller": r.get("seller"),
            "ships_from": r.get("ships_from"),
            "listing_url": r.get("listing_url"),
            "first_seen": r.get("first_seen"),
            "created_at": now,
        }
        for r in new_rows
        if r.get("listing_id") is not None
    ]
    if not payload:
        return 0

    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO listing_alerts
            (user_id, listing_id, release_id, alert_type, artist, title, price_usd, currency,
             condition, seller, ships_from, listing_url, first_seen, created_at)
        VALUES
            (:user_id, :listing_id, :release_id, :alert_type, :artist, :title, :price_usd, :currency,
             :condition, :seller, :ships_from, :listing_url, :first_seen, :created_at)
        """,
        payload,
    )
    inserted = conn.total_changes - before
    return max(0, inserted)


def create_price_change_alerts(
    conn: sqlite3.Connection,
    user_id: int,
    artist: str,
    title: str,
    change_rows: list[dict],
) -> int:
    """Insert price-change alerts.

    Each entry in change_rows should include keys:
      listing_id, release_id, price_usd (current), prev_price_usd, change_pct,
      currency, condition, seller, ships_from, listing_url, first_seen
    """
    if not change_rows:
        return 0
    now = datetime.utcnow().isoformat()
    payload = [
        {
            "user_id": user_id,
            "listing_id": r.get("listing_id"),
            "release_id": r.get("release_id"),
            "alert_type": "price_change",
            "artist": artist,
            "title": title,
            "price_usd": r.get("price_usd"),
            "prev_price_usd": r.get("prev_price_usd"),
            "change_pct": r.get("change_pct"),
            "currency": r.get("currency"),
            "condition": r.get("condition"),
            "seller": r.get("seller"),
            "ships_from": r.get("ships_from"),
            "listing_url": r.get("listing_url"),
            "first_seen": r.get("first_seen"),
            "created_at": now,
        }
        for r in change_rows
        if r.get("listing_id") is not None
    ]
    if not payload:
        return 0

    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO listing_alerts
            (user_id, listing_id, release_id, alert_type, artist, title, price_usd, prev_price_usd, change_pct, currency,
             condition, seller, ships_from, listing_url, first_seen, created_at)
        VALUES
            (:user_id, :listing_id, :release_id, :alert_type, :artist, :title, :price_usd, :prev_price_usd, :change_pct, :currency,
             :condition, :seller, :ships_from, :listing_url, :first_seen, :created_at)
        """,
        payload,
    )
    inserted = conn.total_changes - before
    return max(0, inserted)


def get_pending_listing_alerts(
    conn: sqlite3.Connection,
    user_id: int,
    alert_type: str = "new_listing",
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM listing_alerts
                WHERE user_id = ?
                    AND alert_type = ?
          AND email_sent_at IS NULL
        ORDER BY created_at ASC, listing_id ASC
        """,
                (user_id, alert_type),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_listing_alerts_sent(conn: sqlite3.Connection, alert_ids: list[int]) -> None:
    if not alert_ids:
        return
    now = datetime.utcnow().isoformat()
    conn.executemany(
        """
        UPDATE listing_alerts
        SET email_sent_at = ?,
            email_status  = 'sent',
            email_error   = NULL
        WHERE alert_id = ?
        """,
        [(now, int(aid)) for aid in alert_ids],
    )


def mark_listing_alerts_failed(
    conn: sqlite3.Connection,
    alert_ids: list[int],
    error_message: str,
) -> None:
    if not alert_ids:
        return
    trimmed = (error_message or "")[:500]
    conn.executemany(
        """
        UPDATE listing_alerts
        SET email_status = 'failed',
            email_error  = ?
        WHERE alert_id = ?
        """,
        [(trimmed, int(aid)) for aid in alert_ids],
    )


def get_existing_listing_enrichment(
    conn: sqlite3.Connection,
    release_id: int,
) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT listing_id, price, currency, price_usd, ships_to_us, shipping_notes
        FROM wantlist_listings
        WHERE release_id = ? AND listing_id IS NOT NULL
        """,
        (release_id,),
    ).fetchall()
    return {
        r["listing_id"]: {
            "price": r["price"],
            "currency": r["currency"],
            "price_usd": r["price_usd"],
            "ships_to_us": r["ships_to_us"],
            "shipping_notes": r["shipping_notes"],
        }
        for r in rows
    }


def clear_wantlist_listings(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM wantlist_listings")


def get_all_price_trends(conn: sqlite3.Connection) -> dict[int, str]:
    """Returns {listing_id: trend_indicator} for all listings with price history.

    Trend values: 'new' (first snapshot), '↑' (price rose), '↓' (price fell), '→' (unchanged).
    """
    from collections import defaultdict
    rows = conn.execute("""
        SELECT listing_id, price_usd
        FROM (
            SELECT listing_id, price_usd,
                   ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY fetched_at DESC) AS rn
            FROM wantlist_price_history
        )
        WHERE rn <= 2
        ORDER BY listing_id, rn
    """).fetchall()
    by_listing: dict[int, list] = defaultdict(list)
    for r in rows:
        by_listing[r["listing_id"]].append(r["price_usd"])
    trends: dict[int, str] = {}
    for lid, prices in by_listing.items():
        if len(prices) == 1:
            trends[lid] = "new"
        elif prices[0] > prices[1]:
            trends[lid] = "↑"
        elif prices[0] < prices[1]:
            trends[lid] = "↓"
        else:
            trends[lid] = "→"
    return trends


# ── Spotify ───────────────────────────────────────────────────────────────────

def init_spotify_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spotify_tokens (
            id            INTEGER PRIMARY KEY CHECK (id = 1),
            access_token  TEXT NOT NULL,
            refresh_token TEXT,
            expires_at    INTEGER NOT NULL,
            scope         TEXT
        );

        CREATE TABLE IF NOT EXISTS spotify_suggestions (
            release_id    INTEGER PRIMARY KEY,
            title         TEXT,
            artist        TEXT,
            year          TEXT,
            cover_url     TEXT,
            source_artist TEXT,
            genres        TEXT DEFAULT '[]',
            styles        TEXT DEFAULT '[]',
            last_fetched  TEXT
        );
    """)


def save_spotify_tokens(conn: sqlite3.Connection,
                        access_token: str,
                        refresh_token: str | None,
                        expires_at: int,
                        scope: str | None) -> None:
    conn.execute("""
        INSERT INTO spotify_tokens (id, access_token, refresh_token, expires_at, scope)
        VALUES (1, :access_token, :refresh_token, :expires_at, :scope)
        ON CONFLICT(id) DO UPDATE SET
            access_token  = excluded.access_token,
            refresh_token = COALESCE(excluded.refresh_token, spotify_tokens.refresh_token),
            expires_at    = excluded.expires_at,
            scope         = excluded.scope
    """, {"access_token": access_token, "refresh_token": refresh_token,
          "expires_at": expires_at, "scope": scope})


def get_spotify_tokens(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM spotify_tokens WHERE id = 1").fetchone()
    return dict(row) if row else None


def save_spotify_suggestions(conn: sqlite3.Connection, suggestions: list[dict]) -> None:
    conn.execute("DELETE FROM spotify_suggestions")
    for s in suggestions:
        conn.execute("""
            INSERT OR REPLACE INTO spotify_suggestions
                (release_id, title, artist, year, cover_url,
                 source_artist, genres, styles, last_fetched)
            VALUES
                (:release_id, :title, :artist, :year, :cover_url,
                 :source_artist, :genres, :styles, :last_fetched)
        """, {**s,
              "genres": json.dumps(s.get("genres", [])),
              "styles": json.dumps(s.get("styles", []))})


def get_spotify_suggestions(conn: sqlite3.Connection) -> list[dict]:
    owned_ids = {
        r["release_id"]
        for r in conn.execute("SELECT DISTINCT release_id FROM releases").fetchall()
    }
    rows = conn.execute(
        "SELECT * FROM spotify_suggestions ORDER BY source_artist, artist, title"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d["release_id"] in owned_ids:
            continue
        d["genres"] = json.loads(d.get("genres") or "[]")
        d["styles"] = json.loads(d.get("styles") or "[]")
        result.append(d)
    return result


def get_last_spotify_fetch(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT MAX(last_fetched) AS lf FROM spotify_suggestions"
    ).fetchone()
    val = row["lf"] if row else None
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        return None
