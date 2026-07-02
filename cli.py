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
import json
import logging
import re
import signal
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


def _elevate_if_needed(programs: list[dict]) -> None:
    """На Linux повышает привилегии, если хотя бы одна команда требует root.

    Если повышение удалось — перезапускает процесс от root и завершает текущий
    с кодом дочернего. Если не удалось — печатает предупреждение и продолжаем
    как есть (часть команд может завершиться с ошибкой доступа).
    """
    import os
    from utils import command_needs_root, is_admin, relaunch_cli_as_root
    if os.name == "nt" or is_admin():
        return
    if not any(command_needs_root(p.get("cmd", "")) for p in programs):
        return
    rc = relaunch_cli_as_root()
    if rc is None:
        logging.warning(
            "Не удалось повысить привилегии; установка системных пакетов "
            "может не сработать без root."
        )
        return
    sys.exit(rc)


def _elevate_if_needed_for_names(names_arg: str, programs_db: dict[str, list[dict]]) -> None:
    all_by_name = {p["name"].lower(): p for progs in programs_db.values() for p in progs}
    names = [n.strip() for n in names_arg.split(",") if n.strip()]
    relevant = [all_by_name[n.lower()] for n in names if n.lower() in all_by_name]
    _elevate_if_needed(relevant)


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
                     filter_status: str | None = None,
                     json_output: bool = False) -> int:
    """Команда --list — вывести все программы с их статусом."""
    if not programs_db:
        print("Каталог пуст (programs.json не найден или пустой).", file=sys.stderr)
        return 3

    rows: list[dict] = []
    for category, progs in programs_db.items():
        for p in progs:
            if not is_program_applicable(p):
                continue
            status, ver = check_status(p, installed_entries)
            if filter_status and status != filter_status:
                continue
            rows.append({
                "name": p["name"],
                "category": category,
                "version": p.get("version") or ver or "",
                "status": status,
                "url": p.get("url", ""),
            })

    if json_output:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    total = 0
    cur_cat: str | None = None
    for r in rows:
        if r["category"] != cur_cat:
            print(f"\n[{r['category']}]")
            cur_cat = r["category"]
        marker = {"ok": "✓", "outdated": "↑", "missing": " ", "runnable": "·"}.get(r["status"], "?")
        ver_str = f" ({r['version']})" if r["version"] else ""
        print(f"  {marker} {r['name']}{ver_str}  — {r['status']}")
        total += 1

    print(f"\nВсего: {total}", file=sys.stderr)
    return 0


def cmd_list_installed(installed_entries: list[tuple[str, str]],
                      json_output: bool = False) -> int:
    """Команда --list-installed — все программы установленные в системе."""
    if not installed_entries:
        if json_output:
            print("[]")
        else:
            print("Не найдено установленных программ.", file=sys.stderr)
        return 0
    items = [{"name": n, "version": v} for n, v in sorted(installed_entries)]
    if json_output:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    for it in items:
        print(f"{it['name']}\t{it['version']}" if it["version"] else it["name"])
    print(f"\nВсего: {len(items)}", file=sys.stderr)
    return 0


def cmd_export_installed(installed_entries: list[tuple[str, str]],
                         output_path: str | None = None) -> int:
    """Экспорт списка установленных программ в JSON-файл."""
    items = [{"name": n, "version": v} for n, v in sorted(installed_entries)]
    if not output_path:
        output_path = "installed_programs.json"
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"Список установленных программ сохранён: {output_path} ({len(items)} записей)")
        return 0
    except Exception as e:
        print(f"Ошибка сохранения: {e}", file=sys.stderr)
        return 3


def cmd_list_profiles(json_output: bool = False) -> int:
    """Команда --list-profiles — все доступные профили."""
    loaded = profiles.list_profiles()
    if not loaded:
        if json_output:
            print("[]")
        else:
            print("Нет профилей в папке profiles/", file=sys.stderr)
        return 0
    rows = [
        {
            "name": p["name"],
            "filename": p["_filename"],
            "description": p.get("description", ""),
            "programs": len(p.get("programs", [])),
        }
        for p in loaded
    ]
    if json_output:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for p in loaded:
        print(f"\n{p['name']} ({p['_filename']})")
        if p.get("description"):
            print(f"  {p['description']}")
        print(f"  Программ: {len(p.get('programs', []))}")
    return 0


# ====================================================================
# Обновления программ каталога (по url) и экспорт профиля
# ====================================================================

