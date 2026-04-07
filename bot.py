import asyncio
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, WebAppInfo, MenuButtonWebApp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiocryptopay import AioCryptoPay, Networks
import json
import base64
import aiohttp
from aiohttp import web
from typing import Any, Awaitable, Callable, Dict
from aiogram.dispatcher.middlewares.base import BaseMiddleware
import logging
from logging.handlers import RotatingFileHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('app.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

import os
from dotenv import load_dotenv

import database as db
import remnawave_api as panel_api

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOPAY_TOKEN = os.getenv("CRYPTOPAY_TOKEN")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip().isdigit()]

# Инициализация CryptoPay
cryptopay = None

# Инициализация ЮKassa
yookassa_enabled = False
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    try:
        from yookassa import Configuration, Payment as YKPayment
        Configuration.account_id = YOOKASSA_SHOP_ID
        Configuration.secret_key = YOOKASSA_SECRET_KEY
        yookassa_enabled = True
        logger.info("YooKassa initialized successfully")
    except Exception as e:
        logger.error(f"YooKassa init error: {e}")

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_days = State()
    waiting_for_broadcast_msg = State()
    waiting_for_broadcast_btn = State()
    waiting_for_search_id = State()
    waiting_for_limit_user = State()
    waiting_for_limit_count = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()

class AdminSupportStates(StatesGroup):
    replying_to_user = State()

class GiftStates(StatesGroup):
    waiting_for_recipient = State()

class GiftStates(StatesGroup):
    waiting_for_recipient = State()


class GiftStates(StatesGroup):
    waiting_for_recipient = State()


support_timeouts: Dict[int, asyncio.Task] = {} # user_id -> asyncio.Task

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- МИДЛВАРЬ АНТИСПАМА ---
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, time_limit: float = 1.0):
        self.limit = time_limit
        self.user_timestamps = {}

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        now = datetime.now()

        if user_id in self.user_timestamps:
            delta = (now - self.user_timestamps[user_id]).total_seconds()
            if delta < self.limit:
                # Игнорируем запрос, если прошло меньше времени (спам)
                if isinstance(event, CallbackQuery):
                    await event.answer("Подождите немного перед следующим действием...", show_alert=True)
                return
                
        self.user_timestamps[user_id] = now
        try:
            return await handler(event, data)
        except Exception as e:
            logger.exception(f"Exception handling event from {user_id}: {e}")
            raise


# Регистрация мидлвари
dp.message.middleware(ThrottlingMiddleware())
dp.callback_query.middleware(ThrottlingMiddleware())

# Инициализация БД при запуске
db.init_db()


# --- PROXY SERVER LOGIC ---
def extract_hwid(request: web.Request) -> tuple[str, str, str, str, bool]:
    user_agent = request.headers.get("User-Agent", "Unknown")
    x_device_id = request.headers.get("X-Device-Id")
    x_device_model = request.headers.get("X-Device-Model", "Unknown")
    x_device_os = request.headers.get("X-Device-Os", "Unknown")
    
    ua_lower = user_agent.lower()
    
    # Список известных VPN-клиентов
    vpn_clients = ["v2rayNG", "v2raytun", "Shadowrocket", "V2Box", "Happ/", "Hiddify", "CFNetwork", "Darwin"]
    is_vpn_client = any(client.lower() in ua_lower for client in vpn_clients) or x_device_id
    
    # Список признаков браузера
    browser_keywords = ["Mozilla", "Chrome", "Safari", "Edge", "Opera", "YaBrowser"]
    is_likely_browser = any(k.lower() in ua_lower for k in browser_keywords)
    
    # Если это браузер и НЕ ЯВНЫЙ VPN-клиент, помечаем как браузер
    is_browser = is_likely_browser and not is_vpn_client

    if x_device_id:
        return x_device_id, x_device_model, x_device_os, user_agent, False
    
    # По умолчанию
    hwid = user_agent[:100]
    model = "VPN Client"
    platform = "Unknown"
    
    # 1. Попытка специфичного парсинга по форматам клиентов
    if "v2raytun" in ua_lower:
        model = "v2rayTun"
        # v2raytun/ios/1.0.0/uuid или v2raytun/android/...
        parts = user_agent.split("/")
        for p in parts:
            pl = p.lower()
            if pl == "ios": platform = "iOS"
            elif pl == "android": platform = "Android"
        if len(parts) >= 4:
            hwid = parts[3]
            
    elif "hiddify" in ua_lower or "happ/" in ua_lower:
        model = "Hiddify Client"
        parts = user_agent.split("/")
        if len(parts) >= 4:
            platform = parts[2].capitalize()
            hwid = parts[3]
            
    elif "v2rayng" in ua_lower:
        model = "v2rayNG"
        platform = "Android"
        
    elif "shadowrocket" in ua_lower:
        model = "Shadowrocket"
        platform = "iOS"
        
    elif "v2box" in ua_lower:
        model = "V2Box"
        platform = "iOS"
        
    # 2. Если платформа все еще не определена, ищем по ключевым словам в UA
    if platform == "Unknown":
        if any(kw in ua_lower for kw in ["iphone", "ipad", "ipod", "ios"]):
            platform = "iOS"
        elif "android" in ua_lower:
            platform = "Android"
        elif "windows" in ua_lower:
            platform = "Windows"
        elif any(kw in ua_lower for kw in ["macintosh", "macos", "darwin", "cfnetwork"]):
            platform = "macOS/iOS"
        elif "linux" in ua_lower:
            platform = "Linux"

    return hwid, model, platform, user_agent, is_browser


async def handle_sub_request(request: web.Request):
    uuid_path = request.match_info.get("uuid_path", "")
    full_path = request.path
    logger.info(f"--- Incoming Proxy Request ---")
    logger.info(f"Path: {full_path}")
    logger.info(f"UUID Fragment: {uuid_path}")

    if not uuid_path:
        logger.warning("Empty UUID fragment received")
        return web.Response(status=404, text="Not Found")
        
    import sqlite3
    conn = sqlite3.connect(db.DB_NAME)
    cursor = conn.cursor()
    # Ищем по частичному совпадению или по полному пути
    cursor.execute("SELECT telegram_id, is_active FROM users WHERE uuid LIKE ?", (f"%{uuid_path}%",))
    user_row = cursor.fetchone()
    conn.close()
    
    telegram_id = user_row[0] if user_row else None
    
    if telegram_id:
        logger.info(f"User identified: {telegram_id}")
        hwid, model, platform, ua, is_browser = extract_hwid(request)
        
        if is_browser:
            logger.info(f"Browser detected for User {telegram_id}, skipping device tracking.")
        else:
            device_count = db.get_device_count(telegram_id)
            user_limit = db.get_user_device_limit(telegram_id)
            
            conn = sqlite3.connect(db.DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM user_devices WHERE telegram_id = ? AND hwid = ?", (telegram_id, hwid))
            device_exists = cursor.fetchone() is not None
            conn.close()
            
            if not device_exists and device_count >= user_limit:
                logger.warning(f"Device limit exceeded for User {telegram_id}. Limit: {user_limit}")
                
                # Уведомляем пользователя
                asyncio.create_task(
                    bot.send_message(
                        chat_id=telegram_id,
                        text=f"⚠️ <b>Внимание! При попытке подключиться достигнут лимит устройств.</b>\n\nВы пытаетесь использовать конфигурацию на {user_limit + 1}-ом устройстве, но ваше максимальное количество — {user_limit}.\nПожалуйста, удалите старые устройства (если нужно, переустановите подписку).",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Управление устройствами", callback_data="menu_devices")]]),
                        parse_mode="HTML"
                    )
                )
                
                dummy_node = f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443?security=none&type=tcp#❌ Превышен лимит устройств (макс {user_limit})"
                encoded = base64.b64encode(dummy_node.encode('utf-8')).decode('utf-8')
                return web.Response(text=encoded, content_type="text/plain; charset=utf-8")
                
            db.add_or_update_device(telegram_id, hwid, model, platform, ua)
    else:
        logger.info(f"User NOT found in DB for path: {uuid_path}")


    # Формируем целевой URL. 
    # Если uuid_path уже содержит / в начале или PANEL_URL имеет слеш, убираем один
    base_url = panel_api.PANEL_URL.rstrip('/')
    clean_uuid = uuid_path.lstrip('/')
    target_url = f"{base_url}/{clean_uuid}"
    
    logger.info(f"Forwarding to Panel: {target_url}")
    
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'connection']}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(target_url, headers=headers) as resp:
                content = await resp.read()
                logger.info(f"Panel Response: {resp.status}")
                return web.Response(body=content, status=resp.status, headers=resp.headers)
        except Exception as e:
            logger.error(f"Proxy Forwarding Error: {e}")
            return web.Response(status=500, text=f"Gateway Error: {str(e)}")



async def run_proxy_server():
    app = web.Application()
    app.router.add_get('/{uuid_path:.*}', handle_sub_request)
    
    proxy_port = int(os.getenv("PROXY_PORT", "8080"))
    proxy_host = os.getenv("PROXY_HOST", "0.0.0.0")
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, proxy_host, proxy_port)
    await site.start()
    logger.info(f"Subscription Proxy Server started on http://{proxy_host}:{proxy_port}")
# --- END PROXY LOGIC ---

from urllib.parse import urlencode

