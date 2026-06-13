import argparse
import logging
import sys

import config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="MInstAll",
        description="Мастер тихой установки программ.\n\n"
                    "Без аргументов — запускается GUI.\n"
                    "С --install/--list — работает в CLI-режиме без GUI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Примеры:
  MInstAll                                  # GUI
  MInstAll --install "Google Chrome,Telegram Desktop"
  MInstAll --install-profile developer
  MInstAll --install all --missing-only --parallel
  MInstAll --list
  MInstAll --list --filter-status missing
  MInstAll --install Chrome --dry-run
""",
    )
    parser.add_argument("--version", action="version",
                        version=f"MInstAll v{config.APP_VERSION}")

    # --- Установка ---
    install_group = parser.add_argument_group("Установка (без GUI)")
    install_group.add_argument(
        "--install",
        metavar="NAMES",
        help='Имена программ через запятую или "all"',
    )
    install_group.add_argument(
        "--install-profile",
        metavar="NAME",
        help="Применить готовый профиль (имя из profiles/)",
    )
    install_group.add_argument(
        "--missing-only", action="store_true",
        help="Ставить только отсутствующие/устаревшие из --install",
    )
    install_group.add_argument(
        "--parallel", action="store_true",
        help="Параллельная установка независимых программ",
    )
    install_group.add_argument(
        "--max-jobs", type=int, default=None,
        help=f"Макс. параллельных задач (default: {config.MAX_PARALLEL_JOBS})",
    )
    install_group.add_argument(
        "--dry-run", action="store_true",
        help="Показать что будет установлено, не запуская",
    )

    # --- Linux-управление ---
    linux_group = parser.add_argument_group("Linux: обновление и удаление пакетов")
    linux_group.add_argument(
        "--update",
        metavar="NAMES",
        help="Обновить установленные пакеты через apt/flatpak/snap/pacman",
    )
    linux_group.add_argument(
        "--uninstall",
        metavar="NAMES",
        help="Удалить пакеты через apt/flatpak/snap/dpkg/rpm/pacman",
    )

    # --- Информация ---
    info_group = parser.add_argument_group("Информация")
    info_group.add_argument(
        "--list", action="store_true",
        help="Список всех программ из programs.json",
    )
    info_group.add_argument(
        "--list-installed", action="store_true",
        help="Список установленных в системе программ",
    )
    info_group.add_argument(
        "--list-profiles", action="store_true",
        help="Список доступных профилей",
    )
    info_group.add_argument(
        "--filter-status",
        choices=["ok", "outdated", "missing", "runnable"],
        help="Фильтр для --list по статусу",
    )

    # --- Поведение CLI ---
    behavior_group = parser.add_argument_group("Поведение CLI")
    behavior_group.add_argument(
        "--silent", action="store_true",
        help="Минимальный вывод (только итог)",
    )
    behavior_group.add_argument(
        "--no-color", action="store_true",
        help="Отключить ANSI-цвета в выводе",
    )
    behavior_group.add_argument(
        "--no-gui", action="store_true",
        help="Принудительно CLI-режим (даже без --install)",
    )
    behavior_group.add_argument(
        "--no-elevate", action="store_true",
        help="Не пытаться авто-перезапуск с правами администратора",
    )
    behavior_group.add_argument(
        "--force-elevate", action="store_true",
        help="Принудительно запросить UAC даже при запуске python-скрипта",
    )

    # --- Watchdog ---
    watchdog_group = parser.add_argument_group("Watchdog (детектор зависших инсталляторов)")
    watchdog_group.add_argument(
        "--watchdog-interval", type=int, default=None,
        help=f"Интервал замера активности в секундах (default: {config.WATCHDOG_SAMPLE_INTERVAL})",
    )
    watchdog_group.add_argument(
        "--watchdog-threshold-count", type=int, default=None,
        help=f"Количество 'тихих' замеров до завершения процесса (default: {config.WATCHDOG_HANG_THRESHOLD})",
    )
    watchdog_group.add_argument(
        "--watchdog-cpu-threshold", type=float, default=None,
        help=f"Порог CPU% ниже которого процесс считается 'тихим' (default: {config.WATCHDOG_CPU_THRESHOLD})",
    )

    return parser


def _is_cli_mode(args: argparse.Namespace) -> bool:
    """Определяет, нужен ли CLI-режим (без GUI)."""
    return bool(
        args.install
        or args.install_profile
        or args.list
        or args.list_installed
        or args.list_profiles
        or args.update
        or args.uninstall
        or args.no_gui
    )


def _try_elevate(args: argparse.Namespace) -> bool:
    """
    Если запущены без прав администратора — пробуем перезапуститься через UAC.

    Авто-elevation работает только для собранного .exe (sys.frozen).
    При запуске python-скрипта (IDLE, терминал) elevation пропускается,
    потому что иначе IDLE теряет связь с процессом и кажется что ничего не запустилось.
    Чтобы принудительно — передай флаг --force-elevate.

    Возвращает True если elevation запустился (текущий процесс надо завершить).
    """
    import os
    if args.no_elevate:
        return False

    # Для python-скрипта пропускаем — кроме явного --force-elevate,
    # Но на Linux (если это GUI) мы можем запросить pkexec
    if not getattr(sys, "frozen", False) and not args.force_elevate and os.name == "nt":
        return False

    from core_impl import is_admin, relaunch_as_admin
    if is_admin():
        return False

    if relaunch_as_admin():
        logging.info("UAC-перезапуск с правами администратора")
        return True

    logging.warning("Не удалось получить права администратора, продолжаем как есть")
    return False


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # CLI-режим — без wx, быстрее
    if _is_cli_mode(args):
        import cli
        sys.exit(cli.run(
            args,
            watchdog_interval=args.watchdog_interval,
            watchdog_hang_threshold=args.watchdog_hang_threshold,
            watchdog_cpu_threshold=args.watchdog_cpu_threshold,
        ))

    # GUI-режим
    import wx

    import i18n
    import state
    from gui import MInstAllFrame

    # Авто-elevation для GUI — UAC-диалог при старте, если ещё не админ
    if _try_elevate(args):
        sys.exit(0)

    saved_state = state.load_state()
    prefs = saved_state.get("prefs", {})
    lang_pref = prefs.get("language", "auto")
    actual_lang = i18n.init(language=lang_pref)

    logging.info("========================================")
    logging.info(f"Запуск MInstAll v{config.APP_VERSION} (config schema v{config.CONFIG_VERSION})")
    logging.info(f"Язык: {actual_lang} (запрошен: {lang_pref})")
    logging.info("========================================")

    app = wx.App(False)
    frame = MInstAllFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
