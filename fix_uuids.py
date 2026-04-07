import asyncio
import sqlite3
from datetime import datetime
import remnawave_api as api

DB_NAME = 'vpn_bot.db'

async def fix_missing_uuids():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, sub_end_date FROM users WHERE uuid IS NULL")
    users = cursor.fetchall()
    
    print(f"Found {len(users)} users lacking uuid.")
    
    for uid, end_date_str in users:
        print(f"Fixing user {uid}...")
        expiry_ms = 0
        if end_date_str:
            try:
                dt = datetime.strptime(end_date_str, "%Y-%m-%d %H:%M:%S")
                if dt > datetime.now():
                    expiry_ms = int(dt.timestamp() * 1000)
            except Exception as e:
                print(f"Date error: {e}")
                
        new_uuid = await api.add_client(uid, expiry_ms)
        if new_uuid:
            print(f"Generated new uuid for {uid}: {new_uuid}")
            cursor.execute("UPDATE users SET uuid = ? WHERE telegram_id = ?", (new_uuid, uid))
            conn.commit()
        else:
            print(f"Failed to generate uuid for {uid}")
            
    conn.close()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(fix_missing_uuids())