def get_webapp_url(user_id: int | None = None) -> str:
    WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com").rstrip('/')
    if user_id:
        user_data = db.get_user(user_id)
        if user_data:
            balance, sub_end, client_uuid = user_data
            sub_link = client_uuid if client_uuid else 'None'
            if sub_link != 'None' and sub_link.startswith("/"):
                proxy_base = os.getenv("PROXY_URL_BASE")
                if proxy_base:
                    sub_link = f"{proxy_base.rstrip('/')}{sub_link}"
                else:
                    sub_link = f"{panel_api.PANEL_URL}{sub_link}"
            end_str = str(sub_end) if sub_end else 'None'
            
            params = {'tid': user_id, 'bal': balance, 'end': end_str, 'uuid': sub_link}
            qs = urlencode(params)
            return f"{WEBAPP_URL}/?{qs}"
    return f"{WEBAPP_URL}/"

def get_main_menu_kb(user_id: int | None = None):
    final_url = get_webapp_url(user_id)

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть приложение (Mini App)", web_app=WebAppInfo(url=final_url))],
        [InlineKeyboardButton(text="🔑 Купить подписку", callback_data="menu_tariffs")],
        [
            InlineKeyboardButton(text="📱 Установить VPN", callback_data="menu_install"),
            InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")
        ],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support_start")],
        [InlineKeyboardButton(text="📄 Документы", callback_data="menu_docs")]
    ])


def get_profile_kb(sub_link: str | None = None, auto_renew: bool = False, has_payment_method: bool = False):
    keyboard = []
    if sub_link:
        keyboard.append([InlineKeyboardButton(text="🔗 Страница вашей подписки", url=sub_link)])
        keyboard.append([
            InlineKeyboardButton(text="🦊 Импорт в v2RayTun", url=f"https://guepardvpn.top/redirect.html?app=v2raytun&url={sub_link}"),
            InlineKeyboardButton(text="🐰 Импорт в Happ", url=f"https://guepardvpn.top/redirect.html?app=happ&url={sub_link}")
        ])
    keyboard.extend([
        [InlineKeyboardButton(text="⚙️ Устройства", callback_data="menu_devices")],
        [InlineKeyboardButton(text="💳 Оплатить VPN", callback_data="menu_tariffs")],
    ])
    # if has_payment_method:
    if auto_renew:
        keyboard.append([InlineKeyboardButton(text="Отключить автопродление", callback_data="toggle_auto_renew")])
    keyboard.extend([
        [InlineKeyboardButton(text="🎁 Подарить подписку", callback_data="gift_sub_start")],
        [InlineKeyboardButton(text="🤝 Партнерская программа", callback_data="menu_referral")],
    ])
    keyboard.append([InlineKeyboardButton(text="Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin_give_sub")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin_search_user")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📱 Лимит устройств", callback_data="admin_device_limit")],
        [InlineKeyboardButton(text="🔔 Тест уведомления", callback_data="admin_test_notify")],
        [InlineKeyboardButton(text="🔙 Выход", callback_data="menu_main")]
    ])

def get_devices_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💥 Сбросить устройства (сменить ключ подписки)", callback_data="action_reset_ips")],
        [InlineKeyboardButton(text="🔙 Назад в кабинет", callback_data="menu_profile")]
    ])

def get_install_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍏 iPhone (iOS)", callback_data="stub_os_ios"),
            InlineKeyboardButton(text="🤖 Android", callback_data="stub_os_android")
        ],
        [
            InlineKeyboardButton(text="💻 MacOS", callback_data="stub_os_macos"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="stub_os_windows")
        ],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])


