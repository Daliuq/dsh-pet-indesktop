# -*- coding: utf-8 -*-
"""Regression coverage for the Pet side of the DSH bridge contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pet.agent_link import DshMonitor
from pet.agent_link import AgentLinkManager
from pet.bridge_contract import (
    BRIDGE_CAPABILITIES,
    BRIDGE_EVENT_INVENTORY,
    BRIDGE_PROTOCOL_VERSION,
    BRIDGE_VERSION,
)
from pet.config import Config


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _record(event: str = "AgentStatus", **extra) -> dict:
    return {
        "event": event,
        "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
        "bridgeVersion": BRIDGE_VERSION,
        **extra,
    }


def _append(path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_incompatible_dsh_record_is_stopped_before_all_consumers(tmp_path):
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    states = []
    activities = []
    normalized = []
    incompatible = []
    monitor.raw_record.connect(lambda *args: raw.append(args))
    monitor.state_changed.connect(lambda *args: states.append(args))
    monitor.activity.connect(lambda *args: activities.append(args))
    monitor.normalized_event.connect(lambda event: normalized.append(event))
    monitor.bridge_incompatible.connect(lambda *args: incompatible.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    _append(monitor.events_file, _record(bridgeProtocolVersion=99, state="working", tool="bash"))
    monitor._poll()
    assert not raw
    assert not states
    assert not activities
    assert not normalized
    assert len(incompatible) == 1
    assert incompatible[0][1]["receivedProtocolVersion"] == 99
    monitor.stop()


def test_valid_dsh_record_keeps_existing_state_activity_and_raw_paths(tmp_path):
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    states = []
    activities = []
    monitor.raw_record.connect(lambda *args: raw.append(args))
    monitor.state_changed.connect(lambda *args: states.append(args))
    monitor.activity.connect(lambda *args: activities.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    _append(monitor.events_file, _record(state="working", tool="bash"))
    monitor._poll()
    assert raw and raw[0][1]["event"] == "AgentStatus"
    assert states == [("dsh", "working")]
    assert activities == [("dsh", "bash")]
    monitor.stop()


def test_every_declared_bridge_event_is_consumed_without_unknown_fallback(tmp_path):
    """Producer inventory entries all reach the Pet raw consumer as known events."""
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    unknown = []
    monitor.raw_record.connect(lambda _agent, record: raw.append(record["event"]))
    monitor.unknown_bridge_event.connect(lambda *args: unknown.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    for event in sorted(BRIDGE_EVENT_INVENTORY):
        extra = {}
        if event == "bridge/hello":
            extra = {
                "capabilities": sorted(BRIDGE_CAPABILITIES),
                "emittedEvents": sorted(BRIDGE_EVENT_INVENTORY),
            }
        _append(monitor.events_file, _record(event, **extra))
    monitor._poll()
    assert set(raw) == set(BRIDGE_EVENT_INVENTORY)
    assert unknown == []
    monitor.stop()


def test_pet_control_audit_records_are_not_mistaken_for_bridge_output(tmp_path):
    """dsh*.jsonl includes Pet control logs, which have a separate local contract."""
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    raw = []
    incompatible = []
    unknown = []
    monitor.raw_record.connect(lambda _agent, record: raw.append(record["event"]))
    monitor.bridge_incompatible.connect(lambda *args: incompatible.append(args))
    monitor.unknown_bridge_event.connect(lambda *args: unknown.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    for event in ("pet/control-clicked", "pet/control-queued"):
        _append(monitor.events_file, {"agent": "pet", "event": event})
    monitor._poll()
    assert raw == ["pet/control-clicked", "pet/control-queued"]
    assert incompatible == []
    assert unknown == []
    monitor.stop()


def test_hello_requires_the_exact_event_inventory(tmp_path):
    _qapp()
    monitor = DshMonitor("dsh", tmp_path / "config")
    incompatible = []
    raw = []
    monitor.bridge_incompatible.connect(lambda *args: incompatible.append(args))
    monitor.raw_record.connect(lambda *args: raw.append(args))
    monitor.events_file.parent.mkdir(parents=True, exist_ok=True)
    monitor.events_file.touch()
    monitor._poll()
    _append(monitor.events_file, _record("bridge/hello", emittedEvents=["AgentStatus"]))
    monitor._poll()
    assert len(incompatible) == 1
    assert not raw
    _append(
        monitor.events_file,
        _record(
            "bridge/hello",
            capabilities=sorted(BRIDGE_CAPABILITIES),
            emittedEvents=sorted(BRIDGE_EVENT_INVENTORY),
        ),
    )
    monitor._poll()
    assert len(incompatible) == 1
    assert raw and raw[0][1]["event"] == "bridge/hello"
    monitor.stop()


def _exported_js_strings(source: str, name: str) -> set[str]:
    match = re.search(
        rf"export const {name} = Object\.freeze\(\[(.*?)\]\);", source, re.S,
    )
    assert match is not None, f"missing JavaScript export: {name}"
    return set(re.findall(r'\"([^\"]+)\"', match.group(1)))


def test_bundled_bridge_and_pet_contracts_are_identical():
    """The two runtimes may not add an event/capability/version independently."""
    root = Path(__file__).resolve().parents[1]
    bridge_root = root / "integrations" / "dsh-pet-bridge"
    source = (bridge_root / "index.js").read_text(encoding="utf-8")
    package = json.loads((bridge_root / "package.json").read_text(encoding="utf-8"))

    assert _exported_js_strings(source, "BRIDGE_EVENT_INVENTORY") == set(
        BRIDGE_EVENT_INVENTORY
    )
    assert _exported_js_strings(source, "BRIDGE_CAPABILITIES") == set(
        BRIDGE_CAPABILITIES
    )
    assert package["version"] == BRIDGE_VERSION
    protocol = re.search(r"export const BRIDGE_PROTOCOL_VERSION = (\d+);", source)
    assert protocol is not None
    assert int(protocol.group(1)) == BRIDGE_PROTOCOL_VERSION


def test_hello_requires_the_exact_capability_inventory():
    record = _record(
        "bridge/hello",
        capabilities=["agent-status"],
        emittedEvents=sorted(BRIDGE_EVENT_INVENTORY),
    )
    from pet.bridge_contract import validate_bridge_record

    result = validate_bridge_record(record)
    assert not result
    assert result.reason == "bridge capabilities do not match Pet"


def test_incompatible_bridge_bubble_is_actionable_and_rate_limited(tmp_path):
    _qapp()
    bubbles = []
    clock = [100.0]

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    manager = AgentLinkManager(
        DummyWindow(), Config(base=tmp_path), clock=lambda: clock[0], min_interval=0,
    )
    details = {
        "reason": "unsupported bridge protocol version",
        "receivedProtocolVersion": 99,
        "receivedBridgeVersion": "9.9.9",
        "expectedProtocolVersion": 1,
        "expectedBridgeVersion": BRIDGE_VERSION,
    }
    manager._on_bridge_incompatible("dsh", details)
    manager._on_bridge_incompatible("dsh", details)
    assert len(bubbles) == 1
    assert all(value in bubbles[0] for value in ("99", "1", "9.9.9", BRIDGE_VERSION))
    assert "更新" in bubbles[0] and "重装" in bubbles[0]
    clock[0] += manager._UNKNOWN_BRIDGE_REMIND_COOLDOWN_S + 1
    manager._on_bridge_incompatible("dsh", details)
    assert len(bubbles) == 2
    manager.shutdown()


def test_incompatible_bridge_warning_ignores_report_probability(tmp_path):
    """Protocol incompatibility is required health feedback, not event sampling."""
    _qapp()
    bubbles = []

    class DummyWindow:
        def isVisible(self):
            return True

        def show_bubble(self, text, duration_ms=3000):
            bubbles.append(text)

    cfg = Config(base=tmp_path)
    agent_cfg = dict(cfg.get("agent_link", {}))
    agent_cfg["report_gates"] = {
        **agent_cfg.get("report_gates", {}),
        "bridge": 0.0,
    }
    cfg.set("agent_link", agent_cfg)
    manager = AgentLinkManager(DummyWindow(), cfg, min_interval=0)
    manager._on_bridge_incompatible(
        "dsh", {"reason": "missing bridge protocol version"},
    )
    assert len(bubbles) == 1
    manager.shutdown()
