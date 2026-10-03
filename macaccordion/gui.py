"""图形界面：模式选择 → 乐谱选择 → 演奏。

用 PySide6（Qt for Python）实现。界面本身只负责显示与跳转，所有声音逻辑
仍然走 :mod:`macaccordion.engine` 那套实时引擎——界面不改音频路径，
只在旁边用定时器读它的状态。

分层
----
本模块刻意分成两层：

* **纯逻辑**（``SessionPlan`` / ``build_engine`` / ``status_fields`` 等）不碰
  Qt 控件，可以脱离显示器单测；
* **控件层**（``ModePage`` / ``SongPage`` / ``PlayPage`` / ``MainWindow``）
  只做渲染和事件转发。

这样界面逻辑的回归测试不需要真的开一个窗口。

线程
----
引擎在后台跑三条线程（传感器 / 音频 / 键盘监听），界面在主线程用
:class:`QTimer` 每 80 ms 拉一次状态。**只读**，不反向写引擎状态，
所以不需要额外的锁。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import keymap as km
from . import melody as mel
from .bellows import BellowsConfig
from .engine import AccordionEngine, EngineConfig
from .melody import MelodyPlayer
from .synth import SynthConfig

# --------------------------------------------------------------------- 纯逻辑

MODE_PRO = "pro"
"""专业模式：字母区每个键绑定固定音高，正常演奏。"""

MODE_BLIND = "blind"
"""盲弹模式：音高由乐谱决定，按键只决定什么时候响。"""

MODE_TITLES = {MODE_PRO: "专业模式", MODE_BLIND: "盲弹模式"}

POLL_INTERVAL_MS = 80
"""界面刷新间隔。引擎的音频回调是 172 Hz，这里 12.5 Hz 足够看状态了。"""


@dataclass(frozen=True)
class SessionPlan:
    """一次演奏的方案：用什么模式、哪首曲子、要不要循环。"""

    mode: str = MODE_PRO
    song: str | None = None
    loop: bool = True

    @property
    def is_blind(self) -> bool:
        return self.mode == MODE_BLIND

    def describe(self) -> str:
        if not self.is_blind:
            return "专业模式"
        return f"盲弹《{self.song}》"


def build_engine(plan: SessionPlan,
                 bellows_config: BellowsConfig | None = None,
                 synth_config: SynthConfig | None = None) -> AccordionEngine:
    """按方案造一台引擎。

    注意这里把 ``status_to_stdout`` 关掉了——终端状态栏是给命令行模式用的，
    图形界面下由 :func:`status_fields` 直接读状态自己画。
    """
    engine_config = EngineConfig(status_to_stdout=False)
    player = None
    if plan.is_blind:
        player = MelodyPlayer(mel.get_song(plan.song), loop=plan.loop)
    return AccordionEngine(
        bellows_config=bellows_config,
        synth_config=synth_config,
        engine_config=engine_config,
        melody=player,
    )


def direction_text(direction: int) -> str:
    """风向文字。"""
    return {1: "推开 →", -1: "← 合拢"}.get(direction, "静止")


def song_detail(song: mel.Melody) -> str:
    """乐谱参数一行，不含曲名（曲名在列表里单独显示）。"""
    key = song.key_root.replace("b", "♭")
    return f"1 = {key}　{song.bpm} BPM　{song.n_bars} 小节 / {len(song.notes)} 个音"


def level_color(fraction: float) -> str:
    """气压条的颜色：低=青、中=琥珀、高=红。"""
    if fraction >= 0.85:
        return "#E24B4A"
    if fraction >= 0.6:
        return "#EF9F27"
    return "#5DCAA5"


def status_fields(engine: AccordionEngine) -> dict:
    """把引擎状态摊平成界面要显示的字段。

    只读引擎的公开状态，不改任何东西。特意**不**调用 ``synth.n_held``——
    那个属性在 Python 层遍历正在被音频线程改动的字典，界面线程不该去碰。
    """
    st = engine.state
    player = engine.melody_player
    blind = engine.melody_enabled and player is not None
    return {
        "blind": blind,
        "song": player.melody.name if blind else None,
        "mode_title": MODE_TITLES[MODE_BLIND if blind else MODE_PRO],
        "angle": float(engine.sensor.latest_angle),
        "position": float(st.position),
        "pressure": float(st.pressure),
        "direction": int(st.direction),
        "notes": [km.note_name(p) for p in engine.synth.active_pitches[:10]],
        "sensor": "实体屏幕" if engine.using_real_sensor else "模拟(↑↓)",
        "index": player.index if blind else 0,
        "total": player.total if blind else 0,
        "octave_shift": int(engine.keymap.octave_shift),
        "quit": bool(engine.quit_requested),
    }


# --------------------------------------------------------------------- 样式

DARK_QSS = """
QWidget {
    background: #1c1c1e;
    color: #f2f2f7;
    font-family: -apple-system, "PingFang SC", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}
