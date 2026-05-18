from __future__ import annotations

import os
import json
import ctypes
import re
import shlex
import subprocess
import logging
import threading
import time
import sys
from collections import defaultdict

import config


# ------------------------------------------------------------------
# Логирование
# ------------------------------------------------------------------
def setup_logging() -> None:
    """Инициализация логгера. Вызывается ОДИН РАЗ из main.py."""
    try:
        logging.basicConfig(
            filename=config.LOG_FILE, level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s", encoding="utf-8"
        )
    except TypeError:  # Python < 3.9: basicConfig без encoding
        _logger = logging.getLogger()
        _logger.setLevel(logging.INFO)
        _fh = logging.FileHandler(config.LOG_FILE)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _logger.addHandler(_fh)


# ------------------------------------------------------------------
# Утилиты путей / конфига
# ------------------------------------------------------------------
def resolve_path(rel_path: str) -> str:
    return os.path.join(config.SCRIPT_DIR, rel_path)


def load_programs_from_json() -> dict[str, list[dict]]:
    if not os.path.exists(config.CONFIG_FILE):
        logging.error(f"Файл конфигурации не найден: {config.CONFIG_FILE}")
        return {}

    try:
        with open(config.CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Не удалось прочитать {config.CONFIG_FILE}: {e}")
        return {}

    if not isinstance(data, dict):
        logging.error(f"Некорректная структура {config.CONFIG_FILE}: ожидается объект")
        return {}

    categories = data.get("categories")
    if categories is None:
        logging.error(f"В {config.CONFIG_FILE} отсутствует ключ 'categories'")
        return {}
    if not isinstance(categories, dict):
        logging.error(f"Некорректный тип 'categories' в {config.CONFIG_FILE}: ожидается объект")
        return {}

    valid: dict[str, list[dict]] = {}
    for cat_name, programs in categories.items():
        if not isinstance(programs, list):
            logging.warning(f"Категория '{cat_name}': ожидается список программ, пропущена")
            continue
        valid_progs: list[dict] = []
        for i, prog in enumerate(programs):
            if not isinstance(prog, dict):
                logging.warning(f"Категория '{cat_name}', элемент {i}: ожидается объект, пропущен")
                continue
            if "name" not in prog or "cmd" not in prog:
                logging.warning(f"Категория '{cat_name}', элемент {i}: отсутствует 'name' или 'cmd', пропущен")
                continue
            valid_progs.append(prog)
        valid[cat_name] = valid_progs

    return valid


# ------------------------------------------------------------------
# Валидация команд
# ------------------------------------------------------------------
def validate_cmd(cmd_str: str) -> str | None:
    """Проверяет команду на shell-инъекции. Возвращает текст ошибки или None."""
    for char in config.SHELL_METACHARACTERS:
        if char in cmd_str:
            return f"Недопустимый символ '{char}' в команде"

    parts = shlex.split(cmd_str, posix=False)
    if not parts:
        return "Пустая команда"

    ext = os.path.splitext(parts[0])[1].lower()
    if ext and ext not in config.ALLOWED_CMD_EXTENSIONS:
        return f"Недопустимое расширение '{ext}'"

    return None


# ------------------------------------------------------------------
# Права администратора
# ------------------------------------------------------------------
def is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    if os.name != "nt":
        return False
    exe = sys.executable
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, config.SCRIPT_DIR, 1) > 32
    except Exception:
        return False


# ------------------------------------------------------------------
# Реестр / установленные программы
# ------------------------------------------------------------------
UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]


def get_installed_programs() -> list[tuple[str, str]]:
    if os.name != "nt":
        return []
    import winreg
    entries: list[tuple[str, str]] = []
    for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for key_path in UNINSTALL_KEYS:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as sub:
                                try:
                                    name, _ = winreg.QueryValueEx(sub, "DisplayName")
                                except FileNotFoundError:
                                    continue
                                try:
                                    version, _ = winreg.QueryValueEx(sub, "DisplayVersion")
                                except FileNotFoundError:
                                    version = ""
                                entries.append((name, str(version)))
                        except OSError:
                            continue
            except FileNotFoundError:
                continue
    return entries


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", str(v))) if v else ()