def get_tariffs_kb(telegram_id: int):
    keyboard = []
    if not db.has_ever_subscribed(telegram_id):
        keyboard.append([InlineKeyboardButton(text="🎁 Бесплатный тест (24 часа)", callback_data="test_24h")])
        
    keyboard.extend([
        [InlineKeyboardButton(text="1 месяц - 99р", callback_data="select_tariff_30")],
        [InlineKeyboardButton(text="🔥 3 месяца (Выгода 20%) - 249р", callback_data="select_tariff_90")],
        [InlineKeyboardButton(text="12 месяцев (Выгода 33%) - 899р", callback_data="select_tariff_365")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_payment_method_kb(days: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплата картой (ЮKassa)", callback_data=f"buy_yoomoney_{days}")],
        [InlineKeyboardButton(text="⭐️ Telegram Stars", callback_data=f"buy_stars_{days}")],
        [InlineKeyboardButton(text="🪙 Криптовалюта", callback_data=f"buy_crypto_{days}")],
        [InlineKeyboardButton(text="🔙 Назад к тарифами", callback_data="menu_tariffs")]
    ])


def get_docs_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Политика обработки персональных данных ↗️", url="https://guepardvpn.top/policy.html")],
        [InlineKeyboardButton(text="Публичная оферта ↗️", url="https://guepardvpn.top/oferta.html")],
        [InlineKeyboardButton(text="Политика возвратов ↗️", url="https://guepardvpn.top/refund-policy.html")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="stub_faq")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

async def send_success_message(user_id: int, days: int, is_gift: bool = False, giver_username: str = None):
    text = (
        f"🎉 <b>Подписка успешно оформлена!</b>\n\n"
        f"Вам начислено <b>{days} дней</b> подписки.\n"
        f"Перейдите в Личный кабинет, чтобы скопировать ваш ключ."
    )
    if is_gift:
        text = (
            f"🎁 <b>Вам подарок!</b>\n\n"
            f"Пользователь <b>{giver_username}</b> подарил вам подписку на <b>{days} дней</b>!\n"
            f"Перейдите в Личный кабинет за подробностями."
        )
        
    try:
        from aiogram.types import FSInputFile
        import os
        image_path = "Подписка куплена.png"
        kb = get_main_menu_kb(user_id)
        
        if os.path.exists(image_path):
            photo = FSInputFile(image_path)
            await bot.send_photo(chat_id=user_id, photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление об оплате {user_id}: {e}")

async def send_welcome_message(chat_id: int, user_id: int):
    user_data = db.get_user(user_id)
    is_active = False
    if user_data and user_data[1]:
        try:
            sub_end_date = datetime.strptime(user_data[1], "%Y-%m-%d %H:%M:%S")
            if sub_end_date > datetime.now():
                is_active = True
        except:
            pass

    bot_info = await bot.get_me()
    support_url = f"https://t.me/{bot_info.username}?start=support"

    if is_active:
        text = (
            "🔐 <b>Подписка активна — Вы под защитой GuepardVPN🐆</b>\n\n"
            "Ваш трафик зашифрован, личные данные в безопасности. Подключайтесь в один клик — и пользуйтесь интернетом уверенно.\n\n"
            "📱 До 3 устройств — один ключ.\n"
            "🛠 Управление и инструкции — в личном кабинете.\n"
            f"💬 Нужна помощь? <a href='{support_url}'>Напишите нам</a>"
        )
    else:
        text = (
            "👋 Добро пожаловать в GuepardVPN🐆 — быстрый и стабильный сервис!\n\n"
            "🔥 Получите 1 день бесплатно, чтобы всё протестировать.\n"
            "⚡️ Затем выберите тариф и оформите подписку в пару кликов.\n"
            "🔒 Ваши данные будут под надёжной защитой.\n\n"
            f"💬 Нужна помощь? <a href='{support_url}'>Напишите нам</a>\n\n"
            "Как начать пользоваться:\n"
            "1️⃣ Нажмите «Купить подписку»\n"
            "2️⃣ Выберите тариф\n"
            "3️⃣ Оплатите любым удобным способом\n"
            "4️⃣ Перейдите в «Личный кабинет»\n"
            "5️⃣ Подключайтесь и пользуйтесь безопасным интернетом\n\n"
            "👇 Выберите действие из списка"
        )

    video_url = "BAACAgIAAxkBAAOOabr0LEVa_aU-Xbvnxs7oENdDelcAAjSXAALNodlJkD-ePULV8Fw6BA"

    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(text="GuepardVPN", web_app=WebAppInfo(url=get_webapp_url(user_id)))
        )
    except Exception as e:
        logger.error(f"Failed to set menu button: {e}")

    await bot.send_video(
        chat_id=chat_id,
        video=video_url,
        caption=text,
        reply_markup=get_main_menu_kb(user_id),
        parse_mode="HTML"
    )

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    referrer_id = None
    if message.text:
        args = message.text.split()
        if len(args) > 1:
            if args[1].startswith("ref_"):
                try:
                    ref_id = int(args[1].split("_")[1])
                    if ref_id != message.from_user.id:
                        referrer_id = ref_id
                except ValueError:
                    pass
            elif args[1].startswith("buy_"):
                parts = args[1].split("_")
                if len(parts) >= 3:
                    method = parts[1]
                    days = int(parts[2])
                    
                    if method == "stars":
                        prices_map = {30: 55, 90: 139, 365: 495}
                        price = prices_map.get(days, 55)
                        await bot.send_invoice(
                            chat_id=message.chat.id,
                            title=f"Подписка GuepardVPN на {days} дней",
                            description="Безлимитный трафик, высокая скорость и надежная защита ваших данных.",
                            payload=f"vpn_sub_{days}",
                            provider_token="",
                            currency="XTR",
                            prices=[LabeledPrice(label="VPN Подписка", amount=price)]
                        )
                        return

                    elif method == "yoomoney":
                        if not yookassa_enabled:
                            await message.answer("Оплата картой временно недоступна.")
                            return
                        prices_map = {30: 99, 90: 249, 365: 899}
                        price = prices_map.get(days, 0)
                        if price:
                            try:
                                import uuid as _uuid
                                from yookassa import Payment as YKPayment
                                label = f"vpn_{message.from_user.id}_{days}"
                                payment = YKPayment.create({
                                    "amount": {"value": f"{price}.00", "currency": "RUB"},
                                    "confirmation": {"type": "redirect", "return_url": "https://t.me/guepardvpn_bot"},
                                    "capture": True,
                                    "save_payment_method": True,
                                    "description": f"Подписка GuepardVPN на {days} дней",
                                    "metadata": {"label": label}
                                }, str(_uuid.uuid4()))
                                url = payment.confirmation.confirmation_url
                                kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="💳 Оплатить (ЮKassa)", url=url)]
                                ])
                                await message.answer(
                                    f"🧾 <b>Счет на оплату (Банковская карта / СБП)</b>\n\n"
                                    f"Тариф: <b>{days} дней</b>\n"
                                    f"Сумма: <b>{price} руб</b>\n\n"
                                    f"Нажмите кнопку ниже для безопасной оплаты. После успешной оплаты подписка активируется автоматически в течение минуты.",
                                    reply_markup=kb, parse_mode="HTML"
                                )
                            except Exception as e:
                                logger.error(f"ЮKassa error in start link: {e}")
                                await message.answer("Ошибка создания платежа.")
                        return
                        
                    elif method == "crypto":
                        if not cryptopay:
                            await message.answer("Оплата криптовалютой временно недоступна.")
                            return
                        prices = {30: 99, 90: 249, 365: 899}
                        price_rub = prices.get(days, 0)
                        if price_rub:
                            try:
                                price_usdt = round(price_rub / 80.0, 2)
                                invoice = await cryptopay.create_invoice(asset="USDT", amount=price_usdt, payload=f"vpn_{message.from_user.id}_{days}")
                                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🪙 Оплатить (CryptoBot)", url=invoice.bot_invoice_url)]])
                                await message.answer(
                                    f"🧾 <b>Счет на оплату криптовалютой</b>\n\n"
                                    f"Тариф: <b>{days} дней</b>\n"
                                    f"Сумма: <b>{price_usdt} USDT</b> (~{price_rub} руб)\n\n"
                                    f"Нажмите кнопку ниже для безопасной оплаты. Ваши средства поступят напрямую. После успешной оплаты подписка активируется автоматически в течение минуты.",
                                    reply_markup=kb, parse_mode="HTML"
                                )
                            except Exception as e:
                                logger.error(f"CryptoPay error in start link: {e}")
                                await message.answer("Ошибка создания платежа CryptoPay.")
                        return
            elif args[1] == "support":
                await state.set_state(SupportStates.waiting_for_message)
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Закрыть вопрос", callback_data="support_close")]])
                await message.answer(
                    "📝 <b>Служба поддержки</b>\n\nНапишите ваш вопрос одним сообщением.\nЕсли передумали, нажмите «Закрыть вопрос».\n\nУ вас есть 5 минут на отправку сообщения.",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
                user_id = message.from_user.id
                if user_id in support_timeouts:
                    support_timeouts[user_id].cancel()
                task = asyncio.create_task(support_timeout(user_id, state))
                support_timeouts[user_id] = task
                return

    db.add_user(message.from_user.id, message.from_user.username, referrer_id)
    await send_welcome_message(message.chat.id, message.from_user.id)
# --- АДМИН ПАНЕЛЬ ---

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return  # Игнорируем, если пишет не админ

    text = "🔒 <b>Панель администратора</b>\n\nВыберите нужное действие:"
    await message.answer(text, reply_markup=get_admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_device_limit")
async def process_admin_device_limit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.answer("📱 Введите Telegram ID или @username пользователя для настройки лимита устройств:")
    await state.set_state(AdminStates.waiting_for_limit_user)
    await callback.answer()

@dp.message(AdminStates.waiting_for_limit_user)
async def process_limit_user_input(message: Message, state: FSMContext):
    text = message.text.strip()
    target_id = None
    if text.isdigit():
        target_id = int(text)
    else:
        username = text.lstrip("@")
        found_id = db.get_user_by_username(username)
        if found_id:
            target_id = found_id
    if not target_id or not db.get_user(target_id):
        await message.answer("❌ Пользователь не найден. Проверьте ID или username:")
        return
    await state.update_data(limit_target_id=target_id)
    cur_limit = db.get_user_device_limit(target_id)
    await message.answer(f"👤 Пользователь <b>{target_id}</b>.\nТекущий лимит устройств: <b>{cur_limit}</b>\n\nВведите новый лимит (число):", parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_limit_count)

@dp.message(AdminStates.waiting_for_limit_count)
async def process_limit_count_input(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Введите число:")
        return
    new_limit = int(message.text)
    data = await state.get_data()
    target_id = data.get('limit_target_id')
    
    db.set_user_device_limit(target_id, new_limit)
    await panel_api.update_hwid_device_limit(target_id, new_limit)
    
    await message.answer(f"✅ Лимит устройств для <b>{target_id}</b> успешно обновлён до <b>{new_limit}</b>.", parse_mode="HTML")
    await state.clear()


@dp.callback_query(F.data == "admin_test_notify")
async def process_admin_test_notify(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
    import os

    # Имитируем данные для теста (как будто осталось 3 дня)
    # Текст перенесен 1 в 1 из оригинальной функции check_expiring_soon
    base_notify_text = "⏳ <b>Ваша подписка истекает через 3 дня.</b>"
    test_image_path = "3 дня.png"  # Убедитесь, что этот файл есть на сервере!
    admin_id = callback.from_user.id

    # Вспомогательная функция для сборки и отправки сообщения админу
    async def send_test_message(notify_text: str, is_auto_renew_on: bool):
        if is_auto_renew_on:
            # Текст и клавиатура перенесены 1 в 1 для включенного автопродления
            renewal_note = (
                "\n\n🔄 <b>Автопродление включено</b> — подписка будет продлена автоматически, "
                "сумма спишется с вашей сохранённой карты.\n"
                "Чтобы отключить автопродление, перейдите в 👤 <b>Личный кабинет</b>."
            )
            text = f"{notify_text}{renewal_note}"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
                [InlineKeyboardButton(text="🔄 Автопродление: ВКЛ (отключить)", callback_data="toggle_auto_renew")]
            ])
            status_desc = "ВКЛЮЧЕННЫМ"
        else:
            # Текст и клавиатура перенесены 1 в 1 для выключенного автопродления
            text = f"{notify_text}\n\nПожалуйста, продлите подписку заранее, чтобы не потерять доступ к VPN."
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="menu_tariffs")],
                [InlineKeyboardButton(text="🔕 Автопродление: ВЫКЛ (включить)", callback_data="toggle_auto_renew")]
            ])
            status_desc = "ВЫКЛЮЧЕННЫМ"

        full_text = f"⚙️ <b>[ТЕСТУВЕДОМЛЕНИЯ] Вариант с {status_desc} автопродлением:</b>\n\n{text}"

        try:
            if test_image_path and os.path.exists(test_image_path):
                photo = FSInputFile(test_image_path)
                await bot.send_photo(chat_id=admin_id, photo=photo, caption=full_text, reply_markup=kb,
                                     parse_mode="HTML")
            else:
                await bot.send_message(chat_id=admin_id, text=f"🖼️ (Нет картинки {test_image_path})\n\n{full_text}",
                                       reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            pass  # Если бот не может отправить сообщение себе, игнорируем (например, нет чата)

    # Убираем часики загрузки с кнопки
    await callback.answer("⏳ Генерирую тестовые уведомления...")

    # Отправляем 1-й вариант (Автопродление: ВЫКЛ)
    await send_test_message(base_notify_text, is_auto_renew_on=False)

    # Отправляем 2-й вариант (Автопродление: ВКЛ)
    await send_test_message(base_notify_text, is_auto_renew_on=True)

@dp.callback_query(F.data == "admin_stats")
async def process_admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    total, active = db.get_stats()
    text = (
        "📊 <b>Статистика сервиса</b>\n\n"
        f"Всего пользователей: {total}\n"
        f"Активных подписок: {active}"
    )
    await callback.message.edit_text(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "admin_give_sub")
async def process_admin_give_sub(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await callback.message.answer("Введите Telegram ID или @username пользователя, которому нужно выдать подписку:")
    await state.set_state(AdminStates.waiting_for_user_id)
    await callback.answer()


@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id_input(message: Message, state: FSMContext):
    text = message.text.strip()
    target_id = None
    
    if text.isdigit():
        target_id = int(text)
    else:
        username = text.lstrip("@")
        found_id_by_username = db.get_user_by_username(username)
        if found_id_by_username:
            target_id = found_id_by_username
            
    if not target_id:
        await message.answer("Ошибка. Введите корректный ID (только цифры) или @username пользователя из базы:")
        return

    # Check if target_id exists in db
    user_data = db.get_user(target_id)
    if not user_data:
        await message.answer(f"Пользователь не найден в базе данных. Проверьте ID/username и попробуйте еще раз:")
        return

    await state.update_data(target_id=target_id)
    await message.answer(f"Пользователь найден (ID: {target_id}). Сколько дней подписки выдать?")
    await state.set_state(AdminStates.waiting_for_days)


@dp.message(AdminStates.waiting_for_days)
async def process_days_input(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Ошибка. Введите число дней цифрами:")
        return

    days = int(message.text)
    data = await state.get_data()
    target_id = data['target_id']

    # Выдаем подписку в базе данных
    db.add_subscription(target_id, days)

    # Генерируем ключ в панели, если его еще нет, или обновляем дату
    user_data = db.get_user(target_id)
    if user_data:
        balance, sub_end, client_uuid = user_data
        expiry_ms = 0
        if sub_end:
            dt = datetime.strptime(sub_end, "%Y-%m-%d %H:%M:%S")
            expiry_ms = int(dt.timestamp() * 1000)

        if not client_uuid:
            new_uuid = await panel_api.add_client(target_id, expiry_ms)
            if new_uuid:
                db.update_uuid(target_id, new_uuid)
        else:
            await panel_api.update_client_expiry(client_uuid, target_id, expiry_ms)

        await message.answer(f"✅ Успешно! Пользователю {target_id} выдано {days} дней подписки.")

        # Уведомляем самого пользователя
        try:
            await send_success_message(target_id, days)
        except:
            await message.answer("Пользователь не получил уведомление (возможно, он заблокировал бота).")
    else:
        await message.answer("❌ Пользователь с таким ID не найден в базе.")

    await state.clear()


# --- РАССЫЛКА ---
@dp.callback_query(F.data == "admin_broadcast")
async def process_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await callback.message.answer(
        "📝 Оправьте сообщение для массовой рассылки всем пользователям бота.\n"
        "Вы можете прикрепить фото или видео с текстом (сохраняется форматирование).",
    )
    await state.set_state(AdminStates.waiting_for_broadcast_msg)
    await callback.answer()


@dp.message(AdminStates.waiting_for_broadcast_msg)
async def process_broadcast_message(message: Message, state: FSMContext):
    await state.update_data(
        broadcast_msg_id=message.message_id,
        broadcast_chat_id=message.chat.id
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="broadcast_skip_btn")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_main")]
    ])
    
    await message.answer(
        "Отлично! Сообщение сохранено.\n\n"
        "🔗 Хотите добавить кнопку-ссылку под сообщением?\n"
        "Отправьте её в формате: <b>Текст кнопки - https://link.com</b>\n"
        "Или нажмите «Пропустить».\n",
        reply_markup=kb, parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_broadcast_btn)

@dp.callback_query(F.data == "broadcast_skip_btn")
async def process_broadcast_skip_btn(callback: CallbackQuery, state: FSMContext):
    await execute_broadcast(callback.message, state, None)
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_btn)
async def process_broadcast_btn_message(message: Message, state: FSMContext):
    text = message.text.strip()
    if " - " not in text:
        await message.answer("Неверный формат. Пожалуйста, используйте формат: Текст - https://link.com\nИли нажмите Пропустить.")
        return
        
    parts = text.split(" - ", 1)
    btn_text = parts[0].strip()
    btn_url = parts[1].strip()
    
    if not btn_url.startswith("http"):
        btn_url = "https://" + btn_url
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=btn_url)]])
    await execute_broadcast(message, state, kb)

