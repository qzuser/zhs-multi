"""Hike（职教云）响应格式兼容补丁

## 为什么需要这个

zhs 的 `_hike_query` 是这么判断成功的：

```python
status = result.get("code", 0)
if status != 200:
    raise ApiError(...)
```

但 `studyresources.zhihuishu.com` 的接口实测（2026-11-23）返回的是：

```json
{"currentTime": 1795434473605, "msg": "请求成功", "rt": [...]}
```

**根本没有 `code` 字段**，于是 `status` 取到默认值 `0`，直接抛
`API error 0:`（message 还是空的），整门 Hike 课连资源树都读不出来。

修法不动 zhs 库文件（保持可升级）：在进程内把 `_hike_query` 换成宽松版，
只有**明确给了 code 且不是 200** 才报错。新旧两种格式都能过。
"""

from __future__ import annotations

_PATCHED_FLAG = "_zhs_multi_hike_patched"


def install() -> bool:
    """装上补丁；重复调用安全"""
    try:
        from zhs.api.encrypted_query import EncryptedQuery
        from zhs.crypto import sign_hike
        from zhs.exceptions import ApiError
    except Exception:  # noqa: BLE001
        return False

    if getattr(EncryptedQuery, _PATCHED_FLAG, False):
        return True

    def _hike_query(self, url, data, method="GET", *, sig=False):  # noqa: ANN001
        data = dict(data or {})
        data["_"] = str(self._http.now_timestamp_ms())
        if sig:
            for k in list(data):
                data[k] = str(data[k])
            data["signature"] = sign_hike(data, self._http.crypto.hike_salt)

        result = self._http.api_query(url, data=data, method=method)
        if not isinstance(result, dict):
            return {}

        # 关键：没有 code 字段就当成功（新版接口只给 currentTime/msg/rt）
        code = result.get("code")
        if code is not None:
            try:
                bad = int(code) != 200
            except (TypeError, ValueError):
                bad = True
            if bad:
                msg = result.get("message") or result.get("msg") or ""
                raise ApiError(code=int(code), message=str(msg))
        return result

    EncryptedQuery._hike_query = _hike_query  # type: ignore[method-assign]
    setattr(EncryptedQuery, _PATCHED_FLAG, True)
    return True


def tree(session, course_id: str) -> list:
    """自己读 Hike 资源树（不经过 get_context，避开缓存和旧判断）"""
    from zhs.hike.models import ResourceNode

    url = ("https://studyresources.zhihuishu.com/studyResources/"
           "stuResouce/queryResourceMenuTree")
    params = {"courseId": str(course_id), "_": str(session._http.now_timestamp_ms())}
    result = session._http.api_query(url, data=params, method="GET")
    raw = (result or {}).get("rt") or []
    return [ResourceNode.model_validate(n) for n in raw]
