# zhs-multi 使用手册

> 面向"我只想让它把课刷完"和"我想知道它到底怎么动的手"两种人。前半部分操作，后半部分原理。

---

## 1. 它是什么

| | 原版 `zhs` | `zhs-multi`（本项目） |
|---|---|---|
| 课程选择 | `-c <courseId>` 或 `--url` | 先列表再选：`1` / `1,3` / `2-4` / `all` / 交互 |
| 并发 | 串行，一门课从头到尾 | 线程池，**任务粒度=单个视频**，`jobs` 可调 1–32 |
| 登录 | 只有扫码 | 只有扫码（本项目明确不保留账号密码登录） |
| 输出 | 单线程进度条 | 多线程合成一行总览，互不打架 |
| 作业/考试 | 有 | 复用原版（`--with-homework` 或直接 `zhs` 命令） |

底层协议（AES 加密参数、`ev` 编码、CAS 换票、题库、LLM 答题）**全部复用已安装的 `zhs` 包**，本项目不改它的源码，只做登录增强 + 课程选择 + 并发调度。

---

## 2. 环境

- Windows，Python 3.11+（本机是 3.14.6）
- 依赖：`zhs`（已装在本机的 `..\.venv-zhs`）、`httpx`、`pydantic`、`loguru`
- 启动器会自动去找 `..\.venv-zhs\Scripts\python.exe`；找不到就用系统 `python`

### 三个入口，双击哪个都行

| 入口 | 双击后 | 备注 |
|---|---|---|
| **`一键刷课.exe`** | 中文菜单（7 项 + 退出） | 推荐；靠 Windows 自带的 .NET 编译器现场编的，源码 `tools/launcher/ZhsLauncher.cs` |
| `zhs-multi-oneclick.cmd` | 同样的菜单（英文） | exe 被杀软拦下时的兜底 |
| `zhs-multi.bat` | 直接透传 `python -m app` | 给命令行用：`zhs-multi.bat status` |

菜单 7 项对应：开刷 / 无人值守盯梢 / 看状态 / 扫码登录 / 环境体检 / 演练 / 点验证码。
**10 秒不操作默认走「看状态」**——只读，不会误开刷。三个入口出错都会停在窗口里，不会一闪就没。

`一键刷课.exe` 是签名过的（Authenticode `Valid`），签名主体是本机代码签名证书（自签，`CN=... Local`），带 DigiCert 时间戳；
文件属性里还能看到作者署名：公司 `YG`、版权 `© 2026 YG`、版本 `1.0.0.0`。
重编 + 重签：

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File tools\launcher\sign.ps1
```

体检：

```
zhs-multi.bat doctor
```

输出会告诉你：Python 版本、配置路径、Cookie 是否存在、线程数/倍速、`zhs` 版本、passport 是否可达、登录态是否有效。

---

## 3. 目录

```
zhs-multi\
├─ zhs-multi.bat          启动器
├─ config.ini             全部配置
├─ app\
│  ├─ __main__.py         CLI：login / list / run / probe / selftest / doctor
│  ├─ config.py           config.ini → AppSettings（含范围校验）
│  ├─ login.py            扫码登录 + Cookie 导入（本文第 4 节）
│  ├─ courses.py          课程枚举、选择、展开成视频任务
│  ├─ runner.py           线程池调度 + 统计 + selftest
│  ├─ console.py          线程安全输出 + 单行总览
│  ├─ captcha.py          自动点掉易盾验证码（调 tools/yidun_solve.py）
│  └─ session_factory.py  造 zhs 会话、读写 Cookie
├─ state\                 运行时生成
│  ├─ cookies.json        登录态（等于你的账号）
│  ├─ qrcode.png          扫码二维码
│  ├─ last-run.json       上一轮逐条结果（成功/失败/耗时）
│  └─ daemon.json         盯梢进度（第几轮/还剩多少/阶段/自动点选次数）
└─ logs\run-YYYYMMDD.log  日志（每次进度上报、错误栈都在这）
```

---

## 4. 登录

### 4.1 扫码（默认，最稳）

```
zhs-multi.bat login
```

1. 程序用 CAS 拿二维码 → `state\qrcode.png`
2. 手机「智慧树 APP → 扫一扫」→ 点确认
3. 拿到 Cookie 存进 `state\cookies.json`

细节：
- 二维码约 180 秒过期，程序最多自动换 5 次码，**每次都写到同一个路径**，换码后要重新打开文件
- 想直接在终端里看码：`zhs-multi.bat login --terminal-qr`
- 登录后 `run` 会自动复用 Cookie，直到它过期

### 4.2 没有 APP 的时候：导入浏览器 Cookie

1. 浏览器登录智慧树 → F12 → Network → 随便点一个请求 → Request Headers 的 `Cookie:` 整行复制
2. 粘进来：

```
zhs-multi.bat login --cookie "JSESSIONID=...; CASTGC=...; SERVERID=...; ..."
```

也支持文件：

```
zhs-multi.bat login --cookie-file state\my_cookie.txt
```

文件里可以是 `name=value; name2=value2`，也可以是 JSON（对象或 `[{"name":..,"value":..}]` 列表）。
导入后会提示有没有 `CASTGC`/`JSESSIONID`——没有的话基本等于没登录。

### 4.3 登录态什么时候失效

- Cookie 存在 `state\cookies.json`，`run` / `probe` / `daemon` 一直复用它
- 接口报"未登录"时，工具会自动走一次扫码登录（加 `--no-login` 可以关掉这个行为）
- 想强制重登：删掉 `state\cookies.json`，或把 `config.ini` 里 `use_cookies` 改成 `false`
- 账号密码登录已按需求移除，只剩扫码；`login` 不再接受 `-u/-p/--mode`



---

## 5. 选课

```
zhs-multi.bat list
```

```
  可刷课程共 3 门：
   1) [知到] 大学生职业生涯规划        courseId=1000000000 racId=4e5b...
   2) [知到] 形势与政策                courseId=1000222211 racId=7c1d...
   3) [Hike] 高职英语                   courseId=2033445
