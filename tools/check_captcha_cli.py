"""离线验证 `captcha` 的两个分支（不联网、不开浏览器）"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import captcha as captcha_mod
from app.__main__ import cmd_captcha

args = SimpleNamespace(tries=1, min_score=0.6, check=False, config="")

print("=== 分支 A：点过去了 ===")
captcha_mod.solve = lambda **kw: (True, "")  # type: ignore[assignment]
rc = cmd_captcha(args)
print("rc =", rc, "（期望 0）")

print()
print("=== 分支 B：没点过去 ===")
captcha_mod.solve = lambda **kw: (False, "识别不到目标字符")  # type: ignore[assignment]
rc = cmd_captcha(args)
print("rc =", rc, "（期望 3）")

print()
print("=== 分支 C：环境不可用 ===")
captcha_mod.available = lambda: (False, "没找到装了 playwright 的 Python")  # type: ignore[assignment]
rc = cmd_captcha(args)
print("rc =", rc, "（期望 2）")
