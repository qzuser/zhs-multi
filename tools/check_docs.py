"""文档与命令行一致性自检 —— 不联网

把 README / QUICK-START / USER-MANUAL 里出现的每条 `zhs-multi.bat ...` 抽出来，
逐个对 argparse 的解析器核对：子命令是否存在、用到的开关是否真的注册过。

跑法：
    python tools\\check_docs.py
    zhs-multi.bat selftest --docs

用处：改了参数名/删了开关之后，文档里残留的旧写法会立刻被抓出来。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.__main__ import build_parser  # noqa: E402

DOCS = ["README.md", "QUICK-START.txt", "USER-MANUAL.md"]
# 文档里允许出现的"不是本工具"的命令（原版 zhs、浏览器、系统命令等）
SKIP_SUBS = {"play", "login.bat"}
CMD_RE = re.compile(r"zhs-multi\.bat\s+([A-Za-z_-]+)([^\n`|]*)")

FAILS: list[str] = []


def _subcommands(parser) -> dict[str, object]:
    for action in parser._actions:  # noqa: SLF001
        if action.__class__.__name__ == "_SubParsersAction":
            return dict(action.choices)
    return {}


def _options(parser) -> set[str]:
    opts: set[str] = set()
    for action in parser._actions:  # noqa: SLF001
        opts.update(action.option_strings)
    return opts


def _clean_token(tok: str) -> str:
    return tok.strip("`.,;:()[]{}\"'").split("=")[0]


def main() -> int:
    parser = build_parser()
    subs = _subcommands(parser)
    top = _options(parser)
    print(f"文档自检：{len(subs)} 个子命令 / {len(DOCS)} 份文档")

    total = 0
    seen: set[tuple[str, str]] = set()
    for name in DOCS:
        path = ROOT / name
        if not path.exists():
            print(f"  [!!] 缺文档：{name}")
            FAILS.append(f"缺少 {name}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found = 0
        for m in CMD_RE.finditer(text):
            sub = m.group(1)
            if sub in SKIP_SUBS:
                continue
            found += 1
            total += 1
            line_no = text[: m.start()].count("\n") + 1
            where = f"{name}:{line_no}"
            if sub not in subs:
                print(f"  [!!] {where} 子命令不存在：{sub}")
                FAILS.append(f"{where} 未知子命令 {sub}")
                continue
            ok_opts = _options(subs[sub]) | top
            for raw in m.group(2).replace("`", " ").split():
                tok = _clean_token(raw)
                if not tok.startswith("-") or tok in ("-", "--"):
                    continue
                # `-12`、`-9` 这类是平台错误码，不是命令行开关
                if tok.lstrip("-").isdigit():
                    continue
                if "#" in tok:
                    break  # 注释开始了，后面的都不算命令
                if tok not in ok_opts:
                    print(f"  [!!] {where} `{sub}` 没有这个开关：{tok}")
                    FAILS.append(f"{where} {sub} 缺开关 {tok}")
                else:
                    seen.add((sub, tok))
        print(f"  [OK] {name}：{found} 条命令")

    # 反过来：每个子命令至少要在文档里出现过一次，否则用户根本不知道有它
    documented = {sub for sub, _ in seen} | {
        m.group(1) for name in DOCS for m in CMD_RE.finditer(
            (ROOT / name).read_text(encoding="utf-8", errors="replace")
        )
        if m.group(1) not in SKIP_SUBS
    }
    for sub in subs:
        if sub not in documented:
            print(f"  [!!] 文档里没提到子命令：{sub}")
            FAILS.append(f"未写文档：{sub}")

    print()
    if FAILS:
        print(f"[!!] {len(FAILS)} 处不一致")
        for item in FAILS:
            print(f"     - {item}")
        return 1
    print(f"[OK] 文档与命令行一致（{total} 条命令 / {len(subs)} 个子命令）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
