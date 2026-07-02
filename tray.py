"""Системный трей: значок в области уведомлений, меню и оповещения.

Сворачивает/показывает главное окно, даёт быстрый доступ к установке/отмене
и выходу, а также показывает всплывающее уведомление по завершении установки.
"""
from __future__ import annotations

import os

import wx
import wx.adv

import config
import i18n

_ = i18n.t


class TrayIcon(wx.adv.TaskBarIcon):
    """Значок приложения в системном трее."""

    def __init__(self, frame: wx.Frame) -> None:
        super().__init__()
        self._frame = frame
        self._set_icon()
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._on_left_dclick)
        # Убираем значок, когда фрейм уничтожается (закрытие через крестик и т.д.)
        frame.Bind(wx.EVT_WINDOW_DESTROY, self._on_frame_destroy)

    def _set_icon(self) -> None:
        tip = _("tray.tooltip")
        if os.path.exists(config.ICON_FILE):
            try:
                self.SetIcon(wx.Icon(config.ICON_FILE, wx.BITMAP_TYPE_ANY), tip)
                return
            except Exception:
                pass
        # Фолбэк: пустая иконка, чтобы трей всё равно появился.
        try:
            self.SetIcon(wx.Icon(wx.Bitmap(16, 16)), tip)
        except Exception:
            pass

    # --- Меню по правому клику ---
    def CreatePopupMenu(self) -> wx.Menu:
        menu = wx.Menu()
        shown = self._frame.IsShown()
        show_item = menu.Append(wx.ID_ANY, _("tray.hide") if shown else _("tray.show"))
        self.Bind(wx.EVT_MENU, self._on_toggle_show, show_item)

        menu.AppendSeparator()

        install_item = menu.Append(wx.ID_ANY, _("btn.install"))
        self.Bind(wx.EVT_MENU, lambda e: self._frame.start_install(None), install_item)
        cancel_item = menu.Append(wx.ID_ANY, _("btn.cancel"))
        self.Bind(wx.EVT_MENU, lambda e: self._frame.cancel_install(None), cancel_item)

        menu.AppendSeparator()

        exit_item = menu.Append(wx.ID_EXIT, _("menu.exit"))
        self.Bind(wx.EVT_MENU, self._on_exit, exit_item)
        return menu

    # --- Обработчики ---
    def _on_toggle_show(self, event: wx.CommandEvent) -> None:
        self._toggle_show()

    def _on_left_dclick(self, event: wx.TaskBarIconEvent) -> None:
        self._toggle_show()

    def _toggle_show(self) -> None:
        f = self._frame
        if f.IsShown():
            f.Hide()
        else:
            f.Show()
            f.Raise()
            try:
                f.Iconize(False)
            except Exception:
                pass

    def _on_exit(self, event: wx.CommandEvent) -> None:
        # Инициируем штатное закрытие окна (on_closing сохранит состояние).
        self._frame.Close()

    def _on_frame_destroy(self, event: wx.WindowDestroyEvent) -> None:
        try:
            self.RemoveIcon()
        except Exception:
            pass
        event.Skip()

    def notify(self, title: str, message: str) -> None:
        """Показывает всплывающее уведомление в трее (если поддерживается)."""
        try:
            self.ShowBalloon(title, message)
        except Exception:
            pass