```

选课四种写法：

| 写法 | 含义 |
|---|---|
| `-c 1` | 第 1 门 |
| `-c 1,3` | 第 1、3 门 |
| `-c 2-4` | 第 2 到第 4 门 |
| `-c all` | 全部（`*` 也行） |
| 不写 `-c` | 交互输入，直接回车=全选 |

也可以写死在 `config.ini` 的 `course_list`，命令行 `-c` 优先级更高。

> 只能选**账号里已有的课**。没选上的课先去智慧树网页端选课，工具不会帮你报名。
> 目前视频并发只对**知到**课程生效；Hike 课程会被跳过并提示（可用原版 `zhs play` 处理），
> **智慧课程（AI 课）按知识点并发刷资料**，见第 11 节。

---

## 6. 多线程

### 6.1 怎么并发的

```
选中的课程 ──展开──> [视频1][视频2][视频3]...[视频N]   ← 任务队列
                        │
              ┌─────────┼─────────┐
            W1        W2        W3        ← jobs 个线程，各自抢任务
              │         │         │
         独立 session  独立 session  独立 session
         独立 ctx 副本  独立 ctx 副本  独立 ctx 副本
```

关键设计：

- **任务粒度是单个视频**。一门课 23 个视频、`jobs=3`，就是 3 条视频同时推进，而不是 3 门课各推一条。
- **每个线程一个 `ZhsSession`**：`httpx.Client` 虽然线程安全，但 Cookie 罐共享会让进度上报互相踩；独立会话 + 复制的 Cookie 最省心。
- **每个线程一份课程上下文深拷贝**（`model_copy(deep=True)`）：上下文里有可变的计时字段，共享会串。
- **章节树只拉一次**，主线程预取后各线程复制，省掉 N 倍的接口请求。
- **输出走一把锁**：所有 `print` 被替换成 sink，带进度条的刷新行只更新状态，其它行排队插到状态行上方，所以不会出现花屏。

### 6.2 线程数怎么定

| jobs | 适用 |
|---|---|
| 1 | 等同原版串行，最稳。**实测 6 分钟连续刷完一条视频、日志 0 次 `-10`** |
| 2 | **默认**。速度约 2 倍；但 `-10 多窗口` 仍会周期性出现（12~20 秒一次），靠刷新令牌续上 |
| 3–4 | 更快，但实测 **3 线程跑到第 7 分钟就被要求图形验证码（-12）** |
| >4 | 不建议。同一账号并发上报太多，几乎必然被拦 |

改法：

- 临时：`-j 6`
- 永久：`config.ini` → `[common] jobs = 6`

### 6.3 防风控的几个默认动作

- `speed` 默认 1.5（不是 2.0），上报节奏带 ±0.15 抖动
- 进度上报间隔 30±5 秒（原版逻辑）
- 线程**错峰启动**：第 N 个线程晚 `(N-1) × stagger` 秒才开工（`stagger = 2.0`），避免几个线程同一瞬间开播、同时上报心跳被判成多窗口（`-10`）。设 0 可关闭
- 任务之间插 `delay = 0.4` 秒，再乘 0.85~1.3 的随机系数，节奏不固定
- 视频末尾按 `threshold = 0.91` 收尾，不追 100%
- 单条失败不会直接放弃：并发跑完后单线程补跑一次，状态按视频去重
- 出现 **`CaptchaRequired`（-12）** 或 **学习时长上限（-9）**：**全局立刻停**，正在跑的视频也不再补。硬顶只会让账号更难看

### 6.4 实测：`-10 疑是多窗口观看`

这是并发刷课时**一定会看到**的东西，不是 bug：

```
WARNING | Multi-window detected, refreshing token: API error -10: 疑是多窗口观看
INFO    | Progress report succeeded after token refresh
```

平台给每次进度上报发一个"当前在看的视频"令牌，两个线程交替上报就会互相把令牌顶掉，于是回 `-10`。
`zhs` 的处理是**刷新令牌后立刻重试**，所以进度照样记上（日志第二行就是成功）。

实测数据（3 线程、23 个视频）：`-10` 在 7 分钟内出现 61 次，然后升级成一次真正的
`Progress report failed after retry: 服务端要求验证码`（即 `-12`），工具当场全局停止，期间完成了 3 个视频。
也就是说：3 线程能吃到的加速是真实的，但同一账号大概撑不过十分钟就会被要求验证码。

后来的补充实测（2026-11-09 12:12~12:24，同一账号）：

| 线程数 | `-10` 频率 | 结果 |
|---|---|---|
| 3 | 7 分钟内 61 次 | 升级成 `-12`，全局停 |
| 2 | 每 12~20 秒一次 | 每次都靠刷新令牌续上，进度照涨，但风险在累积 |
| 1 | **0 次** | 6 分钟刷完一条视频，日志无告警 |

结论：想要「一直刷不被拦」就用 `-j 1`；想要速度、能接受偶尔被拦，用 `-j 2` 配
`daemon --solve-captcha`（被拦了自动点掉，不用管）。

所以默认给 **2**：

| 现象 | 判断 |
|---|---|
| 日志里 `-10` 刷屏，进度仍然在涨 | 正常，不用管 |
| 出现 `-12` / 工具打印"要求图形验证码"并停下 | 平台风控升级。`zhs-multi.bat captcha` 自动点掉（点不掉再人工），然后 `-j 2` 或 `-j 1` 重跑 |
| 出现 `-9` 学习时长上限 | 当天到顶，隔天再刷 |

想彻底没有 `-10`：`-j 1`（等于原版串行）。

### 6.5 `-l` 是软上限

`-l 60` 表示"到第 60 分钟就**不再开新视频**"，正在播的那条会继续播完（否则进度白跑）。
所以实际耗时可能是 60 分钟 + 最后一两条视频的尾巴。

### 6.6 并发自检（不联网）

```
zhs-multi.bat selftest -j 4 -n 12
```

```
  [OK] 完成 12/12，墙钟 3.02s，串行预期约 12.0s
  [OK] 并发生效：加速约 4.0x
