# ============================================================
# TUSA TRADE — бот сетапов для Pocket Option
# Режимы: разовый сетап по кнопке + авто-поиск (поток в ЛС).
# Трекинг сделок: бот сам проверяет победа/минус и ведёт статистику.
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
import pocket
import tracker

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = "https://api.telegram.org/bot" + BOT_TOKEN

app = Flask(__name__)

# Защита от спама нажатий
last_request = {}
# Карта: токен -> последний сигнал (для кнопки «Я зашёл»)
pending = {}
_pending_seq = 0
# Антиповтор авто-сигналов: пара+направление -> время последней отправки
last_auto = {}


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


# ---------- Клавиатуры ----------

def build_keyboard(chat_id=None):
    if chat_id is not None and tracker.is_active(chat_id):
        auto_btn = {"text": "⏸ Остановить поиск", "callback_data": "auto_off"}
    else:
        auto_btn = {"text": "▶️ Запустить поиск", "callback_data": "auto_on"}
    return [
        [auto_btn],
        [{"text": "🎯 Дай сетап сейчас", "callback_data": "scan"}],
        [
            {"text": "📊 Статистика", "callback_data": "stats"},
            {"text": "ℹ️ Помощь", "callback_data": "howto"},
        ],
    ]


def signal_keyboard(token):
    return [
        [
            {"text": "✅ Я зашёл", "callback_data": "took|" + str(token)},
            {"text": "⏭ Пропустил", "callback_data": "skip"},
        ],
    ]


WELCOME = (
    "👋 <b>TUSA TRADE — сетапы для Pocket Option</b>\n\n"
    "▶️ <b>Запустить поиск</b> — бот сам ищет сетапы и шлёт их сюда, "
    "пока не остановишь.\n"
    "🎯 <b>Дай сетап сейчас</b> — разовый сигнал по кнопке.\n\n"
    "Под каждым сигналом жми <b>✅ Я зашёл</b> — бот сам проверит "
    "результат и посчитает твой винрейт. 📊\n\n"
    "⚡ Вход на новой минуте, экспирация <b>1 минута</b>.\n"
    "⚠️ Только пары с выплатой <b>+85%</b>."
)

HOWTO = (
    "📖 <b>Как пользоваться</b>\n\n"
    "<b>Авто-поиск:</b> жми ▶️ — бот раз в минуту ищет лучший сетап "
    "и кидает сюда. Останавливается по кнопке ⏸.\n\n"
    "<b>Когда пришёл сетап:</b>\n"
    "1️⃣ Открой пару в Pocket Option\n"
    "2️⃣ Проверь выплату (+85%) и пометку OTC\n"
    "3️⃣ Дождись новой минуты и ставь в нужную сторону\n"
    "4️⃣ Жми <b>✅ Я зашёл</b> — бот проверит результат сам\n\n"
    "<b>Статистика:</b> 📊 — победы/минусы и винрейт, "
    "в т.ч. отдельно по тактикам (откат / тренд).\n\n"
    "❗ Не ставь больше 2-3% депозита на сделку.\n"
    "❗ Бот не гарантирует 100% — это помощник, не казино-чит."
)


# ---------- Формирование сетапа ----------

def format_setup(best):
    word = "ВВЕРХ (выше)" if best["direction"] == "UP" else "ВНИЗ (ниже)"

    now = datetime.now(timezone.utc)
    sec_left = 60 - now.second
    if sec_left <= 5:
        sec_left += 60
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
            "⏳ Текущая свеча ещё идёт против входа — ждём её закрытия "
            "и заходим на следующей минуте.\n"
        )
    text += "Открой пару сейчас и жди.\n\n"
    if best.get("otc"):
        text += "ℹ️ OTC-пара — в Pocket Option ищи её С пометкой OTC."
    elif best.get("payout"):
        text += "ℹ️ Обычная пара — бери БЕЗ пометки OTC. Выплата проверена."
    else:
        text += "⚠️ Только если выплата ≥ 85% и БЕЗ пометки OTC!"
    if best.get("backup"):
        text += (
            "\n\n📡 Котировки Покета недоступны — анализ по реальному "
            "рынку (Binance/Deriv). График Покета может чуть отличаться."
        )
    return text


