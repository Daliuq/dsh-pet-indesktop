# PR 报告：文本生成待办 Agent

> **基线**：`2786c156dc61774a4195d6930806bb0471b59884`（origin/main，Merge PR #190）
> **分支**：`codex/todo-agent-entry-packaging`　**日期**：2026-09-28
> **范围**：10 个文件（实现/工具 5、测试 3、文档 2）
> **关联**：[`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md)

## 一、核心特性

待办提醒面板保留原有「新建待办」手动表单，并新增「文本生成」按钮，点击后打开独立的文字生成窗口。用户可粘贴任意包含事情和时间的消息，Agent 抽取未来事项并加入现有待办列表，也可从窗口切到手动表单。窗口标出当前内置 AI 对话的服务商、模型、Chat Completions 接口和共用的 API Key 聊天额度。只有实际新增待办后才清空输入；空结果、重复项和失败均保留文本。

同时补上 onedir 构建依赖预检和启动就绪检查，避免缺失 `lunar_python` 的包进入交付阶段，也避免只看见进程就误判启动成功。

**不变量**：Agent 网络请求在后台线程；模型结果经 Qt queued signal 回 GUI 线程落盘。现有待办先装载、再去重追加，不覆盖已有数据。Agent 不显示或记录 API Key，不新增配置项、迁移或独立聊天历史。

## 修改文件说明

下面的增删行数来自 `git diff --numstat`；新文件按新增行统计，没有删除文件。

### 实现与工具

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/app.py` | +83 / −1 | 接收 DSH 真人消息；在 GUI 线程校验、去重、追加和刷新待办；退出时关闭 Agent。 |
| `pet/todo_agent.py` | +222 / −0 | 新增 JSON 响应解析、日期/时间校验、输入与队列上限，以及串行后台模型调用。 |
| `pet/todo_panel.py` | +199 / −7 | 保留原有「新建待办」手动表单，增加「文本生成」按钮和独立窗口；展示模型/额度，成功新增后清空文本。 |
| `scripts/build_onedir.ps1` | +53 / −5 | 预检 `lunar_python`；启动包后同时等主窗口和事件循环就绪日志。 |
| `scripts/benchmark_todo_agent.py` | +127 / −0 | 提供不访问网络的提交耗时、空闲线程 CPU 和队列内存复测脚本。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_desktop_pet_features.py` | +2 / −0 | 将 PR 报告排除在产品文案品牌扫描之外；报告记录分支与验证工具，不属于产品文案。 |
| `tests/test_requested_regressions.py` | +16 / −0 | 锁定打包依赖预检与应用就绪检查。 |
| `tests/test_todo_agent.py` | +226 / −0 | 覆盖原有手动入口、独立文本生成弹窗、模型提示刷新、提交状态、清空条件与结果写入。 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/INDEX.md` | +1 / −0 | 登记本报告。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +88 / −0 | 本文件；新增本次 PR 的三项交付证据。 |

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

**弹窗与边界**：本机用实际 `TodoPanelDialog` 和真实 Qt 事件循环做交互探针，输出 `manual_form_after_add=True generate_enabled=False`、`popup_after_generate=True`；未提交文本，因此没有网络请求或读取聊天凭据。离屏截图在 440×460 主面板及生成窗口默认尺寸下确认入口和文案可见。相关 offscreen 回归测试验证原有新建按钮展开手动表单、独立文本生成按钮打开弹窗、手动填写回退、空结果和 Agent 拒绝时保留输入，以及新增成功后清空。

**原生桌面 UI 的验证边界**：本次桌面自动化探针返回 `apps=[]`，且 `cua.listApps` 不可用；因此无法对新构建的 native window 做屏幕点击和截图确认。已用本机 Qt 弹窗对象可见性与布局探针确认点击路由，不能把 offscreen 结果冒充为用户桌面实机截图。真实模型接口未探测，原因是一次请求会消耗用户当前配置的聊天额度；上面的 benchmark 明确输出 `network_calls=0`。

## 测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 定向 | `python -m pytest -q tests/test_todo_agent.py tests/test_requested_regressions.py::test_onedir_build_preflights_lunar_python_and_waits_for_app_readiness` | 6 passed |
| 静态 | `python -m ruff check pet tests scripts` | All checks passed |
| 基准 | `python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5` | 1,000 accepted；无网络；数值见性能表 |
| 全量（PR 文件范围） | CI 同款 pytest 命令（排除四个隔离时序测试）；本地额外忽略一项未跟踪测试文件 | 2907 passed, 11 skipped, 3 warnings（199.02 s） |
| 本地额外忽略 | `tests/test_dsh_control_cross_language.py` | 该文件在当前工作区未跟踪、不属于 PR；复测按 PR 提交文件范围运行，CI 使用实际提交文件集合 |
| 打包 | `scripts/build_onedir.ps1 -Variant webm-chat`；`python -m zipfile -t dist-onedir/dsh-pet-standalone-webm-chat-portable.zip` | 启动、设置窗口、DLL、编码检查与 ZIP CRC 均通过 |

## 已知限制与回滚

- 没有对真实服务商发出请求；模型名和额度来源由当前本地配置动态显示，网络成功路径未消耗真实额度验证。
- 回滚本 PR 不涉及配置迁移或新建持久键；已由用户成功创建的待办继续由既有存储管理。
