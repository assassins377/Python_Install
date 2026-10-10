"""Run the shipped EXE and extracted portable ZIP from clean Unicode paths."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def smoke(arch: str) -> None:
    reports = {}
    with tempfile.TemporaryDirectory(prefix="minstall-smoke-") as temp:
        for variant in ("onefile", "portable"):
            target = Path(temp) / f"{variant} test 测试"
            target.mkdir()
            if variant == "onefile":
                executable = target / f"MInstAll_{arch}.exe"
                shutil.copy2(ROOT / "dist" / executable.name, executable)
            else:
                with zipfile.ZipFile(ROOT / "dist" / f"MInstAll_{arch}_portable.zip") as archive:
                    archive.extractall(target)
                executable = target / f"MInstAll_{arch}_dir.exe"
            report_path = target / "self-check.json"
            subprocess.run([str(executable), "--self-check", str(report_path)],
                           cwd=target, check=True, timeout=120)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not report["ok"] or report["arch"] != arch:
                raise RuntimeError(f"{variant} smoke check failed: {report}")
            reports[variant] = report
    (ROOT / "dist" / f"smoke-{arch}.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=("x86", "x64"), required=True)
    smoke(parser.parse_args().arch)
