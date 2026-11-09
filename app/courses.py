"""课程枚举与选择"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from .console import err, info, ok, say, warn

KIND_LABEL = {"zhidao": "知到", "hike": "Hike", "ai": "AI", "polymas": "AI智课"}

# polymas 平台（知到 App 里「AI 智课 / 学习资料」那一类课）的学生课程列表接口。
# 它不吃 CAS 门票，只认 uuid，所以不带加密参数也能查。
POLYMAS_COURSE_URL = (
    "https://cloudapi.polymas.com/student-course/student/index/teacherQueryStudentCourse"
)


@dataclass
class CourseEntry:
    index: int
    kind: str
    name: str
    course_id: str
    class_id: str = ""
    rac_id: str = ""          # 知到 recruitAndCourseId
    recruit_id: int = 0
    term: int = 0             # polymas 的学期号
    videos: list["VideoTask"] = field(default_factory=list)
    unsupported: str = ""     # 非空 = 能看见但这套工具还刷不了，值是原因

    @property
    def label(self) -> str:
        return f"[{KIND_LABEL.get(self.kind, self.kind)}] {self.name}"

    @property
    def brushable(self) -> bool:
        return not self.unsupported


@dataclass
class VideoTask:
    course_index: int
    course_label: str
    rac_id: str
    video_id: int
    name: str
    chapter: str
    lesson: str
    done: bool
    kind: str = "zhidao"


# ---- AI 智慧课程：一个知识点 = 一个任务，内部逐条刷「学习资料」 ----

AI_RES_TEXT = "文本"
AI_RES_PPT = "PPT"
AI_RES_VIDEO = "视频"
AI_RES_OTHER = "资源"


@dataclass
class AiTask:
    """AI 智慧课程的知识点任务

    字段名对齐 VideoTask（rac_id / video_id / chapter），这样并发池、统计、
    报告都能复用同一套代码；`knowledge_id` 才是真正的主键。
    """

    course_index: int
    course_label: str
    course_id: int
    class_id: int
    theme: str
    knowledge_id: int
    name: str
    progress: int
    done: bool
    materials: int = 0          # 知识点下的资料条数（展开时才知道，先 0）
    kind: str = "ai"
    rac_id: str = ""
    video_id: int = 0           # = knowledge_id，供统一去重/计数
    chapter: str = ""
    lesson: str = ""

    def __post_init__(self) -> None:
        if not self.video_id:
            self.video_id = self.knowledge_id
        if not self.chapter:
            self.chapter = self.theme


def classify_ai_resource(detail: Any) -> str:
    """把 AI 课程资源映射成人类可读类型（对齐 zhs 的 type/distribute 规则）"""
    r_type = getattr(detail, "resources_type", 0)
    r_dist = getattr(detail, "resources_distribute_type", 0)
    if r_type == 2 and r_dist == 1:
        return AI_RES_TEXT
    if r_type == 1 and r_dist == 4:
        return AI_RES_PPT
    if (r_type == 1 and r_dist == 3) or (r_type == 2 and r_dist == 2):
        return AI_RES_VIDEO
    return AI_RES_OTHER


def list_polymas_courses(session: Any) -> list[dict[str, Any]]:
    """读 polymas 的 AI 智课（知到 App「学习资料」里那一类）

    这个平台的接口只认 cookie 里的 uuid（CASLOGC 解出来的那个），不认 CAS 门票，
    也不需要浏览器 SSO，所以直接 POST 明文 JSON。

    坑：这些 cookie 的域是 zhihuishu.com，用 httpx 的域匹配往 polymas.com 发会
    一个都不带（返回 401 拒绝访问）。所以这里手动摊平成无域的键值对。
    """
    uuid = str(getattr(session, "uuid", "") or "")
    if not uuid:
        return []
    try:
        jar = {name: value for name, value in session.cookies.items()}
    except Exception:  # noqa: BLE001
        return []
    if not jar:
        return []

    body = {
        "term": "",
        "status": 0,
        "keyword": "",
        "crateCourseType": "",
        "page": 1,
        "size": 50,
        "allCourse": True,
        "uuid": uuid,
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://hike-teaching-center.polymas.com",
        "Referer": "https://hike-teaching-center.polymas.com/",
        "uuid": uuid,
    }
    try:
        with httpx.Client(timeout=20, cookies=jar) as client:
            resp = client.post(POLYMAS_COURSE_URL, content=json.dumps(body), headers=headers)
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        warn(f"polymas 课程列表请求失败：{type(exc).__name__}: {exc}")
        return []

    if not isinstance(payload, dict) or not payload.get("success"):
        code = payload.get("code") if isinstance(payload, dict) else "?"
        msg = payload.get("msg") if isinstance(payload, dict) else ""
        warn(f"polymas 课程列表返回异常：code={code} msg={msg}")
        return []
    rows = (payload.get("data") or {}).get("list") or []
    return [r for r in rows if isinstance(r, dict)]


def list_courses(session: Any) -> list[CourseEntry]:
    """列出账号下所有课程（知到 / Hike / 智慧课程 / AI 智课）"""
    entries: list[CourseEntry] = []
    idx = 0

    # 知到
    try:
        from zhs.zhidao.course import ZhidaoCourseManager
        from zhs.zhidao.models import extract_course_id

        for c in ZhidaoCourseManager(session).get_course_list():
            idx += 1
            entries.append(
                CourseEntry(
                    index=idx,
                    kind="zhidao",
                    name=c.course_name,
                    course_id=str(extract_course_id(c)),
                    rac_id=c.secret,
                    recruit_id=int(c.recruit_id or 0),
                )
            )
    except Exception as exc:
        warn(f"读取知到课程失败：{exc}")

    # Hike
    try:
        from zhs.hike.course import HikeCourseManager

        for c in HikeCourseManager(session).get_course_list():
            idx += 1
            entries.append(
                CourseEntry(index=idx, kind="hike", name=c.course_name, course_id=str(c.course_id))
            )
    except Exception as exc:
        warn(f"读取 Hike 课程失败：{exc}")

    # AI 智慧课程
    try:
        from zhs.ai.course import AiCourseManager

        for c in AiCourseManager(session).get_ai_course_list():
            idx += 1
            entries.append(
                CourseEntry(
                    index=idx,
                    kind="ai",
                    name=str(c.get("courseName") or "(未命名)"),
                    course_id=str(c.get("courseId") or ""),
                    class_id=str(c.get("classId") or ""),
                )
            )
    except Exception as exc:
        warn(f"读取 AI 课程失败：{exc}")

    # AI 智课（polymas 平台，知到 App「学习资料」里那一类）
    try:
        for c in list_polymas_courses(session):
            idx += 1
            terms = c.get("terms") or []
            entries.append(
                CourseEntry(
                    index=idx,
                    kind="polymas",
                    name=str(c.get("courseName") or "(未命名)"),
                    course_id=str(c.get("courseId") or ""),
                    class_id=str(
                        ((c.get("studentClasses") or [{}])[0] or {}).get("classId") or ""
                    ),
                    term=int(terms[0]) if terms else 0,
                )
            )
    except Exception as exc:
        warn(f"读取 AI 智课失败：{exc}")

    return entries


def render_courses(entries: list[CourseEntry]) -> None:
    if not entries:
        err("账号下没有可用课程")
        return
    say()
    brushable = sum(1 for e in entries if e.brushable)
    say(f"  账号下共 {len(entries)} 门课，可刷 {brushable} 门：")
    for e in entries:
        extra = f"courseId={e.course_id}"
        if e.kind == "ai":
            extra += f" classId={e.class_id}"
        if e.kind == "polymas":
            extra += f" classId={e.class_id}" if e.class_id else ""
        if e.kind == "zhidao":
            extra += f" racId={e.rac_id}"
        mark = "" if e.brushable else "  ← 暂不支持"
        say(f"   {e.index:>2}) {e.label:<38} {extra}{mark}")
    pending = [e for e in entries if not e.brushable]
    if pending:
        say()
        for e in pending:
            warn(f"{e.label}：{e.unsupported}")
    say()


def parse_spec(spec: str, total: int) -> list[int]:
    """解析 "1,3,5-7" / "all" / "1 2 3" """
    spec = (spec or "").strip()
    if not spec:
        return []
    if spec.lower() in ("all", "*", "全部"):
        return list(range(1, total + 1))
    picked: set[int] = set()
    for chunk in re.split(r"[,，\s]+", spec):
        if not chunk:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", chunk)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            picked.update(range(min(start, end), max(start, end) + 1))
            continue
        if chunk.isdigit():
            picked.add(int(chunk))
            continue
        raise ValueError(f"无法识别的课程选择：{chunk}")
    bad = [i for i in picked if i < 1 or i > total]
    if bad:
        raise ValueError(f"序号越界：{bad}（有效范围 1-{total}）")
    return sorted(picked)


def select_courses(entries: list[CourseEntry], spec: str = "") -> list[CourseEntry]:
    """按 spec 选择课程；spec 为空且可交互时提示输入"""
    if not entries:
        return []

    if not spec:
        if not sys.stdin.isatty():
            warn("未指定课程且当前不可交互，默认全选")
            return [e for e in entries if e.brushable]
        render_courses(entries)
        info("输入序号：如 1  或  1,3  或  2-4  或  all（回车 = 全选）")
        try:
            raw = input("  选择 > ").strip()
        except EOFError:
            raw = ""
        spec = raw or "all"

    chosen = parse_spec(spec, len(entries))
    result = [e for e in entries if e.index in chosen]
    skipped = [e for e in result if not e.brushable]
    if skipped:
        for e in skipped:
            warn(f"{e.label} 刷不了，已跳过：{e.unsupported}")
        result = [e for e in result if e.brushable]
    if result:
        ok(f"已选 {len(result)} 门课程：" + "、".join(e.name for e in result))
    else:
        warn("没有可刷的课程")
    return result


def collect_video_tasks(entry: CourseEntry, session: Any, ctx: Any = None) -> list[VideoTask]:
    """把知到课程展开成视频任务列表"""
    tasks: list[VideoTask] = []
    if entry.kind != "zhidao":
        return tasks

    if ctx is None:
        from zhs.zhidao.course import ZhidaoCourseManager

        ctx = ZhidaoCourseManager(session).get_context(entry.rac_id)
    for chapter in ctx.chapters:
        for lesson in chapter.video_lessons:
            for small in lesson.video_small_lessons:
                tasks.append(
                    VideoTask(
                        course_index=entry.index,
                        course_label=entry.label,
                        rac_id=entry.rac_id,
                        video_id=small.video_id,
                        name=small.name or lesson.name,
                        chapter=chapter.name,
                        lesson=lesson.name,
                        done=small.watch_state == 1,
                    )
                )
    return tasks


def collect_ai_tasks(entry: CourseEntry, session: Any) -> list[AiTask]:
    """把 AI 智慧课程展开成知识点任务列表

    一个知识点下的「学习资料」（文本 / PPT / 视频 / 其它资源）由 worker 逐条刷完，
    这样知识点之间可以并发，而同一知识点内部仍然按原顺序串行——和平台自己的
    study_progress 计算方式一致。
    """
    if entry.kind != "ai":
        return []
    if not str(entry.course_id).strip().isdigit() or not str(entry.class_id).strip().isdigit():
        warn(f"{entry.label} 缺少 courseId/classId，跳过")
        return []
    from zhs.ai.course import AiCourseManager

    info_course = AiCourseManager(session).get_knowledge_points(
        int(entry.course_id), int(entry.class_id)
    )
    tasks: list[AiTask] = []
    for theme in info_course.cake_theme_list:
        for k in theme.knowledge_list:
            tasks.append(
                AiTask(
                    course_index=entry.index,
                    course_label=entry.label,
                    course_id=int(entry.course_id),
                    class_id=int(entry.class_id),
                    theme=theme.theme_name,
                    knowledge_id=int(k.knowledge_id),
                    name=k.knowledge_name,
                    progress=int(k.study_progress),
                    done=k.study_progress >= 101,
                )
            )
    return tasks


def list_ai_resources(session: Any, task: AiTask) -> list[Any]:
    """读一个知识点下的资料列表（失败返回空，交给上层记失败）"""
    from zhs.ai.course import AiCourseManager

    return AiCourseManager(session).list_knowledge_resources(
        task.course_id, task.class_id, task.knowledge_id
    )
