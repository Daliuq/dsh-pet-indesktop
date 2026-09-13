# -*- coding: utf-8 -*-
"""点击音效播放、缓存与包解析测试。"""
from __future__ import annotations

import random
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QTimer

from pet import click_sound
from pet import window as window_mod


class FakeQtAudio:
    def __init__(self) -> None:
        self.volume = 1.0

    def setVolume(self, v: float) -> None:
        self.volume = v


class FakeQtPlayer:
    def __init__(self) -> None:
        self.stopped = False
        self.source = None
        self.played = False
        self.audio_output = None

    def stop(self) -> None:
        self.stopped = True

    def setSource(self, qurl) -> None:
        self.source = qurl

    def play(self) -> None:
        self.played = True

    def setAudioOutput(self, audio) -> None:
        self.audio_output = audio


class FakeSignal:
    def connect(self, callback):
        self.callback = callback


class FakeQtEffect:
    instances = []

    def __init__(self):
        self.source = None
        self.volumes = []
        self.play_count = 0
        self.stop_count = 0
        self.loop_counts = []
        self.__class__.instances.append(self)

    def setSource(self, source):
        self.source = source

    def setVolume(self, volume):
        self.volumes.append(volume)

    def stop(self):
        self.stop_count += 1

    def setLoopCount(self, count):
        self.loop_counts.append(count)

    def play(self):
        self.play_count += 1


class FakeQtDecoder:
    def __init__(self):
        self.bufferReady = FakeSignal()
        self.finished = FakeSignal()
        self.error = FakeSignal()

    def setSource(self, source):
        self.source = source

    def start(self):
        self.error.callback("decode failed")


def _fake_classes():
    return (FakeQtDecoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect)


def _make_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"not-a-real-audio-file")
    return path


