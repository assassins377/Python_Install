from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shlex
import subprocess
import sys
import urllib.request

import config


def setup_logging() -> None:
    try:
        logging.basicConfig(
            filename=config.LOG_FILE,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8",
        )
    except TypeError:
        _logger = logging.getLogger()
        _logger.setLevel(logging.INFO)
        _fh = logging.FileHandler(config.LOG_FILE)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _logger.addHandler(_fh)


def resolve_path(rel_path: str) -> str:
    if os.name != "nt":
        rel_path = rel_path.replace("\\", "/")
    return os.path.join(config.SCRIPT_DIR, rel_path)



def load_programs_from_json() -> dict[str, list[dict]]:
    import json

    if not os.path.exists(config.CONFIG_FILE):
        logging.error(f"Файл конфигурации не найден: {config.CONFIG_FILE}")
        return {}

    try:
        with open(config.CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Не удалось прочитать {config.CONFIG_FILE}: {e}")
        return {}

    if not isinstance(data, dict):
        logging.error(
            f"Некорректная структура {config.CONFIG_FILE}: ожидается объект"
        )
        return {}

    categories = data.get("categories")
    if categories is None:
        logging.error(
            f"В {config.CONFIG_FILE} отсутствует ключ 'categories'"
        )
        return {}
    if not isinstance(categories, dict):
        logging.error(
            f"Некорректный тип 'categories' в {config.CONFIG_FILE}: ожидается объект"
        )
        return {}

    valid: dict[str, list[dict]] = {}
    for cat_name, programs in categories.items():
        if not isinstance(programs, list):
            logging.warning(
                f"Категория '{cat_name}': ожидается список программ, пропущена"
            )
            continue
        valid_progs: list[dict] = []
        for i, prog in enumerate(programs):
            if not isinstance(prog, dict):
                logging.warning(
                    f"Категория '{cat_name}', элемент {i}: ожидается объект, пропущен"
                )
                continue
            if "name" not in prog or "cmd" not in prog:
                logging.warning(
                    f"Категория '{cat_name}', элемент {i}: отсутствует 'name' или 'cmd', пропущен"
                )
                continue
            valid_progs.append(prog)
        valid[cat_name] = valid_progs

    return valid



def _normalize_cmd_paths(cmd_str: str) -> str:
    if os.name == "nt":
        return cmd_str
    return cmd_str.replace("\\", "/")


def validate_cmd(cmd_str: str) -> str | None:
    cmd_str = _normalize_cmd_paths(cmd_str)
    for char in config.SHELL_METACHARACTERS:
        if char in cmd_str:
            return f"Недопустимый символ '{char}' в команде"

    parts = shlex.split(cmd_str, posix=(os.name != "nt"))
    if not parts:
        return "Пустая команда"

    first = parts[0].lower()
    ext = os.path.splitext(first)[1]

    if not ext and os.path.basename(first) in config.ALLOWED_BARE_COMMANDS:
        return None

    if ext and ext not in config.ALLOWED_CMD_EXTENSIONS:
        return f"Недопустимое расширение '{ext}'"

    if not ext:
        return f"Команда без расширения и не в списке разрешённых: '{parts[0]}'"

    return None



def is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False



def relaunch_as_admin() -> bool:
    if os.name != "nt":
        if getattr(sys, "frozen", False):
            params = [sys.executable] + sys.argv[1:]
        else:
            params = [
                sys.executable,
                os.path.abspath(sys.argv[0]),
            ] + sys.argv[1:]

        env_vars: list[str] = []
        for key in (
            "DISPLAY",
            "XAUTHORITY",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "HOME",
            "XDG_RUNTIME_DIR",
            "LANG",
            "LC_ALL",
            "LANGUAGE",
        ):
            if key in os.environ:
                env_vars.append(f"{key}={os.environ[key]}")

        try:
            return subprocess.call(["pkexec", "env"] + env_vars + params) == 0
        except Exception:
            pass
        try:
            return (
                subprocess.call(
                    ["sudo", "-E", "--"] + params,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                == 0
            )
        except Exception:
            pass
        return False

    import ctypes

    exe = sys.executable
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        params = subprocess.list2cmdline(
            [os.path.abspath(sys.argv[0])] + sys.argv[1:]
        )
    try:
        return (
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, params, config.SCRIPT_DIR, 1
            )
            > 32
        )
    except Exception:
        return False



def _build_opener(user_agent: str) -> urllib.request.OpenerDirector:
    class _LoggingRedirectHandler(urllib.request.HTTPRedirectHandler):
        max_redirects = config.MAX_REDIRECTS

        def redirect_request(
            self, req, fp, code, msg, headers, newurl,
        ):
            if not hasattr(req, "_redirect_count"):
                req._redirect_count = 0  # type: ignore[attr-defined]
            req._redirect_count += 1  # type: ignore[attr-defined]
            if req._redirect_count > self.max_redirects:  # type: ignore[attr-defined]
                raise urllib.request.HTTPError(
                    req.full_url, code,
                    f"Слишком много редиректов (> {self.max_redirects})",
                    headers, fp,
                )
            logging.info(
                f"HTTP {code} → {newurl} (редирект "
                f"{req._redirect_count}/{self.max_redirects})"  # type: ignore[attr-defined]
            )
            return super().redirect_request(
                req, fp, code, msg, headers, newurl,
            )
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(),
        _LoggingRedirectHandler(),
    )



