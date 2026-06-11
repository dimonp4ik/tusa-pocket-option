# ============================================================
# TUSA TRADE — бот сетапов для Pocket Option
# Жми кнопку — бот сканирует все монеты и кидает лучший сетап.
# Вход на новой минуте, экспирация 1 минута.
# ============================================================

import os
import time
import threading
import traceback
from datetime import datetime, timezone

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


# ---------- Клавиатура ----------

def build_keyboard():
    return [
        [
            {"text": "🪙 СЕТАП — КРИПТА", "callback_data": "scan"},
            {"text": "💱 СЕТАП — ВАЛЮТА", "callback_data": "scan_forex"},
        ],
        [{"text": "ℹ️ Как пользоваться", "callback_data": "howto"}],
    ]


WELCOME = (
    "👋 <b>TUSA TRADE — сетапы для Pocket Option</b>\n\n"
    "Жми кнопку — я просканирую все пары "
    "и кину лучшую прямо сейчас.\n\n"
    "🪙 <b>КРИПТА</b> — монеты (работает 24/7)\n"
    "💱 <b>ВАЛЮТА</b> — валютные пары (пн-пт)\n\n"
    "⚡ Вход: на открытии <b>новой минуты</b>\n"
    "⏱ Экспирация: <b>1 минута</b>\n\n"
    "⚠️ Ставь только пары с выплатой <b>+85%</b> и только <b>БЕЗ пометки OTC</b>!"
)

HOWTO = (
    "📖 <b>Как пользоваться TUSA TRADE</b>\n\n"
    "1️⃣ Жми <b>🎯 ДАЙ СЕТАП</b>\n"
    "2️⃣ Бот сканирует все монеты и выбирает лучшую\n"
    "3️⃣ Открой эту пару в Pocket Option\n"
    "4️⃣ Проверь: выплата <b>+85% или выше</b>, пара <b>БЕЗ пометки OTC</b>\n"
    "5️⃣ Дождись, пока на часах начнётся <b>новая минута</b> (секунды 00) "
    "— и сразу ставь в указанную сторону\n"
    "6️⃣ Экспирация — <b>1 минута</b>\n\n"
    "❗ Если бот пишет «сетапа нет» — НЕ ставь. Лучше пропустить, чем слить.\n"
    "❗ Сетап живёт 1-2 минуты. Протормозил — запроси новый.\n"
    "❗ Не ставь больше 2-3% от депозита на сделку."
)


# ---------- Формирование сетапа ----------

def format_setup(best):
    if best["direction"] == "UP":
        arrow, word = "📈", "ВВЕРХ (выше)"
    else:
        arrow, word = "📉", "ВНИЗ (ниже)"

    now = datetime.now(timezone.utc)
    sec_left = 60 - now.second

    text = f"🎯 <b>СЕТАП: {best['name']}</b>\n\n"
    text += f"{arrow} <b>Ставка: {word}</b>\n"
    text += f"⏱ Экспирация: <b>{config.EXPIRY_MINUTES} минута</b>\n"
    text += f"💪 Сила: <b>{best['score']}/{best['max_score']}</b>\n\n"
    text += "📋 Почему:\n"
    for r in best["reasons"]:
        text += "• " + r + "\n"
    text += f"\n⚡ <b>Вход: на НОВОЙ минуте (через ~{sec_left} сек)</b>\n"
    text += "Открой пару сейчас и жди смены минуты.\n\n"
    text += "⚠️ Только если выплата ≥ 85% и БЕЗ пометки OTC!"
    return text


def format_no_setup(results, min_score):
    text = "😴 <b>Сейчас чёткого сетапа нет.</b>\n\n"
    if results:
        text += "Лучшее, что есть (но слабовато):\n"
        for r in results[:3]:
            d = "📈" if r["direction"] == "UP" else "📉"
            text += f"• {r['name']} {d} — {r['score']}/{r['max_score']}\n"
        text += f"\nНужно минимум {min_score}. "
    text += "Рынок мутный — лучше подождать. Попробуй через 2-3 минуты."
    return text


def handle_scan(chat_id):
    try:
        best, results = signals.scan_all()
        if best:
            send_message(chat_id, format_setup(best), build_keyboard())
        else:
            send_message(
                chat_id,
                format_no_setup(results, config.MIN_SCORE),
                build_keyboard(),
            )
    except Exception:
        traceback.print_exc()
        send_message(
            chat_id,
            "⚠️ Не получилось загрузить данные. Попробуй ещё раз через минуту.",
            build_keyboard(),
        )


def handle_scan_forex(chat_id):
    try:
        best, results, market_open = signals.scan_forex()
        if best:
            send_message(chat_id, format_setup(best), build_keyboard())
        elif not market_open:
            send_message(
                chat_id,
                "🌙 <b>Валютный рынок закрыт</b> (выходные или праздник).\n"
                "Форекс работает пн-пт. А крипта работает всегда — жми 🪙!",
                build_keyboard(),
            )
        else:
            send_message(
                chat_id,
                format_no_setup(results, config.MIN_SCORE_FOREX),
                build_keyboard(),
            )
    except Exception:
        traceback.print_exc()
        send_message(
            chat_id,
            "⚠️ Не получилось загрузить данные по валюте. "
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

        if data == "howto":
            answer_callback(cq["id"])
            send_message(chat_id, HOWTO, build_keyboard())
            return

        now = time.time()
        if now - last_request.get(user_id, 0) < config.USER_COOLDOWN_SECONDS:
            answer_callback(cq["id"], "Подожди пару секунд…")
            return
        last_request[user_id] = now

        if data == "scan":
            answer_callback(cq["id"], "Сканирую все монеты…")
            handle_scan(chat_id)
        elif data == "scan_forex":
            answer_callback(cq["id"], "Сканирую валютные пары…")
            handle_scan_forex(chat_id)
        return

    # Обычное сообщение / команда
    if "message" in upd:
        msg = upd["message"]
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if text.startswith("/start") or text.startswith("/help"):
            send_message(chat_id, WELCOME, build_keyboard())
        elif text:
            send_message(chat_id, "Жми кнопку 👇", build_keyboard())


def keep_alive_loop():
    """Пингует свой URL каждые 10 минут, чтобы Render не усыпил сервис."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not url:
        return
    while True:
        time.sleep(600)
        try:
            requests.get(url, timeout=15)
        except Exception:
            pass


def polling_loop():
    offset = 0
    print("TUSA TRADE запущен, слушаю Telegram…")
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

    ka = threading.Thread(target=keep_alive_loop, daemon=True)
    ka.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