class FakeMonotonic:
    """可控 monotonic 时钟（替换 `click_sound.time`）。

    为什么必须可控：闲置判定比的是 `now - 上次播放`，而 CI runner 是**刚开机**的
    （`time.monotonic()` 绝对值可能只有几十秒）。用例若拿真实时钟做"把时刻往回
    拨 阈值+1 秒"的算术，在 uptime 小于阈值时会算出负数，"真实闲置"就退化成
    "从未播放过"的边界值 → 只在部分机器上红（macOS runner 实测红、Windows runner
    绿）。AGENTS.md 也明确禁止用 monotonic 绝对值做回拨算术：让用例直接掌控时间，
    判据里就只剩时间差。
    """

    def __init__(self, start: float = 10_000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        # 产品在解码等待里也用 time.sleep（真实休眠，不影响判定）。
        time.sleep(seconds)


def _install_clock(monkeypatch, start: float = 10_000.0) -> FakeMonotonic:
    clock = FakeMonotonic(start)
    monkeypatch.setattr(click_sound, "time", clock)
    return clock


def test_wav_restarts_qsound_effect_on_each_click(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    effect = FakeQtEffect()
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)

    path_wav = _make_file(tmp_path, "click.wav")
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert len(FakeQtEffect.instances) >= 1
    assert FakeQtEffect.instances[-1].play_count == 2
    assert FakeQtEffect.instances[-1].volumes == [0.5, 0.5]
    # 回归：同一 QSoundEffect 实例每次播放前先 stop，防止部分 FFmpeg
    # 后端第二次 play 不重启导致“后续点击/试听无声”。
    assert FakeQtEffect.instances[-1].stop_count >= 2
    assert FakeQtEffect.instances[-1].loop_counts == [1, 1]


def test_idle_effect_is_rebuilt_after_long_inactivity(monkeypatch, tmp_path):
    """长时间放置后点击音效消失的回归：QSoundEffect 实例闲置超阈值要重建。

    Windows 上 QtMultimedia 会把长期不播的音频会话休眠/回收，同一实例
    再 play() 不报错但无声。闲置（_EFFECT_IDLE_REBUILD_S）后应丢弃缓存
    实例、走新建重新加载路径（自愈）；未闲置则复用同一实例（保持预热
    低延迟开局）。"""
    _install_clock(monkeypatch)
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_effect_last_play", {})
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(
        click_sound._pool, "_EFFECT_IDLE_REBUILD_S", 300.0,
    )
    path_wav = _make_file(tmp_path, "click.wav")

    before = len(FakeQtEffect.instances)
    assert click_sound.play_sound(path_wav, volume=1.0) is True
    after_first = len(FakeQtEffect.instances)
    assert after_first == before + 1, "首次播放应新建一个 QSoundEffect 实例"

    # 未闲置：连续播放复用同一实例（不新建）
    assert click_sound.play_sound(path_wav, volume=1.0) is True
    assert len(FakeQtEffect.instances) == after_first, "未闲置不得重建实例"

    # 只把**该路径**证的上次播放时刻回拨到阈值之外（全局时钟仍是新鲜的）：
    # 本用例要验证的是每路径判定，不能顺手让整池闲置重建也插一脚。
    key = str(path_wav.resolve())
    click_sound._pool._effect_last_play[key] -= click_sound._pool._EFFECT_IDLE_REBUILD_S + 1
    assert click_sound.play_sound(path_wav, volume=1.0) is True
    assert len(FakeQtEffect.instances) == after_first + 1, \
        "闲置超阈值后应重建 QSoundEffect 实例（自愈无声）"
    assert FakeQtEffect.instances[-1].play_count == 1, "重建实例应重新加载/播放"


def test_idle_reset_rebuilds_the_player_pool_too(monkeypatch, tmp_path):
    """长时间放置后「全部音效消失」：闲置自愈必须同时覆盖播放器池。

    压缩音频（mp3/ogg）解码缓存未就绪时走 QMediaPlayer 池 + 各自的
    QAudioOutput；Windows 上闲置久了这批对象的音频会话同样被回收/休眠，
    只重建 QSoundEffect 的话用户看到的仍是"点哪个都没声"。"""
    clock = _install_clock(monkeypatch)
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound._pool, "_qt_player", None)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", None)
    monkeypatch.setattr(click_sound._pool, "_last_play_at", clock.now)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: tmp_path / "cache")
    path = _make_file(tmp_path, "click.mp3")
    # 计数器是**进程级**的（单例池跨用例共享）：只能按基线增量断言。写成
    # `== 1` 会让结果取决于本用例之前有没有别的用例触发过闲置重建。
    rebuilds_before = click_sound._pool._idle_rebuild_count

    assert click_sound.play_click_sound(path) is True
    pool_before = [player for player, _audio in click_sound._pool._qt_player_pool]
    assert pool_before, "首次播放必须建池（否则本用例空转）"

    # 未闲置：复用同一批播放器对象（不重建，保持预热收益）
    assert click_sound.play_click_sound(path) is True
    assert [p for p, _a in click_sound._pool._qt_player_pool] == pool_before, \
        "未闲置不得重建播放器池"

    # 闲置超阈值：**时间前进**阈值+1 秒（不是把时刻往回拨成负数）→ 整池重建
    clock.now += click_sound._pool._EFFECT_IDLE_REBUILD_S + 1
    assert click_sound.play_click_sound(path) is True
    pool_after = [player for player, _audio in click_sound._pool._qt_player_pool]
    assert pool_after, "闲置后仍须有可用播放器"
    assert all(p not in pool_before for p in pool_after), \
        "闲置超阈值后播放器池必须整体重建（旧对象可能已被系统休眠）"
    assert click_sound._pool._idle_rebuild_count == rebuilds_before + 1, \
        "本用例应恰好触发一次闲置重建"


def test_idle_reset_fires_even_on_a_just_booted_clock(monkeypatch, tmp_path):
    """回归（macOS runner 实测红）：刚开机的机器上必须还能判定"闲置"。

    `time.monotonic()` 的原点/量级取决于机器与开机时长——CI runner 刚开机时只有
    几十秒。此前"从未播放过"用 `_last_play_at <= 0.0` 表达，于是任何"把时刻往回
    拨"的算术在小 uptime 上都会落进那个分支：真实的闲置被当成"还没播放过"，
    整池不重建（Windows runner uptime 大 → 绿；macOS runner 刚开机 → 红）。
    现在判据只看时间差，且 None 才是"尚未播放"的语义。
    """
    clock = _install_clock(monkeypatch, start=5.0)  # 刚开机 5 秒
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_effect_last_play", {})
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    path_wav = _make_file(tmp_path, "click.wav")
    rebuilds_before = click_sound._pool._idle_rebuild_count

    assert click_sound.play_sound(path_wav, volume=1.0) is True
    # 开机 5 秒的机器上"回拨"必然越界成负数——判据仍必须只看时间差
    click_sound._pool._last_play_at = clock.now - (
        click_sound._pool._EFFECT_IDLE_REBUILD_S + 1
    )
    assert click_sound._pool._last_play_at < 0.0, "本用例要的就是越界成负数的时刻"
    assert click_sound.play_sound(path_wav, volume=1.0) is True
    assert click_sound._pool._idle_rebuild_count == rebuilds_before + 1, \
        "负数时刻（真实闲置）必须照样触发整池重建"


