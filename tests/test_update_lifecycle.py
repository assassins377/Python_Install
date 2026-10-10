"""Updater integrity and GUI-worker handoff, with no real process launches."""
import hashlib
import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

import updater


@pytest.mark.parametrize("checksum", [None, "", "a" * 63, "g" * 64, 42, "a" * 64 + " extra"])
@pytest.mark.parametrize("apply_update,suffix", [
    (updater._apply_update_windows, ".exe"),
    (updater._apply_update_appimage, ".AppImage"),
])
def test_invalid_checksum_stops_before_download(tmp_path, monkeypatch, checksum, apply_update, suffix):
    current = tmp_path / ("app" + suffix)
    current.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(current))
    monkeypatch.delenv("APPIMAGE", raising=False)
    download = Mock()
    launch = Mock()
    monkeypatch.setattr(updater, "_download_with_progress", download)
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    events = []
    assert not apply_update({"url": "https://example.com/app", "sha256": checksum}, events.append)
    download.assert_not_called()
    launch.assert_not_called()
    assert current.read_bytes() == b"old"
    assert events[-1]["type"] == "error"
    assert list(tmp_path.iterdir()) == [current]


@pytest.mark.parametrize("failure", ["missing", "no-url", "empty", "invalid", "network"])
def test_release_requires_available_valid_checksum(monkeypatch, failure):
    asset = {"name": updater.exe_asset_name(), "browser_download_url": "https://example.com/app"}
    checksum = {"name": updater.sha256_asset_name(), "browser_download_url": "https://example.com/sha"}
    assets = [asset] if failure == "missing" else [asset, checksum]
    if failure == "no-url":
        checksum.pop("browser_download_url")
    monkeypatch.setattr(updater, "_fetch_json", lambda *args, **kwargs: {"tag_name": "v9.0.0", "assets": assets})
    text = Mock(return_value="" if failure == "empty" else "not-a-sha256 app.exe")
    if failure == "network":
        text.side_effect = OSError("offline")
    monkeypatch.setattr(updater, "_fetch_text", text)
    assert "error" in updater.check_for_updates("1.0.0")


@pytest.mark.parametrize("apply_update,suffix", [
    (updater._apply_update_windows, ".exe"),
    (updater._apply_update_appimage, ".AppImage"),
])
def test_update_worker_returns_success_and_requests_gui_close(tmp_path, monkeypatch, apply_update, suffix):
    current = tmp_path / ("app 测试" + suffix)
    current.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(current))
    monkeypatch.setattr(updater.sys, "argv", [str(current), "--no-elevate"])
    monkeypatch.delenv("APPIMAGE", raising=False)
    digest = hashlib.sha256(b"new").hexdigest()

    def download(url, dst, size, emit):
        Path(dst).write_bytes(b"new")
        return digest

    monkeypatch.setattr(updater, "_download_with_progress", download)
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    events = []
    results = []
    worker = threading.Thread(target=lambda: results.append(apply_update(
        {"url": "https://example.com/app", "sha256": digest}, events.append,
    )))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert results == [True]  # SystemExit in a worker would leave this empty.
    assert events[-1] == {"type": "done"}
    launch.assert_called_once()
    if suffix == ".exe":
        assert current.read_bytes() == b"old"  # Helper waits for GUI exit.
        args = launch.call_args.args[0]
        manifest = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
        assert manifest["current"] == str(current)
        assert manifest["sha256"] == digest
        assert Path(manifest["new"]).read_bytes() == b"new"
        assert manifest["arguments"] == "--no-elevate"
    else:
        assert current.read_bytes() == b"new"


def test_windows_helper_launch_failure_cleans_staging(tmp_path, monkeypatch):
    current = tmp_path / "app.exe"
    current.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(current))

    def download(url, dst, size, emit):
        Path(dst).write_bytes(b"new")
        return "a" * 64

    monkeypatch.setattr(updater, "_download_with_progress", download)
    monkeypatch.setattr(updater.subprocess, "Popen", Mock(side_effect=OSError("cannot start")))
    events = []
    assert not updater._apply_update_windows({"url": "https://example.com/app", "sha256": "a" * 64}, events.append)
    assert current.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [current]
    assert events[-1]["type"] == "error"


@pytest.mark.parametrize("module_path", [None, r"C:\User Modules;C:\Machine Modules"])
def test_windows_manifest_preserves_original_module_path(tmp_path, monkeypatch, module_path):
    current = tmp_path / "app.exe"
    current.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(current))
    if module_path is None:
        monkeypatch.delenv("PSMODULEPATH", raising=False)
    else:
        monkeypatch.setenv("PSMODULEPATH", module_path)

    def download(url, dst, size, emit):
        Path(dst).write_bytes(b"new")
        return "a" * 64

    monkeypatch.setattr(updater, "_download_with_progress", download)
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    assert updater._apply_update_windows({"url": "https://example.com/app", "sha256": "a" * 64}, lambda _: None)
    manifest = json.loads(Path(launch.call_args.args[0][-1]).read_text(encoding="utf-8"))
    assert manifest["ps_module_path"] == module_path


def test_appimage_restart_preserves_arguments_and_environment(monkeypatch):
    arguments = ["space and 'quotes'", "$(touch unwanted)", "", "日本語"]
    monkeypatch.setattr(updater.sys, "argv", ["app.AppImage", *arguments])
    monkeypatch.setenv("MINSTALL_CUSTOM_ENV", "preserve me")
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    updater._launch_appimage_restart("/path with spaces/app.AppImage")
    assert launch.call_args.args[0][6:] == ["/path with spaces/app.AppImage", *arguments]
    assert launch.call_args.kwargs["env"]["MINSTALL_CUSTOM_ENV"] == "preserve me"
    assert launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
