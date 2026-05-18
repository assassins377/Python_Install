import sys
import os

# --- Определение путей (поддержка PyInstaller --onefile) ---
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(SCRIPT_DIR, "programs.json")
ICON_FILE = os.path.join(SCRIPT_DIR, "icons", "system.png")

# --- Безопасное логирование (в %LOCALAPPDATA%\MInstAll) ---
LOG_DIR = os.path.join(os.environ.get('LOCALAPPDATA', SCRIPT_DIR), 'MInstAll')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "install.log")

# --- Версионирование ---
APP_VERSION = "2.0.0"
CONFIG_VERSION = 2

# --- Символы UI ---
CHECK_ON = "☑"
CHECK_OFF = "☐"
RESULT_OK = "✅"
RESULT_FAIL = "❌"
RESULT_CANCELLED = "↺"

# --- Subprocess ---
CREATE_NO_WINDOW = 0x08000000
DEFAULT_INSTALL_TIMEOUT = 900  # секунд (15 мин)

# --- Watchdog: детекция зависших инсталляторов ---
WATCHDOG_ENABLED = True
WATCHDOG_SAMPLE_INTERVAL = 30  # секунды между замерами
WATCHDOG_HANG_THRESHOLD = 5    # сколько подряд "тихих" замеров → killing
WATCHDOG_CPU_THRESHOLD = 0.5   # CPU% ниже которого считаем процесс "тихим"

# --- Обновления ---
DOWNLOAD_TIMEOUT = 30  # секунды для каждого read() чанка
DOWNLOAD_CHUNK_SIZE = 64 * 1024  # 64 КБ

# --- GUI ---
SEARCH_DEBOUNCE_MS = 300

# --- Допустимые расширения инсталляторов ---
ALLOWED_CMD_EXTENSIONS = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".reg"}
SHELL_METACHARACTERS = {"&", "|", "&&", "||", ";", "`", "$", ">", "<", "^"}
