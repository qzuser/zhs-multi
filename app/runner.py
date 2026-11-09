"""多线程刷课调度器

设计要点：
- 任务粒度 = 单个视频（知到），这样才能真正并发吃满线程
- 每个 worker 独占一个 ZhsSession 和一份 ctx 深拷贝（ctx 里有可变的 fucked_time）
- 共享的是只读的任务模型 + 统计；所有输出经 console 走锁
- 风控相关：CaptchaRequired 一出现就全局停（继续刷只会更糟）；每任务间有随机间隔
"""

from __future__ import annotations

import queue
import random
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import stealth
from .config import AppSettings
from .console import (
    clear_status,
    dashboard,
    err,
    info,
    ok,
    progress_count,
    progress_reset,
    progress_risk,
    rule,
    say,
    set_status,
    warn,
)
from .courses import VideoTask
from .session_factory import build_zhs_config, new_session


@dataclass
class TaskOutcome:
    task: VideoTask
    status: str  # ok / skip / fail / stopped
    detail: str = ""
    seconds: float = 0.0


@dataclass
class RunSummary:
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: int = 0
    seconds: float = 0.0
    stop_reason: str = ""
    outcomes: list[TaskOutcome] = field(default_factory=list)

    def counts(self) -> Counter[str]:
        return Counter(o.status for o in self.outcomes)


