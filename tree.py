from __future__ import annotations

import contextlib
import os
import subprocess

import wx
import wx.adv
import wx.lib.agw.customtreectrl as CT

import config
import i18n
import icons
import stats
from core_impl import (
    build_status_cache,
    find_latest_install_log,
    get_installed_programs,
    invalidate_caches,
    invalidate_installed_cache,
    is_installer_available,
    is_program_applicable,
    resolve_path,
    run_uninstall,
)
from utils import _normalize_cmd_paths

_ = i18n.t


class TreeMixin:
    tree: CT.CustomTreeCtrl
    root_item: wx.TreeItemId
    tree_data: dict
    programs_db: dict
    _state: dict

    search_ctrl: wx.TextCtrl
    desc_label: wx.StaticText
    selection_label: wx.StaticText
    _set_status: callable
    _update_selection_counter: callable

    _hide_missing: bool
    status_cache: dict
    _icon_loader: icons.IconLoader
    _icon_pending: dict[str, list]
    _search_timer: wx.Timer
    _last_tooltip_item: wx.TreeItemId | None
    _active_category: str = ""

    def _get_or_create_category_path(self, category_name: str) -> wx.TreeItemId:
        parts = [p.strip() for p in category_name.split(" / ")]

        parent = self.root_item
        for part in parts:
            child, cookie = self.tree.GetFirstChild(parent)
            found = None
            while child is not None and child.IsOk():
                if self.tree.GetItemText(child) == part:
                    found = child
                    break
                child, cookie = self.tree.GetNextChild(parent, cookie)

            if found is None:
                found = self.tree.AppendItem(parent, part, ct_type=0)
                self.tree.SetItemFont(
                    found,
                    wx.Font(
                        10, wx.FONTFAMILY_DEFAULT,
                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD,
                    ),
                )
                self.tree.SetItemTextColour(found, wx.Colour(0, 51, 102))
            parent = found

        return parent

    def populate_tree(self, filter_text: str = "", category: str = "", status_filter: str | None = None) -> None:
        if status_filter is None:
            status_filter = getattr(self, "_active_status_filter", "")

        checked_names = set(self._get_checked_program_names())

        self.tree.DeleteChildren(self.root_item)
        self.tree_data.clear()
        self._icon_pending.clear()
        filter_lower = filter_text.strip().lower()
        cat_filter = None
        if category:
            cat_filter = {category}

        for cat_name, programs in self.programs_db.items():
            if cat_filter is not None and cat_name not in cat_filter:
                continue
            visible: list[dict] = []
            for p in programs:
                if not is_program_applicable(p):
                    continue
                if filter_lower and not (
                    filter_lower in p["name"].lower()
                    or filter_lower in p.get("desc", "").lower()
                ):
                    continue
                if self._hide_missing and not is_installer_available(p):
                    continue

                status = self.status_cache.get(p["name"], ("missing", ""))[0]
                if status_filter and status != status_filter:
                    continue

                visible.append(p)
            if not visible:
                continue

            cat_item = self._get_or_create_category_path(cat_name)

            for prog in visible:
                status, found_ver = self.status_cache.get(
                    prog["name"], ("missing", ""),
                )
                min_ver = (prog.get("detect") or {}).get("min_version")
                available = is_installer_available(prog)

                if status == "ok":
                    label = (
                        _("tree.installed_ver", name=prog["name"], ver=found_ver)
                        if found_ver
                        else _("tree.installed", name=prog["name"])
                    )
                elif status == "outdated":
                    label = _("tree.outdated", name=prog["name"], ver=min_ver)
                elif status == "runnable":
                    label = _("tree.runnable", name=prog["name"])
                elif prog.get("version"):
                    label = _("tree.with_version", name=prog["name"], ver=prog["version"])
                else:
                    label = prog["name"]

                if not available and status != "ok":
                    label += _("tree.file_missing")

                prog_item = self.tree.AppendItem(cat_item, label, ct_type=1)

                if not available and status != "ok":
                    self.tree.SetItemTextColour(
                        prog_item, wx.Colour(160, 160, 160),
                    )
                elif status == "ok":
                    self.tree.SetItemTextColour(
                        prog_item, wx.Colour(34, 139, 34),
                    )
                elif status == "outdated":
                    self.tree.SetItemTextColour(
                        prog_item, wx.Colour(217, 119, 6),
                    )
                elif status == "runnable":
                    self.tree.SetItemTextColour(
                        prog_item, wx.Colour(106, 27, 154),
                    )

                icon_path = icons.resolve_program_icon(prog, resolve_path)
                if not icon_path:
                    fallback = resolve_path(
                        prog.get("icon") or "icons/system.png",
                    )
                    if os.path.exists(fallback):
                        icon_path = fallback

                if icon_path:
                    index = self._icon_loader.get_or_load(icon_path)
                    if index is not None:
                        self.tree.SetItemImage(prog_item, index)
                    else:
                        self._icon_pending.setdefault(
                            icon_path, [],
                        ).append(prog_item)

                prog_meta = dict(prog)
                prog_meta["_status"] = status
                prog_meta["_item_id"] = prog_item
                self.tree_data[prog_item] = prog_meta

                if prog["name"] in checked_names:
                    self.tree.CheckItem(prog_item, True)

            self.tree.SetItemText(cat_item, f"{cat_name} ({len(visible)})")

        if filter_lower:
            self.tree.ExpandAll()
        else:
            child, cookie = self.tree.GetFirstChild(self.root_item)
            while child is not None and child.IsOk():
                self.tree.Expand(child)
                child, cookie = self.tree.GetNextChild(self.root_item, cookie)

    def select_all(self, event: wx.CommandEvent) -> None:
        for item, data in self.tree_data.items():
            if data["_status"] == "ok":
                continue
            if not is_installer_available(data):
                continue
            self.tree.CheckItem(item, True)
        self._update_selection_counter()

    def deselect_all(self, event: wx.CommandEvent) -> None:
        for item in self.tree_data:
            self.tree.CheckItem(item, False)
        self._update_selection_counter()

    def _get_checked_program_names(self) -> list[str]:
        names = []
        for item, data in self.tree_data.items():
            try:
                if self.tree.IsItemChecked(item):
                    names.append(data["name"])
            except Exception:
                pass
        return names

    def _restore_session(self) -> None:
        session = self._state.get("session", {})

        last_filter = session.get("filter", "")
        if last_filter:
            self.search_ctrl.ChangeValue(last_filter)

        checked_names = set(session.get("checked", []))
        if checked_names:
            for item, data in self.tree_data.items():
                if data["name"] in checked_names:
                    self.tree.CheckItem(item, True)

        self._update_selection_counter()

    def _save_session(self) -> None:
        self._state["session"] = {
            "checked": self._get_checked_program_names(),
            "filter": self.search_ctrl.GetValue(),
        }
        import state
        state.save_state(self._state)

    def _on_icon_loaded(self, icon_path: str, image_index: int) -> None:
        items = self._icon_pending.pop(icon_path, [])
        for item in items:
            with contextlib.suppress(Exception):
                self.tree.SetItemImage(item, image_index)

    def on_tree_select(self, event: wx.TreeEvent) -> None:
        item = event.GetItem()
        data = self.tree_data.get(item)
        if data and "desc" in data:
            self.desc_label.SetLabel(data["desc"])
            self.desc_label.Wrap(740)

    def _on_tree_item_check(self, event) -> None:
        self._update_selection_counter()
        event.Skip()

    def _on_tree_motion(self, event: wx.MouseEvent) -> None:
        event.Skip()
        pos = event.GetPosition()
        hit_item, flags = self.tree.HitTest(pos)
        if hit_item is None or not hit_item.IsOk():
            self._last_tooltip_item = None
            self.tree.SetToolTip(None)
            return

        if hit_item == self._last_tooltip_item:
            return
        self._last_tooltip_item = hit_item

        data = self.tree_data.get(hit_item)
        if not data:
            self.tree.SetToolTip(None)
            return

        lines: list[str] = [data["name"]]

        desc = data.get("desc", "").strip()
        if desc:
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append("")
            lines.append(desc)

        status = data.get("_status", "missing")
        status_text = {
            "ok": "✓ Установлено",
            "outdated": "↑ Требуется обновление",
            "runnable": "→ Действие/твик",
            "missing": "○ Не установлено",
        }.get(status, "")
        if status_text:
            lines.append("")
            lines.append(status_text)

        meta_lines: list[str] = []
        if deps := data.get("depends_on"):
            meta_lines.append(f"Зависит от: {', '.join(deps)}")
        if (retry := data.get("retry", 0)) > 0:
            meta_lines.append(f"Повторов при ошибке: {retry}")
        if not is_installer_available(data):
            meta_lines.append("⚠ Файл инсталлятора отсутствует")

        if meta_lines:
            lines.append("")
            lines.extend(meta_lines)

        tooltip = wx.ToolTip("\n".join(lines))
        tooltip.SetDelay(500)
        self.tree.SetToolTip(tooltip)

    def _update_selection_counter(self) -> None:
        selected = [
            data
            for item, data in self.tree_data.items()
            if self.tree.IsItemChecked(item)
        ]
        count, total_size, total_time = stats.selection_summary(
            selected, self._state, t=_,
        )

        if count == 0:
            self.selection_label.SetLabel(_("selection.none"))
            return

        size_str = (
            stats.format_size(total_size, t=_)
            if total_size > 0 else ""
        )
        time_str = (
            stats.format_duration(total_time, t=_)
            if total_time is not None else ""
        )

        if size_str and time_str:
            text = _(
                "selection.summary",
                count=count, size=size_str, time=time_str,
            )
        elif size_str:
            text = _(
                "selection.summary_no_time",
                count=count, size=size_str,
            )
        else:
            text = _(
                "selection.summary_count_only", count=count,
            )

        self.selection_label.SetLabel(text)

    def _on_search_input(self, event: wx.CommandEvent) -> None:
        self._search_timer.Stop()
        self._search_timer.StartOnce(config.SEARCH_DEBOUNCE_MS)

    def _on_search_timer(self, event: wx.TimerEvent) -> None:
        self.populate_tree(
            self.search_ctrl.GetValue(),
            category=getattr(self, '_active_category', ''),
        )

    def _on_tree_right_click(self, event: wx.TreeEvent) -> None:
        item = event.GetItem()
        data = self.tree_data.get(item)
        if not data:
            if item.IsOk() and item != self.root_item:
                category_name = self.tree.GetItemText(item)
                menu = wx.Menu()
                add_item = menu.Append(
                    wx.ID_ANY,
                    _("ctx.add_program_to_category").format(category=category_name),
                )
                self.Bind(
                    wx.EVT_MENU,
                    lambda e, cat=category_name: self._on_add_program(e, cat),
                    add_item,
                )
                self.PopupMenu(menu)
                menu.Destroy()
            return

        self.tree.SelectItem(item)

        menu = wx.Menu()

        edit_item = menu.Append(wx.ID_ANY, _("ctx.edit_program"))
        self.Bind(
            wx.EVT_MENU,
            lambda e, d=data, it=item: self._on_edit_program(d, it),
            edit_item,
        )
        menu.AppendSeparator()

        only_item = menu.Append(wx.ID_ANY, _("ctx.only_this"))
        self.Bind(
            wx.EVT_MENU,
            lambda e, it=item: self._ctx_only_this(it),
            only_item,
        )

        menu.AppendSeparator()

        if data.get("uninstall_cmd") and data.get("_status") in ("ok", "outdated"):
            uninstall_item = menu.Append(wx.ID_ANY, _("ctx.uninstall"))
            self.Bind(
                wx.EVT_MENU,
                lambda e, it=item: self._ctx_uninstall(it),
                uninstall_item,
            )
            menu.AppendSeparator()

        open_folder = menu.Append(wx.ID_ANY, _("ctx.open_folder"))
        self.Bind(
            wx.EVT_MENU,
            lambda e, d=data: self._ctx_open_folder(d),
            open_folder,
        )

        copy_name = menu.Append(wx.ID_ANY, _("ctx.copy_name"))
        self.Bind(
            wx.EVT_MENU,
            lambda e, d=data: self._ctx_copy(d["name"]),
            copy_name,
        )

        copy_cmd = menu.Append(wx.ID_ANY, _("ctx.copy_cmd"))
        self.Bind(
            wx.EVT_MENU,
            lambda e, d=data: self._ctx_copy(d.get("cmd", "")),
            copy_cmd,
        )

        menu.AppendSeparator()

        prog_log_path = find_latest_install_log(data["name"])
        if prog_log_path:
            open_prog_log = menu.Append(wx.ID_ANY, _("ctx.open_prog_log"))
            self.Bind(
                wx.EVT_MENU,
                lambda e, path=prog_log_path: self._ctx_open_prog_log(path),
                open_prog_log,
            )

        open_log = menu.Append(wx.ID_ANY, _("ctx.open_log"))
        self.Bind(wx.EVT_MENU, lambda e: self._ctx_open_log(), open_log)

        self.PopupMenu(menu)
        menu.Destroy()

    def _ctx_only_this(self, item) -> None:
        for it in self.tree_data:
            self.tree.CheckItem(it, False)
        self.tree.CheckItem(item, True)
        self._set_status("Выбрана только одна программа", "info")

    def _ctx_open_folder(self, data: dict) -> None:
        cmd = data.get("cmd", "")
        if not cmd:
            return
        try:
            import shlex

            cmd_clean = _normalize_cmd_paths(cmd)
            parts = shlex.split(cmd_clean, posix=(os.name != "nt"))
            if not parts:
                return
            script_path = resolve_path(parts[0])
            folder = os.path.dirname(script_path)
            if not os.path.isdir(folder):
                wx.MessageBox(
                    f"Папка не существует:\n{folder}",
                    "Ошибка", wx.OK | wx.ICON_WARNING,
                )
                return

            if os.name == "nt":
                if os.path.exists(script_path):
                    subprocess.Popen(["explorer", "/select,", script_path])
                else:
                    subprocess.Popen(["explorer", folder])
            else:
                subprocess.Popen(
                    ["xdg-open", folder],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            wx.MessageBox(
                f"Не удалось открыть папку:\n{e}",
                "Ошибка", wx.OK | wx.ICON_WARNING,
            )

    def _ctx_copy(self, text: str) -> None:
        if not text:
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                self._set_status(f"Скопировано: {text[:50]}", "info")
            finally:
                wx.TheClipboard.Close()

    def _ctx_open_log(self) -> None:
        if not os.path.exists(config.LOG_FILE):
            wx.MessageBox(
                "Лог-файл ещё не создан.",
                "Инфо", wx.OK | wx.ICON_INFORMATION,
            )
            return
        try:
            if os.name == "nt":
                os.startfile(config.LOG_FILE)
            else:
                subprocess.Popen(
                    ["xdg-open", config.LOG_FILE],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            wx.MessageBox(
                f"Не удалось открыть лог:\n{e}",
                "Ошибка", wx.OK | wx.ICON_WARNING,
            )

    def _ctx_uninstall(self, item) -> None:
        data = self.tree_data.get(item)
        if not data:
            return

        name = data["name"]
        cmd = data.get("uninstall_cmd", "")

        confirm_msg = _("dialog.uninstall.confirm", name=name, cmd=cmd)
        dlg = wx.MessageDialog(
            None,
            confirm_msg,
            _("dialog.uninstall.title"),
            wx.YES_NO | wx.ICON_QUESTION,
        )
        try:
            choice = dlg.ShowModal()
        finally:
            dlg.Destroy()

        if choice != wx.ID_YES:
            return

        # Запускаем в фоне
        self.tree.Disable()
        self._set_status(_("status.uninstalling", name=name), "progress")

        def worker_uninstall():
            success = run_uninstall(data)
            wx.CallAfter(self._on_uninstall_done, data, success)

        import threading
        threading.Thread(target=worker_uninstall, daemon=True).start()

    def _on_uninstall_done(self, data: dict, success: bool) -> None:
        self.tree.Enable()
        name = data["name"]
        if success:
            self._set_status(_("status.uninstall_success", name=name), "success")
            # Сброс кэша
            invalidate_caches()
            invalidate_installed_cache(self._state)
            self.installed_names = get_installed_programs(
                state_dict=self._state,
                use_cache=getattr(self, "_installed_cache_enabled", True),
            )
            self.status_cache = build_status_cache(
                self.programs_db, self.installed_names,
            )
            # Обновление дерева
            self.populate_tree(self.search_ctrl.GetValue(), category=getattr(self, "_active_category", ""))
            self._update_selection_counter()
        else:
            self._set_status(_("status.uninstall_failed", name=name), "error")
            wx.MessageBox(
                _("status.uninstall_failed", name=name),
                _("dialog.uninstall.title"),
                wx.OK | wx.ICON_ERROR,
            )

    def _ctx_open_prog_log(self, log_path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(log_path)
            else:
                subprocess.Popen(
                    ["xdg-open", log_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            wx.MessageBox(
                f"Не удалось открыть лог установки:\n{e}",
                "Ошибка", wx.OK | wx.ICON_WARNING,
            )