```

这条命令只跑假任务，不碰平台，用来验证"线程真的在并行、输出不乱"。

---

## 7. 配置项全表（config.ini）

### [common]

| 键 | 默认 | 说明 |
|---|---|---|
| `use_cookies` | true | 复用 `state\cookies.json`；false = 每次都登录 |
| `course_list` | 空 | `1,3-5` / `all`；空 = 交互选 |
| `jobs` | 2 | 并发线程数 1–32 |
| `speed` | 1.5 | 倍速 1.0–2.0 |
| `threshold` | 0.91 | 完成阈值 0.1–1.2（>1 等于强制重刷） |
| `limit` | 0 | 本轮总时间上限（分钟），0 = 不限 |
| `delay` | 0.6 | 任务间隔（秒），实际乘 0.85~1.3 抖动 |
| `stagger` | 2.0 | 线程错峰启动秒数（第 N 个线程晚 (N-1)×stagger 开工，0=关闭） |
| `force` | false | 已完成的也重刷 |
| `dashboard` | true | 单行总览；false = 逐条打印 |
| `unblock_wait` | 900 | 撞验证码后自动解锁最多等多少秒 |

### [ai]（作业/考试用，可不填）

| 键 | 默认 | 说明 |
|---|---|---|
| `api_key` | 空 | OpenAI 兼容接口的 key；空 = 作业只能随机作答 |
| `base_url` | `https://api.deepseek.com/v1` | 接口地址 |
| `model` | `deepseek-chat` | 模型名 |

