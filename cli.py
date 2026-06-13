"""CLI-режим MInstAll — установка без GUI для скриптов и автоматизации.

Использование (примеры):
  MInstAll --install "Google Chrome,Telegram Desktop"
  MInstAll --install-profile developer
  MInstAll --list
  MInstAll --list-installed
  MInstAll --install all --missing-only --parallel
  MInstAll --install Chrome --dry-run

Коды выхода:
  0 — успех
  1 — частичный успех (некоторые программы не установились)
  2 — отменено пользователем (Ctrl+C)
  3 — ошибка аргументов / конфигурации
"""
from __future__ import annotations

import fnmatch
import logging
import sys
import threading
import time

import config
import profiles
from deps import resolve_dependencies, topological_levels
from installer import InstallWorker
from registry import (
    check_status,
    get_installed_programs,
    is_program_applicable,
)
from utils import setup_logging

# Severity → ANSI цвета для красивого вывода в терминал
ANSI_COLORS: dict[str, str] = {
    "info":     "",
    "progress": "\033[36m",   # cyan
    "warn":     "\033[33m",   # yellow
    "error":    "\033[31m",   # red
    "success":  "\033[32m",   # green
}
ANSI_RESET = "\033[0m"


def _supports_color() -> bool:
    """Поддерживает ли терминал ANSI-цвета."""
    if not sys.stdout.isatty():
        return False
    # Windows 10+ поддерживает ANSI через VT-режим, активируем
    import os
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


def _colorize(text: str, severity: str, use_color: bool) -> str:
    if not use_color or severity not in ANSI_COLORS:
        return text
    return f"{ANSI_COLORS[severity]}{text}{ANSI_RESET}"


# ====================================================================
# Команды CLI
# ====================================================================

def cmd_list_programs(programs_db: dict[str, list[dict]], installed_entries: list[tuple[str, str]],
                     filter_status: str | None = None) -> int:
    """Команда --list — вывести все программы с их статусом."""
    if not programs_db:
        print("Каталог пуст (programs.json не найден или пустой).", file=sys.stderr)
        return 3

    total = 0
    for category, progs in programs_db.items():
        cat_printed = False
        for p in progs:
            if not is_program_applicable(p):
                continue
            status, ver = check_status(p, installed_entries)
            if filter_status and status != filter_status:
                continue
            if not cat_printed:
                print(f"\n[{category}]")
                cat_printed = True
            marker = {"ok": "✓", "outdated": "↑", "missing": " ", "runnable": "·"}.get(status, "?")
            ver_str = f" ({p['version']})" if p.get('version') else (f" ({ver})" if ver else "")
            print(f"  {marker} {p['name']}{ver_str}  — {status}")
            total += 1

    print(f"\nВсего: {total}", file=sys.stderr)
    return 0


def cmd_list_installed(installed_entries: list[tuple[str, str]]) -> int:
    """Команда --list-installed — все программы установленные в системе."""
    if not installed_entries:
        print("Не найдено установленных программ.", file=sys.stderr)
        return 0
    for name, version in sorted(installed_entries):
        print(f"{name}\t{version}" if version else name)
    print(f"\nВсего: {len(installed_entries)}", file=sys.stderr)
    return 0


def cmd_list_profiles() -> int:
    """Команда --list-profiles — все доступные профили."""
    loaded = profiles.list_profiles()
    if not loaded:
        print("Нет профилей в папке profiles/", file=sys.stderr)
        return 0
    for p in loaded:
        print(f"\n{p['name']} ({p['_filename']})")
        if p.get("description"):
            print(f"  {p['description']}")
        print(f"  Программ: {len(p.get('programs', []))}")
    return 0


# ====================================================================
# Установка из CLI
# ====================================================================

def resolve_targets(
    install_arg: str,
    programs_db: dict[str, list[dict]],
    missing_only: bool,
    installed_entries: list[tuple[str, str]],
) -> tuple[list[dict], list[str]]:
    """
    Разбирает --install аргумент в список tasks.

    install_arg может быть:
      "all"          — все программы из каталога
      "Chrome,Tg"    — список по именам через запятую
      "*Chrome*"     — wildcard-паттерн (fnmatch по имени)

    missing_only=True — фильтрует, оставляя только missing/outdated.
    """
    all_by_name: dict[str, dict] = {}
    for progs in programs_db.values():
        for p in progs:
            all_by_name[p["name"].lower()] = p

    if install_arg.strip().lower() == "all":
        candidates = list(all_by_name.values())
    else:
        names = [n.strip() for n in install_arg.split(",") if n.strip()]
        candidates: list[dict] = []
        not_found: list[str] = []
        for n in names:
            if prog := all_by_name.get(n.lower()):
                candidates.append(prog)
            elif "*" in n or "?" in n:
                wildcard_lower = n.lower()
                matched = [
                    p for name_lower, p in all_by_name.items()
                    if fnmatch.fnmatch(name_lower, wildcard_lower)
                ]
                if matched:
                    candidates.extend(matched)
                else:
                    not_found.append(n)
            else:
                not_found.append(n)
        if not_found:
            return ([], not_found)

        # Отсекаем неприменимые на текущей ОС (напр. Windows-only .NET на Linux)
    initial_candidates_count = len(candidates)
    candidates = [p for p in candidates if is_program_applicable(p)]
    if initial_candidates_count > len(candidates):
        logging.warning("Некоторые программы отфильтрованы как неприменимые для текущей ОС.")

    if missing_only:
        filtered = []
        for p in candidates:
            status, _ = check_status(p, installed_entries)
            if status in ("missing", "outdated"):
                filtered.append(p)
        candidates = filtered

    return (candidates, [])


