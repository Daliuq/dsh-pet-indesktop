# -*- coding: utf-8 -*-
"""事件汇报概率门（report gates）。

设计口径（用户 2026-09-10 定稿）：

- 全部事件汇报控制统一为**概率门**：值为该类事件的**通过概率** 0.00–1.00，
  ``0`` = 该类完全不汇报，``1`` = 全部汇报。**不再有布尔开关**。
- 门按**事件聚合类别**划分（本模块 ``REPORT_GATE_KEYS``），设置页把它们收进
  「自动化与联动 → 事件气泡触发概率」下的一个可折叠框里，与文案行同组。
- 概率只作用在**出气泡的汇报路径**。检测器本身（卡住检测 / 行为识别 /
  探索看门狗）与 ``raw_record`` 数据链路不经过概率门，不受影响。

本模块只放纯数据与纯函数，不导入 ``pet.config`` 等模块，避免循环导入。
"""

from __future__ import annotations

#: 门名 → 默认通过概率。除过程汇报外默认常开（1.0），即默认行为与改造前一致。
REPORT_GATE_DEFAULTS: dict[str, float] = {
    "state": 1.0,        # 开始干活 / 思考
    "activity": 0.6,     # 过程汇报（读文件/跑命令/改代码…）——提醒量最大，默认抽稀到 60%
    "approval": 1.0,     # 审批与提问（需要你处理）
    "done": 1.0,         # 任务完成
    "exec_failed": 1.0,  # 失败与错误
    "model_access": 1.0,   # 模型访问失败（服务端限流 / 过载 / AI 服务错误）
    "stuck": 1.0,        # 检测类提醒：卡住 / 行为重复 / 循环检测
    "bridge": 1.0,       # 桥接安装与写回、Agent 未找到、余额查询
}

#: 门名的稳定顺序（设置页按此顺序展示）
REPORT_GATE_KEYS: tuple[str, ...] = tuple(REPORT_GATE_DEFAULTS)

#: 门名的中文标签（设置页小节标题）
REPORT_GATE_LABELS: dict[str, str] = {
    "state": "状态提醒（开始干活 / 思考）",
    "activity": "过程汇报（读文件 / 跑命令 / 改代码…）",
    "approval": "审批与提问（需要你处理）",
    "done": "任务完成",
    "exec_failed": "失败与错误",
    "model_access": "模型访问失败",
    "stuck": "检测类提醒（卡住 / 行为重复 / 循环）",
    "bridge": "桥接、写回与查询",
}

#: 设置页每个门的简短说明。文案按类别写，避免八个滑块重复一段难读的
#: 内部术语；“0% / 100%”也让控件的端点语义在不打开帮助时即可理解。
REPORT_GATE_HINTS: dict[str, str] = {
    "state": "开始工作、思考等状态气泡的显示频率。",
    "activity": "读取文件、搜索、编辑和运行命令等过程气泡的显示频率。",
    "approval": "审批和用户问题气泡的显示频率；需要你处理的提醒建议保持开启。",
    "done": "任务完成或暂停待确认气泡的显示频率。",
    "exec_failed": "执行失败、工具错误等气泡的显示频率。",
    "model_access": "模型访问失败和 AI 服务错误气泡的显示频率。",
    "stuck": "卡住、行为重复和循环检测提醒的显示频率。",
    "bridge": "桥接安装、写回、Agent 缺失和余额查询气泡的显示频率。",
}

#: 旧布尔开关 → 新概率门（一次性迁移用；迁移后不再写回旧键）
LEGACY_SWITCH_GATES: dict[str, str] = {
    "notify_state": "state",
    "notify_activity": "activity",
    "notify_approval": "approval",
    "notify_done": "done",
    "notify_exec_failed": "exec_failed",
}

#: 已废弃的旧概率键（0-100 整数）→ 新门（0-1）
LEGACY_PERCENT_GATES: dict[str, str] = {
    "report_probability": "activity",
}

#: 所有当前会进入 ``_dialogue`` 或 ``_report_allowed`` 的稳定事件键。
#:
#: 这份清单是设置页文案编辑器与运行时报告路径之间的契约：新增一个
#: 稳定气泡事件时，必须先登记它，再为它选择所属门。带有动态后缀的
#: 事件（例如 ``activity.<tool>``）仍由下方前缀表覆盖。
REPORT_EVENT_KEYS: tuple[str, ...] = (
    "start", "thinking",
    "activity.read", "activity.search", "activity.edit", "activity.run", "activity.default",
    "agent.attention", "agent.error", "agent.missing",
    "bridge.install.pending", "bridge.install.success", "bridge.install.failed",
    "bridge.uninstall.failed", "bridge.unknown", "dsh.writeback.failed",
    "approval.command", "approval.tool", "approval.generic",
    "question.empty", "question.one", "question.many",
    "watchdog.warning", "watchdog.control", "watchdog.control.result",
    "model_access.one", "model_access.many", "llm_error.api",
    "done.success", "done.attention",
    "failure.retry", "failure.tool", "failure.generic",
    "stuck.reminder", "pattern.warning", "pattern.control",
    "balance.loading", "balance.result",
)