def format_no_setup(candidate):
    if candidate and candidate["score"] >= 2:
        d = "ВВЕРХ" if candidate["direction"] == "UP" else "ВНИЗ"
        return (
            f"Ближе всех: <b>{candidate['name']} {d}</b> — "
            f"{candidate['score']}/{candidate['max_score']}, но этого мало. "
            "Не ставь, жми ещё раз через пару минут."
        )
    return "Пока подходящей пары нет. Жми ещё раз через пару минут."


def register_signal(signal):
    """Сохраняет сигнал под токеном для кнопки «Я зашёл»."""
    global _pending_seq
    _pending_seq += 1
    token = _pending_seq
    pending[token] = signal
    # чистим старьё
    if len(pending) > 200:
        for k in sorted(pending)[:100]:
            pending.pop(k, None)
    return token


def send_setup(chat_id, best):
    token = register_signal(best)
    send_message(chat_id, format_setup(best), signal_keyboard(token))


# ---------- Цена для проверки результата ----------

def price_for(source, symbol):
    try:
        if source == "po":
            return pocket.current_price(symbol)
        return signals.current_price(source, symbol)
    except Exception:
        traceback.print_exc()
        return None


# ---------- Обработчики кнопок ----------

def handle_scan(chat_id):
    try:
        best, candidate = signals.scan_best()
        if best:
            send_setup(chat_id, best)
        else:
            send_message(chat_id, format_no_setup(candidate), build_keyboard(chat_id))
    except Exception:
        traceback.print_exc()
        send_message(
            chat_id,
            "⚠️ Не получилось загрузить данные. Попробуй ещё раз через минуту.",
            build_keyboard(chat_id),
        )


def handle_took(chat_id, token):
    signal = pending.get(int(token))
    if not signal:
        send_message(
            chat_id,
            "⌛ Этот сетап уже устарел. Дождись нового сигнала.",
            build_keyboard(chat_id),
        )
        return
    price = price_for(signal.get("source"), signal.get("symbol"))
    if price is None:
        send_message(
            chat_id,
            "⚠️ Не смог зафиксировать цену входа. Попробуй на следующем сетапе.",
            build_keyboard(chat_id),
        )
        return
    expiry = time.time() + config.EXPIRY_MINUTES * 60
    tracker.add_trade(chat_id, signal, price, expiry)
    d = "ВВЕРХ" if signal["direction"] == "UP" else "ВНИЗ"
    send_message(
        chat_id,
        f"✅ Записал: <b>{signal['name']} {d}</b>, цена входа {price:g}.\n"
        f"Проверю результат через {config.EXPIRY_MINUTES} мин и напишу. 📊",
    )


def handle_stats(chat_id):
    s = tracker.get_stats(chat_id)
    if not s or (s["win"] + s["loss"] + s["tie"]) == 0:
        send_message(
            chat_id,
            "📊 Сделок пока нет. Заходи в сетапы и жми «✅ Я зашёл» — "
            "буду считать твой винрейт.",
            build_keyboard(chat_id),
        )
        return
    total = s["win"] + s["loss"] + s["tie"]
    decided = s["win"] + s["loss"]
    wr = (100.0 * s["win"] / decided) if decided else 0.0
    text = "📊 <b>Твоя статистика</b>\n\n"
    text += f"Сделок: <b>{total}</b>\n"
    text += f"✅ Побед: <b>{s['win']}</b>\n"
    text += f"❌ Минусов: <b>{s['loss']}</b>\n"
    if s["tie"]:
        text += f"➖ Ничьих: <b>{s['tie']}</b>\n"
    text += f"🎯 Винрейт: <b>{wr:.0f}%</b>\n"
    if s.get("regimes"):
        text += "\nПо тактикам:\n"
        for rg, v in s["regimes"].items():
            dec = v["win"] + v["loss"]
            w = (100.0 * v["win"] / dec) if dec else 0.0
            text += f"• {rg}: {v['win']}/{dec} ({w:.0f}%)\n"
    send_message(chat_id, text, build_keyboard(chat_id))


# ---------- Обработка апдейтов ----------

