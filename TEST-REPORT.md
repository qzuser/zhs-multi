# zhs-multi 测试报告

> 环境：Windows / Python 3.14.6 / zhs 0.1.5（editable venv `..\.venv-zhs`）
> 账号：1 门知到课《大学生职业生涯规划》(courseId=1000000000)，无 Hike、无智慧课程
> 最后一次全量回归：2026-11-09 12:40（Asia/Shanghai）｜本轮新增：盯梢进度落盘 `state/daemon.json` + `status` 的「盯梢中」一行

## 1. 命令与退出码

| # | 命令 | 期望 | 实测 | 结论 |
|---|---|---|---|---|
| 1 | `--help` | 列出 10 个子命令 | login/list/run/selftest/probe/unblock/daemon/captcha/doctor/status | ✅ |
| 2 | `run --help` | 参数齐全 | `-c -j -s --threshold -l --dry-run --force --with-homework --auto-unblock --no-dashboard --no-login -v` | ✅ |
| 3 | `login --help` | 只剩扫码 + cookie 导入 | `--terminal-qr --cookie --cookie-file`（`--mode/-u/-p/--captcha` 已移除） | ✅ |
| 3b | `captcha --check` | 找得到装了 playwright 的 Python | 自动定位 `Python311` + chromium-1234，退出码 0 | ✅ |
| 3c | `unblock --help` / `daemon --help` | 有 `--auto` / `--solve-captcha` | 均在 | ✅ |
| 4 | `selftest --help` | 有 `--ai` | 有 | ✅ |
| 5 | `doctor` | 环境体检 | Python 3.14.6 / 线程 2 / zhs 0.1.5 / passport 200 / 智慧课程配置行 | ✅ |
| 6 | `status` | 总览 | 登录态有效、1 门课 23 视频 待刷 20、上轮结果 | ✅ |
| 7 | `list` | 1 门课 | `[知到] 大学生职业生涯规划` racId 正常 | ✅ |
| 8 | `list --json` | 合法 JSON | 可被 `ConvertFrom-Json` 解析 | ✅ |

## 2. 选课 spec 解析

| 输入 | 结果 | 退出码 |
|---|---|---|
| `-c 1` | 选中 1 门 | 0 |
| `-c all` | 选中 1 门 | 0 |
| `-c 99` | `序号越界：[99]（有效范围 1-1）` | 1 |
| `-c 2-4` | `序号越界：[2, 3, 4]（有效范围 1-1）` | 1 |
| `-c x` | `无法识别的课程选择：x` | 1 |

## 3. 参数钳制

| 输入 | 生效值 |
|---|---|
| `-j 99` | 32 |
| `-j 0` | 1（`max(1, …)`） |
| `-s 9` | 2.0 |
| `--threshold 9` | 1.2 |
| `--threshold 0` | 0.1 |
| `-l 1` | 1 分钟 |

`config.ini` 侧同样钳制：`jobs=99→32`、`speed=9→2.0`、`delay=999→30`、`unblock_wait` 0–7200。

## 4. 并发自检（不联网）

| 命令 | 结果 |
|---|---|
| `selftest -j 8 -n 16` | 完成 16/16，墙钟 2.02s，加速 **7.9x** |
| `selftest -j 4 -n 8` | 完成 8/8，墙钟 2.02s，加速 **4.0x** |
| `selftest -j 1 -n 3` | 完成 3/3，3.02s，提示"jobs=1 是串行基准"，退出码 0 |
| `selftest --ai` | 智慧课程链路 24 项断言全通过，退出码 0 |

## 4.1 离线核心自检（`selftest --core` / `tools/check_core.py`）

