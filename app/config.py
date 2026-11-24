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
        "learn_optional": "true",
        "video_threshold": "90",
    },
    "homework": {
        "enable": "false",
        "threshold": "100",
        "max_submit": "2",
        "require_all": "true",
    },
    "stealth": {
        "adaptive": "true",
        "rest_every": "6",
        "rest_seconds": "15-30",
        "night_off": "false",
        "night_from": "0",
        "night_to": "6",
        "max_per_day": "0",
        "device_tag": "zhs",
    },
    "question_bank": {
        "enabled": "false",
        "token": "",
        "url": "https://tk.enncy.cn/query",
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
    ai_learn_optional: bool = True      # 是否连"选学"资料一起刷
    ai_video_threshold: int = 90        # AI 课程视频完成阈值（0-100）

    # [homework]
    homework_enable: bool = False
    homework_threshold: int = 100
    homework_max_submit: int = 2
    homework_require_all: bool = True   # 题库没覆盖全部题目就整份不提交

    # [stealth] 风控应对
    adaptive: bool = True               # 撞 -10 自动压并发 / 拉长间隔
    rest_every: int = 0                 # 每刷完几条长休息一次（0 = 不休息；config.ini 里默认给 6）
    rest_seconds: str = "15-30"         # 长休息多久（范围）
    night_off: bool = False             # 夜里不刷
    night_from: int = 0
    night_to: int = 6
    max_per_day: int = 0                # 每天最多刷几条（0 = 不限）
    device_tag: str = "zhs"            # 上报里的设备标识；实测只有 zhs 能被平台解出来

    # [question_bank] 第三方题库（命中就直接提交，不用 LLM）
    qb_enabled: bool = False
    qb_token: str = ""
    qb_url: str = "https://tk.enncy.cn/query"

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
        # 上限放到 8.0：知到的 speed 是「1 秒真实时间推进几秒视频」，
        # 平台那边只校验进度增量与总时长，翻倍先试试看（4.0 起可能撞 -8 时长下降）
        self.speed = max(0.1, min(100.0, float(self.speed)))
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


def _secret(cfg_path: Path, section: str, key: str, fallback: str = "") -> str:
    """敏感值（token 之类）优先从 state/secrets.ini 取

    config.ini 是仓库文件、会被提交，所以凭据不能写在那儿。
    这里让 config.ini 留空、值放 state/secrets.ini（.gitignore 已忽略 state/）。
    """
    # secrets.ini 优先（凭据不写进要提交的 config.ini）；没配才用 config.ini 的值
    sec = cfg_path.resolve().parent / "state" / "secrets.ini"
    if sec.exists():
        try:
            cp = configparser.ConfigParser(interpolation=None,
                                           inline_comment_prefixes=(";", "#"))
            cp.read(sec, encoding="utf-8")
            if cp.has_section(section) and cp.has_option(section, key):
                val = cp[section][key].strip()
                if val:
                    return val
        except Exception:  # noqa: BLE001,S110
            pass
    return fallback


def save_secret(cfg_path: str | Path, section: str, key: str, value: str) -> Path:
    """把敏感值写进 state/secrets.ini（自动建目录）"""
    sec = Path(cfg_path).resolve().parent / "state" / "secrets.ini"
    sec.parent.mkdir(parents=True, exist_ok=True)
    cp = configparser.ConfigParser(interpolation=None)
    if sec.exists():
        try:
            cp.read(sec, encoding="utf-8")
        except Exception:  # noqa: BLE001,S110
            pass
    if not cp.has_section(section):
        cp.add_section(section)
    cp[section][key] = value or ""
    with sec.open("w", encoding="utf-8") as fh:
        cp.write(fh)
    return sec


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
        ai_learn_optional=_b(parser["ai"].get("learn_optional", "true")),
        ai_video_threshold=int(parser["ai"].get("video_threshold", "90") or 90),
        homework_enable=_b(parser["homework"].get("enable", "false")),
        homework_threshold=int(parser["homework"].get("threshold", "100") or 100),
        homework_max_submit=int(parser["homework"].get("max_submit", "2") or 0),
        homework_require_all=_b(parser["homework"].get("require_all", "true")),
        adaptive=_b(parser["stealth"].get("adaptive", "true")),
        rest_every=int(parser["stealth"].get("rest_every", "6") or 0),
        rest_seconds=parser["stealth"].get("rest_seconds", "15-30").strip(),
        night_off=_b(parser["stealth"].get("night_off", "false")),
        night_from=int(parser["stealth"].get("night_from", "0") or 0),
        night_to=int(parser["stealth"].get("night_to", "6") or 0),
        max_per_day=int(parser["stealth"].get("max_per_day", "0") or 0),
        device_tag=(parser["stealth"].get("device_tag", "zhs") or "zhs").strip(),
        # 题库：开关和 token 都允许放在 state/secrets.ini（config.ini 是要提交的，不放凭据）
        qb_enabled=_b(_secret(cfg_path, "question_bank", "enabled",
                              parser["question_bank"].get("enabled", "false"))),
        qb_token=_secret(cfg_path, "question_bank", "token",
                         parser["question_bank"].get("token", "").strip()),
        qb_url=(_secret(cfg_path, "question_bank", "url", "")
                or parser["question_bank"].get("url", "https://tk.enncy.cn/query")).strip()
        or "https://tk.enncy.cn/query",
        verbose=_b(parser["output"].get("verbose", "false")),
        color=_b(parser["output"].get("color", "true")),
        project_dir=project_dir or cfg_path.resolve().parent,
        config_path=cfg_path.resolve(),
    )
    settings.normalize()
    return settings
