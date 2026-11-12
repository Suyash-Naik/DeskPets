"""Freeze DeskPets into ``dist/DeskPets/`` with Nuitka (standalone folder, not onefile).

Run with ``pixi run -e build build-exe`` (add ``--console`` for a build that shows errors).
Bundles only the sprite atlas; everything else the app reads is code. Version comes from
``src/__init__.py`` so the exe metadata, installer and tag share one source.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
ATLAS = ("layla-and-chico-pet-spritesheet.json", "layla-and-chico-pet-spritesheet.png")

# Heavy or dev-only packages that must never be pulled in, even transitively.
EXCLUDED = ("napari", "PIL", "pytest", "jupyterlab", "IPython", "ipywidgets", "matplotlib",
            "scipy", "pandas", "tkinter", "unittest", "pydoc", "setuptools", "pip", "tests", "tools")


def version() -> str:
    text = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("could not read __version__ from src/__init__.py")
    return match.group(1)


def command(console: bool) -> list[str]:
    ver = version()
    cmd = [
        sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads",
        "--enable-plugin=pyqt6", "--nofollow-import-to=numpy",
        f"--output-dir={DIST}", "--output-filename=DeskPets.exe",
        f"--windows-console-mode={'force' if console else 'disable'}",
        f"--windows-icon-from-ico={ROOT / 'packaging' / 'DeskPets.ico'}",
        "--product-name=DeskPets", f"--product-version={ver}", f"--file-version={ver}.0",
        "--file-description=DeskPets: Layla and Chico desktop companion",
        "--include-package=src",
    ]
    cmd += [f"--include-data-files={ROOT / 'assets' / name}=assets/{name}" for name in ATLAS]
    cmd += [f"--nofollow-import-to={name}" for name in EXCLUDED]
    cmd.append(str(ROOT / "packaging" / "deskpets.py"))
    return cmd


if __name__ == "__main__":
    raise SystemExit(subprocess.call(command("--console" in sys.argv), cwd=ROOT))
