# PR 报告：LLM生成待办 Agent

> **基线**：`2786c156dc61774a4195d6930806bb0471b59884`（origin/main，Merge PR #190）
> **分支**：`codex/todo-agent-entry-packaging`　**日期**：2026-09-28
> **首轮范围**：11 个文件（实现/工具 5、测试 4、文档 2）
> **第二轮累计**：当前 PR 相对 `origin/main` 共 13 个文件（实现/工具 6、测试 5、文档 2）；本轮逐文件差异见文末。
> **关联**：[`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md)

## 一、核心特性

待办提醒面板保留原有「新建待办」手动表单，并新增「LLM生成待办」按钮，点击后打开独立窗口。用户可粘贴任意包含事情和时间的消息，Agent 抽取未来事项并加入现有待办列表，也可从窗口切到手动表单。窗口标出当前内置 AI 对话的服务商、模型、Chat Completions 接口和共用的 API Key 聊天额度。只有实际新增待办后才清空输入；空结果、重复项和失败均保留文本。

同时补上 onedir 构建依赖预检和启动就绪检查，避免缺失 `lunar_python` 的包进入交付阶段，也避免只看见进程就误判启动成功。

**不变量**：Agent 网络请求在后台线程；模型结果经 Qt queued signal 回 GUI 线程落盘。现有待办先装载、再去重追加，不覆盖已有数据。Agent 不显示或记录 API Key，不新增配置项、迁移或独立聊天历史。

## 修改文件说明

下面的增删行数来自 `git diff --numstat`；新文件按新增行统计，没有删除文件。

### 实现与工具

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/app.py` | +83 / −1 | 接收 DSH 真人消息；在 GUI 线程校验、去重、追加和刷新待办；退出时关闭 Agent。 |
| `pet/todo_agent.py` | +222 / −0 | 新增 JSON 响应解析、日期/时间校验、输入与队列上限，以及串行后台模型调用。 |
| `pet/todo_panel.py` | +200 / −7 | 保留原有「新建待办」手动表单，增加蓝色强调的生成入口（当前文案为「LLM生成待办」）和独立窗口；展示模型/额度，成功新增后清空文本。 |
| `scripts/build_onedir.ps1` | +53 / −5 | 预检 `lunar_python`；启动包后同时等主窗口和事件循环就绪日志。 |
| `scripts/benchmark_todo_agent.py` | +127 / −0 | 提供不访问网络的提交耗时、空闲线程 CPU 和队列内存复测脚本。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_desktop_pet_features.py` | +2 / −0 | 将 PR 报告排除在产品文案品牌扫描之外；报告记录分支与验证工具，不属于产品文案。 |
| `tests/test_requested_regressions.py` | +16 / −0 | 锁定打包依赖预检与应用就绪检查。 |
| `tests/test_session_end_ffmpeg_guard.py` | +6 / −0 | 用事件同步对照组的 reader 与后台元数据探测，避免主线程立即断言造成 Windows 竞态失败。 |
| `tests/test_todo_agent.py` | +228 / −0 | 覆盖原有手动入口、与新建按钮同色的文本生成入口、独立弹窗、模型提示刷新、提交状态、清空条件与结果写入。 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/INDEX.md` | +1 / −0 | 登记本报告。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +89 / −0 | 本文件；新增本次 PR 的三项交付证据。 |

## 三、实现要点

- Agent 使用 `Config.chat_settings().active_config`，解析同一 Provider 的 API Key，并通过 `OpenAICompatibleProvider.stream` 发送一条 system prompt 和一条 user prompt。请求带本地时间，要求模型只返回待办 JSON；解析器仅接受最多 5 个有效未来事项。
- 输入上限为 8,000 字符、排队上限为 32 条、响应 `max_tokens` 上限为 700、网络超时上限为 20 秒。无可用配置或队列满时拒绝请求并保留输入。
- 手动入口弹窗打开时不请求模型；点击「手动填写」关闭弹窗并展开既有编辑表单。重复、空结果、失败不会清空文本。
- AppShell 负责给候选项去重并限制待办总量，成功后通过既有 `TodoReminderService` 保存；模型原始响应和对话上下文不落盘。

## 性能分析

**方法**：`python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5`；PowerShell；Windows 10 `10.0.26200`、CPython 3.10.15（conda）。样本 1,000 次。脚本把 `OpenAICompatibleProvider` 替换为进程内空响应探针，未连接网络。

| 指标 | 实测 | 归属 |
|---|---:|---|
| `TodoAgent.submit` 排队接纳，中位数 / P95 / 最大值 | 0.0163 / 0.0325 / 1.2663 ms | 新增路径，计时止于入队，不含网络 |
| 单个空闲 worker，5 秒 CPU 时间 | 0.0156 s | 首次请求后稳态；worker 每次 `Queue.get` 最多等待 0.2 秒 |
| 满队列的文本与 Python 堆增量 | 32 条 × 8,000 字符；256,000 字符；276,690 bytes | 新增路径，`tracemalloc` 实测 |

**结论**：应用启动和面板打开不创建 Agent 线程；首个请求后创建 1 个 daemon worker，应用退出时停止。空闲 5 秒实测 CPU 为 15.6 ms。每条被接纳的手动输入或 DSH 真人消息触发 1 次聊天接口请求；打开/关闭弹窗本身不发请求。此请求与内置 AI 对话共用当前 API Key 和服务商额度。一次真实请求的网络延迟、token 数和计费未测，因为会消耗用户额度；代码将其响应限制为最多 700 tokens、超时不超过 20 秒。没有新增常驻轮询、缓存或历史文件；只有成功新增待办时复用既有待办存储写盘。队列最多保留 32 条、每条最多 8,000 字符，满队列测得 Python 堆增量为 276,690 bytes。

## 实机运行记录

**根因现场**：用户此前启动旧包时日志为 `ModuleNotFoundError: No module named 'lunar_python'`，导入链落在 `pet/festival_calendar.py`。构建机预检输出 `lunar-python OK`；本次 `Analysis-00.toc` 同时包含 `lunar_python`、`pet.todo_agent` 与 `pet.todo_panel`。

**本机打包与启动**：命令 `.\scripts\build_onedir.ps1 -Variant webm-chat`；默认临时目录因祖先含 `node_modules` 导致首轮桥接隔离检查失败，随后将 `TEMP/TMP/TMPDIR` 指向 W: 上无该依赖的临时目录后通过。Windows 10 `10.0.26200-SP0`、Python 3.10.15、PyInstaller 6.22.2。真实输出：`[smoke] exe window and app-ready log appeared after 3.5s`、`[smoke] --settings window appeared after 2.9s`、`[encoding-check] PASS`、ZIP `Done testing`。便携包为 362,397,241 bytes（约 345.6 MiB）。

**弹窗与边界**：本机用实际 `TodoPanelDialog` 和真实 Qt 事件循环做交互探针，输出 `manual_form_after_add=True generate_enabled=False`、`popup_after_generate=True`；未提交文本，因此没有网络请求或读取聊天凭据。离屏截图在 440×460 主面板及生成窗口默认尺寸下确认入口和文案可见；本轮复核两个入口的 `accent=True`，截图中均渲染为 `#0a84ff`。相关 offscreen 回归测试验证原有新建按钮展开手动表单、独立文本生成按钮打开弹窗、手动填写回退、空结果和 Agent 拒绝时保留输入，以及新增成功后清空。

**原生桌面 UI 的验证边界**：本次桌面自动化探针返回 `apps=[]`，且 `cua.listApps` 不可用；因此无法对新构建的 native window 做屏幕点击和截图确认。已用本机 Qt 弹窗对象可见性与布局探针确认点击路由，不能把 offscreen 结果冒充为用户桌面实机截图。真实模型接口未探测，原因是一次请求会消耗用户当前配置的聊天额度；上面的 benchmark 明确输出 `network_calls=0`。

## 测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 定向 | `python -m pytest -q tests/test_todo_agent.py tests/test_requested_regressions.py::test_onedir_build_preflights_lunar_python_and_waits_for_app_readiness`；本次色彩回归先去掉 `accent` 标记确认失败，再恢复后运行 `python -m pytest -q tests/test_todo_agent.py` | 原验证 6 passed；本次 5 passed |
| 静态 | `python -m ruff check pet tests scripts` | All checks passed |
| 基准 | `python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5` | 1,000 accepted；无网络；数值见性能表 |
| 全量（PR 文件范围） | CI 同款 pytest 命令（排除四个隔离时序测试）；本地额外忽略一项未跟踪测试文件 | 2907 passed, 11 skipped, 3 warnings（200.01 s）；本次仅改变孤立按钮样式属性，接口、持久化、生命周期和平台分发均未变，因此没有重跑全量 |
| 本地额外忽略 | `tests/test_dsh_control_cross_language.py` | 该文件在当前工作区未跟踪、不属于 PR；复测按 PR 提交文件范围运行，CI 使用实际提交文件集合 |
| 打包 | `scripts/build_onedir.ps1 -Variant webm-chat`；`python -m zipfile -t dist-onedir/dsh-pet-standalone-webm-chat-portable.zip` | 启动、设置窗口、DLL、编码检查与 ZIP CRC 均通过 |

## 已知限制与回滚

- 没有对真实服务商发出请求；模型名和额度来源由当前本地配置动态显示，网络成功路径未消耗真实额度验证。
- 回滚本 PR 不涉及配置迁移或新建持久键；已由用户成功创建的待办继续由既有存储管理。

## 第二轮改进（2026-09-28，历史记录）：自动补排期、会议提醒与弹窗规范

本轮保留上文首轮实现与证据，补上“没有写具体时间”的本地待办排期，并按 Shared UX Contract 整理文本生成子窗口。此轮当时曾加入会议专属提前量；该行为已由下方第三轮需求修正撤销，当前所有待办统一使用全局提醒偏好。

### 本轮修改文件说明

下表为相对上一 PR 提交的 `git diff --numstat HEAD`，新文件/删除文件均单独列出；本轮没有新增或删除文件。

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/app.py` | +18 / −2 | 在 GUI 线程获取当前启用待办时间快照并传给 Agent；创建待办时保留模型给出的单条提醒提前量。 |
| `pet/todo_agent.py` | +178 / −33 | 更新抽取提示词和 JSON 字段；在用户指定日期或未来 7 天内按“60 分钟内冲突数、当天待办数、日期和时刻”依次排序 09:00–17:00 的候选时段，每条新结果占用其已选时段。若无完全空档，选冲突最少的候选，并保留调度器选定的日期。 |
| `pet/todo_panel.py` | +62 / −35 | 让子窗口只阻塞所属待办面板；移除内部二级卡片，补输入标签关联和关闭后焦点恢复，显示会议提前量。 |
| `pet/todo_reminder.py` | +35 / −5 | 持久化可选的单条提前量，在提醒触发时覆盖全局提前量；系统通知与气泡一致显示提前分钟数。 |
| `scripts/benchmark_todo_agent.py` | +47 / −4 | 基准加入 100 条当前待办快照，并实测无时间事项的本地排期与提示上下文长度。 |
| `tests/test_todo_agent.py` | +206 / −7 | 覆盖会议 30 分钟提醒、待办空档查询、多条新事项错峰、自动选择日期、弹窗窗口模态/辅助标签/焦点和 DSH 快照传递。 |
| `tests/test_todo_reminder.py` | +53 / −0 | 覆盖单条提前量清洗、文件往返保存、覆盖全局偏好以及系统通知文案。 |
| `docs/INDEX.md` | +1 / −1 | 更新报告摘要和适用范围，登记自动排期与会议提醒内容。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +63 / −1 | 追加本轮差异、实测和验证记录，保留首轮证据。 |

### 本轮实现与行为边界

- 待办面板将 `TodoReminderService.items()` 的当前副本在 GUI 线程交给 Agent；队列只携带 `kind/date/time`，不带已有待办标题。提示词收到时间安排上下文；解析器对缺少时刻的事项使用相同快照，依次最小化 60 分钟内冲突数、当天待办数，再按日期和时刻择早；如果没有完全空档，则退到冲突最少的候选。未指定日期时搜索今天到第 7 天，用户指定日期时只在该日找空档；搜索范围为每小时 09:00 至 17:00。它只查询本地待办，不代表外部日历空闲。
- 同一模型响应含多条缺少时刻的事项时，解析器会把已分配时段加入临时快照，避免同批事项选到同一空档。历史版本曾根据会议、面试或预约标记保存 `reminder_lead_minutes=30`；当前实现已删除这项单条覆盖，详见第三轮修正。
- 文本生成窗口改为所属面板的 window-modal 子窗口；保留独立窗口层级，不再嵌入额外二级卡片。窗口采用现有浅/深色令牌、20 px 标题、12 px 提示、7 px 输入圆角与蓝色焦点态；最小 440×440、默认 560×480。输入标签通过 buddy 关系关联文本框，Ctrl+Enter 可提交，关闭时焦点返回“文本生成”入口，手动填写仍切回原表单。

### 本轮性能分析

**方法**：`python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5`；Windows 10 `10.0.26200-SP0`、CPython 3.10.15（conda）；进程内 Provider 探针不访问网络。每次提交和解析使用 100 条启用的待办时间快照，队列压力按 32 条上限、每条 8,000 字符测量。

| 指标 | 实测 | 归属 |
|---|---:|---|
| `TodoAgent.submit` 1,000 次，中位数 / P95 / 最大值 | 0.3466 / 0.4454 / 2.5219 ms | 新增路径；包含 100 条时间快照归一化与入队，不含网络 |
| 缺少时间解析 1,000 次，中位数 / P95 / 最大值 | 0.9204 / 1.1813 / 3.2771 ms | 新增本地路径；含 100 条排期扫描和空档选择 |
| 100 条待办提示上下文长度 | 1,899 字符 | 新增模型输入；不产生额外请求，实际 token 数随模型分词器变化，未调用服务测量 |
| 单个空闲 worker，5 秒 CPU 增量 | 0.0000 s（计时输出精度 0.0001 s） | 首次请求后稳态；探针无响应流量 |
| 32 条满队列的堆增量 | 839,615 bytes；含 256,000 个输入字符和每条 100 个排期时间 | 新增路径；`tracemalloc` 实测 |

**结论**：应用仍只在首次请求后创建 1 个后台 worker；读当前待办是 GUI 线程内的内存副本，空档搜索在已有 worker 返回结果后于本地执行。网络请求频率仍为每条被接纳的 Agent 输入最多 1 次，没有新增外部服务请求；模型输入新增最多 100 条已启用待办时间，100 条样本为 1,899 字符，真实 token 用量未测。没有新增定时器或常驻线程；待办数据沿用原有 JSON 文件，会议只增加每项可选整数，触发后仍由现有调度器写回。队列达到上限时，100 条快照加文本的 Python 堆增量实测 839,615 bytes。

### 本轮实机运行记录

- **实际用户可见窗口与尺寸探针**：Windows 本机 CPython 3.10.15，使用真实 `TodoPanelDialog`/`QDialog`、Qt 事件循环和其实际 QSS，未替换 UI 控件。`QT_QPA_PLATFORM=offscreen` 下抓取 440×440、720×560、1100×680 三种窗口尺寸，并分别设置浅色和深色调色板；六张截图均可见标题、模型额度说明、输入区、状态和动作按钮，空输入时主操作按钮保持禁用且可见。紧凑布局输入框实测 396×213，按钮均在窗口内。离屏环境没有 CJK 字体回退，因此探针仅给截图进程加载 `C:\Windows\Fonts\simsun.ttc`；产品未改字体。截图保存在 `%TEMP%\todo-agent-ui-qa-20260928\`。
- **变更前复现**：首轮 Agent 提示词把未标钟点事项定为 09:00，解析器也没有已存待办快照参数；实现前的行为回归因缺少 `find_available_todo_slot` 入口失败。更晚发现的日期折回缺陷用 `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q tests/test_todo_agent.py::test_missing_date_and_time_keep_the_scheduled_open_day` 重现，红输出为 `1 failed`，实际结果日期 `2026-09-04`、期望 `2026-09-05`；修复后同一用例通过。提醒回归在实现前也观察到清洗流程会丢弃单条提前量字段。
- **行为确认**：定向 Qt 用例通过真实窗口控件验证：点击“文本生成”打开子窗口并聚焦文本框；窗口只对待办面板 window-modal；输入标签关联文本框；关闭后焦点回到入口；“手动填写”仍打开原表单。服务回归覆盖会议条目在 09:30 触发 10:00 会议前的系统通知 `项目会议（10:00，提前30分钟提醒）`，模型网络未调用。
- **自动化边界**：桌面自动化探针先前返回 `apps=[]` 且 `cua.listApps` 不可用，本轮截图因此明确是本机 Qt offscreen 渲染，不冒充原生桌面截屏。没有请求真实聊天服务，因为会消耗当前配置的额度；脚本输出 `network_calls=0`。桌面原生窗口与服务商真实响应仍需在应用运行环境中手动确认。

### 本轮测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 定向 | `python -m pytest -q tests/test_todo_agent.py tests/test_todo_reminder.py`（`QT_QPA_PLATFORM=offscreen`） | 54 passed, 2.26 s |
| 静态 | `python -m ruff check pet tests scripts` | All checks passed |
| 基准 | `python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5` | 1,000 次入队与 1,000 次本地空档解析；0 网络请求，结果见性能表 |
| 全量主套件 | PowerShell：`$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q --ignore=tests/test_webm_reader_lifecycle.py --ignore=tests/test_webm_clip_lifecycle.py --ignore=tests/test_webm_first_frame_lock.py --ignore=tests/test_low_priority_warm_interaction_yield.py --ignore=tests/test_dsh_control_cross_language.py` | 2918 passed, 11 skipped, 3 warnings（191.78 s）；额外忽略项是工作区未跟踪文件，不属于 PR |
| 差异检查 | `git diff --check` | 通过；仅 Git 提示工作区文件的 LF/CRLF 转换警告 |

上文首轮测试表保留当时结果；本表是本轮代码修改后的最新全量主套件结果。

**本轮回滚**：移除本轮的单条提醒覆盖后，旧待办和全局提醒偏好语义保持不变；移除本轮排期快照传递后，无时间事项恢复由原有模型输出时间的行为。没有配置迁移，也没有需要清理的专属缓存或后台服务。

## 第三轮需求修正（2026-09-28）：统一沿用全局提醒提前量

产品确认待办功能已有全局“提前提醒”设置，因此文本 Agent 不再识别会议、面试或预约，也不再给某类事项单独设置 30 分钟。每条待办都由现有 `todo_reminder_lead_minutes` 设置决定提醒提前量。解析器忽略模型可能返回的旧 `is_meeting` 标记；待办标准化时丢弃历史试验版的 `reminder_lead_minutes` 字段，避免旧字段覆盖当前偏好。提醒设置本身和手动新建流程未改。

### 本轮修改文件说明

下表增删行数为本轮工作区相对当前分支 `HEAD` 的 `git diff --numstat`；没有新增或删除文件。

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/app.py` | +0 / −1 | 创建 Agent 结果时不再传递模型给出的单条提前量。 |
| `pet/todo_agent.py` | +3 / −7 | 删除会议识别和 30 分钟规则，统一要求模型沿用全局提醒设置。 |
| `pet/todo_panel.py` | +10 / −13 | 界面说明改为沿用桌宠提醒设置，移除单项提前量徽标，并将入口与窗口统一命名为「LLM生成待办」。 |
| `pet/todo_reminder.py` | +3 / −29 | 调度只读取全局提前量，标准化时清除过往单项覆盖字段。 |
| `tests/test_todo_agent.py` | +6 / −8 | 更新提示、解析和 App 接收行为的回归断言，确保会议标记不产生专属提前量，并同步弹窗无障碍名称。 |
| `tests/test_todo_reminder.py` | +19 / −17 | 覆盖旧字段被清除且不能覆盖全局提醒设置。 |
| `docs/INDEX.md` | +1 / −1 | 将索引摘要改为准确描述全局提醒行为。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +40 / −6 | 记录本轮需求修正、LLM生成待办入口文案、性能分析和重新打包结果。 |

### 本轮实现与行为边界

- 提示词不要求识别会议类别；解析结果只包含待办标题、类型、日期和时间。即使旧模型响应带有 `is_meeting`，解析器也忽略它。
- 手动创建与 Agent 创建的待办均由 `TodoReminderService` 读取同一全局提前量。旧待办的 `reminder_lead_minutes` 不再生效，并在标准化/保存时移除；无配置迁移、新设置或新增聊天请求。入口、窗口标题和无障碍名称统一使用「LLM生成待办」。

### 本轮性能分析

**方法与实测**：PowerShell 内联 Python 探针调用 `advance_todo_state`；Windows 10 `10.0.26200-SP0`、CPython 3.10.15，1,000 轮 × 每轮 100 条（共扫描 100,000 条）。总用时 1.387801 秒，平均每轮 1.388 ms、每条 13.878 μs。该值覆盖完整提醒扫描，未与旧版本做性能对比。改动路径的稳态成本没有新增分支或数据结构；每条待办仍每 30 秒 tick 扫描一次，启动时仍立即扫描一次。网络请求频率不变（Agent 每条输入最多 1 次），没有新增系统调用、线程或持久磁盘写入；仍只在提醒状态变化时保存待办 JSON。内存没有新增常驻字段或缓存，历史单项字段在清洗时被移除。

### 本轮实机运行记录

**重新打包**：提醒规则更新时先将原目录和 ZIP 备份至 `dist-onedir/backup-webm-chat-20260928-131146-106`（1,220 个文件；旧 ZIP 362,397,241 bytes，备份哈希一致）。改成“LLM生成待办”后，又将前一版目录和 ZIP 备份至 `dist-onedir/backup-webm-chat-20260928-133036-743`（1,220 个文件；ZIP 355,843,433 bytes，SHA-256 为 `E8964F2C61CFCE67708EF1D44794FEF847A066E4665A6510B26FD7C1D4FEE23B`）。命令 `powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat`；为通过桥接隔离探针，仅在构建进程中把 `TEMP/TMP/TMPDIR` 指向 `W:\dsh-pet-package-temp-20260928-1331`。Windows 10 `10.0.26200-SP0`、CPython 3.10.15、PyInstaller 6.22.2。最新真实输出：`lunar-python OK`、桥接零依赖冒烟通过、Qt Runtime validation OK、瘦身移除 118 个文件 / 44.92 MB、中文编码检查 PASS、DLL 链检查 ALL OK；应用窗口 3.7 秒就绪，`--settings` 窗口 2.4 秒就绪。新 onedir 目录 1,220 个文件、866,173,795 bytes；portable ZIP 355,844,657 bytes（339.4 MiB），`python -m zipfile -t dist-onedir\dsh-pet-standalone-webm-chat-portable.zip` 输出 `Done testing`，SHA-256 为 `1B3BA18765D03FD97CBB39A19FCB05E52F8DF3461EE7470122FEAA9059435E47`。

**本次需求修正的产品测试套件未运行**；既有测试断言已随文案更新。`python -m ruff check pet tests scripts` 输出 `All checks passed!`；构建脚本的依赖、桥接、Qt DLL、应用启动、设置窗口、编码及 ZIP 检查均已实际通过，`git diff --check` 通过。新包保留原有提醒设置和人工新建流程，Agent 界面说明统一显示沿用桌宠提醒设置。

## 第四轮重做（2026-09-28）：从 bd87d3a 重建习惯学习与确认提醒

本轮以 `bd87d3a9453858c97e92d376e90b010f4355d75c` 为代码起点，新建本地分支 `codex/todo-habit-learning-repair`。PR #193 原分支末尾的 `4917a389` 排期提交不包含在新结果中；当前分支保留文本待办、现有手动表单和全局提醒行为，并加入经过回归验证的自动习惯学习与确认式提醒。发布时用新分支生成的提交替换 PR #193 的源分支内容。

### 本轮修改文件说明

增删行数来自本轮暂存差异的 `git diff --cached --numstat`；新增/删除文件单独标出。本轮没有删除文件。表中每项均写明具体改动及目的。

#### 实现与工具

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `integrations/dsh-pet-bridge/index.js` | +137 / −10 | 为本机文件控制队列加请求/响应 HMAC-SHA256、nonce 校验与常量时间比较，拒绝伪造控制消息；桥目录可由测试隔离指定。 |
| `integrations/dsh-pet-bridge/package.json` | +2 / −1 | 桥包版本升至 0.3.0，并将认证协议入口纳入发布文件。 |
| `integrations/dsh-pet-bridge/impl/0.3.0/index.js`（新增） | +4 / −0 | 提供版本化入口，复用桥根目录唯一实现，避免测试与 DSH 包加载两份不同协议。 |
| `pet/dsh_control.py` | +80 / −0 | Python 端生成 256-bit 共享密钥、签署请求并验证带 nonce 的响应；缺密钥或签名不匹配时 fail closed。 |
| `integrations/dsh-pet-bridge/test/response-signer.mjs`（新增） | +77 / −0 | 调用真实 Node 桥实现签发测试响应，供 Python/Node 跨语言联调，不复制一份算法冒充互通。 |
| `pet/todo_habits.py`（新增） | +444 / −0 | 每分钟观察前台活动与闲置状态，将明确识别的工作、学习、休闲时段压缩为类别/星期/小时/时长样本；不保存窗口标题或进程名，样本最多 256 条，每 5 条更新画像。 |
| `pet/app.py` | +114 / −2 | 增加默认关闭的 Windows 采样定时器、启动/停止与退出清理；待办模型结果使用本机习惯画像，无额外模型请求。 |
| `pet/config.py` | +12 / −0 | 增加自动习惯学习和提醒确认两个默认关闭的配置项及布尔值清洗。 |
| `pet/modern_settings_dialog.py` | +8 / −11 | 将习惯学习和“提醒需点确定”设置接入现代设置页。 |
| `pet/settings_pet_controls.py` | +79 / −7 | 在待办设置组增加两个开关、说明和无障碍名称，供用户明确启用或关闭。 |
| `pet/todo_agent.py` | +164 / −21 | 识别 work/study/leisure/general 类别；无明确时间时依类别采用冷启动时段，训练样本充分后按各类星期与小时习惯、现有待办冲突和预计时长挑选时段。 |
| `pet/todo_panel.py` | +133 / −9 | 在面板提供自动学习开关/状态摘要，新增手动待办类别选择，并将本机画像传给现有 Agent 队列。 |
| `pet/todo_reminder.py` | +70 / −4 | 在待办记录中清洗类别与预计时长，并根据全局确认开关发送必须确认的桌宠提醒或系统通知。 |
| `pet/desktop_notify.py` | +44 / −8 | 系统通知处于确认模式时不启动自动关闭计时器，增加“确定”按钮；点击通知正文仍可打开待办但不会确认关闭。 |
| `scripts/benchmark_todo_habits.py`（新增） | +151 / −0 | 提供可复跑的本地采样、排期、HMAC 与有界存储基准，测量不读取或打印当前窗口标题。 |

#### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_config_schema.py` | +2 / −0 | 确认新的开关默认关闭并经过配置清洗。 |
| `tests/test_dsh_control_client.py` | +11 / −2 | 覆盖密钥缺失、请求签名、响应 nonce/HMAC 验证及错误响应拒绝。 |
| `tests/test_dsh_control_cross_language.py`（新增） | +123 / −0 | 使用真实 Node 桥实现验证 Python 客户端接受正确签名并拒绝错误密钥。 |
| `tests/test_foreground_steal.py` | +10 / −7 | 使 Windows 原生前台窗口测试按运行时 Qt 后端判定，避免被先创建的离屏 QApplication 误跳过。 |
| `tests/test_settings_and_resources.py` | +5 / −1 | 校验新增持久设置被归入待办设置组。 |
| `tests/test_todo_agent.py` | +187 / −5 | 覆盖习惯画像排序、工作与休闲时段分流、类别提示以及面板自动学习入口。 |
| `tests/test_todo_habits.py`（新增） | +218 / −0 | 覆盖本地类别识别、空闲切段、时长边界、批量训练、样本上限、开关生命周期和不存窗口标题。 |
| `tests/test_todo_reminder.py` | +112 / −2 | 覆盖确认模式下的粘性桌宠提醒、系统通知按钮、默认/自定义开关与待办类别清洗。 |

#### 文档

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/INDEX.md` | +1 / −1 | 更新报告索引摘要及适用范围。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +89 / −0 | 追加本轮逐文件清单、实测性能、Windows 实机记录及验证结果。 |

### 本轮实现与边界

- 自动学习默认关闭；仅 Windows 支持。启用后每 60 秒查询一次前台窗口元数据和系统闲置时长，只识别明确的应用/标题信号；闲置超过 10 分钟即结束当前片段。只累计至少 2 分钟、最多 4 小时的活动片段。每类独立画像需要至少 5 条有效样本才影响自动排期；工作与学习/休闲的冷启动时段分别为 09:00–17:00、18:00–22:00。显式时间优先，自动分配只查本地待办，不查询日历。
- 画像样本仅含类别、星期、开始小时与时长；不保存窗口标题、进程名、屏幕图像或原始活动日志，最多保留 256 条。采样和画像统计都在现有 GUI 线程中执行，不创建额外工作线程。档案按实例写入既有配置目录，单条合格活动结束时原子更新一次；冷启动时延用 60 分钟默认时长。
- 习惯特征仅在本地待办解析与时段选择中使用，不追加到 LLM 提示词；一次待办识别仍最多调用原有模型接口一次，开关、观察窗口和排期都不额外调用模型或联网。
- `todo_reminder_requires_confirmation` 默认 false。开启后，桌宠气泡为粘性提醒并带“确定”操作；桌宠隐藏时的系统通知不自动计时关闭，必须点通知内的“确定”。点击通知正文仍可打开待办面板，但不会代替确认。
- DSH 桥接控制协议用 32-byte 随机密钥的 64 位十六进制表示，密钥首次初始化后保存在本地 `watchdog-secret`；请求与响应都包含 HMAC-SHA256 和 nonce。密钥只在初始化时写入一次，正常操作沿用既有本机文件队列，不增加网络连接。

### 本轮性能分析

**方法**：`python -m scripts.benchmark_todo_habits --samples 1000`；Windows 10 `10.0.26200-SP0`、CPython 3.10.15。脚本用 1,000 次本地分类/观察、200 次 100 条待办的排期计算、1,000 次 Python 请求/响应 HMAC、100 次真实 Windows 前台与闲置探针，并在临时目录写满 256 条样本。窗口元数据仅用于调用时长测量，没有打印或持久化。

| 指标 | 实测 | 归属 |
|---|---:|---|
| 前台元数据分类 + 已开始片段观察 | 1,000 次；中位数 0.0147 ms，P95 0.0185 ms，最大 0.1698 ms | 本地常驻采样的纯 Python 路径；不含 Win32 查询 |
| 100 条现有待办 + 学习画像的空档搜索 | 200 次；中位数 2.17395 ms，P95 2.6078 ms，最大 3.9144 ms | 新增本地排期路径；无模型或磁盘调用 |
| 请求 + 响应 canonical HMAC | 1,000 次；中位数 0.0232 ms，P95 0.0247 ms，最大 0.6784 ms | 新增本地认证 CPU 成本；不含文件队列等待 |
| Windows 前台窗口 + 闲置探针 | 100 次；中位数 0.07485 ms，P95 0.3048 ms，最大 1.4419 ms | 实际 Win32 探针；每分钟最多触发一次，即 1,440 次/日 |
| 256 条有界画像 | JSON 文件 17,910 bytes；256 次原子写总计 0.661382 s | 新增本地存储上限；约 2.58 ms/次写入 |

**结论**：自动学习关闭时没有新增采样定时器；打开后新增一个 60 秒 Qt 定时器，不新增线程或网络请求。实测单次前台/闲置探针约 0.075 ms 中位数，按 1,440 次/日约 108 ms 探针耗时；单次本地画像空档搜索对 100 条待办约 2.174 ms 中位数。画像最多 256 条、约 17.9 KB；每个合格活动片段至多写一个画像样本，不按定时器频率写磁盘。习惯画像不进入模型提示词，因此 token 数和请求频率无增量。HMAC 两端仅增加签名 CPU；初次桥接安装/启动写密钥并 fsync 一次，后续沿用已有请求与响应文件操作。

### 本轮实机运行记录

- **实机性能探针**：在 Windows 10 `10.0.26200-SP0`、CPython 3.10.15 运行 `python -m scripts.benchmark_todo_habits --samples 1000`，真实输出为 `windows_foreground_and_idle_probe: n=100, median_ms=0.074850, p95_ms=0.304800, max_ms=1.441900`；测试只记录耗时，不记录窗口标题。真实 `vision.foreground_window_info()` 与 `vision.get_system_idle_seconds()` 都能返回并完成 100 轮。
- **打包及启动**：在本机 Windows 运行 `scripts/build_onedir.ps1 -Variant webm-chat`，构建隔离临时目录后通过依赖、桥接、Qt DLL 与中文编码检查；真实输出主窗口约 4.0 秒就绪、`--settings` 窗口约 3.1 秒就绪，portable ZIP 为 362,418,129 bytes，`dsh-pet-standalone-webm-chat.exe` 为 11,947,851 bytes。应用与设置窗口启动后均正常退出，未遗留进程或 PyInstaller `_MEI` 临时目录。
- **可见行为与边界**：本轮 Qt 回归用例确认默认关闭、启停采样、分类保留、桌宠气泡须确认、桌宠隐藏时系统通知须点按钮。桌面自动化接口本轮没有可用的 native app 列表，未声称做过屏幕点击截图；应用包启动与设置窗口由真实 Windows 构建冒烟检查确认。没有提交真实文本到聊天服务，避免消耗当前 API 额度；模型网络请求数为 0。

### 本轮测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 定向行为 | `python -m pytest -q tests/test_todo_habits.py tests/test_todo_agent.py tests/test_todo_reminder.py tests/test_dsh_control_client.py tests/test_dsh_control_cross_language.py` | 全量命令随后覆盖这些用例；跨语言 Node signer 使用真实桥实现。 |
| 静态 | `python -m ruff check pet tests scripts` | All checks passed；已包含本轮新增 Python 基准脚本。 |
| 性能 | `python -m scripts.benchmark_todo_habits --samples 1000` | 输出见性能表；0 网络请求；样本与磁盘写入均位于临时目录。 |
| 全量主套件 | `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q --ignore=tests/test_webm_reader_lifecycle.py --ignore=tests/test_webm_clip_lifecycle.py --ignore=tests/test_webm_first_frame_lock.py --ignore=tests/test_low_priority_warm_interaction_yield.py` | 2,943 passed，11 skipped，0 failed，186.56 s。 |
| 跨语言/导入 | `node integrations/dsh-pet-bridge/verify_import.mjs`（隔离 `TEMP/TMP`） | 桥接导入验证通过；Python/Node 正确密钥互通及错误密钥拒绝包含在全量测试中。 |
| 差异 | `git diff --check` | 通过；工作区仅有 Git 的 LF/CRLF 转换提示。 |

四个被忽略的 webm 时序测试族沿用仓库既有隔离门禁；本轮涉及配置、Qt 定时器、待办生命周期和跨语言文件协议，因此执行了上述全量主套件，而非只跑定向测试。

## 第五轮 CI 跨平台回归修正（2026-09-28）

推送 `0abf0cdc` 后的首轮 PR matrix 在 Windows 通过，Ubuntu/macOS 暴露两处测试夹具问题（[CI 运行记录](https://github.com/MerZlin/dsh-pet-indesktop/actions/runs/36420888586)）。读取失败日志后修正测试准备条件；本轮没有改动产品代码。

### 修改文件说明

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `tests/test_todo_agent.py` | +8 / −1 | 在待办面板自动学习按钮测试中，仅替换 `todo_panel` 模块的平台标记为 Windows；避免非 Windows CI 按产品设计禁用按钮，也避免改写全局 `sys.platform`。 |
| `tests/test_agent_link_threads.py` | +13 / −1 | 为真实控制 worker 测试准备有效临时 HMAC 密钥，并以 `Event` 等待请求进入轮询；不再因缺密钥而立即 fail closed 后抢先移除 worker。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +20 / −0 | 记录跨平台 CI 根因及修复后的定向验证。 |

### 根因、运行影响与验证

- Ubuntu/macOS 的习惯开关测试沿用了本机 Windows 假设；这些平台上按钮按预期禁用，测试点击无效。修正只模拟目标模块的平台条件，不改变产品支持平台或用户可见行为。
- DSH 认证改为缺密钥时立即拒绝。线程生命周期测试原先创建空桥目录，worker 因而快速返回并从集合移除；在 CI 上测试读取集合前 worker 已结束。测试现生成只用于临时目录的有效 key，并等待真实请求线程进入阻塞轮询，再调用 shutdown 验证取消与文件清理。
- 这是纯测试夹具修正：产品稳态开销、系统调用、网络/磁盘访问、线程数和内存均为零增量；产品性能数字沿用第四轮实测。
- 本机 Windows 定向确认：`test_todo_panel_offers_automatic_learning_command_not_per_item_timers` 为 1 passed（1.35 s）；`test_shutdown_cancels_control_worker_promptly` 连续运行 3 次均为 1 passed（1.14–4.76 s）。线程测试通过真实 worker、临时文件队列和 Event 同步，没有固定 sleep。
- 首轮 CI 完整套件结果为 Ubuntu 2 failed / 2,931 passed，macOS 1 failed / 2,932 passed，Windows passed；失败均已对应到上列测试准备问题。产品代码未变，本轮定向修正后尚未重跑完整本地主套件；推送后由 GitHub matrix 复验全部平台。