async def execute_broadcast(context_message: Message, state: FSMContext, reply_markup: InlineKeyboardMarkup | None):
    data = await state.get_data()
    msg_id = data.get("broadcast_msg_id")
    chat_id = data.get("broadcast_chat_id")
    
    await state.clear()
    users = db.get_all_users()
    success = 0
    fail = 0
    
    await context_message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей...")
    
    for user_id in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=chat_id,
                message_id=msg_id,
                reply_markup=reply_markup
            )
            success += 1
        except Exception:
            fail += 1
            
        await asyncio.sleep(0.05)
        
    await context_message.answer(f"✅ Рассылка завершена!\n\nУспешно: {success}\nОшибок: {fail}")


# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ---
@dp.callback_query(F.data == "admin_search_user")
async def process_admin_search_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await callback.message.answer("🔍 Введите Telegram ID пользователя или его @username:")
    await state.set_state(AdminStates.waiting_for_search_id)
    await callback.answer()


@dp.message(AdminStates.waiting_for_search_id)
async def process_search_user_input(message: Message, state: FSMContext):
    text = message.text.strip()
    target_id = None
    
    if text.isdigit():
        target_id = int(text)
    else:
        username = text.lstrip("@")
        found_id_by_username = db.get_user_by_username(username)
        if found_id_by_username:
            target_id = found_id_by_username
            
    if not target_id:
        await message.answer("Ошибка. Введите корректный ID (только цифры) или @username пользователя из базы:")
        return

    user_data = db.get_user(target_id)
    
    if not user_data:
        await message.answer("❌ Пользователь с таким ID не найден в базе.")
        await state.clear()
        return

    balance, sub_end, client_uuid = user_data
    
    text = (
        f"👤 <b>Профиль пользователя {target_id}</b>\n\n"
        f"Дата окончания: {sub_end or 'Нет'}\n"
        f"UUID: <code>{client_uuid or 'Нет ключа'}</code>\n"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Обнулить и удалить ключ", callback_data=f"admin_delete_user_{target_id}")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="menu_main")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.clear()


@dp.callback_query(F.data.startswith("admin_delete_user_"))
async def process_admin_delete_user(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    target_id = int(callback.data.split("_")[3])
    user_data = db.get_user(target_id)
    
    if user_data:
        client_uuid = user_data[2]
        if client_uuid:
            # Отключаем клиента в панели (мы не удаляем полностью, а дизейблим для простоты)
            await panel_api.disable_client(client_uuid, target_id)
            
        db.delete_user_data(target_id)
        
        await callback.message.edit_text(f"✅ Данные подписки пользователя {target_id} были удалены (ключ аннулирован).")
        try:
             await bot.send_message(target_id, "⚠️ Администратор аннулировал вашу подписку и удалил ключ.")
        except:
             pass
    else:
        await callback.answer("Ошибка: пользователь не найден.")
        
    await callback.answer()

async def safe_edit(message, text: str, reply_markup=None, parse_mode=None):
    """Try to edit a message's text; if the message has no text (e.g. a video),
    delete it and send a new text message instead."""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


@dp.callback_query(F.data == "menu_main")
async def process_main_menu(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_welcome_message(callback.message.chat.id, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "menu_install")
async def process_install(callback: CallbackQuery):
    text = (
        "🛠 <b>Выберите ваше устройство:</b>\n\n"
        "Мы подготовили пошаговые инструкции для подключения. "
        "Нажмите на нужную кнопку и подключайтесь за пару минут."
    )
    await safe_edit(callback.message, text, reply_markup=get_install_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "gift_sub_start")
async def process_gift_sub_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎁 <b>Подарить подписку</b>\n\nВведите Telegram ID пользователя или его @username, которому хотите сделать подарок:", parse_mode="HTML")
    await state.set_state(GiftStates.waiting_for_recipient)
    await callback.answer()

@dp.message(GiftStates.waiting_for_recipient)
async def process_gift_recipient(message: Message, state: FSMContext):
    text = message.text.strip()
    target_id = None
    if text.isdigit():
        target_id = int(text)
    elif text.startswith("@") or text.isalnum():
        username = text.lstrip("@")
        found_id_by_username = db.get_user_by_username(username)
        if found_id_by_username:
            target_id = found_id_by_username

    if not target_id or not db.get_user(target_id):
        await message.answer("❌ Пользователь не найден в базе данных.\nПроверьте данные и попробуйте еще раз (введите ID или @username):")
        return

    if target_id == message.from_user.id:
        await message.answer("Вы не можете подарить подписку самому себе. Выберите 'Оплатить VPN' в главном меню.")
        await state.clear()
        return

    await state.update_data(gift_recipient_id=target_id)
    text = f"🎁 Вы собираетесь купить подписку в подарок для пользователя (ID: <code>{target_id}</code>).\n\n💎 <b>Выберите подходящий тариф:</b>"
    await message.answer(text, reply_markup=get_tariffs_kb(target_id), parse_mode="HTML")
    await state.set_state(None)


@dp.callback_query(F.data == "menu_tariffs")
async def process_tariffs(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(gift_recipient_id=None)
    text = "💎 <b>Выберите подходящий тариф:</b>\n\nЧем дольше период, тем выгоднее цена."
    await safe_edit(callback.message, text, reply_markup=get_tariffs_kb(callback.from_user.id), parse_mode="HTML")


@dp.callback_query(F.data == "menu_docs")
async def process_docs(callback: CallbackQuery):
    text = "На этой странице вы можете ознакомиться с документами сервиса:"
    await safe_edit(callback.message, text, reply_markup=get_docs_kb())
    await callback.answer()


@dp.callback_query(F.data == "stub_os_ios")
async def process_install_os_ios(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами из вашего магазина приложений App Store.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать v2RayTun", url="https://apps.apple.com/ru/app/v2raytun/id6476628951")],
        [InlineKeyboardButton(text="📥 Скачать Happ", url="https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]
    ])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stub_os_android")
async def process_install_os_android(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами из вашего магазина приложений Google Play.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать v2RayTun", url="https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru&pli=1")],
        [InlineKeyboardButton(text="📥 Скачать Happ", url="https://play.google.com/store/apps/details?id=com.happproxy&hl=ru")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]
    ])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stub_os_windows")
async def process_install_os_windows(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать v2RayTun", url="https://v2tun.ru/#download")],
        [InlineKeyboardButton(text="📥 Скачать Happ", url="https://www.happ.su/main")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]
    ])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stub_os_macos")
async def process_install_os_macos(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами из AppStore или браузера.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать v2RayTun", url="https://apps.apple.com/ru/app/v2raytun/id6476628951?platform=mac")],
        [InlineKeyboardButton(text="📥 Скачать Happ", url="https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?platform=mac")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]
    ])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stub_faq")
async def process_faq(callback: CallbackQuery):
    text = (
        "❓ <b>Частые вопросы (FAQ)</b>\n\n"
        "<b>1. Разрешены ли торренты?</b>\n"
        "Нет, скачивание торрентов строго запрещено нашими серверами.\n\n"
        "<b>2. На скольких устройствах я могу использовать VPN?</b>\n"
        "Одновременно можно использовать до 3-х устройств на один ключ.\n\n"
        "<b>3. VPN перестал подключаться, что делать?</b>\n"
        "Зайдите в Личный кабинет -> Устройства и нажмите «Сбросить сессии», затем переподключитесь.\n\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="menu_docs")]])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("stub_"))
async def process_stub(callback: CallbackQuery):
    await callback.answer("Эта функция пока в разработке (подключение шлюза) 🚧", show_alert=True)


@dp.callback_query(F.data.startswith("test_24h"))
async def process_test_24h(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    
    if db.has_ever_subscribed(telegram_id):
        await callback.answer("❌ Вы уже пользовались бесплатным периодом или покупали подписку.", show_alert=True)
        return
        
    db.add_subscription(telegram_id, 1) # 1 день = 24 часа
    
    user_data = db.get_user(telegram_id)
    if user_data:
        balance, sub_end, client_uuid = user_data
        expiry_ms = 0
        if sub_end:
            dt = datetime.strptime(sub_end, "%Y-%m-%d %H:%M:%S")
            expiry_ms = int(dt.timestamp() * 1000)

        if not client_uuid:
            new_uuid = await panel_api.add_client(telegram_id, expiry_ms)
            if new_uuid:
                db.update_uuid(telegram_id, new_uuid)
        else:
            await panel_api.update_client_expiry(client_uuid, telegram_id, expiry_ms)
            
    await send_success_message(telegram_id, 1)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@dp.callback_query(F.data == "gift_sub_start")
async def process_gift_sub_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎁 <b>Подарить подписку</b>\n\nВведите Telegram ID пользователя или его @username:")
    await state.set_state(GiftStates.waiting_for_recipient)
    await callback.answer()

@dp.message(GiftStates.waiting_for_recipient)
async def process_gift_recipient(message: Message, state: FSMContext):
    text = message.text.strip()
    target_id = None
    if text.isdigit():
        target_id = int(text)
    else:
        username = text.lstrip("@")
        found_id = db.get_user_by_username(username)
        if found_id:
            target_id = found_id

    if not target_id or not db.get_user(target_id):
        await message.answer("❌ Пользователь не найден в базе данных. Проверьте данные и попробуйте еще раз:")
        return

    await state.update_data(gift_recipient_id=target_id)
    text = f"🎁 Вы собираетесь купить подписку в подарок для пользователя (ID: {target_id}).\n\n💎 <b>Выберите подходящий тариф:</b>"
    await message.answer(text, reply_markup=get_tariffs_kb(target_id), parse_mode="HTML")
    await state.set_state(None)

# Обработка выбора тарифа
@dp.callback_query(F.data.startswith("select_tariff_"))
async def process_select_tariff(callback: CallbackQuery):
    days = int(callback.data.split("_")[2])
    
    prices = {
        30: 99,
        90: 249,
        365: 899
    }
    price = prices.get(days, 0)
    
    text = (
        f"🛒 <b>Оформление подписки</b>\n\n"
        f"Тариф: <b>{days} дней</b>\n"
        f"Стоимость: <b>{price} ₽</b> (или эквивалент)\n\n"
        f"Выберите удобный способ оплаты:"
    )
    
    await callback.message.edit_text(text, reply_markup=get_payment_method_kb(days), parse_mode="HTML")
    await callback.answer()


# Обработка нажатия на тариф ЮKassa
@dp.callback_query(F.data.startswith("buy_yoomoney_"))
async def process_buy_yookassa(callback: CallbackQuery, state: FSMContext):
    if not yookassa_enabled:
        await callback.answer("Оплата картой временно недоступна. Пожалуйста, обратитесь к администратору.", show_alert=True)
        return

    days = int(callback.data.split("_")[2])
    prices_map = {
        30: 99,
        90: 249,
        365: 899
    }
    price = prices_map.get(days, 0)

    if not price:
        await callback.answer("Ошибка тарифа.", show_alert=True)
        return

    data = await state.get_data()
    recipient_id = data.get("gift_recipient_id")
    if recipient_id:
        label = f"vpn_gift_{recipient_id}_{days}_{callback.from_user.id}"
    else:
        label = f"vpn_{callback.from_user.id}_{days}"

    try:
        import uuid as _uuid
        from yookassa import Payment as YKPayment
        # Синхронный вызов -> в поток
        payment = await asyncio.to_thread(YKPayment.create, {
            "amount": {"value": f"{price}.00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/guepardvpn_bot"},
            "capture": True,
            "save_payment_method": True,
            "description": f"Подписка GuepardVPN на {days} дней",
            "metadata": {"label": label}
        }, str(_uuid.uuid4()))

        if not payment or not hasattr(payment, 'confirmation'):
             await callback.answer("Ошибка связи с платежной системой.", show_alert=True)
             return

        url = payment.confirmation.confirmation_url

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить (ЮKassa)", url=url)],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_tariffs")]
        ])

        await callback.message.edit_text(
            f"🧾 <b>Счет на оплату (Банковская карта / СБП)</b>\n\n"
            f"Тариф: <b>{days} дней</b>\n"
            f"Сумма: <b>{price} руб</b>\n\n"
            f"Нажмите кнопку ниже для безопасной оплаты. После успешной оплаты подписка активируется автоматически в течение минуты.",
            reply_markup=kb, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка создания платежа ЮKassa: {e}")
        await callback.answer("Ошибка создания платежа.", show_alert=True)

    await callback.answer()


# Обработка нажатия на тариф Telegram Stars
@dp.callback_query(F.data.startswith("buy_stars_"))
async def process_buy_stars(callback: CallbackQuery, state: FSMContext):
    days = int(callback.data.split("_")[2])

    # Цены в Звездах (XTR)
    prices_map = {
        30: 55,
        90: 139,
        365: 495
    }

    price = prices_map.get(days, 55)

    data = await state.get_data()
    recipient_id = data.get("gift_recipient_id")

    data = await state.get_data()
    recipient_id = data.get("gift_recipient_id")
    if recipient_id:
        payload = f"vpn_gift_{recipient_id}_{days}_{callback.from_user.id}"
    else:
        payload = f"vpn_sub_{days}"

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"Подписка GuepardVPN на {days} дней",
        description="Безлимитный трафик, высокая скорость и надежная защита ваших данных.",
        payload=payload,
        provider_token="",  # Для Telegram Stars токен провайдера должен быть пустым
        currency="XTR",  # Код валюты Telegram Stars
        prices=[LabeledPrice(label="VPN Подписка", amount=price)]
    )
    await callback.answer()


