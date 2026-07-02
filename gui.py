from __future__ import annotations

import logging
import os
import sys
import threading

import wx
import wx.adv
import wx.lib.agw.customtreectrl as CT

import config
import i18n
import icons
import log_panel
import scanner
import state
from core_impl import (
    InstallWorker,
    build_status_cache,
    get_installed_programs,
    invalidate_caches,
    invalidate_installed_cache,
    is_admin,
    is_program_applicable,
    load_programs_from_json,
    relaunch_as_admin,
)
from dispatch import DispatchMixin
from menu import MenuMixin
from program_editor import ProgramEditorDialog
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

        self.programs_db: dict[str, list[dict]] = load_programs_from_json()

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

        self.installed_names: list[tuple[str, str]] = get_installed_programs(
            state_dict=self._state,
            use_cache=self._installed_cache_enabled,
        )
        self.status_cache: dict[str, tuple[str, str]] = build_status_cache(
            self.programs_db, self.installed_names,
        )
        self.worker: InstallWorker | None = None
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

        # Системный трей: значок, меню и уведомления о завершении установки.
        self._tray = None
        try:
            import tray as _tray_mod
            self._tray = _tray_mod.TrayIcon(self)
        except Exception as e:
            logging.warning(f"Не удалось создать значок в системном трее: {e}")
            self._tray = None

    def init_ui(self) -> None:
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        if os.name == "nt" and not is_admin():
            admin_panel = wx.Panel(panel)
            admin_panel.SetBackgroundColour(wx.Colour(255, 243, 205))
            admin_sizer = wx.BoxSizer(wx.HORIZONTAL)
            warn_text = wx.StaticText(
                admin_panel, label=_("admin.warning"),
            )
            warn_text.SetForegroundColour(wx.Colour(133, 100, 4))
            btn_restart = wx.Button(admin_panel, label=_("btn.restart"))
            btn_restart.Bind(wx.EVT_BUTTON, self._elevate)
            btn_restart.SetToolTip(_("btn.restart.tooltip"))
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

        bottom_sizer = wx.BoxSizer(wx.VERTICAL)
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
        bottom_sizer.Add(
            self.status_label, 0, wx.EXPAND | wx.BOTTOM, 2,
        )
        bottom_sizer.Add(
            self.selection_label, 0, wx.EXPAND | wx.BOTTOM, 5,
        )

        progress_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        progress_row_sizer.Add(
            self.progress_bar, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 15,
        )

        self.btn_cancel = wx.Button(panel, label=_("btn.cancel"))
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.cancel_install)
        self.btn_cancel.SetToolTip(_("btn.cancel.tooltip"))
        self.btn_cancel.Disable()

        self.btn_install = wx.Button(panel, label=_("btn.install"))
        self.btn_install.Bind(wx.EVT_BUTTON, self.start_install)
        self.btn_install.SetToolTip(_("btn.install.tooltip"))

        progress_row_sizer.Add(
            self.btn_cancel, 0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5,
        )
        progress_row_sizer.Add(
            self.btn_install, 0, wx.ALIGN_CENTER_VERTICAL,
        )
        bottom_sizer.Add(progress_row_sizer, 0, wx.EXPAND)

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
        if relaunch_as_admin():
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
                if not is_program_applicable(p):
                    continue
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

    def _on_edit_program(self, program_data: dict, tree_item: CT.CustomTreeItem | None = None) -> None:
        # Determine the initial category for the dialog
        initial_category = ""
        if tree_item and tree_item.IsOk() and tree_item in self.tree_data:
            # Get the category from the tree_item's parent, or from stored _category
            parent_item = self.tree.GetItemParent(tree_item)
            if parent_item and parent_item.IsOk() and parent_item != self.root_item:
                initial_category = self.tree.GetItemText(parent_item)

        dlg = ProgramEditorDialog(self, program_data=program_data)
        # Set the category in the dialog's control for editing
        dlg.category_ctrl.SetValue(initial_category)

        if dlg.ShowModal() == wx.ID_OK:
            updated_program_data = dlg.GetProgramData()
            updated_category = dlg.GetCategory()

            if not updated_program_data.get("name") or not updated_program_data.get("cmd"):
                wx.MessageBox(_("program_editor.error_empty_fields"), _("program_editor.error_title"), wx.OK | wx.ICON_ERROR)
                return

            self._update_programs_db_entry(updated_program_data, updated_category, tree_item)
            scanner.save_merged_to_disk(self.programs_db)
            self.populate_tree(self.search_ctrl.GetValue(), category=self._active_category, status_filter=self._active_status_filter)
            self._set_status(_("program_editor.program_saved"), "success")
        dlg.Destroy()

    def _on_add_program(self, event: wx.CommandEvent, category_name: str | None = None) -> None:
        # Default data for a new program
        default_program_data = {"icon": "icons/system.png"}

        dlg = ProgramEditorDialog(self, program_data=default_program_data)
        if category_name: # Pre-fill category if adding to a specific one
            dlg.category_ctrl.SetValue(category_name)

        if dlg.ShowModal() == wx.ID_OK:
            new_program_data = dlg.GetProgramData()
            new_category = dlg.GetCategory()

            # If category is not explicitly set in dialog, try to guess from cmd or use default
            if not new_category:
                new_category = scanner.guess_category(new_program_data.get("cmd", "")) or _("category.uncategorized") # Use a localized default

            if not new_program_data.get("name") or not new_program_data.get("cmd"):
                wx.MessageBox(_("program_editor.error_empty_fields"), _("program_editor.error_title"), wx.OK | wx.ICON_ERROR)
                return

            self._update_programs_db_entry(new_program_data, new_category, None)
            scanner.save_merged_to_disk(self.programs_db)
            self.populate_tree(self.search_ctrl.GetValue(), category=self._active_category, status_filter=self._active_status_filter)
            self._set_status(_("program_editor.program_added"), "success")
        dlg.Destroy()

    def _on_add_program_to_category(self, event: wx.CommandEvent, category_name: str) -> None:
        self._on_add_program(event, category_name=category_name)

    def _update_programs_db_entry(self, program_data: dict, new_category: str, tree_item: CT.CustomTreeItem | None):
        # Remove old entry if editing existing
        if tree_item and tree_item.IsOk() and tree_item in self.tree_data:
            old_program_data = self.tree_data[tree_item]
            # tree_data хранит копию записи с доп. ключами (_status, _item_id),
            # поэтому сравнение через `in`/`==` не находит оригинал в programs_db
            # и старая запись не удалялась — получался дубликат при редактировании.
            # Ищем оригинал по совпадению name + cmd.
            old_name = old_program_data.get("name")
            old_cmd = old_program_data.get("cmd")
            for cat_name, progs in list(self.programs_db.items()):
                for i, prog in enumerate(progs):
                    if prog.get("name") == old_name and prog.get("cmd") == old_cmd:
                        del progs[i]
                        if not progs:  # Remove category if it becomes empty
                            del self.programs_db[cat_name]
                        break
                else:
                    continue
                break

        # Add new/updated entry to the correct category
        self.programs_db.setdefault(new_category, []).append(program_data)

        # Invalidate caches and refresh statuses for accurate display
        invalidate_caches()
        invalidate_installed_cache(self._state)
        self.installed_names = get_installed_programs(
            state_dict=self._state,
            use_cache=self._installed_cache_enabled,
        )
        self.status_cache = build_status_cache(
            self.programs_db, self.installed_names,
        )


    def on_about(self, event: wx.CommandEvent) -> None:
        """Кастомный диалог «О программе» — информативнее системного AboutBox."""
        dlg = wx.Dialog(self, title=_("about.title"),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        if os.path.exists(config.ICON_FILE):
            try:
                dlg.SetIcon(wx.Icon(config.ICON_FILE, wx.BITMAP_TYPE_ANY))
            except Exception:
                pass

        sizer = wx.BoxSizer(wx.VERTICAL)
        top = wx.BoxSizer(wx.HORIZONTAL)

        icon_bmp = None
        if os.path.exists(config.ICON_FILE):
            try:
                img = wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY).ConvertToImage()
                img.Rescale(96, 96, wx.IMAGE_QUALITY_HIGH)
                icon_bmp = wx.Bitmap(img)
            except Exception:
                icon_bmp = None
        if icon_bmp is not None and icon_bmp.IsOk():
            top.Add(wx.StaticBitmap(dlg, bitmap=icon_bmp), 0, wx.ALL, 15)

        col = wx.BoxSizer(wx.VERTICAL)
        name_lbl = wx.StaticText(dlg, label="MInstAll")
        name_lbl.SetFont(
            wx.Font(20, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        )
        col.Add(name_lbl, 0, wx.BOTTOM, 4)
        ver_lbl = wx.StaticText(
            dlg,
            label=f"{_('about.version_label')}: v{config.APP_VERSION}   (schema v{config.CONFIG_VERSION})",
        )
        ver_lbl.SetForegroundColour(wx.Colour(90, 90, 90))
        col.Add(ver_lbl, 0, wx.BOTTOM, 2)
        col.Add(
            wx.StaticText(
                dlg,
                label=f"{_('about.platform_label')}: " + ("Windows" if os.name == "nt" else "Linux"),
            ),
            0, wx.BOTTOM, 8,
        )
        top.Add(col, 1, wx.EXPAND | wx.TOP | wx.RIGHT, 15)
        sizer.Add(top, 0, wx.EXPAND | wx.BOTTOM, 10)

        desc = wx.StaticText(dlg, label=_("about.description"))
        desc.Wrap(440)
        sizer.Add(desc, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        info = wx.FlexGridSizer(cols=2, hgap=12, vgap=6)
        info.AddGrowableCol(1)

        def lbl(text: str) -> wx.StaticText:
            t = wx.StaticText(dlg, label=text)
            t.SetFont(
                wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            )
            return t

        info.Add(lbl(_("about.license_label")), 0, wx.ALIGN_CENTER_VERTICAL)
        info.Add(wx.StaticText(dlg, label="GPL-3.0-or-later"), 0, wx.EXPAND)
        info.Add(lbl(_("about.tech_label")), 0, wx.ALIGN_CENTER_VERTICAL)
        info.Add(wx.StaticText(dlg, label="wxPython, psutil"), 0, wx.EXPAND)
        info.Add(lbl(_("about.repository_label")), 0, wx.ALIGN_CENTER_VERTICAL)
        try:
            link = wx.adv.HyperlinkCtrl(
                dlg, label="github.com/assassins377/Python_Install",
                url="https://github.com/assassins377/Python_Install",
            )
            info.Add(link, 0, wx.EXPAND)
        except Exception:
            info.Add(
                wx.StaticText(dlg, label="https://github.com/assassins377/Python_Install"),
                0, wx.EXPAND,
            )
        info.Add(lbl(_("about.author_label")), 0, wx.ALIGN_CENTER_VERTICAL | wx.TOP, 6)
        info.Add(
            wx.StaticText(dlg, label=_("about.copyright")),
            0, wx.EXPAND | wx.TOP, 6,
        )
        sizer.Add(info, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        btns = dlg.CreateButtonSizer(wx.OK)
        if btns is not None:
            sizer.Add(btns, 0, wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        dlg.SetSizer(sizer)
        sizer.Fit(dlg)
        dlg.SetMinSize(sizer.GetMinSize())
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def on_show_add_guide(self, event: wx.CommandEvent) -> None:
        """Открывает окно с инструкцией по добавлению программ в каталог."""
        dlg = wx.Dialog(self, title=_("guide.add_program.title"), size=(760, 600))
        sizer = wx.BoxSizer(wx.VERTICAL)
        txt = wx.TextCtrl(
            dlg, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        txt.SetFont(
            wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        )
        txt.SetValue(_("guide.add_program.body"))
        sizer.Add(txt, 1, wx.EXPAND | wx.ALL, 10)
        btns = dlg.CreateButtonSizer(wx.OK)
        sizer.Add(btns, 0, wx.EXPAND | wx.ALL, 10)
        dlg.SetSizer(sizer)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def on_check_program_updates(self, event: wx.CommandEvent) -> None:
        """Проверяет новые версии программ каталога по url (в фоне)."""
        from updater import check_program_update

        candidates = [
            p for progs in self.programs_db.values() for p in progs if p.get("url")
        ]
        if not candidates:
            wx.MessageBox(
                _("program_updates.none"),
                _("menu.check_program_updates"),
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        self._set_status(
            _("program_updates.checking", count=len(candidates)), "progress",
        )
        self.btn_install.Disable()

        def worker() -> None:
            results = []
            for p in candidates:
                try:
                    results.append(check_program_update(p))
                except Exception as e:
                    results.append({
                        "name": p.get("name", ""),
                        "current": p.get("version"),
                        "latest": None,
                        "has_update": False,
                        "url": p.get("url", ""),
                        "error": str(e),
                    })
            wx.CallAfter(self._on_program_updates_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_program_updates_done(self, results: list[dict]) -> None:
        self.btn_install.Enable()
        updates = [r for r in results if r["has_update"]]
        errors = [r for r in results if r["error"]]
        if updates:
            body = "\n".join(
                f"{r['name']}: {r['current']} -> {r['latest']}" for r in updates
            )
            self._set_status(_("program_updates.found", count=len(updates)), "success")
            wx.MessageBox(
                body, _("menu.check_program_updates"),
                wx.OK | wx.ICON_INFORMATION,
            )
        elif errors:
            self._set_status(_("program_updates.error"), "warn")
            wx.MessageBox(
                "\n".join(f"{r['name']}: {r['error']}" for r in errors),
                _("program_updates.error"), wx.OK | wx.ICON_WARNING,
            )
        else:
            self._set_status(_("program_updates.up_to_date"), "success")
            wx.MessageBox(
                _("program_updates.up_to_date"),
                _("menu.check_program_updates"),
                wx.OK | wx.ICON_INFORMATION,
            )

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
