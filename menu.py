from __future__ import annotations

import wx

import config
import i18n
import profiles
import scanner
import state
from core_impl import (
    build_status_cache,
    invalidate_installed_cache,
    load_programs_from_json,
)

_ = i18n.t


class MenuMixin:
    _parallel_menu_item: wx.MenuItem
    _parallel_enabled: bool
    _autoscan_menu_item: wx.MenuItem
    _autoscan_enabled: bool
    _hide_missing_menu_item: wx.MenuItem
    _hide_missing: bool
    _gen_from_scan_menu_item: wx.MenuItem
    _gen_from_scan: bool
    _installed_cache_menu_item: wx.MenuItem
    _installed_cache_enabled: bool
    _log_menu_item: wx.MenuItem
    _lang_radio_items: dict[str, wx.MenuItem]
    _watcher_radio_items: dict[int, wx.MenuItem]
    _watcher_interval_ms: int

    _state: dict
    _watcher_timer: wx.Timer
    _splitter: wx.SplitterWindow
    _log_panel: wx.Window
    tree: wx.TreeCtrl
    search_ctrl: wx.TextCtrl
    programs_db: dict
    worker: object
    _dir_snapshot: object
    installed_names: list
    status_cache: dict
    _catalog_dirty: bool
    _set_status: callable
    populate_tree: callable
    _update_selection_counter: callable
    tree_data: dict

    def create_menu(self) -> None:
        menubar = wx.MenuBar()

        file_menu = wx.Menu()
        add_prog_item = file_menu.Append(wx.ID_ANY, _("menu.add_program"))
        self.Bind(wx.EVT_MENU, self._on_add_program, add_prog_item)
        save_catalog_item = file_menu.Append(
            wx.ID_ANY, _("menu.save_catalog"), _("menu.save_catalog.tooltip")
        )
        self.Bind(wx.EVT_MENU, self._on_save_catalog, save_catalog_item)
        file_menu.AppendSeparator()
        exit_item = file_menu.Append(wx.ID_EXIT, _("menu.exit"))
        self.Bind(wx.EVT_MENU, self.on_closing, exit_item)
        menubar.Append(file_menu, _("menu.file"))

        settings_menu = wx.Menu()
        self._parallel_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.parallel"), _("menu.parallel.tooltip")
        )
        prefs = self._state.get("prefs", {})
        self._parallel_enabled = prefs.get(
            "parallel_install", config.PARALLEL_INSTALL_ENABLED
        )
        self._parallel_menu_item.Check(self._parallel_enabled)
        self.Bind(wx.EVT_MENU, self._on_toggle_parallel, self._parallel_menu_item)

        settings_menu.AppendSeparator()

        lang_menu = wx.Menu()
        current_lang_pref = prefs.get("language", "auto")
        self._lang_radio_items = {}

        auto_item = lang_menu.AppendRadioItem(
            wx.ID_ANY, _("menu.language.auto")
        )
        self._lang_radio_items["auto"] = auto_item
        self.Bind(wx.EVT_MENU, lambda e: self._on_set_language("auto"), auto_item)

        lang_menu.AppendSeparator()

        for code, name in i18n.SUPPORTED_LANGUAGES.items():
            item = lang_menu.AppendRadioItem(wx.ID_ANY, name)
            self._lang_radio_items[code] = item
            self.Bind(wx.EVT_MENU, lambda e, c=code: self._on_set_language(c), item)

        if current_lang_pref in self._lang_radio_items:
            self._lang_radio_items[current_lang_pref].Check(True)

        self._autoscan_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.autoscan"), _("menu.autoscan.tooltip")
        )
        self._autoscan_menu_item.Check(self._autoscan_enabled)
        self.Bind(wx.EVT_MENU, self._on_toggle_autoscan, self._autoscan_menu_item)

        self._gen_from_scan_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.gen_from_scan"), _("menu.gen_from_scan.tooltip")
        )
        self._gen_from_scan_menu_item.Check(self._gen_from_scan)
        self.Bind(
            wx.EVT_MENU, self._on_toggle_gen_from_scan, self._gen_from_scan_menu_item,
        )

        self._hide_missing_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.hide_missing"), _("menu.hide_missing.tooltip")
        )
        self._hide_missing_menu_item.Check(self._hide_missing)
        self.Bind(
            wx.EVT_MENU, self._on_toggle_hide_missing, self._hide_missing_menu_item,
        )

        watcher_menu = wx.Menu()
        self._watcher_radio_items = {}
        for interval_ms in config.WATCHER_INTERVALS_MS:
            if interval_ms == 0:
                label = _("menu.watcher.off")
            else:
                label = _("menu.watcher.interval_sec", n=interval_ms // 1000)
            item = watcher_menu.AppendRadioItem(wx.ID_ANY, label)
            self._watcher_radio_items[interval_ms] = item
            self.Bind(
                wx.EVT_MENU,
                lambda e, ms=interval_ms: self._on_set_watcher_interval(ms),
                item,
            )
        if self._watcher_interval_ms in self._watcher_radio_items:
            self._watcher_radio_items[self._watcher_interval_ms].Check(True)
        settings_menu.AppendSubMenu(watcher_menu, _("menu.watcher"))

        self._installed_cache_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.installed_cache"),
            _("menu.installed_cache.tooltip"),
        )
        self._installed_cache_menu_item.Check(self._installed_cache_enabled)
        self.Bind(
            wx.EVT_MENU, self._on_toggle_installed_cache, self._installed_cache_menu_item,
        )

        settings_menu.AppendSeparator()

        rescan_item = settings_menu.Append(
            wx.ID_ANY, _("menu.rescan"), _("menu.rescan.tooltip")
        )
        self.Bind(wx.EVT_MENU, self._on_rescan, rescan_item)

        settings_menu.AppendSubMenu(lang_menu, _("menu.language"))
        menubar.Append(settings_menu, _("menu.settings"))

        profiles_menu = wx.Menu()
        loaded_profiles = profiles.list_profiles()
        if not loaded_profiles:
            empty = profiles_menu.Append(
                wx.ID_ANY, _("menu.profile.empty")
            )
            empty.Enable(False)
        else:
            for profile in loaded_profiles:
                found, _missing = profiles.resolve_profile_programs(
                    profile, self.programs_db,
                )
                total = len(profile.get("programs", []))
                avail = len(found)

                if avail < total:
                    label = _(
                        "profile.menu_label_with_avail",
                        name=profile["name"], avail=avail, count=total,
                    )
                else:
                    label = _(
                        "profile.menu_label",
                        name=profile["name"], count=total,
                    )

                desc = profile.get("description", "")
                programs_preview = ", ".join(profile.get("programs", [])[:5])
                if len(profile.get("programs", [])) > 5:
                    programs_preview += "…"
                help_text = (
                    f"{desc} — {programs_preview}"
                    if desc else programs_preview
                )

                item = profiles_menu.Append(wx.ID_ANY, label, help_text)
                self.Bind(
                    wx.EVT_MENU,
                    lambda e, p=profile: self._on_apply_profile(p),
                    item,
                )
        menubar.Append(profiles_menu, _("menu.profiles"))

        help_menu = wx.Menu()
        update_item = help_menu.Append(
            wx.ID_ANY, _("menu.check_updates"),
            _("menu.check_updates.tooltip"),
        )
        self.Bind(wx.EVT_MENU, self.on_check_update, update_item)

        self._log_menu_item = help_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.log"), _("menu.log.tooltip"),
        )
        self.Bind(wx.EVT_MENU, self._on_toggle_log, self._log_menu_item)

        help_menu.AppendSeparator()
        about_item = help_menu.Append(
            wx.ID_ABOUT, _("menu.about"), _("menu.about.tooltip"),
        )
        self.Bind(wx.EVT_MENU, self.on_about, about_item)
        menubar.Append(help_menu, _("menu.help"))

        self.SetMenuBar(menubar)

    def _on_toggle_parallel(self, event: wx.CommandEvent) -> None:
        self._parallel_enabled = self._parallel_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["parallel_install"] = self._parallel_enabled
        state.save_state(self._state)
        mode = (
            _("status.mode_parallel")
            if self._parallel_enabled
            else _("status.mode_sequential")
        )
        self._set_status(
            _("status.parallel_mode", mode=mode), "info"
        )

    def _on_toggle_autoscan(self, event: wx.CommandEvent) -> None:
        self._autoscan_enabled = self._autoscan_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["autoscan"] = self._autoscan_enabled
        state.save_state(self._state)

    def _on_toggle_hide_missing(self, event: wx.CommandEvent) -> None:
        self._hide_missing = self._hide_missing_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["hide_missing"] = self._hide_missing
        state.save_state(self._state)
        cat = getattr(self, '_active_category', '')
        self.populate_tree(self.search_ctrl.GetValue(), category=cat)

    def _on_set_watcher_interval(self, interval_ms: int) -> None:
        self._watcher_interval_ms = interval_ms
        prefs = self._state.setdefault("prefs", {})
        prefs["watcher_interval_ms"] = interval_ms
        state.save_state(self._state)
        if self._watcher_timer.IsRunning():
            self._watcher_timer.Stop()
        if interval_ms > 0:
            self._watcher_timer.Start(interval_ms)

    def _on_toggle_installed_cache(self, event: wx.CommandEvent) -> None:
        self._installed_cache_enabled = (
            self._installed_cache_menu_item.IsChecked()
        )
        prefs = self._state.setdefault("prefs", {})
        prefs["installed_cache"] = self._installed_cache_enabled
        if not self._installed_cache_enabled:
            invalidate_installed_cache(self._state)
        state.save_state(self._state)

    def _on_toggle_gen_from_scan(self, event: wx.CommandEvent) -> None:
        self._gen_from_scan = self._gen_from_scan_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["gen_from_scan"] = self._gen_from_scan
        state.save_state(self._state)

        if self._gen_from_scan:
            self.programs_db = scanner.build_catalog_from_scan(
                existing_db=self.programs_db,
            )
            self.status_cache = build_status_cache(
                self.programs_db, self.installed_names,
            )
            cat = getattr(self, '_active_category', '')
            self.populate_tree(
                self.search_ctrl.GetValue(),
                category=cat,
            )
            self._catalog_dirty = True
            total = sum(len(v) for v in self.programs_db.values())
            self._set_status(
                _("scan.saved", count=total).replace(
                    "сохранён", "перегенерирован"
                ),
                "success",
            )

    def _on_watcher_tick(self, event: wx.TimerEvent) -> None:
        if self.worker and self.worker.is_alive():
            return

        current = scanner.directory_snapshot()
        if current == self._dir_snapshot:
            return
        self._dir_snapshot = current

        if self._gen_from_scan:
            self.programs_db = scanner.build_catalog_from_scan(
                existing_db=load_programs_from_json(),
            )
        else:
            self.programs_db, _merged = scanner.scan_and_merge(
                load_programs_from_json(),
            )

        self.status_cache = build_status_cache(
            self.programs_db, self.installed_names,
        )
        cat = getattr(self, '_active_category', '')
        self.populate_tree(self.search_ctrl.GetValue(), category=cat)
        self._update_selection_counter()

    def _on_rescan(self, event: wx.CommandEvent) -> None:
        self.programs_db, new_entries = scanner.scan_and_merge(
            self.programs_db,
        )
        self._last_scan_new = new_entries

        if not new_entries:
            self._set_status(_("scan.no_new"), "info")
            return

        self.status_cache = build_status_cache(
            self.programs_db, self.installed_names,
        )
        self._catalog_dirty = True
        cat = getattr(self, '_active_category', '')
        self.populate_tree(self.search_ctrl.GetValue(), category=cat)

        self._set_status(
            _("scan.new_found", count=len(new_entries)), "success",
        )
        names_by_cat: dict[str, list[str]] = {}
        for entry in new_entries:
            names_by_cat.setdefault(
                entry["_category"], [],
            ).append(entry["name"])
        lines = [
            f"[{cat}]\n  • " + "\n  • ".join(names)
            for cat, names in names_by_cat.items()
        ]
        wx.MessageBox(
            "\n\n".join(lines),
            _("scan.new_list_title"),
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_save_catalog(self, event: wx.CommandEvent) -> None:
        if scanner.save_merged_to_disk(self.programs_db):
            total = sum(len(v) for v in self.programs_db.values())
            self._set_status(_("scan.saved", count=total), "success")
            self._catalog_dirty = False
        else:
            self._set_status(_("scan.save_failed"), "error")
            wx.MessageBox(
                _("scan.save_failed"), "Error", wx.OK | wx.ICON_ERROR,
            )

    def _on_toggle_log(self, event: wx.CommandEvent) -> None:
        show = self._log_menu_item.IsChecked()
        if show:
            total_h = self._splitter.GetSize().height
            sash = max(150, int(total_h * 0.65))
            self._log_panel.Show()
            self._splitter.SplitHorizontally(
                self.tree, self._log_panel, sash,
            )
            self._log_panel.start()
        else:
            self._log_panel.stop()
            self._splitter.Unsplit(self._log_panel)
            self._log_panel.Hide()

    def _on_apply_profile(self, profile: dict) -> None:
        found, missing = profiles.resolve_profile_programs(
            profile, self.programs_db,
        )
        found_names = {p["name"] for p in found}

        for item, data in self.tree_data.items():
            self.tree.CheckItem(item, data["name"] in found_names)

        self._set_status(
            _("profile.applied", name=profile["name"], count=len(found_names)),
            "info",
        )
        self._update_selection_counter()
        if missing:
            wx.MessageBox(
                _(
                    "profile.missing",
                    name=profile["name"],
                    names=", ".join(missing),
                ),
                _("menu.profiles"),
                wx.OK | wx.ICON_WARNING,
            )

    def _on_set_language(self, lang_code: str) -> None:
        prefs = self._state.setdefault("prefs", {})
        prefs["language"] = lang_code
        state.save_state(self._state)
        self._set_status(_("status.lang_changed"), "warn")
        wx.MessageBox(
            _("status.lang_changed"),
            _("menu.language"),
            wx.OK | wx.ICON_INFORMATION,
        )
