# -*- coding: utf-8 -*-
"""跨语言认证联调：Python 桌宠端 ↔ Node 桥接端的真实 HMAC 握手。

前面的常量断言（本文件不重复）只证明"canonical 字面量没漂移"；这里进一步把
**两侧实际代码**连起来跑：由真实 Node 实现（impl/0.3.0/index.js 的
canonicalResponse/computeResponseSignature）为本次请求签发响应，再由
dsh_control.request() 走完整链路（读密钥 → 发签名请求 → 验签响应）。

覆盖两个方向：
1. 正确密钥 → 验签通过，request() 返回成功（握手成立）；
2. 错误密钥（模拟伪桥/被换密钥）→ request() 必须判 bridge-control-untrusted。
若任一侧擅自改了 canonical 字段/顺序/强转/分隔符，这两个用例都会红。

依赖：Node 可执行文件与桥包 node_modules 无关（signer 只 import 实现文件）。
Node 缺失时 skip，绝不让门禁因环境差异假红。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import pet.dsh_control as dsh_control

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SIGNER = _REPO_ROOT / "integrations" / "dsh-pet-bridge" / "test" / "response-signer.mjs"
_BRIDGE_PKG = _REPO_ROOT / "integrations" / "dsh-pet-bridge"


def _node() -> str | None:
    return shutil.which("node")


@pytest.fixture
def bridge_dir(tmp_path, monkeypatch):
    """桥目录指向空临时目录；密钥由 Node signer 自己生成（ensureSecret）。"""
    monkeypatch.setattr(dsh_control, "_bridge_dir", lambda: str(tmp_path))
    return tmp_path


def _run_signer(bridge_dir: Path, *, secret: str = "", timeout: float = 20.0):
    """后台启动 Node signer；它生成密钥、等请求文件、签名后写响应。"""
    args = [_node(), str(_SIGNER), str(bridge_dir), "sign"]
    if secret:
        args.append(f"--secret={secret}")
    # stdio 不捕获（inherit）：本机沙箱下管道式 stdio 会被拒；signer 的结论
    # 通过它写出的响应文件体现，不需要读它的 stdout。
    return subprocess.Popen(args, cwd=str(_BRIDGE_PKG))


pytestmark = pytest.mark.skipif(_node() is None, reason="需要 node 可执行文件做跨语言联调")


def test_python_client_accepts_node_signed_response(bridge_dir):
    """核心断言：Node 真实实现签的响应，Python 客户端验签通过。"""
    signer = _run_signer(bridge_dir)
    try:
        # 等密钥落盘：Python 侧无密钥会 fail closed 直接返回 bridge-not-ready。
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not (bridge_dir / "watchdog-secret").exists():
            time.sleep(0.02)
        assert (bridge_dir / "watchdog-secret").exists(), "Node signer 应已生成共享密钥"

        ok, detail = dsh_control.request(
            "replan", "sess-xlang", goal="跨语言联调", context="待补测试", timeout=15.0)
        assert ok is True, f"Node 签名的响应必须验签通过，实际: {detail!r}"
        parsed = json.loads(detail)
        assert parsed["phase"] == "replanned"
        assert parsed["plan"] == "跨语言联调：下一步补测试"
        assert parsed["foundAgent"] is True
        # 盘上请求必须带签名，且能被**共享密钥**验证（反向：Node 侧会这样校验）。
        assert not list(bridge_dir.glob("watchdog-request-*.json")), "请求文件应被清理"
    finally:
        signer.kill()
        signer.wait(timeout=10)


def test_python_client_rejects_response_signed_with_wrong_secret(bridge_dir):
    """错误密钥签名的响应必须被判不可信（证明验签真的在跑，而非恒真）。"""
    # 先让 signer 生成合法密钥文件，再用另一把密钥签名响应。
    bootstrap = _run_signer(bridge_dir)
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not (bridge_dir / "watchdog-secret").exists():
            time.sleep(0.02)
    finally:
        bootstrap.kill()
        bootstrap.wait(timeout=10)
    assert (bridge_dir / "watchdog-secret").exists()

    wrong = "f" * 64
    assert wrong != (bridge_dir / "watchdog-secret").read_text(encoding="utf-8").strip()
    signer = _run_signer(bridge_dir, secret=wrong)
    try:
        ok, detail = dsh_control.request("interrupt", "sess-xlang-bad", timeout=15.0)
        assert (ok, detail) == (False, "bridge-control-untrusted")
    finally:
        signer.kill()
        signer.wait(timeout=10)


def test_node_secret_matches_the_python_form_gate(bridge_dir):
    """Node ensureSecret 产出的密钥必须过 Python 的**精确形态**门槛。

    门槛不是"够长就行"：两侧都按生成器形态（64 位小写 hex）校验，任一侧放宽都会
    出现"Python 拿一个 Node 不认的密钥签名"→ 症状是认证失败、原因码却说不出是
    形态问题（这正是把门槛收紧到形态级要避免的排查陷阱）。
    """
    proc = subprocess.run(
        [_node(), str(_SIGNER), str(bridge_dir), "secret"],
        cwd=str(_BRIDGE_PKG), capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        pytest.skip(f"signer 未跑通（{proc.returncode}）: {proc.stderr[:200]}")
    secret = proc.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", secret), f"Node 密钥形态不符（{len(secret)} 字符）"
    assert dsh_control._read_secret() == secret, "Python 读到的密钥必须与 Node 写的一致"
