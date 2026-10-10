from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.request
from collections.abc import Callable
from typing import cast

import config
from core_impl import compare_versions
from utils import _build_opener, _download_file, redact_urls

# --- GitHub Releases API ---
GITHUB_REPO = "assassins377/Python_Install"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

USER_AGENT = f"MInstAll/{config.APP_VERSION}"


def _required_checksum(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("Обновление остановлено: отсутствует корректная SHA-256")
    return value.lower()


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
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Release response must be a JSON object")
    return data


def _fetch_text(url: str, timeout: int = 5) -> str:
    opener = _build_opener(USER_AGENT)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as response:
        return cast(bytes, response.read()).decode("utf-8").strip()


def check_for_updates(current_version: str | None = None) -> dict:
    """
    Запрашивает GitHub Releases API. Возвращает dict:
      {"has_update": bool, "latest": str, "url": str, "sha256": str | None,
       "size": int | None, "notes": str}
    или {"error": str} при сбое.

    Файл .sha256 обязателен. При недоступной или некорректной сумме
    обновление не предлагается и скачивание исполняемого файла не начинается.
    """
    current = current_version or config.APP_VERSION

    try:
        release = _fetch_json(RELEASES_API_URL, timeout=5)
    except Exception as e:
        error = redact_urls(str(e))
        logging.warning(f"Не удалось получить релизы с GitHub: {error}")
        return {"error": error}

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

    # A network failure must not silently downgrade update integrity.
    sha_asset = next((a for a in assets if a.get("name") == sha_name), None)
    if not sha_asset or not (sha_url := sha_asset.get("browser_download_url")):
        return {"error": f"Обновление остановлено: в релизе нет {sha_name}"}
    try:
        fields = _fetch_text(sha_url, timeout=5).split()
        sha256 = _required_checksum(fields[0] if fields else None)
    except Exception as e:
        return {"error": f"Не удалось проверить {sha_name}: {redact_urls(str(e))}"}

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

    Windows (.exe): запускает помощник, ожидающий закрытия GUI.
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


# Paths and arguments are data in a UTF-8 manifest, never interpolated into code.
# PowerShell 5.1 is available on supported Windows versions; -LiteralPath avoids
# wildcard interpretation, and the helper waits for the GUI process to exit.
_WINDOWS_UPDATE_SCRIPT = r"""param([string]$Manifest)
# Use this host's built-in modules even when launched from a PowerShell 7 parent.
$env:PSModulePath = [System.IO.Path]::Combine($PSHOME, 'Modules')
$ErrorActionPreference = 'Stop'
$data = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$workDir = Split-Path -Parent $Manifest
$replaced = $false
try {
    $parent = Get-Process -Id $data.pid -ErrorAction SilentlyContinue
    if ($parent -and -not $parent.WaitForExit(60000)) {
        throw 'Application did not close within 60 seconds; update cancelled.'
    }
    # Hash directly with .NET; inherited PSModulePath can hide Get-FileHash
    # when Windows PowerShell is launched by PowerShell 7.
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [System.IO.File]::OpenRead($data.new)
        $digest = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($stream) { $stream.Dispose() }
        $hasher.Dispose()
    }
    if ($digest -ne $data.sha256) { throw 'Update checksum mismatch.' }
    # The onefile bootloader can hold the EXE briefly after its child exits.
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            [System.IO.File]::Replace($data.new, $data.current, $data.backup)
            $replaced = $true
            break
        } catch {
            if ($attempt -eq 19) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
    $start = @{ FilePath = $data.current; WorkingDirectory = (Split-Path -Parent $data.current) }
    if ($data.arguments) { $start.ArgumentList = $data.arguments }
    Start-Process @start
    Remove-Item -LiteralPath $data.backup -Force
} catch {
    $failure = $_.Exception.Message
    if ($replaced -and (Test-Path -LiteralPath $data.backup)) {
        try { [System.IO.File]::Replace($data.backup, $data.current, $null) } catch {}
    }
    $failure | Out-File -LiteralPath $data.log -Encoding UTF8 -Append
    exit 1
} finally {
    # Keep an unrestored backup for recovery if rollback itself failed.
    if (-not (Test-Path -LiteralPath $data.backup)) {
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
"""


def _apply_update_windows(update_info: dict, emit) -> bool:
    """Stage a verified EXE, hand off replacement, then request GUI shutdown."""
    work_dir: str | None = None
    handed_off = False
    try:
        expected_sha = _required_checksum(update_info.get("sha256"))
        current_exe = sys.executable
        exe_dir = os.path.dirname(current_exe)
        work_dir = tempfile.mkdtemp(prefix=".minstall-update-", dir=exe_dir)
        new_path = os.path.join(work_dir, "update.exe")
        emit({"type": "status", "text": "Скачивание обновления..."})
        actual_sha = _download_with_progress(
            update_info["url"], new_path, update_info.get("size"), emit,
        )
        if actual_sha != expected_sha:
            raise ValueError("Контрольная сумма не совпадает. Обновление остановлено.")
        script_path = os.path.join(work_dir, "update.ps1")
        manifest_path = os.path.join(work_dir, "update.json")
        with open(script_path, "w", encoding="utf-8-sig") as stream:
            stream.write(_WINDOWS_UPDATE_SCRIPT)
        with open(manifest_path, "w", encoding="utf-8") as stream:
            json.dump({
                "pid": os.getpid(), "current": current_exe, "new": new_path,
                "backup": os.path.join(work_dir, "previous.exe"), "sha256": expected_sha,
                "arguments": subprocess.list2cmdline(sys.argv[1:]), "log": config.LOG_FILE,
            }, stream, ensure_ascii=False)
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script_path, "-Manifest", manifest_path],
            creationflags=config.CREATE_NO_WINDOW, cwd=exe_dir,
        )
        handed_off = True
        emit({"type": "done"})
        return True
    except Exception as exc:
        error = redact_urls(str(exc))
        logging.error("Ошибка при обновлении: %s", error)
        emit({"type": "error", "text": error})
        return False
    finally:
        if work_dir and not handed_off:
            with contextlib.suppress(OSError):
                shutil.rmtree(work_dir)


