"""图形界面的测试。

分两半：

* **纯逻辑**——模式方案、状态摊平、乐谱摘要这些不碰 Qt，直接调；
* **控件层**——用 Qt 的 offscreen 后端建真的窗口，验证页面跳转和状态刷新。

offscreen 后端让这些测试不需要显示器、也不会弹窗，所以在 CI 或纯终端里都能跑。
引擎一律用替身：既不开 HID 设备，也不开声卡。
"""

import os

# 必须在导入 PySide6 之前设好，否则 QApplication 会去找真实窗口系统
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from macaccordion import gui
from macaccordion import keymap as km
from macaccordion import melody as mel
from macaccordion.bellows import BellowsState
from macaccordion.engine import AccordionEngine, EngineConfig
from macaccordion.melody import MelodyPlayer


# --------------------------------------------------------------------- 替身


class _FakeSensor:
    """不打开 HID 设备的替身传感器。"""

    def __init__(self) -> None:
        self._sensor = None            # → using_real_sensor 为 False
        self.latest_angle = 90.0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def set_simulated(self, angle: float) -> None:
        self.latest_angle = float(angle)


class _StubEngine:
    """够 :func:`gui.status_fields` 用的假引擎，不碰硬件也不出声。"""

    def __init__(self, melody: MelodyPlayer | None = None) -> None:
        self.sensor = _FakeSensor()
        self.keymap = km.Keymap()
        self.melody_player = melody
        self._melody_enabled = melody is not None
        self.state = BellowsState(pressure=0.5, position=0.4, direction=1)
        self._pitches: list[int] = []
        self.started = False
        self.stopped = False
        self.quit_requested = False

    @property
    def using_real_sensor(self) -> bool:
        return False

    @property
    def melody_enabled(self) -> bool:
        return self._melody_enabled

    @property
    def synth(self):
        stub = self

        class _Synth:
            @property
            def active_pitches(self):
                return sorted(stub._pitches)

        return _Synth()

    def start(self, enable_keyboard: bool = True) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


# --------------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(gui.DARK_QSS)
    return app


def _stub_engine(plan: gui.SessionPlan | None = None) -> _StubEngine:
    """按方案造替身引擎——盲弹方案下也带上真的旋律播放器。"""
    player = None
    if plan is not None and plan.is_blind:
        player = MelodyPlayer(mel.get_song(plan.song), loop=plan.loop)
    return _StubEngine(player)


@pytest.fixture
def no_hardware(monkeypatch):
    """把硬件探测全部挡掉：传感器存在性、HID 打开、音频流启动。"""
    from macaccordion import sensor as sensor_mod
    monkeypatch.setattr(sensor_mod.LidSensor, "is_present", staticmethod(lambda: False))
    monkeypatch.setattr(gui, "build_engine",
                        lambda plan=None, **kw: _stub_engine(plan))


@pytest.fixture
def window(qapp, no_hardware):
    win = gui.MainWindow()
    win.show()                       # offscreen，不会真的弹窗
    qapp.processEvents()
    yield win
    win.close()


# --------------------------------------------------------------------- 纯逻辑


def test_专业模式方案():
    plan = gui.SessionPlan(mode=gui.MODE_PRO)
    assert not plan.is_blind
    assert plan.describe() == "专业模式"


def test_盲弹方案带曲名():
    plan = gui.SessionPlan(mode=gui.MODE_BLIND, song="送别")
    assert plan.is_blind
    assert plan.describe() == "盲弹《送别》"


def test_风向文字():
    assert gui.direction_text(1) == "推开 →"
    assert gui.direction_text(-1) == "← 合拢"
    assert gui.direction_text(0) == "静止"


def test_气压条颜色分档():
    assert gui.level_color(0.1) == "#5DCAA5"
    assert gui.level_color(0.7) == "#EF9F27"
    assert gui.level_color(0.9) == "#E24B4A"


def test_乐谱参数不含曲名():
    detail = gui.song_detail(mel.SONGBIE)
    assert "送别" not in detail
    assert "80 BPM" in detail
    assert "88 个音" in detail


def test_默认仍然往终端写状态():
    """命令行模式的既有行为不能被改掉。"""
    assert EngineConfig().status_to_stdout is True


# --------------------------------------------------------------------- 造引擎


def test_专业模式不造旋律播放器(monkeypatch):
    monkeypatch.setattr("macaccordion.engine.open_best_available",
                        lambda **kw: _FakeSensor())
    engine = gui.build_engine(gui.SessionPlan(mode=gui.MODE_PRO))
    assert engine.melody_player is None
    assert not engine.melody_enabled


def test_盲弹模式按方案装载乐谱(monkeypatch):
    monkeypatch.setattr("macaccordion.engine.open_best_available",
                        lambda **kw: _FakeSensor())
    plan = gui.SessionPlan(mode=gui.MODE_BLIND, song="送别", loop=False)
    engine = gui.build_engine(plan)
    assert engine.melody_enabled
    assert engine.melody_player.melody.name == "送别"
    assert engine.melody_player.loop is False


def test_图形界面不往终端刷状态(monkeypatch):
    monkeypatch.setattr("macaccordion.engine.open_best_available",
                        lambda **kw: _FakeSensor())
    engine = gui.build_engine(gui.SessionPlan(mode=gui.MODE_PRO))
    assert engine.ecfg.status_to_stdout is False


def test_认不出的曲名会报错(monkeypatch):
    monkeypatch.setattr("macaccordion.engine.open_best_available",
                        lambda **kw: _FakeSensor())
    with pytest.raises(KeyError):
        gui.build_engine(gui.SessionPlan(mode=gui.MODE_BLIND, song="不存在的曲子"))