### [homework]

| 键 | 默认 | 说明 |
|---|---|---|
| `enable` | false | 刷完视频接着做章节作业（等同 `--with-homework`） |
| `threshold` | 100 | 作业达标线 |
| `max_submit` | 2 | 最大重做次数；0 = 不限 |

### [output]

| 键 | 默认 | 说明 |
|---|---|---|
| `verbose` | false | DEBUG 日志（等同 `-v`） |
| `color` | true | 保留字段 |

配置值会被**范围钳制**：`jobs` 超出 1–32、`speed` 超出 1.0–2.0 都按边界算，不会因为手滑写 100 就起 100 个线程。

---

## 8. 命令行参考

```
zhs-multi.bat [-c 配置文件] <子命令> [参数]
```

子命令一览：`login` `list` `run` `probe` `unblock` `daemon` `captcha` `selftest` `doctor`

### status

```
zhs-multi.bat status
```

一眼看全：配置文件路径、线程数/倍速、登录态是否有效、每门课的视频总数与待刷数量、
上一轮的成功/失败/中止与停止原因；如果有盯梢进程在跑，还会多一行
`盯梢中   : 第 3 轮｜待刷 14/23｜阶段 probe｜自动点选 0 次｜42 秒前`（数据来自 `state\daemon.json`，
超过 30 分钟没更新就显示成「已退出」）。最后给一条建议命令。只读，不改任何东西。

### login

| 参数 | 说明 |
|---|---|
| `--terminal-qr` | 终端里画二维码 |
| `--cookie "a=1; b=2"` | 手动导入 Cookie（浏览器复制） |
| `--cookie-file PATH` | 从文件导入 Cookie（文本或 JSON） |

### list

| 参数 | 说明 |
|---|---|
| `--json` | JSON 输出（给脚本用） |
| `--no-login` | 没 Cookie 就报错，不自动登录 |

### run

| 参数 | 说明 |
|---|---|
| `-c/--courses SPEC` | 课程序号（见第 5 节） |
| `-j/--jobs N` | 并发线程数 |
| `-s/--speed F` | 倍速 |
| `--threshold F` | 完成阈值 |
| `-l/--limit N` | 总时间上限（分钟） |
| `--dry-run` | 只列任务，不动手 |
| `--force` | 已完成的也重刷 |
| `--with-homework` | 刷完接着做作业 |
| `--auto-unblock` | 撞到验证码就开浏览器等你点，解除后自动续刷 |
| `--no-dashboard` | 关总览，逐条打印 |
| `--no-login` | 没 Cookie 直接退出 |
| `-v/--verbose` | DEBUG 日志 |

### selftest / probe / unblock / doctor

- `selftest`：见 6.6（并发）；`--core` 核心链路；`--ai` 智慧课程；`--docs` 文档一致性；`--all` 全跑
- `doctor`：见第 2 节（环境体检）
- `probe`：试播一条视频若干秒，判断 `-12` 验证码限制解除了没
- `unblock`：自动开播放页，等你在网页里点掉验证码，解除后可直接续刷（`--auto` 会先试自动点选）
- `captcha`：官方自动点选入口，见 8.9

```
zhs-multi.bat probe -c 1 -t 45
zhs-multi.bat unblock -c 1 --auto --then-run -j 2
zhs-multi.bat captcha
```

`unblock` 参数：

| 参数 | 说明 |
|---|---|
| `--wait N` | 最多等多少秒（默认 900） |
| `--interval N` | 每隔多少秒重新探一次（默认 30） |
| `--no-open` | 不自动开浏览器，只打印链接 |
| `--then-run` | 解除后立刻开刷 |
| `--auto` | 先用浏览器自动点掉验证码，不行再走人工等待 |
| `--auto-tries N` | 自动点选最多几轮（默认 4） |
| `-j N` | 续刷用的线程数 |

