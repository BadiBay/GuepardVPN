import aiohttp
import logging
import os
import time
from dotenv import load_dotenv
import database as db

load_dotenv()

logger = logging.getLogger(__name__)

PANEL_URL = os.getenv("PANEL_URL", "http://127.0.0.1:3000").rstrip('/')
USERNAME = os.getenv("PANEL_USERNAME", "admin")
PASSWORD = os.getenv("PANEL_PASSWORD", "admin")
# RemnaWave поддерживает постоянные токены. Если задан, используем его
PANEL_TOKEN = os.getenv("PANEL_TOKEN", None)

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
    
    # Если вручную прописан токен в .env, то мы его просто всегда возвращаем
    if PANEL_TOKEN:
        return PANEL_TOKEN
        
    now = time.time()
    if _token and now < _token_expiry:
        return _token

    session = await get_session()
    url = f"{PANEL_URL}/api/auth/login"
    data = {"username": USERNAME, "password": PASSWORD}
    try:
        async with session.post(url, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                _token = result.get("accessToken") or result.get("access_token") or result.get("token")
                _token_expiry = now + (23 * 3600)
                return _token
            return None
    except Exception as e:
        logger.error(f"Error getting RemnaWave token: {e}")
        return None

async def resolve_user(m_user: str, token: str, session: aiohttp.ClientSession) -> dict | None:
    """Вспомогательная функция для получения UUID пользователя по username."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{PANEL_URL}/api/users/resolve"
    try:
        async with session.post(url, json={"username": m_user}, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("response", data) if isinstance(data, dict) else data
            elif resp.status == 404:
                return None
    except Exception as e:
        logger.error(f"Error resolving user: {e}")
    return None


async def get_subscription_url(telegram_id: int) -> str | None:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return None

        m_user = get_m_username(telegram_id)
        user_data = await resolve_user(m_user, token, session)
        if user_data:
            uuid = user_data.get("shortUuid") or user_data.get("uuid") or user_data.get("id")
            sub_url = user_data.get("subscriptionUrl") or user_data.get("subscription_url")
            if sub_url:
                return sub_url
            if uuid:
                return f"/api/sub/{uuid}"
        return None
    except Exception as e:
        logger.error(f"Error getting sub link: {e}")
        return None


async def add_client(telegram_id: int, expiry_time_ms: int = 0) -> str | None:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            logger.warning("RemnaWave auth failed")
            return None

        url = f"{PANEL_URL}/api/users"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        m_user = get_m_username(telegram_id)

        from datetime import datetime, timezone, timedelta
        
        payload = {
            "username": m_user,
            "status": "ACTIVE"
        }
        
        try:
            async with session.get(f"{PANEL_URL}/api/internal-squads", headers=headers) as sq_resp:
                if sq_resp.status == 200:
                    sq_data = await sq_resp.json()
                    sq_resp_body = sq_data.get('response', {})
                    internal_squads = sq_resp_body.get('internalSquads', [])
                    if internal_squads:
                        payload["activeInternalSquads"] = [internal_squads[0]['uuid']]
        except Exception as e:
            logger.error(f"Error fetching internal squads: {e}")
        
        # Получаем лимит устройств из БД
        import database as temp_db
        user_limit = temp_db.get_user_device_limit(telegram_id)
        if user_limit:
            payload["hwidDeviceLimit"] = user_limit

        if expiry_time_ms > 0:
            expire = int(expiry_time_ms / 1000)
            payload["expire"] = expire
            try:
                payload["expireAt"] = datetime.fromtimestamp(expire, tz=timezone.utc).isoformat()
            except Exception:
                pass
        else:
            payload["expireAt"] = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status in (200, 201):
                raw = await resp.json()
                data = raw.get("response", raw) if isinstance(raw, dict) else raw
                uuid = data.get("shortUuid") or data.get("uuid") or data.get("id")
                sub_url = data.get("subscriptionUrl") or data.get("subscription_url")
                if sub_url:
                    return sub_url
                if uuid:
                    return f"/api/sub/{uuid}"
                # Иногда возвращается сразу в корне ответа
                return await get_subscription_url(telegram_id)
            elif resp.status == 409: # Already exists
                return await get_subscription_url(telegram_id)
            else:
                resp_text = await resp.text()
                if resp.status == 400 and '"A019"' in resp_text:
                    return await get_subscription_url(telegram_id)
                
                logger.error(f"API Error add_client {resp.status}: {resp_text}")
                
                # Если создание упало, возможно пользователь уже есть. Пробуем отрезолвить.
                return await get_subscription_url(telegram_id)

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
        user_data = await resolve_user(m_user, token, session)
        if not user_data:
            return False
            
        uuid = user_data.get("uuid")
        if not uuid:
            return False

        url = f"{PANEL_URL}/api/users/{uuid}/actions/disable"
        headers = {"Authorization": f"Bearer {token}"}

        async with session.post(url, headers=headers) as resp:
            return resp.status in (200, 201)
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
        user_data = await resolve_user(m_user, token, session)
        if not user_data:
            return False
            
        uuid = user_data.get("uuid")
        if not uuid:
            return False

        url = f"{PANEL_URL}/api/users"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        payload = {"username": m_user, "status": "ACTIVE"}
        from datetime import datetime, timezone, timedelta
        if expiry_time_ms > 0:
            expire = int(expiry_time_ms / 1000)
            payload["expire"] = expire
            try:
                payload["expireAt"] = datetime.fromtimestamp(expire, tz=timezone.utc).isoformat()
            except Exception:
                pass
        else:
            payload["expire"] = 0
            payload["expireAt"] = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()

        async with session.patch(url, json=payload, headers=headers) as resp:
            # Для RemnaWave апдейт обычно делается через PATCH. Если PATCH упал (404/405), попробуем PUT
            if resp.status in (200, 201):
                # Включаем юзера
                await session.post(f"{PANEL_URL}/api/users/{uuid}/actions/enable", headers=headers)
                return True
            else:
                # Фоллбэк на PUT как в Marzban
                async with session.put(url, json=payload, headers=headers) as resp_put:
                    if resp_put.status in (200, 201):
                        await session.post(f"{PANEL_URL}/api/users/{uuid}/actions/enable", headers=headers)
                        return True
        return False
    except Exception as e:
        logger.error(f"Error in update_client_expiry: {e}")
        return False


async def get_client_ips(telegram_id: int) -> int:
    return 0

async def update_hwid_device_limit(telegram_id: int, limit: int) -> bool:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return False

        m_user = get_m_username(telegram_id)
        url = f"{PANEL_URL}/api/users"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        payload = {"username": m_user, "hwidDeviceLimit": limit}
        async with session.patch(url, json=payload, headers=headers) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        logger.error(f"Error in update_hwid_device_limit: {e}")
        return False


async def clear_client_ips(telegram_id: int) -> str | None:
    # Возвращает новую ссылку на подписку
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return None

        m_user = get_m_username(telegram_id)
        user_data = await resolve_user(m_user, token, session)
        if not user_data:
            return None
            
        uuid = user_data.get("uuid")
        if not uuid:
            return None

        url = f"{PANEL_URL}/api/users/{uuid}/actions/revoke"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        async with session.post(url, headers=headers) as resp:
            if resp.status in (200, 201):
                # Обновляем чтобы сбросить трафик если требуется, но revoke обычно меняет UUID подписки
                raw = await resp.json()
                data = raw.get("response", raw) if isinstance(raw, dict) else raw
                new_sub_url = data.get("subscriptionUrl") or data.get("subscription_url")
                if new_sub_url:
                    return new_sub_url
                    
                # Если revoke поменял uuid, нам надо снова его получить
                new_user_data = await resolve_user(m_user, token, session)
                if new_user_data:
                    new_uuid = new_user_data.get("shortUuid") or new_user_data.get("uuid") or new_user_data.get("id")
                    if new_uuid:
                        return f"/api/sub/{new_uuid}"
            return None
    except Exception as e:
        logger.error(f"Error in clear_client_ips: {e}")
        return None

async def get_client_devices(telegram_id: int) -> list:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return []
            
        m_user = get_m_username(telegram_id)
        user_data = await resolve_user(m_user, token, session)
        if not user_data:
            return []
            
        uuid = user_data.get("uuid")
        if not uuid:
            return []
            
        url = f"{PANEL_URL}/api/hwid/devices/{uuid}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                raw = await resp.json()
                response_data = raw.get("response", {})
                devices = response_data.get("devices", [])
                return devices
        return []
    except Exception as e:
        logger.error(f"Error getting client HWID devices: {e}")
        return []

async def delete_client_device(telegram_id: int, hwid: str) -> bool:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return False
            
        m_user = get_m_username(telegram_id)
        user_data = await resolve_user(m_user, token, session)
        if not user_data:
            return False
            
        uuid = user_data.get("uuid")
        if not uuid:
            return False
            
        url = f"{PANEL_URL}/api/hwid/devices/delete"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        payload = {"userUuid": uuid, "hwid": hwid}
        
        async with session.post(url, json=payload, headers=headers) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        logger.error(f"Error deleting HWID device: {e}")
        return False


async def get_client_stats(telegram_id: int) -> float:
    try:
        session = await get_session()
        token = await get_token()
        if not token:
            return 0.0

        m_user = get_m_username(telegram_id)
        user_data = await resolve_user(m_user, token, session)
        if not user_data:
            return 0.0
            
        uuid = user_data.get("uuid")
        if not uuid:
            return 0.0

        url = f"{PANEL_URL}/api/bandwidth-stats/users/{uuid}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                raw = await resp.json()
                data = raw.get("response", raw) if isinstance(raw, dict) else raw
                # data может быть {"used": 1000, "total": 2000} или {"usedBytes": 1000, ...}
                used_bytes = data.get("used", 0) or data.get("usedBytes", 0) or data.get("used_traffic", 0)
                # Если stats не по этому URL а лежат в самом профиле юзера:
                if used_bytes == 0 and "usedTraffic" in user_data:
                    used_bytes = user_data.get("usedTraffic", 0)
                if used_bytes == 0 and "used_traffic" in user_data:
                    used_bytes = user_data.get("used_traffic", 0)
                    
                return round(used_bytes / (1024 ** 3), 2)
            
            # Фоллбэк: ищем трафик прямо в модели юзера
            used_b = user_data.get("usedTraffic", 0) or user_data.get("used_traffic", 0)
            return round(used_b / (1024 ** 3), 2)
            
    except Exception as e:
        logger.error(f"Error in get_client_stats: {e}")
        return 0.0
