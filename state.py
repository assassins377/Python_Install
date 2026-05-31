"""Хранение состояния приложения (размер окна, последний фильтр и т.д.) в %APPDATA%."""
from __future__ import annotations

import json
import logging
import os

import config

STATE_FILE = os.path.join(config.LOG_DIR, "state.json")


def load_state() -> dict:
    """Загружает сохранённое состояние. При ошибке возвращает {}."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logging.warning(f"Некорректная структура {STATE_FILE}, игнорируем")
        return {}
    except Exception as e:
        logging.warning(f"Не удалось прочитать {STATE_FILE}: {e}")
        return {}


def save_state(state: dict) -> None:
    """Сохраняет состояние. Ошибки логируются, но не пробрасываются."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning(f"Не удалось сохранить {STATE_FILE}: {e}")
