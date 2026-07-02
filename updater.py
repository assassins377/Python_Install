from __future__ import annotations

import contextlib
import json
import logging
import os
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.request
from collections.abc import Callable

import config
from core_impl import compare_versions
from utils import _build_opener, _download_file

# --- GitHub Releases API ---
GITHUB_REPO = "assassins377/Python_Install"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

USER_AGENT = f"MInstAll/{config.APP_VERSION}"


def current_arch() -> str:
    """Возвращает 'x64' или 'x86' в зависимости от разрядности текущего процесса.

    Важно: смотрим именно на текущий Python/exe, а не на ОС — потому что 32-битный
    exe может бежать на 64-битной системе, и обновлять его надо тем же x86.
    """
    return "x64" if struct.calcsize("P") == 8 else "x86"


def exe_asset_name() -> str:
    """Имя основного ассета в Release для текущей платформы и архитектуры.

    Windows -> .exe, Linux -> .AppImage.
    """
    ext = ".exe" if os.name == "nt" else ".AppImage"
    return f"MInstAll_{current_arch()}{ext}"


def sha256_asset_name() -> str:
    """Имя .sha256 ассета для текущей архитектуры."""
    return f"{exe_asset_name()}.sha256"


# ------------------------------------------------------------------
# Проверка обновлений через GitHub Releases API
# ------------------------------------------------------------------
def _fetch_json(url: str, timeout: int = 5) -> dict:
    opener = _build_opener(USER_AGENT)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with opener.open(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_text(url: str, timeout: int = 5) -> str:
    opener = _build_opener(USER_AGENT)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8").strip()


def check_for_updates(current_version: str | None = None) -> dict:
    """
    Запрашивает GitHub Releases API. Возвращает dict:
      {"has_update": bool, "latest": str, "url": str, "sha256": str | None,
       "size": int | None, "notes": str}
    или {"error": str} при сбое.

    Если в Release есть файл .sha256 — он скачивается для верификации.
    Если нет — SHA-256 верификация пропускается (с предупреждением в логе).
    """
    current = current_version or config.APP_VERSION

    try:
        release = _fetch_json(RELEASES_API_URL, timeout=5)
    except Exception as e:
        logging.warning(f"Не удалось получить релизы с GitHub: {e}")
        return {"error": str(e)}

    # Тег вида "v2.1.0" → "2.1.0"
    tag = str(release.get("tag_name", "")).lstrip("v")
    if not tag:
        return {"error": "Не удалось определить версию релиза (отсутствует tag_name)"}

    notes = release.get("body", "") or ""
    assets = release.get("assets", []) or []

    # Динамическое имя по архитектуре текущего процесса (x86 / x64)
    exe_name = exe_asset_name()
    sha_name = sha256_asset_name()

    # Ищем основной .exe
    exe_asset = next((a for a in assets if a.get("name") == exe_name), None)
    if not exe_asset:
        return {"error": f"В релизе v{tag} нет файла {exe_name}"}

    exe_url = exe_asset.get("browser_download_url")
    exe_size = exe_asset.get("size")
    if not exe_url:
        return {"error": f"У файла {exe_name} нет ссылки на скачивание"}

    # Опционально: .sha256 — отдельный файл с хешем
    sha256: str | None = None
    sha_asset = next((a for a in assets if a.get("name") == sha_name), None)
    if sha_asset and (sha_url := sha_asset.get("browser_download_url")):
        try:
            sha_text = _fetch_text(sha_url, timeout=5)
            # Формат может быть: "abc123..." или "abc123...  MInstAll_x86.exe"
            sha256 = sha_text.split()[0].lower()
        except Exception as e:
            logging.warning(f"Не удалось прочитать {sha_name}: {e}")

    if not sha256:
        logging.warning(
            f"В релизе v{tag} нет {sha_name} — обновление пройдёт без верификации SHA-256"
        )

    has_update = compare_versions(tag, current) > 0
    return {
        "has_update": has_update,
        "latest": tag,
        "url": exe_url,
        "sha256": sha256,
        "size": exe_size,
        "notes": notes,
    }


def check_for_updates_async(callback: Callable[[dict], None]) -> None:
    """
    Асинхронная обёртка. callback вызывается из фонового потока —
    GUI должен маршалить в UI-поток сам.
    """
    def _worker() -> None:
        callback(check_for_updates())
    threading.Thread(target=_worker, daemon=True).start()


# ------------------------------------------------------------------
# Скачивание и применение обновления
# ------------------------------------------------------------------
def _download_with_progress(
    url: str,
    dst_path: str,
    expected_size: int | None,
    callback: Callable[[dict], None],
) -> str:

    def _wrap_cb(total: int, downloaded: int) -> None:
        effective_total = expected_size or total
        if effective_total:
            callback({
                "type": "progress",
                "percent": int(downloaded * 100 / effective_total),
            })

    return _download_file(
        url, dst_path, user_agent=USER_AGENT, progress_cb=_wrap_cb,
    )


def download_and_update(
    update_info: dict,
    callback: Callable[[dict], None] | None = None,
) -> bool:
    """
    Скачивает и применяет обновление собранного приложения.

    Windows (.exe): формирует BAT для атомарной замены запущенного файла.
    Linux (AppImage): атомарно заменяет .AppImage и перезапускает его.
    """
    def emit(msg: dict) -> None:
        if callback:
            with contextlib.suppress(Exception):
                callback(msg)

    if not getattr(sys, "frozen", False):
        emit({"type": "error", "text": "Обновление работает только для собранного приложения"})
        return False

    if os.name == "nt":
        return _apply_update_windows(update_info, emit)
    return _apply_update_appimage(update_info, emit)


def _apply_update_windows(update_info: dict, emit) -> bool:
    """Замена запущенного .exe через временный BAT (см. ниже)."""
    current_exe = sys.executable
    exe_dir = os.path.dirname(current_exe)
    new_exe_path = current_exe + ".new"
    bak_exe_path = current_exe + ".bak"
    bat_path = os.path.join(tempfile.gettempdir(), "minstall_updater.bat")

    for stale in (new_exe_path, bak_exe_path):
        try:
            if os.path.exists(stale):
                os.remove(stale)
        except OSError:
            pass

    try:
        emit({"type": "status", "text": "Скачивание обновления..."})
        actual_sha = _download_with_progress(
            update_info["url"], new_exe_path, update_info.get("size"), emit
        )

        # Верификация SHA-256 — только если ожидаемый хеш известен
        expected_sha = update_info.get("sha256")
        if expected_sha:
            if actual_sha != expected_sha.lower():
                logging.error(
                    f"SHA-256 не совпадает. Ожидалось {expected_sha}, получено {actual_sha}"
                )
                with contextlib.suppress(OSError):
                    os.remove(new_exe_path)
                emit({"type": "error",
                      "text": "Контрольная сумма не совпадает. Файл повреждён или подменён."})
                return False
            logging.info(f"Обновление скачано и проверено: SHA-256 {actual_sha}")
        else:
            logging.warning(
                f"SHA-256 верификация пропущена (хеш не предоставлен). Загружено: {actual_sha}"
            )

        emit({"type": "status", "text": "Применение обновления..."})

        # PID текущего процесса — BAT будет ждать пока он не завершится,
        # вместо хрупкого ping-таймаута. Максимум — 60 секунд защиты от вечного цикла.
        current_pid = os.getpid()

        bat_content = f"""@echo off
chcp 866 > nul
echo Обновление MInstAll...

REM Ждём пока текущий процесс завершится (max 60 сек защита от deadlock)
set /a "waited=0"
:wait_loop
tasklist /FI "PID eq {current_pid}" /NH 2>nul | findstr /R /C:"^.* {current_pid} " >nul
if errorlevel 1 goto proceed
if %waited% GEQ 60 (
    echo Процесс {current_pid} не завершился за 60 секунд, прерываем
    del /Q "{new_exe_path}" >nul 2>&1
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
set /a "waited+=1"
goto wait_loop

:proceed
move /Y "{current_exe}" "{bak_exe_path}" >nul 2>&1
if errorlevel 1 (
    echo Не удалось создать резервную копию.
    del /Q "{new_exe_path}" >nul 2>&1
    pause
    exit /b 1
)

move /Y "{new_exe_path}" "{current_exe}" >nul 2>&1
if errorlevel 1 (
    echo Не удалось применить обновление, откат.
    move /Y "{bak_exe_path}" "{current_exe}" >nul 2>&1
    pause
    exit /b 1
)

del /Q "{bak_exe_path}" >nul 2>&1
start "" "{current_exe}"
del "%~f0"
"""
        with open(bat_path, "w", encoding="cp866", errors="replace") as f:
            f.write(bat_content)

        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=config.CREATE_NO_WINDOW,
            cwd=exe_dir,
        )

        emit({"type": "done"})
        logging.info("Передача управления updater.bat. Завершение работы.")
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as e:
        logging.exception(f"Ошибка при обновлении: {e}")
        try:
            if os.path.exists(new_exe_path):
                os.remove(new_exe_path)
        except OSError:
            pass
        emit({"type": "error", "text": f"Ошибка: {e}"})
        return False


