from __future__ import annotations

import contextlib
import datetime
import logging
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
import deps
from utils import _download_file, _normalize_cmd_paths, build_cmd, dispatch_cmd

RETRYABLE_EXIT_CODES = {
    1618,
    1603,
    1641,
}


INSTALL_LOGS_DIR = config.INSTALL_LOGS_DIR


def _safe_log_name(name: str) -> str:
    """Возвращает имя файла лога для задачи — безопасное для файловой системы."""
    safe = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_\- ]", "_", name.strip())
    safe = re.sub(r"\s+", "_", safe)
    if not safe:
        safe = "unnamed"
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe}_{timestamp}.log"


def open_install_log(task_name: str) -> tuple[str, int]:
    """
    Создаёт файл лога для процесса установки в INSTALL_LOGS_DIR.

    Возвращает (log_path, file_descriptor).
    Caller должен закрыть fd после завершения процесса.
    """
    os.makedirs(INSTALL_LOGS_DIR, exist_ok=True)
    fname = _safe_log_name(task_name)
    log_path = os.path.join(INSTALL_LOGS_DIR, fname)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    return log_path, fd


def _append_fd_header(fd: int, text: str) -> None:
    """Дописывает строку в fd — низкоуровневый write, thread-safe."""
    with contextlib.suppress(OSError):
        os.write(fd, f"{text}\n".encode("utf-8", errors="replace"))



def download_installer(
    url: str,
    dest_dir: str,
    expected_sha256: str | None = None,
    progress_cb: callable | None = None,
) -> str:
    import urllib.parse

    os.makedirs(dest_dir, exist_ok=True)

    parsed = urllib.parse.urlparse(url)
    fname = os.path.basename(parsed.path) or "installer.bin"
    dest_path = os.path.join(dest_dir, fname)

    def _wrap_cb(total: int, downloaded: int) -> None:
        if progress_cb:
            progress_cb({"downloaded": downloaded, "total": total})

    actual_sha = _download_file(
        url, dest_path,
        user_agent=f"MInstAll/{config.APP_VERSION}",
        progress_cb=_wrap_cb,
    )

    if expected_sha256 and actual_sha != expected_sha256.lower():
        with contextlib.suppress(OSError):
            os.remove(dest_path)
        raise RuntimeError(
            f"SHA-256 не совпадает: ожидалось {expected_sha256}, получено {actual_sha}"
        )

    return dest_path



def is_installer_available(program: dict) -> bool:

    if program.get("url"):
        return True

    cmd_str = program.get("cmd", "")
    if not cmd_str:
        return False
    try:
        _args, script_path = build_cmd(cmd_str)
    except ValueError:
        return False
    if not script_path:
        return True
    return os.path.exists(script_path)



def _watchdog_monitor(
    pid: int,
    stop_event: threading.Event,
    hung_event: threading.Event,
    watchdog_sample_interval: int,
    watchdog_hang_threshold: int,
    watchdog_cpu_threshold: float,
) -> None:
    try:
        import psutil
    except ImportError:
        logging.warning("psutil не установлен — watchdog отключён")
        return

    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    try:
        proc.cpu_percent(interval=None)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    silent_count = 0
    while not stop_event.wait(watchdog_sample_interval):
        try:
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return

            cpu = proc.cpu_percent(interval=None)
            for child in proc.children(recursive=True):
                try:
                    cpu += child.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if cpu < watchdog_cpu_threshold:
                silent_count += 1
                logging.debug(
                    f"Watchdog PID={pid}: тихий замер {silent_count}/"
                    f"{watchdog_hang_threshold} (CPU={cpu:.2f}%)"
                )
                if silent_count >= watchdog_hang_threshold:
                    logging.warning(
                        f"Watchdog PID={pid}: процесс завис "
                        f"({silent_count} замеров без CPU), завершаем"
                    )
                    hung_event.set()
                    try:
                        for child in proc.children(recursive=True):
                            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                                child.kill()
                        proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    return
            else:
                silent_count = 0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        except Exception as e:
            logging.exception(f"Watchdog ошибка: {e}")
            return



