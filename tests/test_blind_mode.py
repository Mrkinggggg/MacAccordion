"""盲弹模式在引擎里的路由测试。

不碰真实硬件：把 ``open_best_available`` 换成一个假传感器，这样可以在
没有声卡、没有屏幕角度传感器的情况下验证"按键 → 旋律推进"的接线是否正确。

要点：
  - 字母区按键推进旋律，音高由乐谱决定（按哪个字母都一样）
  - 数字行/标点仍然走原来的固定音高，功能键照旧
  - 按住不放的系统重复事件要忽略，不能一口气把整首曲子冲完
  - 同时只有一个音（新按键掐掉上一个）
"""

import pytest

from macaccordion import keymap as km
from macaccordion import melody as mel
from macaccordion.engine import AccordionEngine
from macaccordion.melody import MelodyPlayer


class _FakeSensor:
    """替身传感器：足够 engine 用，不打开任何 HID 设备。"""

    def __init__(self) -> None:
        self._sensor = None            # → using_real_sensor 为 False
        self.latest_angle = 90.0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def set_simulated(self, angle: float) -> None:
        self.latest_angle = float(angle)


@pytest.fixture
def engine(monkeypatch):
    import macaccordion.engine as eng
    monkeypatch.setattr(eng, "open_best_available", lambda **kw: _FakeSensor())
    return AccordionEngine(melody=MelodyPlayer(mel.SONGBIE))


def _press(engine, key: str) -> None:
    engine._handle_key(km.VK[key], down=True)


def _release(engine, key: str) -> None:
    engine._handle_key(km.VK[key], down=False)


def test_字母键推进旋律(engine):
    assert engine.melody_enabled
    assert engine.melody_player.index == 0
    _press(engine, "q")
    assert engine.melody_player.index == 1
    assert engine.synth.active_pitches == [70]      # 第一个音 5 = B♭4


def test_按哪个字母都一样(engine):
    """旋律只由乐谱决定，跟具体按了哪个键无关。"""
    _press(engine, "a")
    _release(engine, "a")
    first = engine.synth.active_pitches

    engine.melody_player.reset()
    engine.synth.reset()
    _press(engine, "p")
    second = engine.synth.active_pitches

    assert first == second == [70]


def test_连按七个键得到旋律前七个音(engine):
    """Mk 举的例子：无论什么顺序按，前七个按键就是乐谱的前七个音。"""
    expected = [engine.melody_player.pitch_of(n) for n in mel.SONGBIE.notes[:7]]
    got = []
    for key in "zxcvbnm":
        _press(engine, key)
        got.append(engine.synth.active_pitches[-1])
        _release(engine, key)
        engine.synth.reset()            # 清掉余音，方便看下一个音
    assert got == expected
    assert got == [70, 67, 70, 75, 72, 75, 72]      # 5 3 5 1̇ 6 1̇ 6


def test_按住不放不会连冲(engine):
    """macOS 会持续发按下事件，必须忽略，否则一按就冲完半首。"""
    for _ in range(10):
        _press(engine, "a")
    assert engine.melody_player.index == 1


def test_同时只有一个音(engine):
    _press(engine, "a")
    assert engine.synth.n_held == 1
    _press(engine, "s")                 # 不松手直接按下一个
    assert engine.synth.n_held == 1, "新音应该掐掉上一个，保持单声部"
    assert 67 in engine.synth.active_pitches       # 第二个音 3 = G4


def test_松开收掉这个音(engine):
    _press(engine, "a")
    assert engine.synth.n_held == 1
    _release(engine, "a")
    assert engine.synth.n_held == 0


def test_松开已经被顶掉的键不出错(engine):
    _press(engine, "a")
    _press(engine, "s")
    _release(engine, "a")               # a 的音早被 s 掐掉了
    assert engine.synth.n_held == 1
    _release(engine, "s")
    assert engine.synth.n_held == 0


def test_数字行仍然走固定音高(engine):
    """数字行不是字母区，按原样当琴键用。"""
    _press(engine, "1")
    assert engine.melody_player.index == 0, "数字键不该推进旋律"
    assert engine.synth.active_pitches == [km.Keymap().pitch_for(km.VK["1"])]


def test_功能键照旧可用(engine):
    _press(engine, "]")
    assert engine.keymap.octave_shift == 12
    assert engine.melody_player.index == 0


def test_八度移调对盲弹也生效(engine):
    _press(engine, "]")
    _release(engine, "]")
    _press(engine, "a")
    assert engine.synth.active_pitches == [82]      # B♭4 + 12


def test_开关盲弹模式(engine):
    assert engine.melody_enabled
    _press(engine, km.KEY_MELODY_TOGGLE)
    assert not engine.melody_enabled
    _press(engine, km.KEY_MELODY_TOGGLE)
    assert engine.melody_enabled


def test_关掉盲弹后字母键变成普通琴键(engine):
    _press(engine, km.KEY_MELODY_TOGGLE)
    _press(engine, "a")
    assert engine.melody_player.index == 0
    assert engine.synth.active_pitches == [km.Keymap().pitch_for(km.VK["a"])]


def test_打开盲弹时旋律回到开头(engine):
    for key in "asdf":
        _press(engine, key)
    assert engine.melody_player.index == 4
    _press(engine, km.KEY_MELODY_TOGGLE)     # 关
    _press(engine, km.KEY_MELODY_TOGGLE)     # 再开 → 复位
    assert engine.melody_player.index == 0


def test_没有乐谱时开关无效(monkeypatch):
    import macaccordion.engine as eng
    monkeypatch.setattr(eng, "open_best_available", lambda **kw: _FakeSensor())
    plain = AccordionEngine()
    assert not plain.melody_enabled
    assert plain.toggle_melody() is False
    _press(plain, "a")
    assert plain.synth.active_pitches == [km.Keymap().pitch_for(km.VK["a"])]


def test_状态栏显示盲弹进度(engine):
    assert "盲弹《送别》0/88音" in engine.status_line()
    _press(engine, "a")
    line = engine.status_line()
    assert "盲弹《送别》1/88音" in line
    assert "A#4" in line, "状态栏应该显示正在响的音名"


def test_关掉盲弹后状态栏显示传感器模式(engine):
    _press(engine, km.KEY_MELODY_TOGGLE)
    assert "模拟(↑↓)" in engine.status_line()
