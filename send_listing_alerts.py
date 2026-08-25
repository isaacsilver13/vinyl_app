"""
send_listing_alerts.py
----------------------
Send digest emails for pending new-listing alerts without running a full refresh.

Usage:
    python vinyl/send_listing_alerts.py
    python vinyl/send_listing_alerts.py --user alice
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(_HERE, ".env"))

import database as db
from refresh_all import _send_email_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def send_for_user(user: dict) -> bool:
    username = user["username"]
    user_id = user["user_id"]
    user_db = user["db_path"]
    email = (user.get("email") or "").strip()

    if not email:
        logger.warning("[%s] No alert email configured; skipping.", username)
        return True

    sent_any = False

    # Send new-listing alerts
    with db.get_conn(user_db) as conn:
        pending_new = db.get_pending_listing_alerts(conn, user_id=user_id, alert_type="new_listing")
    if pending_new:
        ok, detail = _send_email_digest(email, username, pending_new)
        alert_ids = [int(a["alert_id"]) for a in pending_new]
        with db.get_conn(user_db) as conn:
            if ok:
                db.mark_listing_alerts_sent(conn, alert_ids)
                logger.info("[%s] Sent %d new-listing alert(s).", username, len(alert_ids))
                sent_any = True
            else:
                db.mark_listing_alerts_failed(conn, alert_ids, detail)
                logger.error("[%s] Failed to send new-listing alerts: %s", username, detail)

    # Send price-change alerts
    with db.get_conn(user_db) as conn:
        pending_price = db.get_pending_listing_alerts(conn, user_id=user_id, alert_type="price_change")
    if pending_price:
        # Build a readable body for price changes
        body_lines = [f"Hi {username},", "", f"{len(pending_price)} listing price change(s) detected:", ""]
        for a in pending_price:
            artist = a.get("artist") or "Unknown Artist"
            title = a.get("title") or "Untitled"
            prev = a.get("prev_price_usd")
            curr = a.get("price_usd")
            pct = a.get("change_pct")
            pct_str = f"{pct:.1%}" if pct is not None else "N/A"
            body_lines.append(f"- {artist} — {title}")
            body_lines.append(f"  Prev: {prev if prev is not None else 'N/A'} | Now: {curr if curr is not None else 'N/A'} | Change: {pct_str}")
            if a.get("listing_url"):
                body_lines.append(f"  Listing: {a.get('listing_url')}")
            body_lines.append("")
        body_lines.append("--")
        body_lines.append("Vinyl Catalog")
        # Embed body into first alert dict to let _send_email_digest use it
        pending_price[0]["_body"] = "\n".join(body_lines)
        pending_price[0]["_subject"] = f"[Vinyl Catalog] {len(pending_price)} price change(s) for {username}"

        ok, detail = _send_email_digest(email, username, pending_price)
        alert_ids = [int(a["alert_id"]) for a in pending_price]
        with db.get_conn(user_db) as conn:
            if ok:
                db.mark_listing_alerts_sent(conn, alert_ids)
                logger.info("[%s] Sent %d price-change alert(s).", username, len(alert_ids))
                sent_any = True
            else:
                db.mark_listing_alerts_failed(conn, alert_ids, detail)
                logger.error("[%s] Failed to send price-change alerts: %s", username, detail)

    if not sent_any:
        logger.info("[%s] No pending alerts.", username)
        return True
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Send pending Vinyl listing alert digests.")
    parser.add_argument("--user", default=None, help="Only send alerts for this username.")
    args = parser.parse_args()

    db.init_db()
    with db.get_master_conn() as conn:
        users = db.get_all_users(conn)

    if args.user:
        users = [u for u in users if u["username"] == args.user]
        if not users:
            logger.error("User '%s' not found.", args.user)
            raise SystemExit(1)

    if not users:
        logger.info("No users found.")
        return

    failed = []
    for user in users:
        if not send_for_user(user):
            failed.append(user["username"])

    if failed:
        logger.error("Completed with failures for: %s", ", ".join(failed))
        raise SystemExit(1)

    logger.info("Pending alert delivery complete.")


if __name__ == "__main__":
    main()