class Worker:
    """一个工作线程：懒加载自己的 session / player / ctx"""

    def __init__(
        self,
        settings: AppSettings,
        cookies: list[dict[str, Any]],
        shared_ctx: dict[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        self._cookies = cookies
        self._session: Any = None
        self._player: Any = None
        self._ai_mgr: Any = None
        self._ai_player: Any = None
        self._shared_ctx = shared_ctx or {}
        self._ctx_cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._session is not None:
            return
        from zhs.zhidao.video import ZhidaoVideoPlayer

        self._session = new_session(self._settings, self._cookies)
        self._player = ZhidaoVideoPlayer(
            self._session,
            speed=self._settings.speed,
            end_threshold=self._settings.threshold,
            time_limit=0,
        )
    def _ctx(self, rac_id: str) -> Any:
        from zhs.zhidao.course import ZhidaoCourseManager

        with self._lock:
            cached = self._ctx_cache.get(rac_id)
            if cached is not None:
                return cached
        # 优先复用主线程预取的 ctx，省掉每个线程重复拉章节树
        shared = self._shared_ctx.get(rac_id)
        source = shared if shared is not None else ZhidaoCourseManager(self._session).get_context(rac_id)
        local = source.model_copy(deep=True)
        with self._lock:
            self._ctx_cache[rac_id] = local
        return local

    def play(self, task: Any) -> None:
        if getattr(task, "kind", "zhidao") == "ai":
            self._ensure_ai()
            self._play_ai(task)
            return
        self._ensure()
        ctx = self._ctx(task.rac_id)
        self._player.play_video(task.rac_id, task.video_id, ctx)

    # ---- AI 智慧课程 ----

    def _ensure_ai(self) -> None:
        self._ensure()
        if self._ai_mgr is not None:
            return
        from zhs.ai.course import AiCourseManager
        from zhs.ai.video import AiVideoPlayer

        self._ai_mgr = AiCourseManager(self._session)
        self._ai_player = AiVideoPlayer(self._session, speed=self._settings.speed)

    def _play_ai(self, task: Any) -> None:
        """刷一个知识点下的所有「学习资料」：文本/PPT 直接标记完成，视频照常播"""
        from .console import set_status
        from .courses import AI_RES_VIDEO, classify_ai_resource

        resources = self._ai_mgr.list_knowledge_resources(
            task.course_id, task.class_id, task.knowledge_id
        )
        todo = [r for r in resources if r.study_status != 1]
        if not self._settings.ai_learn_optional:
            todo = [r for r in todo if r.resources_detail.resources_sync_type == 1]
        task.materials = len(todo)
        if not todo:
            return

        total = len(todo)
        slot = threading.current_thread().name.rsplit("_", 1)[-1]
        tag = f"W{int(slot) + 1}" if slot.isdigit() else "W0"
        for i, resource in enumerate(todo):
            detail = resource.resources_detail
            kind = classify_ai_resource(detail)
            set_status(tag, label=f"{task.name[:8]}·{kind}", state="mat  ", pct=i / total * 100)
            if kind == AI_RES_VIDEO and detail.resources_file_id:
                self._ai_player.play_video(
                    task.course_id,
                    task.class_id,
                    detail.resources_file_id,
                    task.knowledge_id,
                    schedule=resource.schedule,
                    threshold=self._settings.ai_video_threshold,
                )
            else:
                self._ai_mgr.complete_resource(
                    task.course_id, task.class_id, task.knowledge_id, detail.resources_uid
                )
            if self._settings.delay:
                time.sleep(min(self._settings.delay, 1.5))


def _worker_name(raw: str) -> str:
    """ThreadPoolExecutor 的线程名 -> 短标签"""
    tail = raw.rsplit("_", 1)[-1]
    return f"W{int(tail) + 1}" if tail.isdigit() else raw


def run_tasks(
    settings: AppSettings,
    cookies: list[dict[str, Any]],
    tasks: list[VideoTask],
    jobs: int,
    ctx_cache: dict[str, Any] | None = None,
    on_task: Any = None,
) -> RunSummary:
    """按 jobs 并发执行视频任务"""
    summary = RunSummary(total=len(tasks))
    if not tasks:
        return summary

    # 风控闸门：并发上限 + 上报间隔，撞到 -10 会自己往下压
    gov = stealth.Governor(
        jobs,
        adaptive=settings.adaptive,
        base_delay=settings.delay,
        min_delay=min(0.3, settings.delay) if settings.delay else 0.0,
    )
    stealth.set_current(gov)
    quota = stealth.DailyQuota(settings.state_dir / "daily.json", settings.max_per_day)
    progress_reset(len(tasks), jobs=jobs)

    task_q: queue.Queue[VideoTask | None] = queue.Queue()
    for t in tasks:
        task_q.put(t)

    stop_event = threading.Event()
    deadline = (time.time() + settings.limit * 60) if settings.limit else 0.0
    results: list[TaskOutcome] = []
    res_lock = threading.Lock()
    stop_reason = {"text": ""}
    rest_lock = threading.Lock()
    rest_done = 0

    def record(outcome: TaskOutcome) -> None:
        with res_lock:
            results.append(outcome)
        progress_count(outcome.status, outcome.seconds)
        if on_task is not None:
            try:
                on_task(outcome)
            except Exception:  # noqa: BLE001,S110
                pass

    def worker_loop(worker_no: int) -> None:
        nonlocal rest_done
        name = f"W{worker_no}"
        w = Worker(settings, cookies, ctx_cache)
        # 错峰启动：几个线程同时开播、同时上报心跳，最容易被判成"多窗口观看"（-10/-12）
        if settings.stagger and worker_no > 1:
            time.sleep(settings.stagger * (worker_no - 1))
        while not stop_event.is_set():
            if deadline and time.time() > deadline:
                if not stop_reason["text"]:
                    stop_reason["text"] = f"达到时间上限（{settings.limit} 分钟）"
                stop_event.set()
                break
            try:
                task = task_q.get_nowait()
            except queue.Empty:
                return
            if task is None:
                return

            if quota.exhausted():
                if not stop_reason["text"]:
                    stop_reason["text"] = f"到了每日上限（{settings.max_per_day} 条），明天再说"
                stop_event.set()
                break

            if task.done and not settings.force:
                set_status(name, label=task.name[:16], state="skip ", pct=100)
                record(TaskOutcome(task, "skip", "已完成"))
                clear_status(name)
                continue

            set_status(name, label=task.name[:16], state="play ", pct=0)
            started = time.time()
            try:
                with gov.slot():
                    w.play(task)
                elapsed = time.time() - started
                record(TaskOutcome(task, "ok", "", elapsed))
                gov.note_ok()
                quota.add()
                set_status(name, state="done ", pct=100)
                clear_status(name)
            except Exception as exc:  # noqa: BLE001
                elapsed = time.time() - started
                kind = type(exc).__name__
                msg = str(exc)[:160]
                clear_status(name)
                if kind == "CaptchaRequired":
                    url = (
                        "https://studyh5.zhihuishu.com/videoStudy.html#/studyVideo"
                        f"?recruitAndCourseId={task.rac_id}"
                    )
                    stop_reason["text"] = (
                        "平台要求图形验证码（-12）。先自动点掉它：zhs-multi.bat captcha\n"
                        "    点不掉再人工：到此链接看一小段视频、把弹出的验证码点掉：\n"
                        f"    {url}\n"
                        "    或者一条命令全自动：zhs-multi.bat unblock -c 1 --auto --then-run -j 2"
                    )
                    record(TaskOutcome(task, "stopped", msg, elapsed))
                    stop_event.set()
                    break
                if kind == "TimeLimitExceeded":
                    stop_reason["text"] = "平台学习时长上限（-9）"
                    record(TaskOutcome(task, "stopped", msg, elapsed))
                    stop_event.set()
                    break
                if "-10" in msg or "多窗口" in msg:
                    # 平台说"疑是多窗口观看"：别硬顶，压并发、拉长间隔
                    progress_risk()
                    gov.note_risk("multi_window")
                    warn(f"{name} 被判多窗口观看（-10）→ 已降到 {gov.describe()}")
                record(TaskOutcome(task, "fail", f"{kind}: {msg}", elapsed))
                warn(f"{name} 失败：{task.name} — {kind}: {msg}")
                continue
            finally:
                # 两次之间歇一下，间隔带抖动（固定节奏最容易被认成脚本）
                gov.sleep_between()
                with rest_lock:
                    rest_done += 1
                    do_rest = bool(
                        settings.rest_every
                        and rest_done >= settings.rest_every
                        and not stop_event.is_set()
                        and not task_q.empty()
                    )
                    if do_rest:
                        rest_done = 0
                if do_rest:
                    secs = stealth.rest_seconds(settings.rest_seconds)
                    info(f"休息 {secs:.0f} 秒再继续（每 {settings.rest_every} 条一次，别太机械）")
                    stop_event.wait(secs)

    started_at = time.time()
    say()
    info(f"并发线程数：{jobs}｜任务数：{len(tasks)}｜倍速：{settings.speed}｜阈值：{settings.threshold}")
    info(f"风控：{gov.describe()}" + (f"｜每 {settings.rest_every} 条休息 {settings.rest_seconds} 秒" if settings.rest_every else ""))
    rule()

    threads = [
        threading.Thread(target=worker_loop, args=(i + 1,), name=f"pool_{i}", daemon=True)
        for i in range(jobs)
    ]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop_reason["text"] = "用户中断（Ctrl+C）"
        stop_event.set()
    for t in threads:
        t.join(timeout=5)

    # 失败的单线程补跑一次（网络抖动、临时 5xx、抢不到令牌）
    retry_tasks = [o.task for o in results if o.status == "fail"]
    if retry_tasks and not stop_event.is_set():
        say()
        info(f"补跑 {len(retry_tasks)} 个失败任务（单线程，慢一点但稳）")
        rw = Worker(settings, cookies, ctx_cache)
        for task in retry_tasks:
            if stop_event.is_set():
                break
            set_status("W1", label=task.name[:16], state="retry", pct=0)
            started = time.time()
            try:
                rw.play(task)
                record(TaskOutcome(task, "ok", "补跑成功", time.time() - started))
                ok(f"补跑成功：{task.name}")
            except Exception as exc:  # noqa: BLE001
                kind = type(exc).__name__
                msg = str(exc)[:140]
                record(TaskOutcome(task, "fail", f"{kind}: {msg}", time.time() - started))
                warn(f"补跑仍失败：{task.name} — {kind}: {msg}")
                if kind in ("CaptchaRequired", "TimeLimitExceeded"):
                    stop_reason["text"] = (
                        "平台要求验证码（-12）" if kind == "CaptchaRequired" else "学习时长上限（-9）"
                    )
                    stop_event.set()
                    break
            finally:
                clear_status("W1")
                time.sleep(max(settings.delay, 2.0))

    # 同一视频只保留最后一次结果（补跑会覆盖原来的 fail）
    final: dict[tuple[str, int], TaskOutcome] = {}
    for o in results:
        final[(o.task.rac_id, o.task.video_id)] = o

    summary.seconds = time.time() - started_at
    summary.outcomes = list(final.values())
    summary.stop_reason = stop_reason["text"]
    counts = summary.counts()
    summary.done = counts.get("ok", 0)
    summary.skipped = counts.get("skip", 0)
    summary.failed = counts.get("fail", 0)
    summary.stopped = counts.get("stopped", 0)
    return summary


def run_courses(
    settings: AppSettings,
    cookies: list[dict[str, Any]],
    selected: list[Any],
    jobs: int | None = None,
    dry_run: bool = False,
    with_homework: bool = False,
    auto_unblock: bool = False,
    on_task: Any = None,
) -> int:
    """选课 → 展开任务 → 并发执行（→ 可选作业）"""
    from .courses import collect_ai_tasks, collect_video_tasks

    jobs = jobs or settings.jobs
    session = new_session(settings, cookies)

    all_tasks: list[Any] = []
    ctx_cache: dict[str, Any] = {}
    polymas_entries: list[Any] = []
    for entry in selected:
        if getattr(entry, "kind", "") == "polymas":
            polymas_entries.append(entry)
            continue
        if entry.kind == "ai":
            info(f"展开智慧课程：{entry.label} …")
            try:
                ai_tasks = collect_ai_tasks(entry, session)
            except Exception as exc:  # noqa: BLE001
                err(f"读取知识点失败：{entry.label} — {exc}")
                continue
            info(
                f"  {entry.label}：{len(ai_tasks)} 个知识点，"
                f"已完成 {sum(1 for t in ai_tasks if t.done)} 个"
                f"（每个知识点内的文本/PPT/视频资料会逐条刷）"
            )
            all_tasks.extend(ai_tasks)
            continue
        if entry.kind != "zhidao":
            warn(f"「{entry.label}」暂不支持并发播放，可用原 zhs play -c 处理")
            continue
        info(f"展开课程：{entry.label} …")
        try:
            from zhs.zhidao.course import ZhidaoCourseManager

            ctx = ZhidaoCourseManager(session).get_context(entry.rac_id)
            ctx_cache[entry.rac_id] = ctx
            tasks = collect_video_tasks(entry, session, ctx)
        except Exception as exc:
            err(f"读取章节失败：{entry.label} — {exc}")
            continue
        info(f"  {entry.label}：{len(tasks)} 个视频，已完成 {sum(1 for t in tasks if t.done)} 个")
        all_tasks.extend(tasks)

    if not all_tasks and not polymas_entries:
        err("没有可执行的视频任务")
        return 2

    pending = [t for t in all_tasks if not t.done or settings.force]
    if all_tasks:
        say()
        info(
            f"任务总数 {len(all_tasks)}，待处理 {len(pending)}，"
            f"已完成 {len(all_tasks) - len(pending)}"
        )
        if len(pending) <= 12:
            for t in pending[:12]:
                if getattr(t, "kind", "zhidao") == "ai":
                    say(
                        f"    · {t.course_label} / {t.chapter} / {t.name} "
                        f"(knowledgeId={t.knowledge_id}，进度 {t.progress}%)"
                    )
                else:
                    say(
                        f"    · {t.course_label} / {t.chapter} / {t.lesson} / "
                        f"{t.name} (videoId={t.video_id})"
                    )

    if dry_run:
        if polymas_entries:
            from .polymas import run_entries as _poly_entries

            _poly_entries(
                settings,
                cookies,
                polymas_entries,
                uuid=str(getattr(session, "uuid", "") or ""),
                dry_run=True,
            )
        say()
        ok("dry-run：只列任务，不请求平台，不改变任何进度")
        return 0

    if polymas_entries:
        from .polymas import run_entries as _poly_entries

        say()
        info("开始处理 AI 智课（知到 App「学习资料」那一类）…")
        poly_stats = _poly_entries(
            settings,
            cookies,
            polymas_entries,
            uuid=str(getattr(session, "uuid", "") or ""),
            dry_run=False,
            force=bool(settings.force),
        )
    else:
        poly_stats = {}

    summary: RunSummary | None = None
    if not all_tasks:
        ok("AI 智课已处理完")
    elif not pending:
        ok("全部任务已完成，无需处理")
    else:
        with dashboard(settings.dashboard):
            summary = run_tasks(settings, cookies, pending, jobs, ctx_cache, on_task=on_task)
        _print_summary(summary)
        try:
            path = save_run_report(settings, summary)
            info(f"本轮报告：{path}")
        except Exception as exc:  # noqa: BLE001
            warn(f"写报告失败：{exc}")

        # 撞到验证码且开了自动解锁：等用户在网页点掉，然后重跑剩下的
        if auto_unblock and summary.stopped and "验证码" in summary.stop_reason:
            stopped = next(
                (o.task for o in summary.outcomes if o.status == "stopped"), pending[0]
            )
            if getattr(stopped, "kind", "zhidao") != "zhidao":
                warn("智慧课程不走知到网页播放器，没法自动解锁验证码")
                return 3
            cleared = wait_unblock(
                settings,
                cookies,
                stopped.rac_id,
                stopped.video_id,
                wait=settings.unblock_wait,
            )
            if cleared:
                info("重新开始处理剩余任务")
                return run_courses(
                    settings,
                    cookies,
                    selected,
                    jobs=jobs,
                    with_homework=with_homework,
                    auto_unblock=False,
                )
            err("验证码没解除，本轮结束")
            return 3

    if with_homework:
        _run_homework(settings, session, selected)

    # 被平台拦停（验证码 / 时长上限）时给个非 0 退出码，方便脚本判断
    if summary is not None and summary.stopped and (
        "验证码" in summary.stop_reason or "上限" in summary.stop_reason
    ):
        return 3
    if poly_stats.get("part") or poly_stats.get("fail"):
        return 3
    return 0


def save_run_report(settings: AppSettings, summary: RunSummary) -> Path:
    """把本轮结果写到 state/last-run.json（重跑前看进度、排查失败都方便）"""
    import json as _json

    data = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(summary.seconds, 1),
        "stop_reason": summary.stop_reason,
        "counts": {
            "total": summary.total,
            "ok": summary.done,
            "skip": summary.skipped,
            "fail": summary.failed,
            "stopped": summary.stopped,
        },
        "tasks": [
            {
                "course": o.task.course_label,
                "video_id": o.task.video_id,
                "name": o.task.name,
                "status": o.status,
                "seconds": round(o.seconds, 1),
                "detail": o.detail,
            }
            for o in summary.outcomes
        ],
    }
    path = settings.state_dir / "last-run.json"
    path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_daemon_state(settings: AppSettings, **fields: Any) -> None:
    """把盯梢进度写到 state/daemon.json，`status` 会读它显示"盯梢中"那一行"""
    import json as _json

    payload = {"time": datetime.now().isoformat(timespec="seconds"), **fields}
    try:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        (settings.state_dir / "daemon.json").write_text(
            _json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _heartbeat(settings: AppSettings, snapshot: dict[str, Any], stop: threading.Event,
               interval: float = 60.0) -> None:
    """刷课期间每 interval 秒更新一次 state/daemon.json，别让 status 以为盯梢已经死了"""
    while not stop.wait(interval):
        write_daemon_state(settings, **{**snapshot, "phase": "running"})


def _print_summary(summary: RunSummary) -> None:
    say()
    rule("=")
    ok(f"完成 {summary.done}｜跳过 {summary.skipped}｜失败 {summary.failed}｜中止 {summary.stopped}")
    info(f"耗时 {summary.seconds:.1f}s")
    if summary.stop_reason:
        warn(f"提前停止：{summary.stop_reason}")
    fails = [o for o in summary.outcomes if o.status == "fail"]
    if fails:
        say()
        warn(f"失败明细（{len(fails)} 条，前 10 条）：")
        for o in fails[:10]:
            say(f"    · {o.task.name} (videoId={o.task.video_id}) → {o.detail}")
    rule("=")


def _run_homework(settings: AppSettings, session: Any, selected: list[Any]) -> None:
    """串行跑作业（作业内部本身是逐题提交，并发没有意义且更危险）"""
    from zhs.cli.services.homework_service import run_zhidao_homework

    say()
    rule("=")
    info("开始处理章节作业（串行）")
    if not settings.has_ai_key:
        warn("未配置 [ai] api_key：作业将退化为随机作答，正确率很低")
    cfg = build_zhs_config(settings)
    for entry in selected:
        if entry.kind != "zhidao" or not entry.recruit_id:
            continue
        info(f"  {entry.label}（recruitId={entry.recruit_id}）")
        try:
            run_zhidao_homework(session, cfg, str(entry.recruit_id), int(entry.course_id or 0))
        except Exception as exc:  # noqa: BLE001
            err(f"作业处理失败：{entry.label} — {exc}")
    rule("=")


def course_url(rac_id: str) -> str:
    return (
        "https://studyh5.zhihuishu.com/videoStudy.html#/studyVideo"
        f"?recruitAndCourseId={rac_id}"
    )


def open_course_page(rac_id: str) -> bool:
    """用默认浏览器打开课程播放页（验证码要在页面里点）"""
    import os
    import webbrowser

    url = course_url(rac_id)
    try:
        if hasattr(os, "startfile"):
            os.startfile(url)  # noqa: S606
        else:
            webbrowser.open(url)
        return True
    except Exception:  # noqa: BLE001
        return False


def collect_tasks(
    settings: AppSettings,
    cookies: list[dict[str, Any]],
    selected: list[Any],
    session: Any = None,
) -> list[Any]:
    """展开所有任务（知到视频 + 智慧课程知识点），读不到就跳过并给出原因"""
    from .courses import collect_ai_tasks, collect_video_tasks

    session = session or new_session(settings, cookies)
    tasks: list[Any] = []
    for entry in selected:
        if entry.kind == "ai":
            try:
                tasks.extend(collect_ai_tasks(entry, session))
            except Exception as exc:  # noqa: BLE001
                warn(f"读取知识点失败：{entry.label} — {exc}")
            continue
        if entry.kind != "zhidao":
            continue
        try:
            tasks.extend(collect_video_tasks(entry, session))
        except Exception as exc:  # noqa: BLE001
            warn(f"读取章节失败：{entry.label} — {exc}")
    return tasks


def run_daemon(
    settings: AppSettings,
    cookies: list[dict[str, Any]],
    selected: list[Any],
    jobs: int = 2,
    every: float = 600,
    max_cycles: int = 0,
    max_hours: float = 0.0,
    probe_fn: Any = None,
    run_fn: Any = None,
    collect_fn: Any = None,
    solve_fn: Any = None,
    heartbeat: float = 60.0,
) -> int:
    """盯梢模式：被验证码拦住就等，解除后自动续刷，直到刷完或到上限

    给了 solve_fn 时，被拦住会先试着自动点掉验证码（易盾点选），点过了立刻续刷。

    返回 0 = 全部完成，2 = 没有任务，3 = 到了 --cycles/--hours 上限还没刷完
    """
    probe_fn = probe_fn or probe
    run_fn = run_fn or run_courses
    if collect_fn is None:

        def collect_fn() -> list[Any]:  # type: ignore[misc]
            return collect_tasks(settings, cookies, selected)

    started = time.time()
    cycle = 0
    solved_times = 0
    auth_fails = 0
    limit_hits = 0
    say()
    info(f"盯梢模式：每 {int(every)} 秒看一次"
         + (f"，最多 {max_cycles} 轮" if max_cycles else "，不限轮数")
         + (f"，最多 {max_hours:g} 小时" if max_hours else ""))

    def note_auth_error(text: str) -> bool:
        """认得出"登录态没了"就把次数数上，连着 3 次就停，别空转"""
        nonlocal auth_fails
        low = text.lower()
        if any(k in text for k in ("未登录", "登录态", "重新登录")) or any(
            k in low for k in ("expired", "unauthorized", "session")
        ):
            auth_fails += 1
            if auth_fails >= 3:
                err("连续 3 次提示登录态失效，盯梢停止——请重新扫码：zhs-multi.bat login")
                write_daemon_state(settings, cycle=cycle, total=0, pending=0,
                                   solved=solved_times, phase="auth")
                return True
        return False

    while True:
        cycle += 1
        if settings.night_off and stealth.night_blocked(
            time.localtime().tm_hour, settings.night_from, settings.night_to
        ):
            info(
                f"现在是 {settings.night_from}:00–{settings.night_to}:00，按设定不刷，"
                f"{int(every)} 秒后再看"
            )
            time.sleep(every)
            cycle -= 1
            continue
        if max_cycles and cycle > max_cycles:
            err(f"到了 --cycles 上限（{max_cycles} 轮），还有任务没刷完")
            return 3
        if max_hours and (time.time() - started) / 3600 >= max_hours:
            err(f"到了 --hours 上限（{max_hours:g} 小时），还有任务没刷完")
            return 3

        try:
            tasks = collect_fn()
        except Exception as exc:  # noqa: BLE001
            warn(f"第 {cycle} 轮读取任务失败：{exc}")
            if note_auth_error(str(exc)):
                return 3
            time.sleep(every)
            continue
        auth_fails = 0

        if not tasks:
            err("没有可执行的任务")
            write_daemon_state(settings, cycle=cycle, total=0, pending=0,
                               solved=solved_times, phase="empty")
            return 2

        pending = [t for t in tasks if not t.done or settings.force]
        if not pending:
            ok(f"全部完成：{len(tasks)} 个任务都刷完了")
            write_daemon_state(settings, cycle=cycle, total=len(tasks), pending=0,
                               solved=solved_times, phase="done")
            return 0

        info(f"第 {cycle} 轮：还有 {len(pending)}/{len(tasks)} 个待刷")
        ds: dict[str, Any] = {
            "pid": __import__("os").getpid(),
            "cycle": cycle,
            "total": len(tasks),
            "pending": len(pending),
            "jobs": jobs,
            "every": every,
            "solved": solved_times,
            "phase": "probe",
        }
        write_daemon_state(settings, **ds)
        target = pending[0]
        state = "ok"
        if getattr(target, "kind", "zhidao") == "zhidao":
            try:
                state = probe_fn(
                    settings, cookies, target.rac_id, target.video_id, 12, quiet=True
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"探测失败，先按可刷处理：{exc}")
                state = "error"
        if state != "ok":
            ds.update(phase="captcha" if state == "captcha" else state, probe=state)
            write_daemon_state(settings, **ds)
        if state == "limit":
            limit_hits += 1
            if limit_hits >= 3:
                err("连续 3 次撞到学习时长上限（-9），这门课今天到顶了，明天再来：zhs-multi.bat run -c all")
                write_daemon_state(settings, cycle=cycle, total=len(tasks),
                                   pending=len(pending), solved=solved_times, phase="limit")
                return 3
            info(f"撞到学习时长上限（-9），{int(every)} 秒后再看（第 {limit_hits}/3 次）")
            try:
                time.sleep(every)
            except KeyboardInterrupt:
                warn("已中断")
                return 0
            continue
        limit_hits = 0
        if state == "captcha":
            if solve_fn is not None:
                info("被 -12 拦住，先试着自动点掉验证码…")
                try:
                    solved = bool(solve_fn())
                except Exception as exc:  # noqa: BLE001
                    warn(f"自动点选失败：{exc}")
                    solved = False
                if solved:
                    solved_times += 1
                    ds.update(solved=solved_times, phase="solved")
                    write_daemon_state(settings, **ds)
                    ok("验证码已通过，继续刷")
                    try:
                        state = probe_fn(
                            settings, cookies, target.rac_id, target.video_id, 12, quiet=True
                        )
                    except Exception:  # noqa: BLE001
                        state = "ok"
                    if state == "ok":
                        continue
                    info(f"点过了但接口还是 -12，{int(every)} 秒后再看")
            else:
                info(f"仍要求验证码（-12），{int(every)} 秒后再看；Ctrl+C 可退出")
            try:
                time.sleep(every)
            except KeyboardInterrupt:
                warn("已中断")
                return 0
            continue

        before = len(pending)
        stop_hb = threading.Event()
        hb = threading.Thread(
            target=_heartbeat,
            args=(settings, dict(ds), stop_hb, heartbeat),
            daemon=True,
        )
        hb.start()

        def on_task(outcome: Any) -> None:
            """每刷完一条就把"还剩几条"更新掉，status 显示的数字才是实时的"""
            if getattr(outcome, "status", "") in ("ok", "skip"):
                ds["pending"] = max(int(ds.get("pending", 0)) - 1, 0)
            ds["last"] = getattr(getattr(outcome, "task", None), "name", "")
            write_daemon_state(settings, **ds)

        try:
            rc = run_fn(settings, cookies, selected, jobs=jobs, on_task=on_task)
        except TypeError as exc:
            if "on_task" not in str(exc):
                raise
            rc = run_fn(settings, cookies, selected, jobs=jobs)
        except KeyboardInterrupt:
            warn("已中断")
            return 0
        except Exception as exc:  # noqa: BLE001
            warn(f"第 {cycle} 轮刷课异常：{exc}")
            rc = 1
        finally:
            stop_hb.set()
        if rc == 2:
            return 2

        try:
            after = len([t for t in collect_fn() if not t.done or settings.force])
        except Exception:  # noqa: BLE001
            after = before
        ds.update(phase="idle", pending=after)
        write_daemon_state(settings, **ds)
        if after == 0:
            ok("全部完成")
            write_daemon_state(settings, cycle=cycle, total=len(tasks), pending=0,
                               solved=solved_times, phase="done")
            return 0
        if after >= before:
            info(f"这轮没推进（{before} → {after}），{int(every)} 秒后再试")
            try:
                time.sleep(every)
            except KeyboardInterrupt:
                warn("已中断")
                return 0


def wait_unblock(
    settings: AppSettings,
    cookies: list[dict[str, Any]],
    rac_id: str,
    video_id: int,
    wait: int = 900,
    interval: int = 30,
    open_browser: bool = True,
    quiet: bool = False,
) -> bool:
    """等用户在浏览器里点掉验证码；解除返回 True"""
    # 先探一次：没被拦就不用折腾浏览器
    state = probe(settings, cookies, rac_id, video_id, 12, quiet=True)
    if state == "ok":
        ok("当前没有被要求验证码")
        return True
    if state == "limit":
        err("撞到学习时长上限（-9），今天这门课到顶了")
        return False

    say()
    rule("=")
    info("平台要求图形验证码（-12），需要在网页里点一次")
    info(f"播放页：{course_url(rac_id)}")
    rule("=")
    info("更快的办法：另开一个窗口跑 zhs-multi.bat captcha，让它自动点掉")
    if open_browser:
        if open_course_page(rac_id):
            ok("已用默认浏览器打开该页面")
        else:
            warn("自动打开失败，请手动复制上面的链接")
    info("页面里播一小段 → 把弹出的验证码点掉 → 这里会自动继续")

    deadline = time.time() + wait
    attempt = 0
    while True:
        left = int(deadline - time.time())
        if left <= 0:
            return False
        time.sleep(min(max(15, interval), left))
        attempt += 1
        left = int(deadline - time.time())
        say(f"  第 {attempt} 次检查（剩余 {max(left, 0)}s）…")
        state = probe(settings, cookies, rac_id, video_id, 12, quiet=True)
        if state == "ok":
            ok("验证码已解除")
            return True
        if not quiet:
            warn(f"仍被拦（{state}）")


def probe(settings: AppSettings, cookies: list[dict[str, Any]], rac_id: str,
          video_id: int, seconds: int = 45, quiet: bool = False) -> str:
    """试播一条视频若干秒，用来判断「验证码限制解除了没」

    返回：
        ok       正常推进（说明 -12 已解除）
        captcha  仍然要求验证码
        limit    撞到学习时长上限
        error    其它异常
    """
    from zhs.zhidao.course import ZhidaoCourseManager
    from zhs.zhidao.video import ZhidaoVideoPlayer

    session = new_session(settings, cookies)
    ctx = ZhidaoCourseManager(session).get_context(rac_id)
    player = ZhidaoVideoPlayer(
        session,
        speed=settings.speed,
        end_threshold=settings.threshold,
        time_limit=max(5, int(seconds)),
    )
    if not quiet:
        say()
        info(f"试播 videoId={video_id}，最多 {seconds} 秒……")
    with dashboard(settings.dashboard if not quiet else False):
        try:
            player.play_video(rac_id, video_id, ctx.model_copy(deep=True))
        except Exception as exc:  # noqa: BLE001
            kind = type(exc).__name__
            if kind == "CaptchaRequired":
                if not quiet:
                    warn("结果：仍然要求图形验证码（-12）")
                    info(
                        "去这里看一小段视频、把验证码点掉：\n"
                        f"    https://studyh5.zhihuishu.com/videoStudy.html#/studyVideo"
                        f"?recruitAndCourseId={rac_id}"
                    )
                return "captcha"
            if kind == "TimeLimitExceeded":
                if not quiet:
                    ok(f"结果：能正常上报（{seconds} 秒后按预期停下）")
                return "ok"
            if not quiet:
                err(f"结果：{kind}: {exc}")
            return "error"
    if not quiet:
        ok(f"结果：能正常上报（{seconds} 秒后按预期停下）")
    return "ok"


def selftest(jobs: int = 4, tasks: int = 12) -> int:
    """不联网的并发自检：确认线程真的并行、状态行不打架"""
    say()
    info(f"自检：{jobs} 线程处理 {tasks} 个假任务（每个 1.0s）")
    fake = [
        VideoTask(
            course_index=1,
            course_label="[自检] 假课程",
            rac_id="0",
            video_id=1000 + i,
            name=f"假视频{i + 1}",
            chapter="自检章",
            lesson="自检节",
            done=False,
        )
        for i in range(tasks)
    ]

    from .courses import VideoTask as _VT  # noqa: F401

    summary = RunSummary(total=len(fake))
    q: queue.Queue[Any] = queue.Queue()
    for t in fake:
        q.put(t)
    res: list[TaskOutcome] = []

    def fake_worker(no: int) -> None:
        name = f"W{no}"
        while True:
            try:
                t = q.get_nowait()
            except queue.Empty:
                return
            set_status(name, label=t.name, state="play ", pct=0)
            for pct in (20, 40, 60, 80, 100):
                time.sleep(0.2)
                set_status(name, label=t.name, state="play ", pct=pct)
            clear_status(name)
            res.append(TaskOutcome(t, "ok", "", 1.0))

    t0 = time.time()
    with dashboard(True):
        ths = [threading.Thread(target=fake_worker, args=(i + 1,), name=f"pool_{i}") for i in range(jobs)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    wall = time.time() - t0
    serial = tasks * 1.0
    summary.outcomes = res
    summary.done = len(res)
    summary.seconds = wall
    say()
    ok(f"完成 {len(res)}/{tasks}，墙钟 {wall:.2f}s，串行预期约 {serial:.1f}s")
    if jobs <= 1:
        info("jobs=1 就是串行基准，没有加速比可言（这条不算失败）")
        return 0
    if wall < serial * 0.6:
        ok(f"并发生效：加速约 {serial / wall:.1f}x")
        return 0
    err("并发未生效，检查线程池实现")
    return 1