def run_hook(cmd_str: str, hook_name: str = "hook", task_name: str = "") -> bool:

    if not cmd_str:
        return True

    try:
        cmd_args, script_path = build_cmd(cmd_str)
    except ValueError as e:
        logging.error(f"{hook_name} {task_name}: невалидная команда: {e}")
        return False

    if script_path and not os.path.exists(script_path):
        logging.warning(f"{hook_name} {task_name}: файл не найден: {script_path}")
        return False

    try:
        _popen_kw: dict = dict(
            cwd=(os.path.dirname(script_path) if script_path else None)
            or config.SCRIPT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if config.CREATE_NO_WINDOW:
            _popen_kw["creationflags"] = config.CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd_args, **_popen_kw)
        proc.wait(timeout=config.DEFAULT_INSTALL_TIMEOUT)
        rc = proc.returncode
        if rc == 0:
            logging.info(f"{hook_name} {task_name}: OK")
            return True
        else:
            logging.warning(f"{hook_name} {task_name}: код {rc}")
            return False
    except Exception as e:
        logging.exception(f"{hook_name} {task_name}: исключение: {e}")
        return False



def run_uninstall(task: dict) -> bool:

    uninstall_cmd = task.get("uninstall_cmd", "")
    name = task.get("name", "Unknown")
    if not uninstall_cmd:
        logging.warning(f"Нет команды удаления для {name}")
        return False

    try:
        cmd_args, script_path = build_cmd(uninstall_cmd)
    except ValueError as e:
        logging.error(f"Невалидная команда удаления {name}: {e}")
        return False

    if script_path and not os.path.exists(script_path):
        logging.error(f"Файл удаления не найден: {script_path}")
        return False

    try:
        _uninstall_kw: dict = dict(
            cwd=(os.path.dirname(script_path) if script_path else None)
            or config.SCRIPT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if config.CREATE_NO_WINDOW:
            _uninstall_kw["creationflags"] = config.CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd_args, **_uninstall_kw)
        proc.wait(timeout=config.DEFAULT_INSTALL_TIMEOUT)
        if proc.returncode == 0:
            logging.info(f"Откат OK: {task['name']}")
            return True
        else:
            logging.error(f"Откат {task['name']}: код {proc.returncode}")
            return False
    except Exception as e:
        logging.exception(f"Ошибка отката {task['name']}: {e}")
        return False



