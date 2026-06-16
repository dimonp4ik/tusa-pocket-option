# ============================================================
# TUSA TRADE — трекинг сделок и статистика
# Хранит активных юзеров (авто-поиск), открытые сделки и winrate.
# Сохраняется в файл (переживает рестарт, но сбрасывается на новом деплое).
# ============================================================

import os
import json
import threading

DATA_FILE = os.path.join(os.path.dirname(__file__), "tusa_data.json")

_lock = threading.Lock()
_state = {
    "active": [],     # chat_id, кому слать авто-сигналы
    "open": [],       # открытые сделки (ждут результата)
    "stats": {},      # chat_id(str) -> статистика
    "seq": 0,         # счётчик id сделок
}


def load():
    global _state
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _state["active"] = data.get("active", [])
        _state["open"] = data.get("open", [])
        _state["stats"] = data.get("stats", {})
        _state["seq"] = data.get("seq", 0)
    except Exception:
        pass


def _save():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False)
    except Exception:
        pass


# ---------- Активные юзеры (авто-поиск) ----------

def set_active(chat_id, on):
    with _lock:
        chat_id = int(chat_id)
        if on and chat_id not in _state["active"]:
            _state["active"].append(chat_id)
        elif not on and chat_id in _state["active"]:
            _state["active"].remove(chat_id)
        _save()


def is_active(chat_id):
    return int(chat_id) in _state["active"]


def active_users():
    return list(_state["active"])


# ---------- Открытые сделки ----------

def add_trade(chat_id, signal, entry_price, expiry_epoch):
    with _lock:
        _state["seq"] += 1
        tid = _state["seq"]
        _state["open"].append({
            "id": tid,
            "chat": int(chat_id),
            "name": signal["name"],
            "symbol": signal.get("symbol"),
            "source": signal.get("source"),
            "direction": signal["direction"],
            "regime": signal.get("regime", "?"),
            "entry": entry_price,
            "expiry": expiry_epoch,
        })
        _save()
        return tid


def due_trades(now_epoch):
    """Сделки, у которых истекла экспирация — пора проверять результат."""
    return [t for t in _state["open"] if t["expiry"] <= now_epoch]


def remove_trade(tid):
    with _lock:
        _state["open"] = [t for t in _state["open"] if t["id"] != tid]
        _save()


# ---------- Статистика ----------

def record(chat_id, result, regime):
    """result: 'win' | 'loss' | 'tie'."""
    with _lock:
        key = str(int(chat_id))
        s = _state["stats"].setdefault(
            key, {"win": 0, "loss": 0, "tie": 0, "regimes": {}}
        )
        s[result] = s.get(result, 0) + 1
        rg = s["regimes"].setdefault(regime, {"win": 0, "loss": 0, "tie": 0})
        rg[result] = rg.get(result, 0) + 1
        _save()


def get_stats(chat_id):
    return _state["stats"].get(str(int(chat_id)))
