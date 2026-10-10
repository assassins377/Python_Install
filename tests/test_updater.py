"""Offline regression tests for update metadata, integrity and file replacement."""

import hashlib
import io
import os
import stat
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

import pytest

import updater
import utils


@pytest.fixture
def release(monkeypatch):
    data = {
        "tag_name": "v9.0.0",
        "body": "Release notes",
        "assets": [
            {"name": updater.exe_asset_name(), "browser_download_url": "https://example.com/app", "size": 42},
            {"name": updater.sha256_asset_name(), "browser_download_url": "https://example.com/sha256"},
        ],
    }
    monkeypatch.setattr(updater, "_fetch_json", lambda *a, **kw: data)
    monkeypatch.setattr(updater, "_fetch_text", lambda *a, **kw: "A" * 64 + "  app\n")
    return data


@pytest.mark.parametrize("current,available", [("1.0.0", True), ("9.0.0", False)])
def test_release_metadata_and_checksum(release, current, available):
    assert updater.check_for_updates(current) == {
        "has_update": available,
        "latest": "9.0.0",
        "url": "https://example.com/app",
        "sha256": "a" * 64,
        "size": 42,
        "notes": "Release notes",
    }


@pytest.mark.parametrize("missing", ["tag_name", "assets", "browser_download_url"])
def test_incomplete_release_is_rejected(release, missing):
    if missing == "browser_download_url":
        del release["assets"][0][missing]
    else:
        del release[missing]
    assert "error" in updater.check_for_updates("1.0.0")


def test_release_network_error(monkeypatch):
    monkeypatch.setattr(updater, "_fetch_json", Mock(side_effect=OSError("offline")))
    assert updater.check_for_updates() == {"error": "offline"}


def test_fetch_json_rejects_non_object(monkeypatch):
    monkeypatch.setattr(
        updater, "_build_opener",
        lambda _: SimpleNamespace(open=lambda *a, **kw: io.BytesIO(b"[]")),
    )
    with pytest.raises(ValueError, match="JSON object"):
        updater._fetch_json("https://example.com/release")


def test_download_hash_and_progress(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        headers: ClassVar[dict[str, str]] = {"Content-Length": "6"}

    monkeypatch.setattr(
        utils, "_build_opener",
        lambda _: SimpleNamespace(open=lambda *a, **kw: Response(b"abcdef")),
    )
    monkeypatch.setattr(utils.config, "DOWNLOAD_CHUNK_SIZE", 2)
    events = []
    dst = tmp_path / "update"
    digest = updater._download_with_progress("https://example.com/app", str(dst), 6, events.append)
    assert dst.read_bytes() == b"abcdef"
    assert digest == hashlib.sha256(b"abcdef").hexdigest()
    assert [event["percent"] for event in events] == [33, 66, 100]


@pytest.mark.parametrize("apply_update,suffix", [
    (updater._apply_update_windows, ".exe"),
    (updater._apply_update_appimage, ".AppImage"),
])
def test_bad_checksum_keeps_current_binary(tmp_path, monkeypatch, apply_update, suffix):
    current = tmp_path / ("MInstAll" + suffix)
    current.write_bytes(b"current")
    monkeypatch.setattr(updater.sys, "executable", str(current))
    monkeypatch.delenv("APPIMAGE", raising=False)

    def download(url, dst, size, callback):
        with open(dst, "wb") as stream:
            stream.write(b"corrupt")
        return "b" * 64

    monkeypatch.setattr(updater, "_download_with_progress", download)
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    events = []
    assert not apply_update({"url": "https://example.com/app", "sha256": "a" * 64}, events.append)
    assert current.read_bytes() == b"current"
    assert not (tmp_path / (current.name + ".new")).exists()
    assert events[-1]["type"] == "error"
    launch.assert_not_called()


@pytest.mark.parametrize("mode", [0o700, 0o750, 0o777, 0o4755])
def test_appimage_replacement_preserves_restricted_access(tmp_path, monkeypatch, mode):
    current = tmp_path / "MInstAll.AppImage"
    current.write_bytes(b"old")
    current.chmod(mode)
    monkeypatch.setenv("APPIMAGE", str(current))
    monkeypatch.setattr(updater.sys, "argv", [str(current), "--help"])

    def download(url, dst, size, callback):
        with open(dst, "wb") as stream:
            stream.write(b"new")
        return hashlib.sha256(b"new").hexdigest()

    monkeypatch.setattr(updater, "_download_with_progress", download)
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    events = []
    assert updater._apply_update_appimage({
        "url": "https://example.com/app",
        "sha256": hashlib.sha256(b"new").hexdigest().upper(),
    }, events.append)
    assert current.read_bytes() == b"new"
    assert not (tmp_path / "MInstAll.AppImage.new").exists()
    if os.name != "nt":
        actual = stat.S_IMODE(current.stat().st_mode)
        assert actual & 0o700 == 0o700
        assert actual & 0o7000 == 0
        assert actual & 0o022 == 0
        assert actual & 0o055 == mode & 0o055
    assert events[-1]["type"] == "done"
    launch.assert_called_once()
    args = launch.call_args.args[0]
    assert args[:2] == ["/bin/sh", "-c"]
    assert args[4:6] == [str(os.getpid()), updater.config.LOG_FILE]
    assert args[6:] == [str(current), "--help"]
    assert launch.call_args.kwargs["start_new_session"] is True
    assert launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_failed_replace_cleans_download_and_keeps_current(tmp_path, monkeypatch):
    current = tmp_path / "MInstAll.AppImage"
    current.write_bytes(b"old")
    monkeypatch.setenv("APPIMAGE", str(current))

    def download(url, dst, size, callback):
        with open(dst, "wb") as stream:
            stream.write(b"new")
        return "a" * 64

    monkeypatch.setattr(updater, "_download_with_progress", download)
    monkeypatch.setattr(updater.os, "replace", Mock(side_effect=OSError("read-only")))
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    events = []
    assert not updater._apply_update_appimage(
        {"url": "https://example.com/app", "sha256": "a" * 64}, events.append,
    )
    assert current.read_bytes() == b"old"
    assert not (tmp_path / "MInstAll.AppImage.new").exists()
    assert events[-1]["type"] == "error"
    launch.assert_not_called()
