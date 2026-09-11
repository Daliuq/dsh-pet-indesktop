# -*- coding: utf-8 -*-
"""依赖规格体检 + 桥接 link 自检。

背景（issue：桥接装不上）：profile 的 `package.json` 里可能有**指向本地路径**的依赖
（`link:` / `file:`），路径里往往嵌着会变的东西——打包构建目录名、文件名里的版本号、
本机绝对路径。一旦目录改名或版本升级，spec 就指向不存在的路径，pnpm 解析失败，而报错
只透传 pnpm 的 stderr 尾巴，用户看不出是哪条依赖、该改成什么。

本文件覆盖两层：
1. 依赖规格体检（只诊断不修改）：指名报错 + 给出"疑似应改为"的候选路径；
2. 桥接 link 自检：我们自己写进 profile 的 `link:` 目标不是当前内置插件目录时，
   启动后自动刷新（只刷新已装插件的 profile，绝不在启动时替用户安装）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet import agent_link
from pet.agent_link import DshMonitor


def _profile(tmp_path: Path, name: str = "web", deps: dict | None = None) -> Path:
    profile = tmp_path / "profiles" / name
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(
        json.dumps({"dependencies": deps or {}}, ensure_ascii=False), encoding="utf-8"
    )
    return profile


def _manifest(profile: Path) -> dict:
    data = agent_link._read_manifest(profile)
    assert data is not None
    return data


# ---------------------------------------------------------------- 规格解析

class TestPathSpecTarget:
    def test_accepts_local_path_forms(self, tmp_path):
        link_target = tmp_path / "plugins" / "bridge"
        tar = tmp_path / "artifacts" / "pkg-1.0.0.tgz"
        # 两边都 resolve：macOS 的 /var → /private/var 等符号链接会让裸路径不等
        assert agent_link._path_spec_target(f"link:{link_target}", tmp_path) == link_target.resolve()
        assert agent_link._path_spec_target(f"file:{tar}", tmp_path) == tar.resolve()
        assert agent_link._path_spec_target("./sibling", tmp_path) == (tmp_path / "sibling").resolve()

    def test_decodes_percent_escapes(self, tmp_path):
        spec = "file:C:/Program%20Files/dsh/pkg-1.0.0.tgz"
        got = agent_link._path_spec_target(spec, tmp_path)
        assert got is not None and "Program Files" in str(got)

    def test_ignores_registry_and_remote_specs(self, tmp_path):
        for spec in (
            "^1.2.3", "0.1.14", "workspace:*", "npm:pkg@1.0.0",
            "github:owner/repo#path:/sub", "https://example.com/pkg-1.0.0.tgz",
        ):
            assert agent_link._path_spec_target(spec, tmp_path) is None, spec


# ---------------------------------------------------------------- 缺失诊断

class TestMissingDependencySpecs:
    def test_ok_when_target_exists(self, tmp_path):
        target = tmp_path / "plugins" / "bridge"
        target.mkdir(parents=True)
        profile = _profile(tmp_path, deps={"@dsh-pet/bridge": f"link:{target}"})
        assert agent_link._missing_dependency_specs(profile, _manifest(profile)) == []

    def test_names_dependency_and_missing_path(self, tmp_path):
        missing = tmp_path / "gone" / "ghost-ext"
        profile = _profile(tmp_path, deps={"ghost-ext": f"link:{missing}"})
        findings = agent_link._missing_dependency_specs(profile, _manifest(profile))
        assert len(findings) == 1
        assert "ghost-ext" in findings[0]
        assert "gone" in findings[0]

    def test_suggests_renamed_build_directory(self, tmp_path):
        """打包构建目录改名（dist-onedir/<name>/...）→ 给出新目录下同一相对路径。"""
        new_target = (
            tmp_path / "dist-onedir" / "new-build" / "_internal" / "integrations" / "dsh-pet-bridge"
        )
        new_target.mkdir(parents=True)
        (tmp_path / "dist-onedir" / "old-build" / "_internal" / "integrations").mkdir(parents=True)
        missing = (
            tmp_path / "dist-onedir" / "old-build" / "_internal" / "integrations" / "dsh-pet-bridge"
        )
        profile = _profile(tmp_path, deps={"@dsh-pet/bridge": f"link:{missing}"})

        finding = agent_link._missing_dependency_specs(profile, _manifest(profile))[0]

        assert str(new_target) in finding, finding

    def test_suggests_version_bumped_artifact(self, tmp_path):
        """文件名带版本的 spec（0.12.80 → 0.13.6）→ 指向磁盘上更新的同类文件。"""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        newer = artifacts / "deepseek-ai-dsh-ext-0.13.6.tgz"
        newer.write_text("x", encoding="utf-8")
        profile = _profile(
            tmp_path,
            deps={"@deepseek-ai/dsh-ext": f"file:{artifacts / 'deepseek-ai-dsh-ext-0.12.80.tgz'}"},
        )

        finding = agent_link._missing_dependency_specs(profile, _manifest(profile))[0]

        assert newer.name in finding, finding

    def test_probes_survive_permission_errors(self, tmp_path, monkeypatch):
        """回归（CI ubuntu 实测）：祖先目录无搜索权限时 `Path.is_dir()` 抛 EACCES，
        候选扫描必须退化成「没有建议」，绝不能把 PermissionError 抛进安装失败路径。

        CI 现场：tmp_path 落在 snap private /tmp 下，stat 直接 Permission denied。
        """
        real_is_dir = Path.is_dir

        def fake_is_dir(self):
            if "gone" in str(self):
                raise PermissionError(13, "Permission denied")
            return real_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", fake_is_dir)
        missing = tmp_path / "gone" / "ghost-ext"
        profile = _profile(tmp_path, deps={"ghost-ext": f"link:{missing}"})

        findings = agent_link._missing_dependency_specs(profile, _manifest(profile))

        assert len(findings) == 1
        assert "ghost-ext" in findings[0]
        assert "疑似应改为" not in findings[0], "探测失败时应退化为无建议"

    def test_safe_probes_swallow_permission_errors(self, tmp_path, monkeypatch):
        """候选扫描用的两个探测助手都必须吞掉权限/竞态错误（CI ubuntu 实测 EACCES）。

        注：`Path.is_dir()` 内部也走 stat，所以"只让 mtime 排序失败"没法用打桩区分——
        这两条保证只能各自单元断言（集成面由上面的 permission 用例覆盖）。
        """
        target = tmp_path / "x"
        target.mkdir()

        def boom(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "stat", boom)

        assert agent_link._safe_is_dir(target) is False
        assert agent_link._safe_mtime(target) == 0.0


# ---------------------------------------------------------------- 失败文案

class TestInstallFailureDiagnostics:
    def test_failure_message_names_the_broken_spec(self, tmp_path, monkeypatch):
        plugin = tmp_path / "dsh-pet-bridge"
        plugin.mkdir()
        profile = _profile(
            tmp_path,
            deps={
                "@dsh-pet/bridge": f"link:{plugin}",
                "ghost-ext": "file:W:/nonexistent/ghost-ext-0.12.80.tgz",
            },
        )
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        monkeypatch.setattr(
            agent_link, "_run_pnpm", lambda profile_dir, *args: (1, "ERR_PNPM_ ... 0.12.80")
        )

        ok, message = DshMonitor.install_bridge()

        assert ok is False
        assert profile.name in message
        assert "ghost-ext" in message, message
        assert "0.12.80" in message, message


# ---------------------------------------------------------------- link 自检

class TestBridgeLinkStaleness:
    def _setup(self, tmp_path, monkeypatch, deps: dict):
        plugin = tmp_path / "current-build" / "integrations" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        _profile(tmp_path, name="web", deps=deps)
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        return plugin

    def test_fresh_link_is_not_stale(self, tmp_path, monkeypatch):
        plugin = self._setup(tmp_path, monkeypatch, {})
        (tmp_path / "profiles" / "web" / "package.json").write_text(
            json.dumps({"dependencies": {agent_link.DSH_PLUGIN_NAME: f"link:{plugin}"}}),
            encoding="utf-8",
        )
        assert DshMonitor.bridge_link_stale() == []

    def test_link_to_other_build_is_stale(self, tmp_path, monkeypatch):
        other = tmp_path / "old-build"
        other.mkdir()
        self._setup(
            tmp_path, monkeypatch, {agent_link.DSH_PLUGIN_NAME: f"link:{other}"}
        )
        stale = DshMonitor.bridge_link_stale()
        assert [name for name, _spec in stale] == ["web"]

    def test_missing_link_target_is_stale(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path, monkeypatch, {agent_link.DSH_PLUGIN_NAME: "link:W:/gone/bridge"}
        )
        assert [name for name, _spec in DshMonitor.bridge_link_stale()] == ["web"]

    def test_profile_without_plugin_is_ignored(self, tmp_path, monkeypatch):
        """没装插件 ≠ 陈旧：启动自检绝不替用户安装。"""
        self._setup(tmp_path, monkeypatch, {"dsh-other": "^1.0.0"})
        assert DshMonitor.bridge_link_stale() == []


class TestRefreshStaleBridgeLinks:
    def test_refreshes_only_stale_profiles(self, tmp_path, monkeypatch):
        plugin = tmp_path / "current-build" / "integrations" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        other = tmp_path / "old-build"
        other.mkdir()
        _profile(tmp_path, name="web", deps={agent_link.DSH_PLUGIN_NAME: f"link:{other}"})
        _profile(tmp_path, name="headless", deps={agent_link.DSH_PLUGIN_NAME: f"link:{plugin}"})
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        calls: list[tuple[str, tuple]] = []

        def fake_run(profile_dir, *args):
            calls.append((profile_dir.name, args))
            return 0, ""

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        refreshed = DshMonitor.refresh_stale_bridge_links()

        assert refreshed == ["web"]
        assert [name for name, _args in calls] == ["web"]
        assert calls[0][1] == ("add", str(plugin))

    def test_never_installs_absent_plugin(self, tmp_path, monkeypatch):
        plugin = tmp_path / "current-build" / "integrations" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        _profile(tmp_path, name="web", deps={"dsh-other": "^1.0.0"})
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        calls: list[str] = []
        monkeypatch.setattr(
            agent_link, "_run_pnpm", lambda profile_dir, *args: (calls.append(profile_dir.name), (0, ""))[1]
        )

        assert DshMonitor.refresh_stale_bridge_links() == []
        assert calls == []


class TestScheduling:
    def test_check_runs_at_most_once_per_monitor(self, tmp_path, monkeypatch):
        monitor = DshMonitor("dsh", tmp_path)
        seen: list[int] = []
        monkeypatch.setattr(
            DshMonitor,
            "refresh_stale_bridge_links",
            classmethod(lambda cls: (seen.append(1), [])[1]),
        )

        monitor.schedule_link_refresh_check(spawn=lambda fn: fn())
        monitor.schedule_link_refresh_check(spawn=lambda fn: fn())

        assert seen == [1], "同一次启动只自检一次"

    def test_apply_config_schedules_check_when_dsh_enabled(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.config import Config
        from pet.agent_link import AgentLinkManager

        QApplication.instance() or QApplication([])
        scheduled: list[str] = []
        monkeypatch.setattr(
            DshMonitor,
            "schedule_link_refresh_check",
            lambda self, spawn=None: scheduled.append(self.agent_key),
        )
        cfg = Config(base=tmp_path)
        agent_cfg = dict(cfg.get("agent_link", {}))
        agent_cfg["dsh"] = True
        cfg.set("agent_link", agent_cfg)

        class Win:
            def show_bubble(self, *args, **kwargs):
                pass

            def isVisible(self):
                return True

        manager = AgentLinkManager(Win(), cfg)
        try:
            assert "dsh" in scheduled
        finally:
            manager.shutdown()

    def test_apply_config_skips_check_when_dsh_disabled(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.config import Config
        from pet.agent_link import AgentLinkManager

        QApplication.instance() or QApplication([])
        scheduled: list[str] = []
        monkeypatch.setattr(
            DshMonitor,
            "schedule_link_refresh_check",
            lambda self, spawn=None: scheduled.append(self.agent_key),
        )
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, *args, **kwargs):
                pass

            def isVisible(self):
                return True

        manager = AgentLinkManager(Win(), cfg)
        try:
            assert scheduled == []
        finally:
            manager.shutdown()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
