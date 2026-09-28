# -*- coding: utf-8 -*-
"""Local batch profile for learning work, study and leisure periods.

Foreground metadata is classified in memory. The store keeps no task titles,
window titles, process names or screen captures; it retains only a bounded set
of category, weekday, hour and duration features, then refreshes its empirical
profile after a batch of five new sessions.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import statistics

logger = logging.getLogger(__name__)

TODO_HABIT_VERSION = 2
TODO_HABIT_SAMPLE_LIMIT = 256
TODO_HABIT_BATCH_SIZE = 5
TODO_HABIT_MIN_DURATION_SAMPLES = 3
TODO_HABIT_MIN_SESSION_MINUTES = 2
TODO_HABIT_MAX_SESSION_MINUTES = 240
TODO_HABIT_DEFAULT_DURATION_MINUTES = 60
TODO_HABIT_POLL_INTERVAL_SECONDS = 60
TODO_HABIT_IDLE_CUTOFF_SECONDS = 10 * 60
TODO_HABIT_CATEGORY_STUDY = "study"
TODO_HABIT_CATEGORY_WORK = "work"
TODO_HABIT_CATEGORY_LEISURE = "leisure"
TODO_HABIT_CATEGORY_GENERAL = "general"
_CATEGORIES = {
    TODO_HABIT_CATEGORY_STUDY,
    TODO_HABIT_CATEGORY_WORK,
    TODO_HABIT_CATEGORY_LEISURE,
    TODO_HABIT_CATEGORY_GENERAL,
}
_STUDY_WORDS = (
    "学习", "读书", "阅读", "看书", "背单词", "单词", "复习", "预习",
    "刷题", "做题", "练习", "课程", "网课", "论文", "作业", "自学",
    "听课", "学英语", "学数学", "学编程", "课件", "讲义", "教材",
    "学习资料", "在线课堂", "公开课", "题库", "考试复习",
    "study", "studying", "lesson", "lecture", "course", "tutorial",
    "textbook", "exam prep",
)
_ASCII_STUDY_WORDS = tuple(word for word in _STUDY_WORDS if word.isascii())
_WORK_WORDS = (
    "工作", "会议", "项目", "客户", "工单", "周报", "日报", "汇报", "开发",
    "代码", "邮件", "合同", "审批", "需求", "交付", "排期", "值班",
    "report", "meeting", "standup", "project", "ticket", "customer", "client",
    "email", "work", "sprint", "deploy", "deadline", "review",
)
_ASCII_WORK_WORDS = tuple(word for word in _WORK_WORDS if word.isascii())
_LEISURE_WORDS = (
    "休息", "休闲", "娱乐", "游戏", "电影", "电视剧", "追剧", "直播", "短视频",
    "漫画", "小说", "音乐", "movie", "film", "series", "season", "episode",
    "gaming", "gameplay", "netflix", "youtube", "twitch", "anime",
)
_ASCII_LEISURE_WORDS = tuple(word for word in _LEISURE_WORDS if word.isascii())
_DEDICATED_STUDY_APPS = {
    "anki.exe", "ankiapp.exe", "duolingo.exe", "quizlet.exe", "memrise.exe",
    "supermemo.exe",
}
_WORK_APPS = {
    "excel.exe", "winword.exe", "powerpnt.exe", "outlook.exe", "teams.exe",
    "slack.exe", "zoom.exe", "devenv.exe", "code.exe", "pycharm64.exe",
    "idea64.exe",
}
_LEISURE_APPS = {
    "steam.exe", "epicgameslauncher.exe", "vlc.exe", "potplayer.exe",
    "potplayermini64.exe", "mpc-hc.exe", "mpc-hc64.exe",
}


def classify_todo_category(title, category: str | None = None) -> str:
    """Use an explicit category or conservatively infer work/study/leisure from text."""
    value = str(category or "").strip().lower()
    if value in _CATEGORIES:
        return value
    text = str(title or "").casefold()
    if _has_category_word(text, _STUDY_WORDS, _ASCII_STUDY_WORDS):
        return TODO_HABIT_CATEGORY_STUDY
    if _has_category_word(text, _LEISURE_WORDS, _ASCII_LEISURE_WORDS):
        return TODO_HABIT_CATEGORY_LEISURE
    if _has_category_word(text, _WORK_WORDS, _ASCII_WORK_WORDS):
        return TODO_HABIT_CATEGORY_WORK
    return TODO_HABIT_CATEGORY_GENERAL


def _has_category_word(text: str, words, ascii_words) -> bool:
    if any(word in text for word in words if not word.isascii()):
        return True
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in ascii_words)


def classify_foreground_activity(process, title) -> str | None:
    """Recognize work, study or leisure from local foreground metadata only.

    Window text is inspected in memory and never returned or persisted. Dedicated
    work/study/leisure apps count directly; ambiguous apps need an explicit context
    word in their title. Unknown windows are ignored rather than guessed.
    """
    process_name = str(process or "").strip().replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if process_name in _DEDICATED_STUDY_APPS:
        return TODO_HABIT_CATEGORY_STUDY
    text = str(title or "").casefold()
    title_category = classify_todo_category(text)
    if title_category != TODO_HABIT_CATEGORY_GENERAL:
        return title_category
    if process_name in _WORK_APPS:
        return TODO_HABIT_CATEGORY_WORK
    if process_name in _LEISURE_APPS:
        return TODO_HABIT_CATEGORY_LEISURE
    return None


def estimate_duration_minutes(profile, category: str = TODO_HABIT_CATEGORY_GENERAL) -> int:
    """Estimate a todo's duration from a trained category median or use one hour."""
    if not isinstance(profile, dict):
        return TODO_HABIT_DEFAULT_DURATION_MINUTES
    categories = profile.get("categories")
    info = categories.get(classify_todo_category("", category)) if isinstance(categories, dict) else None
    if not isinstance(info, dict):
        return TODO_HABIT_DEFAULT_DURATION_MINUTES
    try:
        count = int(info.get("count", 0))
        duration = int(info.get("duration_min", 0))
    except (TypeError, ValueError):
        return TODO_HABIT_DEFAULT_DURATION_MINUTES
    if count < TODO_HABIT_MIN_DURATION_SAMPLES or not 15 <= duration <= TODO_HABIT_MAX_SESSION_MINUTES:
        return TODO_HABIT_DEFAULT_DURATION_MINUTES
    return int(round(duration / 15.0) * 15)