| 组 | 项数 | 内容 |
|---|---|---|
| 选课 spec | 9 | `1` / `1,3` / `2-4` / `all` / 反向区间 / 空格 / 空串 / 越界报错 / 非法字符报错 |
| config | 10 | BOM 读取、越界钳制（jobs/speed/threshold/delay/unblock_wait/AI 阈值）、未知登录方式回退、缺段补齐、非法数字与缺文件报错 |
| 进度行分流 | 4 | 进度行只更新状态不刷屏、百分比解析（45.2）、pause 态识别、普通输出进事件流 |
| 接管签名兼容 | 3 | `tree_print(text, depth=, enabled=)` 不炸、`depth` 生效、`enabled=False` 被丢弃 |
| dashboard 还原 | 3 | 接管期内 `print` 被替换、退出后**按原样还原**、接管期内输出能被 sink 收到 |
| 补跑去重 | 3 | 3 条模拟失败全部补跑成功、结果按 videoId 去重、每条恰好两次 |
| 本轮报告 | 4 | 文件名、计数、逐条任务、ISO 时间戳 |
| 密码编码 | 2 | `btoa(encodeURI(json))` 逐字节一致、空格编码成 `%20` |
| Cookie 导入 | 5 | 带 `Cookie:` 前缀的请求头 / JSON 对象 / JSON 数组 / `k=v` 一行 / 每行一条 |
| probe 结果分类 | 4 | 假播放器抛 `CaptchaRequired`→`captcha`、`TimeLimitExceeded`→`ok`、`RuntimeError`→`error`、正常结束→`ok` |
| 自动解锁轮询 | 3 | 第 3 次探测解除→`True`（共探测 3 次）、等不到→`False`、`open_browser=False` 不弹窗 |
| 盯梢模式 | 4 | 一直被拦→到 `--cycles` 上限退出 3；逐条推进→刷完返回 0（触发 3 次刷课）；毫无推进→不死循环、退出 3；本来没活→0 |

`selftest --all`（并发 + 核心 + 智慧课程）退出码 0。

> 本轮据此修掉两个真实缺陷：`import_cookies` 遇到 DevTools 的 `Cookie: xxx` 前缀会把
> `Cookie: SESSION` 当成一个 cookie 名（现在先剥前缀）；`selftest -j 1` 会把串行基准误报成失败。
>
> 第 4 轮又修掉三个：
> 1. **dashboard 退出后没真正还原**——`zhs.zhidao.video` 原本没有 `print/tree_print/wipe_line`
>    这三个模块属性，代码只在"原来存在"时才还原，导致第一次用完之后我们的 Sink 永久挂在模块上，
>    之后任何 `zhs` 串行播放的进度行都被吞掉。现在改成"原来没有就删掉"。
> 2. **Sink 不认 `tree_print(depth=, enabled=)`**——`play_course`（整课串行）会带这两个关键字，
>    接管期间调用会抛 `TypeError`。现在 `__call__` 会把它们转发给 `event()`，`enabled=False` 直接丢弃。
> 3. `Sink.__call__` 原来只把 text 传给 `event()`，`end=/flush=` 之外的关键字全丢——同上修法。

## 5. 真机并发（知到）
| 场景 | 观测 |
|---|---|
| `-j 3` 实刷 | 三条视频同时推进（`71045789` / `70953449` / `71045763`），7 分钟刷完 3 个 |
| 风控 | `-10 疑是多窗口观看` 出现 61 次（自动刷新令牌后重试，进度照记），第 7 分钟升级 `-12` |
| `-j 2` 实跑 | 1.2s 内被 `-12` 拦停，`中止 2`，退出码 3，`state/last-run.json` 正常落盘 |

## 6. 验证码（-12）处置

| 命令 | 场景 | 结果 |
|---|---|---|
| `probe -t 12` | 被拦状态 | `仍要求图形验证码`，退出码 3 |
| `unblock --no-open --wait 15` | 被拦状态 | 轮询 1 次后超时，退出码 3 |
| `unblock -c 1 --then-run -j 2` | 被拦状态 | 自动开播放页（Edge 新标签已确认），解除后自动续刷（未解除时按 900s 放弃） |
| `run --auto-unblock` | 被拦状态 | 撞 `-12` 后进解锁流程，智慧课程任务会提示"不走网页播放器" |

> 平台这一关是**网易易盾**验证码（播放页挂 `captcha.253.com/Captcha.js` + `#captchaYidun`），
> 只能在网页/APP 里人工点一次；换视频、降速、改阈值均无效（实测逐个试过，全部立即返回 -12）。

## 7. 登录（只剩扫码）

