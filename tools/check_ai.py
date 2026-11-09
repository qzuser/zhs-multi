"""智慧课程（AI）资料刷课链路自检 —— 不联网、不需要账号里有 AI 课程

跑法：
    python tools\\check_ai.py
    zhs-multi.bat selftest --ai        # 等价入口

它用假的 AiCourseManager / AiVideoPlayer 顶替真实接口，验证三件事：
 1. 章节树 -> 知识点任务 的展开是否正确（含已完成判定）
 2. 一个知识点内的资料是否按 zhs 的 type/distribute 规则分类
 3. 文本 / PPT / 其它资源走 complete_resource，视频走 play_video；
    选学资源（resourcesSyncType != 1）是否按 learn_optional 开关过滤
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load  # noqa: E402
from app.courses import (  # noqa: E402
    AI_RES_OTHER,
    AI_RES_PPT,
    AI_RES_TEXT,
    AI_RES_VIDEO,
    AiTask,
    CourseEntry,
    classify_ai_resource,
    collect_ai_tasks,
)

# ---------------------------------------------------------------- 假数据结构


class FakeDetail:
    def __init__(self, uid, name, r_type, r_dist, sync=1, file_id=0, url=""):
        self.resources_uid = uid
        self.resources_name = name
        self.resources_type = r_type
        self.resources_distribute_type = r_dist
        self.resources_sync_type = sync
        self.resources_file_id = file_id
        self.resources_url = url


class FakeResource:
    def __init__(self, detail, status=0, schedule=0):
        self.resources_detail = detail
        self.study_status = status
        self.schedule = schedule


class FakeKnowledge:
    def __init__(self, kid, name, progress):
        self.knowledge_id = kid
        self.knowledge_name = name
        self.study_progress = progress
        self.free_exam = 0


class FakeTheme:
    def __init__(self, name, knowledge):
        self.theme_name = name
        self.knowledge_list = knowledge


class FakeInfo:
    def __init__(self):
        self.course_name = "假智慧课程"
        self.cake_theme_list = [
            FakeTheme("主题一", [FakeKnowledge(101, "知识点A", 0), FakeKnowledge(102, "知识点B", 101)]),
            FakeTheme("主题二", [FakeKnowledge(201, "知识点C", 40)]),
        ]


RESOURCES = {
    101: [
        FakeResource(FakeDetail(1001, "导学文本", 2, 1)),
        FakeResource(FakeDetail(1002, "课件PPT", 1, 4, url="http://x/y.pptx")),
        FakeResource(FakeDetail(1003, "教学视频", 1, 3, file_id=8888), schedule=30),
        FakeResource(FakeDetail(1004, "选学资料", 2, 1, sync=2)),
        FakeResource(FakeDetail(1005, "已完成文本", 2, 1), status=1),
        FakeResource(FakeDetail(1006, "没文件的视频", 1, 3, file_id=0)),
        FakeResource(FakeDetail(1007, "奇怪资源", 9, 9)),
    ],
    201: [FakeResource(FakeDetail(2001, "补充阅读", 2, 1))],
}


class FakeManager:
    def __init__(self, session=None, reporter=None):
        self.completed: list[tuple[int, int, int]] = []
        self.listed: list[int] = []

    def get_knowledge_points(self, course_id, class_id):
        return FakeInfo()

    def list_knowledge_resources(self, course_id, class_id, knowledge_id):
        self.listed.append(knowledge_id)
        return RESOURCES.get(knowledge_id, [])

    def complete_resource(self, course_id, class_id, knowledge_id, resources_uid):
        self.completed.append((knowledge_id, resources_uid, class_id))


class FakePlayer:
    def __init__(self, session=None, speed=1.5):
        self.played: list[tuple[int, int, int, int]] = []

    def play_video(self, course_id, class_id, file_id, knowledge_id, schedule=0, speed=None, threshold=100):
        self.played.append((knowledge_id, file_id, schedule, threshold))


MGR = FakeManager()
PLAYER = FakePlayer()


def install_fakes() -> None:
    """把 zhs.ai 里的两个类换成本地假实现"""
    import zhs.ai.course as ai_course
    import zhs.ai.video as ai_video

    ai_course.AiCourseManager = FakeManager  # type: ignore[assignment]
    ai_video.AiVideoPlayer = FakePlayer  # type: ignore[assignment]


def main() -> int:
    install_fakes()
    settings = load(Path(__file__).resolve().parents[1] / "config.ini",
                    project_dir=Path(__file__).resolve().parents[1])
    settings.delay = 0.0
    fails: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(("  [OK] " if cond else "  [!!] ") + label)
        if not cond:
            fails.append(label)

    # 1) 分类规则
    print("1) 资料类型映射")
    check(classify_ai_resource(FakeDetail(1, "t", 2, 1)) == AI_RES_TEXT, "文本 type=2/dist=1")
    check(classify_ai_resource(FakeDetail(1, "t", 1, 4)) == AI_RES_PPT, "PPT type=1/dist=4")
    check(classify_ai_resource(FakeDetail(1, "t", 1, 3)) == AI_RES_VIDEO, "视频 type=1/dist=3")
    check(classify_ai_resource(FakeDetail(1, "t", 2, 2)) == AI_RES_VIDEO, "课程视频 type=2/dist=2")
    check(classify_ai_resource(FakeDetail(1, "t", 9, 9)) == AI_RES_OTHER, "其它 type=9/dist=9")

    # 2) 任务展开
    print("2) 知识点展开")
    entry = CourseEntry(index=1, kind="ai", name="假智慧课程", course_id="9001", class_id="77")
    tasks = collect_ai_tasks(entry, session=None)
    check(len(tasks) == 3, f"展开出 3 个知识点（实际 {len(tasks)}）")
    check([t.knowledge_id for t in tasks] == [101, 102, 201], "知识点顺序与章节树一致")
    check([t.done for t in tasks] == [False, True, False], "studyProgress>=101 判为已完成")
    check(tasks[0].theme == "主题一" and tasks[2].theme == "主题二", "主题归属正确")
    check(tasks[0].video_id == 101, "video_id 复用 knowledge_id（供统一去重）")

    # 3) 知识点内部资料处理
    print("3) 资料处理（learn_optional=True）")
    from app.runner import Worker

    settings.ai_learn_optional = True
    settings.ai_video_threshold = 90
    task = tasks[0]
    w = Worker(settings, [], {})
    w._session = object()  # 跳过真实建连
    w._ensure_ai()
    MGR.completed.clear()
    PLAYER.played.clear()
    w._ai_mgr = MGR
    w._ai_player = PLAYER
    w._play_ai(task)

    done_uids = sorted(u for _, u, _ in MGR.completed)
    check(1001 in done_uids, "文本走 complete_resource")
    check(1002 in done_uids, "PPT 走 complete_resource")
    check(1004 in done_uids, "选学资料被刷（learn_optional=True）")
    check(1007 in done_uids, "未知类型兜底 complete_resource")
    check(1006 in done_uids, "fileId=0 的视频降级为 complete_resource")
    check(1005 not in done_uids, "已完成的资料不重复提交")
    check([p[1] for p in PLAYER.played] == [8888], "真视频走 play_video")
    check(PLAYER.played[0][2] == 30, "续播位置 schedule 透传")
    check(PLAYER.played[0][3] == 90, "完成阈值透传 ai_video_threshold")
    check(task.materials == 6, f"待刷资料计数 = 6（实际 {task.materials}）")

    # 4) 关掉选学
    print("4) 资料处理（learn_optional=False）")
    settings.ai_learn_optional = False
    MGR.completed.clear()
    PLAYER.played.clear()
    task2 = AiTask(
        course_index=1, course_label="[AI] 假智慧课程", course_id=9001, class_id=77,
        theme="主题一", knowledge_id=101, name="知识点A", progress=0, done=False,
    )
    w._play_ai(task2)
    done_uids2 = sorted(u for _, u, _ in MGR.completed)
    check(1004 not in done_uids2, "选学资料被跳过")
    check(1001 in done_uids2 and 1002 in done_uids2, "必修资料照常刷")
    check(task2.materials == 5, f"计数 = 5（实际 {task2.materials}）")

    # 5) 并发池能同时跑两种任务
    print("5) 混合任务并发（1 知到 + 1 智慧课程）")
    from app.courses import VideoTask

    calls: list[str] = []

    class RecWorker:
        def __init__(self, settings, cookies, shared=None):
            pass

        def play(self, t):
            calls.append(getattr(t, "kind", "zhidao"))

    import app.runner as runner_mod

    original = runner_mod.Worker
    runner_mod.Worker = RecWorker  # type: ignore[assignment]
    try:
        mixed = [
            VideoTask(1, "[知到] 课", "RAC", 5, "视频", "章", "节", False),
            AiTask(2, "[AI] 课", 9001, 77, "主题一", 101, "知识点A", 0, False),
        ]
        summ = runner_mod.run_tasks(settings, [], mixed, 2)
    finally:
        runner_mod.Worker = original  # type: ignore[assignment]
    check(summ.done == 2, f"两条任务都完成（实际 {summ.done}）")
    check(sorted(calls) == ["ai", "zhidao"], f"分发到两种处理器（实际 {calls}）")

    print()
    if fails:
        print(f"[!!] {len(fails)} 项未通过：" + "；".join(fails))
        return 1
    print("[OK] 智慧课程资料刷课链路自检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
