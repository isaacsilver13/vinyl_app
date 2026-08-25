import sqlite3, os
db_path = os.path.join(os.path.dirname(__file__), "vinyl.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
conn.execute("UPDATE users SET email = ? WHERE username = ?", ("isaacsilver13@gmail.com","isilver13"))
conn.commit()
r = conn.execute("SELECT user_id,username,email,db_path FROM users WHERE username = ?", ("isilver13",)).fetchone()
print(dict(r) if r else "user not found")
conn.close()
