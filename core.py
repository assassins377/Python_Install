import os
import json
import ctypes
import re
import shlex
import subprocess
import logging
import threading
import time
import sys
import wx
import config

# Настройка логгера
try:
    logging.basicConfig(
        filename=config.LOG_FILE, level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", encoding="utf-8"
    )
except TypeError:
    _logger = logging.getLogger()
    _logger.setLevel(logging.INFO)
    _fh = logging.FileHandler(config.LOG_FILE)
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _logger.addHandler(_fh)

def resolve_path(rel_path):
    return os.path.join(config.SCRIPT_DIR, rel_path)

def load_programs_from_json():
    if not os.path.exists(config.CONFIG_FILE):
        payload = {"_version": config.CONFIG_VERSION, "categories": config.DEFAULT_PROGRAMS}
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        return config.DEFAULT_PROGRAMS

    try:
        with open(config.CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Не удалось прочитать {config.CONFIG_FILE}: {e}")
        return {}

    categories = data.get("categories", data) if isinstance(data, dict) else {}
    user_version = data.get("_version", 1) if isinstance(data, dict) else 1

    if user_version < config.CONFIG_VERSION:
        for cat_name, default_progs in config.DEFAULT_PROGRAMS.items():
            if cat_name not in categories:
                categories[cat_name] = list(default_progs)
                continue
            existing_names = {p["name"] for p in categories[cat_name] if "name" in p}
            for prog in default_progs:
                if prog["name"] not in existing_names:
                    categories[cat_name].append(prog)
        try:
            with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"_version": config.CONFIG_VERSION, "categories": categories}, f, ensure_ascii=False, indent=4)
        except Exception: pass
    return categories

def is_admin():
    if os.name != "nt": return True
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception: return False

def relaunch_as_admin():
    if os.name != "nt": return False
    exe = sys.executable
    params = " ".join([f'"{arg}"' for arg in sys.argv[1:]]) if getattr(sys, "frozen", False) else f'"{os.path.abspath(sys.argv[0])}" ' + " ".join([f'"{arg}"' for arg in sys.argv[1:]])
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, config.SCRIPT_DIR, 1) > 32
    except Exception: return False

UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]

def get_installed_programs():
    if os.name != "nt": return []
    import winreg
    entries = []
    for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for key_path in UNINSTALL_KEYS:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as sub:
                                try: name, _ = winreg.QueryValueEx(sub, "DisplayName")
                                except FileNotFoundError: continue
                                try: version, _ = winreg.QueryValueEx(sub, "DisplayVersion")
                                except FileNotFoundError: version = ""
                                entries.append((name, str(version)))
                        except OSError: continue
            except FileNotFoundError: continue
    return entries

def parse_version(v): return tuple(int(p) for p in re.findall(r"\d+", str(v))) if v else ()
def compare_versions(a, b):
    ta, tb = parse_version(a), parse_version(b)
    n = max(len(ta), len(tb))
    ta, tb = ta + (0,) * (n - len(ta)), tb + (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)

def get_net_framework_release():
    if os.name != "nt": return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
            value, _ = winreg.QueryValueEx(key, "Release")
            return int(value)
    except OSError: return None

def check_status(program, installed_entries):
    detect = program.get("detect", {}) or {}
    if detect.get("always_runnable"): return ("runnable", "")
    
    if (net_min := detect.get("net_framework_release")) is not None:
        release = get_net_framework_release()
        if release is None: return ("missing", "")
        return ("outdated", str(release)) if release < net_min else ("ok", str(release))

    if path := detect.get("path"):
        return ("ok", "") if os.path.exists(os.path.expandvars(path)) else ("missing", "")

    needle = (detect.get("registry_name") or program["name"]).lower()
    found_version = next((v for n, v in installed_entries if needle in n.lower()), None)
    if found_version is None: return ("missing", "")
    
    min_v = detect.get("min_version")
    if min_v and compare_versions(found_version, min_v) < 0: return ("outdated", found_version)
    return ("ok", found_version)

