"""Unit-тесты для core.py — чистая логика, не требует wxPython."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import core


# ------------------------------------------------------------------
# parse_version / compare_versions
# ------------------------------------------------------------------
class TestVersionParsing(unittest.TestCase):
    def test_parse_simple(self) -> None:
        self.assertEqual(core.parse_version("1.2.3"), (1, 2, 3))

    def test_parse_with_text(self) -> None:
        self.assertEqual(core.parse_version("v4.0.1-beta"), (4, 0, 1))

    def test_parse_empty(self) -> None:
        self.assertEqual(core.parse_version(""), ())

    def test_parse_none(self) -> None:
        self.assertEqual(core.parse_version(None), ())

    def test_parse_single(self) -> None:
        self.assertEqual(core.parse_version("10"), (10,))

    def test_compare_equal(self) -> None:
        self.assertEqual(core.compare_versions("1.0.0", "1.0.0"), 0)

    def test_compare_greater(self) -> None:
        self.assertEqual(core.compare_versions("2.0.0", "1.9.9"), 1)

    def test_compare_less(self) -> None:
        self.assertEqual(core.compare_versions("1.0.0", "1.0.1"), -1)

    def test_compare_different_lengths(self) -> None:
        self.assertEqual(core.compare_versions("1.0", "1.0.0"), 0)
        self.assertEqual(core.compare_versions("1.0.1", "1.0"), 1)

    def test_compare_with_text(self) -> None:
        self.assertEqual(core.compare_versions("v2.1.0-rc1", "2.0.9"), 1)

    def test_compare_empty(self) -> None:
        self.assertEqual(core.compare_versions("", ""), 0)
        self.assertEqual(core.compare_versions("1.0", ""), 1)


# ------------------------------------------------------------------
# validate_cmd
# ------------------------------------------------------------------
class TestValidateCmd(unittest.TestCase):
    def test_valid_exe(self) -> None:
        self.assertIsNone(core.validate_cmd("software\\app.exe /silent"))

    def test_valid_msi(self) -> None:
        self.assertIsNone(core.validate_cmd("software\\pkg.msi /quiet"))

    def test_valid_bat(self) -> None:
        self.assertIsNone(core.validate_cmd("scripts\\setup.bat"))

    def test_valid_ps1(self) -> None:
        self.assertIsNone(core.validate_cmd("scripts\\tweak.ps1 -Force"))

    def test_valid_reg(self) -> None:
        self.assertIsNone(core.validate_cmd("tweaks\\fix.reg"))

    def test_reject_pipe(self) -> None:
        err = core.validate_cmd("app.exe | malicious.exe")
        self.assertIsNotNone(err)
        self.assertIn("|", err)

    def test_reject_ampersand(self) -> None:
        err = core.validate_cmd("app.exe & del /f C:\\*")
        self.assertIsNotNone(err)
        self.assertIn("&", err)

    def test_reject_semicolon(self) -> None:
        err = core.validate_cmd("app.exe ; rm -rf /")
        self.assertIsNotNone(err)

    def test_reject_backtick(self) -> None:
        err = core.validate_cmd("app.exe `whoami`")
        self.assertIsNotNone(err)

    def test_reject_bad_extension(self) -> None:
        err = core.validate_cmd("payload.py --evil")
        self.assertIsNotNone(err)
        self.assertIn(".py", err)

    def test_empty_command(self) -> None:
        err = core.validate_cmd("")
        self.assertIsNotNone(err)


# ------------------------------------------------------------------
# build_cmd
# ------------------------------------------------------------------
class TestBuildCmd(unittest.TestCase):
    def test_exe(self) -> None:
        args, path = core.build_cmd("software\\app.exe /silent")
        self.assertTrue(path.endswith("app.exe"))
        self.assertEqual(args[0], path)
        self.assertIn("/silent", args)

    def test_msi(self) -> None:
        args, path = core.build_cmd("software\\pkg.msi")
        self.assertEqual(args[0], "msiexec")
        self.assertIn("/qn", args)

    def test_bat(self) -> None:
        args, path = core.build_cmd("scripts\\run.bat")
        self.assertEqual(args[0], "cmd")
        self.assertEqual(args[1], "/c")

    def test_ps1(self) -> None:
        args, path = core.build_cmd("scripts\\tweak.ps1 -Param value")
        self.assertEqual(args[0], "powershell")
        self.assertIn("-NonInteractive", args)

    def test_reg(self) -> None:
        args, path = core.build_cmd("tweaks\\fix.reg")
        self.assertEqual(args[0], "regedit")
        self.assertIn("/s", args)

    def test_injection_raises(self) -> None:
        with self.assertRaises(ValueError):
            core.build_cmd("app.exe & whoami")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            core.build_cmd("")


# ------------------------------------------------------------------
# check_status
# ------------------------------------------------------------------
class TestCheckStatus(unittest.TestCase):
    def test_always_runnable(self) -> None:
        prog = {"name": "Tweak", "cmd": "t.bat", "detect": {"always_runnable": True}}
        status, ver = core.check_status(prog, [])
        self.assertEqual(status, "runnable")

    def test_missing_program(self) -> None:
        prog = {"name": "Missing App", "cmd": "m.exe", "detect": {}}
        status, ver = core.check_status(prog, [("Other App", "1.0")])
        self.assertEqual(status, "missing")

    def test_found_program(self) -> None:
        prog = {"name": "MyApp", "cmd": "m.exe", "detect": {}}
        status, ver = core.check_status(prog, [("MyApp", "2.0.0")])
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "2.0.0")

    def test_outdated_program(self) -> None:
        prog = {"name": "MyApp", "cmd": "m.exe", "detect": {"min_version": "3.0"}}
        status, ver = core.check_status(prog, [("MyApp", "2.5")])
        self.assertEqual(status, "outdated")
        self.assertEqual(ver, "2.5")

    def test_registry_name_override(self) -> None:
        prog = {"name": "Chrome", "cmd": "c.exe", "detect": {"registry_name": "Google Chrome"}}
        status, ver = core.check_status(prog, [("Google Chrome", "120.0")])
        self.assertEqual(status, "ok")

    def test_path_detection_exists(self) -> None:
        prog = {"name": "X", "cmd": "x.exe", "detect": {"path": __file__}}
        status, _ = core.check_status(prog, [])
        self.assertEqual(status, "ok")

    def test_path_detection_missing(self) -> None:
        prog = {"name": "X", "cmd": "x.exe", "detect": {"path": "/no/such/file"}}
        status, _ = core.check_status(prog, [])
        self.assertEqual(status, "missing")

    def test_no_detect_key(self) -> None:
        prog = {"name": "FooBar", "cmd": "f.exe"}
        status, _ = core.check_status(prog, [("FooBar", "1.0")])
        self.assertEqual(status, "ok")

    def test_case_insensitive_match(self) -> None:
        prog = {"name": "myapp", "cmd": "m.exe", "detect": {}}
        status, _ = core.check_status(prog, [("MyApp Pro Edition", "1.0")])
        self.assertEqual(status, "ok")


# ------------------------------------------------------------------
# load_programs_from_json
# ------------------------------------------------------------------
class TestLoadPrograms(unittest.TestCase):
    def test_load_valid_file(self) -> None:
        data = {
            "_version": 2,
            "categories": {
                "TEST": [{"name": "App", "cmd": "a.exe", "desc": "Test"}]
            }
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            tmp = f.name
        try:
            with patch.object(config, "CONFIG_FILE", tmp):
                result = core.load_programs_from_json()
            self.assertIn("TEST", result)
            self.assertEqual(result["TEST"][0]["name"], "App")
        finally:
            os.unlink(tmp)

    def test_missing_file(self) -> None:
        with patch.object(config, "CONFIG_FILE", "/no/such/file.json"):
            result = core.load_programs_from_json()
        self.assertEqual(result, {})

    def test_corrupted_json(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{broken json!!!")
            tmp = f.name
        try:
            with patch.object(config, "CONFIG_FILE", tmp):
                result = core.load_programs_from_json()
            self.assertEqual(result, {})
        finally:
            os.unlink(tmp)


# ------------------------------------------------------------------
# resolve_path
# ------------------------------------------------------------------
class TestResolvePath(unittest.TestCase):
    def test_relative(self) -> None:
        result = core.resolve_path("software\\app.exe")
        self.assertTrue(result.startswith(config.SCRIPT_DIR))
        self.assertTrue(result.endswith("app.exe"))


# ------------------------------------------------------------------
# resolve_dependencies (топологическая сортировка)
# ------------------------------------------------------------------
class TestResolveDependencies(unittest.TestCase):
    def _make_task(self, name: str, depends_on: list[str] | None = None) -> dict:
        task = {"name": name, "cmd": f"{name.lower()}.exe", "detect": {}}
        if depends_on:
            task["depends_on"] = depends_on
        return task

    def test_no_deps(self) -> None:
        a = self._make_task("A")
        b = self._make_task("B")
        result = core.resolve_dependencies([a, b], {})
        names = [t["name"] for t in result]
        self.assertEqual(set(names), {"A", "B"})

    def test_simple_order(self) -> None:
        base = self._make_task("Base")
        app = self._make_task("App", depends_on=["Base"])
        result = core.resolve_dependencies([app, base], {"cat": [base, app]})
        names = [t["name"] for t in result]
        self.assertLess(names.index("Base"), names.index("App"))

    def test_chain(self) -> None:
        a = self._make_task("A")
        b = self._make_task("B", depends_on=["A"])
        c = self._make_task("C", depends_on=["B"])
        result = core.resolve_dependencies([c, b, a], {"cat": [a, b, c]})
        names = [t["name"] for t in result]
        self.assertEqual(names, ["A", "B", "C"])

    def test_auto_add_missing_dependency(self) -> None:
        """Если зависимость не выбрана пользователем, она добавляется автоматически."""
        base = self._make_task("Base")
        app = self._make_task("App", depends_on=["Base"])
        all_progs = {"cat": [base, app]}
        # Пользователь выбрал только App
        result = core.resolve_dependencies([app], all_progs)
        names = [t["name"] for t in result]
        self.assertIn("Base", names)
        self.assertLess(names.index("Base"), names.index("App"))

    def test_cycle_returns_original(self) -> None:
        """Циклическая зависимость не ломает — возвращает исходный набор."""
        a = self._make_task("A", depends_on=["B"])
        b = self._make_task("B", depends_on=["A"])
        result = core.resolve_dependencies([a, b], {"cat": [a, b]})
        names = {t["name"] for t in result}
        self.assertEqual(names, {"A", "B"})

    def test_missing_dep_not_in_db(self) -> None:
        """Зависимость на несуществующую программу — игнорируется без ошибки."""
        app = self._make_task("App", depends_on=["Phantom"])
        result = core.resolve_dependencies([app], {"cat": [app]})
        names = [t["name"] for t in result]
        self.assertEqual(names, ["App"])


# ------------------------------------------------------------------
# RETRYABLE_EXIT_CODES
# ------------------------------------------------------------------
class TestRetryableExitCodes(unittest.TestCase):
    def test_known_codes_exist(self) -> None:
        self.assertIn(1618, core.RETRYABLE_EXIT_CODES)
        self.assertIn(1603, core.RETRYABLE_EXIT_CODES)

    def test_zero_not_retryable(self) -> None:
        self.assertNotIn(0, core.RETRYABLE_EXIT_CODES)


# ------------------------------------------------------------------
# build_status_cache
# ------------------------------------------------------------------
class TestBuildStatusCache(unittest.TestCase):
    def test_empty_db(self) -> None:
        cache = core.build_status_cache({}, [])
        self.assertEqual(cache, {})

    def test_caches_all_programs(self) -> None:
        db = {
            "CAT1": [
                {"name": "App1", "cmd": "a.exe", "detect": {}},
                {"name": "App2", "cmd": "b.exe", "detect": {"always_runnable": True}},
            ],
            "CAT2": [
                {"name": "App3", "cmd": "c.exe", "detect": {}},
            ],
        }
        installed = [("App1", "1.0")]
        cache = core.build_status_cache(db, installed)

        self.assertEqual(set(cache.keys()), {"App1", "App2", "App3"})
        self.assertEqual(cache["App1"], ("ok", "1.0"))
        self.assertEqual(cache["App2"], ("runnable", ""))
        self.assertEqual(cache["App3"], ("missing", ""))


# ------------------------------------------------------------------
# Cache invalidation
# ------------------------------------------------------------------
class TestCacheInvalidation(unittest.TestCase):
    def test_invalidate_resets_net_cache(self) -> None:
        # Ставим что-то в кеш
        core._net_release_cache = (True, 528040)
        self.assertEqual(core._net_release_cache, (True, 528040))

        core.invalidate_caches()
        self.assertEqual(core._net_release_cache, (False, None))


# ------------------------------------------------------------------
# State (save/load)
# ------------------------------------------------------------------
class TestState(unittest.TestCase):
    def test_load_missing_returns_empty(self) -> None:
        import state
        with patch.object(state, "STATE_FILE", "/no/such/state.json"):
            self.assertEqual(state.load_state(), {})

    def test_save_then_load_roundtrip(self) -> None:
        import state
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            with patch.object(state, "STATE_FILE", tmp):
                state.save_state({"window": {"width": 1000, "height": 700}})
                loaded = state.load_state()
            self.assertEqual(loaded["window"]["width"], 1000)
            self.assertEqual(loaded["window"]["height"], 700)
        finally:
            os.unlink(tmp)

    def test_load_corrupted_returns_empty(self) -> None:
        import state
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
            tmp = f.name
        try:
            with patch.object(state, "STATE_FILE", tmp):
                self.assertEqual(state.load_state(), {})
        finally:
            os.unlink(tmp)


# ------------------------------------------------------------------
# Watchdog config
# ------------------------------------------------------------------
class TestWatchdogConfig(unittest.TestCase):
    def test_watchdog_constants_exist(self) -> None:
        self.assertTrue(hasattr(config, "WATCHDOG_ENABLED"))
        self.assertTrue(hasattr(config, "WATCHDOG_SAMPLE_INTERVAL"))
        self.assertTrue(hasattr(config, "WATCHDOG_HANG_THRESHOLD"))
        self.assertTrue(hasattr(config, "WATCHDOG_CPU_THRESHOLD"))

    def test_thresholds_are_positive(self) -> None:
        self.assertGreater(config.WATCHDOG_SAMPLE_INTERVAL, 0)
        self.assertGreater(config.WATCHDOG_HANG_THRESHOLD, 0)
        self.assertGreaterEqual(config.WATCHDOG_CPU_THRESHOLD, 0)


if __name__ == "__main__":
    unittest.main()
