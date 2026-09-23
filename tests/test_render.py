"""离线渲染链路的端到端测试。"""

import os
import wave

import numpy as np
import pytest

from macaccordion.bellows import BellowsConfig
from macaccordion.render import (
    AngleTrack, NoteEvent, chord, demo, note, pumping_track, render,
)
from macaccordion.synth import SynthConfig


def test_角度轨迹插值():
    tr = AngleTrack([(0.0, 60.0), (1.0, 120.0)])
    assert tr(0.0) == pytest.approx(60.0)
    assert tr(0.5) == pytest.approx(90.0)
    assert tr(1.0) == pytest.approx(120.0)
    assert tr(-5.0) == pytest.approx(60.0)      # 越界取端点
    assert tr(99.0) == pytest.approx(120.0)


def test_空轨迹报错():
    with pytest.raises(ValueError):
        AngleTrack([])


def test_事件构造与校验():
    evs = note(0.0, 1.0, 60)
    assert [e.on for e in evs] == [True, False]
    assert chord(0.0, 1.0, [60, 64, 67]).__len__() == 6
    with pytest.raises(ValueError):
        NoteEvent(0.0, 200, True)


def test_渲染时长与采样点数():
    cfg = SynthConfig()
    res = render(pumping_track(duration=2.0), [], duration=2.0, synth_config=cfg)
    assert res.sample_rate == cfg.sample_rate
    assert res.samples.size >= 2.0 * cfg.sample_rate
    assert res.duration == pytest.approx(2.0, abs=0.02)


def test_无音符时输出静音():
    res = render(pumping_track(duration=1.0), [], duration=1.0)
    assert float(np.max(np.abs(res.samples))) == pytest.approx(0.0, abs=1e-6)


def test_静止不推拉时无声音():
    """pos_base=0 时，屏幕不动就没有声音 —— 这是真实手风琴的行为。"""
    cfg = BellowsConfig(pos_base=0.0)
    track = AngleTrack([(t / 10, 90.0) for t in range(20)])
    res = render(track, note(0.2, 1.5, 60), duration=2.0, bellows_config=cfg)
    assert float(np.max(np.abs(res.samples))) < 0.01


def test_推拉时出声():
    res = render(pumping_track(duration=2.0, period=0.8),
                 note(0.2, 1.8, 60), duration=2.0)
    assert float(np.max(np.abs(res.samples))) > 0.05
    assert res.stats()["nan"] == 0
    assert res.stats()["clipped"] == 0


def test_演示曲能正常渲染():
    track, evs, dur = demo()
    res = render(track, evs, duration=dur)
    st = res.stats()
    assert st["nan"] == 0
    assert st["clipped"] == 0
    assert 0.05 < st["peak"] <= 1.0
    assert st["rms"] > 0.01
    assert len(res.bellows_trace) > 10


def test_风箱轨迹记录了推拉方向():
    track, evs, dur = demo()
    res = render(track, evs, duration=dur, trace_every=0.1)
    dirs = {d for _, _, d in res.bellows_trace}
    assert 1 in dirs and -1 in dirs, "轨迹里应同时出现推开与合拢"
    pressures = [p for _, p, _ in res.bellows_trace]
    assert max(pressures) > 0.3


def test_写出的_wav_能读回且长度一致(tmp_dir):
    res = render(pumping_track(duration=1.0), note(0.1, 0.9, 60), duration=1.0)
    path = os.path.join(tmp_dir, "t.wav")
    res.write_wav(path, normalize=0.9)

    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == res.sample_rate
        frames = w.readframes(w.getnframes())
        assert w.getnframes() == res.samples.size

    back = np.frombuffer(frames, dtype=np.int16)
    assert np.max(np.abs(back)) > 1000          # 确实有信号
    assert np.max(np.abs(back)) <= 32767


def test_归一化写文件后峰值接近目标(tmp_dir):
    res = render(pumping_track(duration=1.0), note(0.1, 0.9, 60), duration=1.0)
    path = os.path.join(tmp_dir, "n.wav")
    res.write_wav(path, normalize=0.8)
    with wave.open(path, "rb") as w:
        back = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert abs(float(np.max(np.abs(back))) / 32767 - 0.8) < 0.02


def test_渲染结果可复现():
    """同样的输入应该渲染出完全一样的输出（噪声用了固定种子）。"""
    track, evs, dur = demo()
    a = render(track, evs, duration=dur).samples
    b = render(track, evs, duration=dur).samples
    assert np.array_equal(a, b)