# Обработка нажатия на тариф CryptoPay
@dp.callback_query(F.data.startswith("buy_crypto_"))
async def process_buy_crypto(callback: CallbackQuery, state: FSMContext):
    if not cryptopay:
        await callback.answer("Оплата криптовалютой временно недоступна.", show_alert=True)
        return

    days = int(callback.data.split("_")[2])
    prices = {
        30: 99,
        90: 249,
        365: 899
    }
    price_rub = prices.get(days, 0)
    
    if not price_rub:
        await callback.answer("Ошибка тарифа.", show_alert=True)
        return
        
    try:
        # Для упрощения 100 рублей = 1 USDT
        price_usdt = round(price_rub / 80.0, 2)
        
        data = await state.get_data()
        recipient_id = data.get("gift_recipient_id")
        
        data = await state.get_data()
        recipient_id = data.get("gift_recipient_id")
        if recipient_id:
            payload = f"vpn_gift_{recipient_id}_{days}_{callback.from_user.id}"
        else:
            payload = f"vpn_{callback.from_user.id}_{days}"
        
        invoice = await cryptopay.create_invoice(
            asset='USDT', 
            amount=price_usdt, 
            description=f"Подписка GuepardVPN на {days} дней",
            payload=payload
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=invoice.bot_invoice_url)],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_tariffs")]
        ])
        
        await callback.message.edit_text(
            f"🧾 <b>Счет на оплату криптовалютой</b>\n\n"
            f"Тариф: <b>{days} дней</b>\n"
            f"Сумма: <b>{price_usdt} USDT</b> (~{price_rub} руб)\n\n"
            f"Нажмите кнопку ниже для перехода к оплате в CryptoBot. После успешной оплаты подписка активируется автоматически.",
            reply_markup=kb, parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка создания счета CryptoBot (Native): {e}")
        await callback.answer("Ошибка при создании счета.", show_alert=True)


# Подтверждение готовности к оплате (обязательный шаг в Telegram API)
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)



