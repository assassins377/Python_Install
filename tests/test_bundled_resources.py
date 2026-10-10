"""Bundled defaults work without sidecars; user edits take precedence."""
import json
from pathlib import Path

import config
import core_impl
import diagnostics
import i18n
import profiles
import scanner


def test_bundled_resources_and_writable_overrides(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    external = tmp_path / "app"
    external.mkdir()
    for name in ("i18n", "profiles", "icons"):
        (bundle / name).mkdir(parents=True)
    (bundle / "programs.json").write_text(json.dumps({
        "categories": {"Default": [{"name": "Demo", "cmd": "demo.exe"}]},
    }))
    (bundle / "category_hints.json").write_text("{}")
    (bundle / "icons" / "system.png").write_bytes(b"icon")
    for language in i18n.SUPPORTED_LANGUAGES:
        (bundle / "i18n" / f"{language}.json").write_text(json.dumps({
            "app.title": "App", "btn.install": "Install",
        }))
    for name in ("default", "second"):
        (bundle / "profiles" / f"{name}.json").write_text(json.dumps({"name": name, "programs": ["Demo"]}))
    monkeypatch.setattr(config, "BUNDLE_DIR", str(bundle))
    monkeypatch.setattr(config, "SCRIPT_DIR", str(external))
    monkeypatch.setattr(config, "CONFIG_FILE", str(external / "programs.json"))
    monkeypatch.setattr(i18n, "I18N_DIR", str(external / "i18n"))
    monkeypatch.setattr(profiles, "PROFILES_DIR", str(external / "profiles"))
    assert diagnostics.resource_report()["ok"]
    assert Path(config.resource_path("icons/system.png")) == bundle / "icons" / "system.png"
    assert core_impl.load_programs_from_json()["Default"][0]["name"] == "Demo"
    assert scanner.save_merged_to_disk({"Custom": [{"name": "User", "cmd": "user.exe"}]})
    assert "Custom" in core_impl.load_programs_from_json()
    assert "Default" in json.loads((bundle / "programs.json").read_text())["categories"]
    (external / "profiles").mkdir()
    (external / "profiles" / "default.json").write_text(json.dumps({"name": "User", "programs": []}))
    assert {p["name"] for p in profiles.list_profiles()} == {"User", "second"}
    (external / "i18n").mkdir()
    (external / "i18n" / "ru.json").write_text(json.dumps({"btn.install": "Custom"}))
    assert i18n._load_translations_file("ru")["btn.install"] == "Custom"
    assert i18n._load_translations_file("en")["btn.install"] == "Install"


def test_missing_bundle_resources_fail_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BUNDLE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(i18n, "I18N_DIR", str(tmp_path / "i18n"))
    monkeypatch.setattr(profiles, "PROFILES_DIR", str(tmp_path / "profiles"))
    report = diagnostics.resource_report()
    assert not report["ok"]
    assert report["errors"]
    assert not list(Path(tmp_path).iterdir())
