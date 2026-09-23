"""风箱模型：把屏幕开合角度变成"气压"。

模型是一个储气罐：
    平衡气压 = 进气量 / 耗气系数
    dP/dt    = 一阶滞后（上升快、下降慢）

    进气量 ∝ 角速度（推拉风箱）+ 位置项（静止时开得大有一点基础气息）
    耗气   ∝ 基础漏气 + 每个按住琴键的簧片耗气

这样能得到接近真实手风琴的行为：
  - 不动 = 没声音（或只有很轻的底音）
  - 推拉越快 = 气压越高 = 越响
  - 同时按住的和弦越多 = 气压被分摊得越低 = 需要更用力推
  - 静止时把屏幕开得更大 = 有一点气息底座，不至于完全断音

纯数值逻辑，不依赖硬件，可单元测试。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .sensor import RangeTracker


@dataclass
class BellowsConfig:
    # --- 角速度 → 进气 ---
    deadzone_dps: float = 8.0
    """角速度死区（度/秒）。低于此值视为没在推拉，避免手抖出音。"""

    max_speed_dps: float = 180.0
    """角速度归一化上限（度/秒）。到这么快就认为是"满进气"。
    人手推拉的典型峰值约 120~200 度/秒，所以取这个量级。"""

    speed_smooth_tau: float = 0.035
    """角速度平滑时间常数（秒）。"""

    # --- 位置项 ---
    pos_base: float = 0.22
    """静止时位置项的最大贡献。设为 0 = 纯角速度驱动（最像真手风琴，
    但停手就断音）；调大 = 静止时也有基础气息，更好上手。"""

    # --- 耗气 ---
    leak_rate: float = 1.10
    """基础漏气速率（1/秒）。越大则气压越难积累。"""

    reed_load: float = 0.45
    """每个按住琴键带来的额外耗气（1/秒）。"""

    # --- 气压动态 ---
    max_pressure: float = 1.25
    """气压上限，允许轻微超驱（>1）以产生"冲"的感觉。"""

    rise_tau: float = 0.018
    """气压上升时间常数（秒）。小 = 反应快、更"冲"。"""

    fall_tau: float = 0.16
    """气压下降时间常数（秒）。大 = 余音更长。"""

    def __post_init__(self) -> None:
        if self.max_speed_dps <= self.deadzone_dps:
            raise ValueError("max_speed_dps 必须大于 deadzone_dps")
        for name in ("rise_tau", "fall_tau", "speed_smooth_tau"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须为正")


@dataclass
class BellowsState:
    pressure: float = 0.0
    """气压 0..max_pressure。直接当音量用。"""

    speed: float = 0.0
    """平滑后的角速度（度/秒，带符号：正=开/推，负=合/拉）。"""

    speed_norm: float = 0.0
    """角速度归一化强度 0..1（无符号）。"""

    direction: int = 0
    """+1 = 开/推，-1 = 合/拉，0 = 静止。用于音色明暗。"""

    position: float = 0.0
    """归一化开合位置 0..1。"""

    air: float = 0.0
    """气流强度 0..1（气压 × 按键数），用于气声噪声量。"""


class Bellows:
    """把角度序列转成气压状态。"""

    def __init__(self, config: BellowsConfig | None = None) -> None:
        self.cfg = config or BellowsConfig()
        self.tracker = RangeTracker()
        self.state = BellowsState()
        self._prev_angle: float | None = None
        self._prev_time: float | None = None
        self._speed_smoothed = 0.0

    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.tracker.reset()
        self.state = BellowsState()
        self._prev_angle = self._prev_time = None
        self._speed_smoothed = 0.0

    # ------------------------------------------------------------------

    def update(self, angle: float, t: float, n_keys: int = 0) -> BellowsState:
        """推进一个控制周期。

        Args:
            angle: 当前屏幕角度（度）
            t: 单调时间戳（秒）
            n_keys: 当前按住的琴键数

        Returns:
            更新后的 BellowsState（同一个对象，就地更新）
        """
        cfg = self.cfg
        self.tracker.update(angle)

        # --- 首次调用：没有上一帧，只记录基准 ---
        if self._prev_angle is None or self._prev_time is None:
            self._prev_angle, self._prev_time = angle, t
            self.state = BellowsState(position=self.tracker.normalize(angle))
            return self.state

        dt = t - self._prev_time
        if dt <= 0:
            return self.state
        # 时间戳异常跳变（休眠唤醒、系统卡顿）时不要产生巨大的伪速度
        if dt > 0.5:
            self._prev_angle, self._prev_time = angle, t
            return self.state

        raw_speed = (angle - self._prev_angle) / dt
        self._prev_angle, self._prev_time = angle, t

        # --- 角速度平滑（一阶低通，dt 无关）---
        a = 1.0 - math.exp(-dt / cfg.speed_smooth_tau)
        self._speed_smoothed += (raw_speed - self._speed_smoothed) * a
        speed = self._speed_smoothed

        # --- 归一化 ---
        magnitude = abs(speed)
        excess = max(0.0, magnitude - cfg.deadzone_dps)
        speed_norm = _clamp(excess / (cfg.max_speed_dps - cfg.deadzone_dps), 0.0, 1.0)
        position = self.tracker.normalize(angle)

        # --- 进气 / 耗气 ---
        inflow = speed_norm + cfg.pos_base * position
        drain = cfg.leak_rate + cfg.reed_load * max(0, n_keys)
        equilibrium = inflow / drain if drain > 0 else 0.0

        # --- 一阶滞后逼近平衡气压（上升/下降用不同时间常数）---
        tau = cfg.rise_tau if equilibrium > self.state.pressure else cfg.fall_tau
        k = 1.0 - math.exp(-dt / tau)
        pressure = self.state.pressure + (equilibrium - self.state.pressure) * k
        pressure = _clamp(pressure, 0.0, cfg.max_pressure)

        if speed > cfg.deadzone_dps:
            direction = 1
        elif speed < -cfg.deadzone_dps:
            direction = -1
        else:
            direction = 0

        self.state = BellowsState(
            pressure=pressure,
            speed=speed,
            speed_norm=speed_norm,
            direction=direction,
            position=position,
            # 没有琴键按住 = 阀门全关 = 没有气流经过簧片，也就不该有气声
            air=_clamp(pressure * min(1.0, max(0, n_keys) / 2.0), 0.0, 1.0),
        )
        return self.state


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)
