"""把「题库优先」接到 zhs 的作业答题器上

zhs 原来的顺序是：缓存正确 → 缓存排除 → **LLM（题库只当提示）** → 随机。
这里在最前面插一层：**题库命中就直接用，不惊动 LLM**。

不命中就原样交回给 zhs 的老逻辑，所以不会有任何回退风险。

```ini
[question_bank]
enabled = true
token   = 你的 ennky 题库 token
url     = https://tk.enncy.cn/query
```
"""

from __future__ import annotations

import re
from typing import Any

from .answers import BankClient

_PATCHED = "_zhs_multi_bank_first"
_TAG = re.compile(r"<[^>]+>")

# 门禁预查好的答案：{题干plain: 选项 id 串}
_PLAN: dict[str, str] = {}


def set_plan(plan: dict[str, str]) -> None:
    global _PLAN
    _PLAN = dict(plan or {})


def clear_plan() -> None:
    global _PLAN
    _PLAN = {}


def current_plan() -> dict[str, str]:
    return dict(_PLAN)


def _plain(text: Any) -> str:
    """题干/选项是 HTML，去掉标签再拿去查题库"""
    s = _TAG.sub(" ", str(text or ""))
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", s).strip()


def install(client: BankClient | None = None) -> bool:
    """装补丁；重复调用安全。client 为 None 时只标记不生效。"""
    try:
        from zhs.question_bank.models import map_question_type
        from zhs.zhidao.homework.worker import HomeworkWorker
    except Exception:  # noqa: BLE001
        return False

    if getattr(HomeworkWorker, _PATCHED, False):
        return True
    if client is None or not client.token:
        return False

    orig = HomeworkWorker._generate_answer_with_source  # type: ignore[attr-defined]

    def _remember(self, source: str) -> None:
        try:
            log = getattr(self, "_gate_sources", None)
            if log is None:
                log = []
                self._gate_sources = log
            log.append(source)
        except Exception:  # noqa: BLE001,S110
            pass

    def patched(self, question, item):  # noqa: ANN001
        title = _plain(getattr(question, "name", ""))
        tid = int(getattr(question, "question_type_id", 0) or 0)

        # ---- 1) 门禁预查好的计划表：直接用，不再请求题库 ----
        if _PLAN and title in _PLAN:
            _remember("计划")
            return _PLAN[title], "计划"

        # ---- 2) 没走门禁：现查题库 ----
        try:
            if title:
                qtype = map_question_type(tid)
                ans = client.query(title, qtype)
                if ans is not None:
                    val = client.to_option_ids(
                        ans, list(getattr(question, "question_options", []) or []), tid == 2
                    )
                    if val is not None:
                        _remember("题库")
                        return val, ("题库AI" if ans.from_ai else "题库")
        except Exception:  # noqa: BLE001,S110
            pass

        # ---- 3) 题库没命中：交回 zhs 老逻辑（缓存排除 / 随机）----
        _remember("其它")
        return orig(self, question, item)

    HomeworkWorker._generate_answer_with_source = patched  # type: ignore[method-assign]
    setattr(HomeworkWorker, _PATCHED, True)
    return True


def client_from_settings(settings: Any) -> BankClient | None:
    """按 config.ini 建题库客户端；没开或没 token 返回 None"""
    if not getattr(settings, "qb_enabled", False):
        return None
    token = str(getattr(settings, "qb_token", "") or "").strip()
    if not token:
        return None
    return BankClient(token, str(getattr(settings, "qb_url", "") or "https://tk.enncy.cn/query"))


def stats_line(client: BankClient | None) -> str:
    if client is None:
        return "题库未启用（config.ini 的 [question_bank] enabled/token）"
    return f"题库命中 {client.hits} 次，未命中 {client.miss} 次，出错 {client.errors} 次"
