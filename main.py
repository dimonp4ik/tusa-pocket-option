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
        [{"text": "🎯 ДАЙ СЕТАП", "callback_data": "scan"}],
        [{"text": "ℹ️ Как пользоваться", "callback_data": "howto"}],
    ]


WELCOME = (
    "👋 <b>TUSA TRADE — сетапы для Pocket Option</b>\n\n"
    "Жми <b>🎯 ДАЙ СЕТАП</b> — я просканирую все монеты "
    "и валютные пары и кину одну лучшую прямо сейчас.\n\n"
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
    word = "ВВЕРХ (выше)" if best["direction"] == "UP" else "ВНИЗ (ниже)"

    now = datetime.now(timezone.utc)
    sec_left = 60 - now.second
    # Если до новой минуты осталось мало — заходим на следующей
    if sec_left <= 5:
        sec_left += 60
    # Живая свеча против сетапа — ждём ещё одну минуту (вход через 60-119 сек)
    if best.get("wait_extra"):
        sec_left += 60

    text = f"🎯 <b>СЕТАП: {best['name']}</b>\n\n"
    text += f"<b>Ставка: {word}</b>\n"
    if best.get("regime"):
        text += f"📊 Тактика: <b>{best['regime']}</b>\n"
    text += f"⏱ Экспирация: <b>{config.EXPIRY_MINUTES} минута</b>\n"
    text += f"💪 Сила: <b>{best['score']}/{best['max_score']}</b>\n"
    if best.get("payout"):
        text += f"💰 Выплата: <b>+{best['payout']}%</b> (проверено)\n"
    text += "\n📋 Почему:\n"
    for r in best["reasons"]:
        text += "• " + r + "\n"
    text += f"\n⚡ <b>Заходи через {sec_left} сек</b> (на новой минуте).\n"
    if best.get("wait_extra"):
        text += (
            "⏳ Текущая свеча ещё идёт против входа — поэтому ждём её "
            "закрытия и заходим на следующей минуте.\n"
        )
    text += "Открой пару сейчас и жди.\n\n"
    if best.get("otc"):
        text += "ℹ️ Это OTC-пара — в Pocket Option ищи её С пометкой OTC."
    elif best.get("payout"):
        text += "ℹ️ Обычная пара — бери её БЕЗ пометки OTC. Выплата проверена."
    else:
        text += "⚠️ Только если выплата ≥ 85% и БЕЗ пометки OTC!"
    if best.get("backup"):
        text += (
            "\n\n📡 Котировки Покета сейчас недоступны — анализ по "
            "реальному рынку (Binance/Deriv). График Покета может "
            "чуть отличаться."
        )
    return text


def format_no_setup(candidate):
    # Слабый кандидат (1/7) не показываем вообще
    if candidate and candidate["score"] >= 2:
        d = "ВВЕРХ" if candidate["direction"] == "UP" else "ВНИЗ"
        return (
            f"Ближе всех: <b>{candidate['name']} {d}</b> — "
            f"{candidate['score']}/{candidate['max_score']}, но этого мало. "
            "Не ставь, жми ещё раз через пару минут."
        )
    return "Пока подходящей пары нет. Жми ещё раз через пару минут."


def handle_scan(chat_id):
    try:
        best, candidate = signals.scan_best()
        if best:
            send_message(chat_id, format_setup(best), build_keyboard())
        else:
            send_message(chat_id, format_no_setup(candidate), build_keyboard())
    except Exception:
        traceback.print_exc()
        send_message(
            chat_id,
            "⚠️ Не получилось загрузить данные. Попробуй ещё раз через минуту.",
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
            answer_callback(cq["id"], "Сканирую крипту и валюту…")
            handle_scan(chat_id)
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