def resolve_profile_targets(
    profile_name: str,
    programs_db: dict[str, list[dict]],
) -> tuple[list[dict], list[str]]:
    """Разрешает --install-profile в список tasks."""
    profile = profiles.find_profile_by_name(profile_name)
    if profile is None:
        return ([], [f"Профиль '{profile_name}' не найден"])

    found, missing = profiles.resolve_profile_programs(profile, programs_db)
    initial_found_count = len(found)
    found = [p for p in found if is_program_applicable(p)]
    if initial_found_count > len(found):
        logging.warning("Некоторые программы из профиля отфильтрованы как неприменимые для текущей ОС.")
    return (found, missing)


def install_cli(
    tasks: list[dict],
    programs_db: dict[str, list[dict]],
    parallel: bool,
    max_jobs: int,
    silent: bool,
    dry_run: bool,
    use_color: bool,
    watchdog_interval: int | None = None,
    watchdog_hang_threshold: int | None = None,
    watchdog_cpu_threshold: float | None = None,
) -> int:
    """Запускает установку в CLI-режиме. Возвращает код выхода."""
    if not tasks:
        print("Нечего устанавливать (после фильтрации).", file=sys.stderr)
        return 0

    # Раскрываем зависимости + отсортируем
    if parallel:
        levels = topological_levels(tasks, programs_db)
        all_tasks = [t for lvl in levels for t in lvl]
    else:
        all_tasks = resolve_dependencies(tasks, programs_db)
        levels = [all_tasks]

    # --- Dry run: только показываем план ---
    if dry_run:
        print(f"\nDRY RUN — план установки ({len(all_tasks)} задач):\n")
        for i, level in enumerate(levels):
            if parallel:
                print(f"  Уровень {i + 1}:")
            for t in level:
                print(f"    • {t['name']}")
                print(f"      cmd: {t['cmd']}")
                if t.get("pre_cmd"):
                    print(f"      pre: {t['pre_cmd']}")
                if t.get("post_cmd"):
                    print(f"      post: {t['post_cmd']}")
                if t.get("depends_on"):
                    print(f"      deps: {', '.join(t['depends_on'])}")
        print(f"\nРежим: {'параллельный' if parallel else 'последовательный'}")
        print("Реальной установки не было.\n")
        return 0

    # --- Реальная установка ---
    print(f"\nЗапуск установки: {len(all_tasks)} задач, режим "
          f"{'параллельный' if parallel else 'последовательный'}\n")

    finished_event = threading.Event()
    final_result: dict = {}

    def dispatch(msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "progress" and not silent:
            severity = msg.get("severity", "info")
            text = _colorize(msg["text"], severity, use_color)
            print(f"  {text}")
        elif msg_type == "value" and not silent:
            pct = msg.get("percent", 0)
            # Простой ASCII прогресс-бар
            bar_width = 30
            filled = int(bar_width * pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            sys.stdout.write(f"\r  [{bar}] {pct}%   ")
            sys.stdout.flush()
            if pct >= 100:
                sys.stdout.write("\n")
        elif msg_type == "finished":
            final_result.update(msg)
            finished_event.set()

    worker = InstallWorker(
        all_tasks,
        dispatch,
        parallel=parallel,
        max_jobs=max_jobs,
        all_programs=programs_db,
        watchdog_interval=watchdog_interval,
        watchdog_hang_threshold=watchdog_hang_threshold,
        watchdog_cpu_threshold=watchdog_cpu_threshold,
    )

    start_time = time.time()
    try:
        worker.start()
        # Ждём пока worker не завершит работу, реагируем на Ctrl+C
        while not finished_event.wait(0.5):
            if not worker.is_alive():
                break
    except KeyboardInterrupt:
        print("\n\n⚠ Прерывание по Ctrl+C, отменяем...", file=sys.stderr)
        worker.stop()
        worker.join(timeout=10.0)
        return 2

    duration = time.time() - start_time

    # --- Итоги ---
    success = final_result.get("success", 0)
    fails = final_result.get("fails", 0)
    reboot = final_result.get("reboot", False)

    print("\n" + "═" * 50)
    print(f"  Успешно:    {_colorize(str(success), 'success', use_color)}")
    print(f"  Ошибок:     {_colorize(str(fails), 'error' if fails else 'info', use_color)}")
    print(f"  Время:      {duration:.1f}с")
    if reboot:
        print(_colorize("\n  ⚠ Требуется перезагрузка системы", "warn", use_color))
    print("═" * 50 + "\n")

    return 0 if fails == 0 else 1


# ====================================================================
# Точка входа
# ====================================================================

def run(
    args,
    watchdog_interval: int | None = None,
    watchdog_hang_threshold: int | None = None,
    watchdog_cpu_threshold: float | None = None,
) -> int:
    """Точка входа CLI. args — namespace из argparse."""
    setup_logging()
    logging.info(f"CLI режим: args={vars(args)}")

    use_color = _supports_color() and not args.no_color

    from core_impl import load_programs_from_json
    programs_db = load_programs_from_json()
    # CLI всегда получает актуальные данные — инвалидируем кэш
    from core_impl import invalidate_installed_cache
    state_dict = {}
    invalidate_installed_cache(state_dict)
    installed_entries = get_installed_programs(state_dict=state_dict, use_cache=False)

    # --- Информационные команды ---
    if args.list:
        return cmd_list_programs(programs_db, installed_entries,
                                  filter_status=args.filter_status)
    if args.list_installed:
        return cmd_list_installed(installed_entries)
    if args.list_profiles:
        return cmd_list_profiles()

    # --- Установка ---
    if args.update:
        return cmd_linux_update(args.update, programs_db, use_color)
    if args.uninstall:
        return cmd_linux_uninstall(args.uninstall, programs_db, use_color)

    if not args.install and not args.install_profile:
        print("Ошибка: укажи --install или --install-profile", file=sys.stderr)
        return 3

    if args.install_profile:
        tasks, errors = resolve_profile_targets(args.install_profile, programs_db)
        if errors and not tasks:
            for e in errors:
                print(f"Ошибка: {e}", file=sys.stderr)
            return 3
        if errors:
            print(f"⚠ Не найдены в каталоге: {', '.join(errors)}", file=sys.stderr)
    else:
        tasks, not_found = resolve_targets(
            args.install, programs_db, args.missing_only, installed_entries
        )
        if not_found:
            for n in not_found:
                print(f"Программа не найдена: {n}", file=sys.stderr)
            return 3

    return install_cli(
        tasks=tasks,
        programs_db=programs_db,
        parallel=args.parallel,
        max_jobs=args.max_jobs or config.MAX_PARALLEL_JOBS,
        silent=args.silent,
        dry_run=args.dry_run,
        use_color=use_color,
        watchdog_interval=args.watchdog_interval,
        watchdog_hang_threshold=args.watchdog_hang_threshold,
        watchdog_cpu_threshold=args.watchdog_cpu_threshold,
    )


def cmd_linux_update(
    names_arg: str,
    programs_db: dict[str, list[dict]],
    use_color: bool,
) -> int:
    import os
    if os.name == "nt":
        print("Команда --update доступна только на Linux.", file=sys.stderr)
        return 3
    from installer import run_linux_update
    all_by_name: dict[str, dict] = {}
    for progs in programs_db.values():
        for p in progs:
            all_by_name[p["name"].lower()] = p
    names = [n.strip() for n in names_arg.split(",") if n.strip()]
    success = 0
    fails = 0
    for n in names:
        prog = all_by_name.get(n.lower())
        if not prog:
            print(f"Программа не найдена: {n}", file=sys.stderr)
            fails += 1
            continue
        print(f"Обновление: {prog['name']}...")
        if run_linux_update(prog):
            print(_colorize(f"  OK: {prog['name']}", "success", use_color))
            success += 1
        else:
            print(_colorize(f"  Ошибка: {prog['name']}", "error", use_color))
            fails += 1
    print(f"\nОбновлено: {success}, ошибок: {fails}")
    return 0 if fails == 0 else 1


def cmd_linux_uninstall(
    names_arg: str,
    programs_db: dict[str, list[dict]],
    use_color: bool,
) -> int:
    import os
    if os.name == "nt":
        print("Команда --uninstall доступна только на Linux.", file=sys.stderr)
        return 3
    from installer import run_linux_uninstall
    all_by_name: dict[str, dict] = {}
    for progs in programs_db.values():
        for p in progs:
            all_by_name[p["name"].lower()] = p
    names = [n.strip() for n in names_arg.split(",") if n.strip()]
    success = 0
    fails = 0
    for n in names:
        prog = all_by_name.get(n.lower())
        if not prog:
            print(f"Программа не найдена: {n}", file=sys.stderr)
            fails += 1
            continue
        print(f"Удаление: {prog['name']}...")
        if run_linux_uninstall(prog):
            print(_colorize(f"  OK: {prog['name']}", "success", use_color))
            success += 1
        else:
            print(_colorize(f"  Ошибка: {prog['name']}", "error", use_color))
            fails += 1
    print(f"\nУдалено: {success}, ошибок: {fails}")
    return 0 if fails == 0 else 1
