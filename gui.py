from __future__ import annotations

import os
import sys
import subprocess

import wx
import wx.lib.agw.customtreectrl as CT
import wx.adv

import config
import core
import icons
import state


SEVERITY_COLORS: dict[str, wx.Colour] = {
    "info":     wx.Colour(100, 100, 100),
    "progress": wx.Colour(0, 0, 255),
    "warn":     wx.Colour(255, 140, 0),
    "error":    wx.Colour(255, 0, 0),
    "success":  wx.Colour(0, 128, 0),
}


class MInstAllFrame(wx.Frame):
    DEFAULT_SIZE = (820, 650)
    MIN_SIZE = (600, 450)

    def __init__(self) -> None:
        self._state = state.load_state()
        win = self._state.get("window", {})
        size = (win.get("width", self.DEFAULT_SIZE[0]),
                win.get("height", self.DEFAULT_SIZE[1]))

        super().__init__(None, title="Мастер установки программ", size=size)
        self.SetMinSize(self.MIN_SIZE)

        # Восстановление позиции (если есть и попадает в видимый экран)
        if "x" in win and "y" in win:
            pos = wx.Point(win["x"], win["y"])
            if self._is_position_visible(pos, size):
                self.SetPosition(pos)
            else:
                self.Centre()
        else:
            self.Centre()

        # Восстановление развёрнутого состояния
        if win.get("maximized"):
            self.Maximize(True)

        if os.path.exists(config.ICON_FILE):
            try:
                icon = wx.Icon()
                icon.CopyFromBitmap(wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY))
                self.SetIcon(icon)
            except Exception:
                pass

        self.programs_db: dict[str, list[dict]] = core.load_programs_from_json()
        self.installed_names: list[tuple[str, str]] = core.get_installed_programs()
        self.status_cache: dict[str, tuple[str, str]] = core.build_status_cache(
            self.programs_db, self.installed_names
        )
        self.worker: core.InstallWorker | None = None
        self.tree_data: dict = {}
        self._closing = False

        # Единственный таймер debounce поиска — переиспользуется при каждом keystroke
        self._search_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_search_timer, self._search_timer)

        self.create_menu()
        self.init_ui()
        self.populate_tree()
        self._restore_session()
        self.Bind(wx.EVT_CLOSE, self.on_closing)

    # --------------------------------------------------------------
    # Меню
    # --------------------------------------------------------------
    def create_menu(self) -> None:
        menubar = wx.MenuBar()
        help_menu = wx.Menu()

        update_item = help_menu.Append(wx.ID_ANY, "Проверить обновления...",
                                       "Проверить наличие новой версии программы")
        self.Bind(wx.EVT_MENU, self.on_check_update, update_item)
        help_menu.AppendSeparator()
        about_item = help_menu.Append(wx.ID_ABOUT, "О программе...\tF1",
                                      "Показать информацию о программе")
        self.Bind(wx.EVT_MENU, self.on_about, about_item)

        menubar.Append(help_menu, "Справка")
        self.SetMenuBar(menubar)

    def on_about(self, event: wx.CommandEvent) -> None:
        info = wx.adv.AboutDialogInfo()
        info.SetName("MInstAll")
        info.SetVersion(f"v{config.APP_VERSION}")
        info.SetDescription(
            "Универсальный мастер тихой установки программ и системных твиков.\n\n"
            "Позволяет автоматизировать развертывание рабочего окружения "
            "с поддержкой .exe, .msi, .bat, .reg и PowerShell скриптов."
        )
        info.SetCopyright("(C) 2026 Rosa")
        info.SetWebSite("https://github.com/assassins377/minstall_project", "GitHub")

        if os.path.exists(config.ICON_FILE):
            try:
                bmp = wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY)
                img = bmp.ConvertToImage()
                img.Rescale(64, 64, wx.IMAGE_QUALITY_HIGH)
                info.SetIcon(wx.Icon(wx.Bitmap(img)))
            except Exception:
                pass

        wx.adv.AboutBox(info)

    def on_check_update(self, event: wx.CommandEvent) -> None:
        import updater
        self._set_status("Проверка обновлений...", "progress")

        def on_check_done(result: dict) -> None:
            wx.CallAfter(self._handle_update_check_result, result)

        updater.check_for_updates_async(on_check_done)

    def _handle_update_check_result(self, result: dict) -> None:
        import updater

        if "error" in result:
            self._set_status("Не удалось проверить обновления.", "warn")
            wx.MessageBox(f"Не удалось проверить обновления:\n{result['error']}",
                          "Обновление", wx.OK | wx.ICON_WARNING)
            return

        if not result["has_update"]:
            self._set_status("У вас установлена последняя версия.", "success")
            wx.MessageBox("У вас установлена самая актуальная версия.",
                          "Инфо", wx.OK | wx.ICON_INFORMATION)
            return

        msg = f"Доступна новая версия MInstAll (v{result['latest']}).\n"
        if result.get("notes"):
            msg += f"\n{result['notes']}\n"
        msg += "\nСкачать и установить сейчас?"

        dlg = wx.MessageDialog(self, msg, "Обновление", wx.YES_NO | wx.ICON_INFORMATION)
        try:
            choice = dlg.ShowModal()
        finally:
            dlg.Destroy()

        if choice != wx.ID_YES:
            return

        self.btn_install.Disable()
        self.progress_bar.SetValue(0)

        def update_cb(msg: dict) -> None:
            wx.CallAfter(self._on_update_message, msg)

        import threading
        threading.Thread(
            target=updater.download_and_update,
            args=(result, update_cb),
            daemon=True,
        ).start()

    def _on_update_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "status":
            self._set_status(msg["text"], "progress")
        elif t == "progress":
            self.progress_bar.SetValue(msg["percent"])
        elif t == "error":
            self._set_status(msg["text"], "error")
            self.btn_install.Enable()
            wx.MessageBox(msg["text"], "Ошибка обновления", wx.OK | wx.ICON_ERROR)
        elif t == "done":
            self._set_status("Перезапуск...", "progress")

    # --------------------------------------------------------------
    # UI
    # --------------------------------------------------------------
    def init_ui(self) -> None:
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        if os.name == "nt" and not core.is_admin():
            admin_panel = wx.Panel(panel)
            admin_panel.SetBackgroundColour(wx.Colour(255, 243, 205))
            admin_sizer = wx.BoxSizer(wx.HORIZONTAL)
            warn_text = wx.StaticText(
                admin_panel,
                label="⚠ Запущено без прав администратора. Тихая установка может не сработать."
            )
            warn_text.SetForegroundColour(wx.Colour(133, 100, 4))
            btn_restart = wx.Button(admin_panel, label="Перезапустить")
            btn_restart.Bind(wx.EVT_BUTTON, self._elevate)
            admin_sizer.Add(warn_text, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 10)
            admin_sizer.Add(btn_restart, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
            admin_panel.SetSizer(admin_sizer)
            main_sizer.Add(admin_panel, 0, wx.EXPAND | wx.BOTTOM, 10)

        # Поиск
        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_sizer.Add(wx.StaticText(panel, label="🔍 Поиск:"),
                         0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.search_ctrl = wx.TextCtrl(panel)
        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_search_input)
        search_sizer.Add(self.search_ctrl, 1, wx.EXPAND)
        main_sizer.Add(search_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Кнопки выбора и развёртывания
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sel_all = wx.Button(panel, label=f"{config.CHECK_ON} Выбрать недостающее")
        btn_sel_all.Bind(wx.EVT_BUTTON, self.select_all)
        btn_desel_all = wx.Button(panel, label=f"{config.CHECK_OFF} Снять всё")
        btn_desel_all.Bind(wx.EVT_BUTTON, self.deselect_all)
        btn_sizer.Add(btn_sel_all, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_desel_all, 0, wx.RIGHT, 15)

        btn_expand = wx.Button(panel, label="▼ Развернуть всё")
        btn_expand.Bind(wx.EVT_BUTTON, self._expand_all_categories)
        btn_collapse = wx.Button(panel, label="▲ Свернуть всё")
        btn_collapse.Bind(wx.EVT_BUTTON, self._collapse_all_categories)
        btn_sizer.Add(btn_expand, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_collapse, 0)

        main_sizer.Add(btn_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        # Дерево
        self.tree = CT.CustomTreeCtrl(
            panel,
            agwStyle=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT
                     | wx.TR_FULL_ROW_HIGHLIGHT | wx.TR_HAS_VARIABLE_ROW_HEIGHT
        )
        self.root_item = self.tree.AddRoot("Root")
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select)
        main_sizer.Add(self.tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self.il = wx.ImageList(16, 16)
        self.tree.AssignImageList(self.il)
        # Асинхронный загрузчик иконок: иконки декодируются в фоне
        self._icon_loader = icons.IconLoader(self.il, self._on_icon_loaded)
        # path -> список item_id'шек ждущих эту иконку
        self._icon_pending: dict[str, list] = {}

        # Описание
        self.desc_label = wx.StaticText(
            panel, label="Наведите курсор на программу, чтобы увидеть описание."
        )
        self.desc_label.Wrap(740)
        desc_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Описание")
        desc_box.Add(self.desc_label, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(desc_box, 0, wx.EXPAND | wx.ALL, 10)

        # Нижняя панель
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_prog_sizer = wx.BoxSizer(wx.VERTICAL)
        self.status_label = wx.StaticText(panel, label=self._initial_status_text())
        self.status_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self.status_label.SetForegroundColour(SEVERITY_COLORS["info"])
        self.progress_bar = wx.Gauge(panel, range=100)
        status_prog_sizer.Add(self.status_label, 0, wx.EXPAND | wx.BOTTOM, 2)
        status_prog_sizer.Add(self.progress_bar, 0, wx.EXPAND)
        bottom_sizer.Add(status_prog_sizer, 1, wx.EXPAND | wx.RIGHT, 15)

        self.btn_cancel = wx.Button(panel, label="✖ Отменить", size=(-1, 40))
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.cancel_install)
        self.btn_cancel.Disable()

        self.btn_install = wx.Button(panel, label="▶ Установить", size=(-1, 40))
        self.btn_install.Bind(wx.EVT_BUTTON, self.start_install)

        bottom_sizer.Add(self.btn_cancel, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        bottom_sizer.Add(self.btn_install, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(bottom_sizer, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(main_sizer)

    def _elevate(self, event: wx.CommandEvent) -> None:
        if core.relaunch_as_admin():
            self.Destroy()
            sys.exit(0)
        else:
            wx.MessageBox("Не удалось перезапуститься с правами администратора.",
                          "Ошибка", wx.OK | wx.ICON_WARNING)

    @staticmethod
    def _is_position_visible(pos: wx.Point, size: tuple[int, int]) -> bool:
        """Проверяет, что окно с такой позицией/размером попадает хотя бы на один монитор.
        Защищает от ситуации, когда пользователь отключил внешний монитор и сохранённая
        позиция оказалась за пределами доступного пространства."""
        for i in range(wx.Display.GetCount()):
            screen = wx.Display(i).GetGeometry()
            # Достаточно чтобы хотя бы 100×100 пикселей окна попало на экран
            if (pos.x + 100 > screen.x and pos.x < screen.x + screen.width - 100 and
                    pos.y + 30 > screen.y and pos.y < screen.y + screen.height - 30):
                return True
        return False

    def _save_window_state(self) -> None:
        """Сохраняет размер и позицию окна в state.json."""
        if self.IsMaximized():
            # Сохраняем флаг + размер из restore-режима
            self.Restore()
            size = self.GetSize()
            pos = self.GetPosition()
            self._state["window"] = {
                "width": size.width, "height": size.height,
                "x": pos.x, "y": pos.y,
                "maximized": True,
            }
        else:
            size = self.GetSize()
            pos = self.GetPosition()
            self._state["window"] = {
                "width": size.width, "height": size.height,
                "x": pos.x, "y": pos.y,
                "maximized": False,
            }
        state.save_state(self._state)

    # --------------------------------------------------------------
    # Помощники
    # --------------------------------------------------------------
    def _set_status(self, text: str, severity: str = "info") -> None:
        self.status_label.SetLabel(text)
        self.status_label.SetForegroundColour(SEVERITY_COLORS.get(severity, SEVERITY_COLORS["info"]))

    def _initial_status_text(self) -> str:
        installed = outdated = runnable = installable_total = 0
        for progs in self.programs_db.values():
            for p in progs:
                status, _ = self.status_cache.get(p["name"], ("missing", ""))
                if status == "runnable":
                    runnable += 1
                    continue
                installable_total += 1
                if status == "ok":
                    installed += 1
                elif status == "outdated":
                    outdated += 1
        parts = [f"Установлено: {installed} из {installable_total}"]
        if outdated:
            parts.append(f"устарело: {outdated}")
        if runnable:
            parts.append(f"твиков: {runnable}")
        return ". ".join(parts) + ". Готов к работе."

    # --------------------------------------------------------------
    # Поиск с debounce
    # --------------------------------------------------------------
    def _on_search_input(self, event: wx.CommandEvent) -> None:
        self._search_timer.Stop()
        self._search_timer.StartOnce(config.SEARCH_DEBOUNCE_MS)

    def _on_search_timer(self, event: wx.TimerEvent) -> None:
        self.populate_tree(self.search_ctrl.GetValue())

    # --------------------------------------------------------------
    # Дерево
    # --------------------------------------------------------------
    def populate_tree(self, filter_text: str = "") -> None:
        # Запоминаем какие программы были отмечены до пересоздания дерева
        checked_names = set(self._get_checked_program_names())

        self.tree.DeleteChildren(self.root_item)
        self.tree_data.clear()
        # Очищаем pending — старые item_id уже невалидны
        self._icon_pending.clear()
        filter_lower = filter_text.strip().lower()

        for category, programs in self.programs_db.items():
            visible = [p for p in programs
                       if not filter_lower
                       or filter_lower in p["name"].lower()
                       or filter_lower in p.get("desc", "").lower()]
            if not visible:
                continue

            cat_item = self.tree.AppendItem(self.root_item, category, ct_type=0)
            self.tree.SetItemFont(cat_item, wx.Font(10, wx.FONTFAMILY_DEFAULT,
                                                     wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
            self.tree.SetItemTextColour(cat_item, wx.Colour(0, 51, 102))

            for prog in visible:
                status, found_ver = self.status_cache.get(prog["name"], ("missing", ""))
                min_ver = (prog.get("detect") or {}).get("min_version")

                if status == "ok":
                    label = (f"{prog['name']} — установлено (v{found_ver})"
                             if found_ver else f"{prog['name']} — установлено")
                elif status == "outdated":
                    label = f"{prog['name']} — требуется обновление до v{min_ver}"
                elif status == "runnable":
                    label = f"{prog['name']} — действие/твик"
                else:
                    label = prog['name']

                prog_item = self.tree.AppendItem(cat_item, label, ct_type=1)

                if status == "ok":
                    self.tree.SetItemTextColour(prog_item, wx.Colour(34, 139, 34))
                elif status == "outdated":
                    self.tree.SetItemTextColour(prog_item, wx.Colour(217, 119, 6))
                elif status == "runnable":
                    self.tree.SetItemTextColour(prog_item, wx.Colour(106, 27, 154))

                icon_path = core.resolve_path(prog.get("icon", ""))
                if icon_path:
                    index = self._icon_loader.get_or_load(icon_path)
                    if index is not None:
                        # Иконка уже была загружена — ставим сразу
                        self.tree.SetItemImage(prog_item, index)
                    else:
                        # Иконка грузится в фоне — запомним кому её потом проставить
                        self._icon_pending.setdefault(icon_path, []).append(prog_item)

                prog_meta = dict(prog)
                prog_meta["_status"] = status
                prog_meta["_item_id"] = prog_item
                self.tree_data[prog_item] = prog_meta

                # Восстанавливаем галочки после пересоздания дерева
                if prog["name"] in checked_names:
                    self.tree.CheckItem(prog_item, True)

            if not filter_lower:
                self.tree.Expand(cat_item)

        if filter_lower:
            self.tree.ExpandAll()

    def select_all(self, event: wx.CommandEvent) -> None:
        for item, data in self.tree_data.items():
            if data["_status"] != "ok":
                self.tree.CheckItem(item, True)

    def deselect_all(self, event: wx.CommandEvent) -> None:
        for item in self.tree_data.keys():
            self.tree.CheckItem(item, False)

    def _get_checked_program_names(self) -> list[str]:
        """Возвращает имена отмеченных программ (для сохранения сессии)."""
        names = []
        for item, data in self.tree_data.items():
            try:
                if self.tree.IsItemChecked(item):
                    names.append(data["name"])
            except Exception:
                # Item может быть удалён во время вызова
                pass
        return names

    def _restore_session(self) -> None:
        """Восстанавливает галочки и фильтр поиска из state.json."""
        session = self._state.get("session", {})

        # Восстанавливаем фильтр (до populate_tree, чтобы он сразу применился)
        last_filter = session.get("filter", "")
        if last_filter:
            self.search_ctrl.ChangeValue(last_filter)

        # Восстанавливаем галочки
        checked_names = set(session.get("checked", []))
        if checked_names:
            for item, data in self.tree_data.items():
                if data["name"] in checked_names:
                    self.tree.CheckItem(item, True)

    def _save_session(self) -> None:
        """Сохраняет отмеченные программы и фильтр поиска в state.json."""
        self._state["session"] = {
            "checked": self._get_checked_program_names(),
            "filter": self.search_ctrl.GetValue(),
        }
        state.save_state(self._state)

    def _on_icon_loaded(self, icon_path: str, image_index: int) -> None:
        """Колбэк IconLoader — вызывается в UI-потоке когда иконка готова."""
        items = self._icon_pending.pop(icon_path, [])
        for item in items:
            try:
                # Tree item мог быть удалён за время загрузки (populate_tree пересоздаёт)
                self.tree.SetItemImage(item, image_index)
            except Exception:
                pass

    def _expand_all_categories(self, event: wx.CommandEvent) -> None:
        self.tree.ExpandAll()

    def _collapse_all_categories(self, event: wx.CommandEvent) -> None:
        # Только категории (root скрыт)
        child, cookie = self.tree.GetFirstChild(self.root_item)
        while child.IsOk():
            self.tree.Collapse(child)
            child, cookie = self.tree.GetNextChild(self.root_item, cookie)

    def on_tree_select(self, event: wx.TreeEvent) -> None:
        item = event.GetItem()
        data = self.tree_data.get(item)
        if data and "desc" in data:
            self.desc_label.SetLabel(data["desc"])
            self.desc_label.Wrap(740)

    # --------------------------------------------------------------
    # Установка
    # --------------------------------------------------------------
    def start_install(self, event: wx.CommandEvent | None) -> None:
        if self.worker and self.worker.is_alive():
            return

        tasks = [data for item, data in self.tree_data.items()
                 if self.tree.IsItemChecked(item)]
        if not tasks:
            self._set_status("Вы ничего не выбрали!", "error")
            return

        tasks = core.resolve_dependencies(tasks, self.programs_db)

        self.btn_install.Disable()
        self.btn_cancel.Enable()
        self.progress_bar.SetValue(0)

        def dispatch(msg: dict) -> None:
            if not self._closing:
                wx.CallAfter(self.on_worker_message, msg)

        self.worker = core.InstallWorker(tasks, dispatch)
        self.worker.start()

    def on_worker_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "progress":
            self._set_status(msg["text"], msg.get("severity", "info"))
        elif msg_type == "value":
            self.progress_bar.SetValue(msg["percent"])
        elif msg_type == "scroll_to":
            if item := msg.get("item_id"):
                self.tree.ScrollTo(item)
        elif msg_type == "finished":
            self.finish_install(msg)

    def finish_install(self, msg: dict) -> None:
        reboot_needed = msg.get("reboot", False)
        success = msg.get("success", 0)
        fails = msg.get("fails", 0)
        results = msg.get("results", {})

        self.worker = None
        self.btn_install.Enable()
        self.btn_cancel.Disable()

        cancelled = sum(1 for r in results.values() if r == "cancelled")

        lines = []
        if success:
            lines.append(f"Успешно: {success}")
        if fails:
            lines.append(f"Ошибок: {fails}")
        if cancelled:
            lines.append(f"Отменено: {cancelled}")
        if reboot_needed:
            lines.append("Требуется перезагрузка")

        if fails > 0:
            severity = "warn"
        elif cancelled > 0:
            severity = "warn"
        else:
            severity = "success"
        self._set_status(". ".join(lines) + "." if lines else "Готово.", severity)

        core.invalidate_caches()
        self.installed_names = core.get_installed_programs()
        self.status_cache = core.build_status_cache(self.programs_db, self.installed_names)
        self.populate_tree(self.search_ctrl.GetValue())

        rollbacks = msg.get("rollbacks", {})
        rolled_back = [n for n, r in rollbacks.items() if r == "rolled_back"]
        rollback_failed = [n for n, r in rollbacks.items() if r == "rollback_failed"]

        summary_msg = f"Установка завершена.\n\nУспешно: {success}\nОшибок: {fails}"
        if cancelled:
            summary_msg += f"\nОтменено: {cancelled}"
        if rolled_back:
            summary_msg += f"\n\nОткачено ({len(rolled_back)}):\n"
            summary_msg += "\n".join(f"  - {n}" for n in rolled_back)
        if rollback_failed:
            summary_msg += f"\n\nОткат не удался ({len(rollback_failed)}):\n"
            summary_msg += "\n".join(f"  - {n}" for n in rollback_failed)
        if reboot_needed:
            summary_msg += "\n\nТребуется перезагрузка."
        wx.MessageBox(summary_msg, "Результат", wx.OK | wx.ICON_INFORMATION)

        if reboot_needed:
            dlg = wx.MessageDialog(
                self,
                "Установщики требуют перезагрузки компьютера.\n\nПерезагрузить сейчас?",
                "Перезагрузка", wx.YES_NO | wx.ICON_QUESTION
            )
            try:
                choice = dlg.ShowModal()
            finally:
                dlg.Destroy()

            if choice == wx.ID_YES:
                try:
                    subprocess.run(
                        ["shutdown", "/r", "/t", "10", "/c",
                         "Мастер установки: перезагрузка"],
                        check=False
                    )
                except Exception:
                    pass

    def cancel_install(self, event: wx.CommandEvent) -> None:
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self._set_status("Отмена установки...", "warn")
            self.btn_cancel.Disable()

    def on_closing(self, event: wx.CloseEvent) -> None:
        if self.worker and self.worker.is_alive():
            dlg = wx.MessageDialog(self, "Установка выполняется. Прервать и выйти?",
                                   "Выход", wx.YES_NO | wx.ICON_WARNING)
            try:
                choice = dlg.ShowModal()
            finally:
                dlg.Destroy()

            if choice == wx.ID_YES:
                self._closing = True
                self.worker.stop()
                self.worker.join(timeout=5.0)
                self._save_window_state()
                self._save_session()
                event.Skip()
            else:
                event.Veto()
        else:
            self._save_window_state()
            self._save_session()
            event.Skip()
