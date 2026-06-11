# ============================================================
# TUSA TRADE — НАСТРОЙКИ
# ============================================================

# Монеты для сканирования (данные с Binance, реальное время).
# Слева — символ Binance, справа — как пара называется в Pocket Option.
# ВАЖНО: ставить только обычные пары, НЕ OTC!
PAIRS = {
    "BTCUSDT": "BTC/USD",
    "ETHUSDT": "ETH/USD",
    "SOLUSDT": "SOL/USD",
    "XRPUSDT": "XRP/USD",
    "ADAUSDT": "ADA/USD",
    "DOGEUSDT": "DOGE/USD",
    "BNBUSDT": "BNB/USD",
    "LTCUSDT": "LTC/USD",
    "AVAXUSDT": "AVAX/USD",
    "LINKUSDT": "LINK/USD",
}

# Валютные пары (данные с Deriv, реальное время, без ключей).
# Слева — символ Deriv, справа — как пара называется в Pocket Option.
FOREX_PAIRS = {
    "frxEURUSD": "EUR/USD",
    "frxGBPUSD": "GBP/USD",
    "frxUSDJPY": "USD/JPY",
    "frxAUDUSD": "AUD/USD",
    "frxUSDCAD": "USD/CAD",
    "frxUSDCHF": "USD/CHF",
    "frxEURJPY": "EUR/JPY",
    "frxGBPJPY": "GBP/JPY",
}

# Экспирация сделки (минут). Вход — на открытии новой минуты.
EXPIRY_MINUTES = 1

# Минимальный балл сетапа для крипты (максимум 7).
# 5+ = жёсткий отбор: сигналов меньше, но качество выше.
MIN_SCORE = 5

# Для валюты максимум 6 (у форекса нет данных по объёму), порог ниже.
MIN_SCORE_FOREX = 4

# Свечи валют старше этого (секунд) = рынок закрыт, пара выбывает
FOREX_MAX_AGE = 180

# Пауза между запросами одного человека (секунд)
USER_COOLDOWN_SECONDS = 5

# ---------- Индикаторы (подобраны под 1-минутный скальпинг) ----------

# Быстрый RSI: период 7, границы 25/75 (сильные: 20/80)
RSI_PERIOD = 7
RSI_OVERSOLD = 25
RSI_OVERBOUGHT = 75
RSI_EXTREME_LOW = 20
RSI_EXTREME_HIGH = 80

# Bollinger Bands
BB_PERIOD = 20
BB_STD = 2.0

# Тренд на 5-минутке
EMA_FAST = 9
EMA_SLOW = 21

# Всплеск объёма: текущий объём >= этого множителя от среднего
VOLUME_SPIKE = 1.8

# Фильтр мёртвого рынка: средний размах свечи к цене ниже этого % — пропуск
MIN_ATR_PCT = 0.02         # крипта: 0.02% от цены
MIN_ATR_PCT_FOREX = 0.005  # валюта двигается мельче, порог ниже

# Фильтр новостной свечи: последняя свеча больше ATR в N раз — пропуск
MAX_CANDLE_VS_ATR = 3.5
