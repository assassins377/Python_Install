import sys
import os

# Определение путей с поддержкой PyInstaller --onefile
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(SCRIPT_DIR, "programs.json")
ICON_FILE = os.path.join(SCRIPT_DIR, "icons", "system.png")

# --- БЕЗОПАСНОЕ ЛОГИРОВАНИЕ (Safe Logging) ---
# Пишем логи в AppData. Если переменной нет (редкость), падаем обратно в папку скрипта
LOG_DIR = os.path.join(os.environ.get('LOCALAPPDATA', SCRIPT_DIR), 'MInstAll')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "install.log")

CONFIG_VERSION = 2

# Символы
CHECK_ON = "☑"
CHECK_OFF = "☐"
RESULT_OK = "✅"
RESULT_FAIL = "❌"
RESULT_CANCELLED = "↺"

# ВАШ СПИСОК ПРОГРАММ (Я сократил для примера, вставьте сюда весь ваш словарь DEFAULT_PROGRAMS)
DEFAULT_PROGRAMS = {
    "СИСТЕМНЫЕ КОМПОНЕНТЫ": [
        {
            "name": "Microsoft .NET Framework 4.8",
            "cmd": "software\\net48.exe /q /norestart",
            "desc": "Необходимый компонент для работы многих программ на Windows.",
            "icon": "icons/system.png",
            "detect": {"net_framework_release": 528040},
        },
        # ... Вставьте сюда остальные программы из старого файла ...
    ]
}