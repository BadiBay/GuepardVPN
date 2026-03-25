import re

with open(r'c:\VPN\bot.py', 'r', encoding='utf-8') as f:
    code = f.read()

# 1. States
code = code.replace(
    "class AdminSupportStates(StatesGroup):\n    replying_to_user = State()",
    "class AdminSupportStates(StatesGroup):\n    replying_to_user = State()\n\nclass GiftStates(StatesGroup):\n    waiting_for_recipient = State()"
)

# 2. get_profile_kb
code = code.replace(
    '[InlineKeyboardButton(text="💳 Оплатить VPN", callback_data="menu_tariffs")],',
    '[InlineKeyboardButton(text="💳 Оплатить VPN", callback_data="menu_tariffs")],\n        [InlineKeyboardButton(text="🎁 Подарить подписку", callback_data="gift_sub_start")],'
)

# 3. process_tariffs
code = code.replace(
    "async def process_tariffs(callback: CallbackQuery):\n    text = \"💎 <b>Выберите подходящий",
    "async def process_tariffs(callback: CallbackQuery, state: FSMContext):\n    await state.update_data(gift_recipient_id=None)\n    text = \"💎 <b>Выберите подходящий"
)

new_handlers = """
@dp.callback_query(F.data == "gift_sub_start")
async def process_gift_sub_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎁 <b>Подарить подписку</b>\\n\\nВведите Telegram ID пользователя или его @username:")
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
        found_id = db.get_user_by_username(username)
        if found_id:
            target_id = found_id

    if not target_id or not db.get_user(target_id):
        await message.answer("❌ Пользователь не найден в базе данных. Проверьте данные и попробуйте еще раз:")
        return

    await state.update_data(gift_recipient_id=target_id)
    text = f"🎁 Вы собираетесь купить подписку в подарок для пользователя (ID: {target_id}).\\n\\n💎 <b>Выберите подходящий тариф:</b>"
    await message.answer(text, reply_markup=get_tariffs_kb(target_id), parse_mode="HTML")
    await state.set_state(None)

# Обработка выбора тарифа
"""
code = code.replace("# Обработка выбора тарифа\n", new_handlers)

# 4. process_buy_yoomoney
code = code.replace(
    "async def process_buy_yoomoney(callback: CallbackQuery):\n    if not YOOMONEY_TOKEN",
    "async def process_buy_yoomoney(callback: CallbackQuery, state: FSMContext):\n    if not YOOMONEY_TOKEN"
)
code = code.replace(
    'label = f"vpn_{callback.from_user.id}_{days}"\n    \n    # Generate Quickpay URL',
    'data = await state.get_data()\n    recipient_id = data.get("gift_recipient_id")\n    if recipient_id:\n        label = f"vpn_gift_{recipient_id}_{days}_{callback.from_user.id}"\n    else:\n        label = f"vpn_{callback.from_user.id}_{days}"\n    \n    # Generate Quickpay URL'
)

# 5. process_buy_stars
code = code.replace(
    "async def process_buy_stars(callback: CallbackQuery):\n    days = int",
    "async def process_buy_stars(callback: CallbackQuery, state: FSMContext):\n    days = int"
)
code = code.replace(
    'payload=f"vpn_sub_{days}",\n        provider_token="",',
    'payload=f"vpn_gift_{recipient_id}_{days}_{callback.from_user.id}" if recipient_id else f"vpn_sub_{days}",\n        provider_token="",'
)
code = code.replace(
    "price = prices_map.get(days, 55)",
    "price = prices_map.get(days, 55)\n\n    data = await state.get_data()\n    recipient_id = data.get(\"gift_recipient_id\")"
)

# 6. process_buy_crypto
code = code.replace(
    "async def process_buy_crypto(callback: CallbackQuery):\n    if not cryptopay:",
    "async def process_buy_crypto(callback: CallbackQuery, state: FSMContext):\n    if not cryptopay:"
)
code = code.replace(
    'payload=f"vpn_{callback.from_user.id}_{days}"\n        )',
    'payload=f"vpn_gift_{recipient_id}_{days}_{callback.from_user.id}" if recipient_id else f"vpn_{callback.from_user.id}_{days}"\n        )'
)
code = code.replace(
    "price_usdt = round(price_rub / 80.0, 2)",
    "price_usdt = round(price_rub / 80.0, 2)\n        \n        data = await state.get_data()\n        recipient_id = data.get(\"gift_recipient_id\")"
)