def cmd_check_program_updates(programs_db: dict[str, list[dict]],
                             json_output: bool = False) -> int:
    """Команда --check-program-updates — новые версии программ по url."""
    from updater import check_program_update
    candidates = [p for progs in programs_db.values() for p in progs if p.get("url")]
    if not candidates:
        if json_output:
            print("[]")
        else:
            print("Нет программ с url для проверки обновлений.", file=sys.stderr)
        return 0
    results = [check_program_update(p) for p in candidates]
    updates = sum(1 for r in results if r["has_update"])
    if json_output:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    for r in results:
        if r["error"]:
            print(f"{r['name']}: ошибка ({r['error']})", file=sys.stderr)
        elif r["has_update"]:
            print(f"{r['name']}: {r['current']} -> {r['latest']}  (доступно обновление)")
        elif r["latest"]:
            print(f"{r['name']}: {r['current']} (актуально, в канале {r['latest']})")
        else:
            print(f"{r['name']}: не удалось определить версию", file=sys.stderr)
    print(f"\nПроверено: {len(results)}, обновлений: {updates}", file=sys.stderr)
    return 0


def cmd_export_profile(name: str, tasks: list[dict]) -> int:
    """Команда --export-profile — сохранить разрешённый набор как профиль."""
    import os
    profiles_dir = profiles.PROFILES_DIR
    try:
        os.makedirs(profiles_dir, exist_ok=True)
    except Exception as e:
        print(f"Не удалось создать папку профилей: {e}", file=sys.stderr)
        return 3
    safe = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_\- ]", "_", name).strip().replace(" ", "_") or "profile"
    path = os.path.join(profiles_dir, f"{safe}.json")
    data = {
        "name": name,
        "description": f"Экспортировано из MInstAll ({len(tasks)} программ)",
        "programs": [t["name"] for t in tasks],
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Не удалось сохранить профиль: {e}", file=sys.stderr)
        return 3
    print(f"Профиль сохранён: {path} ({len(tasks)} программ)")
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

    def _sig_handler(signum, frame):
        print("\n\n⚠ Прерывание по сигналу, отменяем...", file=sys.stderr)
        worker.stop()
        worker.join(timeout=10.0)
        sys.exit(2)

    old_sigint = signal.signal(signal.SIGINT, _sig_handler)
    old_sigterm = signal.signal(signal.SIGTERM, _sig_handler)

    start_time = time.time()
    try:
        worker.start()
        while not finished_event.wait(0.5):
            if not worker.is_alive():
                break
    except KeyboardInterrupt:
        print("\n\n⚠ Прерывание по Ctrl+C, отменяем...", file=sys.stderr)
        worker.stop()
        worker.join(timeout=10.0)
        return 2
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)

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
    import logging as _logging
    log_level = _logging.DEBUG if getattr(args, "debug", False) else (_logging.INFO if not getattr(args, "verbose", False) else _logging.DEBUG)
    setup_logging(level=log_level)
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
                                  filter_status=args.filter_status,
                                  json_output=args.json)
    if args.list_installed:
        return cmd_list_installed(installed_entries, json_output=args.json)
    if args.list_profiles:
        return cmd_list_profiles(json_output=args.json)
    if args.check_program_updates:
        return cmd_check_program_updates(programs_db, json_output=args.json)
    if args.export_installed:
        return cmd_export_installed(installed_entries, output_path=args.export_installed)

    # --- Установка ---
    if args.update:
        _elevate_if_needed_for_names(args.update, programs_db)
        return cmd_linux_update(args.update, programs_db, use_color)
    if args.uninstall:
        _elevate_if_needed_for_names(args.uninstall, programs_db)
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

    if args.export_profile:
        return cmd_export_profile(args.export_profile, tasks)

    # Подтверждение перед реальной установкой — только в интерактивном терминале
    # и там, где не потребуется отдельное повышение (иначе двойной промпт с pkexec).
    import os as _os
    from utils import is_admin as _is_admin, command_needs_root as _needs_root
    will_elevate = (_os.name != "nt" and not _is_admin()
                    and any(_needs_root(p.get("cmd", "")) for p in tasks))
    if (not args.dry_run and not args.yes and not will_elevate
            and sys.stdin.isatty()):
        try:
            ans = input(f"Запустить установку {len(tasks)} задач? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes", "д", "да"):
            print("Отменено пользователем.", file=sys.stderr)
            return 2

    _elevate_if_needed(tasks)

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