#: 事件键 → 门名。判定顺序：先精确表，再前缀表。
#:
#: 稳定事件在精确表中逐项列出，避免只依赖前缀时漏掉 typo 或新增的
#: 特殊键；前缀表则保留给工具类型等可扩展的动态事件。
_EVENT_EXACT: dict[str, str] = {
    "start": "state",
    "thinking": "state",
    "activity.read": "activity",
    "activity.search": "activity",
    "activity.edit": "activity",
    "activity.run": "activity",
    "activity.default": "activity",
    "agent.attention": "approval",
    "agent.error": "exec_failed",
    "agent.missing": "bridge",
    "bridge.install.pending": "bridge",
    "bridge.install.success": "bridge",
    "bridge.install.failed": "bridge",
    "bridge.uninstall.failed": "bridge",
    "bridge.unknown": "bridge",
    "dsh.writeback.failed": "bridge",
    "approval.command": "approval",
    "approval.tool": "approval",
    "approval.generic": "approval",
    "question.empty": "approval",
    "question.one": "approval",
    "question.many": "approval",
    "watchdog.warning": "stuck",
    "watchdog.control": "stuck",
    "watchdog.control.result": "stuck",
    "model_access.one": "model_access",
    "model_access.many": "model_access",
    "llm_error.api": "model_access",
    "done.success": "done",
    "done.attention": "done",
    "failure.retry": "exec_failed",
    "failure.tool": "exec_failed",
    "failure.generic": "exec_failed",
    "stuck.reminder": "stuck",
    "pattern.warning": "stuck",
    "pattern.control": "stuck",
    "balance.loading": "bridge",
    "balance.result": "bridge",
}

_EVENT_PREFIX: tuple[tuple[str, str], ...] = (
    ("activity.", "activity"),
    ("approval.", "approval"),
    ("question.", "approval"),
    ("done.", "done"),
    ("failure.", "exec_failed"),
    ("model_access.", "model_access"),
    ("llm_error.", "model_access"),
    ("stuck.", "stuck"),
    ("pattern.", "stuck"),
    ("watchdog.", "stuck"),
    ("bridge.", "bridge"),
    ("balance.", "bridge"),
    ("dsh.writeback.", "bridge"),
)


def gate_for_event(event_key: str) -> str | None:
    """事件键 → 概率门名；未知事件返回 ``None``（调用方按不抽稀处理）。"""
    key = str(event_key or "").strip()
    if not key:
        return None
    if key in _EVENT_EXACT:
        return _EVENT_EXACT[key]
    for prefix, gate in _EVENT_PREFIX:
        if key.startswith(prefix):
            return gate
    return None


def should_report(probability: float, roll: float) -> bool:
    """按通过概率判决：``roll < probability`` 放行（边界取「小于」）。"""
    return float(roll) < float(probability)


def should_report_event(gates: dict, event_key: str, roll: float) -> bool:
    """按事件键找到所属门并判决。

    未知事件（没有归属门）**不抽稀**，直接放行——新事件上线时不会被静默丢弃。
    """
    gate = gate_for_event(event_key)
    if gate is None:
        return True
    try:
        probability = float(gates.get(gate, REPORT_GATE_DEFAULTS.get(gate, 1.0)))
    except (TypeError, ValueError):
        probability = REPORT_GATE_DEFAULTS.get(gate, 1.0)
    return should_report(probability, roll)


def clean_report_gates(raw: object) -> dict[str, float]:
    """清洗概率门配置。

    - 未给的门取默认值；
    - 未知门名丢弃（防止拼写错误静默生效）；
    - **无法解析的值回落该门默认值**（例如 ``"abc"``/``None`` → 该门默认；
      ``activity`` 的默认是 ``0.6``，不是 1.0）；
    - 越界收敛到 ``[0, 1]``，数值字符串可解析；
    - 布尔不是特例：``True``→``1.0``、``False``→``0.0``（旧开关迁移到概率端点）。
    """
    result = dict(REPORT_GATE_DEFAULTS)
    if not isinstance(raw, dict):
        return result
    for name, value in raw.items():
        key = str(name)
        if key not in REPORT_GATE_DEFAULTS:
            continue
        default = REPORT_GATE_DEFAULTS[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            result[key] = default
            continue
        result[key] = min(1.0, max(0.0, number))
    return result
