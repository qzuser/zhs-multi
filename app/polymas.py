"""polymas 平台（AI 智课）的「学习资料」刷课

知到 App 里那类 **AI 智课**（courseType=hikeAiCourse）不在知到共享课里，
走的是 polymas 平台。它的课程内容是一棵「学习资源树」：视频 / 课件 / 文档，
每个资源有 completeRate（0~100），到 100 就 isFinished。

刷的原理：视频播放器每 30 秒往
    POST /student-course/study/student/record/report
上报一个窗口 {startWatchTime, endWatchTime, studyTotalTime}，
服务端按窗口累计时长算 completeRate。我们直接照着窗口逐个上报即可，
不需要真的把视频播一遍。

鉴权：只要 cookie（CASLOGC 里解出的 uuid）。注意这些 cookie 的域是
zhihuishu.com，用 httpx 的域匹配往 polymas.com 发会一个都不带（401），
所以这里手动摊平成无域键值对。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx

from .console import err, info, ok, say, warn

API = "https://cloudapi.polymas.com"
WINDOW = 30          # 平台自己的上报窗口，固定 30 秒
MAX_WINDOWS = 4000   # 单个资源的兜底上限（约 33 小时视频）
MOBILE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class PolymasTask:
    """一个学习资源 = 一个任务"""

    course_index: int
    course_label: str
    course_id: str
    term: int
    class_id: str
    folder_id: str
    resource_id: str
    title: str
    suffix: str
    duration: int
    end_watch: int
    rate: float
    done: bool

    @property
    def label(self) -> str:
        return f"{self.title}"


class PolymasError(RuntimeError):
    pass


class PolymasClient:
    """polymas 学生端 API"""

    def __init__(self, cookies: list[dict[str, Any]] | dict[str, str], uuid: str = "") -> None:
        if isinstance(cookies, dict):
            jar = dict(cookies)
        else:
            jar = {c["name"]: c["value"] for c in cookies}
        self.uuid = uuid or _uuid_from_cookies(jar)
        if not jar:
            raise PolymasError("没有 cookie，先扫码登录")
        self._client = httpx.Client(
            timeout=25,
            cookies=jar,
            headers={
                "User-Agent": MOBILE_UA,
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://hike-teaching-center.polymas.com",
                "Referer": "https://hike-teaching-center.polymas.com/",
                "uuid": self.uuid,
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PolymasClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 底层 ----
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(API + path, content=json.dumps(payload))
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise PolymasError(f"{path} 返回的不是 JSON（{resp.status_code}）") from exc
        if not isinstance(data, dict) or not data.get("success"):
            code = data.get("code") if isinstance(data, dict) else "?"
            msg = data.get("msg") if isinstance(data, dict) else ""
            raise PolymasError(f"{path} 失败：code={code} msg={msg}")
        return data

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.get(API + path, params=params)
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise PolymasError(f"{path} 返回的不是 JSON（{resp.status_code}）") from exc
        if not isinstance(data, dict) or not data.get("success"):
            code = data.get("code") if isinstance(data, dict) else "?"
            msg = data.get("msg") if isinstance(data, dict) else ""
            raise PolymasError(f"{path} 失败：code={code} msg={msg}")
        return data

    # ---- 课程 ----
    def list_courses(self) -> list[dict[str, Any]]:
        body = {
            "term": "",
            "status": 0,
            "keyword": "",
            "crateCourseType": "",
            "page": 1,
            "size": 50,
            "allCourse": True,
            "uuid": self.uuid,
            "date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        data = self._post("/student-course/student/index/teacherQueryStudentCourse", body)
        rows = (data.get("data") or {}).get("list") or []
        return [r for r in rows if isinstance(r, dict)]

    def term_of(self, course_id: str) -> int:
        data = self._get("/student-course/student/term/list", {})
        rows = data.get("data") or []
        for r in rows:
            if r.get("isCurrent"):
                return int(r.get("term") or 0)
        return int(rows[0].get("term") or 0) if rows else 0

    def class_of(self, course_id: str, term: int) -> str:
        data = self._post(
            "/student-course/student/classList", {"courseId": course_id, "term": term}
        )
        rows = data.get("data") or []
        return str(rows[0].get("classId") or "") if rows else ""

    def library_folder(self, course_id: str, term: int) -> str:
        data = self._get(
            "/teacher-course/library/course/findStudyLibraryByCourseId",
            {"courseId": course_id, "term": term},
        )
        return str(data.get("data") or "")

    def course_study(self, course_id: str, term: int) -> dict[str, Any]:
        data = self._get(
            "/student-course/study/student/resource/courseStudy",
            {"courseId": course_id, "term": term},
        )
        return data.get("data") or {}

    # ---- 资源 ----
    def resource_tree(self, course_id: str, term: int, class_id: str) -> list[dict[str, Any]]:
        folder = self.library_folder(course_id, term)
        if not folder:
            raise PolymasError("这门课没有学习资源库")
        data = self._post(
            "/student-course/study/student/resource/queryTree",
            {
                "libraryFolderId": folder,
                "classId": class_id,
                "courseId": course_id,
                "term": term,
            },
        )
        return data.get("data") or []

    def resource_detail(
        self, course_id: str, folder_id: str, resource_id: str, term: int
    ) -> dict[str, Any]:
        data = self._post(
            "/student-course/study/student/resource/detail",
            {
                "courseId": course_id,
                "libraryFolderId": folder_id,
                "resourceId": resource_id,
                "term": term,
            },
        )
        return data.get("data") or {}

    def report(
        self,
        course_id: str,
        resource_id: str,
        start: int,
        end: int,
        client_type: str = "Windows",
    ) -> dict[str, Any]:
        now = datetime.now()
        body = {
            "startWatchTime": start,
            "endWatchTime": end,
            "startDate": (now + timedelta(seconds=start)).strftime("%Y-%m-%d %H:%M:%S"),
            "endDate": (now + timedelta(seconds=end)).strftime("%Y-%m-%d %H:%M:%S"),
            "studyTotalTime": end - start,
            "clientType": client_type,
            "courseId": course_id,
            "resourceId": resource_id,
        }
        data = self._post("/student-course/study/student/record/report", body)
        return data.get("data") or {}


def _uuid_from_cookies(jar: dict[str, str]) -> str:
    """从 CASLOGC（URL 编码的 JSON）里解 uuid"""
    raw = jar.get("CASLOGC") or ""
    if not raw:
        return ""
    from urllib.parse import unquote

    try:
        obj = json.loads(unquote(raw))
    except Exception:  # noqa: BLE001
        return ""
    return str(obj.get("uuid") or "")


def _walk_leaves(nodes: list[dict[str, Any]], out: list[dict[str, Any]]) -> None:
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == 2:
            out.append(n)
        _walk_leaves(n.get("children") or [], out)


def collect_tasks(client: PolymasClient, entry: Any) -> list[PolymasTask]:
    """把一门 AI 智课展开成资源任务列表"""
    term = int(getattr(entry, "term", 0) or 0) or client.term_of(entry.course_id)
    class_id = getattr(entry, "class_id", "") or client.class_of(entry.course_id, term)
    folder = client.library_folder(entry.course_id, term)
    if not folder:
        raise PolymasError("这门课没有学习资源库")
    tree = client.resource_tree(entry.course_id, term, class_id)

    leaves: list[dict[str, Any]] = []
    _walk_leaves(tree, leaves)

    tasks: list[PolymasTask] = []
    for n in leaves:
        det = n.get("detail") or {}
        rid = str(n.get("nodeId") or det.get("bizId") or "")
        if not rid:
            continue
        title = str(det.get("title") or det.get("noSuffixResourceName") or rid)
        rate = float(n.get("completePercentage") or 0.0)
        tasks.append(
            PolymasTask(
                course_index=entry.index,
                course_label=entry.label,
                course_id=entry.course_id,
                term=term,
                class_id=class_id,
                folder_id=folder,
                resource_id=rid,
                title=title,
                suffix=str(det.get("bizSuffix") or ""),
                duration=0,
                end_watch=0,
                rate=rate,
                done=bool(n.get("isFinished")) or rate >= 100.0,
            )
        )
    return tasks


def brush_task(
    client: PolymasClient,
    task: PolymasTask,
    step_delay: float = 0.2,
    on_tick: Callable[[float], None] | None = None,
    force: bool = False,
) -> str:
    """把单个资源刷到 100%

    返回 "ok"（到 100）/ "skip"（本来就完了或没有时长）/ "part"（没到 100）
    """
    detail = client.resource_detail(task.course_id, task.folder_id, task.resource_id, task.term)
    if detail.get("isFinished") and not force:
        return "skip"
    duration = int(float(detail.get("duration") or 0))
    if duration <= 0:
        return "skip"  # 课件/文档没有时长，靠打开就完成，不在这里处理
    end = int(float(detail.get("endWatchTime") or 0))
    rate = float(detail.get("completeRate") or task.rate or 0.0)
    if end >= duration:
        # 时长已经报满，剩下的百分比是平台的取整差，补不动
        task.rate = rate
        task.end_watch = end
        return "ok" if rate >= 100.0 else "skip"

    guard = min(MAX_WINDOWS, duration // WINDOW + 8)
    while end < duration and guard > 0:
        guard -= 1
        nxt = min(end + WINDOW, duration)
        data = client.report(task.course_id, task.resource_id, end, nxt)
        rate = float(data.get("completeRate") or rate)
        end = int(float(data.get("studyTotalTime") or nxt))
        if on_tick is not None:
            try:
                on_tick(rate)
            except Exception:  # noqa: BLE001,S110
                pass
        if rate >= 100.0:
            break
        if guard and step_delay > 0:
            time.sleep(step_delay)

    task.rate = rate
    task.end_watch = end
    if rate >= 100.0:
        task.done = True
        return "ok"
    return "part"


def run_entries(
    settings: Any,
    cookies: list[dict[str, Any]],
    entries: list[Any],
    uuid: str = "",
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, int]:
    """刷若干门 AI 智课，返回统计"""
    stats = {"ok": 0, "skip": 0, "part": 0, "fail": 0}
    targets = [e for e in entries if getattr(e, "kind", "") == "polymas"]
    if not targets:
        return stats

    step_delay = max(0.05, float(getattr(settings, "delay", 0.6) or 0.6) / 3.0)
    try:
        client = PolymasClient(cookies, uuid)
    except PolymasError as exc:
        err(f"AI 智课初始化失败：{exc}")
        return stats

    try:
        for entry in targets:
            info(f"展开 AI 智课：{entry.label} …")
            try:
                tasks = collect_tasks(client, entry)
            except Exception as exc:  # noqa: BLE001
                err(f"读取学习资源失败：{entry.label} — {exc}")
                stats["fail"] += 1
                continue
            pending = [t for t in tasks if force or not t.done]
            info(
                f"  {entry.label}：{len(tasks)} 个学习资源，"
                f"已完成 {sum(1 for t in tasks if t.done)} 个，待刷 {len(pending)} 个"
            )
            if dry_run:
                for t in pending:
                    say(f"    · {t.suffix or '?'}  {(t.rate):>5.1f}%  {t.title[:60]}")
                continue
            if not pending:
                ok("  这门课的学习资源都完成了")
                continue

            for i, t in enumerate(pending, 1):
                info(f"  [{i}/{len(pending)}] {t.title[:56]}")
                try:
                    result = brush_task(client, t, step_delay=step_delay, force=force)
                except Exception as exc:  # noqa: BLE001
                    warn(f"    失败：{type(exc).__name__}: {exc}")
                    stats["fail"] += 1
                    continue
                stats[result] = stats.get(result, 0) + 1
                if result == "ok":
                    ok(f"    {t.rate:.0f}%  完成")
                elif result == "skip":
                    info(f"    跳过（{t.rate:.1f}%，没有时长可补或已完成）")
                else:
                    warn(f"    只到 {t.rate:.1f}%，可能是平台限速")
            try:
                term_now = tasks[0].term if tasks else int(getattr(entry, "term", 0) or 0)
                summary = client.course_study(entry.course_id, term_now)
            except Exception:  # noqa: BLE001
                summary = {}
            if summary:
                say()
                ok(
                    f"  {entry.label} 总进度 {summary.get('totalProgress')}%"
                    f"（{summary.get('hasStudyCount')}/{summary.get('totalResourceNum')} 个资源）"
                )
    finally:
        client.close()
    return stats
