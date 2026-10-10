"""Check release payloads after upload/download, without executing binaries."""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path


def verify(directory: Path) -> None:
    for arch in ("x86", "x64"):
        for suffix in (".exe", "_portable.zip"):
            artifact = directory / f"MInstAll_{arch}{suffix}"
            if not artifact.is_file() or not artifact.stat().st_size:
                raise ValueError(f"Missing/empty artifact: {artifact.name}")
            expected = artifact.with_suffix(artifact.suffix + ".sha256").read_text().split()
            if expected != [hashlib.sha256(artifact.read_bytes()).hexdigest(), artifact.name]:
                raise ValueError(f"Invalid checksum: {artifact.name}")
        with zipfile.ZipFile(directory / f"MInstAll_{arch}_portable.zip") as archive:
            required = {f"MInstAll_{arch}_dir.exe", "programs.json", "category_hints.json",
                        "icons/system.png", "i18n/ru.json", "i18n/en.json", "i18n/zh.json",
                        "profiles/developer.json", "profiles/minimal.json", "profiles/office.json"}
            missing = required.difference(archive.namelist())
            if missing:
                raise ValueError(f"Portable {arch} missing resources: {sorted(missing)}")
        reports = json.loads((directory / f"smoke-{arch}.json").read_text(encoding="utf-8"))
        for variant in ("onefile", "portable"):
            if not reports[variant]["ok"] or reports[variant]["arch"] != arch:
                raise ValueError(f"Smoke check failed: {arch} {variant}")
    print("Both architectures: EXE/ZIP checksums, resources and smoke reports verified")


if __name__ == "__main__":
    verify(Path(sys.argv[1] if len(sys.argv) > 1 else "dist"))
