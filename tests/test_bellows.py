"""风箱模型的单元测试。"""

import math

import pytest

from macaccordion.bellows import Bellows, BellowsConfig


def _drive(bellows: Bellows, angles, dt=1 / 60, n_keys=0):
    """按固定 dt 喂一串角度，返回最后一次的状态。"""
    st = None
    for i, a in enumerate(angles):
        st = bellows.update(a, i * dt, n_keys=n_keys)
    return st


def test_静止不动时气压衰减到接近零():
    cfg = BellowsConfig(pos_base=0.0)
    b = Bellows(cfg)
    # 先快速推一把攒起气压
    _drive(b, [60 + i * 2 for i in range(30)])
    assert b.state.pressure > 0.1
    # 然后停住不动
    _drive(b, [120.0] * 200)
    assert b.state.pressure < 0.02
    assert b.state.direction == 0
    assert abs(b.state.speed) < 1e-6


def test_往复推拉会产生并维持气压():
    cfg = BellowsConfig(pos_base=0.0)
    b = Bellows(cfg)
    angles = [85 + 27 * math.sin(2 * math.pi * i / 96) for i in range(400)]
    st = _drive(b, angles)
    assert st.pressure > 0.2, "往复推拉应该能攒起明显气压"


def test_按住更多琴键时同样速度下气压更低():
    """真实手风琴行为：和弦越多，气流被分摊，需要更用力推。"""
    angles = [85 + 27 * math.sin(2 * math.pi * i / 96) for i in range(400)]
    p1 = _drive(Bellows(), angles, n_keys=1).pressure
    p5 = _drive(Bellows(), angles, n_keys=5).pressure
    assert p5 < p1


def test_死区内的微小抖动不产生气压():
    cfg = BellowsConfig(pos_base=0.0, deadzone_dps=8.0)
    b = Bellows(cfg)
    # 每秒 60 帧、幅度 ±0.02°，角速度远小于死区
    angles = [90 + 0.02 * math.sin(i / 3) for i in range(300)]
    st = _drive(b, angles)
    assert st.pressure < 0.01


def test_气压不超过上限():
    cfg = BellowsConfig(pos_base=1.0, max_pressure=1.25)
    b = Bellows(cfg)
    angles = [60 + 60 * math.sin(2 * math.pi * i / 12) for i in range(2000)]
    st = _drive(b, angles)
    assert st.pressure <= cfg.max_pressure + 1e-9


def test_方向判定():
    b = Bellows()
    b.update(90.0, 0.0)
    assert b.update(100.0, 1 / 60).direction == 1     # 张开
    assert b.update(80.0, 2 / 60).direction == -1     # 合拢
    # 角速度有 35ms 的平滑时间常数，停手后要过几帧才会判为静止
    for i in range(3, 20):
        st = b.update(80.0, i / 60)
    assert st.direction == 0


def test_时间戳跳变不产生伪速度():
    """休眠唤醒/系统卡顿会导致 dt 很大，此时不能当成"推得飞快"。"""
    b = Bellows(BellowsConfig(pos_base=0.0))
    b.update(60.0, 0.0)
    st = b.update(130.0, 3.0)          # dt = 3s，跨了 70°
    assert st.pressure < 0.01
    assert st.speed_norm == 0.0


def test_dt_为零或负时状态不变():
    b = Bellows()
    b.update(90.0, 10.0)
    before = b.state.pressure
    assert b.update(120.0, 10.0).pressure == before
    assert b.update(120.0, 9.0).pressure == before


def test_输出始终有限且位置在范围内():
    b = Bellows()
    angles = [90 + 80 * math.sin(i / 7) for i in range(500)]
    for i, a in enumerate(angles):
        st = b.update(a, i / 60, n_keys=i % 4)
        assert math.isfinite(st.pressure)
        assert math.isfinite(st.speed)
        assert 0.0 <= st.position <= 1.0
        assert 0.0 <= st.speed_norm <= 1.0
        assert st.direction in (-1, 0, 1)


def test_配置校验():
    with pytest.raises(ValueError):
        BellowsConfig(max_speed_dps=5.0, deadzone_dps=10.0)
    with pytest.raises(ValueError):
        BellowsConfig(rise_tau=0.0)
    with pytest.raises(ValueError):
        BellowsConfig(fall_tau=-1.0)


def test_位置项为零时静止完全无声():
    b = Bellows(BellowsConfig(pos_base=0.0))
    _drive(b, [100.0] * 300)
    assert b.state.pressure == pytest.approx(0.0, abs=1e-6)


def test_reset_清空状态():
    b = Bellows()
    _drive(b, [60 + i for i in range(60)])
    b.reset()
    assert b.state.pressure == 0.0
    assert b.tracker.span == 0.0
