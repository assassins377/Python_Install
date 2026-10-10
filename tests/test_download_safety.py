"""Regression tests for URL secrets and concurrent installer downloads."""
import hashlib
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

import pytest

import installer
import utils


@pytest.mark.parametrize("url", [
    "https://demo:sensitive-password@example.com/file?token=sensitive-token#sensitive-fragment",
    "http://demo:sensitive-password@[::1]:8080/?secret=sensitive-token",
])
def test_proxy_and_exception_logs_redact_urls(url, monkeypatch, caplog):
    monkeypatch.setenv("HTTPS_PROXY", url)
    caplog.set_level(logging.INFO)
    utils.setup_logging()
    utils._build_opener("test")
    try:
        raise RuntimeError(url)
    except RuntimeError:
        logging.exception("Failed URL: %s", url)
    assert "sensitive-" not in caplog.text
    assert "REDACTED" in caplog.text


def test_download_error_does_not_expose_url_or_chained_exception(tmp_path, monkeypatch):
    url = "https://demo:sensitive-password@example.com/file?token=sensitive-token"
    opener = SimpleNamespace(open=Mock(side_effect=OSError(f"cannot open {url}")))
    monkeypatch.setattr(utils, "_build_opener", lambda _: opener)
    with pytest.raises(RuntimeError) as exc:
        utils._download_file(url, str(tmp_path / "file"), "test")
    assert "sensitive-" not in str(exc.value)
    assert exc.value.__suppress_context__
    assert not (tmp_path / "file").exists()


def test_install_log_redacts_url_secrets(tmp_path):
    path = tmp_path / "install.log"
    with path.open("wb") as stream:
        installer._append_fd_header(stream.fileno(), "https://user:secret-pass@example.com/?token=secret-token")
    assert "secret-" not in path.read_text()


def test_same_basename_downloads_do_not_replace_verified_file(tmp_path, monkeypatch):
    def download(url, dst, **kwargs):
        payload = url.encode()
        Path(dst).write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(installer, "_download_file", download)
    urls = ["https://example.com/a/setup.exe", "https://example.com/b/setup.exe"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda url: installer.download_installer(
            url, str(tmp_path), hashlib.sha256(url.encode()).hexdigest(),
        ), urls))
    assert paths[0] != paths[1]
    assert [Path(path).read_text() for path in paths] == urls
    assert all(Path(path).suffix == ".exe" for path in paths)


@pytest.mark.parametrize("returncode", [0, 1])
def test_worker_isolates_parallel_downloads_and_cleans_them(tmp_path, monkeypatch, returncode):
    monkeypatch.setattr(installer, "INSTALL_LOGS_DIR", str(tmp_path / "logs"))
    barrier = threading.Barrier(2)
    paths = []

    def download(url, dst, **kwargs):
        Path(dst).write_bytes(url.encode())
        return hashlib.sha256(url.encode()).hexdigest()

    def spawn(args, path, timeout, log_fd):
        paths.append(path)
        barrier.wait(timeout=10)
        assert Path(path).read_text() == args[1]
        return returncode

    tasks = [{"name": name, "cmd": f"setup.exe https://example.com/{name}/setup.exe",
              "url": f"https://example.com/{name}/setup.exe"} for name in ("a", "b")]
    worker = installer.InstallWorker(tasks, lambda message: None, parallel=True)
    monkeypatch.setattr(installer, "_download_file", download)
    monkeypatch.setattr(worker, "_spawn_process", spawn)
    worker._run_parallel()
    assert len(paths) == 2
    assert Path(paths[0]).parent != Path(paths[1]).parent
    assert all(not Path(path).parent.exists() for path in paths)
    assert worker.success_count == (2 if returncode == 0 else 0)
    assert worker.fail_count == (0 if returncode == 0 else 2)


def test_truncated_download_is_rejected(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        headers: ClassVar[dict[str, str]] = {"Content-Length": "12"}

    monkeypatch.setattr(utils, "_build_opener", lambda _: SimpleNamespace(
        open=lambda *args, **kwargs: Response(b"short"),
    ))
    with pytest.raises(RuntimeError, match="не полностью"):
        utils._download_file("https://example.com/setup.exe", str(tmp_path / "setup.exe"), "test")
    assert not (tmp_path / "setup.exe").exists()
