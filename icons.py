"""Асинхронная загрузка и кеширование иконок программ.

Загрузка bitmap из файла + Rescale до 16x16 блокирует UI на ~5-20 мс на иконку.
При 50+ программах это заметные тормоза при старте/фильтрации.

Этот модуль грузит иконки в фоновом потоке и через wx.CallAfter обновляет
ImageList, не блокируя UI.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable

import wx


class IconLoader:
    """Асинхронный загрузчик иконок с кешем."""

    def __init__(self, image_list: wx.ImageList, on_loaded: Callable[[str, int], None]) -> None:
        """
        image_list   — wx.ImageList куда добавляются загруженные иконки
        on_loaded(path, image_index) — колбэк, вызывается в UI-потоке когда иконка готова
        """
        self._image_list = image_list
        self._on_loaded = on_loaded
        self._cache: dict[str, int] = {}      # path -> index в ImageList
        self._loading: set[str] = set()       # пути в процессе загрузки
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._worker_thread: threading.Thread | None = None

    def get_or_load(self, icon_path: str) -> int | None:
        """
        Возвращает индекс иконки в ImageList если она уже загружена.
        Иначе ставит её в очередь на фоновую загрузку и возвращает None.
        Колбэк on_loaded будет вызван когда иконка будет готова.
        """
        if not icon_path or not os.path.exists(icon_path):
            return None

        with self._lock:
            if icon_path in self._cache:
                return self._cache[icon_path]
            if icon_path in self._loading:
                return None
            self._loading.add(icon_path)
            self._queue.append(icon_path)
            self._ensure_worker()

        return None

    def _ensure_worker(self) -> None:
        """Запускает воркер, если он ещё не запущен."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        """Фоновая обработка очереди — декодирует и масштабирует bitmap."""
        while True:
            with self._lock:
                if not self._queue:
                    return
                path = self._queue.pop(0)

            try:
                # Декодирование можно делать в фоне, но создание wx.Bitmap
                # требует GUI thread на некоторых платформах. Мы используем
                # wx.Image — он thread-safe для чтения файла.
                img = wx.Image(path, wx.BITMAP_TYPE_ANY)
                if not img.IsOk():
                    logging.warning(f"Не удалось декодировать иконку: {path}")
                    with self._lock:
                        self._loading.discard(path)
                    continue
                img.Rescale(16, 16, wx.IMAGE_QUALITY_HIGH)
            except Exception as e:
                logging.warning(f"Ошибка загрузки иконки {path}: {e}")
                with self._lock:
                    self._loading.discard(path)
                continue

            # Финальные шаги (Add в ImageList и вызов колбэка) — в UI-потоке,
            # т.к. они трогают wx-объекты которые на macOS/Windows требуют main thread
            wx.CallAfter(self._finalize, path, img)

    def _finalize(self, path: str, img: wx.Image) -> None:
        """Вызывается в UI-потоке: добавляет bitmap в ImageList и нотифицирует."""
        with self._lock:
            self._loading.discard(path)
            if path in self._cache:
                # Другой вызов уже добавил — выходим
                return
            try:
                index = self._image_list.Add(wx.Bitmap(img))
                self._cache[path] = index
            except Exception as e:
                logging.warning(f"Не удалось добавить иконку в ImageList {path}: {e}")
                return

        try:
            self._on_loaded(path, index)
        except Exception as e:
            logging.warning(f"Колбэк on_loaded упал для {path}: {e}")
