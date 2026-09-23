"""离线渲染：不依赖声卡，把「角度轨迹 + 按键事件」渲染成音频。

用途：
  1. 验证整条链路（风箱模型 → 合成）是否正常，不需要真的接音箱；
  2. 快速试听音色和风箱手感，改参数后几秒钟就能出一版；
  3. 用真实传感器录一段角度轨迹，之后离线反复渲染对比参数。

输出 16-bit PCM WAV。
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

from .bellows import Bellows, BellowsConfig, BellowsState
from .synth import AccordionSynth, SynthConfig

# --------------------------------------------------------------------- 角度轨迹


class AngleTrack:
    """角度随时间变化的轨迹，支持线性插值查询。"""

    def __init__(self, points: Sequence[tuple[float, float]]) -> None:
        if not points:
            raise ValueError("至少需要一个 (t, angle) 采样点")
        pts = sorted(points, key=lambda p: p[0])
        self._t = np.array([p[0] for p in pts], dtype=np.float64)
        self._a = np.array([p[1] for p in pts], dtype=np.float64)

    def __call__(self, t: float) -> float:
        return float(np.interp(t, self._t, self._a))

    def __len__(self) -> int:
        return len(self._t)

    @property
    def duration(self) -> float:
        """轨迹覆盖的时长（秒）。"""
        return float(self._t[-1] - self._t[0])

    # -- 读写 -------------------------------------------------------------

    @classmethod
    def from_csv(cls, path: str, time_col: int = 0, angle_col: int = 1,
                 skip_header: bool = True) -> "AngleTrack":
        pts: list[tuple[float, float]] = []
        with open(path) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line or (skip_header and i == 0):
                    continue
                parts = line.replace(",", "\t").split()
                if len(parts) <= max(time_col, angle_col):
                    continue
                try:
                    pts.append((float(parts[time_col]), float(parts[angle_col])))
                except ValueError:
                    continue
        return cls(pts)

    def save_csv(self, path: str) -> None:
        with open(path, "w") as f:
            f.write("t\tangle\n")
            for t, a in zip(self._t, self._a):
                f.write(f"{t:.4f}\t{a:.1f}\n")


def pumping_track(period: float = 2.0, low: float = 55.0, high: float = 115.0,
                  duration: float = 10.0, rate: float = 60.0,
                  ramp: float = 0.0) -> AngleTrack:
    """生成一段"匀速开合"的角度轨迹（正弦往复），用于试听风箱。

    Args:
        period: 一个完整来回的周期（秒）
        low/high: 开合角度的下限/上限（度）
        duration: 总时长（秒）
        rate: 轨迹采样率（Hz）
        ramp: 前 ramp 秒内把幅度从 0 渐入，避免一上来就满速
    """
    pts: list[tuple[float, float]] = []
    n = int(duration * rate) + 1
    mid, amp = (low + high) / 2.0, (high - low) / 2.0
    for i in range(n):
        t = i / rate
        gain = 1.0 if ramp <= 0 else min(1.0, t / ramp)
        pts.append((t, mid + amp * gain * math.sin(2.0 * math.pi * t / period)))
    return AngleTrack(pts)


# --------------------------------------------------------------------- 事件


@dataclass(frozen=True)
class NoteEvent:
    t: float
    pitch: int
    on: bool

    def __post_init__(self) -> None:
        if not (0 <= self.pitch <= 127):
            raise ValueError(f"音高越界: {self.pitch}")


def note(t0: float, t1: float, pitch: int) -> list[NoteEvent]:
    """便捷构造：一个音从 t0 按到 t1。"""
    return [NoteEvent(t0, pitch, True), NoteEvent(t1, pitch, False)]


def chord(t0: float, t1: float, pitches: Iterable[int]) -> list[NoteEvent]:
    evs: list[NoteEvent] = []
    for p in pitches:
        evs += note(t0, t1, p)
    return evs


# --------------------------------------------------------------------- 渲染


@dataclass
class RenderResult:
    samples: np.ndarray
    sample_rate: int
    bellows_trace: list[tuple[float, float, int]]
    """(t, pressure, direction) 轨迹，便于画图和排查。"""

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate

    def stats(self) -> dict:
        s = self.samples
        if not s.size:
            return {"samples": 0, "duration_s": 0.0, "peak": 0.0,
                    "rms": 0.0, "clipped": 0, "nan": 0}
        return {
            "samples": int(s.size),
            "duration_s": round(self.duration, 3),
            "peak": round(float(np.max(np.abs(s))), 4),
            "rms": round(float(np.sqrt(np.mean(s.astype(np.float64) ** 2))), 5),
            "clipped": int(np.sum(np.abs(s) >= 0.999)),
            "nan": int(np.sum(~np.isfinite(s))),
        }

    def write_wav(self, path: str, normalize: float | None = None) -> str:
        """写 16-bit PCM WAV。normalize 给定时按该峰值归一化（None = 原样）。"""
        data = self.samples.astype(np.float64)
        if normalize:
            peak = float(np.max(np.abs(data))) or 1.0
            data = data * (normalize / peak)
        pcm = np.clip(data, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)

        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm.tobytes())
        return path


def render(angle: AngleTrack | Callable[[float], float],
           events: Sequence[NoteEvent] = (),
           duration: float = 5.0,
           bellows_config: BellowsConfig | None = None,
           synth_config: SynthConfig | None = None,
           trace_every: float = 0.05) -> RenderResult:
    """把角度轨迹与按键事件渲染成音频。

    Args:
        angle: 角度轨迹（AngleTrack 或任意 callable: t -> 角度）
        events: 按键事件（无需排序）
        duration: 渲染时长（秒）
        trace_every: 风箱状态记录间隔（秒），<=0 表示不记录
    """
    synth_cfg = synth_config or SynthConfig()
    bellows = Bellows(bellows_config or BellowsConfig())
    synth = AccordionSynth(synth_cfg)

    sr = synth_cfg.sample_rate
    block = synth_cfg.block_size
    n_blocks = int(math.ceil(duration * sr / block))
    out = np.zeros(n_blocks * block, dtype=np.float32)

    evs = sorted(events, key=lambda e: e.t)
    ev_idx = 0
    trace: list[tuple[float, float, int]] = []
    next_trace = 0.0

    for b in range(n_blocks):
        t0 = b * block / sr
        t1 = t0 + block / sr

        # 落在本块时间窗内的事件先应用，再渲染本块
        while ev_idx < len(evs) and evs[ev_idx].t < t1:
            e = evs[ev_idx]
            if e.on:
                synth.note_on(e.pitch)
            else:
                synth.note_off(e.pitch)
            ev_idx += 1

        state = bellows.update(float(angle(t0)), t0, n_keys=synth.n_held)
        out[b * block:(b + 1) * block] = synth.render(
            state.pressure, state.air, state.direction)

        if trace_every > 0 and t0 >= next_trace:
            trace.append((round(t0, 4), round(state.pressure, 4), state.direction))
            next_trace += trace_every

    return RenderResult(out, sr, trace)


def record_angle_track(seconds: float, rate_hz: float = 60.0,
                       progress: Callable[[float, float], None] | None = None) -> AngleTrack:
    """用真实传感器录一段角度轨迹（需要有人开合屏幕）。"""
    from .sensor import SensorReader, open_best_available

    reader = open_best_available(rate_hz=rate_hz)
    pts: list[tuple[float, float]] = []
    import time as _time
    reader.start()
    t0 = _time.monotonic()
    try:
        while True:
            t = _time.monotonic() - t0
            if t >= seconds:
                break
            if reader.valid:
                pts.append((t, reader.latest_angle))
            if progress:
                progress(t, reader.latest_angle)
            _time.sleep(1.0 / rate_hz)
    finally:
        reader.stop()
    if not pts:
        raise RuntimeError("没有录到任何角度数据（传感器不可用？）")
    return AngleTrack(pts)


# --------------------------------------------------------------------- 演示曲


def demo() -> tuple[AngleTrack, list[NoteEvent], float]:
    """一段用来试听的演示：音阶 → 和弦 → 同一音慢推/快推对比。

    刻意做成"体检"而不是"曲子"，一眼能听出风箱模型的几个行为。
    """
    events: list[NoteEvent] = []
    t = 0.6

    # 1) C 大调上行音阶，每个音一小拍
    for pitch in [60, 62, 64, 65, 67, 69, 71, 72]:
        events += note(t, t + 0.28, pitch)
        t += 0.30

    t += 0.4
    # 2) 三和弦：同时按 3 个音，气压被分摊，需要更用力推
    events += chord(t, t + 1.8, [60, 64, 67])
    t += 2.2

    # 3) 同一音，先慢推再快推 —— 听音量和明暗的变化
    events += note(t, t + 1.6, 67)
    t += 2.0

    # 4) 五度双音，收尾
    events += chord(t, t + 1.6, [60, 67, 72])
    t += 2.0

    return pumping_track(period=1.6, low=58.0, high=112.0, duration=t + 0.6, ramp=0.5), events, t + 0.6