class InstallWorker(threading.Thread):

    def __init__(
        self,
        tasks: list[dict],
        dispatch: callable,
        parallel: bool = False,
        max_jobs: int | None = None,
        all_programs: dict[str, list[dict]] | None = None,
        watchdog_interval: int | None = None,
        watchdog_hang_threshold: int | None = None,
        watchdog_cpu_threshold: float | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.tasks = tasks
        self.dispatch = dispatch
        self.parallel = parallel
        self.max_jobs = max_jobs or config.MAX_PARALLEL_JOBS
        self.all_programs = all_programs or {}
        self.total_tasks = len(tasks)
        self._is_running = True
        self.success_count = 0
        self.fail_count = 0
        self.reboot_needed = False
        self.results: dict = {}
        self.rollbacks: dict[str, str] = {}

        self._active_procs: set[subprocess.Popen] = set()
        self._procs_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._completed_count = 0
        self._install_logs: dict[str, str] = {}
        self._task_timings: dict[str, float] = {}
        self._task_timings_lock = threading.Lock()

        self._msi_semaphore = threading.Semaphore(1)

        # Watchdog overrides
        self._watchdog_interval = watchdog_interval or config.WATCHDOG_SAMPLE_INTERVAL
        self._watchdog_hang_threshold = watchdog_hang_threshold or config.WATCHDOG_HANG_THRESHOLD
        self._watchdog_cpu_threshold = watchdog_cpu_threshold or config.WATCHDOG_CPU_THRESHOLD

    def stop(self) -> None:
        self._is_running = False
        with self._procs_lock:
            for proc in list(self._active_procs):
                with contextlib.suppress(Exception):
                    proc.terminate()

    def _emit(self, **kwargs: object) -> None:
        self.dispatch(kwargs)

    def _spawn_process(
        self,
        cmd_args: list[str],
        script_path: str,
        timeout: int,
        log_fd: int | None = None,
    ) -> int:
        cwd = (
            os.path.dirname(script_path) if script_path else None
        ) or config.SCRIPT_DIR

        if log_fd is not None:
            stdout_dest = log_fd
            stderr_dest = log_fd
        else:
            stdout_dest = subprocess.DEVNULL
            stderr_dest = subprocess.DEVNULL

        popen_kwargs: dict = dict(
            cwd=cwd,
            stdout=stdout_dest,
            stderr=stderr_dest,
        )
        if config.CREATE_NO_WINDOW:
            popen_kwargs["creationflags"] = config.CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd_args, **popen_kwargs)

        with self._procs_lock:
            self._active_procs.add(proc)

        watchdog_stop = threading.Event()
        watchdog_hung = threading.Event()
        watchdog_thread = None
        if config.WATCHDOG_ENABLED:
            watchdog_thread = threading.Thread(
                target=_watchdog_monitor,
                args=(proc.pid, watchdog_stop, watchdog_hung, self._watchdog_interval, self._watchdog_hang_threshold, self._watchdog_cpu_threshold),
                daemon=True,
            )
            watchdog_thread.start()

        try:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd_args, timeout) from None

            if watchdog_hung.is_set():
                raise RuntimeError(
                    "Процесс завис и был принудительно завершён watchdog'ом"
                )

            return proc.returncode
        finally:
            watchdog_stop.set()
            if watchdog_thread:
                watchdog_thread.join(timeout=1.0)
            with self._procs_lock:
                self._active_procs.discard(proc)

    def _install_one_task(self, task: dict, emit_scroll: bool = True) -> None:

        item_id = task.get("_item_id")
        name = task["name"]
        timeout = task.get("timeout", config.DEFAULT_INSTALL_TIMEOUT)
        max_retries = task.get("retry", 0)

        self._emit(
            type="progress",
            text=f"Установка: {name}...",
            severity="progress",
        )
        if emit_scroll and item_id:
            self._emit(type="scroll_to", item_id=item_id)

        log_path, log_fd = open_install_log(name)
        _append_fd_header(log_fd, f"=== Установка: {name} ===")
        _append_fd_header(log_fd, f"Время: {datetime.datetime.now()}")
        _append_fd_header(log_fd, f"Команда: {shlex.join(task.get('cmd', '').split()) if task.get('cmd') else '(none)'}")
        _append_fd_header(log_fd, f"Таймаут: {timeout} с  |  Повторов: {max_retries}")
        _append_fd_header(log_fd, f"Лог: {log_path}")
        _append_fd_header(log_fd, "")

        if url := task.get("url"):
            self._emit(
                type="progress",
                text=f"Скачивание: {name}...",
                severity="progress",
            )
            try:
                download_dir = os.path.join(
                    tempfile.gettempdir(),
                    "minstall_downloads",
                )

                def _dl_progress(info: dict) -> None:
                    total = info.get("total", 0)
                    if total > 0:
                        pct = int(info["downloaded"] * 100 / total)
                        self._emit(
                            type="progress",
                            text=f"Скачивание {name}: {pct}%",
                            severity="progress",
                        )

                downloaded_path = download_installer(
                    url,
                    download_dir,
                    expected_sha256=task.get("sha256"),
                    progress_cb=_dl_progress,
                )

                # Пользовательские аргументы берём из cmd, а имя файла из JSON
                # заменяем на реально скачанный путь. Диспетчеризация по
                # расширению — та же кросс-платформенная, что и для локальных
                # файлов (см. core.dispatch_cmd).
                parts = shlex.split(_normalize_cmd_paths(task["cmd"]), posix=True)
                user_args = parts[1:] if parts else []
                cmd_args, script_path = dispatch_cmd(downloaded_path, user_args)
            except Exception as e:
                logging.exception(f"Скачивание {name} упало: {e}")
                _append_fd_header(log_fd, f"ОШИБКА СКАЧИВАНИЯ: {e}")
                os.close(log_fd)
                self._emit(
                    type="progress",
                    text=f"Ошибка скачивания {name}: {e}",
                    severity="error",
                )
                with self._state_lock:
                    self.fail_count += 1
                    if item_id:
                        self.results[item_id] = "fail"
                return
        else:
            try:
                cmd_args, script_path = build_cmd(task["cmd"])
            except ValueError as exc:
                _append_fd_header(log_fd, f"ОШИБКА: {exc}")
                os.close(log_fd)
                self._emit(
                    type="progress",
                    text=f"{exc}: {name}",
                    severity="error",
                )
                with self._state_lock:
                    self.fail_count += 1
                    if item_id:
                        self.results[item_id] = "fail"
                return

        if script_path and not os.path.exists(script_path):
            logging.error(f"Файл не найден: {script_path}")
            _append_fd_header(log_fd, f"ФАЙЛ НЕ НАЙДЕН: {script_path}")
            os.close(log_fd)
            self._emit(
                type="progress",
                text=f"Файл не найден: {script_path}",
                severity="error",
            )
            with self._state_lock:
                self.fail_count += 1
                if item_id:
                    self.results[item_id] = "fail"
            return

        if pre_cmd := task.get("pre_cmd"):
            self._emit(
                type="progress",
                text=f"Подготовка: {name}...",
                severity="progress",
            )
            if not run_hook(pre_cmd, "pre_cmd", name):
                self._emit(
                    type="progress",
                    text=f"Pre-команда {name} вернула ошибку, продолжаем",
                    severity="warn",
                )

        # Определяем MSI по уже разрешённому пути инсталлятора (учитывает и
        # скачанные файлы), а не по наивному split() строки cmd — путь с
        # пробелами иначе разбился бы неверно.
        is_msi = os.path.splitext(script_path)[1].lower() == ".msi"
        if is_msi:
            self._msi_semaphore.acquire()

        _task_start = time.time()
        try:
            attempt = 0
            last_rc = -1
            success = False

            while attempt <= max_retries:
                if not self._is_running:
                    break

                try:
                    if attempt > 0:
                        delay = min(5 * (2 ** (attempt - 1)), 30)
                        self._emit(
                            type="progress",
                            text=f"Повтор {attempt}/{max_retries} для {name} "
                                 f"(через {delay}с)...",
                            severity="warn",
                        )
                        time.sleep(delay)

                    last_rc = self._spawn_process(
                        cmd_args, script_path, timeout, log_fd,
                    )

                    if not self._is_running:
                        break

                    if last_rc == 0:
                        success = True
                        break
                    elif last_rc == 3010:
                        success = True
                        with self._state_lock:
                            self.reboot_needed = True
                        self._emit(
                            type="progress",
                            text=f"Требуется перезагрузка для {name}",
                            severity="warn",
                        )
                        logging.info(f"OK (нужна перезагрузка): {name}")
                        break
                    elif last_rc in RETRYABLE_EXIT_CODES and attempt < max_retries:
                        logging.warning(
                            f"Retryable код {last_rc} для {name}, "
                            f"попытка {attempt + 1}/{max_retries + 1}"
                        )
                        attempt += 1
                        continue
                    else:
                        break

                except subprocess.TimeoutExpired:
                    logging.error(
                        f"Таймаут {timeout}с для {name} (попытка {attempt + 1})"
                    )
                    self._emit(
                        type="progress",
                        text=f"Таймаут {name} ({timeout}с)",
                        severity="error",
                    )
                    if attempt < max_retries:
                        attempt += 1
                        continue
                    break
                except RuntimeError as e:
                    logging.error(f"Watchdog для {name}: {e}")
                    self._emit(
                        type="progress",
                        text=f"Зависание: {name}",
                        severity="error",
                    )
                    if attempt < max_retries:
                        attempt += 1
                        continue
                    break
                except Exception as e:
                    logging.exception(f"Исключение при установке {name}: {e}")
                    self._emit(
                        type="progress",
                        text=f"Ошибка {name}",
                        severity="error",
                    )
                    break
        finally:
            if is_msi:
                self._msi_semaphore.release()

        _task_duration = time.time() - _task_start

        if not self._is_running:
            with self._state_lock:
                if item_id:
                    self.results[item_id] = "cancelled"
            _append_fd_header(log_fd, "ОТМЕНЕНО")
            os.close(log_fd)
            self._emit(type="progress", text=f"Отменено: {name}", severity="warn")
            return

        if success:
            with self._state_lock:
                self.success_count += 1
                if item_id:
                    self.results[item_id] = "ok"
                self._install_logs[name] = log_path
            with self._task_timings_lock:
                self._task_timings[name] = _task_duration
            if attempt > 0:
                logging.info(f"OK (после {attempt + 1} попыток): {name}")
            else:
                logging.info(f"OK: {name}")
            _append_fd_header(log_fd, f"ОК (код {last_rc}, попыток: {attempt + 1})")
            os.close(log_fd)

            if post_cmd := task.get("post_cmd"):
                self._emit(
                    type="progress",
                    text=f"Завершение: {name}...",
                    severity="progress",
                )
                if not run_hook(post_cmd, "post_cmd", name):
                    self._emit(
                        type="progress",
                        text=f"Post-команда {name} вернула ошибку",
                        severity="warn",
                    )
        else:
            with self._state_lock:
                self.fail_count += 1
                if item_id:
                    self.results[item_id] = "fail"
                self._install_logs[name] = log_path
            self._emit(
                type="progress",
                text=f"Ошибка {name} (код {last_rc})",
                severity="error",
            )
            logging.error(
                f"Ошибка {name}: код {last_rc} (после {attempt + 1} попыток)"
            )
            _append_fd_header(log_fd, f"ОШИБКА (код {last_rc}, попыток: {attempt + 1})")
            os.close(log_fd)

            if task.get("uninstall_cmd"):
                self._emit(
                    type="progress",
                    text=f"Откат {name}...",
                    severity="warn",
                )
                if run_uninstall(task):
                    with self._state_lock:
                        self.rollbacks[name] = "rolled_back"
                    self._emit(
                        type="progress",
                        text=f"Откат {name}: успешно",
                        severity="info",
                    )
                else:
                    with self._state_lock:
                        self.rollbacks[name] = "rollback_failed"
                    self._emit(
                        type="progress",
                        text=f"Откат {name}: не удался",
                        severity="error",
                    )
            else:
                with self._state_lock:
                    self.rollbacks[name] = "no_uninstall"

    def _emit_progress_pct(self) -> None:
        with self._state_lock:
            self._completed_count += 1
            pct = int(self._completed_count / self.total_tasks * 100)
        self._emit(type="value", percent=pct)

    def _run_sequential(self) -> None:
        for task in self.tasks:
            if not self._is_running:
                item_id = task.get("_item_id")
                if item_id:
                    with self._state_lock:
                        self.results[item_id] = "cancelled"
                self._emit(
                    type="progress",
                    text="Установка отменена.",
                    severity="warn",
                )
                break

            self._install_one_task(task, emit_scroll=True)
            self._emit_progress_pct()

    def _run_parallel(self) -> None:
        levels = deps.topological_levels(self.tasks, self.all_programs)
        logging.info(
            f"Параллельная установка: {len(levels)} уровней, "
            f"max_jobs={self.max_jobs}"
        )

        for level_idx, level in enumerate(levels):
            if not self._is_running:
                break

            self._emit(
                type="progress",
                text=f"Уровень {level_idx + 1}/{len(levels)}: "
                     f"{len(level)} программ параллельно",
                severity="info",
            )

            with ThreadPoolExecutor(max_workers=self.max_jobs) as executor:
                futures = [
                    executor.submit(self._install_one_task, task, False)
                    for task in level
                ]
                for fut in futures:
                    try:
                        fut.result()
                    except Exception as e:
                        logging.exception(f"Поток-исполнитель упал: {e}")
                    self._emit_progress_pct()

    def run(self) -> None:
        try:
            if self.parallel and self.total_tasks > 1:
                self._run_parallel()
            else:
                self._run_sequential()
        except Exception as e:
            logging.exception(f"InstallWorker.run упал: {e}")
        finally:
            self._emit(
                type="finished",
                success=self.success_count,
                fails=self.fail_count,
                reboot=self.reboot_needed,
                results=self.results,
                rollbacks=self.rollbacks,
                install_logs=self._install_logs,
                task_timings=self._task_timings,
            )


