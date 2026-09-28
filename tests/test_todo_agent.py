"""Manual text entry for the todo agent."""
from __future__ import annotations

import os
import json
import threading
from types import SimpleNamespace
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)
from PySide6.QtCore import Qt

from pet.todo_agent import TodoAgent, find_available_todo_slot, parse_todo_response


class _TodoService:
    def __init__(self):
        self._items = []

    def apply_config(self):
        pass

    def items(self):
        return list(self._items)

    def set_items(self, items):
        self._items = list(items)


class _TodoAgent:
    def __init__(self, *, accepted=True):
        self.accepted = accepted
        self.requests = []

    def submit(self, session_id, text, *, existing_todos=None, habit_profile=None):
        self.requests.append((session_id, text, existing_todos, habit_profile))
        return self.accepted


class _App:
    def __init__(self, agent):
        self.todo_service = _TodoService()
        self.todo_agent = agent
        self.config = _Config()


class _Config:
    def __init__(self):
        self.values = {}
        self.provider = SimpleNamespace(
            name="DeepSeek", model="deepseek-v4-flash", timeout=20,
            max_tokens=700, api_key="",
        )

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        return True

    def chat_settings(self):
        return SimpleNamespace(active_config=self.provider)


class _PanelResult:
    def __init__(self):
        self.reloaded = False
        self.completed = []

    def reload_items(self):
        self.reloaded = True

    def finish_agent_request(self, session_id, status, *, added_count=0):
        self.completed.append((session_id, status, added_count))


def _qapp():
    return QApplication.instance() or QApplication([])


def test_new_todo_button_keeps_manual_flow_and_agent_button_opens_popup():
    from pet.todo_panel import TodoPanelDialog

    app = _qapp()
    panel = TodoPanelDialog(_App(_TodoAgent()))
    try:
        panel.show()
        app.processEvents()
        popup = panel.findChild(QDialog, "todoAgentDialog")
        new_todo = panel.findChild(QPushButton, "todoAddButton")
        generate = panel.findChild(QPushButton, "todoAgentOpenButton")
        manual = panel.findChild(QPushButton, "todoManualAddButton")
        hint = panel.findChild(QLabel, "todoAgentHint")

        assert popup is not None
        assert popup.accessibleName() == "LLM生成待办"
        assert popup.windowModality() == Qt.WindowModality.WindowModal
        assert popup.minimumSize().width() == 440
        assert popup.findChild(QFrame, "todoAgentCard") is None
        assert hint is not None
        assert "已有待办安排空档" in hint.text()
        assert "桌宠设置" in hint.text()
        assert "会议类提前" not in hint.text()
        assert new_todo is not None
        assert generate is not None
        assert generate.property("accent") is True
        assert generate.property("accent") == new_todo.property("accent")
        assert manual is not None
        assert not popup.isVisible()

        new_todo.click()
        app.processEvents()
        assert not popup.isVisible()
        assert panel._editor_card.isVisible()
        assert not generate.isEnabled()

        panel.close_editor()
        app.processEvents()
        assert generate.isEnabled()

        generate.click()
        app.processEvents()
        assert popup.isVisible()
        assert popup.focusWidget() is panel._agent_text
        assert not panel._editor_card.isVisible()

        manual.click()
        app.processEvents()
        assert not popup.isVisible()
        assert panel._editor_card.isVisible()
        assert panel.focusWidget() is panel._title_edit
        assert not generate.isEnabled()

        panel.close_editor()
        generate.click()
        app.processEvents()
        popup.reject()
        app.processEvents()
        assert panel.focusWidget() is generate
    finally:
        panel.close()


