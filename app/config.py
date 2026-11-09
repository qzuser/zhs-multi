"""config.ini 读取与校验"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    "common": {
        "use_cookies": "true",
        "course_list": "",
        "jobs": "4",
        "speed": "1.5",
        "threshold": "0.91",
        "limit": "0",
        "delay": "0.4",
        "stagger": "2.0",
        "force": "false",
        "dashboard": "true",
        "unblock_wait": "900",
    },
    "ai": {
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "learn_optional": "true",
        "video_threshold": "90",
    },
    "homework": {
        "enable": "false",
        "threshold": "100",
        "max_submit": "2",
    },
    "stealth": {
        "adaptive": "true",
        "rest_every": "4",
        "rest_seconds": "60-180",
        "night_off": "false",
        "night_from": "0",
        "night_to": "6",
        "max_per_day": "0",
    },
    "output": {
        "verbose": "false",
        "color": "true",
    },
}


def _b(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class AppSettings:
    # [common]
    use_cookies: bool = True
    course_list: str = ""
    jobs: int = 4
    speed: float = 1.5
    threshold: float = 0.91
    limit: int = 0
    delay: float = 0.4
    stagger: float = 2.0
    force: bool = False
    dashboard: bool = True
    unblock_wait: int = 900

    # [ai]
    ai_api_key: str = ""
    ai_base_url: str = "https://api.deepseek.com/v1"
    ai_model: str = "deepseek-chat"
    ai_learn_optional: bool = True      # 是否连"选学"资料一起刷
    ai_video_threshold: int = 90        # AI 课程视频完成阈值（0-100）

    # [homework]
    homework_enable: bool = False
    homework_threshold: int = 100
    homework_max_submit: int = 2

    # [stealth] 风控应对
    adaptive: bool = True               # 撞 -10 自动压并发 / 拉长间隔
    rest_every: int = 0                 # 每刷完几条长休息一次（0 = 不休息；config.ini 里默认给 4）
    rest_seconds: str = "60-180"        # 长休息多久（范围）
    night_off: bool = False             # 夜里不刷
    night_from: int = 0
    night_to: int = 6
    max_per_day: int = 0                # 每天最多刷几条（0 = 不限）

    # [output]
    verbose: bool = False
    color: bool = True

    project_dir: Path = field(default_factory=Path.cwd)
    config_path: Path = field(default_factory=lambda: Path("config.ini"))

    # ------------------------------------------------------------ 派生路径
    @property
    def state_dir(self) -> Path:
        path = self.project_dir / "state"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def log_dir(self) -> Path:
        path = self.project_dir / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cookies_path(self) -> Path:
        return self.state_dir / "cookies.json"

    @property
    def qr_path(self) -> Path:
        return self.state_dir / "qrcode.png"

    # ------------------------------------------------------------ 校验
    def normalize(self) -> None:
        self.jobs = max(1, min(32, int(self.jobs)))
        self.speed = max(1.0, min(2.0, float(self.speed)))
        self.threshold = max(0.1, min(1.2, float(self.threshold)))
        self.limit = max(0, int(self.limit))
        self.delay = max(0.0, min(30.0, float(self.delay)))
        self.stagger = max(0.0, min(30.0, float(self.stagger)))
        self.unblock_wait = max(0, min(7200, int(self.unblock_wait)))
        self.homework_threshold = max(0, min(100, int(self.homework_threshold)))
        self.rest_every = max(0, min(200, int(self.rest_every)))
        self.night_from = max(0, min(23, int(self.night_from)))
        self.night_to = max(0, min(24, int(self.night_to)))
        self.max_per_day = max(0, int(self.max_per_day))
        self.homework_max_submit = max(0, int(self.homework_max_submit))
        self.ai_video_threshold = max(1, min(100, int(self.ai_video_threshold)))

    @property
    def has_ai_key(self) -> bool:
        return bool(self.ai_api_key.strip())


def load(path: str | Path, project_dir: Path | None = None) -> AppSettings:
    """读取 config.ini，缺段缺键都用默认值补齐"""
    cfg_path = Path(path)
    parser = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=(";", "#"),
    )
    parser.optionxform = str.lower  # type: ignore[assignment]

    for section, values in DEFAULTS.items():
        parser[section] = dict(values)

    if cfg_path.exists():
        # utf-8-sig：兼容记事本/Set-Content 写出的 BOM
        parser.read(cfg_path, encoding="utf-8-sig")
    else:
        raise FileNotFoundError(f"找不到配置文件：{cfg_path}")

    c = parser["common"]
    settings = AppSettings(
        use_cookies=_b(c.get("use_cookies", "true")),
        course_list=c.get("course_list", "").strip(),
        jobs=int(c.get("jobs", "4") or 4),
        speed=float(c.get("speed", "1.5") or 1.5),
        threshold=float(c.get("threshold", "0.91") or 0.91),
        limit=int(c.get("limit", "0") or 0),
        delay=float(c.get("delay", "0.4") or 0.4),
        stagger=float(c.get("stagger", DEFAULTS["common"]["stagger"]) or 0.0),
        force=_b(c.get("force", "false")),
        dashboard=_b(c.get("dashboard", "true")),
        unblock_wait=int(c.get("unblock_wait", "900") or 900),
        ai_api_key=parser["ai"].get("api_key", "").strip(),
        ai_base_url=parser["ai"].get("base_url", DEFAULTS["ai"]["base_url"]).strip(),
        ai_model=parser["ai"].get("model", DEFAULTS["ai"]["model"]).strip(),
        ai_learn_optional=_b(parser["ai"].get("learn_optional", "true")),
        ai_video_threshold=int(parser["ai"].get("video_threshold", "90") or 90),
        homework_enable=_b(parser["homework"].get("enable", "false")),
        homework_threshold=int(parser["homework"].get("threshold", "100") or 100),
        homework_max_submit=int(parser["homework"].get("max_submit", "2") or 0),
        adaptive=_b(parser["stealth"].get("adaptive", "true")),
        rest_every=int(parser["stealth"].get("rest_every", "4") or 0),
        rest_seconds=parser["stealth"].get("rest_seconds", "60-180").strip(),
        night_off=_b(parser["stealth"].get("night_off", "false")),
        night_from=int(parser["stealth"].get("night_from", "0") or 0),
        night_to=int(parser["stealth"].get("night_to", "6") or 0),
        max_per_day=int(parser["stealth"].get("max_per_day", "0") or 0),
        verbose=_b(parser["output"].get("verbose", "false")),
        color=_b(parser["output"].get("color", "true")),
        project_dir=project_dir or cfg_path.resolve().parent,
        config_path=cfg_path.resolve(),
    )
    settings.normalize()
    return settings
