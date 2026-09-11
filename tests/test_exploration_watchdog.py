# -*- coding: utf-8 -*-
"""探索循环 Watchdog 的 payload 回归测试。

P1-1：`_evaluate_locked` / `_poll_long_think` 构造 payload 时读取未定义的
`self.mode`，首次触发 warning 必抛 AttributeError，提醒永远发不出。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.exploration_watchdog import ExplorationWatchdog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_repeated_command_warning_payload_is_emitted(app):
    """同一命令重复 5 次触发 warning：payload 构造不得抛 AttributeError。"""
    wd = ExplorationWatchdog()
    seen = []
    wd.warning.connect(lambda session, payload: seen.append((session, payload)))
    try:
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s1", "command": "ls"})
    finally:
        wd.close()
    assert seen, "同一命令重复 5 次应触发 warning"
    session, payload = seen[0]
    assert session == "agent"
    assert payload["type"] == "pet/exploration-watchdog"
    assert payload["level"] == "warning"
    assert payload["reasons"]
    # mode 在 watchdog 内没有任何数据源、pet 侧也无消费方：不再输出未定义字段。
    assert "mode" not in payload


def test_plugin_user_message_does_not_override_goal(app):
    """agent.inject() 注入上下文（sourceKind=plugin）不得覆盖真人目标。

    每轮 4-5 条 system-reminder/技能目录注入记录都是 user/message,若不区分,
    goal 会被注入文案冲掉,探索看门狗据此误判「对话目标」。
    """
    wd = ExplorationWatchdog()
    try:
        wd.feed_record("agent", {"event": "user/message", "sourceKind": "user",
                                 "text": "帮我修 user/message 事件"})
        with wd._lock:
            assert wd._states["agent"]["goal"] == "帮我修 user/message 事件"
        wd.feed_record("agent", {"event": "user/message", "sourceKind": "plugin",
                                 "text": "<system-reminder> 技能目录……"})
        with wd._lock:
            assert wd._states["agent"]["goal"] == "帮我修 user/message 事件"
    finally:
        wd.close()


def test_long_think_warning_payload_is_emitted(app):
    """超长 Think 的定时轮询路径同样构造 payload，不得抛 AttributeError。"""
    wd = ExplorationWatchdog()
    seen = []
    wd.warning.connect(lambda session, payload: seen.append(payload))
    try:
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "继续思考"})
        with wd._lock:
            state = wd._states["agent"]
            state["current"].think_started_at -= wd.long_think_seconds + 1
        wd._poll_long_think()
    finally:
        wd.close()
    assert seen, "超长 Think 应触发 warning"
    assert seen[0]["threshold_phase"] == "long-think"
    assert "mode" not in seen[0]


class _FakeClock:
    """单调钟替身：只实现 watchdog 模块用到的 time.monotonic。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now


def test_pause_stops_think_timer_and_ignores_feed(app):
    """桌宠隐藏（决策：方案A）——看门狗暂停：轮询停走、喂入忽略。"""
    wd = ExplorationWatchdog()
    try:
        wd.pause()
        assert not wd._think_timer.isActive(), "暂停后 1s 轮询必须停走"
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "思考"})
        assert "agent" not in wd._states, "暂停期间喂入必须忽略，不积累状态"
        wd.pause()  # 幂等
        wd.resume()
        assert wd._think_timer.isActive(), "恢复后轮询必须重启"
        wd.resume()  # 幂等
    finally:
        wd.close()


def test_pause_does_not_latch_long_think_and_resume_reanchors(app, monkeypatch):
    """方案A：隐藏时长不计入任何时长判定，计时锚点整体后移暂停时长。

    已超阈值的长思考在暂停期间手动轮询也不得发射或置位已上报标志；
    恢复后可见期积累仍然有效——提醒延迟到恢复时发出，而不是永久丢失。
    """
    import pet.exploration_watchdog as watchdog_mod

    clock = _FakeClock()
    monkeypatch.setattr(watchdog_mod, "time", clock)
    wd = ExplorationWatchdog()
    seen = []
    wd.warning.connect(lambda session, payload: seen.append(payload))
    try:
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "思考"})
        with wd._lock:
            state = wd._states["agent"]
            state["current"].think_started_at = clock.now - wd.long_think_seconds - 5
            started_before = state["started_at"]
            grace_delta_before = state["grace_until"] - state["started_at"]

        wd.pause()
        wd._poll_long_think()  # 暂停期间即使手动轮询也不得发射/置位
        assert not seen, "暂停期间不得发射长思考提醒"
        with wd._lock:
            assert wd._states["agent"]["current"].long_think_reported is False, \
                "暂停期间轮询不得置位已上报标志（否则恢复后提醒永久丢失）"

        clock.now += 600  # 隐藏 10 分钟
        wd.resume()
        with wd._lock:
            state = wd._states["agent"]
            assert state["started_at"] == started_before + 600, "隐藏时长不得计入累计"
            assert abs((state["grace_until"] - state["started_at"]) - grace_delta_before) < 1e-6, \
                "宽限期与起始时间的相对关系必须保持不变"
            assert state["current"].think_started_at == clock.now - wd.long_think_seconds - 5

        wd._poll_long_think()
        assert len(seen) == 1, "可见期积累的超阈值提醒应在恢复后补发（延迟而非丢失）"
        assert seen[0]["threshold_phase"] == "long-think"
    finally:
        wd.close()
