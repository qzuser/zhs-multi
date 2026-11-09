"""看清一张验证码图里到底有什么：所有连通域 + 每个块的取色方式对比"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yidun_solve import COLORS, OUT, components, ink_mask, name_color  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "bg-3.jpg"
img = np.array(Image.open(OUT / name).convert("RGB"))
mask = ink_mask(img)
print(f"{name} 尺寸 {img.shape[1]}x{img.shape[0]}｜墨迹像素 {int(mask.sum())}")
print(f"背景中位亮度 {float(np.median(0.299*img[:,:,0]+0.587*img[:,:,1]+0.114*img[:,:,2])):.0f}")

boxes = sorted(components(mask, min_area=40, max_area=9000), key=lambda b: -b[4])
for bx, by, bw, bh, area in boxes[:20]:
    pix = img[by: by + bh, bx: bx + bw][mask[by: by + bh, bx: bx + bw]]
    mean = pix.mean(axis=0)
    mx = pix.max(axis=1).astype(np.float32)
    mn = pix.min(axis=1).astype(np.float32)
    sat = mx - mn
    if len(pix) >= 12:
        top = pix[np.argsort(-sat)[: max(6, len(pix) // 3)]]
    else:
        top = pix
    n_mean, d_mean = name_color(tuple(mean))
    n_top, d_top = name_color(tuple(top.mean(axis=0)))
    print(
        f"  box=({bx},{by},{bw},{bh}) area={area:<5} "
        f"均值RGB=({mean[0]:.0f},{mean[1]:.0f},{mean[2]:.0f})→{n_mean}({d_mean:.0f})  "
        f"高饱和RGB=({top.mean(axis=0)[0]:.0f},{top.mean(axis=0)[1]:.0f},{top.mean(axis=0)[2]:.0f})→{n_top}({d_top:.0f})"
    )
