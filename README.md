# zhs-multi — 智慧树多线程刷课工具

基于 [pjm314159/zhs](https://github.com/pjm314159/zhs) 的协议实现，重新封装成一个**可双击运行、按课程勾选、多线程并发**的小工具。

支持平台：知到共享课 / Hike 职教云 / 知到 AI 智慧课程 / AI 智课（「学习资料」里的课）。
知到视频、智慧课程资料、AI 智课学习资源都支持并发；Hike 会提示并跳过。
登录方式：**只有扫码登录**（智慧树 APP → 扫一扫）。没有 APP 时可用 `login --cookie` 从浏览器导入登录态。

> 许可：本项目沿用上游 `zhs` 的 **GPL-3.0**（见 [LICENSE](LICENSE)）。`zhs` 本身需要单独安装，见下方「快速开始」。
> 只用于自己账号的学习进度管理。

## 一键启动（双击就行）

| 入口 | 说明 |
|---|---|
| **`一键刷课.exe`** | 双击出中文菜单：开刷 / 无人值守 / 看状态 / 扫码 / 体检 / 演练 / 点验证码。也能带参数：`一键刷课.exe status` |
| `zhs-multi-oneclick.cmd` | 同样的菜单（英文，纯批处理），exe 跑不了时的兜底 |
| `zhs-multi.bat` | 最原始透传：`zhs-multi.bat <任意子命令>` |

三个入口都**不会闪退**：出错会停在窗口里等你看完。10 秒不操作默认走「看状态」（只读，不会误开刷）。

> `一键刷课.exe` 是**本机编译产物**，不进仓库（也不该进：带了个人签名证书链）。
> 克隆下来后自己编一个，一条命令：
>
> ```bat
> powershell -NoProfile -ExecutionPolicy Bypass -File tools\launcher\build.ps1
> ```
>
> 不想编就直接双击 `zhs-multi-oneclick.cmd`，菜单一模一样。

### 签名与署名（可选，本机自签）

编译出来的 `一键刷课.exe` 可以自己签名，文件属性 → 数字签名里会显示：

| 项 | 值 |
|---|---|
| 签名主体 | 本机代码签名证书（`sign.ps1` 用 `Cert:\CurrentUser\My` 里带私钥的那张） |
| 时间戳 | `CN=DigiCert SHA256 RSA4096 Timestamp Responder`（带时间戳，证书过期后签名依然有效） |
| 校验状态 | `Valid` |
| 作者署名 | 文件属性 → 详细信息：公司 `YG`、产品 `zhs-multi`、版权 `© 2026 YG` |

重编 + 重签一条命令（脚本是纯 ASCII，Windows PowerShell 5.1 直接能跑）：

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File tools\launcher\sign.ps1
```

没有代码签名证书也能跑 `build.ps1`，只是签不了名。`zhs-multi.bat` / `.cmd` 签不了——
Windows 不校验批处理文件的 Authenticode，所以想不带警告就用 exe。

## 目录结构

| 文件/目录 | 说明 |
|---|---|
| `zhs-multi-oneclick.cmd` | 双击即用的菜单入口（纯批处理，不用编译） |
| `zhs-multi.bat` | 启动器，等效于 `python -m app` |
| `config.ini` | 配置：登录方式、课程序号、线程数、倍速、时间上限 |
| `app/` | 源码 |
| `app/config.py` | 读 `config.ini`、钳制参数、路径 |
| `app/login.py` | 扫码登录（CAS 一次性密码）+ Cookie 导入 |
| `app/courses.py` | 列出四类课程（知到 / Hike / 智慧课程 / AI 智课），选课 |
| `app/runner.py` | 任务队列 + 线程池 + 统计，含并发自检 `selftest` |
| `app/polymas.py` | AI 智课（polymas）学习资源刷课：资源树 + 30 秒窗口上报 |
| `app/console.py` | 线程安全输出 + 单行总览进度（多线程不打乱） |
| `app/captcha.py` | 被 -12 拦住时调起浏览器自动点掉易盾点选验证码 |
| `tools/launcher/ZhsLauncher.cs` | 一键菜单的 C# 源码（`build.ps1` 编译 / `sign.ps1` 编译+签名） |
| `tools/check_*.py` | 离线自检：核心 / 智慧课程 / AI 智课 / 文档 / 验证码 |
| `tools/yidun_solve.py` | 易盾点选识别 + 类人点击（playwright，用全局 Python 3.11） |
| `tools/replay_captcha.py` | 拿存下来的验证码图离线复盘「会点哪个」，不联网不点击 |
| `tools/inspect_video.py` | 列每条视频的平台侧元数据（`watch_state` / `video_sec`） |
| `state/cookies.json` | **运行时生成、不进仓库**：登录态 |
| `state/qrcode.png` | 运行时生成：扫码二维码（每次换码覆盖同一文件） |
| `state/last-run.json` | 运行时生成：上一轮每个视频的结果（成功/失败/耗时） |
| `state/daemon.json` | 运行时生成：盯梢进度（第几轮、还剩几条、阶段、自动点选次数），`status` 会读它 |
| `logs/run-YYYYMMDD.log` | 运行时生成：DEBUG/INFO 日志（含每次进度上报） |
| `QUICK-START.txt` | 极简上手 |
| `USER-MANUAL.md` | 详细手册（配置项、扫码登录、并发调参、FAQ） |
| `TEST-REPORT.md` | 真机实测记录：并发对照、验证码处置、AI 智课接入 |

## 快速开始（3 步）

1. **双击 `zhs-multi.bat`** —— 没登录会自动进入扫码流程，二维码落在 `state\qrcode.png`。
2. 用**智慧树 APP → 扫一扫**扫它，手机上点「确认登录」。
3. 程序列出课程表，输入序号（`1` / `1,3` / `2-4` / `all`，回车=全选），开始并发刷。

不想交互就把课程写进 `config.ini` 的 `course_list`。

## 命令行

```
zhs-multi.bat                        # 交互式：登录 → 选课 → 刷
zhs-multi.bat login                  # 扫码登录
zhs-multi.bat status                 # 总览：登录态/课程/待刷数量/上轮结果
zhs-multi.bat list                   # 课程表
zhs-multi.bat run -c all -j 8        # 8 线程刷全部课程
zhs-multi.bat run -c 1 --dry-run     # 只看任务，不动手
zhs-multi.bat probe -c 1             # 试播 45 秒：验证码限制解除没
zhs-multi.bat unblock -c 1 --then-run -j 2   # 开网页解锁验证码，解除后自动续刷
zhs-multi.bat daemon -c 1 -j 2 --every 600    # 盯梢：被拦就等，解除后自己续刷到刷完
zhs-multi.bat daemon -c 1 --solve-captcha     # 盯梢 + 被 -12 拦住时自动点掉验证码
zhs-multi.bat captcha                         # 自动点掉易盾点选验证码
zhs-multi.bat captcha --check                 # 只检查自动点选环境能不能用
zhs-multi.bat selftest -j 4          # 并发自检（不联网）
zhs-multi.bat doctor                 # 环境体检
```

| 子命令 | 干什么 |
|---|---|
| `status` | 登录态、课程、每门课待刷/已完成数量、上一轮结果 |
| `list` | 只列课程表 |
| `run` | 选课 + 多线程刷视频 |
| `login` | 扫码 / 导入 Cookie |
| `probe` | 试播十几秒，判断 -12 解除了没 |
| `unblock` | 开网页等你点验证码，解除后自动续刷 |
| `daemon` | 盯梢模式：定时探测，解除后自动续刷，直到刷完或到 `--cycles/--hours` 上限；`--solve-captcha` 可自动点验证码 |
| `captcha` | 用真实浏览器自动点掉易盾点选验证码（`--tries` / `--min-score` / `--check`） |
| `selftest` | 自检（不联网）：并发 / `--core` 核心 / `--ai` 智慧课程 / `--docs` 文档一致性 / `--all` 全跑 |
| `doctor` | 环境体检 |

`run` 的参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `-c, --courses` | 课程序号 `1` / `1,3` / `2-4` / `all`，留空=交互 | 交互 |
| `-j, --jobs` | 并发线程数 1–32 | 2 |
| `-s, --speed` | 倍速 1.0–2.0 | 1.5 |
| `--threshold` | 视频完成阈值 0.1–1.2（>1 强制重刷） | 0.91 |
| `-l, --limit` | 本轮总时间上限（分钟） | 0 不限 |
| `--dry-run` | 只展开任务列表 | — |
| `--force` | 已完成的也重刷 | false |
| `--with-homework` | 刷完接着做章节作业 | false |
| `--auto-unblock` | 撞到验证码就开浏览器等你点，解除后自动续刷 | false |
| `--no-dashboard` | 关掉单行总览，改逐条打印 | — |
| `-v, --verbose` | DEBUG 日志 | false |

## 智慧课程（AI 课）的资料刷课

知到的「智慧课程」不是按视频算，而是按**知识点**算，每个知识点下面挂一堆「学习资料」：

| 资料类型 | 平台字段 | 工具怎么处理 |
|---|---|---|
| 文本 | `type=2, distribute=1` | `complete_resource` 直接标记完成 |
| PPT | `type=1, distribute=4` | `complete_resource` 标记完成（课件 URL 也会收集） |
| 视频 / 课程视频 | `type=1,d=3` / `type=2,d=2` | `play_video` 按 `schedule` 续播到阈值 |
| 其它类型 | 兜底 | `complete_resource` |

调度方式：**一个知识点 = 一个任务**，知识点之间用线程池并发；同一知识点内部按原顺序串行，
和平台自己算 `studyProgress` 的方式一致。已完成的资料（`studyStatus=1`）不会重复提交。

| 配置项（`[ai]`） | 默认 | 说明 |
|---|---|---|
| `learn_optional` | true | 是否连「选学」资料（`resourcesSyncType != 1`）一起刷 |
| `video_threshold` | 90 | 智慧课程视频完成阈值（0–100） |

离线自检（不需要账号里有 AI 课）：

```
zhs-multi.bat selftest --ai
```

它用假接口验证：章节树→知识点展开、资料类型分类、文本/PPT/视频分别走对接口、
选学资料的过滤开关、以及两种任务混跑时分发正确。

## AI 智课（知到 App「学习资料」里的那类课）

知到 App 里点「学习资料」看到的那门课（例如 **高等数学AI课**）不在知到共享课里，
它是 **polymas** 平台的 `hikeAiCourse`，域名 `hike-teaching-center.polymas.com`。
这套工具单独接了一层，`list` 里会显示成 `[AI智课]`：

```
  2) [AI智课] 高等数学AI课    courseId=AbCdEf123456 classId=Cls0001
