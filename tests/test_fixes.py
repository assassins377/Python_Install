"""Регрессионные тесты для критических исправлений (см. отчёт по анализу).

Покрывают чистую логику без wxPython:
  • scanner._make_entry больше не ломает тихий флаг /S в \S;
  • registry корректно извлекает имя пакета из apt/flatpak/snap-команд;
  • utils.build_cmd регистронезависимо распознаёт bare-команды (Winget/apt/...).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import registry
import scanner
import utils


class TestScannerSilentFlag(unittest.TestCase):
    def test_exe_silent_flag_is_slash_s(self) -> None:
        entry = scanner._make_entry(
            "ARCHIVERS", "7-Zip-26.00.exe",
            "software/Archivers/7-Zip-26.00.exe", ".exe",
        )
        # Путь нормализован под Windows (обратные слэши), но тихий флаг
        # остаётся "/S", а не превращается в "\S".
        self.assertTrue(entry["cmd"].endswith(" /S"))
        self.assertNotIn("\\S", entry["cmd"])

    def test_no_silent_flag_keeps_path_only(self) -> None:
        entry = scanner._make_entry(
            "PKGS", "foo.deb", "software/foo.deb", ".deb",
        )
        self.assertEqual(entry["cmd"], "software\\foo.deb")


class TestLinuxPackageExtraction(unittest.TestCase):
    def test_apt_install_extracts_package(self) -> None:
        prog = {"name": "Firefox", "cmd": "apt install -y firefox"}
        self.assertEqual(
            registry.get_linux_update_command(prog),
            "apt-get install -y --only-upgrade firefox",
        )

    def test_flatpak_uninstall_extracts_app_id(self) -> None:
        prog = {"name": "FF", "cmd": "flatpak install -y org.mozilla.firefox"}
        self.assertEqual(
            registry.get_linux_uninstall_command(prog),
            "flatpak uninstall -y org.mozilla.firefox",
        )

    def test_snap_refresh_extracts_package(self) -> None:
        prog = {"name": "FF", "cmd": "snap install firefox"}
        self.assertEqual(
            registry.get_linux_update_command(prog),
            "snap refresh firefox",
        )


class TestBuildCmdCaseInsensitiveBare(unittest.TestCase):
    def test_capitalized_winget_recognized_as_bare(self) -> None:
        # Раньше build_cmd был регистрочувствительным: "Winget" проходил
        # валидацию, но не распознавался как bare-команда → искался файл.
        cmd_args, script_path = utils.build_cmd(
            "Winget install --id Google.Chrome --silent"
        )
        self.assertEqual(script_path, "")
        self.assertEqual(cmd_args[0].lower(), "winget")

    def test_lowercase_apt_still_bare(self) -> None:
        cmd_args, script_path = utils.build_cmd("apt install -y curl")
        self.assertEqual(script_path, "")
        self.assertEqual(cmd_args[0], "apt")


class TestProgramsJsonSilentFlag(unittest.TestCase):
    def test_no_backslash_s_flag_in_catalog(self) -> None:
        # Все .exe-записи в programs.json должны использовать /S, а не \S.
        import json
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "programs.json",
        )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        bad = []
        for progs in data.get("categories", {}).values():
            for p in progs:
                cmd = p.get("cmd", "")
                if cmd.rstrip().endswith(" \\S"):
                    bad.append(cmd)
        self.assertEqual(bad, [], f"Найдены записи с флагом \\S: {bad}")


if __name__ == "__main__":
    unittest.main()