"""Measure local todo habit sampling, scheduling, and bounded storage costs."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

from pet import dsh_control
from pet.todo_agent import find_available_todo_slot
from pet.todo_habits import TodoHabitObserver, TodoHabitStore


def _measure(label: str, callback, samples: int) -> None:
    timings = []
    for index in range(samples):
        started = time.perf_counter_ns()
        callback(index)
        timings.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(timings)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    print(
        f"{label}: n={samples}, median_ms={statistics.median(timings):.6f}, "
        f"p95_ms={p95:.6f}, max_ms={max(timings):.6f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1000)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1")

    print(f"python={sys.version.split()[0]} platform={platform.platform()}")
    now = datetime.now().replace(microsecond=0)
    with tempfile.TemporaryDirectory(prefix="todo-habit-benchmark-") as temp_dir:
        store = TodoHabitStore(Path(temp_dir) / "todo_habits.json")
        observer = TodoHabitObserver(store)
        window = {"process": "chrome.exe", "title": "Python course lesson"}
        observer.observe(window, 0, now=now)
        _measure(
            "foreground_metadata_classify_observe_cached_session",
            lambda index: observer.observe(
                window, 0, now=now + timedelta(seconds=index + 1)
            ),
            args.samples,
        )

        todos = []
        for index in range(100):
            day = (now.date() + timedelta(days=index % 8)).isoformat()
            todos.append({
                "kind": "once",
                "date": day,
                "time": f"{8 + index % 14:02d}:00",
                "enabled": True,
                "category": "study" if index % 2 else "work",
                "duration_min": 45,
            })
        hour_counts = [0] * 24
        hour_counts[19] = 32
        weekday_counts = [0] * 7
        weekday_counts[now.weekday()] = 32
        profile = {"categories": {"study": {
            "count": 32,
            "duration_min": 45,
            "hour_counts": hour_counts,
            "weekday_counts": weekday_counts,
        }}}
        schedule_samples = max(1, args.samples // 5)
        _measure(
            "slot_search_100_existing_todos_and_learned_profile",
            lambda _index: find_available_todo_slot(
                todos, now, category="study", habit_profile=profile
            ),
            schedule_samples,
        )

        request = {
            "id": "1" * 32,
            "nonce": "2" * 32,
            "ts": 1_790_000_000_000,
            "operation": "replan",
            "sessionId": "session-1",
            "text": "",
            "goal": "",
            "context": "",
            "provider": "",
            "model": "",
            "timeoutMs": 30_000,
        }
        response = {
            "id": request["id"],
            "nonce": request["nonce"],
            "ok": True,
            "phase": "replanned",
            "error": "",
            "alreadyIdle": False,
            "foundAgent": True,
            "cancelInvoked": False,
            "wasSubagent": False,
            "appliedToRoot": False,
            "rootSessionId": request["sessionId"],
            "plan": "",
        }
        secret = "a1" * 32
        _measure(
            "watchdog_request_and_response_hmac",
            lambda _index: (
                dsh_control._compute_request_signature(secret, request),
                dsh_control._compute_response_signature(secret, response),
            ),
            args.samples,
        )

        if sys.platform == "win32":
            from pet import vision

            _measure(
                "windows_foreground_and_idle_probe",
                lambda _index: (
                    vision.foreground_window_info(),
                    vision.get_system_idle_seconds(),
                ),
                min(args.samples, 100),
            )
        else:
            print("windows_foreground_and_idle_probe: skipped (Windows only)")

        write_started = time.perf_counter()
        for index in range(256):
            started = now + timedelta(days=index, hours=18)
            store.record_observed_session(
                "study", started, started + timedelta(minutes=45)
            )
        write_seconds = time.perf_counter() - write_started
        size = (Path(temp_dir) / "todo_habits.json").stat().st_size
        print(
            f"bounded_store: samples={store.sample_count()}, file_bytes={size}, "
            f"256_atomic_writes_seconds={write_seconds:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