def _apply_update_appimage(update_info: dict, emit) -> bool:
    """Атомарно заменяет запущенный AppImage и перезапускает его.

    AppImage при запуске монтирует свой squashfs с исходного inode, поэтому
    замена файла по имени не ломает текущий процесс — он дорабатывает до
    закрытия GUI, а новый AppImage стартует уже с обновлённого файла.
    """
    appimage_path = os.environ.get("APPIMAGE") or sys.executable
    if not (appimage_path.lower().endswith(".appimage") or os.environ.get("APPIMAGE")):
        emit({"type": "error", "text": "Самообновление на Linux поддерживается только для AppImage"})
        return False

    new_path: str | None = None
    try:
        expected_sha = _required_checksum(update_info.get("sha256"))
        fd, new_path = tempfile.mkstemp(prefix=".minstall-update-", dir=os.path.dirname(appimage_path))
        os.close(fd)
        emit({"type": "status", "text": "Скачивание обновления..."})
        actual_sha = _download_with_progress(
            update_info["url"], new_path, update_info.get("size"), emit
        )

        if actual_sha != expected_sha:
            logging.error(
                f"SHA-256 не совпадает. Ожидалось {expected_sha}, получено {actual_sha}"
            )
            with contextlib.suppress(OSError):
                os.remove(new_path)
            emit({"type": "error",
                  "text": "Контрольная сумма не совпадает. Файл повреждён или подменён."})
            return False
        emit({"type": "status", "text": "Применение обновления..."})
        # Preserve existing read/execute access without introducing write access
        # for other users or carrying setuid/setgid bits to the downloaded file.
        mode = stat.S_IMODE(os.stat(appimage_path).st_mode) & 0o755
        os.chmod(new_path, mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        # Атомарная замена: старый inode остаётся смонтирован у текущего
        # процесса, новый файл встаёт на имя .AppImage.
        os.replace(new_path, appimage_path)

        subprocess.Popen([appimage_path, *sys.argv[1:]])
        emit({"type": "done"})
        return True
    except Exception as exc:
        error = redact_urls(str(exc))
        logging.error("Ошибка при обновлении AppImage: %s", error)
        emit({"type": "error", "text": error})
        return False
    finally:
        if new_path:
            with contextlib.suppress(OSError):
                os.remove(new_path)


def check_program_update(program: dict, timeout: int = 5) -> dict:
    """Проверяет наличие новой версии у программы по её url (best-effort).

    Делает HEAD-запрос, следует за редиректами и извлекает версию из итогового
    URL (имя файла ассета). Сравнивает с program['version'] через compare_versions.

    Возвращает dict: {"name", "current", "latest", "has_update", "url", "error"}.
    Работает только для записей с url и version; иначе поле error поясняет причину.
    """
    from core_impl import compare_versions
    from scanner import extract_version_from_filename

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
