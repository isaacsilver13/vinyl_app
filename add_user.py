"""
add_user.py
-----------
Admin CLI tool to add a user account to the Vinyl Catalog app.

Each user gets their own SQLite database for their collection data.
The shared users table lives in vinyl.db (the master DB).

Usage:
    # Add a new user — will create vinyl_alice.db for their data
    python vinyl/add_user.py --username alice --discogs-token XXX --discogs-username alice123 --email alice@example.com

    # Add yourself using your EXISTING vinyl.db (owner / first user)
    python vinyl/add_user.py --username justin --discogs-token XXX --discogs-username justin123 --db-path vinyl.db

    # The script will prompt for a password (input is hidden)
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(_HERE, ".env"))

import bcrypt
import database as db


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a user account to the Vinyl Catalog app."
    )
    parser.add_argument("--username",         required=True,  help="Login username")
    parser.add_argument("--discogs-token",    required=True,  help="Discogs API token for this user")
    parser.add_argument("--discogs-username", required=True,  help="Discogs username for this user")
    parser.add_argument("--email",            default=None,   help="Alert email for new listing digests")
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "DB filename for this user's data (default: vinyl_{username}.db). "
            "Use 'vinyl.db' for the owner to reuse the existing database."
        ),
    )
    args = parser.parse_args()

    username         = args.username.strip()
    discogs_token    = args.discogs_token.strip()
    discogs_username = args.discogs_username.strip()
    email            = (args.email or "").strip() or None
    user_db          = args.db_path or f"vinyl_{username}.db"

    # Check that the user doesn't already exist
    db.init_db()
    with db.get_master_conn() as conn:
        existing = db.get_user_by_username(conn, username)
    if existing:
        print(f"Error: user '{username}' already exists.")
        sys.exit(1)

    # Prompt for password (twice to confirm)
    while True:
        password = getpass.getpass(f"Set password for '{username}': ")
        if len(password) < 8:
            print("Password must be at least 8 characters. Try again.")
            continue
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match. Try again.")
            continue
        break

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    # Scaffold the user's data DB (no-op if it already exists and has tables)
    print(f"Initialising data DB: {user_db} …")
    db.init_user_db(user_db)

    # Insert user record
    with db.get_master_conn() as conn:
        db.create_user(conn, username, password_hash, discogs_token, discogs_username, user_db, email)

    print(f"✅  User '{username}' created successfully.")
    print(f"    Data DB : {user_db}")
    print(f"    Discogs : {discogs_username}")
    if email:
        print(f"    Alerts  : {email}")


if __name__ == "__main__":
    main()
