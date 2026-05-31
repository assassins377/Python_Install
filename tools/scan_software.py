#!/usr/bin/env python3
"""
Сканер папки software/ — генерирует или обновляет programs.json.

Использование:
  python tools/scan_software.py                  # сухой запуск, выводит в stdout
  python tools/scan_software.py --write          # записывает в programs.json
  python tools/scan_software.py --dir path/to/sw # сканировать другую папку
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import scanner

SUPPORTED_EXTENSIONS = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".reg"}

# Эвристика: имя файла -> категория
CATEGORY_HINTS = scanner.CATEGORY_HINTS

DEFAULT_CATEGORY = "ПРОЧЕЕ"

# Стандартные silent-флаги по расширению
SILENT_FLAGS: dict[str, str] = {
    ".exe": "/S",
    ".msi": "",       # msiexec добавляется автоматически в build_cmd
    ".bat": "",
    ".cmd": "",
    ".ps1": "",
    ".reg": "",
}


def guess_category(filename: str) -> str:
    """Угадывает категорию по имени файла."""
    lower = filename.lower()
    for hint, category in CATEGORY_HINTS.items():
        if hint in lower:
            return category
    return DEFAULT_CATEGORY


def filename_to_name(filename: str) -> str:
    """Превращает имя файла в человекочитаемое название."""
    name = os.path.splitext(filename)[0]
    # Убираем версии типа _1.2.3, -setup, _x64
    import re
    name = re.sub(r'[-_]?(setup|install(er)?|x(86|64)|v?\d+(\.\d+)+)', '', name, flags=re.IGNORECASE)
    name = name.replace("_", " ").replace("-", " ").strip()
    return name.title() if name else filename


def scan_directory(software_dir: str) -> dict[str, list[dict]]:
    """Сканирует директорию и возвращает категории с программами."""
    categories: dict[str, list[dict]] = {}

    if not os.path.isdir(software_dir):
        print(f"Директория не найдена: {software_dir}", file=sys.stderr)
        return categories

    for entry in sorted(os.listdir(software_dir)):
        full_path = os.path.join(software_dir, entry)

        if os.path.isdir(full_path):
            # Подпапка = отдельная категория, сканируем рекурсивно
            for sub_entry in sorted(os.listdir(full_path)):
                ext = os.path.splitext(sub_entry)[1].lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue
                category = entry.upper()
                rel_path = f"software/{entry}/{sub_entry}"
                _add_program(categories, category, sub_entry, rel_path, ext)
            continue

        ext = os.path.splitext(entry)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue

        category = guess_category(entry)
        rel_path = f"software/{entry}"
        _add_program(categories, category, entry, rel_path, ext)

    return categories


def _add_program(
    categories: dict[str, list[dict]],
    category: str,
    filename: str,
    rel_path: str,
    ext: str,
) -> None:
    """Добавляет программу в категорию."""
    name = filename_to_name(filename)
    silent = SILENT_FLAGS.get(ext, "")
    cmd = f"{rel_path} {silent}".strip() if silent else rel_path

    program = {
        "name": name,
        "cmd": cmd.replace("/", "\\"),
        "desc": f"Автоматически обнаружено: {filename}",
        "icon": "icons/system.png",
        "detect": {},
    }

    categories.setdefault(category, []).append(program)


def merge_with_existing(
    existing: dict[str, list[dict]],
    scanned: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], int]:
    """
    Добавляет новые программы из scanned в existing (не трогает имеющиеся).
    Возвращает (merged, count_new).
    """
    merged = {cat: list(progs) for cat, progs in existing.items()}
    count_new = 0

    existing_cmds: set[str] = set()
    for progs in existing.values():
        for p in progs:
            existing_cmds.add(p.get("cmd", "").lower())

    for cat, progs in scanned.items():
        for prog in progs:
            if prog["cmd"].lower() not in existing_cmds:
                merged.setdefault(cat, []).append(prog)
                count_new += 1

    return merged, count_new


def main() -> None:
    parser = argparse.ArgumentParser(description="Сканер software/ для programs.json")
    parser.add_argument("--dir", default=os.path.join(config.SCRIPT_DIR, "software"),
                        help="Папка с инсталляторами (по умолчанию: software/)")
    parser.add_argument("--write", action="store_true",
                        help="Записать результат в programs.json (иначе — stdout)")
    parser.add_argument("--merge", action="store_true",
                        help="Добавить только новые записи в существующий programs.json")
    args = parser.parse_args()

    scanned = scan_directory(args.dir)

    if not scanned:
        print("Ничего не найдено.", file=sys.stderr)
        return

    if args.merge and os.path.exists(config.CONFIG_FILE):
        with open(config.CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        existing = data.get("categories", {})
        categories, count_new = merge_with_existing(existing, scanned)
        print(f"Найдено новых: {count_new}", file=sys.stderr)
        if count_new == 0 and not args.write:
            print("Нечего добавлять.", file=sys.stderr)
            return
    else:
        categories = scanned
        total = sum(len(v) for v in categories.values())
        print(f"Найдено инсталляторов: {total}", file=sys.stderr)

    output = {"_version": config.CONFIG_VERSION, "categories": categories}

    if args.write:
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=4)
        print(f"Записано в {config.CONFIG_FILE}", file=sys.stderr)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=4))


if __name__ == "__main__":
    main()
