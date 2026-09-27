"""Measure local todo-agent queue admission without making network requests."""
from __future__ import annotations

import argparse
import statistics
import threading
import time
import tracemalloc
from types import SimpleNamespace

import psutil

import pet.chat.providers as chat_providers
from pet.todo_agent import TodoAgent


class _ProbeProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.condition = threading.Condition()

    def stream(self, *_args, **_kwargs):
        with self.condition:
            self.calls += 1
            self.condition.notify_all()
        return iter(())


class _ProbeConfig:
    def chat_settings(self):
        provider = SimpleNamespace(
            name="Probe",
            model="no-network",
            timeout=60.0,
            max_tokens=2048,
            api_key="",
        )
        return SimpleNamespace(active_config=provider)

    def resolve_api_key(self, _provider):
        return "probe-only"


class _AlreadyRunningWorker:
    def is_alive(self) -> bool:
        return True


def _thread_cpu_seconds(thread_id: int) -> float:
    thread = next(
        thread for thread in psutil.Process().threads() if thread.id == thread_id
    )
    return thread.user_time + thread.system_time


def benchmark(samples: int, idle_seconds: float) -> None:
    provider = _ProbeProvider()
    chat_providers.OpenAICompatibleProvider = lambda: provider
    agent = TodoAgent(_ProbeConfig(), lambda *_args: None)
    latencies_ms = []

    for sample in range(samples):
        with provider.condition:
            expected_calls = provider.calls + 1
        started = time.perf_counter_ns()
        accepted = agent.submit(str(sample), "明天上午十点提交周报")
        latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        if not accepted:
            raise RuntimeError(f"request {sample} was not accepted")
        with provider.condition:
            consumed = provider.condition.wait_for(
                lambda: provider.calls >= expected_calls, timeout=2.0
            )
        if not consumed:
            raise TimeoutError(f"worker did not consume request {sample}")

    worker = agent._thread
    if worker is None or worker.native_id is None:
        raise RuntimeError("todo-agent worker did not start")
    cpu_before = _thread_cpu_seconds(worker.native_id)
    time.sleep(max(0.0, idle_seconds))
    cpu_after = _thread_cpu_seconds(worker.native_id)
    agent.shutdown()

    ordered = sorted(latencies_ms)
    p95_index = max(0, int(0.95 * len(ordered)) - 1)
    print(
        f"submit samples={samples} median_ms={statistics.median(ordered):.4f} "
        f"p95_ms={ordered[p95_index]:.4f} max_ms={max(ordered):.4f}"
    )
    print(
        f"idle seconds={idle_seconds:.1f} thread_cpu_delta_seconds="
        f"{cpu_after - cpu_before:.4f} network_calls=0"
    )

    queued_agent = TodoAgent(_ProbeConfig(), lambda *_args: None)
    queued_agent._thread = _AlreadyRunningWorker()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    queued = sum(
        queued_agent.submit(str(index), "x" * 8000)
        for index in range(queued_agent._queue.maxsize)
    )
    after = tracemalloc.take_snapshot()
    heap_delta = sum(
        stat.size_diff for stat in after.compare_to(before, "filename")
    )
    tracemalloc.stop()
    print(
        f"queue accepted={queued} queued_chars={queued * 8000} "
        f"heap_delta_bytes={heap_delta} capacity={queued_agent._queue.maxsize}"
    )
    queued_agent._closed = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--idle-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    benchmark(args.samples, args.idle_seconds)


if __name__ == "__main__":
    main()
