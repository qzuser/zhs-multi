"""最小的页面探针：打开指定 URL，注入 Cookie，打印标题/最终 URL/可见文本

用法：
    python311 tools\\page_probe.py [URL] [等待秒数]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
def _find_chrome() -> str | None:
    """本机 Chromium 路径：PLAYWRIGHT_CHROMIUM 优先，否则找 ms-playwright 里最新的那个。
    都找不到就返回 None，交给 Playwright 用自带的浏览器。"""
    env = os.environ.get("PLAYWRIGHT_CHROMIUM")
    if env and Path(env).exists():
        return env
    base = Path(os.environ.get("LOCALAPPDATA") or "") / "ms-playwright"
    if base.exists():
        hits = sorted(str(x) for x in base.glob("chromium-*/chrome-win64/chrome.exe"))
        if hits:
            return hits[-1]
    return None


CHROME = _find_chrome()
DEFAULT = (
    "https://studyh5.zhihuishu.com/videoStudy.html#/studyVideo"
    "?recruitAndCourseId=0123456789abcdef0123456789abcdef"
)

url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
wait_s = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

cookies = [
    {**c, "domain": ".zhihuishu.com", "path": "/"}
    for c in json.loads((ROOT / "state" / "cookies.json").read_text(encoding="utf-8"))
]

with sync_playwright() as p:
    _kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
    if CHROME:
        _kw["executable_path"] = CHROME
    b = p.chromium.launch(**_kw)
    ctx = b.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    )
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(int(wait_s * 1000))
    info = page.evaluate(
        """() => ({
            title: document.title,
            href: location.href,
            text: (document.body.innerText || '').replace(/\\n{2,}/g, '\\n').slice(0, 800),
            videos: document.querySelectorAll('video').length,
            dialogs: [...document.querySelectorAll('[class*=dialog]')]
                .map(d => (d.innerText || '').trim()).filter(Boolean).slice(0, 5),
        })"""
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if errors:
        print("console errors:", json.dumps(errors[:5], ensure_ascii=False))
    print("cookies now:", ", ".join(sorted(c["name"] for c in ctx.cookies())))
    ctx.close()
    b.close()
