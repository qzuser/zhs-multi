"""与 zhs 库的粘合层：构造会话、读写 Cookie"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zhs.config import AppConfig
from zhs.session import ZhsSession
from zhs.utils.cookie import cookies_to_list, list_to_cookies

from .config import AppSettings


def build_zhs_config(settings: AppSettings) -> AppConfig:
    """把 config.ini 映射成 zhs 的 AppConfig"""
    cfg = AppConfig()
    cfg.save_cookies = settings.use_cookies
    cfg.threshold = settings.threshold
    cfg.limit = 0  # 时间限制由本工具统一调度
    cfg.video.zhidao_speed = settings.speed
    cfg.video.hike_speed = settings.speed
    cfg.video.ai_speed = settings.speed
    cfg.homework.threshold = settings.homework_threshold
    cfg.homework.max_submit = settings.homework_max_submit
    cfg.display.log_level = "DEBUG" if settings.verbose else "INFO"
    if settings.has_ai_key:
        cfg.ai.use_builtin_ai = False
        cfg.ai.api_key = settings.ai_api_key
        cfg.ai.base_url = settings.ai_base_url
        cfg.ai.model = settings.ai_model
    return cfg


def new_session(settings: AppSettings, cookies: list[dict[str, Any]] | None = None) -> ZhsSession:
    """新建一个独立会话（每个线程各持一个，避免共享 httpx client）"""
    session = ZhsSession(build_zhs_config(settings))
    if cookies:
        session.cookies = list_to_cookies(cookies)
    return session


def load_cookies(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, list) or not data:
        return None
    return data


def save_cookies(path: Path, cookies: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = cookies_to_list(cookies)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
