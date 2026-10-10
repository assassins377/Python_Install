"""Offline checks for a packaged application, without scanning/installing software."""
from __future__ import annotations

import json
import os
import struct

import config
import i18n
import profiles
from core_impl import load_programs_from_json


def resource_report() -> dict:
    errors: list[str] = []
    catalog = load_programs_from_json()
    if not catalog or not any(catalog.values()):
        errors.append("Program catalog is empty or missing")
    languages = {}
    for language in i18n.SUPPORTED_LANGUAGES:
        translations = i18n._load_translations_file(language)
        languages[language] = len(translations)
        if not translations.get("app.title") or not translations.get("btn.install"):
            errors.append(f"Missing translations: {language}")
    loaded_profiles = profiles.list_profiles()
    if not loaded_profiles:
        errors.append("No default profiles found")
    for relative in ("icons/system.png", "category_hints.json"):
        if not os.path.isfile(config.resource_path(relative)):
            errors.append(f"Missing resource: {relative}")
    return {
        "ok": not errors, "errors": errors, "version": config.APP_VERSION,
        "arch": "x64" if struct.calcsize("P") == 8 else "x86",
        "programs": sum(len(items) for items in catalog.values()),
        "languages": languages, "profiles": len(loaded_profiles),
    }


def write_report(path: str) -> int:
    report = resource_report()
    try:
        import wx

        from gui import MInstAllFrame

        # Import the actual GUI and decode its icon; avoid a full frame's
        # package-manager queries, filesystem watcher and elevation logic.
        app = wx.App(False)
        frame = wx.Frame(None, size=MInstAllFrame.DEFAULT_SIZE)
        bitmap = wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY)
        if not bitmap.IsOk():
            report["errors"].append("GUI icon cannot be decoded")
        frame.Destroy()
        app.Destroy()
    except Exception as exc:
        report["errors"].append(f"GUI check failed: {exc}")
    report["ok"] = not report["errors"]
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0 if report["ok"] else 1
