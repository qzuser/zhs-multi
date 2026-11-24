"""作业门禁：**题库没覆盖全部题目，就整份不提交**

## 为什么

v1.3.x 的答题链路是「题库命中 → 缓存 → 排除 → 随机」。最后那档"随机"很难看：
一份 5 题的选择题，蒙对 3 题就是 60 分，会真的写进平台成绩，之后还要靠重做去救。

改成：**先拿全部题目逐题查一遍题库，只要有一题没查到，这份作业直接跳过、一个字都不提交**。
等题库补全（或换更全的题库）再回来做，成绩不会被随机答案污染。

## 怎么做到"只查一次"

门禁阶段把「题干 → 选项 id」存成计划表（`plan`），做题时 patch 直接查这张表，
不再重复请求题库（enncy 是按次扣额度的）。
"""

from __future__ import annotations

import re
from typing import Any

from .answers import BankClient

_TAG = re.compile(r"<[^>]+>")
_PATCHED = "_zhs_multi_gate_patched"


def plain(text: Any) -> str:
    """题干/选项是 HTML，去掉标签"""
    s = _TAG.sub(" ", str(text or ""))
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", s).strip()


def _qtype_of(question: Any) -> tuple[str, bool]:
    """返回 (题库 type 字符串, 是否多选)"""
    try:
        from zhs.question_bank.models import map_question_type

        tid = int(getattr(question, "question_type_id", 0) or 0)
        return map_question_type(tid), tid == 2
    except Exception:  # noqa: BLE001
        return "", False


def build_plan(worker: Any, item: Any, recruit_id: str, school_id: str,
               bank: BankClient) -> tuple[bool, dict[str, str], str]:
    """把整份作业的题目拉下来逐题查题库

    Returns:
        (是否全部命中, {题干plain: 选项id串}, 说明文字)
    """
    try:
        detail = worker._fetch_homework_detail(item, recruit_id, school_id)  # noqa: SLF001
        questions = worker._extract_questions(detail)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        return False, {}, f"拉题目失败（{type(exc).__name__}）"

    if not questions:
        return False, {}, "没有题目"

    plan: dict[str, str] = {}
    missing = 0
    for q in questions:
        title = plain(getattr(q, "name", ""))
        qtype, multi = _qtype_of(q)
        val = None
        if title:
            try:
                ans = bank.query(title, qtype)
                if ans is not None:
                    val = bank.to_option_ids(
                        ans, list(getattr(q, "question_options", []) or []), multi
                    )
            except Exception:  # noqa: BLE001,S110
                val = None
        if val is None:
            missing += 1
        else:
            plan[title] = val

    if missing:
        return False, plan, f"{missing}/{len(questions)} 题题库里没有答案"
    return True, plan, f"{len(questions)} 题全部命中题库"


def prime(plan: dict[str, str]) -> None:
    """把计划表交给做题 patch（之后不再请求题库）"""
    from . import bank_first

    bank_first.set_plan(plan)


def install_return_guard() -> bool:
    """装一个兜底：如果答题时发现计划表里没有的题，也不许提交

    正常流程走 build_plan 门禁就够了；这里防的是「门禁过了但做题时题目
    顺序/文本有细微差异」——那种情况宁可不交，也不要随机蒙。
    """
    try:
        from zhs.zhidao.homework.worker import HomeworkWorker
    except Exception:  # noqa: BLE001
        return False

    if getattr(HomeworkWorker, _PATCHED, False):
        return True

    orig_submit = HomeworkWorker._submit  # type: ignore[attr-defined]

    def _submit(self, item, recruit_id, answer_count):  # noqa: ANN001
        from . import bank_first as bf

        plan = bf.current_plan()
        log = list(getattr(self, "_gate_sources", []) or [])
        if plan and log and any("计划" not in s and "题库" not in s for s in log):
            try:
                self._reporter.print("  有题目没拿到题库答案 → 本次不提交")
            except Exception:  # noqa: BLE001,S110
                pass
            self._gate_skipped = True
            return 0.0
        return orig_submit(self, item, recruit_id, answer_count)

    HomeworkWorker._submit = _submit  # type: ignore[method-assign]
    setattr(HomeworkWorker, _PATCHED, True)
    return True
