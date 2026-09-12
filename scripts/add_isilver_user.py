import os
import sys
from pathlib import Path

import bcrypt

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))).resolve()
sys.path.insert(0, str(PROJECT_ROOT))

import database
p = os.environ.get("DISCOGS_TOKEN_FILE", r"C:\Users\justj\.github_token_discogs.txt")
try:
    token = open(p, "r", encoding="utf-8").read().strip()
except Exception as e:
    print('ERROR reading token file:', e)
    raise
username = 'isilver13'
discogs_username = 'isilver13'

database.init_db()
with database.get_master_conn() as conn:
    existing = database.get_user_by_username(conn, username)
    if existing:
        conn.execute(
            'UPDATE users SET discogs_token=?, discogs_username=?, db_path=? WHERE username=?',
            (token, discogs_username, f'vinyl_{username}.db', username)
        )
        conn.commit()
        print('Updated user isilver13')
    else:
        import secrets
        generated_password = secrets.token_urlsafe(12)
        password_hash = bcrypt.hashpw(generated_password.encode(), bcrypt.gensalt()).decode()
        database.create_user(conn, username, password_hash, token, discogs_username, f'vinyl_{username}.db', None)
        conn.commit()
        print('Created user isilver13')
        print(f'Generated password (record this now, it is not stored anywhere else): {generated_password}')