| 项目 | 结果 |
|---|---|
| 扫码 `login` | 生成 `state/qrcode.png`，过期自动换码写同路径；本轮重构后重新实测：`[OK] 二维码已生成` |
| 账号密码登录 | **已按需求移除**：`login()` 只剩 `(settings, terminal_qr)`，`login_password`/`_secret`/`SlideCaptchaRequired` 全部删除，config 里 `login_mode/username/password` 也删了（`selftest --core` 第 6 节 5 条断言守着） |
| `login --cookie "a=1; b=2"` | 写入 2 条，格式正确 |
| `login --cookie-file`（JSON 对象 / JSON 数组 / `k=v` 一行 / 每行一条） | 4 种格式全部解析为 2 条 |
| 无 Cookie 时 `status` / `list --no-login` | 提示"还没有登录态 / 本地没有 Cookie"，退出码 2 |

## 7.1 易盾点选验证码（-12）自动点选

平台风控后播放页弹**网易易盾点选**验证码：图上 4~6 个彩色单字符，提示语形如
`请点击绿色小写u`，也常见 `请点击绿色数字5朝向一样的大写H`（参照字符 + 朝向/颜色约束）。

| 步骤 | 实测结果 |
|---|---|
| 打开播放页并注入 Cookie | `studyh5` → 302 到 `studyvideoh5.zhihuishu.com/stuStudy`，课程目录 29 条正常渲染 |
| 弹窗判定 | `.yidun_popup` + `.yidun_bgimg` 320×160 + 提示语可读，判为点选型（非滑块） |
| 识别 | 墨迹连通域 → 色块主色命名 → 系统字体模板（6 字体 × 5 旋转 × 62 字符）IoU 匹配，正确字符得分 0.78–0.93 |
| 点击 | 二次缓动 + 抖动轨迹（14–24 步），按下 50–110ms |
| 首次成功 | `[OK] 验证码通过` → 紧接着 `probe -c 1 -t 14` 返回 **0**（`结果：能正常上报`） |
| 失败兜底 | 命中率不够就点"刷新"换一张，最多 `--tries` 轮 |

结论：**这一关已经不用人工**。`zhs-multi.bat captcha` 一条命令即可，点过后刷课立刻恢复。

## 8. 智慧课程（离线自检，账号内无 AI 课）

`tools/check_ai.py` / `selftest --ai` 覆盖：

- 资料类型映射：文本 `type2/dist1`、PPT `1/4`、视频 `1/3`、课程视频 `2/2`、其它兜底
- 知识点展开：顺序、主题归属、`studyProgress>=101` 判完成
- 资料处理：文本/PPT/未知类型走 `complete_resource`，视频走 `play_video`，`schedule` 与阈值透传，
  `fileId=0` 降级为标完成，`studyStatus=1` 不重复提交
- 选学开关：`learn_optional=false` 时选学跳过、必修照刷
- 混合并发：知到视频 + 智慧课程知识点同池跑完，分发到两种处理器

## 9. 配置与健壮性

| 场景 | 结果 |
|---|---|
| BOM 的 config.ini | `utf-8-sig` 读取正常（PowerShell `Set-Content -Encoding UTF8` 曾写出 BOM） |
| 缺段缺键 | 用 DEFAULTS 补齐，不抛异常 |
| `config.ini` 不存在 | `FileNotFoundError：找不到配置文件` |
| stdout 非 tty | 自动关闭单行总览，逐条打印（`ZHS_MULTI_DASH=1` 可强制） |
| 单条失败 | 并发跑完后单线程补跑一次，按 videoId 去重（模拟 3 条失败 → 全部补跑成功，计数不翻倍） |
| Ctrl+C | 记录"用户中断"，已完成的结果已落盘 |

## 10. 结论

- 探测课程（`status` / `list` / `--dry-run`）：**通过**。
- 刷课（并发）：**通过**，2~3 线程实测在推进（本轮 5/23 → 继续）。
- 验证码（-12）：**已自动化**。`captcha` 点过之后 `probe` 返回 0，刷课可继续；
  `daemon --solve-captcha` 把"被拦 → 点掉 → 续刷"接成一个闭环。
- 登录：**只剩扫码**，账号密码路径按需求彻底移除，文档与 CLI 同步（`selftest --docs` 55 条命令全对）。

## 11. 本轮修的 bug

