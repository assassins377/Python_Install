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


def setup_logging(level: int = logging.INFO) -> None:
    try:
        logging.basicConfig(
            filename=config.LOG_FILE,
            level=level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8",
        )
    except TypeError:
        _logger = logging.getLogger()
        _logger.setLevel(level)
        _fh = logging.FileHandler(config.LOG_FILE)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _logger.addHandler(_fh)


def resolve_path(rel_path: str) -> str:
    if os.name != "nt":
        rel_path = rel_path.replace("\\", "/")
    return os.path.join(config.SCRIPT_DIR, rel_path)


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

    handlers: list = [urllib.request.HTTPCookieProcessor(), _LoggingRedirectHandler()]

    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        logging.info(f"Используется прокси: {proxy_url}")

    return urllib.request.build_opener(*handlers)


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
    if ext == ".AppImage":
        return ([script_path] + user_args, script_path)
    return ([script_path] + user_args, script_path)


def build_cmd(cmd_str: str) -> tuple[list[str], str]:
    cmd_clean = _normalize_cmd_paths(cmd_str)
    err = validate_cmd(cmd_clean)
    if err:
        raise ValueError(err)

    try:
        parts = shlex.split(cmd_clean, posix=(os.name != "nt"))
    except ValueError as e:
        raise ValueError(f"Ошибка парсинга команды: {e}") from e

    if not parts:
        return ([], "")

    first = parts[0]
    first_lower = first.lower()
    if os.path.splitext(first_lower)[1] == "" and os.path.basename(first_lower) in config.ALLOWED_BARE_COMMANDS:
        return (parts, "")

    script_path = resolve_path(parts[0])
    return dispatch_cmd(script_path, parts[1:])


def command_needs_root(cmd_str: str) -> bool:
    """Нужно ли для команды повышение привилегий на Linux.

    True для "голых" команд менеджеров пакетов (apt/dpkg/snap/flatpak/...)
    и для локальных .deb — без root они завершатся с ошибкой доступа.
    """
    if not cmd_str:
        return False
    try:
        parts = shlex.split(_normalize_cmd_paths(cmd_str), posix=(os.name != "nt"))
    except ValueError:
        parts = _normalize_cmd_paths(cmd_str).split()
    if not parts:
        return False
    first = parts[0].lower()
    ext = os.path.splitext(first)[1]
    _PM_BARE = {"apt", "apt-get", "dpkg", "snap", "flatpak", "pacman", "rpm", "yum", "dnf"}
    if ext == "" and os.path.basename(first) in _PM_BARE:
        return True
    if ext == ".deb":
        return True
    return False


def relaunch_cli_as_root() -> int | None:
    """Перезапускает текущий CLI-процесс от root на Linux (pkexec/sudo).

    Возвращает код выхода дочернего процесса, либо None, если повышение
    не требуется (Windows / уже root) или не удалось (нет pkexec/sudo).
    Caller должен завершиться с возвращённым кодом, не доходя до повторного
    выполнения, иначе установка запустится дважды.
    """
    if os.name == "nt" or is_admin():
        return None

    if getattr(sys, "frozen", False):
        params = [sys.executable] + sys.argv[1:]
    else:
        params = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]

    env_vars: list[str] = []
    for key in (
        "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY",
        "DBUS_SESSION_BUS_ADDRESS", "HOME", "XDG_RUNTIME_DIR",
        "LANG", "LC_ALL", "LANGUAGE",
    ):
        if key in os.environ:
            env_vars.append(f"{key}={os.environ[key]}")

    try:
        return subprocess.call(["pkexec", "env"] + env_vars + params)
    except Exception:
        pass
    try:
        return subprocess.call(["sudo", "--"] + params)
    except Exception:
        return None
