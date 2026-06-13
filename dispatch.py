from __future__ import annotations

import os
import subprocess

import wx

import config
import i18n
import state
import stats
from core_impl import (
    InstallWorker,
    build_status_cache,
    find_latest_install_log,
    get_installed_programs,
    invalidate_caches,
    invalidate_installed_cache,
    resolve_dependencies,
)


class DispatchMixin:
    worker: InstallWorker | None
    programs_db: dict
    _state: dict
    _closing: bool

    btn_install: wx.Button
    btn_cancel: wx.Button
    progress_bar: wx.Gauge
    search_ctrl: wx.TextCtrl
    _set_status: callable
    populate_tree: callable
    _save_window_state: callable
    _save_session: callable
    _parallel_enabled: bool
    _installed_cache_enabled: bool
    installed_names: list
    status_cache: dict
    tree_data: dict
    tree: wx.TreeCtrl

    def start_install(self, event: wx.CommandEvent | None) -> None:
        if self.worker and self.worker.is_alive():
            return

        tasks = [
            data
            for item, data in self.tree_data.items()
            if self.tree.IsItemChecked(item)
        ]
        if not tasks:
            self._set_status("Вы ничего не выбрали!", "error")
            return

        tasks = resolve_dependencies(tasks, self.programs_db)

        self.btn_install.Disable()
        self.btn_cancel.Enable()
        self.progress_bar.SetValue(0)

        def dispatch(msg: dict) -> None:
            if not self._closing:
                wx.CallAfter(self.on_worker_message, msg)

        self.worker = InstallWorker(
            tasks,
            dispatch,
            parallel=self._parallel_enabled,
            all_programs=self.programs_db,
            watchdog_interval=None,  # GUI doesn't expose these, use defaults
            watchdog_hang_threshold=None,
            watchdog_cpu_threshold=None,
        )
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

        task_timings = msg.get("task_timings", {})
        for name, duration in task_timings.items():
            if duration > 0:
                stats.record_install_time(self._state, name, duration)
        if task_timings:
            state.save_state(self._state)

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

        severity = "warn" if fails > 0 or cancelled > 0 else "success"
        self._set_status(
            ". ".join(lines) + "." if lines else "Готово.", severity,
        )

        invalidate_caches()
        invalidate_installed_cache(self._state)
        self.installed_names = get_installed_programs(
            state_dict=self._state,
            use_cache=self._installed_cache_enabled,
        )
        self.status_cache = build_status_cache(
            self.programs_db, self.installed_names,
        )
        cat = getattr(self, '_active_category', '')
        self.populate_tree(self.search_ctrl.GetValue(), category=cat)

        rollbacks = msg.get("rollbacks", {})
        rolled_back = [
            n for n, r in rollbacks.items() if r == "rolled_back"
        ]
        rollback_failed = [
            n for n, r in rollbacks.items() if r == "rollback_failed"
        ]

        summary_msg = (
            f"Установка завершена.\n\n"
            f"Успешно: {success}\nОшибок: {fails}"
        )
        if cancelled:
            summary_msg += f"\nОтменено: {cancelled}"
        if rolled_back:
            summary_msg += f"\n\nОткачено ({len(rolled_back)}):\n"
            summary_msg += "\n".join(f"  - {n}" for n in rolled_back)
        if rollback_failed:
            summary_msg += (
                f"\n\nОткат не удался ({len(rollback_failed)}):\n"
            )
            summary_msg += "\n".join(f"  - {n}" for n in rollback_failed)
        if reboot_needed:
            summary_msg += "\n\nТребуется перезагрузка."
        wx.MessageBox(
            summary_msg, "Результат", wx.OK | wx.ICON_INFORMATION,
        )

        if fails > 0:
            failed_programs = [
                data["name"]
                for item, data in self.tree_data.items()
                if results.get(item) == "fail"
            ]
            if len(failed_programs) == 1:
                failed_name = failed_programs[0]
                confirm_msg = i18n.t("dialog.error_log.confirm_single", name=failed_name)
                dlg = wx.MessageDialog(
                    self,
                    confirm_msg,
                    i18n.t("dialog.error_log.title"),
                    wx.YES_NO | wx.ICON_QUESTION,
                )
                try:
                    if dlg.ShowModal() == wx.ID_YES:
                        log_path = find_latest_install_log(failed_name)
                        if log_path:
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
                                    f"Не удалось открыть лог:\n{e}",
                                    "Ошибка", wx.OK | wx.ICON_WARNING,
                                )
                finally:
                    dlg.Destroy()
            elif len(failed_programs) > 1:
                confirm_msg = i18n.t("dialog.error_log.confirm_multiple", count=fails)
                dlg = wx.MessageDialog(
                    self,
                    confirm_msg,
                    i18n.t("dialog.error_log.title"),
                    wx.YES_NO | wx.ICON_QUESTION,
                )
                try:
                    if dlg.ShowModal() == wx.ID_YES:
                        try:
                            if os.name == "nt":
                                subprocess.Popen(["explorer", config.INSTALL_LOGS_DIR])
                            else:
                                subprocess.Popen(
                                    ["xdg-open", config.INSTALL_LOGS_DIR],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                        except Exception as e:
                            wx.MessageBox(
                                f"Не удалось открыть папку с логами:\n{e}",
                                "Ошибка", wx.OK | wx.ICON_WARNING,
                            )
                finally:
                    dlg.Destroy()

        if reboot_needed:
            dlg = wx.MessageDialog(
                self,
                "Установщики требуют перезагрузки компьютера.\n\n"
                "Перезагрузить сейчас?",
                "Перезагрузка",
                wx.YES_NO | wx.ICON_QUESTION,
            )
            try:
                choice = dlg.ShowModal()
            finally:
                dlg.Destroy()

            if choice == wx.ID_YES:
                try:
                    if os.name == "nt":
                        subprocess.run(
                            [
                                "shutdown", "/r", "/t", "10", "/c",
                                "Мастер установки: перезагрузка",
                            ],
                            check=False,
                        )
                    else:
                        subprocess.run(
                            [
                                "shutdown", "-r", "+1",
                                "Мастер установки: перезагрузка",
                            ],
                            check=False,
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
            dlg = wx.MessageDialog(
                self,
                "Установка выполняется. Прервать и выйти?",
                "Выход",
                wx.YES_NO | wx.ICON_WARNING,
            )
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
            self._closing = True
            if hasattr(self, '_watcher_timer') and self._watcher_timer.IsRunning():
                self._watcher_timer.Stop()
            if hasattr(self, '_search_timer') and self._search_timer.IsRunning():
                self._search_timer.Stop()
            self._save_window_state()
            self._save_session()
            event.Skip()
