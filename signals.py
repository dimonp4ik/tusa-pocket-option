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
    """Балльная оценка по ЗАКРЫТЫМ свечам (без перерисовки).
    v1=None для валюты (нет объёма, максимум 6 баллов)."""
    if len(c1) < 41 or len(c5) < 41:
        return None

    if min_atr_pct is None:
        min_atr_pct = config.MIN_ATR_PCT

    # Последняя свеча в данных ещё формируется. Сигнал считаем ТОЛЬКО по
    # закрытым свечам — иначе «перерисовка»: то, что видим в середине минуты,
    # к её закрытию часто исчезает, и сделка уходит в минус.
    live_o, live_c = o1[-1], c1[-1]
    o1, h1, l1, c1 = o1[:-1], h1[:-1], l1[:-1], c1[:-1]
    if v1 is not None:
        v1 = v1[:-1]
    c5 = c5[:-1]

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

    # 3. Слишком дикий тренд (ADX выше потолка) — непредсказуемо, пропуск
    adx_val = adx(h1, l1, c1, config.ADX_PERIOD)
    if adx_val > config.ADX_MAX:
        return None

    # Общие данные
    rsi1 = rsi(c1, config.RSI_PERIOD)
    rsi_prev = rsi(c1[:-1], config.RSI_PERIOD)
    bb_low, bb_mid, bb_high = bollinger(c1, config.BB_PERIOD, config.BB_STD)
    ema_fast5 = ema(c5, config.EMA_FAST)
    ema_slow5 = ema(c5, config.EMA_SLOW)
    ema_fast1 = ema(c1, config.EMA_FAST)
    k_now, k_prev, d_now, d_prev = stochastic(
        h1, l1, c1, config.STOCH_K, config.STOCH_SMOOTH, config.STOCH_D,
    )
    body = abs(c1[-1] - o1[-1])
    lower_wick = min(c1[-1], o1[-1]) - l1[-1]
    upper_wick = h1[-1] - max(c1[-1], o1[-1])
    has_vol = v1 is not None
    max_score = 7 if has_vol else 6

    def vol_spike():
        if not has_vol:
            return 0.0
        avg = sum(v1[-21:-1]) / 20
        return v1[-1] / avg if avg > 0 else 0.0

    # ===== РЕЖИМ РЫНКА =====
    if adx_val < config.ADX_RANGE:
        # ----- ПЛОСКИЙ РЫНОК: тактика ОТКАТА (фейдим края) -----
        regime = "откат"
        up, down = 0, 0
        up_r, down_r = [], []

        # RSI крайности — до 2
        if rsi1 <= config.RSI_EXTREME_LOW:
            up += 2; up_r.append(f"RSI сильно перепродан ({rsi1:.0f})")
        elif rsi1 <= config.RSI_OVERSOLD:
            up += 1; up_r.append(f"RSI перепродан ({rsi1:.0f})")
        if rsi1 >= config.RSI_EXTREME_HIGH:
            down += 2; down_r.append(f"RSI сильно перекуплен ({rsi1:.0f})")
        elif rsi1 >= config.RSI_OVERBOUGHT:
            down += 1; down_r.append(f"RSI перекуплен ({rsi1:.0f})")

        # Bollinger прокол+возврат — до 2
        if l1[-1] < bb_low and price > bb_low:
            up += 2; up_r.append("прокол нижней Bollinger и возврат внутрь")
        elif price <= bb_low:
            up += 1; up_r.append("цена у нижней Bollinger")
        if h1[-1] > bb_high and price < bb_high:
            down += 2; down_r.append("прокол верхней Bollinger и возврат внутрь")
        elif price >= bb_high:
            down += 1; down_r.append("цена у верхней Bollinger")

        # Хвост свечи — 1
        if body > 0 and lower_wick >= body * 2:
            up += 1; up_r.append("длинный нижний хвост — покупатели отбили цену")
        if body > 0 and upper_wick >= body * 2:
            down += 1; down_r.append("длинный верхний хвост — продавцы отбили цену")

        # Stochastic разворот из крайней зоны — 1
        if k_prev <= d_prev and k_now > d_now and k_now <= config.STOCH_LOW:
            up += 1; up_r.append(f"Stochastic развернулся вверх ({k_now:.0f})")
        if k_prev >= d_prev and k_now < d_now and k_now >= config.STOCH_HIGH:
            down += 1; down_r.append(f"Stochastic развернулся вниз ({k_now:.0f})")

        # Объём — 1 (крипта)
        if has_vol and vol_spike() >= config.VOLUME_SPIKE:
            if up >= down:
                up += 1; up_r.append(f"всплеск объёма x{vol_spike():.1f}")
            else:
                down += 1; down_r.append(f"всплеск объёма x{vol_spike():.1f}")

        if up >= down:
            direction, score, reasons = "UP", up, up_r
        else:
            direction, score, reasons = "DOWN", down, down_r

    else:
        # ----- ТРЕНД: тактика ПРОДОЛЖЕНИЯ (входим ПО тренду на откате) -----
        regime = "тренд"
        is_up = ema_fast5 > ema_slow5
        direction = "UP" if is_up else "DOWN"
        score = 0
        reasons = []
        tdir = "вверх" if is_up else "вниз"
        reasons.append(f"тренд 5м {tdir} (ADX {adx_val:.0f})")

        # 1. Откат закончился, свеча пошла обратно по тренду — до 2
        resumed = (c1[-1] > o1[-1]) if is_up else (c1[-1] < o1[-1])
        pulled = (c1[-2] < o1[-2]) if is_up else (c1[-2] > o1[-2])
        if resumed and pulled:
            score += 2; reasons.append("откат закончился, цена пошла по тренду")
        elif resumed:
            score += 1; reasons.append("свеча идёт по тренду")

        # 2. RSI откатил к середине и поворачивает по тренду — 1
        if is_up and 40 <= rsi1 <= 60 and rsi1 > rsi_prev:
            score += 1; reasons.append(f"RSI откатил и растёт ({rsi1:.0f})")
        if (not is_up) and 40 <= rsi1 <= 60 and rsi1 < rsi_prev:
            score += 1; reasons.append(f"RSI откатил и падает ({rsi1:.0f})")

        # 3. Stochastic поворачивает по тренду из зоны отката — 1
        if is_up and k_prev <= d_prev and k_now > d_now and k_now < 60:
            score += 1; reasons.append(f"Stochastic повернул вверх ({k_now:.0f})")
        if (not is_up) and k_prev >= d_prev and k_now < d_now and k_now > 40:
            score += 1; reasons.append(f"Stochastic повернул вниз ({k_now:.0f})")

        # 4. Цена на правильной стороне EMA9(1м) — тренд цел — 1
        if (is_up and price > ema_fast1) or ((not is_up) and price < ema_fast1):
            score += 1; reasons.append("цена держит сторону EMA9")

        # 5. Сильный тренд (ADX) — продолжение надёжнее — 1
        if adx_val >= config.ADX_STRONG:
            score += 1; reasons.append(f"сильный тренд (ADX {adx_val:.0f})")

        # 6. Объём по тренду — 1 (крипта)
        if has_vol and vol_spike() >= config.VOLUME_SPIKE and resumed:
            score += 1; reasons.append(f"всплеск объёма x{vol_spike():.1f}")

    # Живая (ещё не закрытая) свеча идёт против сетапа?
    # Если да — разворот не подтверждён, ждём её закрытия и заходим
    # на следующей минуте (вход переносится на +1 мин).
    wait_extra = False
    live_body = live_c - live_o
    threshold = a * config.WAIT_EXTRA_ATR
    if direction == "UP" and live_body < -threshold:
        wait_extra = True
    elif direction == "DOWN" and live_body > threshold:
        wait_extra = True

    # Экспирация под характер сетапа:
    #  откат — быстрый отскок, держим коротко (1 мин)
    #  тренд — даём движению время; чем сильнее тренд, тем дольше
    if regime == "откат":
        expiry = config.EXPIRY_MIN
    elif adx_val >= config.ADX_STRONG:
        expiry = config.EXPIRY_MAX
    else:
        expiry = min(config.EXPIRY_MIN + 1, config.EXPIRY_MAX)

    return {
        "name": name,
        "direction": direction,
        "score": score,
        "max_score": max_score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi1,
        "regime": regime,
        "wait_extra": wait_extra,
        "expiry": expiry,
    }


