"""AI 智课（polymas）刷课链路自检 —— 不联网、不需要账号里有这门课

跑法：
    python tools\\check_polymas.py
    zhs-multi.bat selftest --polymas        # 等价入口

用 httpx.MockTransport 顶替真实接口，验证四件事：
 1. CASLOGC -> uuid 的解析
 2. 学习资源树 -> 任务列表（type=1 是目录、type=2 是资源；isFinished 判定）
 3. 30 秒窗口补报：0-30-60-…-duration，到 100% 就停，且请求体字段齐全
 4. 已完成的资源不重复上报（force 才补）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.polymas import (  # noqa: E402
    WINDOW,
    PolymasClient,
    PolymasTask,
    _uuid_from_cookies,
    brush_task,
    collect_tasks,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [!!] {name}  {detail}")


# ------------------------------------------------------------------ 假后端

TREE = [
    {
        "type": 1,
        "nodeId": "ch1",
        "resourceNum": 2,
        "isFinished": False,
        "children": [
            {
                "type": 2,
                "nodeId": "res-done",
                "isFinished": True,
                "isRequire": True,
                "completePercentage": 100.0,
                "detail": {"bizId": "res-done", "title": "1.1 已完成.mp4", "bizSuffix": "mp4"},
                "children": [],
            },
            {
                "type": 2,
                "nodeId": "res-todo",
                "isFinished": False,
                "isRequire": True,
                "completePercentage": 0.0,
                "detail": {"bizId": "res-todo", "title": "1.2 待刷.mp4", "bizSuffix": "mp4"},
                "children": [],
            },
        ],
    },
    {
        "type": 1,
        "nodeId": "ch2",
        "children": [
            {
                "type": 2,
                "nodeId": "res-pdf",
                "isFinished": True,
                "completePercentage": 100.0,
                "detail": {"bizId": "res-pdf", "title": "1.3 课件.pdf", "bizSuffix": "pdf"},
                "children": [],
            }
        ],
    },
]

CALLS: list[dict[str, Any]] = []
DURATION = 100  # 测试用假视频时长


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    body = {}
    if request.content:
        try:
            body = json.loads(request.content.decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
    CALLS.append({"path": path, "body": body})

    if path.endswith("/student/term/list"):
        return _ok([{"term": 20271, "isCurrent": 1}])
    if path.endswith("/student/classList"):
        return _ok([{"classId": "CLS1", "className": "测试班"}])
    if path.endswith("/findStudyLibraryByCourseId"):
        return _ok("FOLDER1")
    if path.endswith("/resource/queryTree"):
        return _ok(TREE)
    if path.endswith("/resource/detail"):
        rid = body.get("resourceId")
        return _ok(
            {
                "resourceId": rid,
                "duration": str(DURATION),
                "endWatchTime": 0,
                "completeRate": 0.0,
                "isFinished": False,
                "resourceName": f"{rid}.mp4",
            }
        )
    if path.endswith("/record/report"):
        end = int(body.get("endWatchTime") or 0)
        rate = round(end / DURATION * 100, 2)
        return _ok({"completeRate": rate, "studyTotalTime": end, "duration": str(DURATION)})
    return _ok(None)


def _ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "success": True, "data": data})


def make_client() -> PolymasClient:
    c = PolymasClient({"CASLOGC": quote(json.dumps({"uuid": "U1"}))}, uuid="U1")
    c._client.close()
    c._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"uuid": "U1"},
        timeout=10,
    )
    return c


class FakeEntry:
    kind = "polymas"
    index = 2
    label = "[AI智课] 假课"
    course_id = "CID"
    class_id = "CLS1"
    term = 20271


# ---------------------------------------------------------------------- 用例


def case_uuid() -> None:
    print("\n1) uuid 解析")
    jar = {"CASLOGC": quote(json.dumps({"uuid": "TESTUUID", "realName": "x"}))}
    check("从 CASLOGC 解出 uuid", _uuid_from_cookies(jar) == "TESTUUID", _uuid_from_cookies(jar))
    check("没有 CASLOGC 时返回空串", _uuid_from_cookies({}) == "")


def case_tree() -> None:
    print("\n2) 学习资源树 -> 任务")
    CALLS.clear()
    c = make_client()
    tasks = collect_tasks(c, FakeEntry)
    check("展开 3 个资源", len(tasks) == 3, str(len(tasks)))
    check("id 正确", [t.resource_id for t in tasks] == ["res-done", "res-todo", "res-pdf"])
    done = [t.done for t in tasks]
    check("完成判定正确", done == [True, False, True], str(done))
    check("term 透传", all(t.term == 20271 for t in tasks))
    check("class 透传", all(t.class_id == "CLS1" for t in tasks))
    check("folder 透传", all(t.folder_id == "FOLDER1" for t in tasks))
    check("后缀识别", [t.suffix for t in tasks] == ["mp4", "mp4", "pdf"])
    check(
        "建树只用了一次 queryTree",
        sum(1 for x in CALLS if x["path"].endswith("/resource/queryTree")) == 1,
    )
    c.close()


def case_brush() -> None:
    print("\n3) 30 秒窗口补报")
    CALLS.clear()
    c = make_client()
    task = PolymasTask(
        course_index=2, course_label="假课", course_id="CID", term=20271, class_id="CLS1",
        folder_id="FOLDER1", resource_id="res-todo", title="1.2 待刷.mp4", suffix="mp4",
        duration=0, end_watch=0, rate=0.0, done=False,
    )
    result = brush_task(c, task, step_delay=0)
    reports = [x["body"] for x in CALLS if x["path"].endswith("/record/report")]
    check("结果 ok", result == "ok", result)
    check("到 100%", task.rate >= 100.0, str(task.rate))
    check("窗口数 = 时长/30 向上取整", len(reports) == -(-DURATION // WINDOW), str(len(reports)))
    check("窗口首尾相接", all(
        reports[i]["endWatchTime"] == reports[i + 1]["startWatchTime"]
        for i in range(len(reports) - 1)
    ))
    check("从 0 开始", reports[0]["startWatchTime"] == 0)
    check("末尾不超过时长", reports[-1]["endWatchTime"] == DURATION)
    need = {"startWatchTime", "endWatchTime", "startDate", "endDate",
            "studyTotalTime", "clientType", "courseId", "resourceId"}
    check("请求体字段齐全", need <= set(reports[0]), str(need - set(reports[0])))
    check("studyTotalTime = 窗口长度", reports[0]["studyTotalTime"] == reports[0]["endWatchTime"])
    check("courseId / resourceId 对", reports[0]["courseId"] == "CID"
          and reports[0]["resourceId"] == "res-todo")
    c.close()


def case_skip() -> None:
    print("\n4) 已完成不重复上报")
    CALLS.clear()
    c = make_client()

    class Finished:
        pass

    # detail 返回 isFinished=True
    def finished_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resource/detail"):
            return _ok({"duration": str(DURATION), "endWatchTime": DURATION,
                        "completeRate": 100.0, "isFinished": True})
        return _ok(None)

    c._client.close()
    c._client = httpx.Client(transport=httpx.MockTransport(finished_handler), timeout=10)
    task = PolymasTask(
        course_index=2, course_label="假课", course_id="CID", term=20271, class_id="CLS1",
        folder_id="FOLDER1", resource_id="res-done", title="x.mp4", suffix="mp4",
        duration=0, end_watch=0, rate=100.0, done=True,
    )
    check("isFinished -> skip", brush_task(c, task, step_delay=0) == "skip")
    check("没有发出 report", not any(x["path"].endswith("/record/report") for x in CALLS))
    c.close()


def main() -> int:
    print("=" * 60)
    print("  AI 智课（polymas）离线自检")
    print("=" * 60)
    case_uuid()
    case_tree()
    case_brush()
    case_skip()
    print("\n" + "-" * 60)
    print(f"  通过 {PASS} 条，失败 {FAIL} 条")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