def test_todo_panel_submits_freeform_text_to_agent_and_reports_result():
    from pet.todo_panel import TodoPanelDialog

    _qapp()
    agent = _TodoAgent()
    panel = TodoPanelDialog(_App(agent))
    try:
        message = panel.findChild(QPlainTextEdit, "todoAgentInput")
        input_label = panel.findChild(QLabel, "todoAgentInputLabel")
        submit = panel.findChild(QPushButton, "todoAgentSubmitButton")
        status = panel.findChild(QLabel, "todoAgentStatus")
        model_note = panel.findChild(QLabel, "todoAgentModelNote")
        assert message is not None
        assert input_label is not None
        assert input_label.buddy() is message
        assert submit is not None
        assert status is not None
        assert model_note is not None
        assert "DeepSeek" in model_note.text()
        assert "deepseek-v4-flash" in model_note.text()
        assert "Chat Completions" in model_note.text()
        assert "API Key" in model_note.text()
        assert "额度" in model_note.text()
        assert message.accessibleName() == "待办文本"
        assert not submit.isEnabled()

        message.setPlainText("明天下午三点给客户回电话，然后周五上午提交周报")
        existing = [{"kind": "once", "date": "2099-09-05", "time": "14:00", "enabled": True}]
        panel._app.todo_service._items = existing
        panel._app.config.provider.name = "OpenAI-Compatible"
        panel._app.config.provider.model = "gpt-4.1-mini"
        assert submit.isEnabled()
        submit.click()

        assert "OpenAI-Compatible" in model_note.text()
        assert "gpt-4.1-mini" in model_note.text()

        assert len(agent.requests) == 1
        session_id, submitted_text, schedule, profile = agent.requests[0]
        assert session_id.startswith("todo-panel:")
        assert submitted_text == message.toPlainText()
        assert schedule == existing
        assert profile == {"categories": {}}
        assert not submit.isEnabled()
        assert not message.isEnabled()
        assert "正在识别" in status.text()

        panel.finish_agent_request(session_id, "success", added_count=2)
        assert not submit.isEnabled()
        assert message.isEnabled()
        assert message.toPlainText() == ""
        assert status.text() == "已添加 2 条待办。"

        message.setPlainText("这段话没有明确的未来安排")
        assert submit.isEnabled()
        submit.click()
        empty_session_id = agent.requests[-1][0]
        panel.finish_agent_request(empty_session_id, "empty")
        assert submit.isEnabled()
        assert message.toPlainText() == "这段话没有明确的未来安排"
        assert "没有识别到" in status.text()
    finally:
        panel.close()


def test_todo_panel_reports_when_agent_cannot_accept_text():
    from pet.todo_panel import TodoPanelDialog

    _qapp()
    agent = _TodoAgent(accepted=False)
    panel = TodoPanelDialog(_App(agent))
    try:
        message = panel.findChild(QPlainTextEdit, "todoAgentInput")
        submit = panel.findChild(QPushButton, "todoAgentSubmitButton")
        status = panel.findChild(QLabel, "todoAgentStatus")
        message.setPlainText("明天记得交材料")
        submit.click()

        assert agent.requests
        assert submit.isEnabled()
        assert message.isEnabled()
        assert message.toPlainText() == "明天记得交材料"
        assert "暂时不可用" in status.text()
    finally:
        panel.close()


def test_todo_panel_offers_automatic_learning_command_not_per_item_timers(
    tmp_path, monkeypatch
):
    import pet.todo_panel as todo_panel_module

    from pet.todo_habits import TodoHabitStore
    from pet.todo_panel import TodoPanelDialog
    from pet.todo_reminder import new_todo_item

    # This UI path is Windows-only; simulate that platform without mutating the
    # shared sys module used by pytest and unrelated application modules.
    monkeypatch.setattr(todo_panel_module, "sys", SimpleNamespace(platform="win32"))
    _qapp()
    app = _App(_TodoAgent())
    store = TodoHabitStore(tmp_path / "todo_habits.json")
    app._ensure_todo_habits = lambda: store
    app.todo_service.set_items([new_todo_item("学英语", "daily", "20:00")])
    app._set_todo_habit_learning_enabled = lambda enabled: (
        app.config.set("todo_habit_learning_enabled", enabled) or app.config.save()
    )
    panel = TodoPanelDialog(app)
    try:
        toggle = panel.findChild(QPushButton, "todoHabitLearningToggle")
        assert toggle is not None
        assert toggle.text() == "开启自动学习"
        assert not any(
            child.objectName().startswith("todoTrack_")
            for child in panel.findChildren(QPushButton)
        )
        toggle.click()

        assert app.config.get("todo_habit_learning_enabled") is True
        assert toggle.text() == "暂停自动学习"
        assert store.sample_count("study") == 0
    finally:
        panel.close()


def test_app_ignores_model_per_item_reminder_and_completes_panel_request():
    from pet.app import AppShell

    service = _TodoService()
    panel = _PanelResult()
    shell = AppShell.__new__(AppShell)
    shell._ensure_todo_service = lambda: service
    shell.todo_panel = panel
    shell.win = None

    session_id = "todo-panel:request-1"
    AppShell._accept_agent_todos(
        shell,
        session_id,
        [{"title": "项目会议", "kind": "once", "date": "2026-12-31", "time": "09:00",
          "reminder_lead_minutes": 30}],
    )

    assert len(service.items()) == 1
    assert service.items()[0]["title"] == "项目会议"
    assert "reminder_lead_minutes" not in service.items()[0]
    assert panel.reloaded
    assert panel.completed == [(session_id, "success", 1)]


def test_app_reports_empty_agent_results_to_the_matching_panel_request():
    from pet.app import AppShell

    panel = _PanelResult()
    shell = AppShell.__new__(AppShell)
    shell.todo_panel = panel

    session_id = "todo-panel:request-2"
    AppShell._accept_agent_todos(shell, session_id, [])

    assert panel.completed == [(session_id, "empty", 0)]


