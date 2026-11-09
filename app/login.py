"""登录：只走扫码（智慧树 APP 扫一扫）

流程（CAS 一次性密码）：
    GET  https://passport.zhihuishu.com/login?service=<SERVICE>   拿二维码
    手机扫码 → 服务器下发 oncePassword
    GET  https://passport.zhihuishu.com/login?pwd=<oncePassword>&service=<SERVICE>
拿到的 Cookie（CASTGC / JSESSIONID / SERVERID ...）落盘到 state/cookies.json，之后一直复用。

说明：账号密码登录（/user/validateAccountAndPassword + 图形验证码）已按需求移除，
统一走扫码；扫码过期会自己换码。没有 APP 的话，用 `login --cookie` 从浏览器导入登录态。
"""

from __future__ import annotations

import json
from typing import Any

from .config import AppSettings
from .console import info, ok, say, warn
from .session_factory import build_zhs_config, save_cookies

SERVICE = "https://onlineservice-api.zhihuishu.com/login/gologin"
PASSPORT = "https://passport.zhihuishu.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# encodeURI 不转义的字符集
_ENCODE_URI_SAFE = ";,/?:@&=+$-_.!~*'()#"


class LoginError(RuntimeError):
    pass


# ---------------------------------------------------------------- 扫码


def login_qr(settings: AppSettings, terminal_qr: bool = False) -> list[dict[str, Any]]:
    """扫码登录，返回 Cookie 列表"""
    from zhs.login import LoginManager
    from zhs.session import ZhsSession

    cfg = build_zhs_config(settings)
    cfg.save_cookies = False  # 由本工具自己落盘到 state/
    cfg.qr.image_path = str(settings.qr_path)
    settings.state_dir.mkdir(parents=True, exist_ok=True)

    session = ZhsSession(cfg)
    mgr = LoginManager(session, cfg)

    shown = {"path": False}

    def on_qr(img_bytes: bytes) -> None:
        if terminal_qr:
            try:
                from zhs.utils.display import show_qrcode_img

                show_qrcode_img(img_bytes)
            except Exception:
                pass
        if not shown["path"]:
            shown["path"] = True
            say()
            ok("二维码已生成")
            info(f"用「智慧树 APP → 扫一扫」扫这个文件：{settings.qr_path}")
            warn("二维码约 3 分钟过期，程序会自动换新码（同一路径，扫前请重新打开文件）")
            say()

    def on_scanned() -> None:
        ok("已扫码，等待手机端点「确认登录」…")

    try:
        result = mgr.login_with_qr(on_qr, on_scanned, image_path=str(settings.qr_path))
    except Exception as exc:  # LoginFailed 等
        raise LoginError(str(exc)) from exc

    if not result.success or not result.cookies:
        raise LoginError("扫码登录未成功")

    save_cookies(settings.cookies_path, result.cookies)
    ok(f"登录成功，Cookie 已保存：{settings.cookies_path}")
    return json.loads(settings.cookies_path.read_text(encoding="utf-8"))


def login(settings: AppSettings, terminal_qr: bool = False) -> list[dict[str, Any]]:
    """登录入口（只有扫码这一条路）"""
    return login_qr(settings, terminal_qr=terminal_qr)


# ---------------------------------------------------------------- 手动导入 Cookie


def import_cookies(settings: AppSettings, raw: str) -> list[dict[str, Any]]:
    """从浏览器复制来的 Cookie 导入登录态

    支持三种写法：
    - DevTools 的请求头：JSESSIONID=xxx; CASTGC=yyy; ...
    - JSON 对象：{"JSESSIONID": "xxx", ...}
    - JSON 列表：[{"name": "JSESSIONID", "value": "xxx", ...}, ...]
    """
    text = (raw or "").strip()
    # 有人会连 DevTools 的 "Cookie:" 一起复制过来，先剥掉
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        raise LoginError("Cookie 是空的")

    items: list[dict[str, Any]] = []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoginError(f"JSON 解析失败：{exc}") from exc
        items = [c for c in data if isinstance(c, dict) and c.get("name")]
    elif text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoginError(f"JSON 解析失败：{exc}") from exc
        items = [{"name": k, "value": str(v)} for k, v in data.items()]
    else:
        for part in text.replace("\n", ";").split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            if key.lower().startswith("cookie"):
                key = key.split(":", 1)[1].strip() if ":" in key else ""
            if not key or key.lower() == "cookie":
                continue
            items.append({"name": key, "value": value.strip()})

    if not items:
        raise LoginError("没解析出任何 Cookie，检查一下格式（name=value; name2=value2）")

    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.cookies_path.write_text(
        json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    names = ", ".join(str(i["name"]) for i in items)
    ok(f"已导入 {len(items)} 条 Cookie：{names}")
    if not any(str(i["name"]).upper().startswith(("CASTGC", "JSESSIONID")) for i in items):
        warn("里面没有 CASTGC / JSESSIONID，多半不是登录后的 Cookie，可能仍提示未登录")
    return items