```

它的进度不是「章节 + 视频」，而是**一棵学习资源树**：视频（mp4）、课件（pdf），
每个资源有 `completeRate`（0–100）和 `isFinished`。66 个资源、必修 100% 才算完。

**怎么刷的**：播放器每 30 秒往

```
POST /student-course/study/student/record/report
{"startWatchTime":0,"endWatchTime":30,"studyTotalTime":30,...,"resourceId":"..."}
```

上报一个窗口，服务端按窗口累计时长算 `completeRate`。工具直接按窗口顺序补报，
直到 100%，所以一个 12 分钟的视频几十秒就补完了，不用真的播一遍。

```bash
zhs-multi.bat list              # 看到 [AI智课] 那一行
zhs-multi.bat run -c 2          # 只刷它
zhs-multi.bat run -c all        # 知到 + AI 智课 一起
zhs-multi.bat run -c 2 --force  # 已完成的也补一遍，把 99.8% 这种取整差补满
zhs-multi.bat status            # 每门课待刷多少 + 平台进度
```

实测（2026-11-09）：13 个未完成视频，一轮 2 分钟内全部 100%，
`mustStudyProgress` 从 78.79% → **100.0%**。

**两个坑（已绕过，写在这里省得下次再踩）**

| 现象 | 原因 | 处理 |
|---|---|---|
| 直接请求 polymas 接口返回 `401 拒绝访问，未登录` | 登录 cookie 的域是 `.zhihuishu.com`，httpx 按域匹配往 `polymas.com` 发时一个都不带 | 把 cookie 摊平成**无域**键值对再发 |
| 浏览器 SSO 进课程页 `{"code":500,"msg":"操作失败！"}` | `oauth/login/ssoLogin` 那条链对 AI 课不通 | 不用浏览器：cookie + uuid 直接调 API；要抓包就把同一批 cookie 复制一份挂到 `.polymas.com` 域 |

## 配置速览（config.ini）

```ini
[common]
course_list =          ; 1,3-5 / all；留空=交互选
jobs = 2               ; 并发线程数
speed = 1.5            ; 倍速
threshold = 0.91       ; 完成阈值
limit = 0              ; 总时间上限（分钟）
delay = 0.6            ; 任务间隔（秒），防风控
dashboard = true       ; 单行总览进度

