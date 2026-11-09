"""离线复盘点选识别：拿已保存的背景图 + 提示语，只看"会点哪个"，不点也不联网

用法：
    python311 tools\\replay_captcha.py bg-3.jpg "请点击蓝色数字5"
    python311 tools\\replay_captcha.py --all      # 把 state/captcha 里的图按记录全跑一遍
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yidun_solve import (  # noqa: E402
    OUT,
    Glyph,
    components,
    ink_mask,
    ink_color,
    parse_prompt,
)


def analyse(path: Path, prompt_text: str) -> Glyph | None:
    pr = parse_prompt(prompt_text)
    img = np.array(Image.open(path).convert("RGB"))
    mask = ink_mask(img)
    boxes = [b for b in components(mask) if 20 <= b[3] <= 55 and 8 <= b[2] <= 60]
    print(f"\n=== {path.name}｜提示：{prompt_text}")
    print(f"    要找：{pr.describe()}｜色块 {len(boxes)} 个")

    glyphs: list[Glyph] = []
    for bx, by, bw, bh, _a in boxes:
        sub = mask[by: by + bh, bx: bx + bw].astype(np.float32)
        pix = img[by: by + bh, bx: bx + bw][mask[by: by + bh, bx: bx + bw]]
        if len(pix) == 0:
            continue
        cname, _d = ink_color(pix)
        glyphs.append(Glyph((bx, by, bw, bh), cname, sub))

    ref_color, ref_rot = "", None
    if pr.constraint and pr.ref and glyphs:
        ref_g = max(glyphs, key=lambda g: g.score(pr.ref)[0])
        rs, rr = ref_g.score(pr.ref)
        if rs >= 0.45:
            ref_color, ref_rot = ref_g.color, rr
            print(f"    参照「{pr.ref}」={ref_g.box} 颜色={ref_color} 旋转={rr}° 相似度 {rs:.3f}")
        else:
            print(f"    参照「{pr.ref}」没认出来（{rs:.3f}）→ 不加约束")

    want_color = pr.color or (ref_color if pr.constraint == "color" else "")
    pool = [g for g in glyphs if (not want_color or g.color == want_color)]
    if pr.constraint == "color" and ref_color:
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
        print("    约束过滤后没候选 → 放宽到全部")
        pool = list(glyphs)

    for g in sorted(pool, key=lambda g: -g.score(pr.want)[0]):
        s, rot = g.score(pr.want)
        mark = " ←会点这个" if g is sorted(pool, key=lambda g: -g.score(pr.want)[0])[0] else ""
        print(f"    {g.box} 颜色={g.color} 像「{pr.want}」{s:.3f} 旋转{rot}°{mark}")
    if not pool:
        return None
    best = sorted(pool, key=lambda g: -g.score(pr.want)[0])[0]

    canvas = Image.fromarray(img.copy())
    draw = ImageDraw.Draw(canvas)
    bx, by, bw, bh = best.box
    draw.rectangle([bx, by, bx + bw, by + bh], outline=(255, 0, 0), width=3)
    canvas.save(OUT / f"replay-{path.stem}.png")
    print(f"    标图：{OUT / f'replay-{path.stem}.png'}")
    return best


CASES = [
    ("bg-3.jpg", "请点击蓝色数字5"),
    ("bg-4.jpg", "请点击蓝色小写y朝向一样的大写U"),
    ("bg-1.jpg", "请点击侧向的小写v"),
    ("bg-2.jpg", "请点击圆锥朝向一样的数字4"),
]


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] != "--all":
        path = OUT / args[0]
        prompt = args[1] if len(args) > 1 else ""
        analyse(path, prompt)
        return 0
    for name, prompt in CASES:
        path = OUT / name
        if path.exists():
            analyse(path, prompt)
        else:
            print(f"\n=== {name} 不存在，跳过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