# 7. process_successful_payment
succ_pay_old = """    payload = message.successful_payment.invoice_payload
    days = int(payload.split("_")[2])

    telegram_id = message.from_user.id"""
succ_pay_new = """    payload = message.successful_payment.invoice_payload
    is_gift = False
    giver_username = None
    if payload.startswith("vpn_gift_"):
        parts = payload.split("_")
        telegram_id = int(parts[2])
        days = int(parts[3])
        giver_id = int(parts[4])
        is_gift = True
        g_name = db.get_username(giver_id)
        giver_username = f"@{g_name}" if g_name else f"ID:{giver_id}"
        try:
            await bot.send_message(giver_id, f"✅ Оплата прошла успешно! Вы подарили подписку на {days} дней пользователю {telegram_id}.")
        except: pass
    else:
        days = int(payload.split("_")[2])
        telegram_id = message.from_user.id"""
code = code.replace(succ_pay_old, succ_pay_new)

code = code.replace("await send_success_message(telegram_id, days)\n\n\n# Новая функция", "await send_success_message(telegram_id, days, is_gift=is_gift, giver_username=giver_username)\n\n\n# Новая функция")

# 8. check_yoomoney_invoices & check_cryptopay_invoices (use regex)
def repl_yoo(m):
    return """            if label and label.startswith("vpn_"):
                parts = label.split("_")
                is_gift = False
                giver_username = None
                
                if parts[1] == "gift" and len(parts) >= 5:
                    user_id = int(parts[2])
                    days = int(parts[3])
                    giver_id = int(parts[4])
                    is_gift = True
                    g_name = db.get_username(giver_id)
                    giver_username = f"@{g_name}" if g_name else f"ID:{giver_id}"
                    try:
                        await bot.send_message(giver_id, f"✅ Оплата прошла успешно! Вы подарили подписку на {days} дней пользователю {user_id}.")
                    except: pass
                elif len(parts) == 3:
                    user_id = int(parts[1])
                    days = int(parts[2])
                else:
                    continue"""

code = re.sub(
    r'            if label and label\.startswith\("vpn_"\):\n                parts = label\.split\("_"\)\n                if len\(parts\) == 3:\n                    user_id = int\(parts\[1\]\)\n                    days = int\(parts\[2\]\)',
    repl_yoo, code
)

# And for Cryptopay it's 'inv.payload' instead of 'label'
def repl_crypto(m):
    return """            if inv.payload and inv.payload.startswith("vpn_"):
                parts = inv.payload.split("_")
                is_gift = False
                giver_username = None
                
                if parts[1] == "gift" and len(parts) >= 5:
                    user_id = int(parts[2])
                    days = int(parts[3])
                    giver_id = int(parts[4])
                    is_gift = True
                    g_name = db.get_username(giver_id)
                    giver_username = f"@{g_name}" if g_name else f"ID:{giver_id}"
                    try:
                        await bot.send_message(giver_id, f"✅ Оплата прошла успешно! Вы подарили подписку на {days} дней пользователю {user_id}.")
                    except: pass
                elif len(parts) == 3:
                    user_id = int(parts[1])
                    days = int(parts[2])
                else:
                    continue"""

code = re.sub(
    r'            if inv.payload and inv\.payload\.startswith\("vpn_"\):\n                parts = inv\.payload\.split\("_"\)\n                if len\(parts\) == 3:\n                    user_id = int\(parts\[1\]\)\n                    days = int\(parts\[2\]\)',
    repl_crypto, code
)

code = code.replace("await send_success_message(user_id, days)", "await send_success_message(user_id, days, is_gift=is_gift, giver_username=giver_username)")

with open(r'c:\VPN\bot.py', 'w', encoding='utf-8') as f:
    f.write(code)

print("Patch applied")