@dp.callback_query(F.data == "menu_profile")
async def process_profile(callback: CallbackQuery):
    await callback.answer()
    user_data = db.get_user(callback.from_user.id)
    sub_end_date_str = user_data[1] if user_data else None
    client_uuid = user_data[2] if user_data else None

    auto_renew_flag, saved_pm = db.get_auto_renew(callback.from_user.id)

    status_text = "❌ Неактивна"
    time_left_str = "0 дн."
    key_text = "<i>(появится после оплаты)</i>"
    used_gb = 0.0
    sub_link = None
    device_count = db.get_device_count(callback.from_user.id)

    if sub_end_date_str:
        sub_end_date = datetime.strptime(sub_end_date_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        if sub_end_date > now:
            status_text = "✅ Активна"
            time_diff = sub_end_date - now
            d_left = time_diff.days
            h_left = time_diff.seconds // 3600

            if d_left > 0:
                time_left_str = f"{d_left} дн. {h_left} ч."
            else:
                time_left_str = f"{h_left} ч."

            used_gb = await panel_api.get_client_stats(callback.from_user.id)

            if client_uuid:
                sub_link = client_uuid
                if sub_link.startswith("/"):
                    proxy_base = os.getenv("PROXY_URL_BASE")
                    if proxy_base:
                        sub_link = f"{proxy_base.rstrip('/')}{sub_link}"
                    else:
                        sub_link = f"{panel_api.PANEL_URL}{sub_link}"
                key_text = f"<code>{sub_link}</code>\n<i>* Нажмите, чтобы скопировать</i>"

    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"ID: <code>{callback.from_user.id}</code>\n\n"
        f"Статус подписки: {status_text}\n"
        f"Осталось времени: {time_left_str}\n"
        f"Использовано трафика: {used_gb} ГБ\n"
        f"📱 Устройства: {device_count}/3\n\n"
        f"Ваш ключ (ссылка на подписку):\n{key_text}"
    )

    await safe_edit(callback.message, text, reply_markup=get_profile_kb(sub_link, auto_renew_flag, bool(saved_pm)), parse_mode="HTML")

@dp.callback_query(F.data == "toggle_auto_renew")
async def process_toggle_auto_renew(callback: CallbackQuery):
    user_id = callback.from_user.id
    auto_renew_flag, saved_pm = db.get_auto_renew(user_id)

    if not auto_renew_flag:
        await callback.answer("Автопродление будет включено автоматически при следующей оплате через ЮKassa.", show_alert=True)
        return

    # Отключаем автопродление
    db.set_auto_renew(user_id, False)
    await callback.answer("Автопродление отключено. Вы можете включить его снова при следующей оплате.", show_alert=True)
    await process_profile(callback)


@dp.callback_query(F.data == "menu_devices")
async def process_devices(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    devices = db.get_user_devices(telegram_id)
    
    if not devices:
        text = "📱 <b>Ваши устройства</b>\n\nВы еще не подключали ни одного устройства через ссылку подписки."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад в кабинет", callback_data="menu_profile")]
        ])
        await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return

    text = f"📱 <b>Отслеживание устройств (Макс 3)</b>\n\nПривязано: {len(devices)}/3\n\n"
    keyboard = []
    
    for i, dev in enumerate(devices):
        dev_id, hwid, model, platform, ua, created_at, updated_at = dev
        text += f"<b>{i+1}.</b>\n"
        text += f"📱 Модель: {model}\n"
        text += f"🧠 Платформа: {platform}\n"
        text += f"🌐 User-Agent: {ua}\n"
        text += f"⏱ Создано: {created_at}\n"
        text += f"🔄 Обновлено: {updated_at}\n"
        text += f"🔑 HWID: <code>{hwid}</code>\n\n"
        
    keyboard.append([InlineKeyboardButton(text="💥 Сбросить устройства (сменить ключ подписки)", callback_data="action_reset_ips")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад в кабинет", callback_data="menu_profile")])
    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "action_reset_ips")
async def process_reset_ips(callback: CallbackQuery):
    telegram_id = callback.from_user.id

    new_sub_url = await panel_api.clear_client_ips(telegram_id)

    if new_sub_url:
        db.update_uuid(telegram_id, new_sub_url)
        db.clear_user_devices(telegram_id)
        # Формируем полную ссылку для новых кнопок
        full_sub_link = new_sub_url
        if full_sub_link.startswith("/"):
            proxy_base = os.getenv("PROXY_URL_BASE")
            if proxy_base:
                full_sub_link = f"{proxy_base.rstrip('/')}{full_sub_link}"
            else:
                full_sub_link = f"{panel_api.PANEL_URL}{full_sub_link}"
                
        text = (
            "🔄 <b>Ваш ключ подписки был изменен!</b>\n\n"
            "В целях безопасности, мы обновили вашу ссылку на подписку.\n\n"
            "⚠️ <b>Пожалуйста, скопируйте новый ключ из Личного кабинета и обновите подписку во всех ваших приложениях VPN.</b>\n\n"
            f"🔑 Ваш новый ключ:\n<code>{full_sub_link}</code>\n<i>(нажмите, чтобы скопировать)</i>"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🦊 v2RayTun", url=f"https://guepardvpn.top/redirect.html?app=v2raytun&url={full_sub_link}"),
                InlineKeyboardButton(text="🐰 Happ", url=f"https://guepardvpn.top/redirect.html?app=happ&url={full_sub_link}")
            ],
            [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")]
        ])
        
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer("✅ Сессии успешно сброшены!", show_alert=True)
    else:
        await callback.answer("❌ Ошибка сброса. Возможно, у вас нет активной подписки.", show_alert=True)

@dp.callback_query(F.data == "menu_referral")
async def process_referral(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    ref_count = db.get_referral_count(telegram_id)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
    
    text = (
        "🤝 <b>Партнерская программа</b>\n\n"
        "Приглашайте друзей и получайте бонусные дни!\n"
        "За первую и последующие оплаты приглашенного друга вы будете получать <b>+3 дня</b> к своей подписке.\n\n"
        f"Ваша реферальная ссылка:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено друзей: <b>{ref_count}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в кабинет", callback_data="menu_profile")]
    ])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


async def support_timeout(user_id: int, state: FSMContext):
    await asyncio.sleep(300) # 5 minutes
    current_state = await state.get_state()
    if current_state == SupportStates.waiting_for_message.state:
        await state.clear()
        try:
            await bot.send_message(
                chat_id=user_id,
                text="⏳ Время ожидания истекло. Вы не отправили сообщение в течение 5 минут. Обращение закрыто.",
                reply_markup=get_main_menu_kb(user_id)
            )
        except:
            pass
    if user_id in support_timeouts:
        task = support_timeouts.pop(user_id, None)
        if task:
            task.cancel()


@dp.callback_query(F.data == "support_start")
async def process_support_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportStates.waiting_for_message)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Закрыть вопрос", callback_data="support_close")]
    ])
    
    await safe_edit(
        callback.message,
        "📝 <b>Служба поддержки</b>\n\nНапишите ваш вопрос одним сообщением.\nЕсли передумали, нажмите «Закрыть вопрос».\n\nУ вас есть 5 минут на отправку сообщения.",
        reply_markup=kb,
        parse_mode="HTML"
    )
    
    # Cancel previous timeout if exists
    user_id = callback.from_user.id
    if user_id in support_timeouts:
        support_timeouts[user_id].cancel()
        
    # Start new timeout
    task = asyncio.create_task(support_timeout(user_id, state))
    support_timeouts[user_id] = task
    await callback.answer()


@dp.callback_query(F.data == "support_close")
async def process_support_close(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in support_timeouts:
        support_timeouts[user_id].cancel()
        del support_timeouts[user_id]
        
    current_state = await state.get_state()
    if current_state == SupportStates.waiting_for_message.state:
        await state.clear()
        
    await callback.message.edit_text(
        "Обращение в поддержку отменено.",
        reply_markup=get_main_menu_kb(user_id)
    )
    await callback.answer()


@dp.message(SupportStates.waiting_for_message)
async def process_support_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Cancel timeout since user sent a message
    if user_id in support_timeouts:
        support_timeouts[user_id].cancel()
        del support_timeouts[user_id]
        
    # Forward the message or send its text to all admins
    user_text = message.text or message.caption or "<i>[Вложение без текста]</i>"
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"
    
    admin_text = (
        f"📩 <b>Новое обращение в поддержку</b>\n"
        f"От: {username} (<code>{user_id}</code>)\n\n"
        f"Текст:\n{user_text}"
    )
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"admin_reply_{user_id}")],
        [InlineKeyboardButton(text="❌ Закрыть вопрос", callback_data=f"admin_close_{user_id}")]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            if message.photo:
                await bot.send_photo(chat_id=admin_id, photo=message.photo[-1].file_id, caption=admin_text, reply_markup=admin_kb, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=admin_id, text=admin_text, reply_markup=admin_kb, parse_mode="HTML")
        except:
            pass

    await state.clear()
    await message.answer(
        "✅ Ваш вопрос отправлен администратору. Ожидайте ответа, мы напишем вам сюда.",
        reply_markup=get_main_menu_kb(user_id)
    )


