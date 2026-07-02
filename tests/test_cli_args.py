"""Регрессионные тесты для CLI-парсера и новых флагов автоматизации."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestCliParser(unittest.TestCase):
    def _parser(self):
        return main._build_parser()

    def test_watchdog_threshold_count_dest_matches(self) -> None:
        # Раньше --watchdog-threshold-count давал dest "watchdog_threshold_count",
        # а main.py обращался к args.watchdog_hang_threshold → AttributeError
        # ломал ВЕСЬ CLI. Теперь dest приведён в соответствие.
        args = self._parser().parse_args(["--watchdog-threshold-count", "7"])
        self.assertEqual(args.watchdog_hang_threshold, 7)

    def test_watchdog_defaults_none(self) -> None:
        args = self._parser().parse_args(["--list"])
        self.assertIsNone(args.watchdog_hang_threshold)
        self.assertIsNone(args.watchdog_interval)
        self.assertIsNone(args.watchdog_cpu_threshold)

    def test_json_and_yes_flags(self) -> None:
        args = self._parser().parse_args(["--list", "--json", "--yes"])
        self.assertTrue(args.json)
        self.assertTrue(args.yes)

    def test_no_confirm_alias(self) -> None:
        args = self._parser().parse_args(["--list", "--no-confirm"])
        self.assertTrue(args.yes)

    def test_export_profile_flag(self) -> None:
        args = self._parser().parse_args(["--install", "all", "--export-profile", "X"])
        self.assertEqual(args.export_profile, "X")

    def test_check_program_updates_flag(self) -> None:
        args = self._parser().parse_args(["--check-program-updates"])
        self.assertTrue(args.check_program_updates)


class TestVersionDetectorLogic(unittest.TestCase):
    def test_check_program_update_no_url(self) -> None:
        from updater import check_program_update
        r = check_program_update({"name": "Foo"})
        self.assertIsNotNone(r["error"])

    def test_check_program_update_no_version(self) -> None:
        from updater import check_program_update
        r = check_program_update({"name": "Foo", "url": "https://example.com/a-1.0.exe"})
        self.assertIsNotNone(r["error"])


if __name__ == "__main__":
    unittest.main()