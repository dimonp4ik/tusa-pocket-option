# ============================================================
# НАСТРОЙКИ БОТА — здесь можно менять параметры
# ============================================================

# Валютные пары (данные с Yahoo Finance).
# Слева — тикер Yahoo, справа — название кнопки / пары в Pocket Option.
FOREX_PAIRS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD",
    "USDCAD=X": "USD/CAD",
    "USDCHF=X": "USD/CHF",
    "EURJPY=X": "EUR/JPY",
    "GBPJPY=X": "GBP/JPY",
}

# Монеты (данные с Binance).
# Слева — символ Binance, справа — название кнопки / пары в Pocket Option.
CRYPTO_PAIRS = {
    "BTCUSDT": "BTC/USD",
    "ETHUSDT": "ETH/USD",
    "SOLUSDT": "SOL/USD",
    "XRPUSDT": "XRP/USD",
    "ADAUSDT": "ADA/USD",
    "DOGEUSDT": "DOGE/USD",
}

# Время экспирации сделки в Pocket Option (минут)
EXPIRY_MINUTES = 5

# Минимальный балл, чтобы сетап считался рабочим (максимум 7).
# Если балл ниже — бот честно скажет "чёткого сетапа нет".
MIN_SCORE = 4

# Пауза между нажатиями кнопок одним человеком (секунд) — защита от спама
USER_COOLDOWN_SECONDS = 5

# Настройки индикаторов
RSI_PERIOD = 14
RSI_OVERSOLD = 30      # ниже = перепродано, ждём отскок ВВЕРХ
RSI_OVERBOUGHT = 70    # выше = перекуплено, ждём откат ВНИЗ
BB_PERIOD = 20         # период Bollinger Bands
BB_STD = 2.0           # ширина полос
EMA_FAST = 9
EMA_SLOW = 21
