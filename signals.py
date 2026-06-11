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


def stochastic(highs, lows, closes, k_period, smooth, d_period):
    """Медленный Stochastic. Возвращает (%K сейчас, %K раньше, %D сейчас, %D раньше)."""
    raw = []
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        raw.append(100.0 * (closes[i] - ll) / (hh - ll) if hh > ll else 50.0)
    k = [sum(raw[i - smooth + 1:i + 1]) / smooth
         for i in range(smooth - 1, len(raw))]
    d = [sum(k[i - d_period + 1:i + 1]) / d_period
         for i in range(d_period - 1, len(k))]
    return k[-1], k[-2], d[-1], d[-2]


def adx(highs, lows, closes, period=14):
    """ADX по Уайлдеру: сила тренда (0-100)."""
    trs, pdms, ndms = [], [], []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
        up_m = highs[i] - highs[i - 1]
        dn_m = lows[i - 1] - lows[i]
        pdms.append(up_m if (up_m > dn_m and up_m > 0) else 0.0)
        ndms.append(dn_m if (dn_m > up_m and dn_m > 0) else 0.0)

    atr_s = sum(trs[:period])
    pdm_s = sum(pdms[:period])
    ndm_s = sum(ndms[:period])
    dxs = []
    for i in range(period, len(trs)):
        atr_s = atr_s - atr_s / period + trs[i]
        pdm_s = pdm_s - pdm_s / period + pdms[i]
        ndm_s = ndm_s - ndm_s / period + ndms[i]
        pdi = 100.0 * pdm_s / atr_s if atr_s > 0 else 0.0
        ndi = 100.0 * ndm_s / atr_s if atr_s > 0 else 0.0
        dxs.append(100.0 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi > 0 else 0.0)

    if len(dxs) < period:
        return 0.0
    a = sum(dxs[:period]) / period
    for x in dxs[period:]:
        a = (a * (period - 1) + x) / period
    return a


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

    # 3. «Падающий нож»: ADX слишком высокий = тренд-поезд,
    # ловить разворот против него — верный слив
    if adx(h1, l1, c1, config.ADX_PERIOD) > config.ADX_MAX:
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

    # 4. Stochastic: пересечение %K/%D в крайней зоне — 1 балл
    k_now, k_prev, d_now, d_prev = stochastic(
        h1, l1, c1, config.STOCH_K, config.STOCH_SMOOTH, config.STOCH_D,
    )
    if k_prev <= d_prev and k_now > d_now and k_now <= config.STOCH_LOW:
        up += 1
        up_r.append(f"Stochastic развернулся вверх из перепроданности ({k_now:.0f})")
    if k_prev >= d_prev and k_now < d_now and k_now >= config.STOCH_HIGH:
        down += 1
        down_r.append(f"Stochastic развернулся вниз из перекупленности ({k_now:.0f})")

    # 5. Всплеск объёма — 1 балл (только крипта, у форекса объёма нет)
    max_score = 7
    if v1 is not None:
        max_score = 8
        avg_vol = sum(v1[-21:-1]) / 20
        if avg_vol > 0 and v1[-1] >= avg_vol * config.VOLUME_SPIKE:
            if up >= down:
                up += 1
                up_r.append(f"всплеск объёма x{v1[-1] / avg_vol:.1f}")
            else:
                down += 1
                down_r.append(f"всплеск объёма x{v1[-1] / avg_vol:.1f}")

    # 6. Тренд 5-минутки — 1 балл
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


# ---------- Общее сканирование: крипта + валюта, одна лучшая ----------

def scan_best():
    """Сканирует крипту и валюту параллельно.
    Возвращает (лучший_прошедший_порог, лучший_кандидат_вообще).
    Сравнение между категориями — по доле набранных баллов."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_crypto = pool.submit(scan_all)
        f_forex = pool.submit(scan_forex)
        crypto_best, crypto_all = f_crypto.result()
        forex_best, forex_all, _ = f_forex.result()

    def ratio(r):
        return r["score"] / r["max_score"]

    passed = [r for r in (crypto_best, forex_best) if r]
    if passed:
        return max(passed, key=ratio), None

    candidates = crypto_all + forex_all
    if candidates:
        return None, max(candidates, key=ratio)
    return None, None
