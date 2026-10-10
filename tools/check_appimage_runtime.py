"""Linux integration gate: relaunch only after old process finishes saving state."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import updater

ARGUMENTS = ["space and 'quotes'", "$(touch unwanted)", "", "日本語"]


def wait_for_file(path: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for {path.name}")
        time.sleep(0.05)


def old_process(root: Path) -> None:
    appimage = root / "app ' $ 测试.AppImage"
    appimage.write_bytes(b"old image")
    appimage.chmod(0o700)
    payload = b'#!/bin/sh\nexec "$MINSTALL_TEST_PYTHON" "$MINSTALL_TEST_PROBE" "$@"\n'

    def download(url, dst, size, emit):
        Path(dst).write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    updater._download_with_progress = download
    updater.config.LOG_FILE = str(root / "update.log")
    os.environ["APPIMAGE"] = str(appimage)
    sys.argv = [str(appimage), *ARGUMENTS]
    events = []
    results = []
    worker = threading.Thread(target=lambda: results.append(updater._apply_update_appimage(
        {"url": "https://example.invalid/update", "sha256": hashlib.sha256(payload).hexdigest()}, events.append,
    )))
    worker.start()
    worker.join(timeout=5)
    if worker.is_alive() or results != [True] or events[-1] != {"type": "done"}:
        raise RuntimeError(f"Update handoff failed: {events}")
    # Simulate a slow, non-atomic save in the GUI close handler.
    (root / "state.json").write_text('{"phase":', encoding="utf-8")
    (root / "closing").touch()
    wait_for_file(root / "finish-close")
    (root / "state.json").write_text(json.dumps({"phase": "saved"}), encoding="utf-8")


def check_handoff() -> None:
    with tempfile.TemporaryDirectory(prefix="minstall-appimage-test-") as temp:
        root = Path(temp)
        probe = root / "probe.py"
        probe.write_text(
            "import json, os, pathlib, sys\n"
            "root = pathlib.Path(__file__).parent\n"
            "result = {'state': json.loads((root / 'state.json').read_text()),\n"
            "          'args': sys.argv[1:], 'reset': os.environ.get('PYINSTALLER_RESET_ENVIRONMENT')}\n"
            "(root / 'result.json').write_text(json.dumps(result), encoding='utf-8')\n",
            encoding="utf-8",
        )
        environment = {**os.environ, "MINSTALL_TEST_PYTHON": sys.executable,
                       "MINSTALL_TEST_PROBE": str(probe)}
        old = subprocess.Popen([sys.executable, __file__, "--old-process", str(root)], env=environment)
        try:
            wait_for_file(root / "closing")
            time.sleep(1.5)  # Give an incorrectly immediate restart time to run.
            if old.poll() is not None or (root / "result.json").exists() or (root / "update.log").exists():
                raise RuntimeError("New instance started before the old process finished saving")
            (root / "finish-close").touch()
            if old.wait(timeout=5) != 0:
                raise RuntimeError("Old process failed during shutdown")
            wait_for_file(root / "result.json")
            result = json.loads((root / "result.json").read_text(encoding="utf-8"))
            if result != {"state": {"phase": "saved"}, "args": ARGUMENTS, "reset": "1"}:
                raise RuntimeError(f"Relaunch lost state, arguments or runtime isolation: {result}")
        finally:
            if old.poll() is None:
                old.terminate()
            old.wait(timeout=5)
    print("AppImage restart waits for process exit and preserves saved state/arguments")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--old-process":
        old_process(Path(sys.argv[2]))
    else:
        check_handoff()