def _apply_update_appimage(update_info: dict, emit) -> bool:
    """Атомарно заменяет запущенный AppImage и перезапускает его.

    AppImage при запуске монтирует свой squashfs с исходного inode, поэтому
    замена файла по имени не ломает текущий процесс — он дорабатывает до
    sys.exit(), а новый AppImage стартует уже с обновлённого файла.
    """
    appimage_path = os.environ.get("APPIMAGE") or sys.executable
    if not (appimage_path.lower().endswith(".appimage") or os.environ.get("APPIMAGE")):
        emit({"type": "error", "text": "Самообновление на Linux поддерживается только для AppImage"})
        return False

    new_path = appimage_path + ".new"
    try:
        if os.path.exists(new_path):
            os.remove(new_path)
    except OSError:
        pass

    try:
        emit({"type": "status", "text": "Скачивание обновления..."})
        actual_sha = _download_with_progress(
            update_info["url"], new_path, update_info.get("size"), emit
        )

        expected_sha = update_info.get("sha256")
        if expected_sha and actual_sha != expected_sha.lower():
            logging.error(
                f"SHA-256 не совпадает. Ожидалось {expected_sha}, получено {actual_sha}"
            )
            with contextlib.suppress(OSError):
                os.remove(new_path)
            emit({"type": "error",
                  "text": "Контрольная сумма не совпадает. Файл повреждён или подменён."})
            return False
        if not expected_sha:
            logging.warning(
                f"SHA-256 верификация пропущена (хеш не предоставлен). Загружено: {actual_sha}"
            )

        emit({"type": "status", "text": "Применение обновления..."})
        os.chmod(new_path, 0o755)

        # Атомарная замена: старый inode остаётся смонтирован у текущего
        # процесса, новый файл встаёт на имя .AppImage.
        os.replace(new_path, appimage_path)
        os.chmod(appimage_path, 0o755)

        emit({"type": "done"})
        logging.info("AppImage обновлён, перезапуск.")
        # Перезапуск нового AppImage; текущий процесс завершается.
        subprocess.Popen([appimage_path] + sys.argv[1:])
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as e:
        logging.exception(f"Ошибка при обновлении AppImage: {e}")
        try:
            if os.path.exists(new_path):
                os.remove(new_path)
        except OSError:
            pass
        emit({"type": "error", "text": f"Ошибка: {e}"})
        return False