def test_first_play_after_warmup_reuses_the_warmed_effect(monkeypatch, tmp_path):
    """预热后首次点击必须复用预热实例。

    回归：闲置判定此前用 `now - self._effect_last_play.get(key, 0.0)`——缺条目
    等于"自开机起一直闲置"（monotonic 是开机秒数），于是预热刚建好的
    QSoundEffect 会在第一次点击时被当场丢弃重建，预热白做、首次点击重新背上
    加载延迟。现在创建时即登记时刻，缺条目按"不闲置"处理。"""
    clock = _install_clock(monkeypatch)
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_effect_last_play", {})
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    # 钉住"整池闲置"时钟：本用例只验证**每路径**判定，不允许全局闲置重建插手。
    monkeypatch.setattr(click_sound._pool, "_last_play_at", clock.now)
    path_wav = _make_file(tmp_path, "click.wav")

    click_sound._pool.effect_for(path_wav)  # 预热：创建并登记
    before = len(FakeQtEffect.instances)
    assert click_sound.play_sound(path_wav, volume=1.0) is True
    assert len(FakeQtEffect.instances) == before, \
        "预热实例不得在首次播放时被当成闲置而重建"


def test_idle_reset_clears_effect_cache_and_shared_audio(monkeypatch, tmp_path):
    """闲置重建要连 effect 缓存与共享 QAudioOutput/QMediaPlayer 一起丢掉。"""
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    clock = _install_clock(monkeypatch)
    monkeypatch.setattr(click_sound._pool, "_last_play_at", clock.now)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    path_wav = _make_file(tmp_path, "click.wav")
    assert click_sound.play_sound(path_wav, volume=1.0) is True
    assert click_sound._pool._qt_effects, "首次播放后应有 effect 缓存"

    # 时间前进超过阈值 → 整池重建（含共享播放器/音频输出）
    clock.now += click_sound._pool._EFFECT_IDLE_REBUILD_S + 1
    shared_player, shared_audio = FakeQtAudio(), FakeQtAudio()
    monkeypatch.setattr(click_sound._pool, "_qt_player", shared_player)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", shared_audio)
    assert click_sound.play_sound(path_wav, volume=1.0) is True
    assert click_sound._pool._qt_player is not shared_player, \
        "共享 QMediaPlayer 必须在闲置重建时丢弃（按需重建）"
    assert click_sound._pool._qt_audio is not shared_audio
    assert len(click_sound._pool._qt_effects) >= 1, "重建后仍要能播出（新 effect 已就绪）"


def test_mp3_decode_failure_falls_back_to_player_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: tmp_path / "cache")

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is True
    assert len(click_sound._pool._qt_player_pool) == 4
    assert click_sound._pool._qt_player_pool[0][0].played is True


def test_nonwav_qt_unavailable_on_windows_skips_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", lambda: None)

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is False


def test_mp3_second_click_uses_decoded_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: cache_dir)
    path = _make_file(tmp_path, "click.mp3")

    class Decoder(FakeQtDecoder):
        def start(self):
            class Format:
                def sampleFormat(self): return 2
                def channelCount(self): return 1
                def sampleRate(self): return 8000
            class Buffer:
                def format(self): return Format()
                def data(self): return b"pcm"
            self.bufferAvailable = lambda: bool(getattr(self, "pending", True))
            self.read = lambda: (setattr(self, "pending", False) or Buffer())
            self.bufferReady.callback()
            self.finished.callback()

    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", lambda: (Decoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect))
    assert click_sound.play_sound(path) is True
    assert list(cache_dir.glob("*.wav"))
    assert click_sound.play_sound(path) is True
    assert FakeQtEffect.instances[-1].play_count == 1


def test_warm_player_pool_precreates_qt_players(monkeypatch):
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player", None)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", None)

    click_sound._warm_player_pool()

    assert len(click_sound._pool._qt_player_pool) == 4