def _download_file(
    url: str,
    dst_path: str,
    user_agent: str,
    progress_cb: callable | None = None,
) -> str:
    """
    Скачивает файл из url в dst_path. Возвращает SHA-256 хеш (hex).

    progress_cb вызывается как progress_cb(total=total_bytes, downloaded=N).
    Бросает RuntimeError при ошибке скачивания.
    """
    sha = hashlib.sha256()
    downloaded = 0

    opener = _build_opener(user_agent)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with opener.open(req, timeout=config.DOWNLOAD_TIMEOUT) as resp:
            total = 0
            try:
                cl = resp.headers.get("Content-Length")
                if cl:
                    total = int(cl)
            except Exception:
                pass

            with open(dst_path, "wb") as out:
                while True:
                    chunk = resp.read(config.DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        with contextlib.suppress(Exception):
                            progress_cb(total=total, downloaded=downloaded)
    except Exception as e:
        try:
            if os.path.exists(dst_path):
                os.remove(dst_path)
        except OSError:
            pass
        raise RuntimeError(f"Ошибка скачивания {url}: {e}") from e

    return sha.hexdigest().lower()



def dispatch_cmd(script_path: str, user_args: list[str]) -> tuple[list[str], str]:
    """
    Собирает команду запуска для уже разрешённого пути к инсталлятору.

    Выделено из build_cmd, чтобы переиспользовать ту же кросс-платформенную
    диспетчеризацию по расширению для скачанных файлов (см. installer.py),
    не гоняя путь через строку (пути с пробелами ломают round-trip shlex).
    """
    ext = os.path.splitext(script_path)[1].lower()

    if ext == ".reg":
        return (["regedit", "/s", script_path], script_path)
    if ext in (".bat", ".cmd"):
        return (["cmd", "/c", script_path] + user_args, script_path)
    if ext == ".ps1":
        return (
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-NonInteractive",
                "-File",
                script_path,
            ]
            + user_args,
            script_path,
        )
    if ext == ".msi":
        return (
            ["msiexec", "/i", script_path, "/qn", "/norestart"] + user_args,
            script_path,
        )
    if ext in (".sh", ".bash"):
        return (["bash", script_path] + user_args, script_path)
    if ext == ".deb":
        cmd_list = ["apt-get", "install", "-y", script_path] + user_args
        if os.name != "nt" and not is_admin():
            cmd_list = ["pkexec"] + cmd_list
        return (cmd_list, script_path)
    if ext == ".appimage":
        return ([script_path] + user_args, script_path)
    return ([script_path] + user_args, script_path)


def build_cmd(cmd_str: str) -> tuple[list[str], str]:
    cmd_str = _normalize_cmd_paths(cmd_str)
    error = validate_cmd(cmd_str)
    if error:
        raise ValueError(error)

    parts = shlex.split(cmd_str, posix=(os.name != "nt"))
    first = parts[0]
    user_args = parts[1:]
    ext = os.path.splitext(first)[1].lower()

    if not ext and os.path.basename(first).lower() in config.ALLOWED_BARE_COMMANDS:
        return ([first] + user_args, "")

    script_path = resolve_path(first)
    return dispatch_cmd(script_path, user_args)


def find_latest_install_log(program_name: str) -> str | None:
    """Находит последний файл лога установки для указанной программы."""
    import glob
    import re
    # Нормализуем имя аналогично _safe_log_name в installer.py
    safe = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_\- ]", "_", program_name.strip())
    safe = re.sub(r"\s+", "_", safe)
    if not safe:
        return None

    try:
        os.makedirs(config.INSTALL_LOGS_DIR, exist_ok=True)
    except Exception:
        return None

    pattern = os.path.join(config.INSTALL_LOGS_DIR, f"{safe}_*.log")
    files = glob.glob(pattern)
    if not files:
        return None

    try:
        return max(files, key=os.path.getmtime)
    except Exception:
        return None


from deps import resolve_dependencies, topological_levels  # noqa: E402, F401
from installer import (  # noqa: E402, F401
    RETRYABLE_EXIT_CODES,
    InstallWorker,
    download_installer,
    is_installer_available,
    run_hook,
    run_uninstall,
)
from registry import (  # noqa: E402, F401
    _get_installed_programs_uncached,
    _normalize_for_match,
    build_status_cache,
    check_status,
    compare_versions,
    get_installed_programs,
    get_net_framework_release,
    invalidate_caches,
    invalidate_installed_cache,
    is_program_applicable,
    parse_version,
)
