from __future__ import annotations

import os
import re
import shlex
import subprocess
import time

import config

# Расширения инсталляторов, привязанные к платформе. Если у записи нет явной
# метки "os", применимость выводим из расширения/команды: Windows-инсталлятор
# (.exe/.msi/...) на Linux не запустится, и наоборот.
_WINDOWS_EXTS = {".exe", ".msi", ".reg", ".bat", ".cmd", ".ps1"}
_LINUX_EXTS = {".deb", ".appimage", ".sh", ".bash"}
_WINDOWS_BARE = {"winget", "choco"}
_LINUX_BARE = {"apt", "apt-get", "dpkg", "snap", "flatpak"}


def _cmd_platform(cmd_str: str) -> str | None:
    """Выводит платформу записи из её команды: "windows", "linux" или None.

    None — команда нейтральна (например, голый "apt" встречается только на
    Linux, но неизвестное расширение трактуем как «применимо везде»).
    """
    if not cmd_str:
        return None
    # Пути в JSON бывают с \ (Windows) или / (Linux); нормализуем перед split.
    try:
        parts = shlex.split(cmd_str.replace("\\", "/"), posix=True)
    except ValueError:
        parts = cmd_str.replace("\\", "/").split()
    if not parts:
        return None

    base = os.path.basename(parts[0]).lower()
    ext = os.path.splitext(base)[1]
    if ext:
        if ext in _WINDOWS_EXTS:
            return "windows"
        if ext in _LINUX_EXTS:
            return "linux"
        return None
    if base in _WINDOWS_BARE:
        return "windows"
    if base in _LINUX_BARE:
        return "linux"
    return None

UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]

_net_release_cache: tuple[bool, int | None] = (False, None)


def _query_dpkg() -> list[tuple[str, str]]:
    """Установленные пакеты dpkg (Debian/Ubuntu): (имя_пакета, версия)."""
    entries: list[tuple[str, str]] = []
    try:
        out = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${binary:Package}|${Version}\\n"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return entries
    for line in out.strip().split("\n"):
        if "|" in line:
            name, version = line.split("|", 1)
            entries.append((name, version))
    return entries


