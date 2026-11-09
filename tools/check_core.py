"""核心链路离线自检 —— 不联网、不依赖账号

跑法：
    python tools\\check_core.py
    zhs-multi.bat selftest --core

覆盖：
 1. 选课 spec 解析（1 / 1,3 / 2-4 / all / 空格 / 越界 / 非法）
 2. config 读取与钳制（BOM、缺键、非法值、登录方式回退）
 3. 播放器进度行分流（进度行只更新状态，不刷屏）
 4. 失败补跑 + 结果去重
 5. 本轮报告 last-run.json 结构
 6. 密码登录的 secretStr 编码（btoa(encodeURI(json))）
 7. Cookie 导入四种格式
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import AppSettings, load  # noqa: E402
from app.courses import VideoTask, parse_spec  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  [OK] " if cond else "  [!!] ") + label)
    if not cond:
        FAILS.append(label)


def expect_raises(fn, label: str) -> None:
    try:
        fn()
    except Exception:
        print(f"  [OK] {label}")
        return
    print(f"  [!!] {label}")
    FAILS.append(label)


# ------------------------------------------------------------------ 1 spec
def test_spec() -> None:
    print("1) 选课 spec")
    check(parse_spec("1", 3) == [1], "单个序号")
    check(parse_spec("1,3", 3) == [1, 3], "逗号分隔")
    check(parse_spec("2-4", 5) == [2, 3, 4], "区间")
    check(parse_spec("all", 3) == [1, 2, 3], "all")
    check(parse_spec("4-2", 5) == [2, 3, 4], "反向区间自动纠正")
    check(parse_spec("1 3", 3) == [1, 3], "空格分隔")
    check(parse_spec("", 3) == [], "空串")
    expect_raises(lambda: parse_spec("9", 3), "越界报错")
    expect_raises(lambda: parse_spec("x", 3), "非法字符报错")


# ---------------------------------------------------------------- 2 config
def test_config() -> None:
    print("2) config 读取与钳制")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        p = d / "c.ini"
        # 带 BOM、越界值、非法登录方式、缺段
        p.write_bytes(
            "\ufeff[common]\njobs = 99\nspeed = 9\nthreshold = 9\ndelay = 999\n"
            "unblock_wait = 99999\n\n[ai]\nvideothreshold = 1\n"
            "video_threshold = 555\n".encode("utf-8")
        )
        s = load(p, project_dir=d)
        check(s.jobs == 32, f"jobs 99→32（{s.jobs}）")
        check(s.speed == 2.0, f"speed 9→2.0（{s.speed}）")
        check(s.threshold == 1.2, f"threshold 9→1.2（{s.threshold}）")
        check(s.delay == 30.0, f"delay 999→30（{s.delay}）")
        check(s.unblock_wait == 7200, f"unblock_wait 99999→7200（{s.unblock_wait}）")
        check(s.ai_video_threshold == 100, f"AI 阈值 555→100（{s.ai_video_threshold}）")
        check(s.jobs >= 1 and s.homework_max_submit >= 0, "缺段用默认值补齐")

        bad = d / "bad.ini"
        bad.write_text("[common]\njobs = abc\n", encoding="utf-8")
        try:
            load(bad, project_dir=d)
            check(False, "非法数字应当报错")
        except ValueError:
            check(True, "非法数字报错（ValueError）")

        missing = d / "nope.ini"
        try:
            load(missing, project_dir=d)
            check(False, "缺文件应当报错")
        except FileNotFoundError:
            check(True, "缺文件报 FileNotFoundError")


# ---------------------------------------------------------------- 3 console
def test_console() -> None:
    print("3) 进度行分流")
    from app import console

    events: list[str] = []
    key = __import__("threading").current_thread().name
    console.set_status(key, label="视频A", state="play ", pct=0)
    sink = console.Sink(on_event=events.append)
    sink("playing  71045789 [#...............] 45.2%")
    check(not events, "进度行不产出事件（不刷屏）")
    check(abs(console._STATUS.get(key, {}).get("pct", 0) - 45.2) < 0.01,
          f"状态里的百分比被更新（{console._STATUS.get(key, {}).get('pct')}）")
    sink("pause  [#.......] 33%")
    check(console._STATUS.get(key, {}).get("state") == "pause", "pause 行识别为暂停态")
    sink("完成: 第一章 1.1")
    check(events and "完成" in events[-1], "普通输出进入事件流")
    # zhs 的 tree_print 会带 depth=/enabled= 关键字，接管后不能炸
    try:
        sink("!! 需要验证码，停止课程", depth=3, enabled=True)
        sink("跳过(已完成)", depth=2, enabled=False)
        sink("\r进度条", end="", flush=True)
        check(True, "兼容 tree_print(depth/enabled) 与 print(end/flush)")
    except TypeError as exc:
        check(False, f"接管签名不兼容：{exc}")
    check(any(e.endswith("需要验证码，停止课程") for e in events), "depth 关键字被正确接收")
    check(all("跳过" not in e for e in events), "enabled=False 的输出被丢弃")
    console.clear_status(key)


def test_dashboard() -> None:
    print("3.1) dashboard 接管与还原")
    import zhs.zhidao.video as zvideo

    from app import console

    before = (getattr(zvideo, "print", None), getattr(zvideo, "tree_print", None),
              getattr(zvideo, "wipe_line", None))
    seen: list[str] = []
    with console.dashboard(False) as sink:
        sink._on_event = seen.append
        inside_print = getattr(zvideo, "print", None)
        zvideo.tree_print("完成课程: X", depth=1, enabled=True)
        check(inside_print is not None and inside_print is not before[0], "接管期内 print 被替换")
    after = (getattr(zvideo, "print", None), getattr(zvideo, "tree_print", None),
             getattr(zvideo, "wipe_line", None))
    check(after == before, "退出后按原样还原")
    check(any("完成课程" in s for s in seen), "接管期内的输出被 sink 收到")


# ------------------------------------------------------------- 4 补跑去重
def test_retry() -> None:
    print("4) 失败补跑与去重")
    import collections
    import time

    import app.runner as runner_mod

    settings = AppSettings(delay=0.0, limit=0, dashboard=False)
    attempts = collections.Counter()

    class FlakyWorker:
        def __init__(self, *a, **kw):
            pass

        def play(self, task):
            attempts[task.video_id] += 1
            time.sleep(0.05)
            if attempts[task.video_id] == 1:
                raise ConnectionError("模拟抖动")

    original = runner_mod.Worker
    runner_mod.Worker = FlakyWorker  # type: ignore[assignment]
    try:
        tasks = [VideoTask(1, "[测]", "R", 100 + i, f"v{i}", "章", "节", False) for i in range(3)]
        summary = runner_mod.run_tasks(settings, [], tasks, 2)
    finally:
        runner_mod.Worker = original  # type: ignore[assignment]

    check(summary.done == 3, f"3 条全部补跑成功（{summary.done}）")
    check(len(summary.outcomes) == 3, f"结果按 videoId 去重后仍是 3 条（{len(summary.outcomes)}）")
    check(all(attempts[t.video_id] == 2 for t in tasks), "每条恰好尝试两次")


# ----------------------------------------------------------------- 5 报告
def test_report() -> None:
    print("5) 本轮报告")
    import app.runner as runner_mod

    settings = AppSettings(project_dir=Path(tempfile.mkdtemp()))
    task = VideoTask(1, "[知到] 测", "R", 42, "视频A", "第一章", "1.1", False)
    summary = runner_mod.RunSummary(total=1, done=1, seconds=3.5)
    summary.outcomes = [runner_mod.TaskOutcome(task, "ok", "", 3.5)]
    path = runner_mod.save_run_report(settings, summary)
    data = json.loads(path.read_text(encoding="utf-8"))
    check(path.name == "last-run.json", "文件名 last-run.json")
    check(data["counts"]["ok"] == 1 and data["counts"]["total"] == 1, "计数落盘")
    check(data["tasks"][0]["video_id"] == 42, "逐条任务落盘")
    check(isinstance(data["time"], str) and "T" in data["time"], "时间戳 ISO 格式")


# ------------------------------------------------------------ 6 只保留扫码
def test_secret() -> None:
    print("6) 登录入口只剩扫码")
    import inspect

    from app import login as login_mod

    params = list(inspect.signature(login_mod.login).parameters)
    check(not any(p in params for p in ("mode", "username", "password", "captcha")),
          f"login() 不再接受账号密码参数（{params}）")
    check(hasattr(login_mod, "login_qr"), "扫码入口在")
    check(not hasattr(login_mod, "login_password"), "账号密码入口已移除")
    check(not hasattr(login_mod, "_secret"), "密码加密函数已移除")
    check(not hasattr(login_mod, "SlideCaptchaRequired"), "滑块异常类已移除")

    from app.config import AppSettings

    fields = AppSettings.__dataclass_fields__
    check("username" not in fields and "password" not in fields and "login_mode" not in fields,
          "配置里不再有 username/password/login_mode")


# ---------------------------------------------------------------- 7 cookie
def test_cookie() -> None:
    print("7) Cookie 导入")
    sys.path.insert(0, str(ROOT))
    from app import login as login_mod

    settings = AppSettings(project_dir=Path(tempfile.mkdtemp()))
    cases = {
        "headed 请求头": "Cookie: SESSION=abc; SERVERID=web1",
        "JSON 对象": '{"SESSION": "abc", "SERVERID": "web1"}',
        "JSON 数组": '[{"name": "SESSION", "value": "abc"}, {"name": "SERVERID", "value": "web1"}]',
        "k=v 一行": "SESSION=abc; SERVERID=web1",
        "每行一条": "SESSION=abc\nSERVERID=web1",
    }
    for name, body in cases.items():
        try:
            got = login_mod.import_cookies(settings, body)
        except Exception as exc:  # noqa: BLE001
            check(False, f"{name} 解析抛错：{exc}")
            continue
        pairs = {c["name"]: c["value"] for c in got}
        check(pairs.get("SESSION") == "abc" and pairs.get("SERVERID") == "web1",
              f"{name} 解析出 2 条")


# ------------------------------------------------------- 8 probe 结果分类
def test_probe() -> None:
    print("8) probe 结果分类（假播放器）")
    from zhs.exceptions import CaptchaRequired, TimeLimitExceeded

    import app.runner as runner_mod

    class FakeCtx:
        def model_copy(self, deep=True):
            return self

    class FakeMgr:
        def __init__(self, *a, **kw):
            pass

        def get_context(self, rac_id):
            return FakeCtx()

    state = {"mode": "ok"}

    class FakePlayer:
        def __init__(self, *a, **kw):
            pass

        def play_video(self, *a, **kw):
            mode = state["mode"]
            if mode == "captcha":
                raise CaptchaRequired("服务端要求验证码")
            if mode == "limit":
                raise TimeLimitExceeded("学习时长上限")
            if mode == "error":
                raise RuntimeError("boom")
            return None

    import zhs.zhidao.course as zcourse
    import zhs.zhidao.video as zvideo

    orig_mgr, orig_player = zcourse.ZhidaoCourseManager, zvideo.ZhidaoVideoPlayer
    zcourse.ZhidaoCourseManager = FakeMgr  # type: ignore[assignment]
    zvideo.ZhidaoVideoPlayer = FakePlayer  # type: ignore[assignment]
    settings = AppSettings(delay=0.0, dashboard=False)
    try:
        for mode, expected in (
            ("ok", "ok"),
            ("captcha", "captcha"),
            ("limit", "ok"),
            ("error", "error"),
        ):
            state["mode"] = mode
            got = runner_mod.probe(settings, [], "RAC", 1, 5, quiet=True)
            check(got == expected, f"play_video 抛 {mode} → probe 返回 {expected}（{got}）")
    finally:
        zcourse.ZhidaoCourseManager = orig_mgr  # type: ignore[assignment]
        zvideo.ZhidaoVideoPlayer = orig_player  # type: ignore[assignment]


# ------------------------------------------------------- 9 自动解锁轮询
def test_wait_unblock() -> None:
    print("9) 自动解锁轮询（假 probe，不联网）")
    import app.runner as runner_mod

    settings = AppSettings(delay=0.0, dashboard=False)
    original = runner_mod.probe
    calls: list[float] = []

    def flaky_probe(*a, **kw):
        calls.append(1)
        return "captcha" if len(calls) < 3 else "ok"

    runner_mod.probe = flaky_probe  # type: ignore[assignment]
    try:
        calls.clear()
        got = runner_mod.wait_unblock(
            settings, [], "RAC", 1, wait=20, interval=15, open_browser=False, quiet=True
        )
        check(got is True, "第 3 次探测解除 → 返回 True")
        check(len(calls) == 3, f"共探测 3 次（{len(calls)}）")

        calls.clear()
        runner_mod.probe = lambda *a, **kw: (calls.append(1), "captcha")[1]  # type: ignore[assignment]
        got = runner_mod.wait_unblock(
            settings, [], "RAC", 1, wait=1, interval=15, open_browser=False, quiet=True
        )
        check(got is False, "等不到解除 → 返回 False")
    finally:
        runner_mod.probe = original  # type: ignore[assignment]

# ------------------------------------------------------- 10 盯梢模式
def test_daemon() -> None:
    print("10) 盯梢模式（假 probe / 假 run，不联网）")
    from app.courses import VideoTask
    from app.runner import run_daemon

    _sandbox = tempfile.TemporaryDirectory()
    settings = AppSettings(delay=0.0, dashboard=False, project_dir=Path(_sandbox.name))

    def make_tasks(total: int, pending: int) -> list[VideoTask]:
        return [
            VideoTask(1, "[测]", "R", i, f"v{i}", "章", "节", done=(i >= pending))
            for i in range(total)
        ]

    # 场景一：探测始终被拦 → 到了 --cycles 上限就退出 3
    state = {"pending": 3, "probe": "captcha"}
    rc = run_daemon(
        settings, [], [], jobs=1, every=0.01, max_cycles=2,
        probe_fn=lambda *a, **kw: state["probe"],
        run_fn=lambda *a, **kw: 0,
        collect_fn=lambda: make_tasks(3, state["pending"]),
    )
    check(rc == 3, f"一直拦着 → 到上限退出 3（{rc}）")

    # 场景二：每次刷掉一条 → 最终返回 0
    state = {"pending": 3, "probe": "ok"}
    runs: list[int] = []

    def fake_run(settings_, cookies_, selected_, jobs=None, **kw):
        runs.append(1)
        state["pending"] -= 1
        return 0

    rc = run_daemon(
        settings, [], [], jobs=1, every=0.01, max_cycles=20,
        probe_fn=lambda *a, **kw: state["probe"],
        run_fn=fake_run,
        collect_fn=lambda: make_tasks(3, state["pending"]),
    )
    check(rc == 0, f"逐条刷完 → 返回 0（{rc}）")
    check(len(runs) == 3, f"共触发 3 次刷课（{len(runs)}）")

    # 场景三：一轮下来毫无推进 → 不会死循环，到上限退出 3
    rc = run_daemon(
        settings, [], [], jobs=1, every=0.01, max_cycles=3,
        probe_fn=lambda *a, **kw: "ok",
        run_fn=lambda *a, **kw: 0,
        collect_fn=lambda: make_tasks(3, 3),
    )
    check(rc == 3, f"毫无推进也不死循环（{rc}）")

    # 场景四：本来就没活 → 直接 0
    rc = run_daemon(
        settings, [], [], jobs=1, every=0.01, max_cycles=3,
        probe_fn=lambda *a, **kw: "captcha",
        run_fn=lambda *a, **kw: 0,
        collect_fn=lambda: make_tasks(3, 0),
    )
    check(rc == 0, f"没有待刷 → 返回 0（{rc}）")

    # 场景五：给了 solve_fn 且点得过去 → 不睡等待，直接续刷（返回 0）
    state = {"pending": 2, "probe": "captcha", "solved": 0}

    def fake_solve() -> bool:
        state["solved"] += 1
        state["probe"] = "ok"  # 点过之后探测就通了
        return True

    def fake_run2(settings_, cookies_, selected_, jobs=None, **kw):
        state["pending"] -= 1
        return 0

    rc = run_daemon(
        settings, [], [], jobs=1, every=30, max_cycles=10,
        probe_fn=lambda *a, **kw: state["probe"],
        run_fn=fake_run2,
        collect_fn=lambda: make_tasks(2, state["pending"]),
        solve_fn=fake_solve,
    )
    check(rc == 0, f"自动点掉验证码后继续刷 → 返回 0（{rc}）")
    check(state["solved"] == 1, f"只点了一次（{state['solved']}）")

    # 场景六：solve_fn 点了但接口还是 -12 → 退回等待，到上限退出 3（不会空转）
    rc = run_daemon(
        settings, [], [], jobs=1, every=0.01, max_cycles=2,
        probe_fn=lambda *a, **kw: "captcha",
        run_fn=lambda *a, **kw: 0,
        collect_fn=lambda: make_tasks(2, 2),
        solve_fn=lambda: True,
    )
    check(rc == 3, f"点了仍被拦 → 到上限退出 3（{rc}）")

    # 场景七：solve_fn 抛异常 → 当作没点过，继续等（不崩）
    rc = run_daemon(
        settings, [], [], jobs=1, every=0.01, max_cycles=2,
        probe_fn=lambda *a, **kw: "captcha",
        run_fn=lambda *a, **kw: 0,
        collect_fn=lambda: make_tasks(2, 2),
        solve_fn=lambda: (_ for _ in ()).throw(RuntimeError("浏览器没起来")),
    )
    check(rc == 3, f"自动点选异常也不崩（{rc}）")

    # 状态文件：盯梢进度要能被 status 读到
    with tempfile.TemporaryDirectory() as tmp:
        st = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp))
        from app.runner import write_daemon_state

        write_daemon_state(st, cycle=3, total=7, pending=4, phase="probe", solved=1)
        f = Path(tmp) / "state" / "daemon.json"
        check(f.exists(), "写入 daemon.json")
        import json as _j

        data = _j.loads(f.read_text(encoding="utf-8"))
        check(data["cycle"] == 3 and data["pending"] == 4, "字段落盘正确")
        check("T" in str(data["time"]), "时间是 ISO 格式")

        # 真跑一轮盯梢，确认它会自己写这个文件
        st2 = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp) / "s2")
        rc = run_daemon(
            st2, [], [], jobs=1, every=0.01, max_cycles=1,
            probe_fn=lambda *a, **kw: "captcha",
            run_fn=lambda *a, **kw: 0,
            collect_fn=lambda: make_tasks(2, 2),
        )
        check((Path(tmp) / "s2" / "state" / "daemon.json").exists(), f"盯梢循环自己写状态（rc={rc}）")

        # 心跳：刷课期间要持续刷新时间戳，否则 status 会以为盯梢已退出
        import json as _j2
        import time as _t

        st3 = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp) / "s3")
        f3 = Path(tmp) / "s3" / "state" / "daemon.json"

        def slow_run(*a, **kw):
            _t.sleep(0.35)
            return 0

        rc = run_daemon(
            st3, [], [], jobs=1, every=0.01, max_cycles=1,
            probe_fn=lambda *a, **kw: "ok",
            run_fn=slow_run,
            collect_fn=lambda: make_tasks(2, 2),
            heartbeat=0.1,
        )
        check(f3.exists(), f"心跳写了状态文件（rc={rc}）")
        d3 = _j2.loads(f3.read_text(encoding="utf-8"))
        check(d3.get("phase") in ("running", "idle"), f"心跳阶段标记（{d3.get('phase')}）")

        # 每刷完一条要实时扣减 pending（run 结束后会用 collect 重算，所以要在跑的过程中看）
        st6 = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp) / "s6")
        f6 = Path(tmp) / "s6" / "state" / "daemon.json"
        seen: dict[str, int] = {}

        class _Out:
            def __init__(self, name: str, status: str = "ok") -> None:
                self.status = status
                self.task = type("T", (), {"name": name})()

        def run_with_hook(settings_, cookies_, selected_, jobs=1, on_task=None):
            if on_task:
                on_task(_Out("视频A"))
                seen["pending"] = _j2.loads(f6.read_text(encoding="utf-8"))["pending"]
                on_task(_Out("视频B", "fail"))
            return 0

        rc = run_daemon(
            st6, [], [], jobs=1, every=0.01, max_cycles=1,
            probe_fn=lambda *a, **kw: "ok",
            run_fn=run_with_hook,
            collect_fn=lambda: make_tasks(3, 3),
        )
        check(seen.get("pending") == 2, f"刷完 1 条 pending 3→2（实际 {seen.get('pending')}）")
        d6 = _j2.loads(f6.read_text(encoding="utf-8"))
        check(d6.get("last") == "视频B", f"记了最后一条名字（{d6.get('last')}）")

        # -9 学习时长上限：不能再盲目跑 run，连续 3 次就收工
        st7 = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp) / "s7")
        ran7: list[int] = []
        rc = run_daemon(
            st7, [], [], jobs=1, every=0.01, max_cycles=99,
            probe_fn=lambda *a, **kw: "limit",
            run_fn=lambda *a, **kw: (ran7.append(1), 0)[1],
            collect_fn=lambda: make_tasks(3, 3),
        )
        check(rc == 3, f"连续 -9 后退出 3（{rc}）")
        check(not ran7, f"-9 时不硬跑刷课（跑了 {len(ran7)} 次）")

        # 探测抛异常 → 不能把盯梢带崩，要按"可刷"继续
        st4 = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp) / "s4")
        ran: list[int] = []

        def boom_probe(*a, **kw):
            raise RuntimeError("网络断了")

        rc = run_daemon(
            st4, [], [], jobs=1, every=0.01, max_cycles=1,
            probe_fn=boom_probe,
            run_fn=lambda *a, **kw: (ran.append(1), 0)[1],
            collect_fn=lambda: make_tasks(1, 1),
        )
        check(len(ran) == 1, f"探测异常仍继续刷（rc={rc}, 刷了 {len(ran)} 次）")

        # 连续 3 次"登录态失效" → 停下并提示重新扫码，不空转
        st5 = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp) / "s5")
        calls = {"n": 0}

        def auth_boom():
            calls["n"] += 1
            raise RuntimeError("未登录：请重新登录")

        rc = run_daemon(
            st5, [], [], jobs=1, every=0.01, max_cycles=99,
            probe_fn=lambda *a, **kw: "ok",
            run_fn=lambda *a, **kw: 0,
            collect_fn=auth_boom,
        )
        check(rc == 3, f"登录态失效 3 次后停止（rc={rc}）")
        check(calls["n"] == 3, f"只试 3 次就停（{calls['n']}）")

    _sandbox.cleanup()

    # 易盾提示语解析（跑在装了 playwright 的解释器里；没装就跳过）
    from app import captcha as cap_mod

    py = cap_mod.find_python()
    if py:
        import subprocess

        proc = subprocess.run(
            [str(py),
             str(Path(__file__).resolve().parents[1] / "tools" / "yidun_solve.py"),
             "--selftest-parse"],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
        )
        check(proc.returncode == 0, f"提示语解析用例全过（rc={proc.returncode}）")
        check("解析用例 7/7" in (proc.stdout or ""), "7 类提示语都能解析")
    else:
        print("  [--] 没找到 playwright 解释器，跳过提示语解析用例")


# ------------------------------------------------------- 11 错峰与抖动
def test_stagger() -> None:
    print("11) 错峰启动与上报抖动")
    import time

    import app.runner as runner_mod
    from app.courses import VideoTask

    import threading

    firsts: dict[str, float] = {}

    class SlowWorker:
        def __init__(self, *a, **kw):
            pass

        def play(self, task):
            firsts.setdefault(threading.current_thread().name, time.monotonic())
            time.sleep(0.2)

    original = runner_mod.Worker
    runner_mod.Worker = SlowWorker  # type: ignore[assignment]
    try:
        settings = AppSettings(delay=0.0, dashboard=False, stagger=0.15)
        tasks = [VideoTask(1, "[测]", "R", i, f"v{i}", "章", "节", False) for i in range(6)]
        runner_mod.run_tasks(settings, [], tasks, 3)
    finally:
        runner_mod.Worker = original  # type: ignore[assignment]

    spread = (max(firsts.values()) - min(firsts.values())) if len(firsts) == 3 else 0.0
    check(len(firsts) == 3, f"3 个线程都拿到了活（{len(firsts)}）")
    check(spread >= 0.2, f"三个线程错峰启动，首个任务时间差 {spread:.2f}s（stagger 0.15×2 应 ≥0.3）")
    check(0.0 <= spread <= 1.5, f"错峰没有拖慢整体（{spread:.2f}s）")

    # 抖动：delay 乘 0.85~1.3 的随机系数
    delays: list[float] = []

    class FastWorker:
        def __init__(self, *a, **kw):
            pass

        def play(self, task):
            pass

    import random as _random

    orig_sleep = time.sleep
    runner_mod.Worker = FastWorker  # type: ignore[assignment]
    try:
        def fake_sleep(sec):
            delays.append(sec)

        runner_mod.time.sleep = fake_sleep  # type: ignore[attr-defined]
        settings = AppSettings(delay=1.0, dashboard=False, stagger=0.0)
        tasks = [VideoTask(1, "[测]", "R", i, f"v{i}", "章", "节", False) for i in range(10)]
        runner_mod.run_tasks(settings, [], tasks, 1)
    finally:
        runner_mod.time.sleep = orig_sleep  # type: ignore[attr-defined]
        runner_mod.Worker = original  # type: ignore[assignment]

    jitter = [d for d in delays if 0.8 <= d <= 1.35]
    check(len(jitter) >= 10, f"每次任务后的等待落在 0.85~1.3 倍区间（{len(jitter)} 次）")
    check(len(set(round(d, 3) for d in jitter)) > 1, "等待时长不是固定值（有抖动）")

def test_daemon_wiring() -> None:
    """`daemon --solve-captcha` 到底有没有把"能点验证码"的函数交进去"""
    print("12) daemon --solve-captcha 的接线")
    import tempfile
    from types import SimpleNamespace

    import app.__main__ as M
    import app.captcha as CAP
    import app.courses as C
    import app.runner as R
    import app.session_factory as SF

    saved = {
        (M, "_load_settings"): M._load_settings,
        (M, "_setup_logging"): M._setup_logging,
        (M, "_cookies_or_login"): M._cookies_or_login,
        (SF, "new_session"): SF.new_session,
        (C, "list_courses"): C.list_courses,
        (C, "select_courses"): C.select_courses,
        (CAP, "available"): CAP.available,
        (CAP, "solve"): CAP.solve,
        (R, "run_daemon"): R.run_daemon,
    }
    captured: dict[str, object] = {}
    solved_calls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = AppSettings(delay=0.0, dashboard=False, project_dir=Path(tmp))
        M._load_settings = lambda a: sandbox  # type: ignore[assignment]
        M._setup_logging = lambda s: None  # type: ignore[assignment]
        M._cookies_or_login = lambda s, allow_login=True: []  # type: ignore[assignment]
        SF.new_session = lambda s, c: None  # type: ignore[assignment]
        C.list_courses = lambda sess: [SimpleNamespace(label="[知到] 假课", kind="zhidao")]  # type: ignore[assignment]
        C.select_courses = lambda entries, spec: entries  # type: ignore[assignment]
        CAP.available = lambda: (True, "")  # type: ignore[assignment]

        def fake_solve(**kw):
            solved_calls.append(kw)
            return True, ""

        CAP.solve = fake_solve  # type: ignore[assignment]

        def fake_daemon(*a, **kw):
            captured.update(kw)
            return 0

        R.run_daemon = fake_daemon  # type: ignore[assignment]
        try:
            rc = M.cmd_daemon(
                SimpleNamespace(
                    config="", courses="", no_login=False, solve_captcha=True,
                    jobs=1, every=120, cycles=0, hours=0.0,
                )
            )
            check(rc == 0, f"打开开关时正常返回（{rc}）")
            check(callable(captured.get("solve_fn")), "把 solve_fn 交给了盯梢循环")
            got = captured["solve_fn"]() if callable(captured.get("solve_fn")) else None
            check(got is True, f"这个函数真能返回 True（{got}）")
            check(bool(solved_calls) and solved_calls[0].get("quiet") is True,
                  "调的是静音模式（quiet=True）")

            # 关掉开关时不该传 solve_fn
            captured.clear()
            M.cmd_daemon(
                SimpleNamespace(
                    config="", courses="", no_login=False, solve_captcha=False,
                    jobs=1, every=120, cycles=0, hours=0.0,
                )
            )
            check(captured.get("solve_fn") is None, "关掉开关时传的是 None")

            # 环境不可用时要降级成纯等待，而不是抛
            captured.clear()
            CAP.available = lambda: (False, "没装 playwright")  # type: ignore[assignment]
            M.cmd_daemon(
                SimpleNamespace(
                    config="", courses="", no_login=False, solve_captcha=True,
                    jobs=1, every=120, cycles=0, hours=0.0,
                )
            )
            check(captured.get("solve_fn") is None, "点选不可用时自动降级为纯等待")
        finally:
            for (mod, name), orig in saved.items():
                setattr(mod, name, orig)


def test_stealth_progress() -> None:
    """风控闸门 + 总进度：撞 -10 会不会真的降并发，进度算得对不对"""
    print("13) 风控闸门 + 总进度")
    import tempfile

    from app import stealth as S
    from app import console as C

    # --- 总进度
    C.progress_reset(10)
    for _ in range(3):
        C.progress_count("ok", 100.0)
    line = C.progress_line()
    check("3/10" in line, f"总进度显示 3/10（{line.split()[0:2]}）")
    check("30.0%" in line, "百分比算对（30.0%）")
    check("剩 ~" in line, "还有剩余时间估算")
    C.progress_count("fail", 0.0)
    check("失败 1" in C.progress_line(), "失败数会显示出来")
    C.progress_count("skip", 0.0)
    check(C.progress_done() == 4, f"完成数 = ok + skip = 4（{C.progress_done()}）")

    # --- 并发闸门：撞 -10 就往下压
    gov = S.Governor(4, adaptive=True, base_delay=0.4, cool_down=0.0)
    check(gov.limit == 4, "初始并发 = 4")
    gov.note_risk("multi_window")
    check(gov.limit == 3, f"撞一次 -10 降到 3（{gov.limit}）")
    check(gov.delay > 0.4, f"间隔跟着拉长（{gov.delay:.2f}s）")
    gov.note_risk("multi_window")
    gov.note_risk("multi_window")
    gov.note_risk("multi_window")
    check(gov.limit == 1, f"连着撞就压到最低 1（{gov.limit}）")
    for _ in range(10):
        gov.note_ok()
    check(gov.limit >= 2, f"冷却后能慢慢放回去（{gov.limit}）")

    # adaptive=false 时不许自作主张
    gov2 = S.Governor(3, adaptive=False)
    gov2.note_risk("multi_window")
    check(gov2.limit == 3, "关掉自适应后并发不变")

    # --- 日志信号识别
    S.set_current(gov2)
    before = gov2.risks
    S.on_log("Multi-window detected, refreshing token: ...")
    check(gov2.risks == before + 1, "从日志里认出 Multi-window detected")
    S.on_log("完全无关的一行日志")
    check(gov2.risks == before + 1, "无关日志不计数")
    S.set_current(None)

    # --- 休息时长 / 作息
    lo, hi = S.parse_range("60-180")
    check((lo, hi) == (60.0, 180.0), "解析 60-180")
    check(60 <= S.rest_seconds("60-180") <= 180, "休息时长落在区间内")
    check(S.parse_range("bad") == (60.0, 180.0), "写错了回落到默认区间")
    check(S.night_blocked(3, 0, 6), "3 点在 0~6 点里")
    check(not S.night_blocked(9, 0, 6), "9 点不在 0~6 点里")
    check(S.night_blocked(23, 22, 6), "跨零点：23 点算夜间")
    check(not S.night_blocked(12, 22, 6), "跨零点：12 点不算")

    # --- 每日上限
    with tempfile.TemporaryDirectory() as tmp:
        q = S.DailyQuota(Path(tmp) / "daily.json", 3)
        check(not q.exhausted(), "空账本没到上限")
        q.add()
        q.add()
        q.add()
        check(q.exhausted(), "刷到 3 条就到上限")
        q2 = S.DailyQuota(Path(tmp) / "daily.json", 3)
        check(q2.count == 3, f"重启后账本还记得（{q2.count}）")


def main() -> int:
    test_spec()
    test_config()
    test_console()
    test_dashboard()
    test_retry()
    test_report()
    test_secret()
    test_cookie()
    test_probe()
    test_wait_unblock()
    test_daemon()
    test_daemon_wiring()
    test_stagger()
    test_stealth_progress()
    print()
    if FAILS:
        print(f"[!!] {len(FAILS)} 项未通过：" + "；".join(FAILS))
        return 1
    print("[OK] 核心链路自检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
