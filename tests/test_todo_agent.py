"""Manual text entry for the todo agent."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit, QPushButton


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

    def submit(self, session_id, text):
        self.requests.append((session_id, text))
        return self.accepted


class _App:
    def __init__(self, agent):
        self.todo_service = _TodoService()
        self.todo_agent = agent
        self.config = _Config()


class _Config:
    def __init__(self):
        self.provider = SimpleNamespace(name="DeepSeek", model="deepseek-v4-flash")

    def get(self, key, default=None):
        return default

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

        assert popup is not None
        assert new_todo is not None
        assert generate is not None
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
        assert not panel._editor_card.isVisible()

        manual.click()
        app.processEvents()
        assert not popup.isVisible()
        assert panel._editor_card.isVisible()
        assert not generate.isEnabled()
    finally:
        panel.close()


def test_todo_panel_submits_freeform_text_to_agent_and_reports_result():
    from pet.todo_panel import TodoPanelDialog

    _qapp()
    agent = _TodoAgent()
    panel = TodoPanelDialog(_App(agent))
    try:
        message = panel.findChild(QPlainTextEdit, "todoAgentInput")
        submit = panel.findChild(QPushButton, "todoAgentSubmitButton")
        status = panel.findChild(QLabel, "todoAgentStatus")
        model_note = panel.findChild(QLabel, "todoAgentModelNote")
        assert message is not None
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
        panel._app.config.provider.name = "OpenAI-Compatible"
        panel._app.config.provider.model = "gpt-4.1-mini"
        assert submit.isEnabled()
        submit.click()

        assert "OpenAI-Compatible" in model_note.text()
        assert "gpt-4.1-mini" in model_note.text()

        assert len(agent.requests) == 1
        session_id, submitted_text = agent.requests[0]
        assert session_id.startswith("todo-panel:")
        assert submitted_text == message.toPlainText()
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


def test_app_stores_agent_results_and_completes_the_matching_panel_request():
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
        [{"title": "提交周报", "kind": "once", "date": "2026-12-31", "time": "09:00"}],
    )

    assert len(service.items()) == 1
    assert service.items()[0]["title"] == "提交周报"
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