def test_warm_click_sound_effects_precreates_wav_effect_and_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound._pool, "qt_available", lambda: True)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player", None)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", None)

    wav = _make_file(tmp_path, "click.wav")
    pack = {"kind": "file", "id": "custom", "path": str(wav)}
    click_sound.warm_click_sound_effects(pack, data_dir=tmp_path)

    assert str(wav.resolve()) in click_sound._pool._qt_effects
    assert len(click_sound._pool._qt_player_pool) == 4


def test_resolve_click_sound_candidates_and_choose(tmp_path):
    # 1. file mode
    f = _make_file(tmp_path, "test.mp3")
    pack_file = {"kind": "file", "id": "custom", "path": str(f)}
    candidates = click_sound.resolve_click_sound_candidates(pack_file)
    assert candidates == [f]
    assert click_sound.choose_sound(candidates) == f

    # 2. folder mode
    folder = tmp_path / "sounds_folder"
    folder.mkdir()
    f1 = _make_file(folder, "1.wav")
    f2 = _make_file(folder, "2.mp3")
    _make_file(folder, "ignored.txt")
    pack_folder = {"kind": "folder", "id": "custom", "path": str(folder)}
    candidates_folder = click_sound.resolve_click_sound_candidates(pack_folder)
    assert candidates_folder == [f1, f2]
    # deterministic choose via seeded rng
    rng = random.Random(42)
    chosen = click_sound.choose_sound(candidates_folder, rng=rng)
    assert chosen in {f1, f2}

    # 3. empty list
    assert click_sound.choose_sound([]) is None

    # 4. builtin duck pack
    pack_duck = {"kind": "builtin", "id": "duck", "path": ""}
    duck_candidates = click_sound.resolve_click_sound_candidates(pack_duck)
    assert len(duck_candidates) >= 2
    assert any(c.name == "Ya1.mp3" for c in duck_candidates)
    assert any(c.name == "Ya2.mp3" for c in duck_candidates)


def test_window_play_click_sound_uses_pack(monkeypatch, tmp_path):
    custom = _make_file(tmp_path, "custom.wav")
    cfg_dir = tmp_path / "data"
    cfg_dir.mkdir()

    class Cfg:
        dir = cfg_dir

        def get(self, key, default=None):
            if key == "click_sound_pack":
                return {"kind": "file", "id": "custom", "path": str(custom)}
            if key == "click_sound_volume":
                return 0.8
            return default

    class FakePet:
        click_sound_enabled = True
        cfg = Cfg()

    sent = []

    def capture(path, volume=1.0):
        sent.append((path, volume))
        return True

    monkeypatch.setattr(window_mod, "play_sound", capture)
    window_mod.PetWindow._play_click_sound(FakePet())
    assert sent == [(custom, 0.8)]


def test_resolve_click_sound_pair_duck_and_non_duck(monkeypatch, tmp_path):
    press = _make_file(tmp_path, "Ya1.mp3")
    release = _make_file(tmp_path, "Ya2.mp3")
    monkeypatch.setattr(click_sound, "resolve_click_sound_candidates", lambda pack, data_dir=None: [press, release])
    monkeypatch.setattr(click_sound, "_cache_path", lambda path: path.with_suffix(".wav"))
    assert click_sound.resolve_click_sound_pair({"kind": "builtin", "id": "duck"}) == (press, release)
    press.with_suffix(".wav").write_bytes(b"")
    release.with_suffix(".wav").write_bytes(b"")
    assert click_sound.resolve_click_sound_pair({"kind": "builtin", "id": "duck"}) == (
        press.with_suffix(".wav"), release.with_suffix(".wav"))
    assert click_sound.resolve_click_sound_pair({"kind": "file", "id": "custom"}) is None


def test_press_sound_stops_release_and_restarts_press(monkeypatch, tmp_path):
    pair = (_make_file(tmp_path, "press.wav"), _make_file(tmp_path, "release.wav"))
    release_effect = SimpleNamespace(
        stopped=False, stop=lambda: setattr(release_effect, "stopped", True),
        setLoopCount=lambda count: None, setVolume=lambda volume: None,
    )
    played = []
    monkeypatch.setattr(click_sound._pool, "effect_for", lambda path: release_effect)
    monkeypatch.setattr(click_sound._pool, "play_sound", lambda path, volume=1.0: played.append((path, volume)) or True)
    assert click_sound.play_press_sound(pair, 0.6) is True
    assert release_effect.stopped is True
    assert played == [(pair[0], 0.6)]


