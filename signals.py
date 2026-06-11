# ============================================================
# TUSA TRADE — анализ рынка для 1-минутного скальпинга
# Крипта: Binance (через зеркало binance.vision) + запас Bybit.
# Валюта: Deriv API (реальное время, без ключей).
# ============================================================

import json
import time
import concurrent.futures

import requests
from websocket import create_connection

import config


# ---------- Крипта: загрузка свечей ----------
# Основной источник: data-api.binance.vision — официальное зеркало Binance
# для рыночных данных, работает с американских IP (Render не блокируется).
# Запасные: api.binance.com, потом Bybit.

BINANCE_URLS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]


def _parse_binance(data):
    opens = [float(k[1]) for k in data]
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    closes = [float(k[4]) for k in data]
    volumes = [float(k[5]) for k in data]
    return opens, highs, lows, closes, volumes


def _fetch_bybit(symbol, interval, limit):
    """Запасной источник: Bybit. interval '1m'/'5m' -> '1'/'5'."""
    r = requests.get(
        "https://api.bybit.com/v5/market/kline",
        params={
            "category": "spot",
            "symbol": symbol,
            "interval": interval.replace("m", ""),
            "limit": limit,
        },
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json()["result"]["list"]
    rows.reverse()  # Bybit отдаёт новые первыми, разворачиваем
    opens = [float(k[1]) for k in rows]
    highs = [float(k[2]) for k in rows]
    lows = [float(k[3]) for k in rows]
    closes = [float(k[4]) for k in rows]
    volumes = [float(k[5]) for k in rows]
    return opens, highs, lows, closes, volumes


def fetch_binance(symbol, interval, limit=60):
    """Свечи: open/high/low/close/volume. Перебирает источники по очереди."""
    last_err = None
    for url in BINANCE_URLS:
        try:
            r = requests.get(
                url,
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=10,
            )
            r.raise_for_status()
            return _parse_binance(r.json())
        except Exception as e:
            last_err = e
    try:
        return _fetch_bybit(symbol, interval, limit)
    except Exception:
        raise last_err


# ---------- Валюта: загрузка свечей с Deriv ----------

DERIV_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"


def _deriv_request(ws, symbol, granularity, count=60):
    """Запрашивает свечи по одному символу через открытое соединение."""
    ws.send(json.dumps({
        "ticks_history": symbol,
        "style": "candles",
        "granularity": granularity,
        "count": count,
        "end": "latest",
    }))
    resp = json.loads(ws.recv())
    if "error" in resp:
        raise ValueError(resp["error"].get("message", "deriv error"))
    candles = resp["candles"]
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    last_epoch = candles[-1]["epoch"]
    return opens, highs, lows, closes, last_epoch


# ---------- Индикаторы ----------

def ema(values, period):
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def bollinger(closes, period, num_std):
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    std = var ** 0.5
    return mid - num_std * std, mid, mid + num_std * std


def atr(highs, lows, period=14):
    ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
    return sum(ranges) / len(ranges)


# ---------- Оценка сетапа (общая для крипты и валюты) ----------

def score_setup(name, o1, h1, l1, c1, v1, c5, min_atr_pct=None):
    """Балльная оценка. v1=None для валюты (нет объёма, максимум 6 баллов)."""
    if len(c1) < 40 or len(c5) < 40:
        return None

    if min_atr_pct is None:
        min_atr_pct = config.MIN_ATR_PCT

    price = c1[-1]
    a = atr(h1, l1, 14)

    # --- ЖЁСТКИЕ ФИЛЬТРЫ: не прошёл — пара выбывает ---

    # 1. Мёртвый рынок: движения слишком мелкие, сигналы = шум
    if a / price * 100 < min_atr_pct:
        return None

    # 2. Новостная свеча: аномальный размах, непредсказуемо
    last_range = h1[-1] - l1[-1]
    if a > 0 and last_range > a * config.MAX_CANDLE_VS_ATR:
        return None

    # --- БАЛЛЫ ---

    rsi1 = rsi(c1, config.RSI_PERIOD)
    bb_low, bb_mid, bb_high = bollinger(c1, config.BB_PERIOD, config.BB_STD)
    ema_fast5 = ema(c5, config.EMA_FAST)
    ema_slow5 = ema(c5, config.EMA_SLOW)

    up, down = 0, 0
    up_r, down_r = [], []

    # 1. Быстрый RSI(7) — до 2 баллов
    if rsi1 <= config.RSI_EXTREME_LOW:
        up += 2
        up_r.append(f"RSI сильно перепродан ({rsi1:.0f})")
    elif rsi1 <= config.RSI_OVERSOLD:
        up += 1
        up_r.append(f"RSI перепродан ({rsi1:.0f})")
    if rsi1 >= config.RSI_EXTREME_HIGH:
        down += 2
        down_r.append(f"RSI сильно перекуплен ({rsi1:.0f})")
    elif rsi1 >= config.RSI_OVERBOUGHT:
        down += 1
        down_r.append(f"RSI перекуплен ({rsi1:.0f})")

    # 2. Bollinger: прокол полосы + возврат внутрь — до 2 баллов
    if l1[-1] < bb_low and price > bb_low:
        up += 2
        up_r.append("прокол нижней Bollinger и возврат внутрь")
    elif price <= bb_low:
        up += 1
        up_r.append("цена ниже нижней Bollinger")
    if h1[-1] > bb_high and price < bb_high:
        down += 2
        down_r.append("прокол верхней Bollinger и возврат внутрь")
    elif price >= bb_high:
        down += 1
        down_r.append("цена выше верхней Bollinger")

    # 3. Хвост свечи (wick rejection) — 1 балл
    body = abs(c1[-1] - o1[-1])
    lower_wick = min(c1[-1], o1[-1]) - l1[-1]
    upper_wick = h1[-1] - max(c1[-1], o1[-1])
    if body > 0 and lower_wick >= body * 2:
        up += 1
        up_r.append("длинный нижний хвост — покупатели отбили цену")
    if body > 0 and upper_wick >= body * 2:
        down += 1
        down_r.append("длинный верхний хвост — продавцы отбили цену")

    # 4. Всплеск объёма — 1 балл (только крипта, у форекса объёма нет)
    max_score = 6
    if v1 is not None:
        max_score = 7
        avg_vol = sum(v1[-21:-1]) / 20
        if avg_vol > 0 and v1[-1] >= avg_vol * config.VOLUME_SPIKE:
            if up >= down:
                up += 1
                up_r.append(f"всплеск объёма x{v1[-1] / avg_vol:.1f}")
            else:
                down += 1
                down_r.append(f"всплеск объёма x{v1[-1] / avg_vol:.1f}")

    # 5. Тренд 5-минутки — 1 балл
    if ema_fast5 > ema_slow5:
        up += 1
        up_r.append("тренд 5м вверх")
    else:
        down += 1
        down_r.append("тренд 5м вниз")

    if up >= down:
        direction, score, reasons = "UP", up, up_r
    else:
        direction, score, reasons = "DOWN", down, down_r

    return {
        "name": name,
        "direction": direction,
        "score": score,
        "max_score": max_score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi1,
    }


# ---------- Сканирование: КРИПТА ----------

def analyze_crypto(symbol):
    o1, h1, l1, c1, v1 = fetch_binance(symbol, "1m", 60)
    o5, h5, l5, c5, v5 = fetch_binance(symbol, "5m", 60)
    return score_setup(config.PAIRS.get(symbol, symbol), o1, h1, l1, c1, v1, c5)


def scan_all():
    """Сканирует все монеты параллельно, возвращает (лучший, все)."""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(analyze_crypto, s): s for s in config.PAIRS}
        for f in concurrent.futures.as_completed(futures):
            try:
                res = f.result()
                if res:
                    results.append(res)
            except Exception:
                pass

    if not results:
        return None, []

    results.sort(key=lambda r: r["score"], reverse=True)
    best = results[0]
    if best["score"] >= config.MIN_SCORE:
        return best, results
    return None, results


# ---------- Сканирование: ВАЛЮТА ----------

def scan_forex():
    """Сканирует валютные пары через одно соединение с Deriv.
    Возвращает (лучший, все, рынок_открыт)."""
    results = []
    market_open = False

    ws = create_connection(DERIV_URL, timeout=15)
    try:
        for symbol, name in config.FOREX_PAIRS.items():
            try:
                o1, h1, l1, c1, ep1 = _deriv_request(ws, symbol, 60)
                # Свечи старые = рынок закрыт (выходные/праздники)
                if time.time() - ep1 > config.FOREX_MAX_AGE:
                    continue
                market_open = True
                o5, h5, l5, c5, ep5 = _deriv_request(ws, symbol, 300)
                res = score_setup(
                    name, o1, h1, l1, c1, None, c5,
                    min_atr_pct=config.MIN_ATR_PCT_FOREX,
                )
                if res:
                    results.append(res)
            except Exception:
                continue
    finally:
        ws.close()

    if not results:
        return None, [], market_open

    results.sort(key=lambda r: r["score"], reverse=True)
    best = results[0]
    if best["score"] >= config.MIN_SCORE_FOREX:
        return best, results, market_open
    return None, results, market_open