def todo_habits_path(config_dir, instance_id: str = "") -> Path:
    """Path follows the existing per-instance todo storage convention."""
    name = f"todo_habits-{instance_id}.json" if instance_id else "todo_habits.json"
    return Path(config_dir) / name


def _local_naive(value: datetime) -> datetime:
    return value.astimezone().replace(tzinfo=None) if value.tzinfo is not None else value


def _clean_sample(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    category = classify_todo_category("", raw.get("category"))
    try:
        weekday = int(raw.get("weekday"))
        hour = int(raw.get("hour"))
        duration = int(raw.get("duration_min"))
    except (TypeError, ValueError):
        return None
    if not 0 <= weekday <= 6 or not 0 <= hour <= 23:
        return None
    if not TODO_HABIT_MIN_SESSION_MINUTES <= duration <= TODO_HABIT_MAX_SESSION_MINUTES:
        return None
    return {
        "category": category,
        "weekday": weekday,
        "hour": hour,
        "duration_min": duration,
    }


def _clean_profile(raw) -> dict:
    if not isinstance(raw, dict) or not isinstance(raw.get("categories"), dict):
        return {"categories": {}}
    categories = {}
    for category, raw_info in raw["categories"].items():
        if category not in _CATEGORIES or not isinstance(raw_info, dict):
            continue
        raw_weekdays = raw_info.get("weekday_counts")
        raw_hours = raw_info.get("hour_counts")
        if (
            not isinstance(raw_weekdays, list)
            or len(raw_weekdays) != 7
            or not isinstance(raw_hours, list)
            or len(raw_hours) != 24
        ):
            continue
        try:
            count = max(0, int(raw_info.get("count", 0)))
            duration = int(raw_info.get("duration_min", 0))
            weekday_counts = [max(0, int(value)) for value in raw_weekdays]
            hour_counts = [max(0, int(value)) for value in raw_hours]
        except (TypeError, ValueError):
            continue
        if (
            not 0 <= count <= TODO_HABIT_SAMPLE_LIMIT
            or not 15 <= duration <= TODO_HABIT_MAX_SESSION_MINUTES
            or len(weekday_counts) != 7
            or len(hour_counts) != 24
        ):
            continue
        categories[category] = {
            "count": count,
            "duration_min": duration,
            "weekday_counts": weekday_counts,
            "hour_counts": hour_counts,
        }
    return {"categories": categories}


class TodoHabitStore:
    """Bounded title-free session store with lazy, batched local aggregation."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw_samples = raw.get("samples")
        if not isinstance(raw_samples, list):
            raw_samples = []
        raw_samples = raw_samples[-TODO_HABIT_SAMPLE_LIMIT:]
        samples = [
            sample for sample in (_clean_sample(item) for item in raw_samples)
            if sample is not None
        ]
        try:
            pending = max(0, min(TODO_HABIT_BATCH_SIZE, int(raw.get("pending", 0))))
        except (TypeError, ValueError):
            pending = 0
        profile = _clean_profile(raw.get("profile"))
        return {
            "samples": samples[-TODO_HABIT_SAMPLE_LIMIT:],
            "profile": profile,
            "pending": pending,
        }

    def _save(self) -> bool:
        payload = {"version": TODO_HABIT_VERSION, **self._data}
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            logger.exception("待办习惯记录写入失败：%s", self.path)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def record_observed_session(
        self, category: str, started_at: datetime, ended_at: datetime
    ) -> dict:
        """Persist one automatically observed session as coarse local features."""
        started = _local_naive(started_at)
        ended = _local_naive(ended_at)
        try:
            duration = int(round((ended - started).total_seconds() / 60.0))
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            return {"recorded": False, "duration_min": duration}
        recorded = (
            TODO_HABIT_MIN_SESSION_MINUTES
            <= duration
            <= TODO_HABIT_MAX_SESSION_MINUTES
        )
        if recorded:
            self._append_sample(category, started, duration)
            self._save()
        return {"recorded": recorded, "duration_min": duration}

    def _append_sample(self, category: str, started: datetime, duration: int) -> None:
        self._data["samples"].append({
            "category": classify_todo_category("", category),
            "weekday": started.weekday(),
            "hour": started.hour,
            "duration_min": duration,
        })
        self._data["samples"] = self._data["samples"][-TODO_HABIT_SAMPLE_LIMIT:]
        self._data["pending"] += 1
        if self._data["pending"] >= TODO_HABIT_BATCH_SIZE:
            self._train_batch()

    def profile(self) -> dict:
        """Return the cached model, training on demand only after a full batch."""
        if self._data["pending"] >= TODO_HABIT_BATCH_SIZE:
            self._train_batch()
            self._save()
        return deepcopy(self._data["profile"])

    def sample_count(self, category: str | None = None) -> int:
        selected = classify_todo_category("", category) if category else None
        return sum(
            1 for sample in self._data["samples"]
            if selected is None or sample["category"] == selected
        )

    def summary(self) -> str:
        profile = self.profile()
        categories = profile.get("categories", {})
        labels = (
            (TODO_HABIT_CATEGORY_WORK, "工作"),
            (TODO_HABIT_CATEGORY_STUDY, "学习"),
            (TODO_HABIT_CATEGORY_LEISURE, "休闲"),
        )
        parts = []
        for category, label in labels:
            count = self.sample_count(category)
            if not count:
                continue
            info = categories.get(category, {})
            trained_count = int(info.get("count", 0) or 0)
            if trained_count < TODO_HABIT_MIN_DURATION_SAMPLES:
                parts.append(
                    f"{label} {count} 次（排期画像 {trained_count}/{TODO_HABIT_BATCH_SIZE}）"
                )
                continue
            hour_counts = info.get("hour_counts", [])
            hour = (
                max(range(24), key=lambda value: hour_counts[value])
                if len(hour_counts) == 24
                else 0
            )
            period = "早上" if hour < 12 else "下午" if hour < 18 else "晚上"
            duration = int(info.get("duration_min", TODO_HABIT_DEFAULT_DURATION_MINUTES))
            parts.append(f"{label} {count} 次，常见在{period}，约 {duration} 分钟")
        if not parts:
            return "尚无工作、学习或休闲时段样本；识别到连续活动后会自动累计。"
        return "\n".join(parts) + f"\n每类积累 {TODO_HABIT_BATCH_SIZE} 条样本后单独调整排期。"

    def _train_batch(self) -> None:
        categories: dict[str, list[dict]] = {}
        for sample in self._data["samples"]:
            categories.setdefault(sample["category"], []).append(sample)
        trained: dict[str, dict] = {}
        for category, samples in categories.items():
            recent = samples[-64:]
            durations = [sample["duration_min"] for sample in recent]
            median = int(statistics.median(durations))
            duration = max(15, min(TODO_HABIT_MAX_SESSION_MINUTES, int(round(median / 15.0) * 15)))
            weekday_counts = [0] * 7
            hour_counts = [0] * 24
            for sample in recent:
                weekday_counts[sample["weekday"]] += 1
                hour_counts[sample["hour"]] += 1
            trained[category] = {
                "count": len(recent),
                "duration_min": duration,
                "weekday_counts": weekday_counts,
                "hour_counts": hour_counts,
            }
        self._data["profile"] = {"categories": trained}
        self._data["pending"] = 0


class TodoHabitObserver:
    """Turn sparse foreground/idle probes into bounded study-session samples."""

    def __init__(
        self,
        store: TodoHabitStore,
        *,
        poll_interval_seconds: int = TODO_HABIT_POLL_INTERVAL_SECONDS,
        idle_cutoff_seconds: int = TODO_HABIT_IDLE_CUTOFF_SECONDS,
    ) -> None:
        self.store = store
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.idle_cutoff_seconds = max(1, int(idle_cutoff_seconds))
        self._category: str | None = None
        self._started_at: datetime | None = None
        self._last_seen_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self._started_at is not None

    def observe(self, window_info, idle_seconds, *, now: datetime | None = None) -> dict | None:
        """Consume one local probe; return a sample result only when a session closes."""
        current = _local_naive(now or datetime.now())
        try:
            idle = max(0.0, float(idle_seconds))
        except (TypeError, ValueError):
            idle = float(self.idle_cutoff_seconds + 1)
        info = window_info if isinstance(window_info, dict) else {}
        category = classify_foreground_activity(info.get("process"), info.get("title"))
        if idle >= self.idle_cutoff_seconds:
            category = None

        if category is not None:
            if self._started_at is None:
                self._category = category
                self._started_at = current
            elif self._category != category:
                self._finish(self._estimated_end(current))
                self._category = category
                self._started_at = current
            self._last_seen_at = current

            if self._started_at and (
                current - self._started_at
            ).total_seconds() >= TODO_HABIT_MAX_SESSION_MINUTES * 60:
                boundary = self._started_at + timedelta(
                    minutes=TODO_HABIT_MAX_SESSION_MINUTES
                )
                self.store.record_observed_session(self._category, self._started_at, boundary)
                self._started_at = boundary
                self._last_seen_at = current
            return None

        return self._finish(self._estimated_end(current))

    def stop(self, *, now: datetime | None = None) -> dict | None:
        """Close any active session at shutdown/toggle-off without extending it."""
        current = _local_naive(now or datetime.now())
        return self._finish(self._estimated_end(current))

    def _estimated_end(self, current: datetime) -> datetime:
        if self._last_seen_at is None:
            return current
        interval_end = self._last_seen_at + timedelta(
            seconds=self.poll_interval_seconds
        )
        return min(current, interval_end)

    def _finish(self, ended_at: datetime) -> dict | None:
        if self._started_at is None or self._category is None:
            return None
        result = self.store.record_observed_session(
            self._category,
            self._started_at,
            ended_at,
        )
        self._category = None
        self._started_at = None
        self._last_seen_at = None
        return result