| 退出码 | 含义 |
|---|---|
| 0 | 能正常上报，可以开刷 |
| 3 | 仍在验证码状态，按打印的 `studyh5` 链接去点掉 |
| 4 | 其它错误，看日志 |

---

### captcha

```
zhs-multi.bat captcha                 # 自动点掉易盾点选验证码，最多 4 轮
zhs-multi.bat captcha --tries 6 --min-score 0.55
zhs-multi.bat captcha --check         # 只体检，不点
zhs-multi.bat captcha --rac <课程id>   # 指定打开哪门课的播放页
```

| 参数 | 说明 |
|---|---|
| `--tries N` | 最多几轮（每轮失败会点"刷新"换一张） |
| `--min-score F` | 识别置信度下限（默认 0.6） |
| `--check` | 只检查环境能不能用 |
| `--rac ID` | 播放页的课程 id（`recruitAndCourseId`）；不填就自动取账号里第一门知到课 |

原理（`tools/yidun_solve.py`）：抓提示语 → 下载背景图 → 墨迹连通域 → 按色块主色命名颜色 →
用系统字体模板（6 字体 × 5 旋转）算「这块有多像目标字符」→ 缓动 + 抖动的鼠标轨迹点中。
识别对了点一下，`-12` 立刻解除（实测 `probe` 直接返回 0）。

提示语有三种写法，都能处理：

| 写法 | 含义 | 处理 |
|---|---|---|
| `请点击绿色小写u` | 颜色 + 字符都写明 | 按颜色筛，再比「像不像 u」 |
| `请点击小写h颜色一样的大写Y` | 先找参照字符 h，取它的**颜色** | 找 h → 取色 → 同色里挑最像 Y 的 |
| `请点击绿色大写L朝向一样的数字3` | 先找参照字符 L，取它的**旋转角** | 找 L → 取角度 → 同朝向里挑最像 3 的 |

另外 `侧向/倾斜`（"要歪的那个"）会优先挑旋转角不为 0 的色块；`圆锥` 这类认不出的参照词
就当没有约束（实测直接取最像目标字符的那个也能过）。

关键判据不是"这块最像什么字"，而是**"这块有多像目标字"**。早期版本先猜字符再比字符串，
遇到 `Y`/`U`/`5` 经常猜成 `d`/`I`/`l` 而点错（分数 0.78~0.82 却全错）；改成按目标字符算
相似度后，实测 0.750 就一次过（阈值 0.34）。

一张图最多试 `--per-attempt`（默认 3）个候选：点错了弹窗还在就试下一个；提示语变了说明图
换了，直接重新解析。想换页面自测用 `--url`，只想验解析用 `--selftest-parse`。

依赖只需要装一次：

```
py -3.11 -m pip install playwright
py -3.11 -m playwright install chromium
```

### daemon

| 参数 | 说明 |
|---|---|
| `-c/--courses SPEC` | 盯哪几门课（默认按配置） |
| `-j/--jobs N` | 续刷线程数 |
| `--every N` | 每轮间隔秒数（默认 600） |
| `--cycles N` | 最多轮数（0 = 不限） |
| `--hours F` | 最多盯多少小时（0 = 不限） |
| `--solve-captcha` | 被 `-12` 拦住时自动点验证码，点过就接着刷 |

```
zhs-multi.bat daemon -c 1 -j 2 --every 600 --solve-captcha --hours 8
```

退出码：0 全部刷完；2 没有任务；3 到上限没刷完、或被拦太久、或登录态失效。

盯梢期间的三个自我保护：

- 探测那一步抛异常（网络抖动之类）→ 记一句「先按可刷处理」继续刷，不会因为一次探测失败就退出
- 连续 3 轮都提示登录态失效 → 停下并打印 `zhs-multi.bat login`，不空转（扫码得人来扫，空转没意义）
- 撞到学习时长上限（`-9`）→ 不再硬跑 `run`（那只会再失败一次、白打接口），先等一轮；连续 3 次就
  判定「今天这门课到顶了」，`state/daemon.json` 记 `phase=limit`，`status` 显示「学习时长到顶」，
  退出码 3

## 9. 输出长什么样

