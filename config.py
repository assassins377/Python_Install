import contextlib
import os
import sys

# ====================================================================
# Пути и файлы конфигурации
# ====================================================================
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(SCRIPT_DIR, "programs.json")
CATEGORY_HINTS_FILE = os.path.join(SCRIPT_DIR, "category_hints.json")
ICON_FILE = os.path.join(SCRIPT_DIR, "icons", "system.png")

# ====================================================================
# Логирование
# ====================================================================
if os.name == 'nt':
    LOG_DIR = os.path.join(os.environ.get('LOCALAPPDATA', SCRIPT_DIR), 'MInstAll')
else:
    LOG_DIR = os.path.join(os.environ.get('HOME', SCRIPT_DIR), '.local', 'share', 'MInstAll')
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    LOG_DIR = SCRIPT_DIR
    with contextlib.suppress(Exception):
        os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "install.log")
INSTALL_LOGS_DIR = os.path.join(LOG_DIR, "installs")

# ====================================================================
# Версионирование
# ====================================================================
APP_VERSION = "2.2.1"
CONFIG_VERSION = 2

# ====================================================================
# Символы UI
# ====================================================================
CHECK_ON = "☑"
CHECK_OFF = "☐"
RESULT_OK = "✅"
RESULT_FAIL = "❌"
RESULT_CANCELLED = "↺"

# ====================================================================
# Subprocess и платформы
# ====================================================================
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
DEFAULT_INSTALL_TIMEOUT = 900  # секунд (15 мин)

# ====================================================================
# Watchdog: детекция зависших инсталляторов
# ====================================================================
WATCHDOG_ENABLED = True
WATCHDOG_SAMPLE_INTERVAL = 30  # секунды между замерами
WATCHDOG_HANG_THRESHOLD = 5    # сколько подряд "тихих" замеров → killing
WATCHDOG_CPU_THRESHOLD = 0.5   # CPU% ниже которого считаем процесс "тихим"

# ====================================================================
# Параллельная установка
# ====================================================================
PARALLEL_INSTALL_ENABLED = False
MAX_PARALLEL_JOBS = 3

# ====================================================================
# Обновления
# ====================================================================
DOWNLOAD_TIMEOUT = 30  # секунды для каждого read() чанка
DOWNLOAD_CHUNK_SIZE = 64 * 1024  # 64 КБ
MAX_REDIRECTS = 5  # максимум HTTP-редиректов при скачивании

# ====================================================================
# GUI
# ====================================================================
SEARCH_DEBOUNCE_MS = 300

# ====================================================================
# Watcher: следит за изменениями в software/
# ====================================================================
WATCHER_ENABLED = True
WATCHER_POLL_INTERVAL_MS = 3000
WATCHER_INTERVALS_MS = [0, 3000, 10000, 30000, 60000]

# ====================================================================
# Кеш списка установленных программ из реестра
# ====================================================================
INSTALLED_CACHE_TTL_SECONDS = 600  # 10 минут

# ====================================================================
# Допустимые расширения инсталляторов
# ====================================================================
ALLOWED_CMD_EXTENSIONS = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".reg", ".sh", ".bash", ".deb", ".appimage"}
ALLOWED_BARE_COMMANDS = {"winget", "choco", "apt", "apt-get", "dpkg", "snap", "flatpak"}
SHELL_METACHARACTERS = {"&", "|", "&&", "||", ";", "`", "$", ">", "<", "^"}