def test_release_sound_schedules_press_tail(monkeypatch, tmp_path):
    press = tmp_path / "press.wav"
    release = tmp_path / "release.wav"
    for path in (press, release):
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(1000)
            out.writeframes(b"\0\0" * 1000)
    calls = []
    monkeypatch.setattr(click_sound._pool, "play_sound", lambda path, volume=1.0: calls.append((path, volume)) or True)
    monkeypatch.setattr(click_sound.time, "monotonic", lambda: 0.2)
    scheduled = []
    monkeypatch.setattr(QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))
    click_sound.play_release_sound((press, release), 0.5, press_started_at=0.0)
    assert scheduled and scheduled[0][0] == 700
    scheduled[0][1]()
    assert calls == [(release, 0.5)]


def test_second_pool_instance_state_is_isolated_from_singleton(tmp_path):
    """批4：第二个 ClickSoundPool 实例与模块单例 _pool 的状态互相隔离。

    类方法一律走 self.<method>()，实例的可变状态（音效/解码器/播放器池/
    时长缓存/配对状态）必须各自独立。不造 Qt 对象，仅轻量状态断言。
    """
    other = click_sound.ClickSoundPool()
    singleton = click_sound._pool

    # 1) 可变状态容器不是同一对象
    for attr in ("_qt_effects", "_qt_decoders", "_qt_player_pool",
                 "_wav_duration_cache", "_click_pair_state"):
        assert getattr(other, attr) is not getattr(singleton, attr), attr

    # 2) 直接写互不串
    other._wav_duration_cache["a.wav"] = 1.5
    assert "a.wav" not in singleton._wav_duration_cache
    other._qt_effects["k"] = object()
    assert "k" not in singleton._qt_effects
    index_before = singleton._qt_player_index
    other._qt_player_index = 7
    assert singleton._qt_player_index == index_before

    # 3) 经实例方法写入只落在第二个实例：批4 前 play_with_effect 会经实例
    #    方法 effect_for 把音效写进单例 _pool._qt_effects（实例间串写）。
    wav = tmp_path / "tick.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(1000)
        out.writeframes(b"\0\0" * 100)

    assert other.wav_duration(wav) == 0.1
    assert str(wav.resolve()) in other._wav_duration_cache
    assert str(wav.resolve()) not in singleton._wav_duration_cache

    class _FakeEffect:
        def __init__(self):
            self.source = None

        def setSource(self, source):
            self.source = source

        def setVolume(self, volume):
            pass

        def play(self):
            pass

    other.qt_multimedia_classes = lambda: (None, None, None, None, _FakeEffect)
    assert other.play_with_effect(wav, 0.5) is True
    assert str(wav.resolve()) in other._qt_effects
    assert str(wav.resolve()) not in singleton._qt_effects


def test_click_sound_immediate_toggle_in_dialog_affects_pet_window(tmp_path, monkeypatch):
    """回归测试：设置对话框中即时关闭点击音效，桌宠窗口点击立即不播放。"""
    from pet.config import Config
    from pet.window import PetWindow
    from pet.modern_settings_dialog import ModernSettingsDialog
    from PySide6.QtWidgets import QApplication
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    config = Config(tmp_path)
    config.set("click_sound_enabled", True)

    win = PetWindow.__new__(PetWindow)
    win.cfg = config

    # 初始状态下属性读取 cfg 为 True
    assert win.click_sound_enabled is True

    # 模拟设置对话框即时关闭
    monkeypatch.setattr("pet.modern_settings_dialog.autostart_mod.is_enabled", lambda: False)
    dialog = ModernSettingsDialog(config, include_ai=False)
    assert dialog.click_sound_check.isChecked() is True

    played = []
    monkeypatch.setattr("pet.window.play_sound", lambda *a, **k: played.append(a))
    monkeypatch.setattr("pet.window.play_press_sound", lambda *a, **k: played.append(a))

    # 关闭点击音效
    dialog.click_sound_check.setChecked(False)

    # 验证即时写回 config 且 PetWindow 读到 False
    assert config.get("click_sound_enabled") is False
    assert win.click_sound_enabled is False

    # 触发播放点击音效
    win._play_click_sound()
    assert played == []

    dialog.close()
    app.processEvents()