@dp.callback_query(F.data.startswith("admin_reply_"))
async def process_admin_reply_btn(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
        
    target_user_id = int(callback.data.split("_")[2])
    
    await state.set_state(AdminSupportStates.replying_to_user)
    await state.update_data(reply_to_user_id=target_user_id)
    
    await callback.message.answer(
        f"✍️ Напишите ваш ответ пользователю <code>{target_user_id}</code>.\nЧтобы отменить, вызовите /admin.",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_close_"))
async def process_admin_close_ticket(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
        
    target_user_id = int(callback.data.split("_")[2])
    
    try:
        await bot.send_message(
            chat_id=target_user_id,
            text="🤝 Администратор закрыл ваше обращение. Если у вас появились новые вопросы, вы можете открыть новое обращение в меню поддержки.",
            reply_markup=get_main_menu_kb(target_user_id)
        )
    except:
        pass
        
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"✅ Вопрос от пользователя {target_user_id} закрыт. Пользователь уведомлен.")
    await callback.answer()


@dp.message(AdminSupportStates.replying_to_user)
async def process_admin_reply_message(message: Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get("reply_to_user_id")
    
    if not target_user_id:
        await state.clear()
        return
        
    admin_text = message.text or message.caption or ""
    
    user_text = (
        f"👨‍💻 <b>Ответ от службы поддержки:</b>\n\n"
        f"{admin_text}"
    )
    
    user_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data="support_start")],
        [InlineKeyboardButton(text="❌ Закрыть вопрос", callback_data="support_close_final")]
    ])
    
    try:
        if message.photo:
            await bot.send_photo(chat_id=target_user_id, photo=message.photo[-1].file_id, caption=user_text, reply_markup=user_kb, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=target_user_id, text=user_text, reply_markup=user_kb, parse_mode="HTML")
            
        await message.answer("✅ Сообщение отправлено пользователю.")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: пользователь мог заблокировать бота ({e})")
        
    await state.clear()


@dp.callback_query(F.data == "support_close_final")
async def process_support_close_final(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Обращение завершено. Спасибо!",
        reply_markup=get_main_menu_kb()
    )
    await callback.answer()


