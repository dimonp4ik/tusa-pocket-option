# ============================================================
# TUSA TRADE — OTC-котировки напрямую из Pocket Option
# Неофициальный API (binaryoptionstoolsv2), демо-аккаунт,
# SSID в переменной окружения PO_SSID.
# ============================================================

import os
import time
import threading
import traceback

import config
import signals

_client = None
_lock = threading.Lock()


def enabled():
    return bool(os.environ.get("PO_SSID", ""))


def _get_client():
    global _client
    if _client is None:
        from BinaryOptionsToolsV2.pocketoption import PocketOption
        ssid = os.environ.get("PO_SSID", "")
        if not ssid:
            return None
        _client = PocketOption(ssid)
    return _client


def _reset_client():
    global _client
    try:
        if _client is not None:
            _client.disconnect()
    except Exception:
        pass
    _client = None


def pretty_name(asset):
    """EURUSD_otc -> EUR/USD OTC, BTCUSD -> BTC/USD."""
    is_otc = asset.endswith("_otc")
    base = asset[:-4] if is_otc else asset
    if len(base) == 6:
        base = base[:3] + "/" + base[3:]
    return base + (" OTC" if is_otc else "")


# Крипто-основы: для них порог «мёртвого рынка» выше, чем у валют
CRYPTO_BASES = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOG", "LTC", "BNB", "AVA",
    "LNK", "DOT", "TRX", "TON", "BCH", "ETC", "XLM", "XMR", "NEO",
}


def _min_atr(asset):
    base = asset[:3]
    if base in CRYPTO_BASES:
        return config.MIN_ATR_PCT
    return config.MIN_ATR_PCT_FOREX


def _to_arrays(candles):
    """Список свечей из API -> массивы open/high/low/close + время последней."""
    candles = sorted(candles, key=lambda c: c.get("timestamp", 0))
    opens = [float(c["open"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    last_time = candles[-1].get("timestamp", 0) if candles else 0
    return opens, highs, lows, closes, last_time


def _is_pair(asset):
    """Только валютные/крипто пары вида XXXYYY_otc — акции и индексы мимо."""
    base = asset[:-4] if asset.endswith("_otc") else asset
    return len(base) == 6 and base.isalpha() and base.isupper()


def current_price(symbol):
    """Текущая цена пары Покета для проверки результата сделки."""
    if not enabled():
        return None
    with _lock:
        try:
            client = _get_client()
            c = client.history(symbol, 60)
            if not c:
                return None
            _, _, _, cl, _ = _to_arrays(c)
            return cl[-1]
        except Exception:
            _reset_client()
            return None


def scan_po():
    """Сканирует все пары Покета (обычные + OTC) с выплатой >= MIN_PAYOUT.
    Возвращает (best, all, ok). ok=False — связи с Покетом нет,
    надо переключаться на запасные источники."""
    if not enabled():
        return None, [], False

    with _lock:
        try:
            client = _get_client()
            payouts = client.payout()
            if not payouts:
                raise ValueError("пустой список выплат")
        except Exception:
            traceback.print_exc()
            _reset_client()
            return None, [], False

        # Валютные/крипто пары с нормальной выплатой, OTC и обычные
        assets = [
            (a, p) for a, p in payouts.items()
            if _is_pair(a) and p and p >= config.MIN_PAYOUT
        ]
        # Сначала с самой высокой выплатой
        assets.sort(key=lambda x: x[1], reverse=True)
        assets = assets[:config.MAX_PO_ASSETS]

        results = []
        for asset, payout in assets:
            try:
                c1 = client.history(asset, 60)
                if not c1 or len(c1) < 45:
                    continue
                o1, h1, l1, cl1, t1 = _to_arrays(c1)
                # Свечи старые = пара сейчас не торгуется (рынок закрыт)
                if time.time() - t1 > config.PO_MAX_AGE:
                    continue
                c5 = client.history(asset, 300)
                if not c5 or len(c5) < 45:
                    continue
                o5, h5, l5, cl5, t5 = _to_arrays(c5)

                res = signals.score_setup(
                    pretty_name(asset),
                    o1, h1, l1, cl1, None, cl5,
                    min_atr_pct=_min_atr(asset),
                )
                if res:
                    res["payout"] = payout
                    res["otc"] = asset.endswith("_otc")
                    res["symbol"] = asset
                    res["source"] = "po"
                    results.append(res)
            except Exception:
                continue

    if not results:
        return None, [], True

    results.sort(key=lambda r: r["score"], reverse=True)
    best = results[0]
    if best["score"] >= config.MIN_SCORE_FOREX:
        return best, results, True
    return None, results, True