def process_update(upd):
    if "callback_query" in upd:
        cq = upd["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        user_id = cq["from"]["id"]
        data = cq.get("data", "")

        if data == "howto":
            answer_callback(cq["id"])
            send_message(chat_id, HOWTO, build_keyboard(chat_id))
            return
        if data == "skip":
            answer_callback(cq["id"], "Ок, пропускаем")
            return
        if data == "stats":
            answer_callback(cq["id"])
            handle_stats(chat_id)
            return
        if data == "auto_on":
            answer_callback(cq["id"], "Поиск запущен")
            tracker.set_active(chat_id, True)
            send_message(
                chat_id,
                "▶️ <b>Поиск запущен.</b> Буду присылать сетапы сюда. "
                "Останови кнопкой ⏸ когда надо.",
                build_keyboard(chat_id),
            )
            return
        if data == "auto_off":
            answer_callback(cq["id"], "Поиск остановлен")
            tracker.set_active(chat_id, False)
            send_message(
                chat_id, "⏸ <b>Поиск остановлен.</b>", build_keyboard(chat_id)
            )
            return
        if data.startswith("took|"):
            answer_callback(cq["id"], "Записал сделку")
            handle_took(chat_id, data.split("|", 1)[1])
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

    if "message" in upd:
        msg = upd["message"]
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if text.startswith("/start") or text.startswith("/help"):
            send_message(chat_id, WELCOME, build_keyboard(chat_id))
        elif text.startswith("/stats"):
            handle_stats(chat_id)
        elif text:
            send_message(chat_id, "Жми кнопку 👇", build_keyboard(chat_id))


# ---------- Фоновые циклы ----------

def keep_alive_loop():
    """Пинг своего URL, чтобы Render не усыпил сервис."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not url:
        return
    while True:
        time.sleep(600)
        try:
            requests.get(url, timeout=15)
        except Exception:
            pass


def scanner_loop():
    """Раз в минуту (у границы минуты) сканирует и шлёт сетап активным."""
    last_minute = -1
    while True:
        try:
            now = datetime.now(timezone.utc)
            users = tracker.active_users()
            # Сканируем один раз ближе к концу минуты — свеча почти закрыта
            if users and now.second >= 50 and now.minute != last_minute:
                last_minute = now.minute
                best, _ = signals.scan_best()
                if best:
                    key = best["name"] + best["direction"]
                    if time.time() - last_auto.get(key, 0) >= 120:
                        last_auto[key] = time.time()
                        for chat_id in users:
                            send_setup(chat_id, best)
        except Exception:
            traceback.print_exc()
        time.sleep(3)


def resolver_loop():
    """Проверяет открытые сделки: цена на выходе vs вход -> победа/минус."""
    while True:
        try:
            now = time.time()
            for t in tracker.due_trades(now):
                exit_price = price_for(t.get("source"), t.get("symbol"))
                if exit_price is None:
                    # не смогли получить цену — попробуем позже, но не вечно
                    if now - t["expiry"] > 600:
                        tracker.remove_trade(t["id"])
                    continue
                entry = t["entry"]
                if exit_price == entry:
                    result = "tie"
                elif (t["direction"] == "UP" and exit_price > entry) or \
                     (t["direction"] == "DOWN" and exit_price < entry):
                    result = "win"
                else:
                    result = "loss"

                tracker.record(t["chat"], result, t.get("regime", "?"))
                tracker.remove_trade(t["id"])

                d = "ВВЕРХ" if t["direction"] == "UP" else "ВНИЗ"
                if result == "win":
                    head = "✅ <b>ПОБЕДА!</b>"
                elif result == "loss":
                    head = "❌ <b>Минус.</b>"
                else:
                    head = "➖ <b>Ничья</b> (цена не изменилась)."
                send_message(
                    t["chat"],
                    f"{head}\n{t['name']} {d}\n"
                    f"Вход: {entry:g} → Выход: {exit_price:g}",
                    build_keyboard(t["chat"]),
                )
        except Exception:
            traceback.print_exc()
        time.sleep(5)


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

    tracker.load()

    for fn in (polling_loop, keep_alive_loop, scanner_loop, resolver_loop):
        threading.Thread(target=fn, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
