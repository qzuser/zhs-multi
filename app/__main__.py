"""zhs-multi 命令行入口

子命令：
    login     扫码登录（也可导入浏览器 Cookie）
    list      列出账号下课程
    run       选课 + 多线程刷视频（默认命令）
    probe     试播十几秒，看 -12 解除没
    unblock   解锁验证码（可 --auto 自动点选）
    daemon    盯梢：被拦就等，解除后自动续刷
    captcha   自动点掉易盾点选验证码
    selftest  并发自检（不联网）
    doctor    环境与配置体检
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import paths as _paths

APP_DIR = Path(__file__).resolve().parent
# 打包成 exe 后就是 exe 所在目录；源码运行时是项目根目录
PROJECT_DIR = _paths.project_dir()


# ---------------------------------------------------------------- 基础设施


def _fix_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)  # type: ignore[union-attr]
    except Exception:
        pass


def _setup_logging(settings) -> Path:
    from loguru import logger

    logger.remove()
    log_path = settings.log_dir / "run-{time:YYYYMMDD}.log"
    logger.add(
        str(log_path),
        level="DEBUG" if settings.verbose else "INFO",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )
    # 从日志里盯风控信号（-10 多窗口 / -9 上限 / -12 验证码），撞到就自动降速
    from . import stealth

    logger.add(stealth.on_log, level="WARNING", format="{message}")
    return log_path


def _load_settings(args) -> "object":
    from .config import load

    cfg_path = Path(args.config) if args.config else PROJECT_DIR / "config.ini"
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    return load(cfg_path, project_dir=PROJECT_DIR)


def _cookies_or_login(settings, allow_login: bool = True):
    """确保有可用 Cookie；过期/缺失时按配置登录"""
    from .console import err, info, ok, warn
    from .session_factory import load_cookies, new_session

    cookies = load_cookies(settings.cookies_path) if settings.use_cookies else None
    if cookies:
        session = new_session(settings, cookies)
        try:
            from zhs.api.session_auth import SessionStatus, verify_session

            status = verify_session(session)
            if status is SessionStatus.OK:
                ok("登录状态有效")
                return cookies
            if status is SessionStatus.EXPIRED:
                warn("Cookie 已过期")
            else:
                err("接口不可用（网络/代理？），暂时无法校验登录态")
        except Exception as exc:  # noqa: BLE001
            warn(f"登录态校验失败：{exc}")
    else:
        info("本地没有 Cookie")

    if not allow_login:
        raise SystemExit(2)

    from .login import login

    info("开始扫码登录（智慧树 APP → 扫一扫）")
    return login(settings)


# ---------------------------------------------------------------- 子命令


def cmd_login(args) -> int:
    from .console import err, ok
    from .login import LoginError, import_cookies, login

    settings = _load_settings(args)
    _setup_logging(settings)

    if args.cookie or args.cookie_file:
        raw = args.cookie or ""
        if args.cookie_file:
            from pathlib import Path as _P

            path = _P(args.cookie_file)
            if not path.is_absolute():
                path = (PROJECT_DIR / path).resolve()
            raw = path.read_text(encoding="utf-8")
        try:
            items = import_cookies(settings, raw)
        except LoginError as exc:
            err(str(exc))
            return 1
        ok(f"共 {len(items)} 条 Cookie，已写入 {settings.cookies_path}")
        return 0

    try:
        cookies = login(settings, terminal_qr=args.terminal_qr)
    except LoginError as exc:
        err(str(exc))
        return 1
    ok(f"共拿到 {len(cookies)} 条 Cookie")
    return 0


def cmd_list(args) -> int:
    from .console import say
    from .courses import list_courses, render_courses

    settings = _load_settings(args)
    _setup_logging(settings)
    cookies = _cookies_or_login(settings, allow_login=not args.no_login)
    from .session_factory import new_session

    entries = list_courses(new_session(settings, cookies))
    if args.json:
        say(json.dumps([e.__dict__ for e in entries], ensure_ascii=False, indent=2))
    else:
        render_courses(entries)
    return 0


def cmd_run(args) -> int:
    from .courses import list_courses, select_courses
    from .runner import run_courses

    settings = _load_settings(args)
    _setup_logging(settings)

    # 命令行覆盖配置
    if args.jobs:
        settings.jobs = max(1, min(32, args.jobs))
    if args.speed:
        settings.speed = max(1.0, min(2.0, args.speed))
    if args.threshold:
        settings.threshold = max(0.1, min(1.2, args.threshold))
    if args.limit is not None:
        settings.limit = max(0, args.limit)
    if args.force:
        settings.force = True
    if args.no_dashboard:
        settings.dashboard = False
    if args.verbose:
        settings.verbose = True

    cookies = _cookies_or_login(settings, allow_login=not args.no_login)
    from .session_factory import new_session

    entries = list_courses(new_session(settings, cookies))
    spec = args.courses if args.courses is not None else settings.course_list
    selected = select_courses(entries, spec)
    if not selected:
        from .console import err

        err("没有选中任何课程")
        return 2

    return run_courses(
        settings,
        cookies,
        selected,
        jobs=settings.jobs,
        dry_run=args.dry_run,
        with_homework=args.with_homework or settings.homework_enable,
        auto_unblock=args.auto_unblock,
    )


def cmd_daemon(args) -> int:
    """盯梢刷课：被验证码拦住就等，解除后自动续刷，直到刷完或到上限"""
    from .console import info
    from .courses import list_courses, select_courses
    from .runner import run_daemon
    from .session_factory import new_session

    settings = _load_settings(args)
    _setup_logging(settings)
    cookies = _cookies_or_login(settings, allow_login=not args.no_login)

    entries = list_courses(new_session(settings, cookies))
    spec = args.courses or settings.course_list or "all"
    selected = select_courses(entries, spec)
    info(f"盯的课程：{'、'.join(e.label for e in selected)}")

    solve_fn = None
    if args.solve_captcha:
        from . import captcha as captcha_mod

        usable, why = captcha_mod.available()
        if usable:
            info("已开启自动点选验证码")
            _rac = next((getattr(e, "rac_id", "") for e in selected if getattr(e, "rac_id", "")), "")
            _sd = str(settings.state_dir)
            solve_fn = lambda: captcha_mod.solve(tries=4, quiet=True, rac_id=_rac, state_dir=_sd)[0]  # noqa: E731
        else:
            info(f"自动点选不可用，改为纯等待：{why.splitlines()[0]}")

    return run_daemon(
        settings,
        cookies,
        selected,
        jobs=max(1, min(32, args.jobs or settings.jobs)),
        every=max(5, args.every),
        max_cycles=max(0, args.cycles),
        max_hours=max(0.0, args.hours),
        solve_fn=solve_fn,
    )


def cmd_unblock(args) -> int:
    """打开网页播放页，等用户把验证码点掉，解除后自动开刷"""
    from .console import err, info, ok, say
    from .courses import collect_video_tasks, list_courses, select_courses
    from .runner import course_url, run_courses, wait_unblock
    from .session_factory import new_session

    settings = _load_settings(args)
    _setup_logging(settings)
    cookies = _cookies_or_login(settings, allow_login=not args.no_login)
    session = new_session(settings, cookies)

    entries = list_courses(session)
    spec = args.courses or settings.course_list or "1"
    selected = select_courses(entries, spec)
    target = next((e for e in selected if e.kind == "zhidao"), None)
    if target is None:
        err("没选中知到课程")
        return 2

    tasks = collect_video_tasks(target, session)
    pending = [t for t in tasks if not t.done] or tasks
    if not pending:
        ok("这门课没有待刷视频")
        return 0

    say(f"  课程播放页：{course_url(target.rac_id)}")
    if args.auto:
        from . import captcha as captcha_mod
        from .runner import probe as _probe

        usable, why = captcha_mod.available()
        if not usable:
            from .console import warn as _warn

            _warn(why)
        else:
            info("先试自动点掉验证码")
            solved, _out = captcha_mod.solve(
                tries=args.auto_tries, rac_id=target.rac_id, state_dir=str(settings.state_dir)
            )
            if solved:
                state = _probe(
                    settings, cookies, target.rac_id, pending[0].video_id, 12, quiet=True
                )
                if state == "ok":
                    ok("验证码已解除，可以正常上报")
                    if args.then_run:
                        return run_courses(
                            settings, cookies, selected, jobs=args.jobs or settings.jobs
                        )
                    return 0
                info("点过了但接口还是 -12，继续按人工流程等")

    cleared = wait_unblock(
        settings,
        cookies,
        target.rac_id,
        pending[0].video_id,
        wait=args.wait,
        interval=args.interval,
        open_browser=args.open,
    )
    if not cleared:
        warn_msg = "等了这么久还没解除：先在浏览器把验证码点掉，再跑 zhs-multi.bat probe -c 1"
        from .console import warn

        warn(warn_msg)
        return 3

    ok("可以正常上报了")
    if args.then_run:
        return run_courses(settings, cookies, selected, jobs=args.jobs or settings.jobs)
    info("现在可以跑： zhs-multi.bat run -c all -j 2")
    return 0


def cmd_status(args) -> int:
    """一眼看全：登录态、课程、每门课还剩多少视频、上一轮结果"""
    import json as _json
    from pathlib import Path as _P

    from .console import err, info, ok, rule, say, warn
    from .courses import collect_ai_tasks, collect_video_tasks, list_courses
    from .session_factory import load_cookies, new_session

    settings = _load_settings(args)
    _setup_logging(settings)
    say()
    rule("=")
    info(f"配置文件  : {settings.config_path}")
    info(f"线程/倍速 : {settings.jobs} / {settings.speed}（阈值 {settings.threshold}）")
    from . import stealth as _stealth

    _quota = _stealth.DailyQuota(settings.state_dir / "daily.json", settings.max_per_day)
    info(
        f"风控      : "
        + ("自适应开" if settings.adaptive else "自适应关")
        + (f"，每 {settings.rest_every} 条休息 {settings.rest_seconds}s" if settings.rest_every else "")
        + f"，每日 {_quota.describe()}"
    )

    cookies = load_cookies(settings.cookies_path)
    if not cookies:
        err("还没有登录态，先跑 zhs-multi.bat login")
        rule("=")
        return 2
    info(f"Cookie    : {len(cookies)} 条（{settings.cookies_path.name}）")

    session = new_session(settings, cookies)
    try:
        from zhs.api.session_auth import SessionStatus, verify_session

        status = verify_session(session)
        if status is SessionStatus.OK:
            ok("登录态有效")
        elif status is SessionStatus.EXPIRED:
            warn("登录态已过期，需要重新登录")
        else:
            warn("接口不可用，无法校验登录态")
    except Exception as exc:  # noqa: BLE001
        warn(f"登录态校验失败：{exc}")

    entries = list_courses(session)
    if not entries:
        warn("账号下没有读到课程")
        rule("=")
        return 3

    say()
    say(f"  课程 {len(entries)} 门：")
    total_pending = 0
    for e in entries:
        if e.kind == "zhidao":
            try:
                tasks = collect_video_tasks(e, session)
                pending = sum(1 for t in tasks if not t.done)
                total_pending += pending
                say(f"   {e.index:>2}) {e.label:<34} {len(tasks)} 个视频，待刷 {pending}，已完成 {len(tasks) - pending}")
            except Exception as exc:  # noqa: BLE001
                warn(f"   {e.index:>2}) {e.label} 章节读取失败：{exc}")
        elif e.kind == "ai":
            try:
                ai_tasks = collect_ai_tasks(e, session)
                pending = sum(1 for t in ai_tasks if not t.done)
                total_pending += pending
                say(
                    f"   {e.index:>2}) {e.label:<34} {len(ai_tasks)} 个知识点（含资料），"
                    f"待刷 {pending}，已完成 {len(ai_tasks) - pending}"
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"   {e.index:>2}) {e.label} 知识点读取失败：{exc}")
        elif e.kind == "polymas":
            try:
                from .polymas import PolymasClient, collect_tasks

                with PolymasClient(cookies, str(getattr(session, "uuid", "") or "")) as pc:
                    ptasks = collect_tasks(pc, e)
                    pstudy = pc.course_study(e.course_id, ptasks[0].term if ptasks else e.term)
                pending = sum(1 for t in ptasks if not t.done)
                total_pending += pending
                say(
                    f"   {e.index:>2}) {e.label:<34} {len(ptasks)} 个学习资源，"
                    f"待刷 {pending}，平台进度 {pstudy.get('mustStudyProgress')}%"
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"   {e.index:>2}) {e.label} 学习资源读取失败：{exc}")
        else:
            say(f"   {e.index:>2}) {e.label:<34} （暂不支持并发播放）")

    report = settings.state_dir / "last-run.json"
    if report.exists():
        try:
            data = _json.loads(report.read_text(encoding="utf-8"))
            c = data.get("counts", {})
            say()
            info(
                f"上一轮  : {data.get('time')} → 完成 {c.get('ok')}｜跳过 {c.get('skip')}"
                f"｜失败 {c.get('fail')}｜中止 {c.get('stopped')}（{data.get('seconds')}s）"
            )
            if data.get("stop_reason"):
                warn(f"停止原因：{data['stop_reason'].splitlines()[0]}")
        except Exception:
            pass

    daemon_state = settings.state_dir / "daemon.json"
    if daemon_state.exists():
        try:
            ds = _json.loads(daemon_state.read_text(encoding="utf-8"))
            from datetime import datetime as _dt

            stamp = _dt.fromisoformat(str(ds.get("time")))
            age = (_dt.now() - stamp).total_seconds()
            if age < 1800:
                phase = {"probe": "探测中", "running": "正在刷", "captcha": "被验证码拦住",
                         "solved": "刚点掉验证码", "idle": "准备下一轮", "done": "已完成",
                         "empty": "没任务", "auth": "登录态失效", "error": "探测失败",
                         "limit": "学习时长到顶"}.get(
                    str(ds.get("phase")), str(ds.get("phase")))
                info(
                    f"盯梢中   : 第 {ds.get('cycle')} 轮｜待刷 {ds.get('pending')}/{ds.get('total')}"
                    f"｜{phase}｜自动点选 {ds.get('solved')} 次"
                    f"｜{int(age)} 秒前"
                )
            else:
                info(f"盯梢    : 上次记录在 {int(age // 60)} 分钟前（可能已退出）")
        except Exception:
            pass

    say()
    if total_pending:
        ok(f"还有 {total_pending} 个视频要刷：zhs-multi.bat run -c all -j {settings.jobs}")
        info("一条命令全自动（被 -12 拦住自己点掉再续刷）："
             "zhs-multi.bat daemon -c 1 -j 1 --solve-captcha --every 120")
    else:
        ok("所有知到视频都已完成")
    rule("=")
    return 0


def cmd_selftest(args) -> int:
    from .runner import selftest

    if getattr(args, "all", False):
        rc = selftest(jobs=args.jobs or 4, tasks=args.tasks or 12)
        rc = max(rc, _run_tool_check("check_core.py"))
        rc = max(rc, _run_tool_check("check_ai.py"))
        rc = max(rc, _run_tool_check("check_polymas.py"))
        rc = max(rc, _run_tool_check("check_docs.py"))
        return rc
    if getattr(args, "core", False):
        return _run_tool_check("check_core.py")
    if getattr(args, "ai", False):
        return _run_tool_check("check_ai.py")
    if getattr(args, "polymas", False):
        return _run_tool_check("check_polymas.py")
    if getattr(args, "docs", False):
        return _run_tool_check("check_docs.py")

    return selftest(jobs=args.jobs or 4, tasks=args.tasks or 12)


def _run_tool_check(name: str) -> int:
    """跑 tools/ 下的离线自检脚本"""
    import importlib.util

    from .console import err, info, say

    path = _paths.tool_path(name)
    if not path.exists():
        err(f"找不到自检脚本：{path}")
        return 2
    say()
    info(f"离线自检：{name}")
    spec = importlib.util.spec_from_file_location(f"zhs_multi_{path.stem}", path)
    if spec is None or spec.loader is None:
        err("自检脚本加载失败")
        return 2
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def cmd_probe(args) -> int:
    """试播一条视频，判断验证码限制是否还在"""
    from .console import err, info, ok
    from .courses import collect_video_tasks, list_courses, select_courses
    from .runner import probe
    from .session_factory import new_session

    settings = _load_settings(args)
    _setup_logging(settings)
    cookies = _cookies_or_login(settings, allow_login=not args.no_login)
    session = new_session(settings, cookies)

    entries = list_courses(session)
    spec = args.courses or settings.course_list or "1"
    selected = select_courses(entries, spec)
    if not selected:
        err("没有选中课程")
        return 2

    for entry in selected:
        if entry.kind != "zhidao":
            info(f"{entry.label} 不是知到课程，跳过")
            continue
        tasks = collect_video_tasks(entry, session)
        pending = [t for t in tasks if not t.done] or tasks
        if not pending:
            info(f"{entry.label} 没有可试播的视频")
            continue
        rc = probe(settings, cookies, entry.rac_id, pending[0].video_id, args.seconds)
        if rc == "ok":
            ok("可以开始刷了")
            return 0
        if rc == "captcha":
            return 3
        return 4
    return 2


def cmd_captcha(args) -> int:
    """自动点掉知到播放页的易盾验证码（-12 那一关）"""
    from . import captcha as captcha_mod
    from .console import err, info, ok, rule, say, warn

    settings = _load_settings(args)
    _setup_logging(settings)

    usable, why = captcha_mod.available()
    if not usable:
        err(why)
        return 2
    info(why)
    if args.check:
        ok("自动点选可用（--check 只检查环境，不点）")
        return 0

    say()
    rule()
    info("打开播放页并尝试自动点掉验证码，最多 %d 轮" % args.tries)
    # 播放页要带课程 id：从课程列表里取第一门知到课的 racId（也可以 --rac 直接指定）
    rac = getattr(args, "rac", "") or ""
    if not rac:
        try:
            from .courses import list_courses
            from .session_factory import load_cookies, new_session

            cookies = load_cookies(settings.cookies_path)
            if cookies:
                entries = list_courses(new_session(settings, cookies))
                rac = next((e.rac_id for e in entries if e.rac_id), "")
        except Exception as exc:  # noqa: BLE001
            warn(f"读课程列表失败，播放页只能用电台默认课程：{exc}")
    if rac:
        info(f"播放页课程：{rac}")
    solved, out = captcha_mod.solve(
        tries=args.tries, min_score=args.min_score, rac_id=rac, state_dir=str(settings.state_dir)
    )
    if solved:
        ok("验证码已通过")
        return 0
    warn("这一轮没点过去，可以再跑一次，或手动在浏览器里点")
    if out:
        say(out)
    return 3


def cmd_doctor(args) -> int:
    import importlib

    from .console import err, info, ok, rule, say, warn

    settings = _load_settings(args)
    say()
    rule("=")
    info(f"Python      : {sys.version.split()[0]}")
    info(f"项目目录    : {PROJECT_DIR}")
    info(f"配置文件    : {settings.config_path}")
    info(f"Cookie      : {settings.cookies_path}（{'存在' if settings.cookies_path.exists() else '缺失'}）")
    info(f"线程数      : {settings.jobs}（错峰 {settings.stagger:g}s 启动）")
    info(f"倍速/阈值   : {settings.speed} / {settings.threshold}")
    info(f"时间上限    : {settings.limit or '不限'} 分钟")
    info(f"AI Key      : {'已配置 ' + settings.ai_model if settings.has_ai_key else '未配置（作业会随机作答）'}")
    info(
        f"智慧课程    : 选学资料{'刷' if settings.ai_learn_optional else '跳过'}"
        f" / 视频阈值 {settings.ai_video_threshold}"
    )

    for mod in ("zhs", "httpx", "pydantic", "loguru"):
        try:
            m = importlib.import_module(mod)
            ok(f"{mod:<10} {getattr(m, '__version__', 'ok')}")
        except Exception as exc:  # noqa: BLE001
            err(f"{mod} 导入失败：{exc}")

    try:
        import httpx

        r = httpx.get("https://passport.zhihuishu.com/login", timeout=10, follow_redirects=True)
        ok(f"passport 可达（HTTP {r.status_code}）")
    except Exception as exc:  # noqa: BLE001
        warn(f"passport 不可达：{exc}")

    if settings.cookies_path.exists():
        try:
            _cookies_or_login(settings)
        except SystemExit:
            pass
    rule("=")
    return 0


# ---------------------------------------------------------------- 参数解析


def build_parser() -> argparse.ArgumentParser:
    # 免安装版里入口叫 zhs-multi.exe，源码版里是 zhs-multi.bat
    entry = "zhs-multi.exe" if _paths.frozen() else "zhs-multi.bat"
    p = argparse.ArgumentParser(
        prog="zhs-multi",
        description="智慧树多线程刷课工具（知到 / Hike / AI）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            f"  {entry}                       # 交互式：自动登录 → 选课 → 刷\n"
            f"  {entry} login                 # 扫码登录（智慧树 APP 扫一扫）\n"
            f"  {entry} list                  # 看课程表\n"
            f"  {entry} run -j 8 -c 1,3 --dry-run\n"
            f"  {entry} run -c all -s 1.8 -l 60\n"
            f"  {entry} captcha               # 被 -12 拦住时自动点掉验证码\n"
            f"  {entry} daemon -c 1 --solve-captcha   # 盯梢 + 自动点验证码\n"
        ),
    )
    p.add_argument("-c", "--config", default="", help="配置文件路径（默认项目目录 config.ini）")
    sub = p.add_subparsers(dest="cmd")

    lp = sub.add_parser("login", help="登录（扫码；也可导入浏览器 Cookie）")
    lp.add_argument("--terminal-qr", action="store_true", help="在终端里直接画二维码")
    lp.add_argument("--cookie", default=None, help='手动导入 Cookie："name=value; name2=value2"')
    lp.add_argument("--cookie-file", default=None, help="从文件导入 Cookie（JSON 或 name=value 文本）")
    lp.set_defaults(func=cmd_login)

    ls = sub.add_parser("list", help="列出课程")
    ls.add_argument("--json", action="store_true", help="以 JSON 输出")
    ls.add_argument("--no-login", action="store_true", help="没有 Cookie 时不自动登录")
    ls.set_defaults(func=cmd_list)

    rp = sub.add_parser("run", help="多线程刷视频")
    rp.add_argument("-c", "--courses", default=None, help="课程序号：1 / 1,3 / 2-4 / all（留空=交互选择）")
    rp.add_argument("-j", "--jobs", type=int, default=0, help="并发线程数（默认取 config.ini）")
    rp.add_argument("-s", "--speed", type=float, default=0.0, help="倍速 1.0-2.0")
    rp.add_argument("--threshold", type=float, default=0.0, help="视频完成阈值 0.1-1.2")
    rp.add_argument("-l", "--limit", type=int, default=None, help="总时间上限（分钟，0=不限）")
    rp.add_argument("--dry-run", action="store_true", help="只列任务不动手")
    rp.add_argument("--force", action="store_true", help="已完成的也重刷")
    rp.add_argument("--with-homework", action="store_true", help="刷完视频接着做章节作业")
    rp.add_argument("--auto-unblock", action="store_true", help="撞到验证码时开浏览器等你点，解除后自动续刷")
    rp.add_argument("--no-dashboard", action="store_true", help="关闭总览状态行（改成逐条打印）")
    rp.add_argument("--no-login", action="store_true", help="没有 Cookie 时不自动登录")
    rp.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    rp.set_defaults(func=cmd_run)

    sp = sub.add_parser("selftest", help="并发自检（不联网）")
    sp.add_argument("-j", "--jobs", type=int, default=4)
    sp.add_argument("-n", "--tasks", type=int, default=12)
    sp.add_argument("--ai", action="store_true", help="智慧课程资料链路自检（离线假接口）")
    sp.add_argument("--polymas", action="store_true", help="AI 智课（polymas）刷课链路自检（离线假接口）")
    sp.add_argument("--core", action="store_true", help="核心链路自检（spec/config/编码/补跑）")
    sp.add_argument("--docs", action="store_true", help="文档与命令行一致性自检")
    sp.add_argument("--all", action="store_true", help="并发 + 核心 + 智慧课程 + AI 智课 + 文档 全跑一遍")
    sp.set_defaults(func=cmd_selftest)

    pp = sub.add_parser("probe", help="试播一条视频，检查验证码限制是否解除")
    pp.add_argument("-c", "--courses", default=None, help="课程序号（默认第一门）")
    pp.add_argument("-t", "--seconds", type=int, default=45, help="试播秒数（默认 45）")
    pp.add_argument("--no-login", action="store_true", help="没 Cookie 时不自动登录")
    pp.set_defaults(func=cmd_probe)

    up = sub.add_parser("unblock", help="打开播放页解锁验证码，解除后自动开刷")
    up.add_argument("-c", "--courses", default=None, help="课程序号（默认第一门）")
    up.add_argument("-j", "--jobs", type=int, default=0, help="解除后用的线程数")
    up.add_argument("-t", "--seconds", type=int, default=15, help="首次探测秒数")
    up.add_argument("--wait", type=int, default=900, help="最多等多少秒（默认 900）")
    up.add_argument("--interval", type=int, default=30, help="每隔多少秒检查一次（默认 30）")
    up.add_argument("--open", action="store_true", default=True, help="自动用浏览器打开播放页（默认开）")
    up.add_argument("--no-open", dest="open", action="store_false", help="不自动开浏览器")
    up.add_argument("--then-run", action="store_true", help="解除后立刻开刷")
    up.add_argument("--auto", action="store_true", help="先用浏览器自动点掉验证码（易盾点选）")
    up.add_argument("--auto-tries", type=int, default=4, help="自动点选最多几轮（默认 4）")
    up.add_argument("--no-login", action="store_true", help="没 Cookie 时不自动登录")
    up.set_defaults(func=cmd_unblock)

    dm = sub.add_parser("daemon", help="盯梢刷课：被验证码拦住就等，解除后自动续刷到刷完")
    dm.add_argument("-c", "--courses", default=None, help="课程序号（默认按配置/all）")
    dm.add_argument("-j", "--jobs", type=int, default=0, help="线程数（默认按配置）")
    dm.add_argument("--every", type=int, default=600, help="每轮间隔秒数（默认 600，最小 5）")
    dm.add_argument("--cycles", type=int, default=0, help="最多轮数（0=不限）")
    dm.add_argument("--hours", type=float, default=0.0, help="最多盯多少小时（0=不限）")
    dm.add_argument("--solve-captcha", action="store_true", help="被 -12 拦住时自动点掉验证码再续刷")
    dm.add_argument("--no-login", action="store_true", help="没 Cookie 时不自动登录")
    dm.set_defaults(func=cmd_daemon)

    cp = sub.add_parser("captcha", help="自动点掉播放页的易盾验证码（-12）")
    cp.add_argument("--tries", type=int, default=4, help="最多点几轮（默认 4）")
    cp.add_argument("--min-score", type=float, default=0.6, help="识别置信度下限（默认 0.6）")
    cp.add_argument("--check", action="store_true", help="只检查环境能不能用")
    cp.add_argument("--rac", default="", help="指定播放页的课程 id（默认自动取第一门知到课）")
    cp.set_defaults(func=cmd_captcha)

    dp = sub.add_parser("doctor", help="环境体检")
    dp.set_defaults(func=cmd_doctor)

    st = sub.add_parser("status", help="总览：登录态 / 课程 / 每门课待刷数量 / 上一轮结果")
    st.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        # 无子命令 = 默认交互式跑课
        args = parser.parse_args((argv or []) + ["run"])

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n  已中断")
        return 130
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001
        from .console import err

        err(f"{type(exc).__name__}: {exc}")
        if getattr(args, "verbose", False):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
