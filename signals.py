# ============================================================
# АНАЛИЗ РЫНКА: загрузка свечей + индикаторы + оценка сетапа
# ============================================================

import requests

import config

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ---------- Загрузка свечей ----------

def fetch_binance(symbol, interval, limit=120):
    """Свечи с Binance: списки open/high/low/close."""
    url = "https://api.binance.com/api/v3/klines"
    r = requests.get(
        url,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    opens = [float(k[1]) for k in data]
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    closes = [float(k[4]) for k in data]
    return opens, highs, lows, closes


def fetch_yahoo(symbol, interval):
    """Свечи с Yahoo Finance (для валютных пар)."""
    rng = "1d" if interval == "1m" else "5d"
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol
    r = requests.get(
        url,
        params={"interval": interval, "range": rng},
        headers=HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    opens, highs, lows, closes = [], [], [], []
    for o, h, l, c in zip(quote["open"], quote["high"], quote["low"], quote["close"]):
        if o is None or h is None or l is None or c is None:
            continue
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
    return opens[-120:], highs[-120:], lows[-120:], closes[-120:]


def fetch_candles(symbol, source, interval):
    if source == "binance":
        return fetch_binance(symbol, interval)
    return fetch_yahoo(symbol, interval)


# ---------- Индикаторы ----------

def ema(values, period):
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period=14):
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


def bollinger(closes, period=20, num_std=2.0):
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    std = var ** 0.5
    return mid - num_std * std, mid, mid + num_std * std


# ---------- Оценка сетапа ----------

def analyze(symbol, source):
    """Возвращает словарь с направлением, баллом и причинами."""
    o1, h1, l1, c1 = fetch_candles(symbol, source, "1m")
    o5, h5, l5, c5 = fetch_candles(symbol, source, "5m")

    if len(c1) < 40 or len(c5) < 40:
        raise ValueError("мало данных")

    price = c1[-1]
    rsi1 = rsi(c1, config.RSI_PERIOD)
    rsi5 = rsi(c5, config.RSI_PERIOD)
    bb_low, bb_mid, bb_high = bollinger(c1, config.BB_PERIOD, config.BB_STD)
    ema_fast5 = ema(c5, config.EMA_FAST)
    ema_slow5 = ema(c5, config.EMA_SLOW)

    up_score, down_score = 0, 0
    up_reasons, down_reasons = [], []

    # 1. RSI на 1-минутке (до 2 баллов)
    if rsi1 <= config.RSI_OVERSOLD:
        up_score += 2
        up_reasons.append(f"RSI 1м перепродан ({rsi1:.0f})")
    elif rsi1 <= config.RSI_OVERSOLD + 5:
        up_score += 1
        up_reasons.append(f"RSI 1м близко к перепроданности ({rsi1:.0f})")
    if rsi1 >= config.RSI_OVERBOUGHT:
        down_score += 2
        down_reasons.append(f"RSI 1м перекуплен ({rsi1:.0f})")
    elif rsi1 >= config.RSI_OVERBOUGHT - 5:
        down_score += 1
        down_reasons.append(f"RSI 1м близко к перекупленности ({rsi1:.0f})")

    # 2. Bollinger Bands на 1-минутке (до 2 баллов)
    if price <= bb_low:
        up_score += 2
        up_reasons.append("цена пробила нижнюю полосу Bollinger")
    elif price <= bb_low + (bb_mid - bb_low) * 0.2:
        up_score += 1
        up_reasons.append("цена у нижней полосы Bollinger")
    if price >= bb_high:
        down_score += 2
        down_reasons.append("цена пробила верхнюю полосу Bollinger")
    elif price >= bb_high - (bb_high - bb_mid) * 0.2:
        down_score += 1
        down_reasons.append("цена у верхней полосы Bollinger")

    # 3. RSI на 5-минутке подтверждает (1 балл)
    if rsi5 <= 40:
        up_score += 1
        up_reasons.append(f"RSI 5м тоже низкий ({rsi5:.0f})")
    if rsi5 >= 60:
        down_score += 1
        down_reasons.append(f"RSI 5м тоже высокий ({rsi5:.0f})")

    # 4. Разворотная свеча после серии (1 балл)
    if c1[-1] > o1[-1] and c1[-2] < o1[-2] and c1[-3] < o1[-3]:
        up_score += 1
        up_reasons.append("зелёная разворотная свеча после красных")
    if c1[-1] < o1[-1] and c1[-2] > o1[-2] and c1[-3] > o1[-3]:
        down_score += 1
        down_reasons.append("красная разворотная свеча после зелёных")

    # 5. Тренд на 5-минутке по EMA (1 балл)
    if ema_fast5 > ema_slow5:
        up_score += 1
        up_reasons.append("тренд 5м вверх (EMA9 > EMA21)")
    else:
        down_score += 1
        down_reasons.append("тренд 5м вниз (EMA9 < EMA21)")

    if up_score >= down_score:
        direction, score, reasons = "UP", up_score, up_reasons
    else:
        direction, score, reasons = "DOWN", down_score, down_reasons

    return {
        "direction": direction,
        "score": score,
        "max_score": 7,
        "reasons": reasons,
        "price": price,
        "rsi1": rsi1,
        "rsi5": rsi5,
        "good": score >= config.MIN_SCORE,
    }
