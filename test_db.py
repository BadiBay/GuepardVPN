import sqlite3
import os

try:
    conn = sqlite3.connect('vpn_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT uuid FROM users WHERE uuid IS NOT NULL LIMIT 5')
    rows = cursor.fetchall()
    print("ROWS:", rows)
    conn.close()
except Exception as e:
    print("Error:", e)
