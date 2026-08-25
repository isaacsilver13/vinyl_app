"""
refresh_all.py
--------------
Daily refresh script: syncs collection, wantlist, suggestions, and marketplace
listings for every user (or a single specified user).

Run manually or via a scheduler (Windows Task Scheduler, cron, GitHub Actions).

Usage:
    # Refresh all users (respects TTLs)
    python vinyl/refresh_all.py

    # Refresh a single user only
    python vinyl/refresh_all.py --user alice

    # Force-refresh everything, bypassing all TTLs
    python vinyl/refresh_all.py --force

    # Combine
    python vinyl/refresh_all.py --user alice --force

    # Run refresh without sending alert emails
    python vinyl/refresh_all.py --no-alerts
"""

from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(_HERE, ".env"))

import database as db
import discogs_api as api

# ── Config ─────────────────────────────────────────────────────────────────

_LISTING_TTL_HOURS = 24
_VINYL_FORMATS     = {"Vinyl", "LP", '7"', '10"', '12"'}
_PRICE_CHANGE_PCT  = float(os.environ.get("PRICE_CHANGE_PCT", "0.10"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _is_vinyl(fmt: str) -> bool:
    return not fmt or any(v in fmt for v in _VINYL_FORMATS)


def _listings_stale(conn, release_id: int, ttl_hours: int) -> bool:
    row = conn.execute(
        "SELECT last_fetched FROM wantlist_listings WHERE release_id = ? LIMIT 1",
        (release_id,),
    ).fetchone()
    if not row or not row["last_fetched"]:
        return True
    try:
        fetched_at = datetime.fromisoformat(row["last_fetched"]).replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched_at > timedelta(hours=ttl_hours)
    except ValueError:
        return True


def _map_to_db(release_id: int, lst: dict, ts: str) -> dict:
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


def _format_price(price_usd: float | None, currency: str | None) -> str:
    if price_usd is not None:
        return f"${price_usd:.2f}"
    if currency:
        return f"({currency})"
    return "N/A"


def _build_digest_body(username: str, alerts: list[dict]) -> str:
    lines: list[str] = []
    lines.append(f"Hi {username},")
    lines.append("")
    lines.append(f"{len(alerts)} new wantlist listing(s) were found:")
    lines.append("")
    for a in alerts:
        price = _format_price(a.get("price_usd"), a.get("currency"))
        artist = a.get("artist") or "Unknown Artist"
        title = a.get("title") or "Untitled"
        condition = a.get("condition") or "N/A"
        ships_from = a.get("ships_from") or "N/A"
        seller = a.get("seller") or "N/A"
        listing_url = a.get("listing_url") or ""
        lines.append(f"- {artist} — {title}")
        lines.append(f"  Price: {price} | Condition: {condition} | Ships From: {ships_from} | Seller: {seller}")
        if listing_url:
            lines.append(f"  Listing: {listing_url}")
        lines.append("")

    lines.append("--")
    lines.append("Vinyl Catalog")
    return "\n".join(lines)


def _send_email_digest(to_email: str, username: str, alerts: list[dict]) -> tuple[bool, str]:
    host = os.environ.get("SMTP_HOST", "").strip()
    port_raw = os.environ.get("SMTP_PORT", "587").strip()
    smtp_user = os.environ.get("SMTP_USERNAME", "").strip()
    smtp_pass = os.environ.get("SMTP_PASSWORD", "").strip()
    from_email = os.environ.get("ALERT_FROM_EMAIL", "").strip() or smtp_user
    use_tls = os.environ.get("SMTP_USE_TLS", "1").strip().lower() not in {"0", "false", "no"}

    if not host or not from_email:
        return False, "Missing SMTP_HOST or ALERT_FROM_EMAIL configuration."

    try:
        port = int(port_raw)
    except ValueError:
        return False, f"Invalid SMTP_PORT value: {port_raw}"

    msg = EmailMessage()
    # Allow caller to pass pre-formatted alerts and override the subject by providing
    # a specially-named key on the first alert dict: '_subject'. Otherwise use defaults.
    subject = None
    if alerts and isinstance(alerts, list) and isinstance(alerts[0], dict):
        subject = alerts[0].get("_subject")
    if not subject:
        # Default subject depends on alert_type if present
        atype = alerts[0].get("alert_type") if alerts and isinstance(alerts[0], dict) else "new_listing"
        if atype == "price_change":
            subject = f"[Vinyl Catalog] {len(alerts)} price change(s) for {username}"
        else:
            subject = f"[Vinyl Catalog] {len(alerts)} new listing(s) for {username}"
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    # If caller provided a pre-built body in the first alert under '_body', use it.
    body = None
    if alerts and isinstance(alerts[0], dict):
        body = alerts[0].get("_body")
    if not body:
        body = _build_digest_body(username, alerts)
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if smtp_user:
                smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
    except Exception as exc:
        return False, str(exc)

    return True, "sent"


# ── Per-user refresh ─────────────────────────────────────────────────────────

def refresh_user(user: dict, force: bool = False, send_alerts: bool = True) -> bool:
    """Run all refresh steps for one user. Returns True on success."""
    username         = user["username"]
    discogs_token    = user["discogs_token"]
    discogs_username = user["discogs_username"]
    user_db          = user["db_path"]
    user_id          = user["user_id"]
    alert_email      = (user.get("email") or "").strip()

    logger.info("=" * 60)
    logger.info("User: %s  |  DB: %s", username, user_db)
    logger.info("=" * 60)

    # Ensure per-user DB schema is current (idempotent, safe on every run)
    db.init_user_db(user_db)

    try:
        client = api.make_client(discogs_token)
    except Exception as exc:
        logger.error("Failed to build Discogs client for %s: %s", username, exc)
        return False

    # 1 ── Collection sync
    try:
        logger.info("[%s] Step 1/4: Syncing collection…", username)
        count = api.sync_collection(
            force=force,
            log_fn=lambda msg: logger.info("[%s]   %s", username, msg),
            client=client,
            discogs_username=discogs_username,
            db_path=user_db,
        )
        logger.info("[%s] Collection sync done: %d records", username, count)
    except Exception as exc:
        logger.error("[%s] Collection sync failed: %s", username, exc)
        return False

    # 2 ── Wantlist sync
    try:
        logger.info("[%s] Step 2/4: Syncing wantlist…", username)
        count = api.sync_wantlist(
            force=force,
            log_fn=lambda msg: logger.info("[%s]   %s", username, msg),
            client=client,
            discogs_username=discogs_username,
            db_path=user_db,
        )
        logger.info("[%s] Wantlist sync done: %d items", username, count)
    except Exception as exc:
        logger.error("[%s] Wantlist sync failed: %s", username, exc)
        return False

    # 3 ── Suggestions fetch
    try:
        logger.info("[%s] Step 3/4: Fetching suggestions…", username)
        count = api.fetch_suggestions(
            force=force,
            log_fn=lambda msg: logger.info("[%s]   %s", username, msg),
            client=client,
            db_path=user_db,
        )
        logger.info("[%s] Suggestions done: %d new", username, count)
    except Exception as exc:
        logger.error("[%s] Suggestions fetch failed: %s", username, exc)
        # Non-fatal — continue to listings

    # 4 ── Marketplace listings for all wantlist vinyl items
    logger.info("[%s] Step 4/4: Refreshing marketplace listings…", username)
    try:
        with db.get_conn(user_db) as conn:
            wantlist = db.get_wantlist(conn)

        vinyl_items = [w for w in wantlist if _is_vinyl(w.get("format") or "")]
        if not vinyl_items:
            logger.info("[%s] No vinyl wantlist items found — skipping listings.", username)
        else:
            total = len(vinyl_items)
            fetched = skipped = saved = 0
            new_alert_count = 0
            for i, w in enumerate(vinyl_items, 1):
                rid   = w["release_id"]
                label = f"{w['artist']} — {w['title']}"
                existing_listing_ids: set[int] = set()
                with db.get_conn(user_db) as conn:
                    existing_listing_ids = db.get_existing_listing_ids(conn, rid)
                    if not force and not _listings_stale(conn, rid, _LISTING_TTL_HOURS):
                        logger.info("[%s] [%d/%d] SKIP  %s", username, i, total, label)
                        skipped += 1
                        continue

                logger.info("[%s] [%d/%d] FETCH %s", username, i, total, label)
                try:
                    listings = api.fetch_release_listings(
                        rid,
                        log_fn=lambda msg: logger.info("[%s]   %s", username, msg),
                        token=discogs_token,
                        db_path=user_db,
                    )
                except Exception as exc:
                    logger.error("[%s] Listing fetch error for %d: %s", username, rid, exc)
                    continue

                ts = datetime.utcnow().isoformat()
                db_rows = [
                    _map_to_db(rid, lst, ts)
                    for lst in listings
                    if lst.get("listing_id") is not None
                ]
                with db.get_conn(user_db) as conn:
                    saved_count = db.bulk_upsert_wantlist_listings(conn, rid, db_rows)
                    new_rows = [
                        row
                        for row in db_rows
                        if row.get("listing_id") is not None
                        and int(row["listing_id"]) not in existing_listing_ids
                    ]
                    if new_rows:
                        for nr in new_rows:
                            nr["artist"] = w.get("artist")
                            nr["title"] = w.get("title")
                        new_alert_count += db.create_new_listing_alerts(
                            conn,
                            user_id=user_id,
                            artist=w.get("artist") or "",
                            title=w.get("title") or "",
                            new_rows=new_rows,
                        )

                    # Detect price changes (compare last two snapshots per listing)
                    price_alerts: list[dict] = []
                    try:
                        for r in db_rows:
                            lid = r.get("listing_id")
                            if lid is None or r.get("price_usd") is None:
                                continue
                            ph_rows = conn.execute(
                                "SELECT price_usd FROM wantlist_price_history WHERE listing_id = ? ORDER BY fetched_at DESC LIMIT 2",
                                (lid,),
                            ).fetchall()
                            if len(ph_rows) < 2:
                                continue
                            latest = ph_rows[0]["price_usd"]
                            prev = ph_rows[1]["price_usd"]
                            if prev is None or latest is None:
                                continue
                            try:
                                prev_val = float(prev)
                                latest_val = float(latest)
                            except Exception:
                                continue
                            if prev_val == 0:
                                continue
                            change_pct = (latest_val - prev_val) / prev_val
                            if abs(change_pct) >= _PRICE_CHANGE_PCT:
                                price_alerts.append({
                                    "listing_id": lid,
                                    "release_id": r.get("release_id"),
                                    "price_usd": latest_val,
                                    "prev_price_usd": prev_val,
                                    "change_pct": change_pct,
                                    "currency": r.get("currency"),
                                    "condition": r.get("condition"),
                                    "seller": r.get("seller"),
                                    "ships_from": r.get("ships_from"),
                                    "listing_url": r.get("listing_url"),
                                    "first_seen": r.get("first_seen"),
                                })
                        if price_alerts:
                            price_alert_count = db.create_price_change_alerts(
                                conn,
                                user_id=user_id,
                                artist=w.get("artist") or "",
                                title=w.get("title") or "",
                                change_rows=price_alerts,
                            )
                            if price_alert_count:
                                logger.info("[%s] Price-change alerts queued: %d", username, price_alert_count)
                    except Exception as exc:
                        logger.error("[%s] Price-change detection failed for %s: %s", username, label, exc)

                fetched += 1
                saved   += saved_count
                logger.info("[%s]   → %d listing(s) saved", username, saved_count)

            logger.info(
                "[%s] Listings done: %d fetched · %d skipped · %d total saved",
                username, fetched, skipped, saved,
            )
            if new_alert_count:
                logger.info("[%s] New listing alerts queued: %d", username, new_alert_count)
    except Exception as exc:
        logger.error("[%s] Listings step failed: %s", username, exc)
        return False

    if send_alerts:
        if not alert_email:
            logger.warning("[%s] Alert email not configured. Skipping email delivery.", username)
        else:
            with db.get_conn(user_db) as conn:
                pending = db.get_pending_listing_alerts(conn, user_id=user_id)
            if pending:
                ok, detail = _send_email_digest(alert_email, username, pending)
                alert_ids = [int(a["alert_id"]) for a in pending]
                with db.get_conn(user_db) as conn:
                    if ok:
                        db.mark_listing_alerts_sent(conn, alert_ids)
                        logger.info("[%s] Sent alert digest email (%d alert(s))", username, len(alert_ids))
                    else:
                        db.mark_listing_alerts_failed(conn, alert_ids, detail)
                        logger.error("[%s] Alert email failed: %s", username, detail)

    logger.info("[%s] Refresh complete.", username)
    return True


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh Discogs data for all (or one) Vinyl Catalog users."
    )
    parser.add_argument(
        "--user", default=None,
        help="Only refresh this username (default: all users).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bypass all TTLs and force a full refresh.",
    )
    parser.add_argument(
        "--no-alerts", action="store_true",
        help="Disable alert email sending for this run.",
    )
    args = parser.parse_args()

    db.init_db()

    with db.get_master_conn() as conn:
        all_users = db.get_all_users(conn)

    if not all_users:
        logger.error(
            "No user accounts found. Create one with:\n"
            "  python vinyl/add_user.py --username NAME --discogs-token TOKEN "
            "--discogs-username DISCOGS_NAME --db-path vinyl.db"
        )
        sys.exit(1)

    if args.user:
        target = [u for u in all_users if u["username"] == args.user]
        if not target:
            logger.error("User '%s' not found. Available users: %s",
                         args.user, [u["username"] for u in all_users])
            sys.exit(1)
        users_to_run = target
    else:
        users_to_run = all_users

    start = time.time()
    errors: list[str] = []
    for user in users_to_run:
        ok = refresh_user(user, force=args.force, send_alerts=not args.no_alerts)
        if not ok:
            errors.append(user["username"])

    elapsed = time.time() - start
    logger.info("")
    logger.info("─" * 60)
    if errors:
        logger.error("Refresh finished with errors for: %s  (%.1fs)", ", ".join(errors), elapsed)
        sys.exit(1)
    else:
        logger.info("All users refreshed successfully in %.1fs.", elapsed)


if __name__ == "__main__":
    main()