def check_program_update(program: dict, timeout: int = 5) -> dict:
    """Проверяет наличие новой версии у программы по её url (best-effort).

    Делает HEAD-запрос, следует за редиректами и извлекает версию из итогового
    URL (имя файла ассета). Сравнивает с program['version'] через compare_versions.

    Возвращает dict: {"name", "current", "latest", "has_update", "url", "error"}.
    Работает только для записей с url и version; иначе поле error поясняет причину.
    """
    from scanner import extract_version_from_filename
    from core_impl import compare_versions

    result = {
        "name": program.get("name", ""),
        "current": program.get("version"),
        "latest": None,
        "has_update": False,
        "url": program.get("url", ""),
        "error": None,
    }
    url = program.get("url")
    cur = program.get("version")
    if not url:
        result["error"] = "нет url"
        return result
    if not cur:
        result["error"] = "нет version в каталоге"
        return result

    final_url = None
    try:
        opener = _build_opener(USER_AGENT)
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with opener.open(req, timeout=timeout) as resp:
            final_url = resp.geturl()
    except Exception:
        # ряд серверов не принимают HEAD — пробуем GET без скачивания тела
        try:
            opener = _build_opener(USER_AGENT)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout) as resp:
                final_url = resp.geturl()
        except Exception as e:
            result["error"] = str(e)
            return result

    fname = (final_url or "").rstrip("/").split("/")[-1]
    latest = extract_version_from_filename(fname) or extract_version_from_filename(url)
    result["latest"] = latest or None
    if latest:
        result["has_update"] = compare_versions(latest, cur) > 0
    return result
