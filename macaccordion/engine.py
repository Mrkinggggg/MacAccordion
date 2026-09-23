"""实时引擎：把传感器、风箱、合成器、键盘串成一台能弹的琴。

线程模型：
    sensor 线程   ~60 Hz   轮询 Feature Report，只写 latest_angle
    audio 线程    由声卡驱动  读 latest_angle → 跑风箱模型 → 渲染一个块
    keyboard 线程 pynput     按键事件更新合成器的音符集合

为什么风箱模型放在**音频回调**里而不是传感器线程里：音频回调本来就是等间隔
触发的（44.1k / 256 ≈ 172 Hz），比 60 Hz 的传感器线程更均匀，而且省掉一次
跨线程同步。传感器线程只需要提供"最新角度"这一个数。
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import keymap as km
from . import melody as mel
from .bellows import Bellows, BellowsConfig, BellowsState
from .melody import MelodyPlayer
from .sensor import SensorReader, open_best_available
from .synth import AccordionSynth, SynthConfig


@dataclass
class EngineConfig:
    sample_rate: int = 44100
    block_size: int = 256
    sensor_rate_hz: float = 60.0
    status_interval: float = 0.4
    simulate_angle_step: float = 3.0
    """模拟模式下按一次 ↑/↓ 改变的角度（度）。"""


class AccordionEngine:
    """实时手风琴引擎。"""

    def __init__(self, bellows_config: BellowsConfig | None = None,
                 synth_config: SynthConfig | None = None,
                 engine_config: EngineConfig | None = None,
                 melody: MelodyPlayer | None = None) -> None:
        self.ecfg = engine_config or EngineConfig()
        self.scfg = synth_config or SynthConfig(
            sample_rate=self.ecfg.sample_rate, block_size=self.ecfg.block_size)
        self.scfg.sample_rate = self.ecfg.sample_rate
        self.scfg.block_size = self.ecfg.block_size

        self.synth = AccordionSynth(self.scfg)
        self.bellows = Bellows(bellows_config)
        self.keymap = km.Keymap()
        self.sensor: SensorReader = open_best_available(rate_hz=self.ecfg.sensor_rate_hz)

        # 盲弹模式：传了 melody 就默认打开，运行中可以用 \ 开关
        self.melody_player = melody
        self._melody_enabled = melody is not None
        self._melody_held: dict[int, int] = {}

        self._stream = None
        self._listener = None
        self._status_thread: threading.Thread | None = None
        self._running = threading.Event()
        self._state = BellowsState()
        self._state_lock = threading.Lock()

        self.breath = False
        self.callback_count = 0
        self.underflows = 0
        self.last_status = ""
        self._quit_requested = False
        self._quit_cb = None

        # 输出电平统计（自检用）
        self.peak_out = 0.0
        self._sum_sq = 0.0
        self._n_samples = 0

    # ------------------------------------------------------------------

    @property
    def using_real_sensor(self) -> bool:
        return self.sensor._sensor is not None

    @property
    def melody_enabled(self) -> bool:
        """盲弹模式是否开着。"""
        return self._melody_enabled and self.melody_player is not None

    def toggle_melody(self) -> bool:
        """开/关盲弹模式。打开时旋律回到第一个音，返回切换后的状态。"""
        if self.melody_player is None:
            return False
        self._melody_enabled = not self._melody_enabled
        self.synth.all_notes_off()
        self._melody_held.clear()
        if self._melody_enabled:
            self.melody_player.reset()
        return self._melody_enabled

    @property
    def state(self) -> BellowsState:
        with self._state_lock:
            return self._state

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, enable_keyboard: bool = True) -> None:
        import sounddevice as sd

        self.sensor.start()
        self._running.set()

        self._stream = sd.OutputStream(
            samplerate=self.scfg.sample_rate,
            blocksize=self.scfg.block_size,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

        if enable_keyboard:
            self._start_keyboard()

        self._status_thread = threading.Thread(
            target=self._status_loop, name="status", daemon=True)
        self._status_thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.sensor.stop()

    def __enter__(self) -> "AccordionEngine":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # 音频回调
    # ------------------------------------------------------------------

    def _audio_callback(self, outdata, frames, time_info, status) -> None:
        if status:
            self.underflows += 1
        self.callback_count += 1

        try:
            n_keys = 0 if self.breath else self.synth.n_held
            st = self.bellows.update(
                self.sensor.latest_angle, time.monotonic(), n_keys=n_keys)
            with self._state_lock:
                self._state = st
            block = self.synth.render(
                0.0 if self.breath else st.pressure,
                0.0 if self.breath else st.air,
                st.direction)
        except Exception:
            # 音频回调里绝不能抛异常，否则流会直接死掉
            block = np.zeros(frames, dtype=np.float32)

        n = min(frames, block.shape[0])
        outdata[:n, 0] = block[:n]
        if n < frames:
            outdata[n:, 0] = 0.0

        # 电平统计（几乎零开销，用于自检与调音）
        seg = block[:n]
        if seg.size:
            self.peak_out = max(self.peak_out, float(np.max(np.abs(seg))))
            self._sum_sq += float(np.dot(seg.astype(np.float64), seg.astype(np.float64)))
            self._n_samples += int(seg.size)

    @property
    def output_rms(self) -> float:
        return (self._sum_sq / self._n_samples) ** 0.5 if self._n_samples else 0.0

    def reset_meter(self) -> None:
        self.peak_out = 0.0
        self._sum_sq = 0.0
        self._n_samples = 0

    # ------------------------------------------------------------------
    # 键盘
    # ------------------------------------------------------------------

    def _start_keyboard(self) -> None:
        try:
            from pynput import keyboard
        except ImportError:
            print("⚠️  未安装 pynput，键盘不可用（pip install pynput）", file=sys.stderr)
            return

        def on_press(key):
            vk = getattr(key, "vk", None)
            if vk is None:
                return
            self._handle_key(vk, down=True)

        def on_release(key):
            vk = getattr(key, "vk", None)
            if vk is None:
                return
            self._handle_key(vk, down=False)

        try:
            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.start()
        except Exception as e:  # macOS 上多半是辅助功能权限问题
            print(f"⚠️  键盘监听启动失败：{e}", file=sys.stderr)
            print("   请在「系统设置 → 隐私与安全性 → 辅助功能」里允许终端/本程序。",
                  file=sys.stderr)

    def _handle_key(self, vk: int, down: bool) -> None:
        K = km.VK

        # --- 功能键 ---
        if down:
            if vk == K[km.KEY_QUIT]:
                self._quit_requested = True
                if self._quit_cb:
                    self._quit_cb()
                return
            if vk == K[km.KEY_OCTAVE_DOWN]:
                self.keymap.shift_octave(-1)
                return
            if vk == K[km.KEY_OCTAVE_UP]:
                self.keymap.shift_octave(1)
                return
            if vk == K[km.KEY_OCTAVE_RESET]:
                self.keymap.reset_octave()
                return
            if vk == K[km.KEY_BREATH]:
                self.breath = True
                return
            if vk == K[km.KEY_MELODY_TOGGLE]:
                self.toggle_melody()
                return
            if not self.using_real_sensor:
                if vk == K[km.KEY_SIM_OPEN]:
                    self.sensor.set_simulated(
                        self.sensor.latest_angle + self.ecfg.simulate_angle_step)
                    return
                if vk == K[km.KEY_SIM_CLOSE]:
                    self.sensor.set_simulated(
                        self.sensor.latest_angle - self.ecfg.simulate_angle_step)
                    return
        else:
            if vk == K[km.KEY_BREATH]:
                self.breath = False
                return

        # --- 盲弹模式：字母区任意键 → 旋律的下一个音 ---
        # 放在功能键之后、琴键之前：字母区和功能键不重叠，互不干扰。
        if self.melody_enabled and vk in km.LETTER_VKS:
            self._handle_melody_key(vk, down)
            return

        # --- 琴键 ---
        pitch = self.keymap.pitch_for(vk)
        if pitch is None:
            return
        if down:
            self.synth.note_on(pitch)
        else:
            self.synth.note_off(pitch)

    def _handle_melody_key(self, vk: int, down: bool) -> None:
        """盲弹模式的按键处理：按下推进旋律，松开收掉这个音。

        音高完全由乐谱决定，按哪个字母键都一样。同时只发一个音（单声部）：
        新按下的键会先掐掉上一个音，这样旋律线条是干净的。
        """
        player = self.melody_player
        if player is None:
            return

        if down:
            if vk in self._melody_held:
                return          # 按住不放时系统会重复触发，忽略掉
            for pitch in list(self._melody_held.values()):
                self.synth.note_off(pitch)
            self._melody_held.clear()

            step = player.advance()
            if step is None:    # 不循环且已到末尾
                return
            pitch, _note = step
            pitch += self.keymap.octave_shift     # [ ] 移调对盲弹一样生效
            self.synth.note_on(pitch)
            self._melody_held[vk] = pitch
        else:
            pitch = self._melody_held.pop(vk, None)
            if pitch is not None:
                self.synth.note_off(pitch)

    # ------------------------------------------------------------------
    # 状态显示
    # ------------------------------------------------------------------

    def _status_loop(self) -> None:
        while self._running.is_set():
            line = self.status_line()
            self.last_status = line
            sys.stdout.write("\r\x1b[K" + line[:200])
            sys.stdout.flush()
            time.sleep(self.ecfg.status_interval)

    def status_line(self) -> str:
        """拼一行状态。抽成独立方法是为了能脱离线程直接单测。"""
        st = self.state
        bar_len = 20
        filled = int(round(min(1.0, st.pressure) * bar_len))
        bar = "█" * filled + "·" * (bar_len - filled)
        arrow = {1: "推开 →", -1: "← 合拢", 0: "  静止"}[st.direction]
        notes = " ".join(km.note_name(p) for p in self.synth.active_pitches[:10])
        if self.melody_enabled and self.melody_player is not None:
            player = self.melody_player
            mode = f"盲弹《{player.melody.name}》{player.progress()}音"
        else:
            mode = "实体屏幕" if self.using_real_sensor else "模拟(↑↓)"
        return (f"[{mode}] 角度 {self.sensor.latest_angle:5.0f}°  "
                f"位置 {st.position:4.2f}  {arrow}  "
                f"气压 {bar} {st.pressure:4.2f}   "
                f"音 {notes or '—'}")

    # ------------------------------------------------------------------

    def run_until_quit(self) -> None:
        """跑到用户按 Esc（或 Ctrl-C）。"""
        stop = threading.Event()

        def _sigint(_s, _f):
            stop.set()

        old = signal.signal(signal.SIGINT, _sigint)
        self._quit_cb = stop.set
        try:
            while not stop.is_set() and not self._quit_requested:
                stop.wait(0.2)
        finally:
            signal.signal(signal.SIGINT, old)
            sys.stdout.write("\n")

    # ------------------------------------------------------------------

    def run_selftest(self, seconds: float = 5.0,
                     pitches: tuple[int, ...] = (60, 64, 67)) -> dict:
        """不用键盘、不用真人开合，跑一遍完整实时链路并返回统计。

        角度用正弦模拟（周期 1.6s，幅度 ±27°），中间按住一个和弦。
        用来验证：音频流不欠载、风箱模型在实时回调里正常工作、真的出声了。
        """
        import math

        # 强制走模拟角度，避免自检要求人去开合屏幕
        self.sensor._sensor = None
        self.sensor.set_simulated(85.0)
        self.start(enable_keyboard=False)
        self.reset_meter()

        t0 = time.monotonic()
        pressed = False
        try:
            while True:
                t = time.monotonic() - t0
                if t >= seconds:
                    break
                self.sensor.set_simulated(85.0 + 27.0 * math.sin(2 * math.pi * t / 1.6))
                should_press = 0.5 <= t < seconds - 0.5
                if should_press and not pressed:
                    for p in pitches:
                        self.synth.note_on(p)
                    pressed = True
                elif not should_press and pressed:
                    for p in pitches:
                        self.synth.note_off(p)
                    pressed = False
                time.sleep(1.0 / 120.0)
        finally:
            self.synth.all_notes_off()
            time.sleep(0.3)     # 留出释放尾音
            self.stop()

        expected = seconds * self.scfg.sample_rate / self.scfg.block_size
        return {
            "seconds": seconds,
            "callbacks": self.callback_count,
            "expected_callbacks": round(expected),
            "underflows": self.underflows,
            "peak": round(self.peak_out, 4),
            "rms": round(self.output_rms, 5),
            "samples": self._n_samples,
        }


# --------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="macaccordion",
        description="把 MacBook 的开盖角度当风箱、键盘当琴键。")
    p.add_argument("--no-keyboard", action="store_true",
                   help="不监听键盘（只看传感器与气压，用来排查）")
    p.add_argument("--seconds", type=float, default=None,
                   help="跑指定秒数后自动退出（自检用）")
    p.add_argument("--gain", type=float, default=None, help="总输出增益")
    p.add_argument("--detune", type=float, default=None,
                   help="簧片失谐量（音分）。0=齐奏，8~15=经典 Musette")
    p.add_argument("--reeds", type=int, default=None, help="簧片数 1/2/3")
    p.add_argument("--leak", type=float, default=None, help="漏气速率，越大越难攒气压")
    p.add_argument("--pos-base", type=float, default=None,
                   help="静止时的基础气息 0~1。0=停手就断音（最像真手风琴）")
    p.add_argument("--print-layout", action="store_true", help="打印键盘布局后退出")
    p.add_argument("--melody", action="store_true",
                   help="启动即进入盲弹模式：字母区任意键依次奏出预设乐曲")
    p.add_argument("--song", default="送别", metavar="NAME",
                   help="盲弹模式用哪首曲子（默认 送别）")
    p.add_argument("--melody-root", type=int, default=None, metavar="MIDI",
                   help="乐曲里 1 对应的 MIDI 音高（默认取乐谱调号，送别是 63=E♭4）")
    p.add_argument("--no-melody-loop", action="store_true",
                   help="盲弹到末尾就停住，不绕回开头")
    p.add_argument("--list-songs", action="store_true", help="列出可用的乐曲后退出")
    p.add_argument("--print-melody", action="store_true",
                   help="把乐曲还原成简谱打印出来（核对用）后退出")
    p.add_argument("--selftest", type=float, nargs="?", const=5.0, default=None,
                   metavar="SECONDS",
                   help="自检：用模拟角度跑一遍实时链路（会出声），默认 5 秒")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.list_songs:
        print("可用的乐曲：\n")
        for name, song in mel.SONGS.items():
            print(f"  {name}   {mel.summary(song)}")
        return 0

    if args.print_layout:
        k = km.Keymap()
        print("键位布局（C4 = 中央 C）：\n")
        for line in k.layout_lines():
            print("  " + line)
        print("\n  [ 降八度    ] 升八度    Tab 复位八度")
        print("  空格 呼吸（静音泄气）    ↑↓ 模拟开合    Esc 退出")
        print("  \\ 开关盲弹模式（字母区任意键 = 旋律的下一个音）")
        return 0

    try:
        song = mel.get_song(args.song)
    except KeyError as e:
        print(f"❌ {e.args[0]}", file=sys.stderr)
        return 2

    if args.print_melody:
        print(mel.summary(song))
        print()
        for line in mel.jianpu_lines(song):
            print(line)
        print("  记号：1^ = 高八度，7v = 低八度，括号里是拍数（没标的为 1 拍）")
        return 0

    scfg = SynthConfig()
    if args.gain is not None:
        scfg.master_gain = args.gain
    if args.detune is not None:
        scfg.detune_cents = args.detune
    if args.reeds is not None:
        scfg.reed_count = max(1, min(5, args.reeds))

    bcfg = BellowsConfig()
    if args.leak is not None:
        bcfg.leak_rate = args.leak
    if args.pos_base is not None:
        bcfg.pos_base = args.pos_base

    player = MelodyPlayer(song, root_pitch=args.melody_root,
                          loop=not args.no_melody_loop)
    engine = AccordionEngine(bellows_config=bcfg, synth_config=scfg,
                             melody=player if args.melody else None)

    if args.selftest is not None:
        print(f"自检：用模拟角度跑 {args.selftest:g} 秒实时链路（会出声）…")
        st = engine.run_selftest(seconds=args.selftest)
        print(f"  回调次数      {st['callbacks']}（预期约 {st['expected_callbacks']}）")
        print(f"  音频欠载      {st['underflows']} 次")
        print(f"  输出峰值      {st['peak']}")
        print(f"  输出 RMS      {st['rms']}")
        ok = (st["underflows"] == 0 and st["peak"] > 0.05
              and st["callbacks"] >= st["expected_callbacks"] * 0.9)
        print(f"\n{'✅ 通过' if ok else '❌ 未通过'}")
        return 0 if ok else 1

    print("MacAccordion 🪗")
    print(f"  传感器：{'实体屏幕角度传感器' if engine.using_real_sensor else '不可用 → 模拟模式（↑↓ 控制开合）'}")
    print(f"  音频：{scfg.sample_rate} Hz / {scfg.block_size} 样本一块 "
          f"（延迟约 {scfg.block_size / scfg.sample_rate * 1000:.1f} ms）")
    print(f"  音色：{scfg.reed_count} 簧片，失谐 {scfg.detune_cents:g} 音分")
    print(f"  乐曲：{mel.summary(song)}")
    if engine.melody_enabled:
        print("        盲弹模式【开】—— 字母区按任意键就出下一个音")
    else:
        print("        盲弹模式【关】—— 按 \\ 打开")
    print()
    for line in engine.keymap.layout_lines():
        print("  " + line)
    print("\n  [ 降八度  ] 升八度  Tab 复位  空格 呼吸  \\ 盲弹  Esc 退出\n")

    engine.start(enable_keyboard=not args.no_keyboard)
    try:
        if args.seconds:
            time.sleep(args.seconds)
        else:
            engine.run_until_quit()
    finally:
        engine.stop()
        print(f"回调 {engine.callback_count} 次，欠载 {engine.underflows} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
