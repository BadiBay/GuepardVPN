import aiohttp
import uuid
import json
import logging

import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

logger = logging.getLogger(__name__)

PANEL_URL = os.getenv("PANEL_URL", "http://127.0.0.1:2053")
USERNAME = os.getenv("PANEL_USERNAME", "admin")
PASSWORD = os.getenv("PANEL_PASSWORD", "admin")
INBOUND_ID = int(os.getenv("INBOUND_ID", 1))


async def login(session):
    url = f"{PANEL_URL}/login"
    data = {"username": USERNAME, "password": PASSWORD}
    async with session.post(url, data=data) as resp:
        try:
            result = await resp.json()
            return result.get("success", False)
        except Exception:
            return False


async def add_client(telegram_id: int):
    try:
        client_uuid = str(uuid.uuid4())
        email = f"tg_{telegram_id}"

        async with aiohttp.ClientSession() as session:
            is_logged = await login(session)
            if not is_logged:
                logger.warning("Ошибка авторизации в панели 3x-ui")
                return None

            url = f"{PANEL_URL}/panel/api/inbounds/addClient"

            client_data = {
                "id": client_uuid,
                "alterId": 0,
                "email": email,
                "limitIp": 2,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": "",
                "subId": client_uuid
            }

            payload = {
                "id": INBOUND_ID,
                "settings": json.dumps({"clients": [client_data]})
            }

            headers = {'Accept': 'application/json'}

            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка API: Сервер вернул статус {resp.status} для URL {url}")
                    return None

                try:
                    data = await resp.json()
                    if data.get("success"):
                        return client_uuid
                    else:
                        logger.error(f"Ошибка добавления клиента: {data.get('msg')}")
                        return None
                except aiohttp.ContentTypeError:
                    logger.error("Ошибка: Панель вернула не JSON. Возможно, путь к API неверный.")
                    return None
    except (aiohttp.ClientConnectorError, aiohttp.ServerConnectionError, OSError) as e:
        logger.error(f"Панель 3x-ui недоступна (add_client): {e}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка в add_client: {e}")
        return None


async def disable_client(client_uuid: str, telegram_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            is_logged = await login(session)
            if not is_logged:
                return False

            url = f"{PANEL_URL}/panel/api/inbounds/updateClient/{client_uuid}"

            client_data = {
                "id": client_uuid,
                "alterId": 0,
                "email": f"tg_{telegram_id}",
                "limitIp": 2,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": False,
                "tgId": "",
                "subId": client_uuid
            }

            payload = {
                "id": INBOUND_ID,
                "settings": json.dumps({"clients": [client_data]})
            }

            headers = {'Accept': 'application/json'}

            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    return False
                try:
                    data = await resp.json()
                    return data.get("success", False)
                except Exception:
                    return False
    except (aiohttp.ClientConnectorError, aiohttp.ServerConnectionError, OSError) as e:
        logger.error(f"Панель 3x-ui недоступна (disable_client): {e}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка в disable_client: {e}")
        return False


async def clear_client_ips(telegram_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            is_logged = await login(session)
            if not is_logged:
                return False

            email = f"tg_{telegram_id}"
            url = f"{PANEL_URL}/panel/api/inbounds/clearClientIps/{email}"

            headers = {'Accept': 'application/json'}

            async with session.post(url, headers=headers) as resp:
                if resp.status != 200:
                    return False
                try:
                    data = await resp.json()
                    return data.get("success", False)
                except Exception:
                    return False
    except (aiohttp.ClientConnectorError, aiohttp.ServerConnectionError, OSError) as e:
        logger.error(f"Панель 3x-ui недоступна (clear_client_ips): {e}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка в clear_client_ips: {e}")
        return False


async def get_client_stats(telegram_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            is_logged = await login(session)
            if not is_logged:
                return 0.0

            email = f"tg_{telegram_id}"
            url = f"{PANEL_URL}/panel/api/inbounds/getClientTraffics/{email}"

            headers = {'Accept': 'application/json'}

            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return 0.0
                try:
                    data = await resp.json()
                    if data.get("success") and data.get("obj"):
                        obj = data["obj"]
                        up = obj.get("up", 0)
                        down = obj.get("down", 0)
                        total_bytes = up + down
                        used_gb = round(total_bytes / (1024 ** 3), 2)
                        return used_gb
                    return 0.0
                except Exception:
                    return 0.0
    except (aiohttp.ClientConnectorError, aiohttp.ServerConnectionError, OSError) as e:
        logger.error(f"Панель 3x-ui недоступна (get_client_stats): {e}")
        return 0.0
    except Exception as e:
        logger.error(f"Неожиданная ошибка в get_client_stats: {e}")
        return 0.0