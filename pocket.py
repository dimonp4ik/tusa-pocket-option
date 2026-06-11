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
    """EURUSD_otc -> EUR/USD OTC, BTCUSD_otc -> BTC/USD OTC."""
    base = asset[:-4] if asset.endswith("_otc") else asset
    if len(base) == 6:
        base = base[:3] + "/" + base[3:]
    return base + " OTC"


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


def scan_otc():
    """Сканирует OTC-пары с выплатой >= MIN_PAYOUT. Возвращает (best, all)."""
    if not enabled():
        return None, []

    with _lock:
        try:
            client = _get_client()
            payouts = client.payout()
        except Exception:
            traceback.print_exc()
            _reset_client()
            return None, []

        # Только OTC-пары с нормальной выплатой
        otc = [
            (a, p) for a, p in payouts.items()
            if a.endswith("_otc") and _is_pair(a) and p and p >= config.MIN_PAYOUT
        ]
        # Сначала с самой высокой выплатой
        otc.sort(key=lambda x: x[1], reverse=True)
        otc = otc[:config.MAX_OTC_ASSETS]

        results = []
        for asset, payout in otc:
            try:
                c1 = client.history(asset, 60)
                if not c1 or len(c1) < 45:
                    continue
                o1, h1, l1, cl1, t1 = _to_arrays(c1)
                c5 = client.history(asset, 300)
                if not c5 or len(c5) < 45:
                    continue
                o5, h5, l5, cl5, t5 = _to_arrays(c5)

                res = signals.score_setup(
                    pretty_name(asset),
                    o1, h1, l1, cl1, None, cl5,
                    min_atr_pct=config.MIN_ATR_PCT_FOREX,
                )
                if res:
                    res["payout"] = payout
                    res["otc"] = True
                    results.append(res)
            except Exception:
                continue

    if not results:
        return None, []

    results.sort(key=lambda r: r["score"], reverse=True)
    best = results[0]
    if best["score"] >= config.MIN_SCORE_FOREX:
        return best, results
    return None, results