[ai]
api_key =              ; 不做作业可以不填
base_url = https://api.deepseek.com/v1
model = deepseek-chat

[homework]
enable = false         ; 刷完视频接着做作业
threshold = 100
max_submit = 2
```

全部字段、两种登录细节、并发调参见 **《USER-MANUAL.md》**。

## 并发是怎么做的

- 任务粒度 = **单个视频**，线程池按 `jobs` 取任务，每个 worker 独占一个会话与自己那份课程上下文（避免共享可变状态）。
- 输出统一走锁，进度合成一行 `W1:play[####......]45% 第一章 1.1 | W2:...`，不会互相覆盖。
- 每任务之间插 `delay` 秒随机间隔；`CaptchaRequired`（-12）或时长上限（-9）一出现就**全局停**，不硬顶。
- 单条视频失败（网络抖动之类）会在并发跑完后**自动单线程补跑一次**，结果按视频去重，不会重复计数。
- **实测已知行为**：同一账号并发上报时，平台会回 `-10 疑是多窗口观看`。`zhs` 的处理是刷新令牌后重试，日志里能看到 `Multi-window detected ... Progress report succeeded after token refresh`，进度照常记上。
- **实测的临界点**：3 线程、7 分钟 → `-10` 61 次后升级成 `-12`（要图形验证码），工具当场全局停止，期间完成 3 个视频。所以默认 `jobs = 2`；要更稳就 `-j 1`。
- 一旦 `-12` 出现：先 `zhs-multi.bat captcha` 自动点掉（点不掉再人手上浏览器），然后降线程数重跑。

## 验证码（-12）与自动点选

平台风控触发后会回 `-12`，播放页弹出**网易易盾点选验证码**：图上散布若干彩色字母，提示语像
`请点击绿色小写u`、`请点击数字5朝向一样的大写H`。这一关在前端完成校验，接口层绕不过，必须真点一次。

```
zhs-multi.bat captcha              # 起真实浏览器，识别并点中目标字符，最多 4 轮
zhs-multi.bat captcha --check      # 只体检：能不能找到装了 playwright 的 Python
zhs-multi.bat probe -c 1           # 点完确认 -12 解除没（退出码 0 = 已解除）
```

它做的事：解析提示语 → 取背景图 → 按颜色切出候选字符 → 用系统字体模板做 IoU 匹配 →
用缓动 + 抖动的鼠标轨迹点中 → 失败就点"刷新"换一张。实测通过后 `probe` 立刻返回 0。

依赖（只需一次）：

```
py -3.11 -m pip install playwright
py -3.11 -m playwright install chromium
```

## 常见问题速查

| 现象 | 处理 |
|---|---|
| 扫码提示过期 | 二维码 3 分钟有效，扫完立刻在手机上点「确认登录」，工具会自己换新码 |
| 没有智慧树 APP | 用 `login --cookie "JSESSIONID=...; CASTGC=..."` 从浏览器导入登录态（见手册 4.3） |
| 一开跑就 `需要图形验证码，停止` | 平台风控（-12）。先 `captcha` 自动点掉；不行就 `unblock -c 1 --auto --then-run -j 2`；长期挂着用 `daemon -c 1 -j 2 --every 600 --solve-captcha` |
| 提示"学习时长已达上限" | 平台每天/每课有上限（-9），隔天再刷 |
| 想看某门课还剩多少 | `run -c N --dry-run` 会打印"共 X 个视频，已完成 Y 个" |
| 跑一半断了 | 直接重跑同一条命令，已完成的会自动跳过 |

## 与上游 zhs 的关系

协议（AES 加密参数、`ev` 编码、CAS 换票、题库/LLM 答题）全部复用已安装的 `zhs` 包，不改其源码；
本项目只做三件事：**扫码登录**、**课程选择**、**多线程调度**。
因此 `zhs` 升级后本工具同样受益；作业/考试仍可继续用 `zhs` 原命令。

## 安全提醒

`state\cookies.json` 等价于你的登录态，`config.ini` 里可能存着密码，**不要**发给别人或传到公开仓库。
