"""Windows-only integration gate: real update helper and GUI shutdown callback."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import updater


def check_helper() -> None:
    with tempfile.TemporaryDirectory(prefix="minstall-helper-test-") as temp:
        root = Path(temp) / "space % & ' 测试"
        root.mkdir()
        for valid in (True, False):
            current = root / ("valid.exe" if valid else "corrupt.exe")
            current.write_bytes(b"previous version")
            stage = root / ("stage-valid" if valid else "stage-corrupt")
            stage.mkdir()
            candidate = stage / "update.exe"
            # A harmless Windows binary can be launched without app dependencies.
            shutil.copy2(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe", candidate)
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            helper = stage / "update.ps1"
            helper.write_text(updater._WINDOWS_UPDATE_SCRIPT, encoding="utf-8-sig")
            manifest = stage / "update.json"
            parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
            manifest.write_text(json.dumps({
                "pid": parent.pid, "current": str(current), "new": str(candidate),
                "backup": str(stage / "previous.exe"), "sha256": digest if valid else "0" * 64,
                "arguments": "/c exit 0", "log": str(root / "update.log"),
            }, ensure_ascii=False), encoding="utf-8")
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(helper), "-Manifest", str(manifest)],
                    timeout=90, check=False,
                )
            finally:
                parent.wait(timeout=5)
            log = root / "update.log"
            failure = log.read_text(encoding="utf-8-sig") if log.exists() else "No helper error log"
            if valid:
                if result.returncode != 0 or hashlib.sha256(current.read_bytes()).hexdigest() != digest:
                    raise RuntimeError(f"Windows update replacement failed ({result.returncode}): {ascii(failure)}")
            elif result.returncode == 0 or current.read_bytes() != b"previous version":
                raise RuntimeError("Corrupt update was not rejected")
            if stage.exists():
                raise RuntimeError("Helper did not clean its staging directory")


def check_gui_close() -> None:
    import wx

    from dispatch import DispatchMixin
    from gui import MInstAllFrame

    class Probe(wx.Frame, DispatchMixin):
        _on_update_message = MInstAllFrame._on_update_message

        def __init__(self):
            super().__init__(None, title="Update lifecycle check")
            self.worker = None
            self._closing = False
            self._updating = True
            self.saved_window = False
            self.saved_session = False
            self.on_main_thread = False
            self.Bind(wx.EVT_CLOSE, self.on_closing)
            self.Disable()

        def _set_status(self, text, severity="info"):
            self.on_main_thread = threading.current_thread() is threading.main_thread()

        def _save_window_state(self):
            self.saved_window = True

        def _save_session(self):
            self.saved_session = True

    app = wx.App(False)
    probe = Probe()
    probe.Show()
    # Use the same worker-to-GUI boundary as the production update callback.
    worker = threading.Thread(target=lambda: wx.CallAfter(probe._on_update_message, {"type": "done"}))
    worker.start()
    worker.join(timeout=5)
    deadline = wx.CallLater(5000, app.ExitMainLoop)
    app.MainLoop()
    deadline.Stop()
    if not (probe.on_main_thread and probe.saved_window and probe.saved_session and probe._closing):
        raise RuntimeError("Update callback did not close GUI and save state on main thread")
    app.Destroy()


if __name__ == "__main__":
    check_helper()
    check_gui_close()
    print("Windows helper integrity/replacement and GUI shutdown checks passed")