| # | 现象 | 原因 | 处理 |
|---|---|---|---|
| 1 | `selftest --docs` 报 2 处不一致 | 文档里 `# 被 -12 拦住` 被当成了命令行开关 | `check_docs.py` 跳过纯数字的 `-12`/`-9`，并遇 `#` 停解析 |
| 2 | 手册里还留着账号密码整节 | 重构后没同步 | 4.2/4.3 重写为"导入 Cookie / 登录态失效"，配置表、命令表、FAQ 一并删改 |
| 3 | README 出现重复行 | 上一轮插入时没去重 | 删掉重复的"扫码后提示过期"一行 |
| 4 | 易盾识别最初把 `u` 认成 `B`/`q` | 用 `255-灰度` + NCC，笔画方向反了 | 改成色块二值掩码 + 字体模板 IoU |
| 5 | 灰色/白色目标切到背景 | 灰阶颜色饱和度本就低 | 改成"先全局找墨迹、再给色块命名颜色" |
| 6 | `朝向一样` 句式解析错目标 | 把参照字符当成了目标 | 遇 `朝向/方向/一样` 取最后一个字符 |

## 12. 复现命令

```
zhs-multi.bat captcha --check          # 环境（应输出"自动点选可用"）
zhs-multi.bat captcha                  # 点掉验证码
zhs-multi.bat probe -c 1               # 0 = 已解除
zhs-multi.bat run -c 1 -j 2            # 续刷
zhs-multi.bat daemon -c 1 -j 2 --every 300 --solve-captcha --hours 6   # 全自动闭环
```

## 13. 盯梢可见性（本轮新增）

| 项目 | 实测 |
|---|---|
| 落盘 | 盯梢每轮写 `state/daemon.json`：`cycle / total / pending / phase / solved / jobs / every / time / pid` |
| `status` 读取 | 打印 `盯梢中   : 第 1 轮｜待刷 14/23｜阶段 probe｜自动点选 0 次｜120 秒前` |
| 过期判断 | 超过 30 分钟没更新就显示「已退出」，不会把死进程当成还在跑 |
| 阶段枚举 | `probe` → `captcha` / `solved` / `idle` / `done` / `empty` |
| 离线测试 | `selftest --core` 第 10 节新增 3 条断言（写入 / 字段 / 盯梢循环自己写） |

## 14. 线程数与 `-10` 的对照实测（2026-11-09 12:12~12:40）

| 线程 | `-10` 多窗口 | 观测 |
|---|---|---|
| 3 | 7 分钟 61 次 | 升级 `-12`，全局停，期间完成 3 条 |
| 2 | 每 12~20 秒一次 | 靠刷新令牌续，进度照涨，风险累积 |
| 1 | **0 次** | 6~10 分钟一条，23 条里已完成 9 条，日志无告警 |

结论：无人值守用 `-j 1`；要速度用 `-j 2` + `daemon --solve-captcha` 兜底。

### 7.2 点选识别升级后的实测（2026-11-09 13:13）

线上遇到的提示语有三类，旧版本（先猜字符再比字符串）在 `Y/U/5` 上必错，分数 0.78~0.82 却全点错。
改成"按目标字符算相似度" + 参照物约束后：

```
[1] 提示：… 请点击侧向的小写v
    候选 (238,85,37,43) 颜色=粉 像「v」0.749 旋转0°
    点 (238,85,37,43) @(796,344) 相似度 0.749
[2] 提示：… 请点击圆锥朝向一样的数字4
    候选 (119,97,38,45) 颜色=黄 像「4」0.750 旋转0°
    点 (119,97,38,45) @(678,358) 相似度 0.750
    [OK] 验证码通过
```

解除后 `probe -c 1 -t 14` 返回 0（能正常上报），daemon 续刷。

| 升级点 | 效果 |
|---|---|
| 按目标字符算相似度（不再先猜字符） | 阈值 0.34 即可命中，实测 0.75 一次过 |
| 参照物约束（`颜色一样` / `朝向一样`） | 先定位参照字符，取它的颜色 / 旋转角再筛候选 |
| `侧向/倾斜` | 优先挑旋转角不为 0 的色块 |
| 认不出的参照词（如 `圆锥`） | 降级为无约束，直接取最像目标字符的 |
| 一张图多候选（`--per-attempt`） | 点错了弹窗还在就试下一个；图换了重新解析 |
| `--selftest-parse` | 7 类提示语解析用例，进 `selftest --core` |

