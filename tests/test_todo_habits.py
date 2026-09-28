"""Local, title-free habit samples used by todo scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta
import json

from pet.config import Config
from pet.todo_habits import (
    TodoHabitObserver,
    TodoHabitStore,
    classify_foreground_activity,
    estimate_duration_minutes,
)


def test_habit_store_keeps_only_compact_samples_and_trains_in_batches(tmp_path):
    path = tmp_path / "todo_habits.json"
    store = TodoHabitStore(path)
    monday = datetime(2026, 9, 7, 18, 0)

    for index, duration in enumerate((30, 45, 60, 45, 45)):
        started = monday + timedelta(days=7 * index)
        result = store.record_observed_session(
            "study", started, started + timedelta(minutes=duration)
        )
        assert result == {"recorded": True, "duration_min": duration}

    profile = store.profile()
    study = profile["categories"]["study"]
    assert study["count"] == 5
    assert study["duration_min"] == 45
    assert study["hour_counts"][18] == 5
    assert study["weekday_counts"][0] == 5
    assert estimate_duration_minutes(profile, "study") == 45

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert all("title" not in sample for sample in payload["samples"])
    assert all(set(sample) == {"category", "weekday", "hour", "duration_min"}
               for sample in payload["samples"])


def test_store_discards_legacy_manual_session_and_rejects_outlier(tmp_path):
    path = tmp_path / "todo_habits.json"
    started = datetime(2026, 9, 28, 20, 0)
    path.write_text(json.dumps({
        "version": 1,
        "active": {
            "todo_id": "old-id",
            "category": "study",
            "started_at": started.isoformat(),
        },
        "samples": [],
        "profile": {"categories": {}},
        "pending": 0,
    }), encoding="utf-8")

    store = TodoHabitStore(path)
    result = store.record_observed_session(
        "study", started, started + timedelta(hours=5)
    )

    assert result == {"recorded": False, "duration_min": 300}
    assert store.profile()["categories"] == {}
    store.record_observed_session("study", started, started + timedelta(minutes=30))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert "active" not in payload


def test_duration_estimate_uses_safe_default_until_enough_examples():
    profile = {"categories": {"study": {"count": 2, "duration_min": 30}}}
    assert estimate_duration_minutes(profile, "study") == 60


def test_foreground_classifier_needs_a_clear_learning_signal():
    assert classify_foreground_activity("chrome.exe", "Python course - lecture 3") == "study"
    assert classify_foreground_activity("Anki.exe", "Anki") == "study"
    assert classify_foreground_activity("EXCEL.EXE", "Q3 status report") == "work"
    assert classify_foreground_activity("chrome.exe", "The Crown - Season 1") == "leisure"
    assert classify_foreground_activity("chrome.exe", "Inbox - Mail") is None


def test_observer_records_sessions_automatically_without_retaining_window_text(tmp_path):
    path = tmp_path / "todo_habits.json"
    store = TodoHabitStore(path)
    observer = TodoHabitObserver(store, poll_interval_seconds=60)
    started = datetime(2026, 9, 28, 9, 0)

    observer.observe({"process": "chrome.exe", "title": "Python course"}, 0, now=started)
    observer.observe(
        {"process": "chrome.exe", "title": "Python course"},
        30,
        now=started + timedelta(minutes=1),
    )
    observer.observe(
        {"process": "notepad.exe", "title": "Notes"},
        0,
        now=started + timedelta(minutes=2),
    )

    assert not observer.is_active
    assert store.sample_count("study") == 1
    sample = json.loads(path.read_text(encoding="utf-8"))["samples"][0]
    assert sample == {
        "category": "study",
        "weekday": started.weekday(),
        "hour": started.hour,
        "duration_min": 2,
    }
    assert "title" not in sample and "process" not in sample


def test_observer_keeps_work_study_and_leisure_profiles_separate(tmp_path):
    path = tmp_path / "todo_habits.json"
    store = TodoHabitStore(path)
    observer = TodoHabitObserver(store, poll_interval_seconds=60)
    started = datetime(2026, 9, 28, 10, 0)

    for process, title, offset in (
        ("EXCEL.EXE", "Q3 status report", 0),
        ("chrome.exe", "Python course lesson", 180),
        ("steam.exe", "Steam", 360),
    ):
        begin = started + timedelta(minutes=offset)
        observer.observe({"process": process, "title": title}, 0, now=begin)
        observer.observe({"process": process, "title": title}, 0, now=begin + timedelta(minutes=1))
        observer.observe({}, 0, now=begin + timedelta(minutes=2))

    assert store.sample_count("work") == 1
    assert store.sample_count("study") == 1
    assert store.sample_count("leisure") == 1
    categories = [sample["category"] for sample in json.loads(path.read_text(encoding="utf-8"))["samples"]]
    assert categories == ["work", "study", "leisure"]
    assert "Q3 status report" not in path.read_text(encoding="utf-8")


def test_observer_closes_after_idle_and_trains_after_five_automatic_sessions(tmp_path):
    store = TodoHabitStore(tmp_path / "todo_habits.json")
    observer = TodoHabitObserver(store, poll_interval_seconds=60)
    monday = datetime(2026, 9, 28, 16, 0)

    for index in range(5):
        started = monday + timedelta(days=7 * index)
        window = {"process": "msedge.exe", "title": "English lesson"}
        observer.observe(window, 0, now=started)
        observer.observe(window, 0, now=started + timedelta(minutes=89))
        observer.observe(window, 0, now=started + timedelta(minutes=90))
        observer.observe(window, 601, now=started + timedelta(minutes=91))

    profile = store.profile()["categories"]["study"]
    assert profile["count"] == 5
    assert profile["duration_min"] == 90
    assert profile["hour_counts"][16] == 5
    assert not observer.is_active


def test_observer_stop_caps_session_at_last_observed_interval(tmp_path):
    store = TodoHabitStore(tmp_path / "todo_habits.json")
    observer = TodoHabitObserver(store, poll_interval_seconds=60)
    started = datetime(2026, 9, 28, 9, 0)

    observer.observe({"process": "EXCEL.EXE", "title": "Q3 report"}, 0, now=started)
    observer.observe(
        {"process": "EXCEL.EXE", "title": "Q3 report"},
        0,
        now=started + timedelta(minutes=10),
    )
    observer.stop(now=started + timedelta(minutes=40))

    sample = json.loads((tmp_path / "todo_habits.json").read_text(encoding="utf-8"))["samples"][0]
    assert sample["duration_min"] == 11


def test_automatic_learning_preference_defaults_off_and_roundtrips(tmp_path):
    cfg = Config(tmp_path / "appdata")
    assert cfg.get("todo_habit_learning_enabled") is False

    cfg.set("todo_habit_learning_enabled", "true")
    assert cfg.get("todo_habit_learning_enabled") is True
    assert cfg.save() is True

    reloaded = Config(tmp_path / "appdata")
    assert reloaded.get("todo_habit_learning_enabled") is True
    reloaded.set("todo_habit_learning_enabled", "false")
    assert reloaded.get("todo_habit_learning_enabled") is False


def test_app_starts_low_rate_probe_only_after_opt_in(tmp_path, monkeypatch):
    import pet.app as app_module
    import pet.vision as vision

    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell

    QApplication.instance() or QApplication([])
    cfg = Config(tmp_path / "appdata")
    shell = AppShell.__new__(AppShell)
    shell.config = cfg
    shell.todo_habits = None
    shell.todo_habit_observer = None
    shell._todo_habit_timer = None
    calls = []
    monkeypatch.setattr(app_module.sys, "platform", "win32")
    monkeypatch.setattr(vision, "foreground_window_info", lambda: calls.append("window") or None)
    monkeypatch.setattr(vision, "get_system_idle_seconds", lambda: calls.append("idle") or 0)

    shell._sync_todo_habit_learning()
    assert shell._todo_habit_timer is None
    assert calls == []

    assert shell._set_todo_habit_learning_enabled(True) is True
    assert shell._todo_habit_timer.isActive()
    assert calls == ["window", "idle"]

    assert shell._set_todo_habit_learning_enabled(False) is True
    assert not shell._todo_habit_timer.isActive()
    shell._stop_todo_habit_learning()
