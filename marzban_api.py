import aiohttp
import logging
import os
from dotenv import load_dotenv
import database as db

load_dotenv()

logger = logging.getLogger(__name__)

PANEL_URL = os.getenv("PANEL_URL", "http://127.0.0.1:8000").rstrip('/')
USERNAME = os.getenv("PANEL_USERNAME", "admin")
PASSWORD = os.getenv("PANEL_PASSWORD", "admin")

_session: aiohttp.ClientSession | None = None
_token: str | None = None
_token_expiry: float = 0 # timestamp

async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _session


def get_m_username(telegram_id: int) -> str:
    username = db.get_username(telegram_id)
    if username and str(username).strip() != "None":
        clean = "".join(c for c in username if c.isalnum() or c in ("-", "_"))
        if clean:
            return clean
    return f"tg_{telegram_id}"


async def get_token() -> str | None:
    global _token, _token_expiry
    import time
    now = time.time()
    if _token and now < _token_expiry:
        return _token

    session = await get_session()
    url = f"{PANEL_URL}/api/admin/token"
    data = {"username": USERNAME, "password": PASSWORD}
    try:
        async with session.post(url, data=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                _token = result.get("access_token")
                # Token usually valid for 24h, we cache it for 23h
                _token_expiry = now + (23 * 3600)
                return _token
            return None
    except Exception as e:
        logger.error(f"Error getting Marzban token: {e}")
        return None


async def get_subscription_url(telegram_id: int) -> str | None:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return None

        m_user = get_m_username(telegram_id)
        url = f"{PANEL_URL}/api/user/{m_user}"
        headers = {"Authorization": f"Bearer {token}"}

        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("subscription_url")
            return None
    except Exception as e:
        logger.error(f"Error getting sub link: {e}")
        return None


async def add_client(telegram_id: int, expiry_time_ms: int = 0) -> str | None:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            logger.warning("Marzban auth failed")
            return None

        url = f"{PANEL_URL}/api/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        expire = None if expiry_time_ms == 0 else int(expiry_time_ms / 1000)
        m_user = get_m_username(telegram_id)

        payload = {
            "username": m_user,
            "proxies": {"vless": {}},
            "inbounds": {},
            "expire": expire,
            "data_limit": 0,
            "data_limit_reset_strategy": "no_reset",
            "status": "active"
        }

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status in (200, 201):
                data = await resp.json()
                return data.get("subscription_url")
            elif resp.status == 409: # Already exists
                return await get_subscription_url(telegram_id)
            else:
                logger.error(f"API Error add_client {resp.status}: {await resp.text()}")
                return None
    except Exception as e:
        logger.error(f"Unexpected error in add_client: {e}")
        return None


async def disable_client(client_uuid: str, telegram_id: int) -> bool:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return False

        m_user = get_m_username(telegram_id)
        url = f"{PANEL_URL}/api/user/{m_user}"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"status": "disabled"}

        async with session.put(url, json=payload, headers=headers) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Unexpected error in disable_client: {e}")
        return False


async def update_client_expiry(client_uuid: str, telegram_id: int, expiry_time_ms: int) -> bool:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return False

        m_user = get_m_username(telegram_id)
        url = f"{PANEL_URL}/api/user/{m_user}"
        headers = {"Authorization": f"Bearer {token}"}
        expire = None if expiry_time_ms == 0 else int(expiry_time_ms / 1000)
        payload = {"expire": expire, "status": "active"}

        async with session.put(url, json=payload, headers=headers) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Error in update_client_expiry: {e}")
        return False


async def get_client_ips(telegram_id: int) -> int:
    return 0


async def clear_client_ips(telegram_id: int) -> str | None:
    # Возвращает новую ссылку на подписку
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return None

        m_user = get_m_username(telegram_id)
        url = f"{PANEL_URL}/api/user/{m_user}/revoke_sub"
        headers = {"Authorization": f"Bearer {token}"}

        async with session.post(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("subscription_url")
            return None
    except Exception as e:
        logger.error(f"Error in clear_client_ips: {e}")
        return None


async def get_client_stats(telegram_id: int) -> float:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return 0.0

        m_user = get_m_username(telegram_id)
        url = f"{PANEL_URL}/api/user/{m_user}"
        headers = {"Authorization": f"Bearer {token}"}

        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                used_bytes = data.get("used_traffic", 0)
                return round(used_bytes / (1024 ** 3), 2)
            return 0.0
    except Exception as e:
        logger.error(f"Error in get_client_stats: {e}")
        return 0.0