QLabel { background: transparent; }
QLabel#h1 { font-size: 26px; font-weight: 500; }
QLabel#h2 { font-size: 16px; font-weight: 500; }
QLabel#muted { color: #98989d; }
QLabel#badge {
    background: #0F6E56; border-radius: 6px; padding: 3px 10px;
    font-size: 12px; color: #E1F5EE;
}
QLabel#mono {
    font-family: "SF Mono", Menlo, monospace; font-size: 12px; color: #c7c7cc;
}
QFrame#card {
    background: #2c2c2e; border: 1px solid #3a3a3c; border-radius: 12px;
}
QFrame#card:hover { background: #333336; border: 1px solid #5DCAA5; }
QLabel#cardTitle { font-size: 17px; font-weight: 500; }
QLabel#cardSub { color: #98989d; font-size: 12px; }
QFrame#stat {
    background: #2c2c2e; border: 1px solid #3a3a3c; border-radius: 10px;
}
QLabel#statCap { color: #98989d; font-size: 11px; }
QLabel#statVal { font-size: 19px; font-weight: 500; }
QPushButton {
    background: #2c2c2e; border: 1px solid #3a3a3c; border-radius: 8px;
    padding: 8px 18px;
}
QPushButton:hover { background: #3a3a3c; }
QPushButton#primary { background: #0F6E56; border: 1px solid #5DCAA5; }
QPushButton#primary:hover { background: #1D9E75; }
QListWidget {
    background: #2c2c2e; border: 1px solid #3a3a3c; border-radius: 10px; padding: 6px;
}
QListWidget::item { padding: 10px; border-radius: 8px; }
QListWidget::item:selected { background: #0F6E56; color: #E1F5EE; }
QCheckBox { spacing: 8px; }
QProgressBar {
    background: #3a3a3c; border: none; border-radius: 6px; min-height: 12px;
    max-height: 12px;
}
QProgressBar::chunk { background: #5DCAA5; border-radius: 6px; }
"""


# --------------------------------------------------------------------- 小部件


class LevelBar(QWidget):
    """气压条。数值来自风箱模型，上限取 ``BellowsConfig.max_pressure``。"""

    def __init__(self, maximum: float = 1.25, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._maximum = max(1e-6, float(maximum))
        self._value = 0.0
        self.setFixedHeight(16)

    def set_value(self, value: float) -> None:
        value = max(0.0, min(self._maximum, float(value)))
        if abs(value - self._value) > 1e-3:
            self._value = value
            self.update()

    def paintEvent(self, event) -> None:      # noqa: N802  (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3a3a3c"))
        painter.drawRoundedRect(rect, radius, radius)

        fraction = self._value / self._maximum
        if fraction > 0.0:
            width = max(rect.height(), rect.width() * fraction)
            fill = QRectF(rect.x(), rect.y(), width, rect.height())
            painter.setBrush(QColor(level_color(fraction)))
            painter.drawRoundedRect(fill, radius, radius)
        painter.end()


class StatBlock(QFrame):
    """一个统计小块：上面小字标题，下面大字数值。"""

    def __init__(self, caption: str, value: str = "—",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stat")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(1)
        self._caption = QLabel(caption)
        self._caption.setObjectName("statCap")
        self._value = QLabel(value)
        self._value.setObjectName("statVal")
        layout.addWidget(self._caption)
        layout.addWidget(self._value)

    def set_value(self, text: str) -> None:
        if self._value.text() != text:
            self._value.setText(text)


class CardButton(QFrame):
    """大号可点卡片：标题 + 一行说明。用作模式选择入口。"""

    clicked = Signal()

    def __init__(self, title: str, subtitle: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("cardTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("cardSub")
        sub.setWordWrap(True)
        layout.addWidget(head)
        layout.addWidget(sub)
        layout.addStretch(1)

    def mouseReleaseEvent(self, event) -> None:      # noqa: N802  (Qt 命名)
        inside = self.rect().contains(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and inside:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _set_visible(widget: QWidget, visible: bool) -> None:
    """只在显隐状态真的需要变时才动它，避免每 80 ms 触发一次重排。

    用 ``isHidden()`` 而不是 ``isVisible()`` 判断：页面在 QStackedWidget 里
    被切走时，整页的子控件 ``isVisible()`` 都是 False，拿它当依据会把
    "该隐藏的"当成"已经隐藏"，切回来时又冒出来。
    """
    if widget.isHidden() == visible:
        widget.setVisible(visible)


# --------------------------------------------------------------------- 页面


class ModePage(QWidget):
    """第一屏：选专业模式还是盲弹模式。"""

    proChosen = Signal()
    blindChosen = Signal()

    def __init__(self, sensor_hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 28)
        layout.setSpacing(18)

        title = QLabel("MacAccordion")
        title.setObjectName("h1")
        subtitle = QLabel("把 MacBook 的开盖角度当风箱，把键盘当琴键。")
        subtitle.setObjectName("muted")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(6)

        pro = CardButton("专业模式", "字母区每个键对应一个固定音高，正常演奏。")
        blind = CardButton("盲弹模式", "音高由乐谱决定，按任意字母键就出下一个音。")
        pro.clicked.connect(self.proChosen)
        blind.clicked.connect(self.blindChosen)
        self.pro_card = pro
        self.blind_card = blind

        layout.addWidget(pro)
        layout.addWidget(blind)
        layout.addStretch(1)

        self._hint = QLabel(sensor_hint)
        self._hint.setObjectName("muted")
        layout.addWidget(self._hint)

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)


class SongPage(QWidget):
    """盲弹模式的第二屏：挑一首曲子。"""

    startRequested = Signal(str, bool)
    """参数：(乐谱名, 是否循环)"""

    backRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 28)
        layout.setSpacing(14)

        title = QLabel("选择乐谱")
        title.setObjectName("h1")
        layout.addWidget(title)

        self._list = QListWidget()
        for name, song in mel.SONGS.items():
            item = QListWidgetItem(f"{name}　　{song_detail(song)}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        layout.addWidget(self._list, 1)

        self._loop = QCheckBox("走完自动绕回开头")
        self._loop.setChecked(True)
        layout.addWidget(self._loop)

        hint = QLabel("曲谱写在 macaccordion/melody.py 里，加一首就会自动出现在这个列表里。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        back = QPushButton("返回")
        back.clicked.connect(self.backRequested)
        start = QPushButton("开始演奏")
        start.setObjectName("primary")
        start.clicked.connect(self._emit_start)
        buttons.addWidget(back)
        buttons.addStretch(1)
        buttons.addWidget(start)
        layout.addLayout(buttons)

    # -- 内部 -------------------------------------------------------------

    def selected_song(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def loop_enabled(self) -> bool:
        return self._loop.isChecked()

    def _emit_start(self) -> None:
        name = self.selected_song()
        if name is not None:
            self.startRequested.emit(name, self.loop_enabled())


class PlayPage(QWidget):
    """第三屏：演奏中。只显示状态，不参与发声。"""

    stopRequested = Signal()
    changeSongRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(12)

        head = QHBoxLayout()
        self._badge = QLabel("专业模式")
        self._badge.setObjectName("badge")
        self._title = QLabel()
        self._title.setObjectName("h2")
        head.addWidget(self._badge)
        head.addSpacing(8)
        head.addWidget(self._title)
        head.addStretch(1)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self._angle = StatBlock("角度")
        self._pressure = StatBlock("气压")
        self._direction = StatBlock("风向")
        self._notes = StatBlock("正在响")
        for col, block in enumerate((self._angle, self._pressure,
                                     self._direction, self._notes)):
            grid.addWidget(block, 0, col)
            grid.setColumnStretch(col, 1)

        self._level = LevelBar(BellowsConfig().max_pressure)

        self._progress_caption = QLabel("进度")
        self._progress_caption.setObjectName("muted")
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)

        self._layout_text = QLabel()
        self._layout_text.setObjectName("mono")

        hint = QLabel("空格 呼吸　[ ] 移调　Tab 复位　\\ 切换盲弹　Esc 返回")
        hint.setObjectName("muted")

        buttons = QHBoxLayout()
        self._change_song = QPushButton("换乐谱")
        self._change_song.clicked.connect(self.changeSongRequested)
        stop = QPushButton("停止并返回")
        stop.setObjectName("primary")
        stop.clicked.connect(self.stopRequested)
        buttons.addWidget(self._change_song)
        buttons.addStretch(1)
        buttons.addWidget(stop)

        layout.addStretch(1)
        layout.addLayout(head)
        layout.addLayout(grid)
        layout.addWidget(self._level)
        layout.addWidget(self._progress_caption)
        layout.addWidget(self._progress)
        layout.addWidget(self._layout_text)
        layout.addStretch(1)
        layout.addWidget(hint)
        layout.addLayout(buttons)

    # -- 状态更新 ---------------------------------------------------------

    def prepare(self, plan: SessionPlan, engine: AccordionEngine) -> None:
        """开始演奏前重置这一页。"""
        self._badge.setText(MODE_TITLES[plan.mode])
        self._title.setText(plan.describe() if plan.is_blind else "")
        self._angle.set_value("—")
        self._pressure.set_value("0.00")
        self._direction.set_value("静止")
        self._notes.set_value("—")
        self._level.set_value(0.0)
        self._progress.setRange(0, max(1, engine.melody_player.total
                                       if engine.melody_player else 1))
        self._progress.setValue(0)
        self._progress_caption.setText("进度")
        self._layout_text.setText("\n".join(engine.keymap.layout_lines()))
        self._last_octave = 0
        _set_visible(self._progress_caption, plan.is_blind)
        _set_visible(self._progress, plan.is_blind)
        _set_visible(self._change_song, plan.is_blind)
        _set_visible(self._layout_text, not plan.is_blind)

    def update_from(self, fields: dict) -> None:
        self._badge.setText(fields["mode_title"])
        blind = fields["blind"]
        self._title.setText(f"盲弹《{fields['song']}》" if blind else "")
        self._angle.set_value(f"{fields['angle']:.0f}°")
        self._pressure.set_value(f"{fields['pressure']:.2f}")
        self._direction.set_value(direction_text(fields["direction"]))
        self._notes.set_value(" ".join(fields["notes"]) or "—")
        self._level.set_value(fields["pressure"])

        _set_visible(self._progress_caption, blind)
        _set_visible(self._progress, blind)
        _set_visible(self._change_song, blind)
        _set_visible(self._layout_text, not blind)
        if blind:
            self._progress.setRange(0, max(1, fields["total"]))
            self._progress.setValue(fields["index"])
            self._progress_caption.setText(
                f"进度 {fields['index']}/{fields['total']}")

        # 八度移调会改变键位图上的音名，变了才重画
        if not blind and fields["octave_shift"] != self._last_octave:
            self._last_octave = fields["octave_shift"]
            self._layout_text.setText("\n".join(
                self._layout_lines_for(fields["octave_shift"])))

    def _layout_lines_for(self, octave_shift: int) -> list[str]:
        """按给定移调量重画键位图（不依赖引擎，方便单测）。"""
        keymap = km.Keymap()
        keymap.octave_shift = octave_shift
        return keymap.layout_lines()


# --------------------------------------------------------------------- 主窗口


class MainWindow(QMainWindow):
    """把三个页面串起来，并管住引擎的生死。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MacAccordion")
        self.setMinimumSize(720, 540)

        self.engine: AccordionEngine | None = None
        self.plan = SessionPlan()

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self.mode_page = ModePage()
        self.song_page = SongPage()
        self.play_page = PlayPage()
        for page in (self.mode_page, self.song_page, self.play_page):
            self._stack.addWidget(page)

        self.mode_page.proChosen.connect(
            lambda: self._start_session(SessionPlan(mode=MODE_PRO)))
        self.mode_page.blindChosen.connect(self._show_songs)
        self.song_page.backRequested.connect(self._show_modes)
        self.song_page.startRequested.connect(self._start_blind)
        self.play_page.stopRequested.connect(self._show_modes)
        self.play_page.changeSongRequested.connect(self._show_songs)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh)

        self.mode_page.set_hint(self._sensor_hint())
        self._stack.setCurrentWidget(self.mode_page)

    # -- 页面跳转 ---------------------------------------------------------

    def _show_modes(self) -> None:
        self._stop_engine()
        self.mode_page.set_hint(self._sensor_hint())
        self._stack.setCurrentWidget(self.mode_page)

    def _show_songs(self) -> None:
        self._stop_engine()
        self._stack.setCurrentWidget(self.song_page)

    def _start_blind(self, song: str, loop: bool) -> None:
        self._start_session(SessionPlan(mode=MODE_BLIND, song=song, loop=loop))

    def _start_session(self, plan: SessionPlan) -> None:
        self._stop_engine()
        self.plan = plan
        try:
            engine = build_engine(plan)
        except Exception as exc:            # 传感器/声卡初始化失败
            QMessageBox.critical(self, "启动失败", str(exc))
            return
        self.engine = engine
        self.play_page.prepare(plan, engine)
        self._stack.setCurrentWidget(self.play_page)
        engine.start(enable_keyboard=True)
        self._timer.start()

    def _stop_engine(self) -> None:
        self._timer.stop()
        if self.engine is not None:
            self.engine.stop()
            self.engine = None

    # -- 刷新 -------------------------------------------------------------

    def _refresh(self) -> None:
        if self.engine is None:
            return
        try:
            fields = status_fields(self.engine)
        except Exception:
            return
        self.play_page.update_from(fields)
        if fields["quit"]:                  # 用户按了 Esc
            self._show_modes()

    def _sensor_hint(self) -> str:
        """探测传感器是否可用。只是提示，真正打开设备是点开始之后的事。"""
        from .sensor import LidSensor
        try:
            present = LidSensor.is_present()
        except Exception:
            present = False
        if present:
            return "传感器：实体屏幕角度　·　音频：44100 Hz / 256 样本"
        return "传感器：不可用 → 模拟模式（↑↓ 手动开合）　·　音频：44100 Hz / 256 样本"

    # -- 生命周期 ---------------------------------------------------------

    def closeEvent(self, event) -> None:      # noqa: N802  (Qt 命名)
        self._stop_engine()
        super().closeEvent(event)


# --------------------------------------------------------------------- 入口


def main(argv: list[str] | None = None) -> int:
    """打开图形界面。返回进程退出码。"""
    app = QApplication.instance() or QApplication(argv if argv is not None else [])
    app.setApplicationName("MacAccordion")
    app.setStyleSheet(DARK_QSS)

    window = MainWindow()
    window.resize(780, 580)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[:1]))
