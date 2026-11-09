"""易盾点选验证码：识别 + 类人点击

知到播放页的"安全验证"是易盾点选型，提示语有三类：

    请点击绿色小写u                      —— 颜色 + 字符都给了
    请点击蓝色数字5                        —— 颜色 + 字符（数字）
    请点击小写h颜色一样的大写Y            —— 给参照字符 h，要"和 h 同色"的那个 Y
    请点击绿色大写L朝向一样的数字3        —— 给参照字符 L，要"和 L 同朝向"的那个 3

流程：
  1. 解析提示语 → 目标字符 / 参照字符 / 约束（颜色一样 or 朝向一样）
  2. 取背景图做墨迹连通域，逐个色块标颜色，并用系统字体模板（多字体 × 5 个旋转）算
     "和某个字符的相似度"
  3. 有参照字符就先把它找出来，取它的颜色 / 旋转角作为约束；再在满足约束的色块里，
     按"与目标字符的相似度"排序（不猜它是谁，直接比"像不像目标"）
  4. 依次点前几个候选（点错弹窗还在就试下一个），验证码消失即成功；图换了就重新解析

用法：
    python311 tools\\yidun_solve.py --inspect     # 只看识别，不点击
    python311 tools\\yidun_solve.py --tries 4     # 真点，最多 4 轮
    python311 tools\\yidun_solve.py --url URL     # 换页面（自测用）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import string
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
# 状态目录：--state-dir / ZHS_MULTI_STATE 指定，否则就是项目里的 state/
STATE = Path(os.environ.get("ZHS_MULTI_STATE") or (ROOT / "state"))
OUT = STATE / "captcha"
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
# 默认值只是占位；真跑的时候由 `zhs-multi.bat captcha` 或 --rac 传进来
RAC = os.environ.get("ZHS_MULTI_RAC", "0123456789abcdef0123456789abcdef")


def player_url(rac: str) -> str:
    """知到播放页：rac 就是 recruitAndCourseId"""
    return f"https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId={rac}"


PLAYER = player_url(RAC)

COLORS = {
    "红": (205, 60, 60),
    "橙": (230, 130, 40),
    "黄": (215, 195, 45),
    "绿": (60, 170, 80),
    "青": (60, 175, 175),
    "蓝": (60, 100, 210),
    "紫": (150, 70, 190),
    "粉": (230, 130, 180),
    "黑": (45, 45, 45),
    "灰": (135, 135, 135),
    "白": (235, 235, 235),
    "棕": (140, 90, 50),
}
CHARS = string.ascii_lowercase + string.ascii_uppercase + string.digits
FONTS = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\verdana.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]
SIZE = 24
ROTATIONS = (-18, -9, 0, 9, 18)
_TMPL: dict[tuple[str, str, int], np.ndarray] = {}


# ----------------------------------------------------------------- 提示语
@dataclass
class Prompt:
    want: str = ""          # 要点中的字符
    ref: str = ""           # 参照字符（"和 X 同色/同朝向"里的 X）
    color: str = ""         # 明确写出来的颜色（目标自身的颜色）
    constraint: str = ""    # "color" | "rot" | ""
    tilt: bool = False      # "侧向/斜着" —— 要倾斜的那个

    def describe(self) -> str:
        bits = []
        if self.constraint == "color":
            bits.append(f"与「{self.ref}」同色")
        elif self.constraint == "rot":
            bits.append(f"与「{self.ref}」同朝向")
        if self.color:
            bits.append(f"{self.color}色")
        if self.tilt:
            bits.append("倾斜")
        return f"{'/'.join(bits)} 的「{self.want}」" if bits else f"「{self.want}」"


_COLOR_RE = r"[红橙黄绿青蓝紫粉黑灰白棕]"


def parse_prompt(text: str) -> Prompt:
    """把提示语拆成 目标 / 参照 / 约束"""
    p = Prompt()
    p.tilt = any(k in text for k in ("侧向", "倾斜", "斜着", "歪的", "斜的"))
    m = re.search(r"(颜色|朝向|方向)\s*一样", text)
    if m:
        p.constraint = "color" if m.group(1) == "颜色" else "rot"
        before, after = text[: m.start()], text[m.end():]
        bchars = re.findall(r"[A-Za-z0-9]", before)
        achars = re.findall(r"[A-Za-z0-9]", after)
        p.ref = bchars[-1] if bchars else ""
        p.want = achars[0] if achars else ""
        c = re.findall(_COLOR_RE, before)
        if c and not p.tilt:
            p.color = c[-1]
        return p
    color = re.findall(_COLOR_RE, text)
    chars = re.findall(r"[A-Za-z0-9]", text)
    p.color = color[-1] if color else ""
    p.want = chars[-1] if chars else ""
    return p


# --------------------------------------------------------------- 颜色/墨迹
def name_color(rgb: tuple[float, float, float]) -> tuple[str, float]:
    best, dist = "", 1e9
    for name, ref in COLORS.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref)) ** 0.5
        if d < dist:
            best, dist = name, d
    return best, dist


def ink_color(pix: np.ndarray) -> tuple[str, float]:
    """一个色块的代表色：按饱和度取最"浓"的那三分之一再平均

    直接平均会被抗锯齿边缘和背景噪点拉向灰白（实测同一块 均值→灰(79)、取高饱和→绿(72)），
    点选全靠颜色筛，所以这里必须取墨迹本身的颜色。
    """
    if len(pix) == 0:
        return "", 1e9
    mx = pix.max(axis=1).astype(np.float32)
    mn = pix.min(axis=1).astype(np.float32)
    sat = mx - mn
    k = max(6, len(pix) // 3)
    top = pix[np.argsort(-sat)[:k]] if len(pix) >= 12 else pix
    rgb = top.mean(axis=0)
    if float(rgb.max() - rgb.min()) < 25:
        # 本身就是黑/灰/白，"取最浓"没意义，老老实实平均
        rgb = pix.mean(axis=0)
    return name_color(tuple(float(v) for v in rgb))


def ink_mask(img: np.ndarray) -> np.ndarray:
    """墨迹：饱和度高，或与整图背景亮度差得多"""
    r, g, b = (img[:, :, i].astype(np.float32) for i in range(3))
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = mx - mn
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    bg = float(np.median(lum))
    return (sat > 55) | (np.abs(lum - bg) > 48)


def components(mask: np.ndarray, min_area: int = 120, max_area: int = 4000):
    h, w = mask.shape
    seen = np.zeros((h, w), dtype=bool)
    boxes: list[tuple[int, int, int, int, int]] = []
    for y in range(h):
        row = mask[y]
        for x in range(w):
            if not row[x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            ys: list[int] = []
            xs: list[int] = []
            while stack:
                cy, cx = stack.pop()
                ys.append(cy)
                xs.append(cx)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            area = len(ys)
            if min_area <= area <= max_area:
                boxes.append((min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, area))
    return boxes


# -------------------------------------------------------------- 模板匹配
def _norm(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.where(mask > 0.2)
    if len(ys) < 6:
        return np.zeros((SIZE, SIZE), dtype=np.float32)
    crop = mask[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    img = Image.fromarray((crop * 255).astype(np.uint8)).resize((SIZE, SIZE), Image.LANCZOS)
    return np.array(img, dtype=np.float32) / 255.0


def template(ch: str, font_path: str, rot: int) -> np.ndarray:
    key = (ch, font_path, rot)
    if key in _TMPL:
        return _TMPL[key]
    try:
        font = ImageFont.truetype(font_path, 54)
    except OSError:
        _TMPL[key] = np.zeros((SIZE, SIZE), dtype=np.float32)
        return _TMPL[key]
    canvas = Image.new("L", (170, 170), 0)
    ImageDraw.Draw(canvas).text((25, 20), ch, fill=255, font=font)
    if rot:
        canvas = canvas.rotate(rot, resample=Image.BICUBIC, fillcolor=0)
    _TMPL[key] = _norm(np.array(canvas, dtype=np.float32) / 255.0)
    return _TMPL[key]


def score_char(patch: np.ndarray, ch: str) -> tuple[float, int]:
    """这块墨迹"像不像 ch"：返回 (最高 IoU, 对应旋转角)"""
    if not ch:
        return 0.0, 0
    target = _norm(patch)
    if target.sum() < 8:
        return 0.0, 0
    best, best_rot = -1.0, 0
    for font_path in FONTS:
        for rot in ROTATIONS:
            tmpl = template(ch, font_path, rot)
            union = float(np.maximum(target, tmpl).sum())
            if union <= 0:
                continue
            s = float(np.minimum(target, tmpl).sum()) / union
            if s > best:
                best, best_rot = s, rot
    return max(best, 0.0), best_rot


def identify(patch: np.ndarray) -> tuple[str, float]:
    """这块墨迹最像哪个字符（只用于日志/诊断）"""
    target = _norm(patch)
    if target.sum() < 8:
        return "", 0.0
    best, score = "", -1.0
    for ch in CHARS:
        s, _rot = score_char(patch, ch)
        if s > score:
            best, score = ch, s
    return best, score


# ------------------------------------------------------------------ 交互
def human_click(page, x: float, y: float) -> None:
    start_x = x - random.uniform(70, 130)
    start_y = y - random.uniform(45, 95)
    page.mouse.move(start_x, start_y)
    steps = random.randint(14, 24)
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)
        page.mouse.move(
            start_x + (x - start_x) * ease + random.uniform(-1.1, 1.1),
            start_y + (y - start_y) * ease + random.uniform(-1.1, 1.1),
        )
        time.sleep(random.uniform(0.012, 0.028))
    time.sleep(random.uniform(0.08, 0.18))
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.11))
    page.mouse.up()


DIAG = """() => {
  const popup = document.querySelector('.yidun_popup');
  if (!popup) return {found: false};
  const bg = popup.querySelector('.yidun_bg-img');
  const r = bg ? bg.getBoundingClientRect() : null;
  return {
    found: true,
    text: (popup.innerText || '').replace(/\\n/g, ' | ').slice(0, 300),
    img: bg ? bg.src : '',
    rect: r ? {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)} : null,
  };
}"""


def refresh(page) -> None:
    for sel in (".yidun_refresh", "[class*=refresh]", "text=刷新"):
        try:
            el = page.locator(sel).first
            if el.count():
                el.click(force=True, timeout=3000)
                page.wait_for_timeout(2200)
                return
        except Exception:  # noqa: BLE001
            continue


@dataclass
class Glyph:
    box: tuple[int, int, int, int]
    color: str
    sub: np.ndarray
    _best: tuple[str, float] | None = None

    def score(self, ch: str) -> tuple[float, int]:
        """和指定字符的相似度（这才是我们要的：不猜它是什么，只比像不像目标）"""
        return score_char(self.sub, ch)

    def best(self) -> tuple[str, float]:
        if self._best is None:
            self._best = identify(self.sub)
        return self._best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--rac", default="", help="player page recruitAndCourseId")
    ap.add_argument("--state-dir", default="", help="where cookies.json / captcha dumps live")
    ap.add_argument("--min-score", type=float, default=0.34)
    ap.add_argument("--per-attempt", type=int, default=3, help="一张图最多试几个候选")
    ap.add_argument("--url", default="", help="player page URL (overrides --rac)")
    ap.add_argument("--selftest-parse", action="store_true", help="只测提示语解析，不开浏览器")
    args = ap.parse_args()

    global STATE, OUT
    if args.state_dir:
        STATE = Path(args.state_dir)
        OUT = STATE / "captcha"
        OUT.mkdir(parents=True, exist_ok=True)

    if args.selftest_parse:
        cases = [
            ("请点击绿色小写u", ("u", "", "绿", "", False)),
            ("请点击蓝色数字5", ("5", "", "蓝", "", False)),
            ("请点击小写h颜色一样的大写Y", ("Y", "h", "", "color", False)),
            ("请点击绿色大写L朝向一样的数字3", ("3", "L", "绿", "rot", False)),
            ("请点击蓝色小写y朝向一样的大写U", ("U", "y", "蓝", "rot", False)),
            ("请点击侧向的小写v", ("v", "", "", "", True)),
            ("请点击圆锥朝向一样的数字4", ("4", "", "", "rot", False)),
        ]
        bad = 0
        for text, want in cases:
            p = parse_prompt(text)
            got = (p.want, p.ref, p.color, p.constraint, p.tilt)
            flag = "OK " if got == want else "BAD"
            if got != want:
                bad += 1
            print(f"[{flag}] {text} → {got}")
        print(f"[{'OK' if not bad else '!!'}] 解析用例 {len(cases) - bad}/{len(cases)}")
        return 0 if not bad else 1

    cookies = [
        {**c, "domain": ".zhihuishu.com", "path": "/"}
        for c in json.loads((STATE / "cookies.json").read_text(encoding="utf-8"))
    ]
    log: list[str] = []

    def note(t: str) -> None:
        print(t, flush=True)
        log.append(t)

    ok_flag = False
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
        target_url = args.url or player_url(args.rac or RAC)
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)

        for attempt in range(1, args.tries + 1):
            diag = page.evaluate(DIAG)
            if not diag.get("found"):
                note(f"[{attempt}] 没有验证码了")
                ok_flag = True
                break
            prompt_text = diag["text"]
            pr = parse_prompt(prompt_text)
            note(f"[{attempt}] 提示：{prompt_text}")
            if not pr.want or not diag.get("img"):
                note("    解析失败或没图 → 刷新")
                refresh(page)
                continue
            note(f"    要找：{pr.describe()}")

            raw = page.request.get(diag["img"]).body()
            (OUT / f"bg-{attempt}.jpg").write_bytes(raw)
            img = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
            mask = ink_mask(img)
            boxes = [bx for bx in components(mask) if 20 <= bx[3] <= 55 and 8 <= bx[2] <= 60]
            note(f"    墨迹色块 {len(boxes)} 个")

            glyphs: list[Glyph] = []
            canvas = Image.fromarray(img.copy())
            draw = ImageDraw.Draw(canvas)
            for bx, by, bw, bh, _area in boxes:
                sub = mask[by: by + bh, bx: bx + bw].astype(np.float32)
                pix = img[by: by + bh, bx: bx + bw][mask[by: by + bh, bx: bx + bw]]
                if len(pix) == 0:
                    continue
                cname, _d = ink_color(pix)
                g = Glyph((bx, by, bw, bh), cname, sub)
                glyphs.append(g)
                draw.rectangle([bx, by, bx + bw, by + bh], outline=(255, 0, 0), width=1)
                s0, _r0 = g.score(pr.want)
                draw.text((bx, max(0, by - 10)), f"{s0:.2f}/{cname}", fill=(255, 0, 0))
            canvas.save(OUT / f"glyphs-{attempt}.png")

            for g in sorted(glyphs, key=lambda g: -g.score(pr.want)[0]):
                s, rot = g.score(pr.want)
                extra = ""
                if args.inspect:
                    extra = f"（最像「{g.best()[0]}」{g.best()[1]:.2f}）"
                note(f"    {g.box} 颜色={g.color} 像「{pr.want}」{s:.3f} 旋转{rot}°{extra}")
            if args.inspect:
                break

            # ---- 参照字符：拿它的颜色 / 朝向（相似度太低就当没参照，别瞎约束）
            ref_color, ref_rot = "", None
            if pr.constraint and pr.ref and glyphs:
                ref_g = max(glyphs, key=lambda g: g.score(pr.ref)[0])
                rs, rr = ref_g.score(pr.ref)
                if rs >= 0.45:
                    ref_color, ref_rot = ref_g.color, rr
                    note(f"    参照「{pr.ref}」={ref_g.box} 颜色={ref_color} 旋转={rr}° 相似度 {rs:.3f}")
                else:
                    note(f"    参照「{pr.ref}」没认出来（{rs:.3f}）→ 不加约束")

            # ---- 候选排序：直接比"像不像目标字符"
            want_color = pr.color or (ref_color if pr.constraint == "color" else "")
            pool = [g for g in glyphs if (not want_color or g.color == want_color)]
            if pr.constraint == "color" and ref_color:
                # 同色、且不是参照物本身
                pool = [g for g in pool if g.color == ref_color and g.score(pr.ref)[0] < 0.6]
            if ref_rot is not None and pr.constraint == "rot":
                near = [g for g in pool if abs(g.score(pr.want)[1] - ref_rot) <= 9]
                if near:
                    pool = near
            if pr.tilt:
                tilted = [g for g in pool if abs(g.score(pr.want)[1]) >= 9]
                if tilted:
                    pool = tilted
            if not pool:
                note("    约束过滤后没有候选 → 放宽到全部色块")
                pool = list(glyphs)

            ranked = sorted(pool, key=lambda g: -g.score(pr.want)[0])
            for g in ranked[:4]:
                s, rot = g.score(pr.want)
                note(f"    候选 {g.box} 颜色={g.color} 像「{pr.want}」{s:.3f} 旋转{rot}°")

            clicked = False
            for g in ranked[: max(1, args.per_attempt)]:
                s, _rot = g.score(pr.want)
                if s < args.min_score:
                    note(f"    最高分只有 {s:.3f}，低于阈值 → 刷新换一张")
                    break
                bx, by, bw, bh = g.box
                rect = diag["rect"]
                cx = rect["x"] + (bx + bw / 2) * rect["w"] / img.shape[1]
                cy = rect["y"] + (by + bh / 2) * rect["h"] / img.shape[0]
                note(f"    点 {g.box} @({cx:.0f},{cy:.0f}) 相似度 {s:.3f}")
                human_click(page, cx, cy)
                page.wait_for_timeout(2600)
                after = page.evaluate(DIAG)
                if not after.get("found"):
                    note("    [OK] 验证码通过")
                    ok_flag = True
                    clicked = True
                    break
                if after.get("text") != prompt_text:
                    note("    图换了（提示语变了）→ 重新解析")
                    clicked = True
                    break
                note("    还弹着 → 试下一个候选")
            if ok_flag:
                break
            if not clicked:
                refresh(page)

        note("[OK] 验证码通过" if ok_flag else "[!!] 这一轮没通过")
        b.close()

    (OUT / "solve.txt").write_text("\n".join(log), encoding="utf-8")
    return 0 if ok_flag else 3


if __name__ == "__main__":
    raise SystemExit(main())
