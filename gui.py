import os
import sys
import subprocess
import threading
import wx
import wx.lib.agw.customtreectrl as CT
import wx.adv

import config
import core

class MInstAllFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Мастер установки программ (wxPython)", size=(820, 650))
        
        if os.path.exists(config.ICON_FILE):
            try:
                icon = wx.Icon()
                icon.CopyFromBitmap(wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY))
                self.SetIcon(icon)
            except Exception:
                pass

        self.programs_db = core.load_programs_from_json()
        self.installed_names = core.get_installed_programs()
        self.worker = None
        self.tree_data = {} 
        
        self.create_menu()  # Инициализация верхнего меню
        self.init_ui()
        self.populate_tree()
        self.Bind(wx.EVT_CLOSE, self.on_closing)

    def create_menu(self):
        """Создает классическое верхнее меню (MenuBar)"""
        menubar = wx.MenuBar()
        
        help_menu = wx.Menu()
        
        # Пункт проверки обновлений
        update_item = help_menu.Append(wx.ID_ANY, "Проверить обновления...", "Проверить наличие новой версии программы")
        self.Bind(wx.EVT_MENU, self.on_check_update, update_item)
        
        help_menu.AppendSeparator()
        
        # Пункт "О программе"
        about_item = help_menu.Append(wx.ID_ABOUT, "О программе...\tF1", "Показать информацию о программе")
        self.Bind(wx.EVT_MENU, self.on_about, about_item)
        
        menubar.Append(help_menu, "Справка")
        self.SetMenuBar(menubar)

    def on_about(self, event):
        """Обработчик нажатия на 'О программе'"""
        info = wx.adv.AboutDialogInfo()
        info.SetName("MInstAll")
        info.SetVersion(f"v{config.CONFIG_VERSION}.0")
        info.SetDescription(
            "Универсальный мастер тихой установки программ и системных твиков.\n\n"
            "Позволяет автоматизировать развертывание рабочего окружения "
            "с поддержкой .exe, .msi, .bat, .reg и PowerShell скриптов."
        )
        info.SetCopyright("(C) 2026 Rosa")
        info.SetWebSite("https://github.com/", "Страница проекта на GitHub")
        
        if os.path.exists(config.ICON_FILE):
            try:
                bmp = wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY)
                img = bmp.ConvertToImage()
                img.Rescale(64, 64, wx.IMAGE_QUALITY_HIGH)
                info.SetIcon(wx.Icon(wx.Bitmap(img)))
            except Exception:
                pass

        wx.adv.AboutBox(info)

    def on_check_update(self, event):
        """Обработчик нажатия на 'Проверить обновления'"""
        import updater
        
        self.status_label.SetLabel("Проверка обновлений...")
        self.status_label.SetForegroundColour(wx.Colour(0, 0, 255))
        
        has_update, latest_ver = updater.check_for_updates(config.CONFIG_VERSION)
        
        if has_update:
            dlg = wx.MessageDialog(
                self, 
                f"Доступна новая версия MInstAll (v{latest_ver}).\nСкачать и установить сейчас?", 
                "Обновление", 
                wx.YES_NO | wx.ICON_INFORMATION
            )
            if dlg.ShowModal() == wx.ID_YES:
                def update_progress(msg):
                    wx.CallAfter(self.status_label.SetLabel, msg)
                
                threading.Thread(target=updater.download_and_update, args=(update_progress,), daemon=True).start()
        else:
            self.status_label.SetLabel("У вас установлена последняя версия.")
            self.status_label.SetForegroundColour(wx.Colour(0, 128, 0))
            wx.MessageBox("У вас установлена самая актуальная версия.", "Инфо", wx.OK | wx.ICON_INFORMATION)

    def init_ui(self):
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        if os.name == "nt" and not core.is_admin():
            admin_panel = wx.Panel(panel)
            admin_panel.SetBackgroundColour(wx.Colour(255, 243, 205))
            admin_sizer = wx.BoxSizer(wx.HORIZONTAL)
            warn_text = wx.StaticText(admin_panel, label="⚠ Запущено без прав администратора. Тихая установка может не сработать.")
            warn_text.SetForegroundColour(wx.Colour(133, 100, 4))
            btn_restart = wx.Button(admin_panel, label="Перезапустить")
            btn_restart.Bind(wx.EVT_BUTTON, self._elevate)
            admin_sizer.Add(warn_text, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 10)
            admin_sizer.Add(btn_restart, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
            admin_panel.SetSizer(admin_sizer)
            main_sizer.Add(admin_panel, 0, wx.EXPAND | wx.BOTTOM, 10)

        # Панель поиска
        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_sizer.Add(wx.StaticText(panel, label="🔍 Поиск:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.search_ctrl = wx.TextCtrl(panel)
        self.search_ctrl.Bind(wx.EVT_TEXT, self.on_search_changed)
        search_sizer.Add(self.search_ctrl, 1, wx.EXPAND)
        main_sizer.Add(search_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Кнопки Выбрать/Снять
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sel_all = wx.Button(panel, label=f"{config.CHECK_ON} Выбрать недостающее")
        btn_sel_all.Bind(wx.EVT_BUTTON, self.select_all)
        btn_desel_all = wx.Button(panel, label=f"{config.CHECK_OFF} Снять всё")
        btn_desel_all.Bind(wx.EVT_BUTTON, self.deselect_all)
        btn_sizer.Add(btn_sel_all, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_desel_all, 0)
        main_sizer.Add(btn_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        # Дерево программ
        self.tree = CT.CustomTreeCtrl(panel, agwStyle=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_FULL_ROW_HIGHLIGHT | wx.TR_HAS_VARIABLE_ROW_HEIGHT)
        self.root_item = self.tree.AddRoot("Root")
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select)
        main_sizer.Add(self.tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self.il = wx.ImageList(16, 16)
        self.tree.AssignImageList(self.il)
        self.icon_cache = {}

        # Описание программ
        self.desc_label = wx.StaticText(panel, label="Наведите курсор на программу, чтобы увидеть описание.")
        self.desc_label.Wrap(740)
        desc_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Описание")
        desc_box.Add(self.desc_label, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(desc_box, 0, wx.EXPAND | wx.ALL, 10)

        # Нижняя панель (Прогресс и управление)
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_prog_sizer = wx.BoxSizer(wx.VERTICAL)
        self.status_label = wx.StaticText(panel, label=self._initial_status_text())
        self.status_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self.status_label.SetForegroundColour(wx.Colour(100, 100, 100))
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

    def _elevate(self, event):
        if core.relaunch_as_admin():
            self.Destroy()
            sys.exit(0)
        else:
            wx.MessageBox("Не удалось перезапуститься с правами администратора.", "Ошибка", wx.OK | wx.ICON_WARNING)

    def _initial_status_text(self):
        installed = outdated = installable_total = 0
        for progs in self.programs_db.values():
            for p in progs:
                status, _ = core.check_status(p, self.installed_names)
                if status == "runnable": continue
                installable_total += 1
                if status == "ok": installed += 1
                elif status == "outdated": outdated += 1
        parts = [f"Установлено: {installed} из {installable_total}"]
        if outdated: parts.append(f"устарело: {outdated}")
        return ". ".join(parts) + ". Готов к работе."

    def populate_tree(self, filter_text=""):
        self.tree.DeleteChildren(self.root_item)
        self.tree_data.clear()
        filter_lower = filter_text.strip().lower()

        for category, programs in self.programs_db.items():
            visible = [p for p in programs if not filter_lower or filter_lower in p["name"].lower()]
            if not visible: continue

            cat_item = self.tree.AppendItem(self.root_item, category, ct_type=0)
            self.tree.SetItemFont(cat_item, wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
            self.tree.SetItemTextColour(cat_item, wx.Colour(0, 51, 102))

            for prog in visible:
                status, found_ver = core.check_status(prog, self.installed_names)
                min_ver = (prog.get("detect") or {}).get("min_version")

                if status == "ok": label = f"{prog['name']} — установлено (v{found_ver})" if found_ver else f"{prog['name']} — установлено"
                elif status == "outdated": label = f"{prog['name']} — требуется обновление до v{min_ver}"
                elif status == "runnable": label = f"{prog['name']} — действие/твик"
                else: label = prog['name']

                prog_item = self.tree.AppendItem(cat_item, label, ct_type=1)
                
                if status == "ok": self.tree.SetItemTextColour(prog_item, wx.Colour(128, 128, 128))
                elif status == "outdated": self.tree.SetItemTextColour(prog_item, wx.Colour(217, 119, 6))
                elif status == "runnable": self.tree.SetItemTextColour(prog_item, wx.Colour(106, 27, 154))

                icon_path = core.resolve_path(prog.get("icon", ""))
                if icon_path and os.path.exists(icon_path):
                    if icon_path not in self.icon_cache:
                        bmp = wx.Bitmap(icon_path, wx.BITMAP_TYPE_ANY)
                        if bmp.IsOk():
                            img = bmp.ConvertToImage()
                            img.Rescale(16, 16, wx.IMAGE_QUALITY_HIGH)
                            self.icon_cache[icon_path] = self.il.Add(wx.Bitmap(img))
                    if icon_path in self.icon_cache:
                        self.tree.SetItemImage(prog_item, self.icon_cache[icon_path])

                prog_meta = dict(prog)
                prog_meta["_status"] = status
                prog_meta["_item_id"] = prog_item
                self.tree_data[prog_item] = prog_meta

            if not filter_lower: self.tree.Expand(cat_item)
            else: self.tree.ExpandAll()

    def on_search_changed(self, event):
        self.populate_tree(self.search_ctrl.GetValue())

    def select_all(self, event):
        for item, data in self.tree_data.items():
            if data["_status"] != "ok": self.tree.CheckItem(item, True)

    def deselect_all(self, event):
        for item in self.tree_data.keys(): self.tree.CheckItem(item, False)

    def on_tree_select(self, event):
        item = event.GetItem()
        data = self.tree_data.get(item)
        if data and "desc" in data:
            self.desc_label.SetLabel(data["desc"])
            self.desc_label.Wrap(740)

    def start_install(self, event):
        tasks = [data for item, data in self.tree_data.items() if self.tree.IsItemChecked(item)]
        if not tasks:
            self.status_label.SetLabel("Вы ничего не выбрали!")
            self.status_label.SetForegroundColour(wx.Colour(255, 0, 0))
            return

        self.btn_install.Disable()
        self.btn_cancel.Enable()
        self.progress_bar.SetValue(0)

        self.worker = core.InstallWorker(tasks, self.on_worker_message)
        self.worker.start()

    def on_worker_message(self, msg):
        msg_type = msg.get("type")
        if msg_type == "progress":
            self.status_label.SetLabel(msg["text"])
            if "color" in msg: self.status_label.SetForegroundColour(msg["color"])
        elif msg_type == "value": self.progress_bar.SetValue(msg["percent"])
        elif msg_type == "scroll_to":
            if item := msg.get("item_id"): self.tree.ScrollTo(item)
        elif msg_type == "finished": self.finish_install(msg)

    def finish_install(self, msg):
        reboot_needed, success, fails, results = msg.get("reboot", False), msg.get("success", 0), msg.get("fails", 0), msg.get("results", {})

        self.worker = None
        self.btn_install.Enable()
        self.btn_cancel.Disable()

        cancelled = 0
        for item, result in results.items():
            current_text = self.tree.GetItemText(item)
            for sym in (config.RESULT_OK, config.RESULT_FAIL, config.RESULT_CANCELLED): current_text = current_text.replace(f" {sym}", "")
            if result == "ok": self.tree.SetItemText(item, current_text + f" {config.RESULT_OK}")
            elif result == "fail": self.tree.SetItemText(item, current_text + f" {config.RESULT_FAIL}")
            elif result == "cancelled":
                cancelled += 1
                self.tree.SetItemText(item, current_text + f" {config.RESULT_CANCELLED}")

        lines = [f"Успешно: {success}"] if success else []
        if fails: lines.append(f"Ошибок: {fails}")
        if cancelled: lines.append(f"Отменено: {cancelled}")
        if reboot_needed: lines.append("Требуется перезагрузка")
        
        self.status_label.SetLabel(". ".join(lines) + "." if lines else "Готово.")
        self.status_label.SetForegroundColour(wx.Colour(255, 140, 0) if fails > 0 else (wx.Colour(255, 165, 0) if cancelled > 0 else wx.Colour(0, 128, 0)))
        
        self.installed_names = core.get_installed_programs()

        summary_msg = f"Установка завершена.\n\nУспешно: {success}\nОшибок: {fails}" + (f"\nОтменено: {cancelled}" if cancelled else "") + ("\n\nТребуется перезагрузка." if reboot_needed else "")
        wx.MessageBox(summary_msg, "Результат", wx.OK | wx.ICON_INFORMATION)

        if reboot_needed and wx.MessageDialog(self, "Установщики требуют перезагрузки компьютера.\n\nПерезагрузить сейчас?", "Перезагрузка", wx.YES_NO | wx.ICON_QUESTION).ShowModal() == wx.ID_YES:
            try: subprocess.run(["shutdown", "/r", "/t", "10", "/c", "Мастер установки: перезагрузка"], check=False)
            except Exception: pass

    def cancel_install(self, event):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.status_label.SetLabel("Отмена установки...")
            self.status_label.SetForegroundColour(wx.Colour(255, 140, 0))
            self.btn_cancel.Disable()

    def on_closing(self, event):
        if self.worker and self.worker.is_alive():
            if wx.MessageDialog(self, "Установка выполняется. Прервать и выйти?", "Выход", wx.YES_NO | wx.ICON_WARNING).ShowModal() == wx.ID_YES:
                self.worker.stop()
                self.worker.join(timeout=1.0)
                event.Skip()
        else: event.Skip()