# ---------- Сканирование: КРИПТА ----------

def analyze_crypto(symbol):
    o1, h1, l1, c1, v1 = fetch_binance(symbol, "1m", 60)
    o5, h5, l5, c5, v5 = fetch_binance(symbol, "5m", 60)
    res = score_setup(config.PAIRS.get(symbol, symbol), o1, h1, l1, c1, v1, c5)
    if res:
        res["symbol"] = symbol
        res["source"] = "binance"
    return res


def current_price(source, symbol):
    """Текущая (последняя) цена пары для проверки результата сделки."""
    if source == "binance":
        _, _, _, c, _ = fetch_binance(symbol, "1m", 3)
        return c[-1]
    if source == "deriv":
        ws = create_connection(DERIV_URL, timeout=15)
        try:
            _, _, _, c, _ = _deriv_request(ws, symbol, 60, 3)
            return c[-1]
        finally:
            ws.close()
    return None


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
                    res["symbol"] = symbol
                    res["source"] = "deriv"
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

def _ratio(r):
    return r["score"] / r["max_score"]


def _pick(candidates):
    """Из набора (результат, порог) выбирает лучший прошедший порог И
    разрешённый калибровкой. Возвращает (лучший, ближайший_кандидат)."""
    import tracker

    passed = [
        r for r, thr in candidates
        if r["score"] >= thr and tracker.allowed(r.get("regime", "?"), r["name"])
    ]
    if passed:
        return max(passed, key=_ratio), None
    allcand = [r for r, _ in candidates]
    if allcand:
        return None, max(allcand, key=_ratio)
    return None, None


def scan_best():
    """Главный скан. Сначала котировки самого Покета (обычные + OTC,
    выплата проверена). Если связи с Покетом нет — запасные источники
    (Binance + Deriv). Калибровка сама отсекает просевшие тактики/пары.
    Возвращает (лучший, ближайший_кандидат)."""
    import pocket

    try:
        _, po_all, ok = pocket.scan_po()
        if ok:
            return _pick([(r, config.MIN_SCORE_FOREX) for r in po_all])
    except Exception:
        pass

    # Запасной путь: Binance (крипта) + Deriv (валюта)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_crypto = pool.submit(scan_all)
        f_forex = pool.submit(scan_forex)
        _, crypto_all = f_crypto.result()
        _, forex_all, _ = f_forex.result()

    for r in crypto_all + forex_all:
        r["backup"] = True

    return _pick(
        [(r, config.MIN_SCORE) for r in crypto_all]
        + [(r, config.MIN_SCORE_FOREX) for r in forex_all]
    )
