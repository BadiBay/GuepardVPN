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
import marzban_api as panel_api

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOPAY_TOKEN = os.getenv("CRYPTOPAY_TOKEN")
admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip().isdigit()]

# Инициализация CryptoPay
cryptopay = None

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_days = State()
    waiting_for_broadcast_msg = State()
    waiting_for_search_id = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()

class AdminSupportStates(StatesGroup):
    replying_to_user = State()

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


from urllib.parse import urlencode

def get_main_menu_kb(user_id: int | None = None):
    WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com").rstrip('/')
    
    if user_id:
        user_data = db.get_user(user_id)
        if user_data:
            balance, sub_end, client_uuid = user_data
            sub_link = client_uuid if client_uuid else 'None'
            if sub_link != 'None' and sub_link.startswith("/"):
                sub_link = f"{panel_api.PANEL_URL}{sub_link}"
            end_str = str(sub_end) if sub_end else 'None'
            
            params = {'tid': user_id, 'bal': balance, 'end': end_str, 'uuid': sub_link}
            qs = urlencode(params)
            final_url = f"{WEBAPP_URL}/?{qs}"
        else:
            final_url = f"{WEBAPP_URL}/"
    else:
        final_url = f"{WEBAPP_URL}/"

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


def get_profile_kb(callback=CallbackQuery):
    global sub_link
    user_data = db.get_user(callback.from_user.id)
    sub_end_date_str = user_data[1] if user_data else None
    client_uuid = user_data[2] if user_data else None

    if sub_end_date_str:
        sub_end_date = datetime.strptime(sub_end_date_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        if sub_end_date > now:
            if client_uuid:
                sub_link = client_uuid
                if sub_link.startswith("/"):
                    sub_link = f"{panel_api.PANEL_URL}{sub_link}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить подписку", url=f"{sub_link}")],
        [InlineKeyboardButton(text="⚙️ Устройства", callback_data="menu_devices")],
        [InlineKeyboardButton(text="💳 Оплатить VPN", callback_data="menu_tariffs")],
        [InlineKeyboardButton(text="🤝 Партнерская программа", callback_data="menu_referral")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

def get_admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin_give_sub")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin_search_user")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 Выход", callback_data="menu_main")]
    ])

def get_devices_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Сбросить сессии", callback_data="action_reset_ips")],
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


def get_tariffs_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Бесплатный тест (24 часа)", callback_data="test_24h")],
        [InlineKeyboardButton(text="1 месяц", callback_data="select_tariff_30")],
        [InlineKeyboardButton(text="🔥 3 месяца (Выгода 11%)", callback_data="select_tariff_90")],
        [InlineKeyboardButton(text="12 месяцев (Выгода 27%)", callback_data="select_tariff_365")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

def get_payment_method_kb(days: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Карта России (Ожидается)", callback_data="stub_card")],
        [InlineKeyboardButton(text="⭐️ Telegram Stars", callback_data=f"buy_stars_{days}")],
        [InlineKeyboardButton(text="🪙 Криптовалюта", callback_data=f"buy_crypto_{days}")],
        [InlineKeyboardButton(text="🔙 Назад к тарифами", callback_data="menu_tariffs")]
    ])


def get_docs_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Политика обработки данных ↗️", url="https://example.com/policy")],
        [InlineKeyboardButton(text="Публичная оферта ↗️", url="https://example.com/oferta")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="stub_faq")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])


@dp.message(Command("start"))
async def cmd_start(message: Message):
    referrer_id = None
    if message.text:
        args = message.text.split()
        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                ref_id = int(args[1].split("_")[1])
                if ref_id != message.from_user.id:
                    referrer_id = ref_id
            except ValueError:
                pass

    db.add_user(message.from_user.id, message.from_user.username, referrer_id)

    text = (
        "👋 Добро пожаловать в GuepardVPN🐆!\n\n"
        "Ваш трафик зашифрован, личные данные в безопасности. "
        "Подключайтесь в один клик и пользуйтесь интернетом уверенно."
    )
    # Видео можно отправить по file_id, URL или загрузить файл с диска (FSInputFile)
    video_url = "BAACAgIAAxkBAAOOabr0LEVa_aU-Xbvnxs7oENdDelcAAjSXAALNodlJkD-ePULV8Fw6BA"

    await message.answer_video(
        video=video_url,
        caption=text,
        reply_markup=get_main_menu_kb(message.from_user.id)
    )
# --- АДМИН ПАНЕЛЬ ---

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return  # Игнорируем, если пишет не админ

    text = "🔒 <b>Панель администратора</b>\n\nВыберите нужное действие:"
    await message.answer(text, reply_markup=get_admin_kb(), parse_mode="HTML")


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

    await callback.message.answer("Введите Telegram ID пользователя, которому нужно выдать подписку:")
    await state.set_state(AdminStates.waiting_for_user_id)
    await callback.answer()