```
  [OK] 登录状态有效
  [OK] 已选 1 门课程：大学生职业生涯规划
  展开课程：[知到] 大学生职业生涯规划 …
    [知到] 大学生职业生涯规划：23 个视频，已完成 0 个

  任务总数 23，待处理 23，已完成 0

  并发线程数：3｜任务数：23｜倍速：1.5｜阈值：0.91
------------------------------------------------------------------
W1:play [####......] 45% 1.1 职业生涯概述 | W2:play [##........] 20% 1.2 自我认知 | W3:play [######....] 62% 2.1 决策模型
```

结束时：

```
==================================================================
  [OK] 完成 20｜跳过 3｜失败 0｜中止 0
  耗时 1284.3s
==================================================================
```

- `跳过` = 之前已经刷完的视频（`watchState=1`）
- `失败` 会列出视频名与 videoId，并写进日志
- `中止` = 撞到验证码/时长上限，会打印原因

日志在 `logs\run-YYYYMMDD.log`，保留 7 天，10MB 滚动。

---

## 10. 常见问题

**Q：扫码后一直不成功？**
二维码 3 分钟过期。程序会换码，但路径不变——很多人的问题是拿旧截图去扫。换码后重新打开 `state\qrcode.png`。

**Q：能用账号密码登录吗？**
不能，这一版只保留扫码。没有 APP 就用 `login --cookie "..."` 从浏览器导入登录态。

**Q：为什么一开跑就停了，说"需要图形验证码"？**
平台风控（`-12`）。程序会把该课程的网页播放页链接打出来：

```
https://studyh5.zhihuishu.com/videoStudy.html#/studyVideo?recruitAndCourseId=xxxx
```

浏览器打开它，随便播一小段，弹出的验证码点掉；然后 `zhs-multi.bat probe -c 1` 确认解除（退出码 0），
再用 `-j 2` 或 `-j 1` 重跑。3 线程实测 7 分钟就会被顶到这一步。

不想守着的话，把这一步交给盯梢模式：

```
zhs-multi.bat daemon -c 1 -j 2 --every 600 --hours 6
```

它每 10 分钟探一次（`--every 600`）：还被拦着就继续等，一解除立刻开刷，刷完自动退出 0；
到 `--hours` / `--cycles` 上限还没刷完则退出 3。开着窗口不管它就行。

为什么必须人工点一次：这关是网易易盾的验证码（播放页挂 `captcha.253.com/Captcha.js`，
容器是 `#captchaYidun`），校验结果由播放器回传服务端，接口层绕不过；
而风控标记绑在从 `CASLOGC` cookie 解析出来的 `uuid` 上，也就是"账号+设备"，
换 session、换视频、降倍速、改阈值都躲不掉（都实测过）。

**Q：报"学习时长已达上限"？**
平台侧（`-9`），当天这门课到顶了。隔天再刷。

**Q：`--force` 会不会重复计入时长？**
会重新走一遍播放上报。已完成的视频一般没必要 `--force`，除非平台判定没记上。

**Q：能刷 Hike / AI 的课程吗？**
知到和智慧课程都支持：知到按视频并发，智慧课程按知识点并发、知识点内的文本/PPT/视频资料逐条刷（见第 11 节）。
Hike 仍走原版命令：`zhs play -c <courseId>`。

**Q：断网/关窗口了怎么办？**
进度在平台侧，直接重跑同一条命令，已完成的自动跳过。

**Q：为什么 `-j 8` 没比 `-j 3` 快多少？**
视频时长是硬上限，而且每线程都要走"取流→上报→答题"的节奏。8 线程常被平台限速拖平，还更容易 `-12`。

---

## 11. 智慧课程（AI 课）的资料刷课

知到除了普通共享课，还有一类「智慧课程」。它不按视频算进度，而是按**知识点**算，每个知识点下面挂一堆「学习资料」：

| 资料 | 平台字段 | 处理方式 |
|---|---|---|
| 文本 | `resourcesType=2`、`distributeType=1` | 调 `studyRecord/completed` 直接标完成 |
| PPT | `type=1`、`dist=4` | 同上，并收集课件 URL |
| 视频 | `type=1,d=3`；`type=2,d=2` | `play_video`：按 `schedule` 续播到阈值 |
| 其它 | 兜底 | 标完成 |

调度粒度：**一个知识点 = 一个任务**。知识点之间用线程池并发，同一知识点内部按接口返回顺序串行——
和平台自己累加 `studyProgress` 的方式一致，不会因为乱序卡住。已完成的资料（`studyStatus=1`）跳过，不重复提交。