def run_linux_update(program: dict) -> bool:
    from registry import get_linux_update_command
    cmd_str = get_linux_update_command(program)
    if not cmd_str:
        logging.info(f"Нет команды обновления для {program.get('name', 'Unknown')}")
        return False
    try:
        cmd_args = shlex.split(cmd_str, posix=True)
    except ValueError:
        logging.error(f"Невалидная команда обновления: {cmd_str}")
        return False
    try:
        proc = subprocess.Popen(
            cmd_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=config.DEFAULT_INSTALL_TIMEOUT)
        if proc.returncode == 0:
            logging.info(f"Обновление OK: {program.get('name', 'Unknown')}")
            return True
        else:
            logging.warning(f"Обновление {program.get('name', 'Unknown')}: код {proc.returncode}")
            return False
    except Exception as e:
        logging.exception(f"Ошибка обновления {program.get('name', 'Unknown')}: {e}")
        return False


def run_linux_uninstall(program: dict) -> bool:
    from registry import get_linux_uninstall_command
    cmd_str = get_linux_uninstall_command(program)
    if not cmd_str:
        logging.info(f"Нет команды удаления для {program.get('name', 'Unknown')}")
        return False
    try:
        cmd_args = shlex.split(cmd_str, posix=True)
    except ValueError:
        logging.error(f"Невалидная команда удаления: {cmd_str}")
        return False
    try:
        proc = subprocess.Popen(
            cmd_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=config.DEFAULT_INSTALL_TIMEOUT)
        if proc.returncode == 0:
            logging.info(f"Удаление OK: {program.get('name', 'Unknown')}")
            return True
        else:
            logging.warning(f"Удаление {program.get('name', 'Unknown')}: код {proc.returncode}")
            return False
    except Exception as e:
        logging.exception(f"Ошибка удаления {program.get('name', 'Unknown')}: {e}")
        return False
