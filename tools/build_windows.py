"""Build both Windows distributions with identical bundled defaults."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ("programs.json", "category_hints.json", "i18n", "profiles", "icons")


def build(arch: str) -> None:
    actual = "x64" if struct.calcsize("P") == 8 else "x86"
    if sys.platform != "win32" or arch != actual:
        raise RuntimeError(f"Build {arch} requires a Windows {arch} Python interpreter")
    for variant in ("onefile", "onedir"):
        name = f"MInstAll_{arch}" + ("_dir" if variant == "onedir" else "")
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                   "--noconsole", f"--{variant}", "--uac-admin", "--name", name,
                   "--icon=icons/system.png"]
        for resource in RESOURCES:
            destination = resource if (ROOT / resource).is_dir() else "."
            command.extend(["--add-data", f"{resource}{os.pathsep}{destination}"])
        subprocess.run([*command, "main.py"], cwd=ROOT, check=True)
    portable = ROOT / "dist" / f"MInstAll_{arch}_dir"
    # Editable portable defaults are also supplied next to the executable.
    for resource in RESOURCES:
        source = ROOT / resource
        if source.is_dir():
            shutil.copytree(source, portable / resource, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("cache", "__pycache__"))
        else:
            shutil.copy2(source, portable / resource)
    archive = Path(shutil.make_archive(
        str(ROOT / "dist" / f"MInstAll_{arch}_portable"), "zip", portable,
    ))
    for artifact in (ROOT / "dist" / f"MInstAll_{arch}.exe", archive):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_suffix(artifact.suffix + ".sha256").write_text(
            f"{digest}  {artifact.name}\n", encoding="ascii",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=("x86", "x64"), required=True)
    build(parser.parse_args().arch)