## 15. 全量自检基线（2026-11-09 13:59）

`python -m app selftest --all` → **rc=0**，断言统计：

| 项 | 数量 | 说明 |
|---|---|---|
| `[OK]` 断言通过 | **127** | 核心链路 + 智慧课程资料 + 文档一致性 |
| `[!!]` | 14 | 全是**故意构造**的负向场景：错峰抖动 3、无 CASTGC 的 Cookie 5、浏览器起不来 2、探测抛异常 1、未登录 3 |
| `[ERR]` | 10 | 同样是故意触发的错误路径：到 `--cycles` 上限 8、连续 `-9` 1、连续登录态失效 1 |

这三类合起来才是完整结论：**只要没有真实故障，`[OK]` 全通过、三条汇总行全绿、`rc=0`**。
把它们当"失败"看是误解——它们是用来证明负向分支没把程序带崩的。

## 16. 收尾验收（2026-11-09 14:51）—— 23/23 全部刷完

盯梢进程自己跑到底并正常退出（exit code 0）：

```
==================================================================
  [OK] 完成 11｜跳过 0｜失败 0｜中止 0
  耗时 5842.7s
==================================================================
  本轮报告：state\last-run.json
  [OK] 全部完成
```

平台侧逐条核对（`tools/inspect_video.py`，不是本地计数）：

```
 1 71045763 watch_state=1  587s  生涯五问
 2 71045789 watch_state=1  799s  古今中外说生涯--有什么
 3 70953449 watch_state=1  667s  小赵的大学生涯故事-怎么学
 4 70953409 watch_state=1  699s  我的生涯愿景
 5 70953389 watch_state=1  605s  职业瞭望-探索缤纷职业世界
 6 70953479 watch_state=1  766s  专业洞察-聚焦职业微观事实
 7 70953429 watch_state=1  686s  职业通关-获取职业信息方法
 8 70953459 watch_state=1  571s  找到自己的职业方向
 9 70934833 watch_state=1  759s  探索自我的4方宝剑
10 71180557 watch_state=1 1026s  从识别兴趣到选择职业兴趣
11 70708777 watch_state=1  888s  能力盘点和提升
12 71180199 watch_state=1  773s  如何面对职场中的不匹配
13 70953387 watch_state=1  561s  随心不随意，决策的层次与深度
14 70953501 watch_state=1  851s  多维思考，打造全面决策力
15 70953355 watch_state=1  594s  权衡利弊，智谋职业生涯之路
16 70953443 watch_state=1 1047s  动态平衡，直观数据看决策
17 70709011 watch_state=1  577s  辨析和制定: 短、中、长计划
18 70709019 watch_state=1  698s  灵活应用:SMART和甘特图
19 70709015 watch_state=1  561s  玩转多种计划工具
20 70709021 watch_state=1  656s  拒绝眼高手低:执行力提升
21 70953325 watch_state=1  551s  英雄之旅-生涯赛
22 70953477 watch_state=1  630s  生涯管理AB选
23 70953447 watch_state=1  690s  生涯管理工具-平衡轮
→ 23 条全为 watch_state=1（看完）
```

三条命令互相印证：

| 证据 | 结果 |
|---|---|
| `run -c 1 --dry-run` | 任务总数 23，待处理 **0**，已完成 23 |
| `status` | 盯梢中：第 1 轮｜待刷 0/23｜已完成｜`[OK] 所有知到视频都已完成` |
| `doctor` | zhs/httpx/pydantic/loguru 全 OK，登录状态有效 |
| `selftest --all` | **133 条断言通过，rc=0**（核心 + 智慧课程 + 文档 61 条命令） |
| `state/daemon.json` | `pending: 0, phase: "done"` |

### 本轮实际跑量

13:13 重启盯梢 → 14:49 收工，**97 分钟刷完 11 条**（12→23），全程：

- `-12` 验证码 **0 次**（单线程、1.5 倍速、91% 阈值收尾）
- `-10` 多窗口 **0 次**
- 失败 0、中止 0

## 17. AI 智课（「学习资料」里的高等数学）接入与实测（2026-11-09 17:05~17:20）

上一轮只覆盖了知到共享课，账号里另一门 **高等数学AI课**（知到 App「学习资料」里点进去的那门）
完全看不见。本轮把它接进来了。

