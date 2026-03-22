import sqlite3
from datetime import datetime, timedelta

DB_NAME = 'vpn_bot.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            sub_end_date TEXT DEFAULT NULL,
            uuid TEXT DEFAULT NULL,
            is_active INTEGER DEFAULT 0
        )
    ''')
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def add_user(telegram_id: int, username: str, referrer_id: int | None = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (telegram_id, username, referrer_id) VALUES (?, ?, ?)",
        (telegram_id, username, referrer_id)
    )
    conn.commit()
    conn.close()


def get_user(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance, sub_end_date, uuid FROM users WHERE telegram_id = ?",
        (telegram_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result


def get_username(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None



def update_uuid(telegram_id: int, client_uuid: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET uuid = ? WHERE telegram_id = ?", (client_uuid, telegram_id))
    conn.commit()
    conn.close()


def add_subscription(telegram_id: int, days: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT sub_end_date FROM users WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()

    now = datetime.now()
    if result and result[0]:
        current_end = datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
        if current_end > now:
            now = current_end

    new_end = now + timedelta(days=days)
    new_end_str = new_end.strftime("%Y-%m-%d %H:%M:%S")

    # Обновляем дату и сразу ставим статус активного
    cursor.execute("UPDATE users SET sub_end_date = ?, is_active = 1 WHERE telegram_id = ?", (new_end_str, telegram_id))
    conn.commit()
    conn.close()


def get_expired_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Ищем тех, у кого время вышло, но они всё еще числятся активными
    cursor.execute("SELECT telegram_id, uuid FROM users WHERE sub_end_date < ? AND is_active = 1", (now_str,))
    result = cursor.fetchall()
    conn.close()
    return result


def set_inactive(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_active = 0 WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()


def get_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Считаем всех пользователей
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    # Считаем только активных
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
    active_users = cursor.fetchone()[0]

    conn.close()
    return total_users, active_users


def get_referrer(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT referrer_id FROM users WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def get_referral_count(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (telegram_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0


def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM users")
    result = cursor.fetchall()
    conn.close()
    return [row[0] for row in result]


def has_ever_subscribed(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT sub_end_date FROM users WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] is not None


def delete_user_data(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET sub_end_date = NULL, uuid = NULL, is_active = 0 WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()