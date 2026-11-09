"""运行时路径：源码跑 和 打包成 exe 跑，两套目录规则

源码运行：
    <项目>/app/paths.py  ->  PROJECT_DIR = <项目>
                             BUNDLE_DIR  = <项目>

打包成 exe（PyInstaller onefile）：
    exe 所在目录         ->  PROJECT_DIR = <exe 所在目录>   （config.ini / state / logs 都在这）
    解包出来的临时目录    ->  BUNDLE_DIR  = sys._MEIPASS     （tools/ 这类随程序走的资源）

所以 config.ini、state/、logs/ 永远在 exe 旁边，用户看得见、能改；
tools/*.py 这种只读脚本跟着 exe 走，用户看不到。
"""

from __future__ import annotations

import sys
from pathlib import Path


def frozen() -> bool:
    """是不是 PyInstaller 打出来的 exe"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """只读资源（tools/）所在目录"""
    if frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def project_dir() -> Path:
    """可写目录（config.ini / state / logs）"""
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def tool_path(name: str) -> Path:
    """tools/ 下的脚本；先找 exe 旁边（用户可以自己覆盖），再找打包进去的那份"""
    local = project_dir() / "tools" / name
    if local.exists():
        return local
    return bundle_dir() / "tools" / name