def _query_flatpak() -> list[tuple[str, str]]:
    """Установленные flatpak-приложения: и человекочитаемое имя, и app-id.

    App-id (напр. org.mozilla.firefox) добавляется отдельной записью — это
    повышает шанс совпадения с program['name'] по подстроке.
    """
    entries: list[tuple[str, str]] = []
    try:
        out = subprocess.check_output(
            ["flatpak", "list", "--app",
             "--columns=name,application,version"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return entries
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        cols = line.split("\t")
        name = cols[0].strip() if len(cols) > 0 else ""
        app_id = cols[1].strip() if len(cols) > 1 else ""
        version = cols[2].strip() if len(cols) > 2 else ""
        if name:
            entries.append((name, version))
        if app_id and app_id != name:
            entries.append((app_id, version))
    return entries


def _query_snap() -> list[tuple[str, str]]:
    """Установленные snap-пакеты: (имя, версия)."""
    entries: list[tuple[str, str]] = []
    try:
        out = subprocess.check_output(
            ["snap", "list"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return entries
    lines = out.strip().split("\n")
    for line in lines[1:]:  # первая строка — заголовок (Name Version Rev …)
        parts = line.split()
        if len(parts) >= 2:
            entries.append((parts[0], parts[1]))
    return entries


def _query_rpm() -> list[tuple[str, str]]:
    """Установленные RPM-пакеты (Fedora/RHEL): (имя_пакета, версия)."""
    entries: list[tuple[str, str]] = []
    try:
        out = subprocess.check_output(
            ["rpm", "-qa", "--queryformat", "%{NAME}|%{VERSION}\n"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return entries
    for line in out.strip().split("\n"):
        if "|" in line:
            name, version = line.split("|", 1)
            entries.append((name, version))
    return entries


def _query_pacman() -> list[tuple[str, str]]:
    """Установленные pacman-пакеты (Arch/Manjaro): (имя_пакета, версия)."""
    entries: list[tuple[str, str]] = []
    try:
        out = subprocess.check_output(
            ["pacman", "-Q", "--queryformat", "%{NAME}|%{VERSION}\n"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return entries
    for line in out.strip().split("\n"):
        if "|" in line:
            name, version = line.split("|", 1)
            entries.append((name, version))
    return entries


def _get_installed_programs_uncached() -> list[tuple[str, str]]:
    if os.name != "nt":
        import shutil
        entries: list[tuple[str, str]] = []
        if shutil.which("dpkg-query"):
            entries.extend(_query_dpkg())
        if shutil.which("flatpak"):
            entries.extend(_query_flatpak())
        if shutil.which("snap"):
            entries.extend(_query_snap())
        if shutil.which("rpm"):
            entries.extend(_query_rpm())
        if shutil.which("pacman"):
            entries.extend(_query_pacman())
        return entries
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


def get_installed_programs(
    state_dict: dict | None = None,
    use_cache: bool = True,
) -> list[tuple[str, str]]:
    if not use_cache or state_dict is None:
        return _get_installed_programs_uncached()

    cache = state_dict.get("installed_cache")
    if isinstance(cache, dict):
        ts = cache.get("ts", 0)
        if time.time() - ts < config.INSTALLED_CACHE_TTL_SECONDS:
            entries = cache.get("entries", [])
            if isinstance(entries, list):
                return [tuple(e) for e in entries
                        if isinstance(e, (list, tuple)) and len(e) == 2]

    fresh = _get_installed_programs_uncached()
    state_dict["installed_cache"] = {
        "entries": [list(e) for e in fresh],
        "ts": time.time(),
    }
    return fresh


def invalidate_installed_cache(state_dict: dict | None) -> None:
    if state_dict is not None and "installed_cache" in state_dict:
        del state_dict["installed_cache"]


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", str(v))) if v else ()


def compare_versions(a: str, b: str) -> int:
    ta, tb = parse_version(a), parse_version(b)
    n = max(len(ta), len(tb))
    ta, tb = ta + (0,) * (n - len(ta)), tb + (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


def get_net_framework_release(use_cache: bool = True) -> int | None:
    global _net_release_cache
    if use_cache and _net_release_cache[0]:
        return _net_release_cache[1]

    if os.name != "nt":
        _net_release_cache = (True, None)
        return None

    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Release")
            result = int(value)
    except OSError:
        result = None

    _net_release_cache = (True, result)
    return result


def invalidate_caches() -> None:
    global _net_release_cache
    _net_release_cache = (False, None)


def _normalize_for_match(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[\s._\-/\\:;,!?()'\"]+", "", s.lower())


def check_status(
    program: dict,
    installed_entries: list[tuple[str, str]],
) -> tuple[str, str]:
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

    needle_raw = (detect.get("registry_name") or program["name"])
    needle_norm = _normalize_for_match(needle_raw)

    found_version: str | None = None
    if needle_norm:
        for n, v in installed_entries:
            if needle_norm in _normalize_for_match(n):
                found_version = v
                break

    if found_version is None:
        return ("missing", "")

    min_v = detect.get("min_version")
    if min_v and compare_versions(found_version, min_v) < 0:
        return ("outdated", found_version)
    return ("ok", found_version)


def is_program_applicable(program: dict) -> bool:
    """False для записей, неприменимых на текущей ОС.

    Скрывает:
      • явную метку платформы в JSON — program["os"] ("windows" / "linux"),
        если она задана и не совпадает с текущей ОС;
      • Windows-only системные компоненты (detect.net_framework_release) на
        не-Windows — .NET Framework вне Windows не существует, и без фильтра
        такие записи вечно висели бы как «Не установлено»;
      • записи без метки "os", чьё расширение инсталлятора привязано к другой
        платформе (.exe/.msi/... — Windows; .deb/.appimage/... — Linux).
    """
    only = (program.get("os") or "").lower()
    cur = "windows" if os.name == "nt" else "linux"
    if only and only not in (cur, "any", "all"):
        return False

    if os.name != "nt":
        detect = program.get("detect") or {}
        if detect.get("net_framework_release") is not None:
            return False

    # Без явной метки os выводим платформу из расширения команды — иначе на
    # Linux в списке висели бы неустановимые Windows-инсталляторы (.exe/.msi).
    if not only:
        plat = _cmd_platform(program.get("cmd", ""))
        if plat is not None and plat != cur:
            return False

    return True


def build_status_cache(
    programs_db: dict[str, list[dict]],
    installed_entries: list[tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    cache: dict[str, tuple[str, str]] = {}
    for programs in programs_db.values():
        for prog in programs:
            if not is_program_applicable(prog):
                continue
            cache[prog["name"]] = check_status(prog, installed_entries)
    return cache


_PACKAGE_MANAGER_COMMANDS: dict[str, dict[str, str]] = {
    "apt": {
        "update": "apt-get install -y --only-upgrade {package}",
        "uninstall": "apt-get remove -y {package}",
    },
    "dpkg": {
        "uninstall": "dpkg -r {package}",
    },
    "flatpak": {
        "update": "flatpak update -y {app_id}",
        "uninstall": "flatpak uninstall -y {app_id}",
    },
    "snap": {
        "update": "snap refresh {package}",
        "uninstall": "snap remove {package}",
    },
    "rpm": {
        "uninstall": "rpm -e {package}",
    },
    "pacman": {
        "update": "pacman -S --noconfirm {package}",
        "uninstall": "pacman -R --noconfirm {package}",
    },
}


def get_linux_update_command(program: dict) -> str | None:
    cmd_str = program.get("cmd", "")
    if not cmd_str:
        return None
    try:
        parts = shlex.split(cmd_str.replace("\\", "/"), posix=True)
    except ValueError:
        parts = cmd_str.replace("\\", "/").split()
    if not parts:
        return None
    base = parts[0].lower()
    if base not in _PACKAGE_MANAGER_COMMANDS:
        return None
    pm = _PACKAGE_MANAGER_COMMANDS[base]
    if "update" not in pm:
        return None
    pkg = parts[1] if len(parts) > 1 else program.get("name", "")
    return pm["update"].format(package=pkg, app_id=pkg)


def get_linux_uninstall_command(program: dict) -> str | None:
    cmd_str = program.get("cmd", "")
    if not cmd_str:
        return None
    try:
        parts = shlex.split(cmd_str.replace("\\", "/"), posix=True)
    except ValueError:
        parts = cmd_str.replace("\\", "/").split()
    if not parts:
        return None
    base = parts[0].lower()
    if base not in _PACKAGE_MANAGER_COMMANDS:
        return None
    pm = _PACKAGE_MANAGER_COMMANDS[base]
    if "uninstall" not in pm:
        return None
    pkg = parts[1] if len(parts) > 1 else program.get("name", "")
    return pm["uninstall"].format(package=pkg, app_id=pkg)
