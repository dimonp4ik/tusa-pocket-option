# ============================================================
# TELEGRAM-БОТ: сетапы для Pocket Option по нажатию кнопки
# ============================================================

import os
import time
import threading
import traceback

import requests
from flask import Flask

import config
import signals

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = "https://api.telegram.org/bot" + BOT_TOKEN

app = Flask(__name__)

# Когда пользователь последний раз жал кнопку (защита от спама)
last_request = {}


@app.route("/")
def health():
    return "OK"


# ---------- Отправка в Telegram ----------

def tg(method, payload):
    try:
        r = requests.post(API + "/" + method, json=payload, timeout=15)
        return r.json()
    except Exception:
        traceback.print_exc()
        return None


def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("sendMessage", payload)


def answer_callback(callback_id, text=""):
    tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


# ---------- Клавиатура с парами ----------

def build_keyboard():
    rows = []
    forex = list(config.FOREX_PAIRS.items())
    crypto = list(config.CRYPTO_PAIRS.items())

    rows.append([{"text": "💱 ВАЛЮТНЫЕ ПАРЫ", "callback_data": "noop"}])
    for i in range(0, len(forex), 2):
        row = []
        for sym, name in forex[i:i + 2]:
            row.append({"text": name, "callback_data": "go|yahoo|" + sym})
        rows.append(row)

    rows.append([{"text": "🪙 МОНЕТЫ", "callback_data": "noop"}])
    for i in range(0, len(crypto), 2):
        row = []
        for sym, name in crypto[i:i + 2]:
            row.append({"text": name, "callback_data": "go|binance|" + sym})
        rows.append(row)

    return rows


WELCOME = (
    "👋 <b>Бот сетапов для Pocket Option</b>\n\n"
    "Нажми на пару — я мгновенно проанализирую график "
    "и кину сетап: куда ставить и на сколько минут.\n\n"
    "⚠️ <b>Важно:</b> ставь только те пары, у которых в Pocket Option "
    "выплата <b>+85% и выше</b>. Если выплата ниже — выбери другую пару.\n\n"
    "Выбирай пару: 👇"
)


# ---------- Формирование ответа ----------

def pair_name(source, symbol):
    if source == "binance":
        return config.CRYPTO_PAIRS.get(symbol, symbol)
    return config.FOREX_PAIRS.get(symbol, symbol)


def format_setup(name, res):
    if res["direction"] == "UP":
        arrow, word = "📈", "ВВЕРХ (выше)"
    else:
        arrow, word = "📉", "ВНИЗ (ниже)"

    if res["good"]:
        head = f"🎯 <b>СЕТАП: {name}</b>\n\n"
        head += f"{arrow} <b>Ставка: {word}</b>\n"
        head += f"⏱ Экспирация: <b>{config.EXPIRY_MINUTES} минут</b>\n"
        head += f"💪 Сила сетапа: <b>{res['score']}/{res['max_score']}</b>\n\n"
        head += "📋 Почему:\n"
        for r in res["reasons"]:
            head += "• " + r + "\n"
        head += "\n⚠️ Ставь сразу — сетап живёт пару минут.\n"
        head += "Только если выплата ≥ 85%!"
        return head

    head = f"😴 <b>{name}</b> — чёткого сетапа сейчас нет "
    head += f"(сила {res['score']}/{res['max_score']}, нужно {config.MIN_SCORE}+).\n\n"
    head += "Лучше не ставить. Попробуй другую пару или зайди через пару минут."
    return head


def handle_pair(chat_id, source, symbol):
    name = pair_name(source, symbol)
    try:
        res = signals.analyze(symbol, source)
        send_message(chat_id, format_setup(name, res), build_keyboard())
    except Exception:
        traceback.print_exc()
        send_message(
            chat_id,
            f"⚠️ Не получилось загрузить данные по {name}. "
            "Попробуй ещё раз через минуту.",
            build_keyboard(),
        )


# ---------- Обработка сообщений ----------

def process_update(upd):
    # Нажатие кнопки
    if "callback_query" in upd:
        cq = upd["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        user_id = cq["from"]["id"]
        data = cq.get("data", "")

        if data == "noop":
            answer_callback(cq["id"])
            return

        now = time.time()
        if now - last_request.get(user_id, 0) < config.USER_COOLDOWN_SECONDS:
            answer_callback(cq["id"], "Подожди пару секунд…")
            return
        last_request[user_id] = now

        if data.startswith("go|"):
            _, source, symbol = data.split("|", 2)
            answer_callback(cq["id"], "Анализирую…")
            handle_pair(chat_id, source, symbol)
        return

    # Обычное сообщение / команда
    if "message" in upd:
        msg = upd["message"]
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if text.startswith("/start") or text.startswith("/help"):
            send_message(chat_id, WELCOME, build_keyboard())
        elif text:
            send_message(chat_id, "Жми кнопку с парой 👇", build_keyboard())


def polling_loop():
    offset = 0
    print("Бот запущен, слушаю Telegram…")
    while True:
        try:
            r = requests.get(
                API + "/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40,
            )
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                try:
                    process_update(upd)
                except Exception:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ОШИБКА: не задан TELEGRAM_BOT_TOKEN")
        raise SystemExit(1)

    t = threading.Thread(target=polling_loop, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
