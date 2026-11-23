"""终端输出：一块**固定大小**的屏幕，原地重画，绝不刷屏

## 为什么重写

旧版把「刷完一个视频」当事件行逐条打出来，23~88 个任务就是同样多行，再加状态行不停重画，
屏幕上滚成一片。这里改成：**屏幕上永远只有固定几行**，每次更新先擦掉旧的那块再画新的。

```
┌─ 固定区域（原地刷新，行数不变）──────────────
│ [██████████░░░░░░░░░░░░░░░░░░]  46.6%  7/15  已用 12:30  剩 ~14:20
│   W1 play  [######··]  62.0% 1.2 自我认知
│   W2 play  [###·····]  31.5% 2.1 生涯决策
│   W3 play  [########] 100.0% 1.1 职业概述
│   W4 pause [##······]  22.0% 2.3 时间管理
│   完成 1.1 职业概述
└──────────────────────────────────────────
```

技术上是 ANSI：`ESC[{n}A` 把光标移回上次画的第一行，`ESC[J` 清掉下面所有，
再写新内容。Windows 10+ 的 conhost 支持；万一不支持就退化成「有进展才打一行」
（有节流，不会刷屏）。

## 约定

- `dashboard()` 期间，`info/ok/warn/err` **不落到滚动历史**，只进「最近」槽位，
  屏幕高度不变；
- 重要的东西（报错、最终汇总）在 `dashboard()` 退出后一次性打印；
- 非 tty（重定向到文件）时完全不画框，改为「有实质进展才打一行」。
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any

_LOCK = threading.Lock()
_RECENT: "collections.deque[str]" = collections.deque(maxlen=3)
_STATUS: dict[str, dict[str, Any]] = {}
_ACTIVE = False

# zhs 视频模块的进度条特征：`[####......] 45%` / `pause [##..] 20`
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_BAR_RE = re.compile(r"\[[#\.\s]*\]\s*(\d{1,3}(?:\.\d+)?)\s*%")
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")

_C = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[36m",
    "bold": "\033[1m",
}


def _want_color() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


_COLOR = _want_color()


def _c(text: str, color: str) -> str:
    if not _COLOR:
        return text
    return f"{_C.get(color, '')}{text}{_C['reset']}"


def _hint(text: str) -> str:
    """打包成 exe 后，提示语里的 `zhs-multi.bat` 要换成 `zhs-multi.exe`"""
    try:
        from . import paths

        if paths.frozen():
            return text.replace("zhs-multi.bat", "zhs-multi.exe")
    except Exception:  # noqa: BLE001,S110
        pass
    return text


# ---------------------------------------------------------------- ANSI 支持


def _enable_vt() -> bool:
    """Windows 上显式打开 VT 处理；已经是 tty 且能写 ANSI 才算成功"""
    if os.name != "nt":
        try:
            return bool(sys.stdout.isatty())
        except Exception:  # noqa: BLE001
            return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        # 64 位必须声明 restype，否则 handle 被截成 int32，GetConsoleMode 直接失败
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        if not (mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING):
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        return True
    except Exception:  # noqa: BLE001
        return False


class _Screen:
    """固定区域的原地重画

    `drawn` 记着上一次画了几行，下次先把光标移回那块的第一行再整块清掉。
    行数变了也不会留残影（`ESC[J` 清到屏幕末尾）。
    """

    def __init__(self, vt: bool) -> None:
        self.vt = vt
        self.drawn = 0

    def redraw(self, lines: list[str]) -> None:
        if not self.vt:
            return
        buf: list[str] = []
        if self.drawn:
            buf.append(f"\x1b[{self.drawn}A")     # 回到上次那块的第一行
        buf.append("\r\x1b[J")                    # 清到屏幕末尾
        for ln in lines:
            buf.append(ln + "\x1b[K\n")           # 每行自己清一次行尾
        if not lines:
            buf.append("")
        try:
            sys.stdout.write("".join(buf))
            sys.stdout.flush()
        except Exception:  # noqa: BLE001,S110
            pass
        self.drawn = len(lines)

    def finish(self) -> None:
        """收尾：块留在屏幕上，光标落到它下面一行"""
        if self.vt:
            try:
                sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001,S110
                pass
        self.drawn = 0

    def clear(self) -> None:
        if self.vt and self.drawn:
            try:
                sys.stdout.write(f"\x1b[{self.drawn}A\r\x1b[J")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001,S110
                pass
        self.drawn = 0


# ---------------------------------------------------------------- 基础输出


def _emit(text: str) -> None:
    """普通输出。dashboard 期间只进「最近」槽，不滚屏。"""
    text = _hint(text)
    with _LOCK:
        if _ACTIVE:
            _RECENT.append(text)
            return
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001,S110
        pass


def say(text: str = "") -> None:
    _emit(text)


def rule(char: str = "-", width: int = 66) -> None:
    _emit(_c(char * width, "dim"))


def info(text: str) -> None:
    _emit(f"  {_c('·', 'blue')} {text}")


def ok(text: str) -> None:
    _emit(f"  {_c('[OK]', 'green')} {text}")


def warn(text: str) -> None:
    _emit(f"  {_c('[!!]', 'yellow')} {text}")


def err(text: str) -> None:
    _emit(f"  {_c('[ERR]', 'red')} {text}")


def flash(text: str) -> None:
    """屏幕上闪一行（不占历史），用于"刚完成一个"这种即时反馈"""
    with _LOCK:
        _RECENT.append(_hint(text))


# ---------------------------------------------------------------- 每线程状态


def set_status(worker: str, **fields: Any) -> None:
    with _LOCK:
        _STATUS.setdefault(worker, {}).update(fields)


def clear_status(worker: str) -> None:
    with _LOCK:
        _STATUS.pop(worker, None)


# ---------------------------------------------------------------- 总进度

# 课程级统计：[{"label": 课程名, "total": 总数, "done": 已完成}]
_COURSES: list[dict[str, Any]] = []


def courses_reset(items: list[tuple[str, int]]) -> None:
    """按课程分组登记总数（顺序保持传入顺序）"""
    with _LOCK:
        _COURSES.clear()
        for label, total in items:
            _COURSES.append({"label": label, "total": int(total), "done": 0})


def course_done(label: str) -> None:
    """某门课完成一个（找不到就忽略）"""
    if not label:
        return
    with _LOCK:
        for c in _COURSES:
            if c["label"] == label:
                c["done"] += 1
                return


def course_line_of(label: str) -> str:
    """某门课的 "已完成/总数"，给完成提示用（例如 `4/32`）"""
    with _LOCK:
        for c in _COURSES:
            if c["label"] == label:
                return f"{c['done']}/{c['total']}"
    return "?"


def courses_line(width: int = 0) -> str:
    """一行显示每门课的整体进度，正在刷的那门用 > 标出来"""
    def _short(label: str) -> str:
        # 去掉「[知到] 」这种前缀，省宽度
        core = re.sub(r"^\[[^\]]*\]\s*", "", label)
        return core if len(core) <= 10 else core[:9] + "…"

    with _LOCK:
        items = [dict(c) for c in _COURSES]
        active = {_short(str(v.get("course", ""))) for v in _STATUS.values()}
    if not items:
        return ""
    limit = (width or _term_width()) - 2
    parts: list[str] = []
    for c in items:
        short = _short(str(c["label"]))
        done, total = int(c["done"]), int(c["total"])
        mark = ">" if short in active else ""
        seg = f"{mark}{short} {done}/{total}"
        parts.append(seg if done < total else f"{mark}{short} [OK]")
    line = " · ".join(parts)
    if len(line) > limit:
        line = line[: max(10, limit - 1)] + "…"
    return line


_PROGRESS: dict[str, Any] = {
    "total": 0,
    "ok": 0,
    "skip": 0,
    "fail": 0,
    "stopped": 0,
    "started": 0.0,
    "label": "",
    "durations": [],
    "risks": 0,
    "jobs": 1,
}


def progress_reset(total: int, label: str = "", jobs: int = 1) -> None:
    with _LOCK:
        _PROGRESS.update(
            total=int(total), ok=0, skip=0, fail=0, stopped=0,
            started=time.time(), label=label, durations=[], risks=0,
            jobs=max(1, int(jobs)),
        )


def progress_count(status: str, seconds: float = 0.0) -> None:
    with _LOCK:
        if status in _PROGRESS:
            _PROGRESS[status] += 1
        if status == "ok" and seconds > 0:
            _PROGRESS["durations"].append(float(seconds))
            del _PROGRESS["durations"][:-20]


def progress_risk(kind: str = "") -> int:
    with _LOCK:
        _PROGRESS["risks"] += 1
        return int(_PROGRESS["risks"])


def progress_risk_count() -> int:
    with _LOCK:
        return int(_PROGRESS["risks"])


def progress_done() -> int:
    with _LOCK:
        return int(_PROGRESS["ok"]) + int(_PROGRESS["skip"])


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _term_width() -> int:
    try:
        return shutil.get_terminal_size((110, 24)).columns
    except Exception:  # noqa: BLE001
        return 110


def progress_line() -> str:
    with _LOCK:
        p = dict(_PROGRESS)
        p["durations"] = list(_PROGRESS["durations"])

    total = int(p["total"])
    if total <= 0:
        return ""
    done = int(p["ok"]) + int(p["skip"])
    pct = done * 100.0 / total
    # 学习通刷课脚本那种形状：一条粗条 + 百分比 + 计数 + 剩余
    width = _term_width()
    bar_len = max(12, min(28, width - 46))
    filled = max(0, min(bar_len, int(bar_len * done / total + 0.0001)))
    bar = "█" * filled + "░" * (bar_len - filled)

    elapsed = time.time() - (p["started"] or time.time())
    left = max(0, total - done)
    if left <= 0:
        eta = 0.0
    elif done:
        eta = elapsed / done * left
    elif p["durations"]:
        eta = sum(p["durations"]) / len(p["durations"]) * left / int(p["jobs"] or 1)
    else:
        eta = 0.0

    bits = [f"[{bar}] {pct:5.1f}%", f"{done}/{total}", f"已用 {_hms(elapsed)}"]
    bits.append(f"剩 ~{_hms(eta)}" if left else "全部完成")
    if p["fail"]:
        bits.append(f"失败 {p['fail']}")
    if p["stopped"]:
        bits.append(f"中止 {p['stopped']}")
    if p["risks"]:
        bits.append(f"风控 {p['risks']}")
    return "  ".join(bits)


def _worker_lines(width: int) -> list[str]:
    with _LOCK:
        items = [(k, dict(v)) for k, v in sorted(_STATUS.items())]
    lines: list[str] = []
    for worker, slot in items:
        pct = slot.get("pct")
        label = str(slot.get("label", ""))[: max(8, width - 34)]
        state = str(slot.get("state", ""))
        if pct is None:
            lines.append(f"  {_c(worker, 'dim')} {_c(state or 'idle', 'dim')} {label}")
            continue
        value = float(pct)
        bar_len = 10
        filled = max(0, min(bar_len, int(bar_len * value / 100)))
        bar = "█" * filled + "·" * (bar_len - filled)
        # 当前这条视频的秒数进度（task 的 video_sec × 播放百分比）
        extra = ""
        total_sec = slot.get("total")
        if total_sec:
            try:
                cur = float(total_sec) * value / 100.0
                extra = f"  {_c(f'{cur:.0f}s/{float(total_sec):.0f}s', 'dim')}"
            except (TypeError, ValueError):
                extra = ""
        lines.append(f"  {_c(worker, 'blue')} {state}[{bar}]{value:>5.1f}% {label}{extra}")
    return lines


def _screen_lines() -> list[str]:
    width = _term_width()
    lines: list[str] = []
    head = progress_line()
    if head:
        lines.append(_c(head, "bold"))
    cline = courses_line(width)
    if cline:
        lines.append(_c("  " + cline, "blue"))
    lines.extend(_worker_lines(width))
    with _LOCK:
        recent = list(_RECENT)
    if recent:
        lines.append(_c("  " + recent[-1].strip()[: width - 4], "dim"))
    return lines


def _progress_key() -> tuple:
    """去重键：只看「实质性进展」，不看计时

    进度行里的「已用 00:03」每秒都在变，如果拿它当去重键，等于每 3 秒必打一行——
    跑两小时就是两千多行，还是刷屏。所以只认 完成数/总数/失败/风控。
    """
    with _LOCK:
        return (
            int(_PROGRESS["ok"]),
            int(_PROGRESS["skip"]),
            int(_PROGRESS["fail"]),
            int(_PROGRESS["stopped"]),
            int(_PROGRESS["risks"]),
            int(_PROGRESS["total"]),
        )


def _render_loop(stop: threading.Event, screen: _Screen, interval: float) -> None:
    while not stop.is_set():
        screen.redraw(_screen_lines())
        stop.wait(interval)
    # 留最后一块（用户要看到 100%），光标落到块下方，后面的汇总接着打
    screen.redraw(_screen_lines())
    screen.finish()


def _ticker_loop(stop: threading.Event, interval: float) -> None:
    """非 tty：只在**有实质进展**时打一行

    重定向到文件 / 被别的程序调用时用。23 个视频就打 23 行左右，不会堆成山。
    """
    last = None
    while not stop.wait(interval):
        key = _progress_key()
        if key == last:
            continue
        last = key
        line = progress_line()
        if line:
            _emit(line)


# ---------------------------------------------------------------- zhs 输出接管


class Sink:
    """替代 zhs 视频模块里的 print / tree_print / wipe_line

    - 进度行（含百分比）只更新 worker 状态，不刷屏
    - 其它输出进「最近」槽，只占屏幕上固定一行
    """

    def __init__(self, enabled: bool = True, on_event: Any = None) -> None:
        self._enabled = enabled
        self._on_event = on_event

    def _worker_name(self) -> str:
        """线程名 → 状态行的键

        runner 的线程叫 `pool_0`/`pool_1`，而我们状态行用的是 `W1`/`W2`。
        不映射的话同一条视频会出两行（一行 W1、一行 pool_0）。
        """
        raw = threading.current_thread().name
        m = re.match(r"^pool_(\d+)$", raw)
        if m:
            return f"W{int(m.group(1)) + 1}"
        if raw.startswith("MainThread"):
            # 主线程里刷进度条时归到已存在的第一个 worker，别凭空多一行
            with _LOCK:
                if _STATUS:
                    return sorted(_STATUS)[0]
            return "W1"
        return raw

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        text = " ".join(str(a) for a in args).strip()
        if not text:
            return
        # 先把颜色码剥掉：zhs 的 progress_bar() 返回的是
        #   [<青色####><暗色    >] <白色78.3%>
        # 不剥的话正则匹配不到方括号，pct 永远不更新。
        plain = _ANSI_RE.sub("", text)
        worker = self._worker_name()
        m = _BAR_RE.search(plain)
        if m:
            head = plain.split()[0].lower() if plain.split() else ""
            if head.startswith("pause"):
                state = "pause"
            elif head.startswith("answering"):
                state = "answr"
            else:
                state = "play "
            set_status(worker, pct=float(m.group(1)), state=state)
            return
        if "\r" in text and _PCT_RE.search(plain):
            return
        self.event(text, depth=kwargs.get("depth"), enabled=kwargs.get("enabled", True))

    def event(self, text: str, indent: int = 1, depth: int | None = None,
              enabled: bool = True, **_kw: Any) -> None:
        """兼容 zhs 的 tree_print(text, depth=, enabled=) 签名"""
        if enabled is False:
            return
        if depth is not None:
            indent = max(0, int(depth))
        if self._on_event is not None:
            self._on_event(text)
            return
        flash("  " * indent + text)

    def wipe(self) -> None:
        """原 wipe_line：固定区域模式下不做事，免得擦掉别人的输出"""
        return


def parse_percent(text: str) -> int | None:
    m = _PCT_RE.search(text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- Dashboard


@contextmanager
def dashboard(enabled: bool = True, refresh: float = 0.25):
    """进入「固定区域」模式，并接管 zhs 视频模块的输出

    - tty + 支持 ANSI → 每 `refresh` 秒擦掉重画一块，高度固定
    - 非 tty（重定向）→ 有实质进展才打一行
    """
    global _ACTIVE
    import zhs.zhidao.video as zvideo

    sink = Sink(enabled)
    saved_video_print = getattr(zvideo, "print", None)
    saved_tree = getattr(zvideo, "tree_print", None)
    saved_wipe = getattr(zvideo, "wipe_line", None)

    stop = threading.Event()
    renderer: threading.Thread | None = None
    ticker: threading.Thread | None = None
    screen: _Screen | None = None

    try:
        is_tty = bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        is_tty = False
    vt = (is_tty or os.environ.get("ZHS_MULTI_DASH") == "1") and enabled and _enable_vt()

    # 接管
    # 注意：zhs 的 _print_with_progress / _main_loop 里刷进度条用的是**内置 print**
    # （`print(f"\r{line}", end="")`），不是 zhs.zhidao.video.print。
    # 只替换模块属性时那条进度条照样直接写到终端，把上面画的固定区域挤掉——
    # 表现就是"只有最下面一条进度条在动，别的都看不见"。
    # 所以这里必须临时接管 builtins.print。
    import builtins

    saved_builtin_print = builtins.print
    builtins.print = sink  # type: ignore[assignment]
    zvideo.print = sink  # type: ignore[attr-defined]
    zvideo.tree_print = sink.event  # type: ignore[attr-defined]
    zvideo.wipe_line = sink.wipe  # type: ignore[attr-defined]

    if vt:
        _ACTIVE = True
        screen = _Screen(True)
        renderer = threading.Thread(
            target=_render_loop, args=(stop, screen, max(0.1, float(refresh))),
            name="screen", daemon=True,
        )
        renderer.start()
    elif enabled:
        _ACTIVE = True
        try:
            every = float(os.environ.get("ZHS_MULTI_PROGRESS_EVERY", "3") or 3)
        except ValueError:
            every = 3.0
        ticker = threading.Thread(
            target=_ticker_loop, args=(stop, max(1.0, every)), name="progress", daemon=True,
        )
        ticker.start()

    try:
        yield sink
    finally:
        stop.set()
        if renderer is not None:
            renderer.join(timeout=3)
        if ticker is not None:
            ticker.join(timeout=3)
        _ACTIVE = False
        with _LOCK:
            pending = list(_RECENT)
            _RECENT.clear()
            _STATUS.clear()
        if pending and renderer is None:
            for text in pending[-6:]:
                _emit(text)

        # 先把内置 print 还回去，否则后面所有输出都会进 Sink
        try:
            builtins.print = saved_builtin_print  # type: ignore[assignment]
        except Exception:  # noqa: BLE001,S110
            pass

        # 还原（避免影响后续顺序执行）
        # 原来不存在的属性要删掉，否则会把我们的 Sink 永久留在模块上
        def _restore(name: str, saved: Any) -> None:
            if saved is None:
                if hasattr(zvideo, name):
                    try:
                        delattr(zvideo, name)
                    except AttributeError:
                        pass
            else:
                setattr(zvideo, name, saved)

        _restore("print", saved_video_print)
        _restore("tree_print", saved_tree)
        _restore("wipe_line", saved_wipe)
