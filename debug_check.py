import sqlite3
import os

DB_NAME = 'vpn_bot.db'

def check_db():
    if not os.path.exists(DB_NAME):
        print(f"Error: {DB_NAME} not found.")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    print("--- Users Table Content (top 10) ---")
    cursor.execute("SELECT telegram_id, username, uuid, sub_end_date FROM users WHERE uuid IS NOT NULL LIMIT 10")
    rows = cursor.fetchall()
    for row in rows:
        print(f"ID: {row[0]}, User: {row[1]}, UUID: {row[2]}, Exp: {row[3]}")
    
    conn.close()

if __name__ == "__main__":
    check_db()
