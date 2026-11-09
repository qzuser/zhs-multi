"""查视频在平台侧的元数据：时长 / 进度 / 完成状态"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load  # noqa: E402
from app.courses import list_courses, select_courses  # noqa: E402
from app.session_factory import new_session  # noqa: E402
from zhs.zhidao.course import ZhidaoCourseManager  # noqa: E402

settings = load(ROOT / "config.ini", project_dir=ROOT)
cookies = json.loads((ROOT / "state" / "cookies.json").read_text(encoding="utf-8"))
sess = new_session(settings, cookies)

entries = list_courses(sess)
sel = select_courses(entries, "1")
entry = sel[0]
ctx = ZhidaoCourseManager(sess).get_context(entry.rac_id)

n = 0
for chapter in ctx.chapters:
    for lesson in chapter.video_lessons:
        for small in lesson.video_small_lessons:
            n += 1
            fields = {
                "watch_state": getattr(small, "watch_state", "?"),
                "video_sec": getattr(small, "video_sec", "?"),
                "progress": getattr(small, "progress", "?"),
            }
            print(f"{n:>2} {small.video_id} {fields} {small.name}")
print("总视频数", n)
