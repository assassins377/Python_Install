from __future__ import annotations

import os
import sys
import threading

import wx
import wx.adv
import wx.lib.agw.customtreectrl as CT

import config
import core
import i18n
import icons
import log_panel
import scanner
import state
from dispatch import DispatchMixin
from menu import MenuMixin
from tree import TreeMixin

_ = i18n.t


SEVERITY_COLORS: dict[str, wx.Colour] = {
    "info":     wx.Colour(100, 100, 100),
    "progress": wx.Colour(0, 0, 255),
    "warn":     wx.Colour(255, 140, 0),
    "error":    wx.Colour(255, 0, 0),
    "success":  wx.Colour(0, 128, 0),
}


class MInstAllFrame(wx.Frame, MenuMixin, TreeMixin, DispatchMixin):
    DEFAULT_SIZE = (820, 650)
    MIN_SIZE = (600, 450)

    def __init__(self) -> None:
        self._state = state.load_state()
        win = self._state.get("window", {})
        size = (
            win.get("width", self.DEFAULT_SIZE[0]),
            win.get("height", self.DEFAULT_SIZE[1]),
        )

        super().__init__(None, title=_("app.title"), size=size)
        self.SetMinSize(self.MIN_SIZE)

        if "x" in win and "y" in win:
            pos = wx.Point(win["x"], win["y"])
            if self._is_position_visible(pos, size):
                self.SetPosition(pos)
            else:
                self.Centre()
        else:
            self.Centre()

        if win.get("maximized"):
            self.Maximize(True)

        if os.path.exists(config.ICON_FILE):
            try:
                icon = wx.Icon()
                icon.CopyFromBitmap(
                    wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY),
                )
                self.SetIcon(icon)
            except Exception:
                pass

        self.programs_db: dict[str, list[dict]] = core.load_programs_from_json()

        prefs_early = self._state.get("prefs", {})
        self._installed_cache_enabled = prefs_early.get(
            "installed_cache", True,
        )
        self._gen_from_scan = prefs_early.get("gen_from_scan", True)
        self._autoscan_enabled = prefs_early.get("autoscan", True)
        self._hide_missing = prefs_early.get("hide_missing", False)
        self._active_category = prefs_early.get("active_category", "")
        self._active_status_filter = prefs_early.get("active_status_filter", "")
        self._last_scan_new: list[dict] = []
        self._catalog_dirty = False

        if self._gen_from_scan:
            self.programs_db = scanner.build_catalog_from_scan(
                existing_db=self.programs_db,
            )
            self._catalog_dirty = True
        elif self._autoscan_enabled:
            self.programs_db, self._last_scan_new = scanner.scan_and_merge(
                self.programs_db,
            )
            if self._last_scan_new:
                self._catalog_dirty = True

        self.installed_names: list[tuple[str, str]] = core.get_installed_programs(
            state_dict=self._state,
            use_cache=self._installed_cache_enabled,
        )
        self.status_cache: dict[str, tuple[str, str]] = core.build_status_cache(
            self.programs_db, self.installed_names,
        )
        self.worker: core.InstallWorker | None = None
        self.tree_data: dict = {}
        self._closing = False

        self._search_timer = wx.Timer(self)
        self._search_timer.Bind(wx.EVT_TIMER, self._on_search_timer)

        self.CreateStatusBar(1)
        self.SetStatusText("")

        self._watcher_interval_ms = prefs_early.get(
            "watcher_interval_ms",
            config.WATCHER_POLL_INTERVAL_MS
            if config.WATCHER_ENABLED
            else 0,
        )

        self.create_menu()
        self.init_ui()
        self._populate_category_combo()
        self._populate_status_combo()
        self.populate_tree()
        self._restore_session()
        self.Bind(wx.EVT_CLOSE, self.on_closing)

        self._dir_snapshot = scanner.directory_snapshot()
        self._watcher_timer = wx.Timer(self)
        self._watcher_timer.Bind(wx.EVT_TIMER, self._on_watcher_tick)
        if self._watcher_interval_ms > 0:
            self._watcher_timer.Start(self._watcher_interval_ms)

    def init_ui(self) -> None:
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        if os.name == "nt" and not core.is_admin():
            admin_panel = wx.Panel(panel)
            admin_panel.SetBackgroundColour(wx.Colour(255, 243, 205))
            admin_sizer = wx.BoxSizer(wx.HORIZONTAL)
            warn_text = wx.StaticText(
                admin_panel, label=_("admin.warning"),
            )
            warn_text.SetForegroundColour(wx.Colour(133, 100, 4))
            btn_restart = wx.Button(admin_panel, label=_("btn.restart"))
            btn_restart.Bind(wx.EVT_BUTTON, self._elevate)
            admin_sizer.Add(
                warn_text, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 10,
            )
            admin_sizer.Add(
                btn_restart, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5,
            )
            admin_panel.SetSizer(admin_sizer)
            main_sizer.Add(admin_panel, 0, wx.EXPAND | wx.BOTTOM, 10)

        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_sizer.Add(
            wx.StaticText(panel, label=_("toolbar.search")),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5,
        )
        self.search_ctrl = wx.TextCtrl(panel)
        self.search_ctrl.SetHint(_("toolbar.search.hint"))
        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_search_input)
        search_sizer.Add(self.search_ctrl, 1, wx.EXPAND)
        main_sizer.Add(
            search_sizer, 0,
            wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10,
        )

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sel_all = wx.Button(panel, label=_("toolbar.select_missing"))
        btn_sel_all.Bind(wx.EVT_BUTTON, self.select_all)
        btn_desel_all = wx.Button(panel, label=_("toolbar.deselect_all"))
        btn_desel_all.Bind(wx.EVT_BUTTON, self.deselect_all)
        btn_sizer.Add(btn_sel_all, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_desel_all, 0)
        btn_sizer.AddStretchSpacer()
        cat_label = wx.StaticText(panel, label="📂")
        btn_sizer.Add(cat_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 5)
        self.category_combo = wx.ComboBox(
            panel, style=wx.CB_READONLY, size=(180, -1),
        )
        self.category_combo.Bind(wx.EVT_COMBOBOX, self._on_category_changed)
        btn_sizer.Add(self.category_combo, 0, wx.ALIGN_CENTER_VERTICAL)

        status_label = wx.StaticText(panel, label="🏷️")
        btn_sizer.Add(status_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 5)
        self.status_combo = wx.ComboBox(
            panel, style=wx.CB_READONLY, size=(150, -1),
        )
        self.status_combo.Bind(wx.EVT_COMBOBOX, self._on_status_filter_changed)
        btn_sizer.Add(self.status_combo, 0, wx.ALIGN_CENTER_VERTICAL)

        main_sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self._splitter = wx.SplitterWindow(
            panel, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH,
        )
        self._splitter.SetMinimumPaneSize(80)

        self.tree = CT.CustomTreeCtrl(
            self._splitter,
            agwStyle=wx.TR_DEFAULT_STYLE
            | wx.TR_HIDE_ROOT
            | wx.TR_FULL_ROW_HIGHLIGHT
            | wx.TR_HAS_VARIABLE_ROW_HEIGHT,
        )
        self.root_item = self.tree.AddRoot("Root")
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select)
        self.tree.Bind(
            wx.EVT_TREE_ITEM_RIGHT_CLICK, self._on_tree_right_click,
        )
        self.tree.Bind(CT.EVT_TREE_ITEM_CHECKED, self._on_tree_item_check)
        self.tree.Bind(wx.EVT_MOTION, self._on_tree_motion)
        self._last_tooltip_item = None

        self._log_panel = log_panel.LogPanel(self._splitter)
        self._log_panel.Hide()
        self._splitter.Initialize(self.tree)

        main_sizer.Add(
            self._splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10,
        )

        self.il = wx.ImageList(16, 16)
        self.tree.AssignImageList(self.il)
        self._icon_loader = icons.IconLoader(self.il, self._on_icon_loaded)
        self._icon_pending: dict[str, list] = {}

        self.desc_label = wx.StaticText(panel, label=_("desc.hint"))
        self.desc_label.Wrap(740)
        desc_box = wx.StaticBoxSizer(wx.VERTICAL, panel, _("desc.title"))
        desc_box.Add(self.desc_label, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(desc_box, 0, wx.EXPAND | wx.ALL, 10)

        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_prog_sizer = wx.BoxSizer(wx.VERTICAL)
        self.status_label = wx.StaticText(
            panel, label=self._initial_status_text(),
        )
        self.status_label.SetFont(
            wx.Font(
                9, wx.FONTFAMILY_DEFAULT,
                wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD,
            ),
        )
        self.status_label.SetForegroundColour(SEVERITY_COLORS["info"])

        self.selection_label = wx.StaticText(
            panel, label=_("selection.none"),
        )
        self.selection_label.SetFont(
            wx.Font(
                8, wx.FONTFAMILY_DEFAULT,
                wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL,
            ),
        )
        self.selection_label.SetForegroundColour(wx.Colour(100, 100, 100))

        self.progress_bar = wx.Gauge(panel, range=100)
        status_prog_sizer.Add(
            self.status_label, 0, wx.EXPAND | wx.BOTTOM, 2,
        )
        status_prog_sizer.Add(
            self.selection_label, 0, wx.EXPAND | wx.BOTTOM, 2,
        )
        status_prog_sizer.Add(self.progress_bar, 0, wx.EXPAND)
        bottom_sizer.Add(
            status_prog_sizer, 1, wx.EXPAND | wx.RIGHT, 15,
        )

        self.btn_cancel = wx.Button(
            panel, label=_("btn.cancel"), size=(-1, 40),
        )
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.cancel_install)
        self.btn_cancel.Disable()

        self.btn_install = wx.Button(
            panel, label=_("btn.install"), size=(-1, 40),
        )
        self.btn_install.Bind(wx.EVT_BUTTON, self.start_install)

        bottom_sizer.Add(
            self.btn_cancel, 0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5,
        )
        bottom_sizer.Add(
            self.btn_install, 0, wx.ALIGN_CENTER_VERTICAL,
        )
        main_sizer.Add(bottom_sizer, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(main_sizer)

    def _populate_category_combo(self) -> None:
        items = [_("category.all")]
        items.extend(sorted(self.programs_db.keys()))
        self.category_combo.SetItems(items)
        idx = 0
        stored = self._state.get("prefs", {}).get("active_category", self._active_category)
        if stored and stored in self.programs_db:
            idx = items.index(stored)
        self._active_category = stored if stored else ""
        self.category_combo.Select(idx)

    def _on_category_changed(self, event: wx.CommandEvent) -> None:
        choice = self.category_combo.GetValue()
        selected = "" if choice == _("category.all") else choice
        if selected == self._active_category:
            return
        self._active_category = selected
        prefs = self._state.setdefault("prefs", {})
        prefs["active_category"] = selected
        state.save_state(self._state)
        self.populate_tree(self.search_ctrl.GetValue(), category=selected)

    def _populate_status_combo(self) -> None:
        items = [
            _("status.all_filter"),
            _("status.missing_filter"),
            _("status.outdated_filter"),
            _("status.installed_filter"),
            _("status.runnable_filter"),
        ]
        self.status_combo.SetItems(items)
        idx = 0
        stored = self._state.get("prefs", {}).get("active_status_filter", self._active_status_filter)
        if stored in ["missing", "outdated", "ok", "runnable"]:
            mapping = {"missing": 1, "outdated": 2, "ok": 3, "runnable": 4}
            idx = mapping.get(stored, 0)
        self._active_status_filter = stored if stored else ""
        self.status_combo.Select(idx)

    def _on_status_filter_changed(self, event: wx.CommandEvent) -> None:
        choice_idx = self.status_combo.GetSelection()
        mapping = {0: "", 1: "missing", 2: "outdated", 3: "ok", 4: "runnable"}
        selected = mapping.get(choice_idx, "")
        if selected == self._active_status_filter:
            return
        self._active_status_filter = selected
        prefs = self._state.setdefault("prefs", {})
        prefs["active_status_filter"] = selected
        state.save_state(self._state)
        self.populate_tree(
            self.search_ctrl.GetValue(),
            category=self._active_category,
            status_filter=selected,
        )

    def _elevate(self, event: wx.CommandEvent) -> None:
        if core.relaunch_as_admin():
            self.Destroy()
            sys.exit(0)
        else:
            wx.MessageBox(
                "Не удалось перезапуститься с правами администратора.",
                "Ошибка", wx.OK | wx.ICON_WARNING,
            )

    @staticmethod
    def _is_position_visible(
        pos: wx.Point, size: tuple[int, int],
    ) -> bool:
        for i in range(wx.Display.GetCount()):
            screen = wx.Display(i).GetGeometry()
            if (
                pos.x + 100 > screen.x
                and pos.x < screen.x + screen.width - 100
                and pos.y + 30 > screen.y
                and pos.y < screen.y + screen.height - 30
            ):
                return True
        return False

    def _save_window_state(self) -> None:
        if self.IsMaximized():
            self.Restore()
            size = self.GetSize()
            pos = self.GetPosition()
            self._state["window"] = {
                "width": size.width,
                "height": size.height,
                "x": pos.x,
                "y": pos.y,
                "maximized": True,
            }
        else:
            size = self.GetSize()
            pos = self.GetPosition()
            self._state["window"] = {
                "width": size.width,
                "height": size.height,
                "x": pos.x,
                "y": pos.y,
                "maximized": False,
            }
        state.save_state(self._state)

    def _set_status(self, text: str, severity: str = "info") -> None:
        self.status_label.SetLabel(text)
        self.status_label.SetForegroundColour(
            SEVERITY_COLORS.get(severity, SEVERITY_COLORS["info"]),
        )

    def _initial_status_text(self) -> str:
        installed = outdated = runnable = installable_total = 0
        for progs in self.programs_db.values():
            for p in progs:
                status, _v = self.status_cache.get(
                    p["name"], ("missing", ""),
                )
                if status == "runnable":
                    runnable += 1
                    continue
                installable_total += 1
                if status == "ok":
                    installed += 1
                elif status == "outdated":
                    outdated += 1
        parts = [
            _(
                "status.installed_count",
                installed=installed,
                total=installable_total,
            ),
        ]
        if outdated:
            parts.append(_("status.outdated_count", outdated=outdated))
        if runnable:
            parts.append(
                _("status.runnable_count", runnable=runnable),
            )
        return ". ".join(parts) + ". " + _("app.ready")

    def on_about(self, event: wx.CommandEvent) -> None:
        info = wx.adv.AboutDialogInfo()
        info.SetName("MInstAll")
        info.SetVersion(f"v{config.APP_VERSION}")
        info.SetDescription(_("about.description"))
        info.SetCopyright(_("about.copyright"))
        info.SetWebSite(
            "https://github.com/assassins377/minstall_project", "GitHub",
        )

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
            wx.MessageBox(
                f"Не удалось проверить обновления:\n{result['error']}",
                "Обновление", wx.OK | wx.ICON_WARNING,
            )
            return

        if not result["has_update"]:
            self._set_status(
                "У вас установлена последняя версия.", "success",
            )
            wx.MessageBox(
                "У вас установлена самая актуальная версия.",
                "Инфо", wx.OK | wx.ICON_INFORMATION,
            )
            return

        msg = f"Доступна новая версия MInstAll (v{result['latest']}).\n"
        if result.get("notes"):
            msg += f"\n{result['notes']}\n"
        msg += "\nСкачать и установить сейчас?"

        dlg = wx.MessageDialog(
            self, msg, "Обновление",
            wx.YES_NO | wx.ICON_INFORMATION,
        )
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
            wx.MessageBox(
                msg["text"], "Ошибка обновления", wx.OK | wx.ICON_ERROR,
            )
        elif t == "done":
            self._set_status("Перезапуск...", "progress")