### 17.1 定位过程

| 试过的路 | 结果 |
|---|---|
| 知到共享课 `share/queryShareCourseInfo` | 只有大学生职业生涯规划，没有高数 |
| Hike 职教云 `getMyCourseList` | 空 |
| 智慧课程 `queryStudentAICourseList` | `rt: []` |
| 在线课程 `hike-teaching-api/.../getCourseList` | `totalCount=0`（这个接口是 GET，不是 POST） |
| polymas `teacherQueryStudentCourse` | **命中**：高等数学AI课 / `hikeAiCourse` / `courseId=AbCdEf123456` |

`list` 现在会多一行 `[AI智课]`。

### 17.2 两个卡点与解法

| 卡点 | 现象 | 解法 |
|---|---|---|
| cookie 域不匹配 | 直接请求 polymas 返回 `{"code":401,"msg":"拒绝访问，未登录!"}` | 登录 cookie 的域是 `.zhihuishu.com`，httpx 按域匹配往 `polymas.com` 发时一条都不带；改成**无域键值对**后 200 |
| 浏览器 SSO 打不通 | `oauth/login/ssoLogin?ticket=ST-...` → `{"code":500,"msg":"操作失败！"}`，课程页进不去 | 不依赖浏览器。抓包时把同一批 cookie 复制一份挂到 `.polymas.com` 域，前端就"已登录"，能正常加载课程页并暴露接口 |

### 17.3 上报协议

```
POST /student-course/study/student/record/report
{"startWatchTime":0,"endWatchTime":30,"startDate":"2026-11-09 17:11:38",
 "endDate":"2026-11-09 17:12:08","studyTotalTime":30,"clientType":"Windows",
 "courseId":"AbCdEf123456","resourceId":"res00001"}
```

播放器每 30 秒发一次，服务端按窗口时长累加 `completeRate`。
工具按窗口顺序补报，`completeRate` 到 100 就停。

### 17.4 实测

| 步骤 | 结果 |
|---|---|
| `list` | 2 门课，`[AI智课] 高等数学AI课 courseId=AbCdEf123456 classId=Cls0001` |
| `run -c 2 --dry-run` | 66 个资源，已完成 53，待刷 **13** |
| `run -c 2`（第 1 轮） | 13/13 全部 `100% 完成`，`hasStudyCount` 53 → **66**，`mustStudyProgress` 78.79% → **100.0%** |
| `run -c 2`（第 2 轮） | 已完成 66，待刷 0，`[OK] 这门课的学习资源都完成了` |
| `run -c 2 --force` | 66 条全部复查一遍；4 条停在 99.8~99.9%/92.1% 且 `isFinished=true`（`endWatchTime` 已等于 `duration`，平台取整差，补不动），其余 62 条稳定 100% |
| `status` | `[AI智课] 高等数学AI课  66 个学习资源，待刷 0，平台进度 100.0%` |
| `selftest --polymas` | **21 条断言通过，rc=0** |
| `selftest --all` | 并发 + 核心 + 智慧课程 + AI 智课 + 文档 全过，72 条命令一致，rc=0 |

### 17.5 新增/改动的文件

- `app/polymas.py`（新，约 380 行）：`PolymasClient`（课程/学期/班级/资源树/明细/上报）、
  `collect_tasks`（资源树 → 任务）、`brush_task`（30 秒窗口补报）、`run_entries`（批量 + 统计）
- `app/courses.py`：`CourseEntry.term`；polymas 课程不再标 `unsupported`；`list` 显示 `classId`
- `app/runner.py`：`run_courses` 里把 polymas 课程分流到 `polymas.run_entries`，失败/未刷满给 rc=3
- `app/__main__.py`：`status` 显示 AI 智课资源数与平台进度；新增 `selftest --polymas`
- `tools/check_polymas.py`（新）：0 联网 21 条断言
- `tools/` 下若干探查脚本（`probe_*.py` / `enter_ai_*.py` / `capture_report.py` 等），留作复现证据
- `README.md` / `USER-MANUAL.md`：新增「AI 智课」章节与两个坑的说明

### 17.6 结论

知到 App「学习资料」里的课（polymas `hikeAiCourse`）**已可刷**，与知到共享课、智慧课程并列，
`run -c all` 一次跑完三类课。