# --------------------------------------------------------------------- 状态摊平


def _real_engine(monkeypatch, melody=None):
    monkeypatch.setattr("macaccordion.engine.open_best_available",
                        lambda **kw: _FakeSensor())
    return AccordionEngine(
        engine_config=EngineConfig(status_to_stdout=False), melody=melody)


def test_状态字段_专业模式(monkeypatch):
    engine = _real_engine(monkeypatch)
    fields = gui.status_fields(engine)
    assert fields["blind"] is False
    assert fields["mode_title"] == "专业模式"
    assert fields["sensor"] == "模拟(↑↓)"
    assert fields["total"] == 0
    assert fields["angle"] == 90.0


def test_状态字段_盲弹模式(monkeypatch):
    engine = _real_engine(monkeypatch, melody=MelodyPlayer(mel.SONGBIE))
    fields = gui.status_fields(engine)
    assert fields["blind"] is True
    assert fields["song"] == "送别"
    assert fields["mode_title"] == "盲弹模式"
    assert (fields["index"], fields["total"]) == (0, 88)

    engine._handle_key(km.VK["a"], down=True)
    assert gui.status_fields(engine)["index"] == 1


def test_状态字段跟着引擎的盲弹开关走(monkeypatch):
    engine = _real_engine(monkeypatch, melody=MelodyPlayer(mel.SONGBIE))
    engine.toggle_melody()                     # 按 \ 关掉
    fields = gui.status_fields(engine)
    assert fields["blind"] is False
    assert fields["mode_title"] == "专业模式"


def test_引擎暴露退出请求(monkeypatch):
    engine = _real_engine(monkeypatch)
    assert engine.quit_requested is False
    engine._handle_key(km.VK[km.KEY_QUIT], down=True)
    assert engine.quit_requested is True


# --------------------------------------------------------------------- 控件层


def test_窗口从模式选择页开始(window):
    assert window._stack.currentWidget() is window.mode_page


def test_点专业模式卡片直接进演奏页(window):
    QTest.mouseClick(window.mode_page.pro_card, Qt.MouseButton.LeftButton)
    assert window._stack.currentWidget() is window.play_page
    assert window.engine is not None and window.engine.started
    assert window.play_page._change_song.isHidden()
    assert not window.play_page._layout_text.isHidden()


def test_点盲弹卡片先进乐谱页(window):
    QTest.mouseClick(window.mode_page.blind_card, Qt.MouseButton.LeftButton)
    assert window._stack.currentWidget() is window.song_page
    assert window.engine is None


def test_乐谱页返回模式页(window):
    QTest.mouseClick(window.mode_page.blind_card, Qt.MouseButton.LeftButton)
    window.song_page.backRequested.emit()
    assert window._stack.currentWidget() is window.mode_page


def test_乐谱页默认选中送别并循环(window):
    assert window.song_page.selected_song() == "送别"
    assert window.song_page.loop_enabled() is True


def test_开始演奏带出乐谱与循环选项(window):
    got: list[tuple[str, bool]] = []
    window.song_page.startRequested.connect(lambda n, l: got.append((n, l)))
    window.song_page._loop.setChecked(False)
    window.song_page._emit_start()
    assert got == [("送别", False)]


def test_盲弹演奏页显示进度隐藏键位图(window):
    window.song_page.startRequested.emit("送别", True)
    page = window.play_page
    assert window._stack.currentWidget() is page
    assert not page._progress.isHidden()
    assert not page._change_song.isHidden()
    assert page._layout_text.isHidden()


def test_停止回到模式页并释放引擎(window):
    window.song_page.startRequested.emit("送别", True)
    engine = window.engine
    window.play_page.stopRequested.emit()
    assert window._stack.currentWidget() is window.mode_page
    assert engine.stopped is True
    assert window.engine is None


def test_换乐谱回到乐谱页(window):
    window.song_page.startRequested.emit("送别", True)
    window.play_page.changeSongRequested.emit()
    assert window._stack.currentWidget() is window.song_page


def test_刷新把引擎状态画到界面上(window):
    window.song_page.startRequested.emit("送别", True)
    window.engine.sensor.latest_angle = 73.0
    window.engine._pitches = [70]
    window.engine.melody_player.index = 12
    window._refresh()

    page = window.play_page
    assert page._angle._value.text() == "73°"
    assert page._notes._value.text() == "A#4"
    assert page._progress_caption.text() == "进度 12/88"
    assert page._progress.value() == 12
    assert page._badge.text() == "盲弹模式"


def test_引擎报告退出时退回模式页(window):
    window.song_page.startRequested.emit("送别", True)
    window.engine.quit_requested = True
    window._refresh()
    assert window._stack.currentWidget() is window.mode_page


def test_移调后键位图跟着变(window):
    QTest.mouseClick(window.mode_page.pro_card, Qt.MouseButton.LeftButton)
    page = window.play_page
    assert "C4" in page._layout_text.text()

    window.engine.keymap.shift_octave(1)      # 升八度
    window._refresh()
    assert "C4" not in page._layout_text.text()
    assert "C5" in page._layout_text.text()


def test_气压条上限取风箱配置(window):
    assert window.play_page._level._maximum == pytest.approx(1.25)


def test_关窗会停掉引擎(qapp, no_hardware):
    win = gui.MainWindow()
    win.song_page.startRequested.emit("送别", True)
    engine = win.engine
    win.close()
    assert engine.stopped is True
