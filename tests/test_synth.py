"""合成引擎的单元测试。"""

import numpy as np
import pytest

from macaccordion.synth import AccordionSynth, SynthConfig, WavetableSet, _read_table


def _render_seconds(synth: AccordionSynth, seconds: float, pressure=1.0,
                    air=0.0, direction=0) -> np.ndarray:
    n = int(seconds * synth.cfg.sample_rate / synth.cfg.block_size)
    return np.concatenate([synth.render(pressure, air, direction) for _ in range(n)])


# --------------------------------------------------------------------- 波表


def test_波表按谐波数分级且都归一化():
    ws = WavetableSet(size=512, max_harmonics=32)
    assert [h for h, _ in ws.levels] == [1, 2, 4, 8, 16, 32]
    for _, table in ws.levels:
        assert table.shape == (512,)
        assert np.max(np.abs(table)) == pytest.approx(1.0)


def test_高频时允许的谐波数下降():
    ws = WavetableSet()
    low = ws.allowed_harmonics(110.0, 44100)
    high = ws.allowed_harmonics(2000.0, 44100)
    assert high < low
    # 所有谐波都必须在奈奎斯特之下
    assert 2000.0 * high < 44100 / 2


def test_选表返回两张表且混合比例在范围内():
    ws = WavetableSet()
    dark, bright, mix = ws.pick(440.0, 44100, brightness=0.5)
    assert dark.shape == bright.shape == (ws.size,)
    assert 0.0 <= mix <= 1.0


def test_波表插值不会越界():
    ws = WavetableSet(size=256, max_harmonics=8)
    table = ws.levels[-1][1]
    for phase in np.linspace(0.0, 0.9999, 500):
        v = _read_table(table, np.array([phase]))
        assert np.isfinite(v[0])
        assert abs(v[0]) <= 1.0 + 1e-9


# --------------------------------------------------------------------- 音符管理


def test_note_on_note_off():
    s = AccordionSynth()
    assert s.n_held == 0
    s.note_on(60)
    s.note_on(64)
    assert s.n_held == 2
    assert s.active_pitches == [60, 64]
    s.note_off(60)
    assert s.n_held == 1
    # 松开后仍在余音中，要等包络衰减完才消失
    assert 60 in s.active_pitches
    _render_seconds(s, 0.5)
    assert 60 not in s.active_pitches


def test_重复_note_on_不会创建多个声部():
    s = AccordionSynth()
    s.note_on(60)
    s.note_on(60)
    s.note_on(60)
    assert s.n_active == 1


def test_声部数不超过上限():
    cfg = SynthConfig(max_voices=5)
    s = AccordionSynth(cfg)
    for p in range(60, 80):
        s.note_on(p)
    assert s.n_active <= 5


def test_全部松开后音符会被回收():
    s = AccordionSynth()
    for p in (60, 64, 67):
        s.note_on(p)
    s.all_notes_off()
    _render_seconds(s, 1.0)
    assert s.n_active == 0


# --------------------------------------------------------------------- 音频质量


def test_没有音符且没有气声时输出为静音():
    s = AccordionSynth()
    block = s.render(pressure=1.0, air=0.0)
    assert np.allclose(block, 0.0)


def test_音符输出有声音且不含异常值():
    s = AccordionSynth()
    s.note_on(60)
    audio = _render_seconds(s, 0.5, pressure=0.8)
    assert audio.size > 0
    assert np.all(np.isfinite(audio))
    assert float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) > 0.01
    assert float(np.max(np.abs(audio))) <= 1.0


def test_气压为零时几乎无声():
    s = AccordionSynth()
    s.note_on(60)
    _render_seconds(s, 0.1, pressure=1.0)   # 先让包络起来
    quiet = _render_seconds(s, 0.2, pressure=0.0)
    loud = _render_seconds(s, 0.2, pressure=1.0)
    assert np.max(np.abs(quiet)) < 0.02
    assert np.max(np.abs(loud)) > np.max(np.abs(quiet)) * 5


def test_音高正确_单簧片():
    cfg = SynthConfig(reed_count=1, detune_cents=0.0, breath_gain=0.0, sample_rate=44100)
    s = AccordionSynth(cfg)
    s.note_on(69)          # A4 = 440 Hz
    audio = _render_seconds(s, 1.0, pressure=1.0)

    spec = np.abs(np.fft.rfft(audio.astype(np.float64) * np.hanning(audio.size)))
    freqs = np.fft.rfftfreq(audio.size, 1 / cfg.sample_rate)
    peak = freqs[int(np.argmax(spec))]
    assert abs(peak - 440.0) < 3.0, f"主频 {peak:.1f} Hz，期望 440 Hz"


def test_块与块之间相位连续():
    """分块渲染不能有拼接爆音。"""
    s = AccordionSynth(SynthConfig(breath_gain=0.0))
    s.note_on(60)
    blocks = [s.render(1.0) for _ in range(20)]
    audio = np.concatenate(blocks).astype(np.float64)

    diff = np.abs(np.diff(audio))
    step = s.cfg.block_size
    boundaries = np.array([abs(audio[i] - audio[i - 1]) for i in range(step, len(audio), step)])
    assert boundaries.max() <= diff.max() * 3 + 1e-6, "块边界出现了不连续跳变"


def test_气声随气流强度增加():
    s = AccordionSynth(SynthConfig(breath_gain=0.2))
    s.note_on(60)
    _render_seconds(s, 0.1, pressure=1.0, air=0.0)
    dry = _render_seconds(s, 0.3, pressure=1.0, air=0.0)
    s.reset()
    s.note_on(60)
    _render_seconds(s, 0.1, pressure=1.0, air=1.0)
    wet = _render_seconds(s, 0.3, pressure=1.0, air=1.0)
    assert np.sqrt(np.mean(wet.astype(np.float64) ** 2)) > np.sqrt(
        np.mean(dry.astype(np.float64) ** 2))


def test_明暗度影响高频能量():
    """气压越高应该越亮（高频占比更高）。"""
    cfg = SynthConfig(breath_gain=0.0, reed_count=1, detune_cents=0.0)
    s = AccordionSynth(cfg)
    s.note_on(60)
    _render_seconds(s, 0.05, pressure=1.0)

    def high_ratio(p):
        s2 = AccordionSynth(cfg)
        s2.note_on(60)
        _render_seconds(s2, 0.05, pressure=1.0)
        audio = _render_seconds(s2, 0.5, pressure=p).astype(np.float64)
        spec = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(audio.size, 1 / cfg.sample_rate)
        total = spec.sum() + 1e-12
        return spec[freqs > 1000].sum() / total

    assert high_ratio(1.0) > high_ratio(0.05)


def test_多音叠加不会硬削波():
    s = AccordionSynth(SynthConfig(max_voices=12))
    for p in range(60, 72):
        s.note_on(p)
    audio = _render_seconds(s, 0.5, pressure=1.2)
    assert np.all(np.isfinite(audio))
    assert float(np.max(np.abs(audio))) <= 1.0


def test_推拉方向改变音色():
    cfg = SynthConfig(breath_gain=0.0)
    outs = []
    for d in (1, -1):
        s = AccordionSynth(cfg)
        s.note_on(60)
        _render_seconds(s, 0.05, pressure=1.0, direction=d)
        outs.append(_render_seconds(s, 0.4, pressure=1.0, direction=d))
    assert not np.allclose(outs[0], outs[1]), "推开与合拢应该有音色差异"
