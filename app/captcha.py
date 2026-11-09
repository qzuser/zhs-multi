"""易盾点选验证码的自动处理

知到播放页的"安全验证"是易盾点选型（图片上散布若干彩色字母，提示"请点击绿色小写u"）。
它由播放器前端渲染、校验结果回传服务端，接口层绕不过，只能真的"点"一次。

这里用 playwright 起一个真实 Chromium，注入已登录的 Cookie，打开播放页，
识别并点中目标字符。点过去之后服务端就不再要验证码了（实测：通过后 probe 立刻返回 0）。

playwright 装在全局 Python 3.11 里（zhs 的 venv 没有），所以这里负责找到那个解释器。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths as _paths

SCRIPT = "yidun_solve.py"

# 找装了 playwright 的解释器。环境变量 ZHS_MULTI_PY_PLAYWRIGHT 优先，
# 其余是本机常见位置 + PATH 里的 py/python3/python。
_PY_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
    r"C:\Python311\python.exe",
    "py",
    "python3",
    "python",
]


def _works(python: str) -> bool:
    """这个解释器能不能 import playwright"""
    cmd = [python, "-c", "import playwright; print('ok')"]
    if python == "py":
        cmd = ["py", "-3.11", "-c", "import playwright; print('ok')"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "ok" in out.stdout


def find_python() -> str | None:
    """找一个装了 playwright 的 Python"""
    env = os.environ.get("ZHS_MULTI_PY_PLAYWRIGHT")
    if env and Path(env).exists() and _works(env):
        return env
    for cand in _PY_CANDIDATES:
        if cand == "py":
            if shutil.which("py") and _works("py"):
                return "py"
            continue
        if cand in ("python", "python3"):
            if shutil.which(cand) and _works(cand):
                return cand
            continue
        if Path(cand).exists() and _works(cand):
            return cand
    return None


def available() -> tuple[bool, str]:
    """(能不能用, 说明)"""
    py = find_python()
    if not py:
        return False, (
            "没找到装了 playwright 的 Python。装法：\n"
            "    py -3.11 -m pip install playwright\n"
            "    py -3.11 -m playwright install chromium"
        )
    script = _paths.tool_path(SCRIPT)
    if not script.exists():
        return False, f"缺少脚本 {script}"
    return True, f"用 {py} 跑 {script.name}"


def solve(
    tries: int = 4,
    min_score: float = 0.6,
    quiet: bool = False,
    rac_id: str = "",
    state_dir: str = "",
) -> tuple[bool, str]:
    """跑一次自动点选。返回 (是否通过, 输出)

    rac_id 是要打开的课程播放页（知到 recruitAndCourseId），从当前选中的课程传进来。
    """
    ok, why = available()
    if not ok:
        return False, why

    py = find_python()
    assert py is not None
    script = _paths.tool_path(SCRIPT)
    extra = ["--tries", str(tries), "--min-score", str(min_score)]
    if rac_id:
        extra += ["--rac", rac_id]
    if state_dir:
        extra += ["--state-dir", str(state_dir)]
    cmd = [py, str(script), *extra]
    if py == "py":
        cmd = ["py", "-3.11", str(script), *extra]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, env=env
        )
    except subprocess.TimeoutExpired:
        return False, "自动点选超时（600 秒）"
    out = (proc.stdout or "") + (proc.stderr or "")
    if not quiet:
        sys.stdout.write(out)
    solved = "[OK] 验证码通过" in out
    return solved, out.strip()[-800:]
