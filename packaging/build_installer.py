"""Compile ``packaging/DeskPets.iss`` into ``dist/DeskPets-Setup-<version>.exe`` and a checksum.

Run with ``pixi run -e build build-installer`` (builds the windowed exe first). Needs Inno Setup 6.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_exe import DIST, ROOT, version  # noqa: E402


def find_iscc() -> Path:
    candidates = [shutil.which("ISCC"),
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
                  Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
                  Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit("Inno Setup 6 (ISCC.exe) not found; install it from https://jrsoftware.org/isinfo.php")


def main() -> int:
    ver = version()
    if not (DIST / "deskpets.dist" / "DeskPets.exe").is_file():
        raise SystemExit("dist/deskpets.dist/DeskPets.exe is missing; run build-exe first")
    code = subprocess.call([str(find_iscc()), f"/DAppVersion={ver}", str(ROOT / "packaging" / "DeskPets.iss")])
    if code:
        return code
    setup = DIST / f"DeskPets-Setup-{ver}.exe"
    digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    (DIST / "SHA256SUMS").write_text(f"{digest}  {setup.name}\n", encoding="utf-8")
    print(f"{setup.name}: {setup.stat().st_size / 1e6:.1f} MB, sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
