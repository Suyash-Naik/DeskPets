"""Frozen-build entry point. Nuitka compiles this script; it follows the import into ``src``."""
from src.main import main

if __name__ == "__main__":
    raise SystemExit(main())
