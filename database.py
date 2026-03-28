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
            is_active INTEGER DEFAULT 0,
            notified_24h INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_payments (
            operation_id TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            hwid TEXT,
            device_model TEXT,
            platform TEXT,
            user_agent TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(telegram_id, hwid)
        )
    ''')
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN notified_24h INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN auto_renew INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN saved_payment_method_id TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_tariff_days INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN sub_updated_at TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN notified_zero_traffic INTEGER DEFAULT 0")
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

def get_user_by_username(username: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    clean_username = username.lstrip('@')
    cursor.execute("SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?)", (clean_username,))
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
    sub_updated_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Обновляем дату и сразу ставим статус активного
    cursor.execute(
        "UPDATE users SET sub_end_date = ?, is_active = 1, notified_24h = 0, sub_updated_at = ?, notified_zero_traffic = 0 WHERE telegram_id = ?",
        (new_end_str, sub_updated_at_str, telegram_id)
    )
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


def get_active_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, sub_end_date FROM users WHERE is_active = 1 AND sub_end_date IS NOT NULL")
    result = cursor.fetchall()
    conn.close()
    return result


def get_users_expired_24h():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, sub_end_date FROM users WHERE is_active = 0 AND sub_end_date IS NOT NULL AND notified_24h = 0")
    results = cursor.fetchall()
    conn.close()
    
    now = datetime.now()
    expired_users = []
    for row in results:
        tid, dt_str = row
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            if (now - dt).total_seconds() >= 86400: # 24 hours
                expired_users.append(tid)
        except Exception:
            pass
    return expired_users


def mark_notified_24h(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET notified_24h = 1 WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()


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

def is_payment_processed(operation_id: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_payments WHERE operation_id = ?", (str(operation_id),))
    result = cursor.fetchone()
    conn.close()
    return bool(result)

def mark_payment_processed(operation_id: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO processed_payments (operation_id) VALUES (?)", (str(operation_id),))
    conn.commit()
    conn.close()


def set_auto_renew(telegram_id: int, enabled: bool):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET auto_renew = ? WHERE telegram_id = ?", (1 if enabled else 0, telegram_id))
    conn.commit()
    conn.close()


def get_auto_renew(telegram_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT auto_renew, saved_payment_method_id FROM users WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return bool(result[0]), result[1]
    return False, None


def save_payment_method(telegram_id: int, method_id: str, days: int):
    """Сохраняет payment_method_id и тариф для автопродления, автоматически включает автопродление."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET saved_payment_method_id = ?, last_tariff_days = ?, auto_renew = 1 WHERE telegram_id = ?",
        (method_id, days, telegram_id)
    )
    conn.commit()
    conn.close()


def get_users_for_auto_renew():
    """Возвращает пользователей с auto_renew=1, сохранённым методом оплаты,
    у которых подписка истекает в ближайшие 2 часа."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.now()
    window_end = now + timedelta(hours=2)
    cursor.execute(
        """SELECT telegram_id, saved_payment_method_id, last_tariff_days, sub_end_date
           FROM users
           WHERE auto_renew = 1
             AND saved_payment_method_id IS NOT NULL
             AND is_active = 1
             AND sub_end_date > ?
             AND sub_end_date <= ?""",
        (now.strftime("%Y-%m-%d %H:%M:%S"), window_end.strftime("%Y-%m-%d %H:%M:%S"))
    )
    result = cursor.fetchall()
    conn.close()
    return result

def get_users_zero_traffic_24h():
    """Возвращает ID пользователей и их даты, которые купили подписку >24ч назад, активны и не получали сообщение"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, sub_updated_at FROM users WHERE is_active = 1 AND sub_updated_at IS NOT NULL AND notified_zero_traffic = 0")
    results = cursor.fetchall()
    conn.close()
    
    now = datetime.now()
    users_to_check = []
    for row in results:
        tid, dt_str = row
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            diff = (now - dt).total_seconds()
            if 86400 <= diff < (86400 * 3): # От 24 часов до 3 дней
                users_to_check.append(tid)
        except Exception:
            pass
    return users_to_check

def mark_notified_zero_traffic(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET notified_zero_traffic = 1 WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

# --- Device Tracking API ---
def add_or_update_device(telegram_id: int, hwid: str, device_model: str, platform: str, user_agent: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("SELECT id FROM user_devices WHERE telegram_id = ? AND hwid = ?", (telegram_id, hwid))
    row = cursor.fetchone()
    
    if row:
        cursor.execute('''
            UPDATE user_devices 
            SET device_model = ?, platform = ?, user_agent = ?, updated_at = ?
            WHERE id = ?
        ''', (device_model, platform, user_agent, now_str, row[0]))
    else:
        cursor.execute('''
            INSERT INTO user_devices (telegram_id, hwid, device_model, platform, user_agent, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (telegram_id, hwid, device_model, platform, user_agent, now_str, now_str))
        
    conn.commit()
    conn.close()

def get_user_devices(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, hwid, device_model, platform, user_agent, created_at, updated_at 
        FROM user_devices WHERE telegram_id = ? ORDER BY id ASC
    """, (telegram_id,))
    result = cursor.fetchall()
    conn.close()
    return result

def get_device_count(telegram_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_devices WHERE telegram_id = ?", (telegram_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def delete_user_device(device_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_devices WHERE id = ?", (device_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def clear_user_devices(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_devices WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()