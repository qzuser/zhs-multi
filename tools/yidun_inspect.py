"""检查易盾弹窗（yidun_popup）是哪种验证：滑块 / 点选 / 文字

用法：python311 tools\\yidun_inspect.py

产出（state/captcha/）：
    yidun.html     弹窗序列化结果（含 shadow DOM）
    yidun.png      弹窗截图
    yidun.txt      诊断文本
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "state" / "captcha"
OUT.mkdir(parents=True, exist_ok=True)
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
RAC = "0123456789abcdef0123456789abcdef"
PLAYER = f"https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId={RAC}"

# 递归展开 shadow DOM 的序列化 + 结构摘要
DUMP = """() => {
  const walk = (root, depth) => {
    if (depth > 6) return '';
    let out = '';
    for (const el of root.querySelectorAll('*')) {
      const cls = (el.className && el.className.toString) ? el.className.toString() : '';
      const b = el.getBoundingClientRect();
      const tag = el.tagName.toLowerCase();
      if (!cls && !el.shadowRoot && tag !== 'img' && tag !== 'input') continue;
      out += '  '.repeat(depth) + `<${tag} class="${cls.slice(0,80)}" ${Math.round(b.width)}x${Math.round(b.height)}>`;
      if (tag === 'img') out += ` src=${(el.src||'').slice(0,120)}`;
      if (tag === 'input') out += ` type=${el.type}`;
      out += '\\n';
      if (el.shadowRoot) out += walk(el.shadowRoot, depth + 1);
    }
    return out;
  };
  const popup = document.querySelector('.yidun_popup') || document.querySelector('[class*=yidun]');
  if (!popup) return {found: false};
  const r = popup.getBoundingClientRect();
  const text = (popup.innerText || '').replace(/\\n/g, ' | ').slice(0, 400);
  const slider = popup.querySelector('.yidun_slider');
  const bgimg = popup.querySelector('.yidun_bgimg');
  const clickTip = /依次点击|按顺序点击/.test(text);
  return {
    found: true,
    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    classes: popup.className,
    text,
    kind: slider ? 'slider' : (bgimg ? 'image' : 'unknown'),
    clickTip,
    hasShadow: !!(popup.shadowRoot || popup.querySelector('*')?.shadowRoot),
    tree: walk(popup, 0).slice(0, 6000),
    html: popup.outerHTML.slice(0, 8000),
  };
}"""


def main() -> int:
    lines: list[str] = []

    def note(t: str) -> None:
        print(t, flush=True)
        lines.append(t)

    cookies = [
        {**c, "domain": ".zhihuishu.com", "path": "/"}
        for c in json.loads((ROOT / "state" / "cookies.json").read_text(encoding="utf-8"))
    ]
    with sync_playwright() as p:
        _kw = {"headless": False}
        if CHROME:
            _kw["executable_path"] = CHROME
        b = p.chromium.launch(
            **_kw,
            args=[
                "--window-position=-2400,-2400",
                "--window-size=1400,900",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = b.new_context(
            viewport={"width": 1400, "height": 860},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(PLAYER, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        note(f"title={page.title()}")

        diag = page.evaluate(DUMP)
        if not diag.get("found"):
            note("没找到 yidun 弹窗，可能这一轮没触发")
            page.screenshot(path=str(OUT / "no-yidun.png"))
            ctx.close()
            b.close()
            return 2

        note(f"类型={diag['kind']} 文字={diag['text'][:120]}")
        note(f"尺寸={diag['rect']} shadow={diag['hasShadow']} 点选提示={diag['clickTip']}")
        (OUT / "yidun.txt").write_text("\n".join(lines) + "\n\n" + diag["tree"], encoding="utf-8")
        (OUT / "yidun.html").write_text(diag["html"], encoding="utf-8")
        print(diag["tree"][:4000], flush=True)
        try:
            page.locator(".yidun_popup").first.screenshot(path=str(OUT / "yidun.png"))
            note("已截图 yidun.png")
        except Exception as exc:  # noqa: BLE001
            note(f"截图失败：{exc}")
        page.screenshot(path=str(OUT / "full.png"))
        ctx.close()
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
