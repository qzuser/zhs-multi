"""PyInstaller 入口：把 `python -m app` 打成不依赖 Python 的 exe

    python -m PyInstaller --onefile --console --name zhs-multi \
        --paths . --collect-submodules zhs --add-data "tools;tools" \
        packaging/zhs_multi_main.py

（打包脚本见 tools/pack/build-standalone.ps1，直接用那个就行）
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    from app.__main__ import main as app_main

    return app_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