```ini
[ai]
learn_optional = true    ; 连「选学」资料一起刷（resourcesSyncType != 1）
video_threshold = 90     ; 智慧课程视频完成阈值 0-100
```

两条限制：

- 智慧课程**不走**知到网页播放器，所以 `-12` 那一套（`probe` / `unblock` / `--auto-unblock`）对它不适用；
  撞到限流按普通失败处理，进补跑。
- 智慧课程的作业 / 考试仍由原版 `zhs` 负责，本工具只做「学习资料」这一层。

离线自检（账号里没有 AI 课也能跑）：

```
zhs-multi.bat selftest --ai
```

覆盖：知识点展开、资料类型分类、文本/PPT/视频分别走对接口、选学开关过滤、知到 + 智慧课程混跑时的任务分发。

---

## 11.1 AI 智课（「学习资料」里的课，polymas 平台）

App 里点「学习资料」看到的那门课（本次是 **高等数学AI课**）和上面第 11 节的智慧课程**不是同一个系统**：

| | 智慧课程（第 11 节） | AI 智课（本节） |
|---|---|---|
| 平台 | `kg-ai-run.zhihuishu.com` | `hike-teaching-center.polymas.com`（接口在 `cloudapi.polymas.com`） |
| 课程类型 | `aiCourse` | `hikeAiCourse` |
| 进度单位 | 知识点 `studyProgress` | 学习资源 `completeRate` 0–100 |
| `list` 里的标签 | `[AI]` | `[AI智课]` |

**它是怎么算进度的**：每个资源（视频 mp4 / 课件 pdf）有 `completeRate`，
播放器每 30 秒上报一个窗口：

```
POST /student-course/study/student/record/report
{"startWatchTime":0,"endWatchTime":30,"startDate":"...","endDate":"...",
 "studyTotalTime":30,"clientType":"Windows","courseId":"...","resourceId":"..."}
```

服务端把窗口时长累加成百分比。工具照着窗口顺序补报，到 100% 就 `isFinished`。
一个 12 分钟的视频 ≈ 24 个窗口，几十秒补完，不用真播。

**用法**

```bash
zhs-multi.bat list                 # [AI智课] 高等数学AI课 会列出来
zhs-multi.bat run -c 2             # 单独刷它
zhs-multi.bat run -c all           # 知到 + AI 智课一起刷
zhs-multi.bat run -c 2 --force     # 把 99.8% / 92.1% 这类"平台取整差"再补一次
zhs-multi.bat status               # 显示 66 个资源、待刷多少、平台进度
```

**本次实测（2026-11-09）**

```
展开 AI 智课：高等数学AI课：66 个学习资源，已完成 53 个，待刷 13 个
[1/13] … [13/13] 全部 100% 完成
总进度 99.87%（66/66 个资源），mustStudyProgress 100.0%
```

少数几个资源停在 99.8% / 92.1% 且 `isFinished=true`，是平台自己的取整差
（`endWatchTime` 已经等于 `duration`，补不动），**不影响必修完成度**。

**坑（已绕过）**

- polymas 的接口只认 cookie 里的 `uuid`（从 `CASLOGC` 解出来）。
- 这些 cookie 的域是 `.zhihuishu.com`，httpx 按域匹配往 `polymas.com` 发会**一条都不带**，
  返回 `{"code":401,"msg":"拒绝访问，未登录!"}`。所以 `app/polymas.py` 里把 cookie
  摊平成无域键值对再发。
- 浏览器那条 SSO（`oauth/login/ssoLogin`）对 AI 课返回 500，别走浏览器，直接调 API。

---

## 12. 二次开发提示

现在两类课程都已接入，照着加 Hike 只需三步：

1. 在 `courses.py` 里加 `collect_hike_tasks()`，把 Hike 课程展开成带 `kind="hike"` 的任务
2. 在 `runner.py` 的 `Worker` 里给 `kind == "hike"` 加一条分发（`HikeVideoPlayer`）
3. 在 `run_courses()` 的展开循环里加一个分支；线程池、输出、统计、停止条件、报告都不用动

`Worker._ensure()` 是懒加载，加分支不影响现有链路。

---

## 13. 安全

- `state\cookies.json` 等价于登录态；`config.ini` 可能存明文密码
- 这两样别外发、别提交到 git、别扔网盘
- 想立即失效：网页端退出登录，或删掉 `state\cookies.json`
