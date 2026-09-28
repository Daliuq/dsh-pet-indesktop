# -*- coding: utf-8 -*-
"""Client for the in-process DSH bridge watchdog control queue."""
from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import tempfile
import time
import uuid

TIMEOUT_S = 30.0
POLL_S = 0.08
_SECRET_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_FIELDS = (
    ("id", "str"), ("nonce", "str"), ("ts", "int"),
    ("operation", "str"), ("sessionId", "str"), ("text", "str"),
    ("goal", "str"), ("context", "str"), ("provider", "str"),
    ("model", "str"), ("timeoutMs", "int"),
)
_RESPONSE_FIELDS = (
    ("id", "str"), ("nonce", "str"), ("ok", "bool"),
    ("phase", "str"), ("error", "str"), ("alreadyIdle", "bool"),
    ("foundAgent", "bool"), ("cancelInvoked", "bool"),
    ("wasSubagent", "bool"), ("appliedToRoot", "bool"),
    ("rootSessionId", "str"), ("plan", "str"),
)


def _bridge_path(name: str) -> str:
    return os.path.join(_bridge_dir(), name)


def _read_secret() -> str | None:
    """Read the bridge's 256-bit hex key. Invalid or missing keys fail closed."""
    try:
        with open(_bridge_path("watchdog-secret"), "r", encoding="ascii") as handle:
            secret = handle.read().strip()
    except (OSError, UnicodeError):
        return None
    return secret if _SECRET_RE.fullmatch(secret) else None


def _canonical_fields(payload: dict, fields: tuple[tuple[str, str], ...]) -> str:
    values = []
    for name, kind in fields:
        value = payload.get(name)
        if kind == "bool":
            values.append(value is True)
        elif kind == "int":
            try:
                values.append(int(value))
            except (TypeError, ValueError, OverflowError):
                values.append(0)
        else:
            values.append(value if isinstance(value, str) else "")
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _compute_signature(secret: str, payload: dict, fields: tuple[tuple[str, str], ...]) -> str:
    key = bytes.fromhex(secret)
    message = _canonical_fields(payload, fields).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _compute_request_signature(secret: str, payload: dict) -> str:
    return _compute_signature(secret, payload, _REQUEST_FIELDS)


def _compute_response_signature(secret: str, payload: dict) -> str:
    return _compute_signature(secret, payload, _RESPONSE_FIELDS)


def _bridge_dir() -> str:
    override = os.environ.get("DSH_PET_BRIDGE_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, "dsh-pet-bridge")
    if os.sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "dsh-pet-bridge")
    return os.path.join(os.path.expanduser("~"), ".config", "dsh-pet-bridge")


def _atomic_json(path: str, value: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="watchdog-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _log_event(base_dir: str, event: str, **fields) -> None:
    # 形参名不能叫 directory：调用方把 directory 作为 JSON 字段写进事件，
    # 同名会造成 "got multiple values for argument 'directory'"（PR57 遗留）。
    try:
        path = os.path.join(base_dir, f"dsh-pet-control-{os.getpid()}.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), "agent": "pet", "event": event, **fields}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def request(operation: str, session_id: str, text: str = "", ports: list[int] | None = None,
            *, goal: str = "", context: str = "", provider: str = "", model: str = "",
            timeout: float = TIMEOUT_S, alert_id: str = "", cancel=None) -> tuple[bool, str]:
    del ports  # retained for compatibility; bridge discovery is file based
    if not session_id or session_id.startswith("turn:"):
        return False, "missing-session-id"
    if operation not in {"interrupt", "replan"}:
        return False, "unsupported-operation"
    secret = _read_secret()
    if secret is None:
        return False, "bridge-not-ready"
    request_id = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    directory = _bridge_dir()
    request_path = os.path.join(directory, f"watchdog-request-{request_id}.json")
    response_path = os.path.join(directory, f"watchdog-response-{request_id}.json")
    payload = {
        "id": request_id,
        "nonce": nonce,
        "ts": int(time.time() * 1000),
        "operation": operation,
        "sessionId": session_id,
        "text": text[:12000],
        "goal": goal[:2000],
        "context": context[:12000],
        "provider": provider[:120],
        "model": model[:240],
        "timeoutMs": max(1000, int(float(timeout) * 1000)),
    }
    payload["sig"] = _compute_request_signature(secret, payload)
    try:
        _atomic_json(request_path, payload)
        _log_event(directory, "pet/control-clicked", requestId=request_id, sessionId=session_id,
                   operation=operation, alertId=alert_id)
        _log_event(directory, "pet/control-queued", requestId=request_id, sessionId=session_id,
                   operation=operation, requestPath=request_path, directory=directory, written=True)
    except Exception as exc:
        _log_event(directory, "pet/control-queued", requestId=request_id, sessionId=session_id,
                   operation=operation, requestPath=request_path, directory=directory, written=False,
                   error=str(exc))
        return False, f"queue-write-failed:{exc}"

    deadline = time.monotonic() + max(1.0, float(timeout))
    try:
        while time.monotonic() < deadline:
            # 可取消等待：pet 侧 shutdown 时置位 cancel，30s 轮询立刻退出，
            # 保证 worker 能在 shutdown 的 join 预算内结束。
            if cancel is not None and cancel.is_set():
                return False, "cancelled"
            try:
                with open(response_path, "r", encoding="utf-8") as handle:
                    result = json.load(handle)
                if not isinstance(result, dict):
                    return False, "bridge-control-untrusted"
                signature = str(result.get("sig") or "")
                expected = _compute_response_signature(secret, result)
                if (
                    result.get("id") != request_id
                    or result.get("nonce") != nonce
                    or not hmac.compare_digest(signature, expected)
                ):
                    return False, "bridge-control-untrusted"
                if result.get("ok") is True:
                    return True, json.dumps(result, ensure_ascii=False)
                return False, str(result.get("error") or "bridge-control-rejected")
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                if cancel is not None:
                    if cancel.wait(POLL_S):
                        return False, "cancelled"
                else:
                    time.sleep(POLL_S)
    finally:
        for path in (request_path, response_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    return False, "bridge-control-timeout"