def test_dsh_message_passes_current_todo_snapshot_to_agent():
    from pet.app import AppShell

    agent = _TodoAgent()
    schedule = [{"kind": "once", "date": "2099-09-05", "time": "14:00", "enabled": True}]
    shell = AppShell.__new__(AppShell)
    shell._dsh_link_manager = lambda: None
    shell._todo_items_for_agent = lambda: schedule
    shell.todo_agent = agent

    AppShell._on_dsh_user_message(shell, "session-1", "明天有会议")

    assert agent.requests == [(
        "session-1", "明天有会议", schedule, {"categories": {}}
    )]


def test_habit_profile_estimates_study_duration_and_prefers_learned_hour():
    now = datetime(2026, 9, 7, 8, 0)  # Monday
    profile = {"categories": {"study": {
        "count": 5,
        "duration_min": 45,
        "weekday_counts": [5, 0, 0, 0, 0, 0, 0],
        "hour_counts": [0] * 19 + [5] + [0] * 4,
    }}}

    result = parse_todo_response(
        json.dumps({"todos": [{
            "title": "学英语",
            "kind": "once",
            "time": "",
            "time_is_explicit": False,
        }]}),
        now,
        habit_profile=profile,
    )

    assert result == [{
        "title": "学英语",
        "kind": "once",
        "date": "2026-09-07",
        "time": "19:00",
        "category": "study",
        "duration_min": 45,
    }]


def test_habit_profile_prefers_learned_weekday_across_open_days():
    now = datetime(2026, 9, 28, 23, 30)  # Monday night
    weekdays = [0] * 7
    weekdays[4] = 10  # Friday
    hours = [0] * 24
    hours[16] = 10
    profile = {"categories": {"study": {
        "count": 10,
        "duration_min": 90,
        "weekday_counts": weekdays,
        "hour_counts": hours,
    }}}

    slot = find_available_todo_slot(
        [], now, duration_min=90, category="study", habit_profile=profile
    )

    assert slot == ("2026-10-02", "16:00")


def test_auto_schedule_separates_work_from_study_and_leisure_time():
    now = datetime(2026, 9, 28, 8, 0)

    assert find_available_todo_slot([], now, category="work")[1] == "09:00"
    assert find_available_todo_slot([], now, category="study")[1] == "18:00"
    assert find_available_todo_slot([], now, category="leisure")[1] == "18:00"


def test_auto_schedule_uses_separate_work_and_leisure_habit_profiles():
    now = datetime(2026, 9, 28, 8, 0)
    work_hours = [0] * 24
    work_hours[10] = 5
    leisure_hours = [0] * 24
    leisure_hours[20] = 5
    monday_counts = [5, 0, 0, 0, 0, 0, 0]
    profile = {"categories": {
        "work": {
            "count": 5, "duration_min": 60,
            "weekday_counts": monday_counts, "hour_counts": work_hours,
        },
        "leisure": {
            "count": 5, "duration_min": 60,
            "weekday_counts": monday_counts, "hour_counts": leisure_hours,
        },
    }}

    work = find_available_todo_slot([], now, category="work", habit_profile=profile)
    leisure = find_available_todo_slot([], now, category="leisure", habit_profile=profile)

    assert work == ("2026-09-28", "10:00")
    assert leisure == ("2026-09-28", "20:00")


def test_agent_category_routes_ambiguous_title_to_matching_habit_profile():
    now = datetime(2026, 9, 28, 8, 0)
    work_hours = [0] * 24
    work_hours[10] = 5
    leisure_hours = [0] * 24
    leisure_hours[20] = 5
    weekdays = [5, 0, 0, 0, 0, 0, 0]
    profile = {"categories": {
        "work": {
            "count": 5, "duration_min": 60,
            "weekday_counts": weekdays, "hour_counts": work_hours,
        },
        "leisure": {
            "count": 5, "duration_min": 60,
            "weekday_counts": weekdays, "hour_counts": leisure_hours,
        },
    }}

    work = parse_todo_response(
        json.dumps({"todos": [{
            "title": "整理素材",
            "category": "work",
            "kind": "once",
            "time_is_explicit": False,
        }]}),
        now,
        habit_profile=profile,
    )
    leisure = parse_todo_response(
        json.dumps({"todos": [{
            "title": "整理素材",
            "category": "leisure",
            "kind": "once",
            "time_is_explicit": False,
        }]}),
        now,
        habit_profile=profile,
    )

    assert (work[0]["category"], work[0]["time"]) == ("work", "10:00")
    assert (leisure[0]["category"], leisure[0]["time"]) == ("leisure", "20:00")