@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id_input(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Ошибка. ID должен состоять только из цифр. Введите ID еще раз:")
        return

    await state.update_data(target_id=int(message.text))
    await message.answer("Сколько дней подписки выдать?")
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
            await bot.send_message(
                chat_id=target_id,
                text=f"🎁 Администратор выдал вам {days} дней подписки! Перейдите в Личный кабинет."
            )
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

    await callback.message.answer("📝 Введите текст для массовой рассылки всем пользователям бота (поддерживается HTML разметка):")
    await state.set_state(AdminStates.waiting_for_broadcast_msg)
    await callback.answer()


@dp.message(AdminStates.waiting_for_broadcast_msg)
async def process_broadcast_message(message: Message, state: FSMContext):
    await state.clear()
    users = db.get_all_users()
    success = 0
    fail = 0
    
    await message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей...")
    
    for user_id in users:
        try:
            await bot.send_message(user_id, message.html_text, parse_mode="HTML")
            success += 1
        except Exception:
            fail += 1
            
        await asyncio.sleep(0.05) # Небольшая пауза для избежания FloodWait
        
    await message.answer(f"✅ Рассылка завершена!\n\nУспешно: {success}\nОшибок: {fail}")


# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ---
@dp.callback_query(F.data == "admin_search_user")
async def process_admin_search_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await callback.message.answer("🔍 Введите Telegram ID пользователя, которого хотите найти:")
    await state.set_state(AdminStates.waiting_for_search_id)
    await callback.answer()


@dp.message(AdminStates.waiting_for_search_id)
async def process_search_user_input(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Ошибка. ID должен состоять только из цифр. Введите ID еще раз:")
        return

    target_id = int(message.text)
    user_data = db.get_user(target_id)
    
    if not user_data:
        await message.answer("❌ Пользователь с таким ID не найден в базе.")
        await state.clear()
        return

    balance, sub_end, client_uuid = user_data
    
    text = (
        f"👤 <b>Профиль пользователя {target_id}</b>\n\n"
        f"Баланс: {balance} ₽\n"
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
    text = "Вы в главном меню. Выберите нужное действие:"
    await safe_edit(callback.message, text, reply_markup=get_main_menu_kb(callback.from_user.id))
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


@dp.callback_query(F.data == "menu_tariffs")
async def process_tariffs(callback: CallbackQuery):
    text = "💎 <b>Выберите подходящий тариф:</b>\n\nЧем дольше период, тем выгоднее цена."
    await safe_edit(callback.message, text, reply_markup=get_tariffs_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "menu_docs")
async def process_docs(callback: CallbackQuery):
    text = "На этой странице вы можете ознакомиться с документами сервиса:"
    await safe_edit(callback.message, text, reply_markup=get_docs_kb())
    await callback.answer()


@dp.callback_query(F.data == "stub_os_ios")
async def process_install_os(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами из вашего магазина приложений App Store.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stub_os_android")
async def process_install_os(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами из вашего магазина приложений Google Play.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stub_os_windows")
async def process_install_os(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]])
    await safe_edit(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stub_os_macos")
async def process_install_os(callback: CallbackQuery):
    text = (
        "📲 <b>Инструкция по установке:</b>\n\n"
        "1. Скачайте приложение <b>V2Ray Tun</b>, <b>Hiddify</b>, <b>Happ</b> или любое другое приложение для работы с прокси-серверами из AppStore или браузера.\n"
        "2. Скопируйте ваш уникальный ключ (ссылку на подписку) из Личного кабинета.\n"
        "3. Откройте скачанное приложение и нажмите <b>+</b> (Добавить конфигурацию).\n"
        "4. Выберите «Импорт из буфера обмена».\n"
        "5. Нажмите кнопку <b>Подключить</b>!\n\n"
        "<i>Если VPN не работает, попробуйте сбросить сессии в Личном кабинете -> Устройства.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="menu_install")]])
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
            
    await callback.message.edit_text(
        "🎁 <b>Вам выдан бесплатный тестовый период на 24 часа!</b>\n\n"
        "Перейдите в Личный кабинет, чтобы получить ключ.",
        reply_markup=get_main_menu_kb(telegram_id), parse_mode="HTML"
    )
    await callback.answer()

# Обработка выбора тарифа
@dp.callback_query(F.data.startswith("select_tariff_"))
async def process_select_tariff(callback: CallbackQuery):
    days = int(callback.data.split("_")[2])
    
    prices = {
        30: 299,
        90: 690,
        365: 2190
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


# Обработка нажатия на тариф Telegram Stars
@dp.callback_query(F.data.startswith("buy_stars_"))
async def process_buy_stars(callback: CallbackQuery):
    days = int(callback.data.split("_")[2])

    # Цены в Звездах (XTR)
    prices_map = {
        30: 150,
        90: 400,
        365: 1300
    }

    price = prices_map.get(days, 150)

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=f"Подписка GuepardVPN на {days} дней",
        description="Безлимитный трафик, высокая скорость и надежная защита ваших данных.",
        payload=f"vpn_sub_{days}",
        provider_token="",  # Для Telegram Stars токен провайдера должен быть пустым
        currency="XTR",  # Код валюты Telegram Stars
        prices=[LabeledPrice(label="VPN Подписка", amount=price)]
    )
    await callback.answer()


# Обработка нажатия на тариф CryptoPay
@dp.callback_query(F.data.startswith("buy_crypto_"))
async def process_buy_crypto(callback: CallbackQuery):
    if not cryptopay:
        await callback.answer("Оплата криптовалютой временно недоступна.", show_alert=True)
        return

    days = int(callback.data.split("_")[2])
    prices = {
        30: 299,
        90: 690,
        365: 2190
    }
    price_rub = prices.get(days, 0)
    
    if not price_rub:
        await callback.answer("Ошибка тарифа.", show_alert=True)
        return
        
    try:
        # Для упрощения 100 рублей = 1 USDT
        price_usdt = round(price_rub / 100.0, 2)
        
        invoice = await cryptopay.create_invoice(
            asset='USDT', 
            amount=price_usdt, 
            description=f"Подписка GuepardVPN на {days} дней",
            payload=f"vpn_{callback.from_user.id}_{days}"
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
    user_data = db.get_user(callback.from_user.id)
    balance = user_data[0] if user_data else 0
    sub_end_date_str = user_data[1] if user_data else None
    client_uuid = user_data[2] if user_data else None

    status_text = "❌ Неактивна"
    days_left = 0
    key_text = "<i>(появится после оплаты)</i>"
    used_gb = 0.0

    if sub_end_date_str:
        sub_end_date = datetime.strptime(sub_end_date_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        if sub_end_date > now:
            status_text = "✅ Активна"
            days_left = (sub_end_date - now).days
            used_gb = await panel_api.get_client_stats(callback.from_user.id)

            if client_uuid:
                sub_link = client_uuid
                if sub_link.startswith("/"):
                    sub_link = f"{panel_api.PANEL_URL}{sub_link}"
                key_text = f"<code>{sub_link}</code>\n<i>* Нажмите, чтобы скопировать</i>"

    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"ID: <code>{callback.from_user.id}</code>\n"
        f"Баланс: {balance} ₽\n\n"
        f"Статус подписки: {status_text}\n"
        f"Осталось дней: {days_left}\n"
        f"Использовано трафика: {used_gb} ГБ\n\n"
        f"Ваш ключ (ссылка на подписку):\n{key_text}"
    )

    await safe_edit(callback.message, text, reply_markup=get_profile_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "menu_devices")
async def process_devices(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    text = (
        "📱 <b>Управление устройствами</b>\n\n"
        "Вы можете использовать VPN на нескольких устройствах.\n\n"
        "Если VPN перестал подключаться (например, при смене Wi-Fi на мобильный интернет), "
        "вероятно, ваша сессия зависла.\n\n"
        "Нажмите кнопку ниже, чтобы сбросить сессии (ваша ссылка на подписку обновится)."
    )
    await safe_edit(callback.message, text, reply_markup=get_devices_kb(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "action_reset_ips")
async def process_reset_ips(callback: CallbackQuery):
    telegram_id = callback.from_user.id

    new_sub_url = await panel_api.clear_client_ips(telegram_id)

    if new_sub_url:
        db.update_uuid(telegram_id, new_sub_url)
        await callback.message.edit_text("✅ Сессии сброшены! Перейдите в кабинет, чтобы скопировать новую ссылку.", reply_markup=get_devices_kb())
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

    text = (
        f"🎉 <b>Оплата успешно прошла!</b>\n\n"
        f"Вам начислено {days} дней подписки.\n"
        f"Перейдите в Личный кабинет, чтобы скопировать ваш ключ."
    )
    await message.answer(text, reply_markup=get_main_menu_kb(), parse_mode="HTML")


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
                        "Доступ к VPN приостановлен. Пополните баланс и продлите тариф в Личном кабинете, "
                        "чтобы снова пользоваться сервисом."
                    )
                    await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление {telegram_id}: {e}")

async def check_cryptopay_invoices():
    """Фоновая задача: проверка статуса счетов CryptoBot"""
    if not cryptopay:
        return
        
    try:
        # Получаем все инвойсы со статусом 'paid'
        invoices = await cryptopay.get_invoices(status="paid")
        if not invoices:
            return
        for inv in invoices:
            if inv.payload and inv.payload.startswith("vpn_"):
                parts = inv.payload.split("_")
                if len(parts) == 3:
                    user_id = int(parts[1])
                    days = int(parts[2])
                    
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

                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=f"✅ <b>Оплата через CryptoBot успешно получена!</b>\n\nВам начислено <b>{days} дней</b> подписки.\nПерейдите в Личный кабинет за подробностями.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление об оплате CryptoBot пользователю {user_id}: {e}")
                        
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
    # Для теста можно поставить проверку каждую минуту: trigger='interval', minutes=1
    scheduler.add_job(check_expired_subscriptions, trigger='interval', hours=1)
    scheduler.add_job(check_cryptopay_invoices, trigger='interval', seconds=15)
    scheduler.start()

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Критическая ошибка при работе бота: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную.")