"""风控应对：把「像不像人」这件事做成旋钮

平台判脚本，看的不是你在不在刷，而是**节奏**：

| 现象 | 平台给的码 | 这里怎么对付 |
|---|---|---|
| 同一门课同时开好几个窗口 | `-10` 疑是多窗口观看 | 自适应并发：一撞就降到 1 路，冷却后再放 |
| 心跳间隔精确得像定时器 | 统计上异常 | 每次上报前加 0.85~1.3 倍抖动（`delay`） |
| 一口气刷十几个小时 | `-9` 学习时长上限 | 每 N 条强制休息；跨天分散；可选每日上限 |
| 换视频 0 秒切过去 | 行为不像人 | 每条之间 `delay` + 每 N 条长休息 |
| 凌晨三点还在上课 | 行为不像人 | `night_off`：0~6 点不刷 |
| 播放页要素验证码 | `-12` | 触发一次就降速，别硬顶 |

撞到 `-10` 之后 zhs 自己会刷新 token 重试一次；这里做的是**它试完之后**的事——
如果这轮又撞了，说明并发对平台来说太明显，直接把各路线程压回 1，并加长间隔。
"""

from __future__ import annotations

import random
import threading
import time
from contextlib import contextmanager
from typing import Any

# ---------------------------------------------------------------- 全局单例
#
# loguru 的 watcher 是模块级回调，拿不到 run_tasks 里的局部对象，
# 所以当前生效的 Governor 挂在这里。

CURRENT: "Governor | None" = None

# loguru 日志里出现这些就说明撞风控了（zhs 内部的措辞）
_MARKERS = {
    "Multi-window detected": "multi_window",      # -10
    "Study time decreased": "time_decreased",     # -8
    "CaptchaRequired": "captcha",                 # -12
    "TimeLimitExceeded": "time_limit",            # -9
}


def set_current(gov: "Governor | None") -> None:
    global CURRENT
    CURRENT = gov


def on_log(text: str) -> None:
    """loguru sink：从日志里认出风控信号，交给当前 Governor"""
    for marker, kind in _MARKERS.items():
        if marker in text:
            gov = CURRENT
            if gov is not None:
                gov.note_risk(kind)
            break


# ---------------------------------------------------------------- Governor


class Governor:
    """控制同时有几路在播，以及两次上报之间歇多久"""

    def __init__(
        self,
        jobs: int,
        adaptive: bool = True,
        base_delay: float = 0.4,
        min_delay: float = 0.3,
        max_delay: float = 8.0,
        cool_down: float = 180.0,
    ) -> None:
        self.base_jobs = max(1, int(jobs))
        self.limit = self.base_jobs
        self.adaptive = adaptive
        self.base_delay = max(0.0, float(base_delay))
        self.min_delay = max(0.0, float(min_delay))
        self.max_delay = max(self.base_delay, float(max_delay))
        self.cool_down = float(cool_down)

        self.delay = self.base_delay
        self.active = 0
        self.risks = 0
        self.oks = 0
        self._last_risk = 0.0
        self._cond = threading.Condition()

    # ------------------------------------------------------------ 并发闸门
    @contextmanager
    def slot(self):
        """占一路并发；撞过 -10 之后这里会把多余的线程挡在门外"""
        with self._cond:
            while self.active >= self.limit:
                self._cond.wait(timeout=5.0)
            self.active += 1
        try:
            yield
        finally:
            with self._cond:
                self.active -= 1
                self._cond.notify_all()

    # ------------------------------------------------------------ 风控反馈
    def note_risk(self, kind: str = "") -> None:
        """撞一次风控：压并发 + 拉长间隔"""
        with self._cond:
            self.risks += 1
            self._last_risk = time.time()
            if not self.adaptive:
                self._cond.notify_all()
                return
            if kind in ("multi_window", "captcha"):
                if self.limit > 1:
                    self.limit -= 1
                self.delay = min(self.max_delay, max(self.base_delay, self.delay) * 1.6 + 0.5)
            elif kind == "time_decreased":
                # 服务端时间比我们高：多半是别处也在刷，慢一点最稳
                self.delay = min(self.max_delay, self.delay * 1.3 + 0.3)
            self._cond.notify_all()

    def note_ok(self) -> None:
        """刷完一条：冷却期过了、风控没再出现，就一点点把并发放回去"""
        with self._cond:
            self.oks += 1
            if not self.adaptive:
                return
            idle = time.time() - self._last_risk
            if (
                self.limit < self.base_jobs
                and self.oks % 5 == 0
                and (self._last_risk == 0.0 or idle >= self.cool_down)
            ):
                self.limit += 1
                self.delay = max(self.min_delay, self.delay * 0.8)
            self._cond.notify_all()

    # ------------------------------------------------------------ 上报间隔
    def sleep_between(self) -> None:
        """两条任务之间歇一下；间隔带抖动，不是固定值"""
        wait = max(0.0, self.delay) * random.uniform(0.85, 1.30)
        if wait:
            time.sleep(wait)

    def describe(self) -> str:
        parts = [f"并发 {self.limit}/{self.base_jobs}", f"间隔 {self.delay:.1f}s"]
        if self.risks:
            parts.append(f"风控 {self.risks} 次")
        return "、".join(parts)


# ---------------------------------------------------------------- 休息 / 作息


def parse_range(text: str, default: tuple[float, float] = (60.0, 180.0)) -> tuple[float, float]:
    """"60-180" / "90" -> (lo, hi)"""
    raw = str(text or "").strip()
    if not raw:
        return default
    try:
        if "-" in raw:
            lo, hi = raw.split("-", 1)
            return (max(0.0, float(lo)), max(0.0, float(hi)))
        one = float(raw)
        return (max(0.0, one), max(0.0, one))
    except ValueError:
        return default


def rest_seconds(text: str) -> float:
    lo, hi = parse_range(text)
    return random.uniform(min(lo, hi), max(lo, hi))


def night_blocked(hour: int, start: int = 0, end: int = 6) -> bool:
    """现在是"人应该睡觉"的时段吗（跨零点也支持）"""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# ---------------------------------------------------------------- 每日上限


class DailyQuota:
    """state/daily.json 记今天刷了几条，防止一天顶到 -9"""

    def __init__(self, path: Any, limit: int = 0) -> None:
        self.path = path
        self.limit = max(0, int(limit))
        self.count = 0
        self.load()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def load(self) -> None:
        import json

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        if data.get("date") == self._today():
            self.count = int(data.get("count") or 0)

    def save(self) -> None:
        import json

        try:
            self.path.write_text(
                json.dumps({"date": self._today(), "count": self.count}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001,S110
            pass

    def add(self, n: int = 1) -> None:
        self.count += n
        self.save()

    def exhausted(self) -> bool:
        return bool(self.limit) and self.count >= self.limit

    def describe(self) -> str:
        if not self.limit:
            return "不限"
        return f"今天 {self.count}/{self.limit}"