def compare_versions(a: str, b: str) -> int:
    """Возвращает -1, 0, 1 (как cmp). Сравнивает только числовые компоненты."""
    ta, tb = parse_version(a), parse_version(b)
    n = max(len(ta), len(tb))
    ta, tb = ta + (0,) * (n - len(ta)), tb + (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


_net_release_cache: tuple[bool, int | None] = (False, None)


def get_net_framework_release(use_cache: bool = True) -> int | None:
    """Версия .NET Framework. Кешируется, т.к. лезет в реестр."""
    global _net_release_cache
    if use_cache and _net_release_cache[0]:
        return _net_release_cache[1]

    if os.name != "nt":
        _net_release_cache = (True, None)
        return None

    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
            value, _ = winreg.QueryValueEx(key, "Release")
            result = int(value)
    except OSError:
        result = None

    _net_release_cache = (True, result)
    return result


def invalidate_caches() -> None:
    """Сбрасывает кеши после установки — чтобы свежие данные были подхвачены."""
    global _net_release_cache
    _net_release_cache = (False, None)


def check_status(program: dict, installed_entries: list[tuple[str, str]]) -> tuple[str, str]:
    detect = program.get("detect", {}) or {}
    if detect.get("always_runnable"):
        return ("runnable", "")

    if (net_min := detect.get("net_framework_release")) is not None:
        release = get_net_framework_release()
        if release is None:
            return ("missing", "")
        return ("outdated", str(release)) if release < net_min else ("ok", str(release))

    if path := detect.get("path"):
        return ("ok", "") if os.path.exists(os.path.expandvars(path)) else ("missing", "")

    needle = (detect.get("registry_name") or program["name"]).lower()
    found_version = next((v for n, v in installed_entries if needle in n.lower()), None)
    if found_version is None:
        return ("missing", "")

    min_v = detect.get("min_version")
    if min_v and compare_versions(found_version, min_v) < 0:
        return ("outdated", found_version)
    return ("ok", found_version)


def build_status_cache(
    programs_db: dict[str, list[dict]],
    installed_entries: list[tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    """
    Считает статусы всех программ один раз, возвращает {name -> (status, version)}.

    Используется чтобы избежать повторного вызова check_status для каждой
    программы в populate_tree и _initial_status_text — это сильно ускоряет
    запуск при росте каталога.
    """
    cache: dict[str, tuple[str, str]] = {}
    # Предкешируем .NET release один раз, чтобы не лезть в реестр на каждом вызове
    for programs in programs_db.values():
        for prog in programs:
            cache[prog["name"]] = check_status(prog, installed_entries)
    return cache


# ------------------------------------------------------------------
# Построение команды запуска
# ------------------------------------------------------------------
def build_cmd(cmd_str: str) -> tuple[list[str], str]:
    """
    Возвращает кортеж (cmd_args, script_path), где:
      - cmd_args   — что передавать в subprocess.Popen
      - script_path — реальный путь к скрипту/установщику для проверки существования
    """
    error = validate_cmd(cmd_str)
    if error:
        raise ValueError(error)

    parts = shlex.split(cmd_str, posix=False)
    script_path = resolve_path(parts[0])
    user_args = parts[1:]
    ext = os.path.splitext(parts[0])[1].lower()

    if ext == ".reg":
        return (["regedit", "/s", script_path], script_path)
    if ext in (".bat", ".cmd"):
        return (["cmd", "/c", script_path] + user_args, script_path)
    if ext == ".ps1":
        return (["powershell", "-ExecutionPolicy", "Bypass", "-NonInteractive",
                 "-File", script_path] + user_args, script_path)
    if ext == ".msi":
        return (["msiexec", "/i", script_path, "/qn", "/norestart"] + user_args, script_path)
    return ([script_path] + user_args, script_path)


# ------------------------------------------------------------------
# Граф зависимостей — топологическая сортировка задач
# ------------------------------------------------------------------
def resolve_dependencies(tasks: list[dict], all_programs: dict[str, list[dict]]) -> list[dict]:
    """
    Сортирует tasks так, что зависимости идут перед зависимыми программами.
    Если зависимость отсутствует в tasks — она добавляется автоматически.

    Использует топологическую сортировку (Кана) для обнаружения циклов.
    """
    # Индекс всех программ по имени
    all_by_name: dict[str, dict] = {}
    for progs in all_programs.values():
        for p in progs:
            all_by_name[p["name"]] = p

    # Индекс задач по имени
    task_names: set[str] = {t["name"] for t in tasks}
    task_by_name: dict[str, dict] = {t["name"]: t for t in tasks}

    # Автоматическое добавление отсутствующих зависимостей
    queue = list(tasks)
    while queue:
        task = queue.pop()
        for dep_name in task.get("depends_on", []):
            if dep_name not in task_names and dep_name in all_by_name:
                dep = dict(all_by_name[dep_name])
                task_names.add(dep_name)
                task_by_name[dep_name] = dep
                queue.append(dep)

    # Граф: name -> список имён, от которых зависит
    graph: dict[str, list[str]] = {}
    in_degree: dict[str, int] = defaultdict(int)

    for name in task_names:
        graph.setdefault(name, [])
        in_degree.setdefault(name, 0)

    for name in task_names:
        task = task_by_name[name]
        for dep_name in task.get("depends_on", []):
            if dep_name in task_names:
                graph[dep_name].append(name)
                in_degree[name] += 1

    # Алгоритм Кана
    queue_kahn: list[str] = [n for n in task_names if in_degree[n] == 0]
    sorted_names: list[str] = []

    while queue_kahn:
        node = queue_kahn.pop(0)
        sorted_names.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue_kahn.append(neighbor)

    if len(sorted_names) != len(task_names):
        # Обнаружен цикл — возвращаем исходный порядок + логируем
        cycle_nodes = task_names - set(sorted_names)
        logging.warning(f"Обнаружен цикл зависимостей: {cycle_nodes}. Порядок не изменён.")
        return list(task_by_name.values())

    return [task_by_name[name] for name in sorted_names]


# ------------------------------------------------------------------
# Откат установки (uninstall)
# ------------------------------------------------------------------
def run_uninstall(task: dict) -> bool:
    """
    Запускает команду удаления программы. Возвращает True при успехе.
    """
    uninstall_cmd = task.get("uninstall_cmd", "")
    if not uninstall_cmd:
        logging.warning(f"Нет команды удаления для {task['name']}")
        return False

    try:
        cmd_args, script_path = build_cmd(uninstall_cmd)
    except ValueError as e:
        logging.error(f"Невалидная команда удаления {task['name']}: {e}")
        return False

    if not os.path.exists(script_path):
        logging.error(f"Файл удаления не найден: {script_path}")
        return False

    try:
        proc = subprocess.Popen(
            cmd_args,
            cwd=os.path.dirname(script_path) or config.SCRIPT_DIR,
            creationflags=config.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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


# ------------------------------------------------------------------
# Retry-коды: при этих exit-кодах имеет смысл повторять
# ------------------------------------------------------------------
RETRYABLE_EXIT_CODES = {
    1618,   # ERROR_INSTALL_ALREADY_RUNNING — другой MSI запущен
    1603,   # ERROR_INSTALL_FAILURE — общая ошибка (иногда transient)
    1641,   # ERROR_SUCCESS_REBOOT_INITIATED (установщик перезапускается)
}


# ------------------------------------------------------------------
# Воркер установки — с retry и откатом
# ------------------------------------------------------------------
class InstallWorker(threading.Thread):
    """
    Эмитит сообщения через dispatch(message_dict).
    GUI отвечает за маршалинг dispatch в UI-поток.

    Формат сообщений:
      {"type": "progress",  "text": "...", "severity": "info|progress|warn|error|success"}
      {"type": "value",     "percent": 42}
      {"type": "scroll_to", "item_id": <object>}
      {"type": "finished",  "success": N, "fails": M, "reboot": bool,
                            "results": {...}, "rollbacks": {...}}
    """

    def __init__(self, tasks: list[dict], dispatch: callable) -> None:
        super().__init__(daemon=True)
        self.tasks = tasks
        self.dispatch = dispatch
        self.total_tasks = len(tasks)
        self._is_running = True
        self.success_count = 0
        self.fail_count = 0
        self.reboot_needed = False
        self.current_proc: subprocess.Popen | None = None
        self.results: dict = {}
        self.rollbacks: dict[str, str] = {}  # name -> "rolled_back" | "rollback_failed" | "no_uninstall"

    def stop(self) -> None:
        self._is_running = False
        if self.current_proc:
            try:
                self.current_proc.terminate()
            except Exception:
                pass

    def _emit(self, **kwargs: object) -> None:
        self.dispatch(kwargs)

    def _run_single_install(self, cmd_args: list[str], script_path: str, timeout: int) -> int:
        """Запускает процесс и возвращает returncode. Поднимает исключение при проблемах."""
        self.current_proc = subprocess.Popen(
            cmd_args,
            cwd=os.path.dirname(script_path) or config.SCRIPT_DIR,
            creationflags=config.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Запускаем watchdog в фоне — он kill-ает процесс если завис
        watchdog_stop = threading.Event()
        watchdog_hung = threading.Event()
        watchdog_thread = None
        if config.WATCHDOG_ENABLED:
            watchdog_thread = threading.Thread(
                target=_watchdog_monitor,
                args=(self.current_proc.pid, watchdog_stop, watchdog_hung),
                daemon=True,
            )
            watchdog_thread.start()

        try:
            try:
                self.current_proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.current_proc.kill()
                self.current_proc.wait()
                raise subprocess.TimeoutExpired(cmd_args, timeout)

            # Если watchdog убил процесс — сообщим через исключение
            if watchdog_hung.is_set():
                raise RuntimeError(f"Процесс завис и был принудительно завершён watchdog'ом")

            return self.current_proc.returncode
        finally:
            watchdog_stop.set()
            if watchdog_thread:
                watchdog_thread.join(timeout=1.0)


# ------------------------------------------------------------------
# Watchdog: мониторит процесс, kill-ает если завис (нет CPU-активности)
# ------------------------------------------------------------------
def _watchdog_monitor(
    pid: int,
    stop_event: threading.Event,
    hung_event: threading.Event,
) -> None:
    """
    Раз в WATCHDOG_SAMPLE_INTERVAL секунд снимает CPU% процесса.
    Если CPU < WATCHDOG_CPU_THRESHOLD WATCHDOG_HANG_THRESHOLD раз подряд — kill.
    """
    try:
        import psutil
    except ImportError:
        logging.warning("psutil не установлен — watchdog отключён")
        return

    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    # cpu_percent с interval=None даёт замер с момента предыдущего вызова,
    # первый вызов всегда даёт 0 — поэтому "прогреваем"
    try:
        proc.cpu_percent(interval=None)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    silent_count = 0
    while not stop_event.wait(config.WATCHDOG_SAMPLE_INTERVAL):
        try:
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return

            cpu = proc.cpu_percent(interval=None)
            # Учитываем CPU и всех дочерних процессов (инсталляторы часто
            # запускают подпроцессы — установщик может быть idle, но дети работают)
            for child in proc.children(recursive=True):
                try:
                    cpu += child.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if cpu < config.WATCHDOG_CPU_THRESHOLD:
                silent_count += 1
                logging.debug(
                    f"Watchdog PID={pid}: тихий замер {silent_count}/"
                    f"{config.WATCHDOG_HANG_THRESHOLD} (CPU={cpu:.2f}%)"
                )
                if silent_count >= config.WATCHDOG_HANG_THRESHOLD:
                    logging.warning(
                        f"Watchdog PID={pid}: процесс завис "
                        f"({silent_count} замеров без CPU), завершаем"
                    )
                    hung_event.set()
                    try:
                        # Сначала вежливо детям, потом родителю
                        for child in proc.children(recursive=True):
                            try:
                                child.kill()
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
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

    def run(self) -> None:
        try:
            for index, task in enumerate(self.tasks):
                item_id = task.get("_item_id")

                if not self._is_running:
                    self._emit(type="progress", text="Установка отменена.", severity="warn")
                    if item_id:
                        self.results[item_id] = "cancelled"
                    break

                name = task["name"]
                timeout = task.get("timeout", config.DEFAULT_INSTALL_TIMEOUT)
                max_retries = task.get("retry", 0)
                self._emit(type="progress", text=f"Установка: {name}...", severity="progress")
                if item_id:
                    self._emit(type="scroll_to", item_id=item_id)

                # --- Построение команды ---
                try:
                    cmd_args, script_path = build_cmd(task["cmd"])
                except ValueError as exc:
                    self._emit(type="progress", text=f"{exc}: {name}", severity="error")
                    self.fail_count += 1
                    if item_id:
                        self.results[item_id] = "fail"
                    self._emit(type="value", percent=int(((index + 1) / self.total_tasks) * 100))
                    continue

                # --- Проверка файла ---
                if not os.path.exists(script_path):
                    logging.error(f"Файл не найден: {script_path}")
                    self._emit(type="progress", text=f"Файл не найден: {script_path}", severity="error")
                    time.sleep(1)
                    self.fail_count += 1
                    if item_id:
                        self.results[item_id] = "fail"
                    self._emit(type="value", percent=int(((index + 1) / self.total_tasks) * 100))
                    continue

                # --- Запуск с retry ---
                attempt = 0
                last_returncode = -1
                success = False

                while attempt <= max_retries:
                    if not self._is_running:
                        break

                    try:
                        if os.name == "nt":
                            if attempt > 0:
                                delay = min(5 * (2 ** (attempt - 1)), 30)  # 5, 10, 30 сек
                                self._emit(
                                    type="progress",
                                    text=f"Повтор {attempt}/{max_retries} для {name} "
                                         f"(через {delay}с)...",
                                    severity="warn",
                                )
                                time.sleep(delay)

                            last_returncode = self._run_single_install(cmd_args, script_path, timeout)

                            if not self._is_running:
                                break

                            if last_returncode == 0:
                                success = True
                                break
                            elif last_returncode == 3010:
                                success = True
                                self.reboot_needed = True
                                self._emit(type="progress",
                                           text=f"Требуется перезагрузка для {name}",
                                           severity="warn")
                                logging.info(f"OK (нужна перезагрузка): {name}")
                                break
                            elif last_returncode in RETRYABLE_EXIT_CODES and attempt < max_retries:
                                logging.warning(
                                    f"Retryable код {last_returncode} для {name}, "
                                    f"попытка {attempt + 1}/{max_retries + 1}"
                                )
                                attempt += 1
                                continue
                            else:
                                # Не retryable или попытки кончились
                                break
                        else:
                            # Заглушка для не-Windows
                            time.sleep(1.5)
                            success = True
                            break

                    except subprocess.TimeoutExpired:
                        logging.error(f"Таймаут {timeout}с для {name} (попытка {attempt + 1})")
                        self._emit(type="progress",
                                   text=f"Таймаут {name} ({timeout}с)", severity="error")
                        if attempt < max_retries:
                            attempt += 1
                            continue
                        break
                    except RuntimeError as e:
                        # Watchdog убил зависший процесс — retryable
                        logging.error(f"Watchdog для {name}: {e}")
                        self._emit(type="progress",
                                   text=f"Зависание: {name}", severity="error")
                        if attempt < max_retries:
                            attempt += 1
                            continue
                        break
                    except Exception as e:
                        logging.exception(f"Исключение при установке {name}: {e}")
                        self._emit(type="progress", text=f"Ошибка {name}", severity="error")
                        break
                    finally:
                        self.current_proc = None

                # --- Результат ---
                if not self._is_running:
                    if item_id:
                        self.results[item_id] = "cancelled"
                    self._emit(type="progress", text=f"Отменено: {name}", severity="warn")
                    break

                if success:
                    self.success_count += 1
                    if item_id:
                        self.results[item_id] = "ok"
                    if attempt > 0:
                        logging.info(f"OK (после {attempt + 1} попыток): {name}")
                    else:
                        logging.info(f"OK: {name}")
                else:
                    self.fail_count += 1
                    if item_id:
                        self.results[item_id] = "fail"
                    self._emit(type="progress",
                               text=f"Ошибка {name} (код {last_returncode})",
                               severity="error")
                    logging.error(f"Ошибка {name}: код {last_returncode} "
                                  f"(после {attempt + 1} попыток)")

                    # --- Предложение отката ---
                    if task.get("uninstall_cmd"):
                        self._emit(type="progress",
                                   text=f"Откат {name}...", severity="warn")
                        if run_uninstall(task):
                            self.rollbacks[name] = "rolled_back"
                            self._emit(type="progress",
                                       text=f"Откат {name}: успешно", severity="info")
                        else:
                            self.rollbacks[name] = "rollback_failed"
                            self._emit(type="progress",
                                       text=f"Откат {name}: не удался", severity="error")
                    else:
                        self.rollbacks[name] = "no_uninstall"

                self._emit(type="value", percent=int(((index + 1) / self.total_tasks) * 100))
        finally:
            self._emit(type="finished",
                       success=self.success_count,
                       fails=self.fail_count,
                       reboot=self.reboot_needed,
                       results=self.results,
                       rollbacks=self.rollbacks)