@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    is_gift = False
    giver_username = None
    
    if payload.startswith("vpn_gift_"):
        parts = payload.split("_")
        telegram_id = int(parts[2])
        days = int(parts[3])
        buyer_id = int(parts[4])
        is_gift = True
        giver_username = db.get_username(buyer_id) or f"ID:{buyer_id}"
        
        try:
            bot_info = await bot.get_me()
            await bot.send_message(
                chat_id=buyer_id,
                text=f"🎁 Вы успешно подарили подписку на {days} дней пользователю <code>{telegram_id}</code>!", parse_mode="HTML"
            )
        except:
            pass
    else:
        days = int(payload.split("_")[2])
        telegram_id = message.from_user.id

    db.add_subscription(telegram_id, days)

    # Проверяем реферала и выдаем бонусы
    referrer_id = db.get_referrer(telegram_id)
    if referrer_id:
        db.add_subscription(referrer_id, 3)
        try:
            await bot.send_message(
                chat_id=referrer_id,
                text=f"🎁 <b>Бонус!</b>\n\nВаш реферал приобрел подписку. Вам начислено <b>+3 бонусных дня</b>!",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Не удалось отправить уведомление рефереру {referrer_id}: {e}")

    # Проверяем, есть ли уже у пользователя UUID
    user_data = db.get_user(telegram_id)
    client_uuid = user_data[2] if user_data else None

    # Если ключа еще нет, создаем его в панели
    if not client_uuid:
        new_uuid = await panel_api.add_client(telegram_id)
        if new_uuid:
            db.update_uuid(telegram_id, new_uuid)

    await send_success_message(telegram_id, days, is_gift=is_gift, giver_username=giver_username)


# Новая функция для проверки истекших подписок
async def check_expired_subscriptions():
    expired_users = db.get_expired_users()

    for user in expired_users:
        telegram_id, client_uuid = user

        if client_uuid:
            # Отключаем клиента в панели Marzban
            success = await panel_api.disable_client(client_uuid, telegram_id)
            if success:
                # Отмечаем неактивным в локальной БД
                db.set_inactive(telegram_id)

                # Отправляем уведомление пользователю
                try:
                    text = (
                        "⚠️ <b>Ваша подписка истекла!</b>\n\n"
                        "Доступ к VPN приостановлен. Оплатите тариф, "
                        "чтобы снова пользоваться сервисом."
                    )
                    from aiogram.types import FSInputFile
                    import os
                    image_path = "Подписка истекла.png"
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="menu_tariffs")]
                    ])
                    
                    if os.path.exists(image_path):
                        photo = FSInputFile(image_path)
                        await bot.send_photo(chat_id=telegram_id, photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
                    else:
                        await bot.send_message(chat_id=telegram_id, text=text, reply_markup=kb, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление {telegram_id}: {e}")

async def check_expiring_soon():
    active_users = db.get_active_users()
    now = datetime.now()
    from aiogram.types import FSInputFile
    import os

    for user_id, sub_end_date_str in active_users:
        sub_end = datetime.strptime(sub_end_date_str, "%Y-%m-%d %H:%M:%S")
        diff = sub_end - now
        total_hours = diff.total_seconds() / 3600.0

        notify_text = ""
        image_path = None

        if 71.0 < total_hours <= 72.0:
            notify_text = "⏳ <b>Ваша подписка истекает через 3 дня.</b>"
            image_path = "3 дня.png"
        elif 47.0 < total_hours <= 48.0:
            notify_text = "⏳ <b>Ваша подписка истекает через 2 дня.</b>"
            image_path = "2 дня.png"
        elif 23.0 < total_hours <= 24.0:
            notify_text = "⏳ <b>Ваша подписка истекает через 1 день.</b>"
            image_path = "1 день.png"
        elif 2.0 < total_hours <= 3.0:
            notify_text = "⚠️ <b>Внимание! Ваша подписка истекает всего через 3 часа.</b>"
            image_path = "3 часа.png"

        if notify_text:
            # Получаем статус автопродления
            auto_renew_flag, saved_pm = db.get_auto_renew(user_id)

            if auto_renew_flag and saved_pm:
                renewal_note = (
                    "\n\n🔄 <b>Автопродление включено</b> — подписка будет продлена автоматически, "
                    "сумма спишется с вашей сохранённой карты.\n"
                    "Чтобы отключить автопродление, перейдите в 👤 <b>Личный кабинет</b>."
                )
                text = f"{notify_text}{renewal_note}"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
                    [InlineKeyboardButton(text="Отключить автопродление ❌", callback_data="toggle_auto_renew")]
                ])
            else:
                text = f"{notify_text}\n\nПожалуйста, продлите подписку заранее, чтобы не потерять доступ к VPN."
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="menu_tariffs")],
                    [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")]
                ])

            try:
                if image_path and os.path.exists(image_path):
                    photo = FSInputFile(image_path)
                    await bot.send_photo(chat_id=user_id, photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
                else:
                    await bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass


async def check_expired_24h_ago():
    expired_users = db.get_users_expired_24h()
    from aiogram.types import FSInputFile
    import os
    
    image_path = "24 часа прошло.png"
    
    for user_id in expired_users:
        try:
            text = (
                "🚀 <b>Ваш лучший выбор — GuepardVPN</b>\n\n"
                "Прошло 24 часа без вашей подписки, но мы готовы снова защитить ваши данные.\n\n"
                "Не ждите, пока станет поздно — продлите подписку и пользуйтесь быстрым и надёжным подключением!"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Купить подписку", callback_data="menu_tariffs")]
            ])
            
            if os.path.exists(image_path):
                photo = FSInputFile(image_path)
                await bot.send_photo(chat_id=user_id, photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
            
            db.mark_notified_24h(user_id)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление 24h {user_id}: {e}")

async def check_zero_traffic_after_24h():
    """Рассылка, если прошло 24 часа с покупки, подписка активна, но трафик 0"""
    users_to_check = db.get_users_zero_traffic_24h()
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    for user_id in users_to_check:
        try:
            # Проверяем трафик пользователя
            used_traffic_gb = await panel_api.get_client_stats(user_id)
            if used_traffic_gb == 0.0 or used_traffic_gb < 0.001:
                # Отправляем сообщение
                text = (
                    "👋 <b>Привет!</b>\n\n"
                    "Прошло 24 часа с момента вашей подписки на GuepardVPN, "
                    "но мы видим, что вы еще ни разу не подключались.\n\n"
                    "Всё ли хорошо? Получилось ли у вас настроить VPN?\n"
                    "Напишите нам, если нужна помощь!"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💬 Поддержка", callback_data="support_start")],
                    [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")]
                ])
                await bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
            
            # Отмечаем, что проверили (или отправили)
            db.mark_notified_zero_traffic(user_id)
        except Exception as e:
            logger.error(f"Ошибка в check_zero_traffic_after_24h для {user_id}: {e}")


async def check_yookassa_invoices():
    """Фоновая задача: проверка статуса платежей ЮKassa"""
    if not yookassa_enabled:
        return

    logger.info("Запуск проверки платежей ЮKassa...")
    try:
        from yookassa import Payment as YKPayment
        # Получаем последние succeeded-платежи (limit 100)
        # Добавляем таймаут 20 сек, чтобы задача не зависала
        payments_list = await asyncio.wait_for(
            asyncio.to_thread(YKPayment.list, {"status": "succeeded", "limit": 100}),
            timeout=20
        )

        if not payments_list or not hasattr(payments_list, 'items'):
             logger.info("Платежи ЮKassa не найдены.")
             return

        for payment in payments_list.items:
            payment_id = payment.id

            if db.is_payment_processed(payment_id):
                continue

            metadata = payment.metadata or {}
            label = metadata.get("label", "")

            if not label or not label.startswith("vpn_"):
                continue

            parts = label.split("_")
            is_gift = False
            giver_username = None

            if parts[1] == "gift" and len(parts) == 5:
                user_id = int(parts[2])
                days = int(parts[3])
                buyer_id = int(parts[4])
                is_gift = True
                giver_username = db.get_username(buyer_id) or f"ID:{buyer_id}"
                try:
                    await bot.send_message(buyer_id, f"🎁 Вы успешно подарили подписку на {days} дней пользователю <code>{user_id}</code>!", parse_mode="HTML")
                except:
                    pass
            elif len(parts) == 3:
                user_id = int(parts[1])
                days = int(parts[2])
            else:
                continue

            logger.info(f"Найден оплаченный платеж ЮKassa {payment_id}: {payment.amount.value} руб от {user_id}")
            db.mark_payment_processed(payment_id)

            # Выдаем подписку
            db.add_subscription(user_id, days)

            # Сохраняем метод оплаты для автопродления (только для обычного платежа, не подарка)
            if not is_gift:
                try:
                    pm = getattr(payment, 'payment_method', None)
                    if pm and getattr(pm, 'saved', False) and getattr(pm, 'id', None):
                        db.save_payment_method(user_id, pm.id, days)
                        logger.info(f"Сохранён payment_method_id для {user_id}: {pm.id}")
                except Exception as e:
                    logger.warning(f"Не удалось сохранить payment_method для {user_id}: {e}")

            # Рефералка
            referrer_id = db.get_referrer(user_id)
            if referrer_id:
                db.add_subscription(referrer_id, 3)
                try:
                    await bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎁 <b>Бонус!</b>\n\nВаш реферал оплатил подписку. Вам начислено <b>+3 бонусных дня</b>!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

            # Ключ
            user_data = db.get_user(user_id)
            client_uuid = user_data[2] if user_data else None
            if not client_uuid:
                new_uuid = await panel_api.add_client(user_id)
                if new_uuid:
                    db.update_uuid(user_id, new_uuid)

            await send_success_message(user_id, days, is_gift=is_gift, giver_username=giver_username)
        logger.info("Проверка платежей ЮKassa завершена.")
    except asyncio.TimeoutError:
        logger.error("Таймаут при запросе к API ЮKassa в check_yookassa_invoices")
    except Exception as e:
        logger.error(f"Ошибка при проверке платежей ЮKassa: {e}")



async def auto_renew_subscriptions():
    """Фоновая задача: автоматическое продление подписок через ЮKassa."""
    if not yookassa_enabled:
        return

    logger.info("Запуск задачи автопродления подписок...")
    users = db.get_users_for_auto_renew()
    if not users:
        logger.info("Нет пользователей для автопродления.")
        return

    from yookassa import Payment as YKPayment
    import uuid as _uuid

    for user_id, method_id, days, sub_end_str in users:
        if not days:
            continue
        prices_map = {30: 99, 90: 249, 365: 899}
        price = prices_map.get(days, 99)
        label = f"vpn_{user_id}_{days}"

        try:
            # ЮKassa синхронная, запускаем в потоке с таймаутом
            payment = await asyncio.wait_for(
                asyncio.to_thread(YKPayment.create, {
                    "amount": {"value": f"{price}.00", "currency": "RUB"},
                    "capture": True,
                    "payment_method_id": method_id,
                    "description": f"Автопродление GuepardVPN на {days} дней",
                    "metadata": {"label": label}
                }, str(_uuid.uuid4())),
                timeout=20
            )

            if payment.status == "succeeded":
                logger.info(f"Автопродление {user_id}: успешно списано {price} руб")
                db.mark_payment_processed(payment.id)
                db.add_subscription(user_id, days)

                # Обновляем ключ в панели
                user_data = db.get_user(user_id)
                if user_data:
                    balance, sub_end, client_uuid = user_data
                    if sub_end and client_uuid:
                        dt = datetime.strptime(sub_end, "%Y-%m-%d %H:%M:%S")
                        expiry_ms = int(dt.timestamp() * 1000)
                        await panel_api.update_client_expiry(client_uuid, user_id, expiry_ms)

                from aiogram.types import FSInputFile
                import os
                
                success_text = (
                    f"✅ <b>Подписка успешно продлена!</b>\n\n"
                    f"С вашей карты списано <b>{price} руб</b>.\n"
                    f"Подписка продлена на <b>{days} дней</b>.\n\n"
                    f"Чтобы отключить автопродление, перейдите в 👤 Личный кабинет."
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")]
                ])
                
                image_path = "Подписка куплена.png"
                try:
                    if os.path.exists(image_path):
                        photo = FSInputFile(image_path)
                        await bot.send_photo(chat_id=user_id, photo=photo, caption=success_text, reply_markup=kb, parse_mode="HTML")
                    else:
                        await bot.send_message(chat_id=user_id, text=success_text, reply_markup=kb, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление об автопродлении {user_id}: {e}")

            elif payment.status == "canceled":
                logger.warning(f"Автопродление {user_id}: платёж отклонён")
                db.set_auto_renew(user_id, False)
                from aiogram.types import FSInputFile
                import os
                
                fail_text = (
                    "⚠️ <b>Не удалось продлить подписку автоматически.</b>\n\n"
                    "Банк отклонил списание с карты. Автопродление отключено.\n\n"
                    "Пожалуйста, оплатите подписку вручную, чтобы не потерять доступ к VPN."
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Оплатить вручную", callback_data="menu_tariffs")]
                ])
                
                image_path = "Оплата не прошла.png"
                try:
                    if os.path.exists(image_path):
                        photo = FSInputFile(image_path)
                        await bot.send_photo(chat_id=user_id, photo=photo, caption=fail_text, reply_markup=kb, parse_mode="HTML")
                    else:
                        await bot.send_message(chat_id=user_id, text=fail_text, reply_markup=kb, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление об ошибке автопродления {user_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка автопродления для {user_id}: {e}")


async def check_cryptopay_invoices():
    """Фоновая задача: проверка статуса счетов CryptoBot"""
    if not cryptopay:
        return
        
    try:
        # Получаем все инвойсы со статусом 'paid'
        # Добавляем таймаут, чтобы избежать зависания задачи
        invoices = await asyncio.wait_for(cryptopay.get_invoices(status="paid"), timeout=15)
        if not invoices:
            return
        for inv in invoices:
            if inv.payload and inv.payload.startswith("vpn_"):
                parts = inv.payload.split("_")
                
                is_gift = False
                giver_username = None
                
                if parts[1] == "gift" and len(parts) == 5:
                    user_id = int(parts[2])
                    days = int(parts[3])
                    buyer_id = int(parts[4])
                    is_gift = True
                    giver_username = db.get_username(buyer_id) or f"ID:{buyer_id}"
                    try:
                        await bot.send_message(buyer_id, f"🎁 Вы успешно подарили подписку на {days} дней пользователю <code>{user_id}</code>!", parse_mode="HTML")
                    except:
                        pass
                elif len(parts) == 3:
                    user_id = int(parts[1])
                    days = int(parts[2])
                else:
                    continue
                    
                amount = inv.amount
                logger.info(f"Найден оплаченный счет CryptoBot {inv.invoice_id} на {amount} {inv.asset}")
                    
                # Удаляем инвойс чтобы не обрабатывать дважды
                try:
                    await cryptopay.delete_invoice(invoice_id=inv.invoice_id)
                except Exception as e:
                    logger.error(f"Ошибка удаления инвойса: {e}")
                    
                # Выдаем подписку
                db.add_subscription(user_id, days)

                # Рефералка
                referrer_id = db.get_referrer(user_id)
                if referrer_id:
                    db.add_subscription(referrer_id, 3)
                    try:
                        await bot.send_message(
                            chat_id=referrer_id,
                            text=f"🎁 <b>Бонус!</b>\n\nВаш реферал оплатил криптовалютой. Вам начислено <b>+3 бонусных дня</b>!",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                
                # Ключ
                user_data = db.get_user(user_id)
                client_uuid = user_data[2] if user_data else None
                if not client_uuid:
                    new_uuid = await panel_api.add_client(user_id)
                    if new_uuid:
                        db.update_uuid(user_id, new_uuid)

                await send_success_message(user_id, days, is_gift=is_gift, giver_username=giver_username)
                        
    except Exception as e:
        logger.error(f"Ошибка при проверке инвойсов CryptoPay: {e}")


async def main():
    global cryptopay
    if CRYPTOPAY_TOKEN:
        cryptopay = AioCryptoPay(token=CRYPTOPAY_TOKEN, network=Networks.MAIN_NET)

    logger.info("Бот запущен...")
    
    WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com")
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="GuepardVPN", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except Exception as e:
        logger.error(f"Не удалось установить кнопку меню WebApp: {e}")

    # Настройка и запуск планировщика задач
    scheduler = AsyncIOScheduler(timezone='Europe/Moscow')
    scheduler.add_job(check_expired_subscriptions, trigger='interval', hours=1)
    scheduler.add_job(check_expiring_soon, trigger='interval', hours=1)
    scheduler.add_job(check_expired_24h_ago, trigger='interval', hours=1)
    scheduler.add_job(check_cryptopay_invoices, trigger='interval', seconds=30)
    scheduler.add_job(check_yookassa_invoices, trigger='interval', seconds=15)
    scheduler.add_job(auto_renew_subscriptions, trigger='interval', hours=1)
    scheduler.add_job(check_zero_traffic_after_24h, trigger='interval', hours=1)
    scheduler.start()

    # Start proxy server concurrently without blocking
    asyncio.create_task(run_proxy_server())

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Критическая ошибка при работе бота: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную.")