def test_todo_agent_prompt_requires_a_category_for_each_todo():
    from pet.todo_agent import _SYSTEM_PROMPT

    assert '"category":"work"' in _SYSTEM_PROMPT
    assert "category 只能为 work、study、leisure 或 general" in _SYSTEM_PROMPT
    assert "课程、读书、备考、自我提升归 study" in _SYSTEM_PROMPT
    assert "游戏、娱乐、休息、个人消遣归 leisure" in _SYSTEM_PROMPT


def test_meeting_todo_uses_global_reminder_preference():
    now = datetime(2026, 9, 4, 8, 0)
    result = parse_todo_response(
        json.dumps({"todos": [{
            "title": "项目会议",
            "kind": "once",
            "date": "2026-09-05",
            "date_is_explicit": True,
            "time": "10:00",
            "time_is_explicit": True,
            "is_meeting": True,
        }]}),
        now,
    )

    assert result == [{
        "title": "项目会议",
        "kind": "once",
        "date": "2026-09-05",
        "time": "10:00",
        "category": "work",
        "duration_min": 60,
    }]


def test_missing_time_uses_a_free_slot_from_existing_todos():
    now = datetime(2026, 9, 4, 8, 0)
    existing = [
        {"kind": "once", "date": "2026-09-05", "time": time, "enabled": True}
        for time in ("09:00", "11:00", "13:00")
    ]
    result = parse_todo_response(
        json.dumps({"todos": [{
            "title": "项目会议",
            "kind": "once",
            "date": "2026-09-05",
            "date_is_explicit": True,
            "time": "",
            "time_is_explicit": False,
        }]}),
        now,
        existing_todos=existing,
    )

    assert result == [{
        "title": "项目会议",
        "kind": "once",
        "date": "2026-09-05",
        "time": "15:00",
        "category": "work",
        "duration_min": 60,
    }]


def test_missing_date_and_time_keep_the_scheduled_open_day():
    now = datetime(2026, 9, 4, 8, 0)
    result = parse_todo_response(
        json.dumps({"todos": [{
            "title": "准备汇报",
            "kind": "once",
            "date": "",
            "date_is_explicit": False,
            "time": "",
            "time_is_explicit": False,
        }]}),
        now,
        existing_todos=[
            {"kind": "once", "date": "2026-09-04", "time": time, "enabled": True}
            for time in ("09:00", "10:00", "11:00")
        ],
    )

    assert result == [{
        "title": "准备汇报",
        "kind": "once",
        "date": "2026-09-05",
        "time": "09:00",
        "category": "work",
        "duration_min": 60,
    }]


def test_multiple_missing_times_get_distinct_free_slots():
    now = datetime(2026, 9, 4, 8, 0)
    result = parse_todo_response(
        json.dumps({"todos": [
            {
                "title": title,
                "kind": "once",
                "date": "2026-09-05",
                "date_is_explicit": True,
                "time": "",
                "time_is_explicit": False,
            }
            for title in ("准备会议", "整理资料")
        ]}),
        now,
        existing_todos=[
            {"kind": "once", "date": "2026-09-05", "time": time, "enabled": True}
            for time in ("09:00", "11:00", "13:00")
        ],
    )

    assert [item["date"] for item in result] == ["2026-09-05", "2026-09-05"]
    assert [item["time"] for item in result] == ["15:00", "17:00"]


def test_available_slot_prefers_a_less_busy_day_and_ignores_disabled_items():
    now = datetime(2026, 9, 4, 8, 0)
    existing = [
        {"kind": "once", "date": "2026-09-04", "time": "09:00", "enabled": True},
        {"kind": "once", "date": "2026-09-04", "time": "10:00", "enabled": True},
        {"kind": "once", "date": "2026-09-05", "time": "10:00", "enabled": False},
    ]

    assert find_available_todo_slot(existing, now) == ("2026-09-05", "09:00")


def test_agent_includes_existing_schedule_in_model_prompt(monkeypatch):
    from pet.chat import providers

    received = {}
    called = threading.Event()

    class _Provider:
        def stream(self, messages, *_args, **_kwargs):
            received["messages"] = messages
            called.set()
            return iter(['{"todos":[]}'])

    monkeypatch.setattr(providers, "OpenAICompatibleProvider", _Provider)
    config = _Config()
    config.resolve_api_key = lambda _provider: "test-only"
    agent = TodoAgent(config, lambda *_args: None)
    next_day = (datetime.now() + timedelta(days=1)).date().isoformat()
    existing = [{"kind": "once", "date": next_day, "time": "14:00", "enabled": True}]
    try:
        assert agent.submit("s", "明天开会", existing_todos=existing)
        assert called.wait(3.0)
        user_prompt = received["messages"][1]["content"]
        assert f"{next_day} 14:00" in user_prompt
        assert "不代表外部日历" in user_prompt
    finally:
        agent.shutdown()
