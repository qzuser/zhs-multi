"""线程安全的控制台输出

多线程刷课时多个 worker 会同时打印进度条，直接 print 会互相覆盖。
本模块提供：
- Sink：接管 zhs 视频播放器模块里的 print / tree_print / wipe_line
- Dashboard：后台刷新一行总览状态，事件行插入到状态行上方
- say()：带锁的普通输出
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any

_LOCK = threading.RLock()
_EVENTS: deque[str] = deque()
_STATUS: dict[str, dict[str, Any]] = {}
_ACTIVE = False

_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
# 播放器输出的进度行形如：playing 12345 [#.......] 45.2%
# 随机暂停时是：pause  [#.......] 33%  （这里的百分比是暂停倒计时，不是视频进度）
_BAR_RE = re.compile(r"\[[#.\s]+\]\s*(\d{1,3}(?:\.\d+)?)%")

# ---------------------------------------------------------------- 基础输出

# 打包成 exe 之后提示语里的 "zhs-multi.bat" 就不对了，统一在这里换掉
try:
    from .paths import frozen as _frozen

    _LAUNCH_HINT = "zhs-multi.exe" if _frozen() else "zhs-multi.bat"
except Exception:  # noqa: BLE001
    _LAUNCH_HINT = "zhs-multi.bat"


def _hint(text: str) -> str:
    if _LAUNCH_HINT != "zhs-multi.bat" and "zhs-multi.bat" in text:
        return text.replace("zhs-multi.bat", _LAUNCH_HINT)
    return text


def _emit(text: str) -> None:
    text = _hint(text)
    with _LOCK:
        if _ACTIVE:
            _EVENTS.append(text)
        else:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()


def say(text: str = "") -> None:
    """普通一行输出（带锁，dashboard 开启时插到状态行上方）"""
    _emit(text)


def info(text: str) -> None:
    _emit(f"  {text}")


def ok(text: str) -> None:
    _emit(f"  [OK] {text}")


def warn(text: str) -> None:
    _emit(f"  [!!] {text}")


def err(text: str) -> None:
    _emit(f"  [ERR] {text}")


def rule(char: str = "-", width: int = 66) -> None:
    _emit(char * width)


# ---------------------------------------------------------------- 工作线程状态


def set_status(worker: str, **fields: Any) -> None:
    with _LOCK:
        slot = _STATUS.setdefault(worker, {})
        slot.update(fields)


def clear_status(worker: str) -> None:
    with _LOCK:
        _STATUS.pop(worker, None)


# ---------------------------------------------------------------- 总进度
#
# 一条总进度：刷了几条 / 一共几条 / 百分比 / 已用多久 / 按当前速度还要多久。
# tty 下贴在状态行最前面，非 tty（重定向、别的程序调）改成每 30 秒打一行。

_PROGRESS: dict[str, Any] = {
    "total": 0,
    "ok": 0,
    "skip": 0,
    "fail": 0,
    "stopped": 0,
    "started": 0.0,
    "label": "",
    "durations": [],      # 最近若干条的耗时，用来估剩余时间
    "risks": 0,           # 撞到风控（-10 / -12）的次数
    "jobs": 1,            # 几路并发，估剩余时间时要用
}


def progress_reset(total: int, label: str = "", jobs: int = 1) -> None:
    with _LOCK:
        _PROGRESS.update(
            total=int(total), ok=0, skip=0, fail=0, stopped=0,
            started=time.time(), label=label, durations=[], risks=0,
            jobs=max(1, int(jobs)),
        )


def progress_count(status: str, seconds: float = 0.0) -> None:
    """记一条任务结果：ok / skip / fail / stopped"""
    with _LOCK:
        if status in _PROGRESS:
            _PROGRESS[status] += 1
        if status == "ok" and seconds > 0:
            _PROGRESS["durations"].append(float(seconds))
            del _PROGRESS["durations"][:-20]     # 只留最近 20 条


def progress_risk(kind: str = "") -> int:
    """风控计数 +1，返回累计次数（runner 用它决定要不要降速）"""
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
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def progress_line() -> str:
    """总进度那一行；没有总任务时返回空串"""
    with _LOCK:
        p = dict(_PROGRESS)
        p["durations"] = list(_PROGRESS["durations"])

    total = int(p["total"])
    if total <= 0:
        return ""
    done = int(p["ok"]) + int(p["skip"])
    pct = done * 100.0 / total
    bar_len = 12
    filled = max(0, min(bar_len, int(bar_len * done / total + 0.0001)))
    bar = "#" * filled + "." * (bar_len - filled)

    elapsed = time.time() - (p["started"] or time.time())
    left = max(0, total - done)
    # 估剩余时间：已经刷过几条就用「实测吞吐」推，一条没有就按单条耗时摊并发
    if left <= 0:
        eta = 0.0
    elif done:
        eta = elapsed / done * left
    elif p["durations"]:
        eta = sum(p["durations"]) / len(p["durations"]) * left / int(p["jobs"] or 1)
    else:
        eta = 0.0

    bits = [f"总进度 [{bar}] {done}/{total} {pct:5.1f}%",
            f"已用 {_hms(elapsed)}"]
    if left:
        bits.append(f"剩 ~{_hms(eta)}")
    else:
        bits.append("全部完成")
    if p["fail"]:
        bits.append(f"失败 {p['fail']}")
    if p["stopped"]:
        bits.append(f"中止 {p['stopped']}")
    if p["risks"]:
        bits.append(f"风控 {p['risks']} 次")
    return "  ".join(bits)


def _render_status() -> str:
    """单行总览：左边总进度，右边每线程的当前进度（超宽就砍线程那段）"""
    parts: list[str] = []
    for worker in sorted(_STATUS):
        slot = _STATUS[worker]
        pct = slot.get("pct")
        label = slot.get("label", "")
        state = slot.get("state", "")
        if pct is None:
            parts.append(f"{worker}:{state or 'idle'}")
        else:
            value = float(pct)
            bar_len = 8
            filled = max(0, min(bar_len, int(bar_len * value / 100)))
            bar = "#" * filled + "." * (bar_len - filled)
            parts.append(f"{worker}:{state}[{bar}]{value:>5.1f}% {label}")
    head = progress_line()
    body = " | ".join(parts)
    if not head:
        return body
    if not body:
        return head
    width = 0
    try:
        width = _term_width()
    except Exception:  # noqa: BLE001
        width = 0
    line = head + " | " + body
    if width and len(line) > width - 1:
        room = width - 1 - len(head) - 3
        if room > 12:
            line = head + " | " + body[:room]
        else:
            line = head
    return line


def _term_width() -> int:
    import shutil

    return shutil.get_terminal_size((120, 24)).columns


# ---------------------------------------------------------------- Sink


class Sink:
    """替代 zhs 视频模块里的 print / tree_print / wipe_line

    - 进度行（含百分比）只更新 worker 状态，不刷屏
    - 其它输出（跳过/完成/报错）作为事件行输出
    """

    def __init__(self, enabled: bool = True, on_event: Any = None) -> None:
        # enabled 仅为兼容旧调用保留：接管期间一律输出事件行
        self._enabled = enabled
        self._on_event = on_event

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        text = " ".join(str(a) for a in args)
        text = text.strip()
        if not text:
            return
        worker = threading.current_thread().name
        # 任何带进度条的刷新行都只更新状态，不刷屏（playing / pause / 其它）
        m = _BAR_RE.search(text)
        if m:
            head = text.split()[0].lower()
            state = "pause" if head.startswith("pause") else "play "
            set_status(worker, pct=float(m.group(1)), state=state)
            return
        if "\r" in text and _PCT_RE.search(text):
            return
        self.event(
            text,
            depth=kwargs.get("depth"),
            enabled=kwargs.get("enabled", True),
        )

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
        _emit("  " * indent + text)

    def wipe(self) -> None:
        """原 wipe_line：dashboard 模式下不做事，避免清掉别人的输出"""
        return


def parse_percent(text: str) -> int | None:
    m = _PCT_RE.search(text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- Dashboard


def _render_loop(stop: threading.Event) -> None:
    last_len = 0
    while not stop.is_set():
        with _LOCK:
            lines = list(_EVENTS)
            _EVENTS.clear()
            status = _render_status()
        out: list[str] = []
        for line in lines:
            out.append("\r" + " " * last_len + "\r" + line)
        if status:
            out.append("\r" + " " * last_len + "\r" + status[: last_len + 200])
            last_len = len(status)
        if out:
            sys.stdout.write("\n".join(out))
            sys.stdout.flush()
        time.sleep(0.35)
    with _LOCK:
        lines = list(_EVENTS)
        _EVENTS.clear()
    for line in lines:
        sys.stdout.write("\r" + " " * (last_len + 1) + "\r" + line + "\n")
    if last_len:
        sys.stdout.write("\r" + " " * (last_len + 1) + "\r")
    sys.stdout.flush()


def _ticker_loop(stop: threading.Event, interval: float) -> None:
    """非 tty（重定向 / 被别的程序调）时，每隔一会儿打一行总进度"""
    while not stop.wait(interval):
        line = progress_line()
        if line:
            _emit(line)


@contextmanager
def dashboard(enabled: bool = True):
    """开启/关闭总览状态行，并接管 zhs 视频模块的输出"""
    global _ACTIVE
    import zhs.zhidao.video as zvideo

    sink = Sink(enabled)
    saved_video_print = getattr(zvideo, "print", None)
    saved_tree = getattr(zvideo, "tree_print", None)
    saved_wipe = getattr(zvideo, "wipe_line", None)

    use_dash = enabled and (sys.stdout.isatty() or os.environ.get("ZHS_MULTI_DASH") == "1")
    stop = threading.Event()
    renderer: threading.Thread | None = None
    ticker: threading.Thread | None = None

    # 接管
    zvideo.print = sink  # type: ignore[attr-defined]
    zvideo.tree_print = sink.event  # type: ignore[attr-defined]
    zvideo.wipe_line = sink.wipe  # type: ignore[attr-defined]

    if use_dash:
        _ACTIVE = True
        renderer = threading.Thread(target=_render_loop, args=(stop,), name="dash", daemon=True)
        renderer.start()
    elif enabled:
        # 没法原地刷新就定时打一行，照样看得见进度
        try:
            every = float(os.environ.get("ZHS_MULTI_PROGRESS_EVERY", "30") or 30)
        except ValueError:
            every = 30.0
        ticker = threading.Thread(
            target=_ticker_loop, args=(stop, max(5.0, every)), name="progress", daemon=True
        )
        ticker.start()

    try:
        yield sink
    finally:
        stop.set()
        if renderer is not None:
            renderer.join(timeout=2)
        if ticker is not None:
            ticker.join(timeout=2)
            line = progress_line()
            if line:
                say(line)
        _ACTIVE = False
        with _LOCK:
            _STATUS.clear()
            _EVENTS.clear()
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
