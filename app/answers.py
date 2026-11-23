"""题库优先答题：先查题库直取答案，查不到再退回 LLM / 随机

## 为什么另写一套

zhs 自带的题库是**给 LLM 当提示源**用的（`format_hint()` → 塞进 prompt 让模型参考），
所以：

1. 必须配 LLM，没有 LLM 就 `init_question_bank` 直接返回 None，题库白配；
2. 题库里有标准答案也不直接用，还要让模型再"解读"一遍——多一次调用、还可能被改错。

这里按学习通那类做题脚本的做法反过来：**题库命中的题直接用题库答案提交**，
题库没有的才落到 LLM / 缓存排除 / 随机。

## 用法

```ini
[question_bank]
enabled = true
token   = 你的 enncy 题库 token      ; https://tk.enncy.cn 个人中心
url     = https://tk.enncy.cn/query
```

题库答案到选项的映射：先按「答案文本 == 选项内容」精确比；不行再按
「答案文本是否出现在选项内容里」；多选题按分隔符拆成多个答案分别映射。
"""

from __future__ import annotations

import re
from typing import Any

import httpx

_SPLIT = re.compile(r"[#\n\r;；,，、|]+")


class BankAnswer:
    __slots__ = ("raw", "parts", "from_ai")

    def __init__(self, raw: str, parts: list[str], from_ai: bool) -> None:
        self.raw = raw
        self.parts = parts
        self.from_ai = from_ai


class BankClient:
    """极简题库客户端（只做「题目 → 答案文本」这一件事）"""

    def __init__(self, token: str, url: str = "https://tk.enncy.cn/query",
                 timeout: float = 20.0) -> None:
        self.token = (token or "").strip()
        self.url = url or "https://tk.enncy.cn/query"
        self.hits = 0
        self.miss = 0
        self.errors = 0
        self._http = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001,S110
            pass

    # ------------------------------------------------------------ 查询

    def query(self, title: str, qtype: str = "") -> BankAnswer | None:
        """查一题；没答案返回 None

        enncy 题库响应：`{"code":1,"data":{"question":…,"answer":"A","times":…,"ai":false}}`
        `code=0` = 没查到（不扣次数）。
        """
        if not self.token or not title:
            return None
        params = {"title": title[:400], "token": self.token}
        if qtype:
            params["type"] = qtype
        try:
            r = self._http.get(self.url, params=params)
            data = r.json()
        except Exception:  # noqa: BLE001
            self.errors += 1
            return None

        if not isinstance(data, dict) or data.get("code") != 1:
            self.miss += 1
            return None
        inner = data.get("data") or {}
        raw = str(inner.get("answer") or "").strip()
        if not raw:
            self.miss += 1
            return None
        parts = [p.strip() for p in _SPLIT.split(raw) if p.strip()]
        if not parts:
            self.miss += 1
            return None
        self.hits += 1
        return BankAnswer(raw=raw, parts=parts, from_ai=bool(inner.get("ai")))

    # ------------------------------------------------------------ 映射到选项

    @staticmethod
    def to_option_ids(answer: BankAnswer, options: list[Any],
                      multi: bool = False) -> str | None:
        """把题库答案映射成选项 id

        options 是 `HomeworkQuestionOption`（有 .id / .content）。
        匹配顺序：
        1. 答案原样就是选项 id（`A` / `B` 这种）
        2. 答案文本 == 选项内容（去掉空白和标点后）
        3. 答案文本包含在选项内容里，或反过来
        """
        if not options:
            return None

        def norm(s: str) -> str:
            return re.sub(r"[\s\u3000，。、；：“”‘’（）()\\[\\]【】.,;:!?\"']", "", str(s or "")).lower()

        by_id = {str(o.id).upper(): o.id for o in options}
        texts = [(norm(getattr(o, "content", "")), o.id) for o in options]

        picked: list[Any] = []
        for part in answer.parts:
            key = str(part).strip().upper()
            if key in by_id:
                picked.append(by_id[key])
                continue
            np = norm(part)
            if not np:
                continue
            hit = next((oid for txt, oid in texts if txt and txt == np), None)
            if hit is None:
                hit = next((oid for txt, oid in texts if txt and (np in txt or txt in np)), None)
            if hit is not None and hit not in picked:
                picked.append(hit)

        if not picked:
            return None
        if multi:
            return ",".join(str(x) for x in picked)
        return picked[0]
