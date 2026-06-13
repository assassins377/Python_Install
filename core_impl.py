from __future__ import annotations

import logging
import os

import config
from utils import (  # noqa: F401
    _download_file,
    _normalize_cmd_paths,
    build_cmd,
    dispatch_cmd,
    is_admin,
    relaunch_as_admin,
    resolve_path,
    setup_logging,
    validate_cmd,
)


def load_programs_from_json() -> dict[str, list[dict]]:
    import json

    if not os.path.exists(config.CONFIG_FILE):
        logging.error(f"Файл конфигурации не найден: {config.CONFIG_FILE}")
        return {}

    try:
        with open(config.CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Не удалось прочитать {config.CONFIG_FILE}: {e}")
        return {}

    if not isinstance(data, dict):
        logging.error(
            f"Некорректная структура {config.CONFIG_FILE}: ожидается объект"
        )
        return {}

    categories = data.get("categories")
    if categories is None:
        logging.error(
            f"В {config.CONFIG_FILE} отсутствует ключ 'categories'"
        )
        return {}
    if not isinstance(categories, dict):
        logging.error(
            f"Некорректный тип 'categories' в {config.CONFIG_FILE}: ожидается объект"
        )
        return {}

    valid: dict[str, list[dict]] = {}
    for cat_name, programs in categories.items():
        if not isinstance(programs, list):
            logging.warning(
                f"Категория '{cat_name}': ожидается список программ, пропущена"
            )
            continue
        valid_progs: list[dict] = []
        for i, prog in enumerate(programs):
            if not isinstance(prog, dict):
                logging.warning(
                    f"Категория '{cat_name}', элемент {i}: ожидается объект, пропущен"
                )
                continue
            if "name" not in prog or "cmd" not in prog:
                logging.warning(
                    f"Категория '{cat_name}', элемент {i}: отсутствует 'name' или 'cmd', пропущен"
                )
                continue
            valid_progs.append(prog)
        valid[cat_name] = valid_progs

    return valid


def find_latest_install_log(program_name: str) -> str | None:
    """Находит последний файл лога установки для указанной программы."""
    import glob
    import re
    safe = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_\- ]", "_", program_name.strip())
    safe = re.sub(r"\s+", "_", safe)
    if not safe:
        return None

    try:
        os.makedirs(config.INSTALL_LOGS_DIR, exist_ok=True)
    except Exception:
        return None

    pattern = os.path.join(config.INSTALL_LOGS_DIR, f"{safe}_*.log")
    files = glob.glob(pattern)
    if not files:
        return None

    try:
        return max(files, key=os.path.getmtime)
    except Exception:
        return None


from deps import resolve_dependencies, topological_levels  # noqa: E402, F401
from installer import (  # noqa: E402, F401
    RETRYABLE_EXIT_CODES,
    InstallWorker,
    download_installer,
    is_installer_available,
    run_hook,
    run_uninstall,
)
from registry import (  # noqa: E402, F401
    _get_installed_programs_uncached,
    _normalize_for_match,
    build_status_cache,
    check_status,
    compare_versions,
    get_installed_programs,
    get_net_framework_release,
    invalidate_caches,
    invalidate_installed_cache,
    is_program_applicable,
    parse_version,
)