def build_cmd(cmd_str):
    parts = shlex.split(cmd_str, posix=False)
    if not parts: raise ValueError("Пустая команда")
    script_path = parts[0]
    user_args = parts[1:]
    ext = os.path.splitext(script_path)[1].lower()

    if ext == ".reg": return ["regedit", "/s", resolve_path(script_path)]
    if ext in (".bat", ".cmd"): return ["cmd", "/c", resolve_path(script_path)] + user_args
    if ext == ".ps1": return ["powershell", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", resolve_path(script_path)] + user_args
    if ext == ".msi": return ["msiexec", "/i", resolve_path(script_path), "/qn", "/norestart"] + user_args
    return [resolve_path(script_path)] + user_args

class InstallWorker(threading.Thread):
    def __init__(self, tasks, callback):
        super().__init__(daemon=True)
        self.tasks = tasks
        self.callback = callback
        self.total_tasks = len(tasks)
        self._is_running = True
        self.success_count = self.fail_count = 0
        self.reboot_needed = False
        self.current_proc = None
        self.results = {}

    def stop(self):
        self._is_running = False
        if self.current_proc:
            try: self.current_proc.terminate()
            except Exception: pass

    def emit(self, msg_type, **kwargs):
        kwargs["type"] = msg_type
        wx.CallAfter(self.callback, kwargs)

    def run(self):
        try:
            for index, task in enumerate(self.tasks):
                item_id = task.get("_item_id")
                if not self._is_running:
                    self.emit("progress", text="Установка отменена.", color=wx.Colour(255, 140, 0))
                    if item_id: self.results[item_id] = "cancelled"
                    break

                name = task["name"]
                self.emit("progress", text=f"Установка: {name}...", color=wx.Colour(0, 0, 255))
                if item_id: self.emit("scroll_to", item_id=item_id)
                
                try: cmd_args = build_cmd(task["cmd"])
                except ValueError:
                    self.emit("progress", text=f"Пустая команда: {name}", color=wx.Colour(255, 0, 0))
                    self.fail_count += 1
                    if item_id: self.results[item_id] = "fail"
                    self.emit("value", percent=int(((index + 1) / self.total_tasks) * 100))
                    continue

                if not os.path.exists(cmd_args[0]):
                    self.emit("progress", text=f"Файл не найден: {cmd_args[0]}", color=wx.Colour(255, 0, 0))
                    time.sleep(1)
                    self.fail_count += 1
                    if item_id: self.results[item_id] = "fail"
                    self.emit("value", percent=int(((index + 1) / self.total_tasks) * 100))
                    continue

                try:
                    if os.name == "nt":
                        self.current_proc = subprocess.Popen(
                            cmd_args, cwd=os.path.dirname(cmd_args[0]) or config.SCRIPT_DIR,
                            creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        try: self.current_proc.wait(timeout=900)
                        except subprocess.TimeoutExpired:
                            self.current_proc.kill(); self.current_proc.wait()
                            raise subprocess.TimeoutExpired(cmd_args, 900)

                        returncode = self.current_proc.returncode
                        if not self._is_running:
                            if item_id: self.results[item_id] = "cancelled"
                            self.emit("progress", text=f"Отменено: {name}", color=wx.Colour(255, 140, 0))
                            break

                        if returncode == 0:
                            self.success_count += 1
                            if item_id: self.results[item_id] = "ok"
                        elif returncode == 3010:
                            self.success_count += 1
                            self.reboot_needed = True
                            if item_id: self.results[item_id] = "ok"
                            self.emit("progress", text=f"Требуется перезагрузка для {name}", color=wx.Colour(255, 140, 0))
                        else:
                            self.fail_count += 1
                            if item_id: self.results[item_id] = "fail"
                            self.emit("progress", text=f"Ошибка {name} (код {returncode})", color=wx.Colour(255, 0, 0))
                    else:
                        time.sleep(1.5)
                        self.success_count += 1
                        if item_id: self.results[item_id] = "ok"
                except Exception:
                    self.fail_count += 1
                    if item_id: self.results[item_id] = "fail"
                    self.emit("progress", text=f"Ошибка {name}", color=wx.Colour(255, 0, 0))
                finally: self.current_proc = None

                self.emit("value", percent=int(((index + 1) / self.total_tasks) * 100))
        finally:
            self.emit("finished", success=self.success_count, fails=self.fail_count, reboot=self.reboot_needed, results=self.results)