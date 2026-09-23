"""实时合成：手风琴音色。

手风琴的音色特征来自"多簧片略微失谐"（Musette 调音）：同一个音由 2~3 片
簧片同时发声，彼此差几到十几音分，产生标志性的"颤音厚度"。

实现要点：
  - 用**波表**而不是逐个谐波累加，保证实时性
  - 波表按谐波数做 mip 分级（1/2/4/8/...），按基频选表，避免高频混叠
  - 明暗由**在暗表与亮表之间线性插值**实现，全向量化，无逐样本循环
  - 音量由风箱气压驱动；按键只负责"打开阀门"（包络）
  - 气声噪声随气流强度变化

分块渲染，无状态泄漏；不依赖声卡，可离线调用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------- 波表


def _saw_table(size: int, harmonics: int) -> np.ndarray:
    """带限锯齿波（1/n 谐波叠加），归一化到 ±1。"""
    x = np.arange(size, dtype=np.float64) / size
    t = np.zeros(size, dtype=np.float64)
    for n in range(1, harmonics + 1):
        t += np.sin(2.0 * np.pi * n * x) / n
    peak = np.max(np.abs(t))
    return t / peak if peak > 0 else t


class WavetableSet:
    """一组按谐波数分级的带限锯齿波表。"""

    def __init__(self, size: int = 2048, max_harmonics: int = 64) -> None:
        self.size = size
        self.levels: list[tuple[int, np.ndarray]] = []
        h = 1
        while h <= max_harmonics:
            self.levels.append((h, _saw_table(size, h)))
            h *= 2
        if not self.levels:
            raise ValueError("max_harmonics 太小")

    def allowed_harmonics(self, freq: float, sample_rate: int, headroom: float = 0.45) -> int:
        """不超过奈奎斯特、并留出余量时，该基频最多能放多少谐波。"""
        if freq <= 0:
            return 1
        return max(1, int(sample_rate * headroom / freq))

    def pick(self, freq: float, sample_rate: int, brightness: float = 1.0):
        """按基频和明暗度选两张表（暗表, 亮表, 混合比例）。

        brightness=0 只出基频附近（暗），=1 用满允许的谐波（亮）。
        """
        cap = self.allowed_harmonics(freq, sample_rate)
        bright_h = max(1, min(cap, self.levels[-1][0]))
        dark_h = max(1, bright_h // 4)

        dark = self._level_for(dark_h)
        bright = self._level_for(bright_h)
        return dark, bright, float(np.clip(brightness, 0.0, 1.0))

    def _level_for(self, harmonics: int) -> np.ndarray:
        best = self.levels[0]
        for h, table in self.levels:
            if h <= harmonics:
                best = (h, table)
            else:
                break
        return best[1]


def _read_table(table: np.ndarray, phase: np.ndarray) -> np.ndarray:
    """线性插值读取波表。phase 为 [0,1) 的相位数组。"""
    n = table.shape[0]
    pos = phase * n
    i0 = np.floor(pos).astype(np.int64) % n
    i1 = (i0 + 1) % n
    frac = pos - np.floor(pos)
    return table[i0] * (1.0 - frac) + table[i1] * frac


# --------------------------------------------------------------------- 配置


@dataclass
class SynthConfig:
    sample_rate: int = 44100
    block_size: int = 256

    table_size: int = 2048
    max_harmonics: int = 64

    reed_count: int = 3
    """簧片数。1=单簧，2=双簧，3=Musette（默认）。"""

    detune_cents: float = 9.0
    """相邻簧片的失谐量（音分）。越大越"抖"，经典 Musette 约 8~15。"""

    master_gain: float = 0.42
    """总输出增益。留出余量，让和弦也不会顶到软削波的天花板。"""

    attack_tau: float = 0.012
    """按键阀门打开时间常数（秒）。"""

    release_tau: float = 0.090
    """按键阀门关闭时间常数（秒）。"""

    breath_gain: float = 0.05
    """气声噪声上限增益。"""

    breath_smooth: int = 8
    """气声噪声的降采样倍数（越大越"闷"）。"""

    brightness_low: float = 0.30
    """气压为 0 时的明暗度。"""

    brightness_high: float = 0.95
    """气压拉满时的明暗度。"""

    brightness_push: float = 0.10
    """推开（+）与合拢（-）的明暗差异，模拟真实手风琴推拉音色不同。"""

    soft_clip: float = 1.6
    """软削波驱动量，防止多音叠加时硬削。"""

    max_voices: int = 24

    seed: int = 20260923


# --------------------------------------------------------------------- 单音


class _Voice:
    """一个正在发声的音。"""

    __slots__ = ("pitch", "freq", "phases", "increments", "env", "held")

    def __init__(self, pitch: int, freq: float, reed_ratios: np.ndarray,
                 sample_rate: int, n_reeds: int) -> None:
        self.pitch = pitch
        self.freq = freq
        self.phases = np.zeros(n_reeds, dtype=np.float64)
        self.increments = freq * reed_ratios / sample_rate
        self.env = 0.0
        self.held = True


# --------------------------------------------------------------------- 合成器


class AccordionSynth:
    """分块渲染的手风琴合成器。

    使用方式（音频回调）::

        synth.note_on(60)
        block = synth.render(pressure=0.7, air=0.5, direction=1)
        synth.note_off(60)
    """

    def __init__(self, config: SynthConfig | None = None) -> None:
        self.cfg = config or SynthConfig()
        self.tables = WavetableSet(self.cfg.table_size, self.cfg.max_harmonics)
        self.rng = np.random.default_rng(self.cfg.seed)
        self._voices: dict[int, _Voice] = {}
        self._noise_state = np.zeros(0, dtype=np.float64)

        n = self.cfg.reed_count
        if n < 1:
            raise ValueError("reed_count 至少为 1")
        # 对称分布的失谐：1 簧 [0]；2 簧 [-d, +d]；3 簧 [-d, 0, +d]
        if n == 1:
            offsets = np.array([0.0])
        else:
            spread = np.linspace(-1.0, 1.0, n)
            offsets = spread * self.cfg.detune_cents
        self.reed_ratios = np.power(2.0, offsets / 1200.0)

    # ------------------------------------------------------------------

    @staticmethod
    def midi_to_freq(pitch: int) -> float:
        return 440.0 * (2.0 ** ((pitch - 69) / 12.0))

    @property
    def active_pitches(self) -> list[int]:
        return sorted(self._voices)

    @property
    def n_active(self) -> int:
        """正在发声的音数（含已松开但仍在余音中的）。"""
        return len(self._voices)

    @property
    def n_held(self) -> int:
        """当前实际按住的琴键数。风箱耗气按这个算。"""
        return sum(1 for v in self._voices.values() if v.held)

    # ------------------------------------------------------------------

    def note_on(self, pitch: int) -> None:
        if pitch in self._voices:
            self._voices[pitch].held = True
            return
        if len(self._voices) >= self.cfg.max_voices:
            # 超限时踢掉最早的一个，避免音频回调里分配失控
            self._voices.pop(next(iter(self._voices)))
        self._voices[pitch] = _Voice(
            pitch, self.midi_to_freq(pitch), self.reed_ratios,
            self.cfg.sample_rate, self.cfg.reed_count)

    def note_off(self, pitch: int) -> None:
        v = self._voices.get(pitch)
        if v is not None:
            v.held = False

    def all_notes_off(self) -> None:
        for v in self._voices.values():
            v.held = False

    def reset(self) -> None:
        self._voices.clear()
        self._noise_state = np.zeros(0, dtype=np.float64)

    # ------------------------------------------------------------------

    def render(self, pressure: float, air: float = 0.0, direction: int = 0) -> np.ndarray:
        """渲染一个音频块。

        Args:
            pressure: 风箱气压（音量）
            air: 气流强度 0..1（气声噪声量）
            direction: +1 推开 / -1 合拢 / 0 静止（影响明暗）

        Returns:
            float32 数组，长度 = block_size，范围约 [-1, 1]
        """
        cfg = self.cfg
        n = cfg.block_size
        out = np.zeros(n, dtype=np.float64)

        # --- 明暗度：气压 + 推拉方向 ---
        span = max(1e-6, cfg.brightness_high - cfg.brightness_low)
        brightness = cfg.brightness_low + span * float(np.clip(pressure, 0.0, 1.2))
        brightness += cfg.brightness_push * (1 if direction > 0 else (-1 if direction < 0 else 0))
        brightness = float(np.clip(brightness, 0.0, 1.0))

        # --- 按键包络（块率一阶滞后）---
        dt = n / cfg.sample_rate
        a_attack = 1.0 - math.exp(-dt / cfg.attack_tau)
        a_release = 1.0 - math.exp(-dt / cfg.release_tau)
        ramp = np.arange(n, dtype=np.float64)

        dead: list[int] = []
        for pitch, v in self._voices.items():
            a = a_attack if v.held else a_release
            v.env += ((1.0 if v.held else 0.0) - v.env) * a

            if not v.held and v.env < 1e-4:
                dead.append(pitch)
                continue

            # 逐样本相位，形状 (簧片数, 块长)。
            # 注意：相位必须逐样本推进 —— 一个块只算一个相位值的话，
            # 输出会变成 172 Hz 的采样保持阶梯，波形完全错。
            ph = v.phases[:, None] + v.increments[:, None] * ramp[None, :]
            ph -= np.floor(ph)

            dark, bright, mix = self.tables.pick(v.freq, cfg.sample_rate, brightness)
            wave = _read_table(dark, ph) * (1.0 - mix) + _read_table(bright, ph) * mix
            # 失谐簧片之间部分不相干，按功率相加 → 除以 sqrt(N) 而不是 N
            out += wave.sum(axis=0) * v.env / math.sqrt(v.phases.shape[0])

            v.phases += v.increments * n
            v.phases -= np.floor(v.phases)

        for pitch in dead:
            del self._voices[pitch]

        # --- 音量：气压驱动 ---
        out *= float(max(0.0, pressure))

        # --- 气声噪声（降采样后重复 = 粗糙低通，比白噪声更像气流）---
        if air > 1e-4 and cfg.breath_gain > 0:
            k = max(1, cfg.breath_smooth)
            m = (n + k - 1) // k
            coarse = self.rng.standard_normal(m)
            noise = np.repeat(coarse, k)[:n]
            out += noise * air * cfg.breath_gain

        # --- 输出级 ---
        out *= cfg.master_gain
        out = np.tanh(out * cfg.soft_clip) / np.tanh(cfg.soft_clip)
        # tanh 的渐近线是 1/tanh(soft_clip) > 1，所以最后再夹一次保证不越界
        return np.clip(out, -1.0, 1.0).astype(np.float32)
