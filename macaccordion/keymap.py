r"""键位映射：把 Mac 键盘变成两排钢琴键。

布局（上下两排各覆盖一个 C..E 的音域，黑键放在白键上一行的对应位置）::

    数字行   1 2 · 4 5 6 · 8 9        高音区黑键
    Q 行     Q W E R T Y U I O P      高音区白键  C5..E6
    A 行     A S · F G H · K L        低音区黑键
    Z 行     Z X C V B N M , . /      低音区白键  C4..E5

    ·   = 刻意留空，对应钢琴上 E-F、B-C 之间没有黑键的位置

**按键按 macOS 虚拟键码（virtual keycode）识别，而不是字符**。
这样中文输入法开着也照样能弹（字符会被输入法改写，键码不会）。

音域移调：`[` 降八度、`]` 升八度、Tab 复位。
盲弹模式开关：`\`（详见 :mod:`macaccordion.melody`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------- 音高

# MIDI 音高：C4 = 60
_WHITE_SEMITONES = [0, 2, 4, 5, 7, 9, 11]          # C D E F G A B
_BLACK_SEMITONES = [1, 3, None, 6, 8, 10, None]    # C# D# (无) F# G# A# (无)


def _range_pitches(root: int) -> tuple[list[int], list[int]]:
    """返回 (白键音高, 黑键音高)，覆盖 root 起的一个八度 + 到 E。

    root = C 的 MIDI 音高。白键 10 个（C..E），黑键 7 个。
    """
    whites: list[int] = []
    blacks: list[int] = []
    for octave in (0, 12):
        for i, semi in enumerate(_WHITE_SEMITONES):
            whites.append(root + octave + semi)
            black = _BLACK_SEMITONES[i]
            if black is not None and len(blacks) < 7:
                blacks.append(root + octave + black)
    # whites: C D E F G A B C D E (10)，blacks: C# D# F# G# A# C# D# (7)
    return whites[:10], blacks[:7]


LOW_WHITES, LOW_BLACKS = _range_pitches(60)    # C4..E5
HIGH_WHITES, HIGH_BLACKS = _range_pitches(72)  # C5..E6

# --------------------------------------------------------------------- 键码

# macOS 虚拟键码
VK = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B,
    "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17,
    "=": 0x18, "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D,
    "]": 0x1E, "o": 0x1F, "u": 0x20, "[": 0x21, "i": 0x22, "p": 0x23,
    "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29, "\\": 0x2A,
    ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E, ".": 0x2F,
    "tab": 0x30, "space": 0x31, "esc": 0x35,
    "up": 0x7E, "down": 0x7D, "left": 0x7B, "right": 0x7C,
}

# 低音区（Z 行白键 / A 行黑键）
_LOW_WHITE_KEYS = ["z", "x", "c", "v", "b", "n", "m", ",", ".", "/"]
_LOW_BLACK_KEYS = ["a", "s", "f", "g", "h", "k", "l"]

# 高音区（Q 行白键 / 数字行黑键）
_HIGH_WHITE_KEYS = ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"]
_HIGH_BLACK_KEYS = ["1", "2", "4", "5", "6", "8", "9"]

# 字母区：A–Z 共 26 个键。盲弹模式下这一整片区域都当作"下一个音"的触发器。
LETTER_KEYS = [
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
]
LETTER_VKS: frozenset[int] = frozenset(VK[k] for k in LETTER_KEYS)

# 功能键
KEY_OCTAVE_DOWN = "["
KEY_OCTAVE_UP = "]"
KEY_OCTAVE_RESET = "tab"
KEY_BREATH = "space"       # 呼吸（静音泄气）
KEY_QUIT = "esc"
KEY_MELODY_TOGGLE = "\\"   # 开/关盲弹模式（打开时旋律回到第一个音）
KEY_SIM_OPEN = "up"        # 模拟模式下增加角度
KEY_SIM_CLOSE = "down"     # 模拟模式下减小角度


@dataclass
class Keymap:
    """键码 → 音高的映射，支持八度移调。"""

    octave_shift: int = 0
    min_pitch: int = 24
    max_pitch: int = 108

    base: dict[int, int] = field(init=False, repr=False)
    reverse: dict[int, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        base: dict[int, int] = {}
        for key, pitch in zip(_LOW_WHITE_KEYS, LOW_WHITES):
            base[VK[key]] = pitch
        for key, pitch in zip(_LOW_BLACK_KEYS, LOW_BLACKS):
            base[VK[key]] = pitch
        for key, pitch in zip(_HIGH_WHITE_KEYS, HIGH_WHITES):
            base[VK[key]] = pitch
        for key, pitch in zip(_HIGH_BLACK_KEYS, HIGH_BLACKS):
            base[VK[key]] = pitch
        self.base = base
        self.reverse = {p: k for k, p in base.items()}

    # ------------------------------------------------------------------

    def pitch_for(self, vk: int) -> int | None:
        """键码 → MIDI 音高（已应用移调）。非琴键返回 None。"""
        base = self.base.get(vk)
        if base is None:
            return None
        pitch = base + self.octave_shift
        if not (self.min_pitch <= pitch <= self.max_pitch):
            return None
        return pitch

    def key_for(self, pitch: int) -> str | None:
        """音高 → 键名（用于界面显示）。"""
        vk = self.reverse.get(pitch - self.octave_shift)
        if vk is None:
            return None
        for name, code in VK.items():
            if code == vk:
                return name
        return None

    # ------------------------------------------------------------------

    def shift_octave(self, delta: int) -> None:
        self.octave_shift = int(max(-24, min(24, self.octave_shift + delta * 12)))

    def reset_octave(self) -> None:
        self.octave_shift = 0

    # ------------------------------------------------------------------

    def layout_lines(self) -> list[str]:
        """返回可直接打印的键盘布局示意图。"""
        def row(keys: list[str], pitches: list[int]) -> str:
            cells = []
            for k, p in zip(keys, pitches):
                name = _NOTE_NAMES[(p + self.octave_shift) % 12]
                octave = (p + self.octave_shift) // 12 - 1
                cells.append(f"{k.upper():>2} {name}{octave}")
            return "  ".join(cells)

        def row_with_gaps(keys: list[str], pitches: list[int]) -> str:
            cells = []
            for k, p in zip(keys, pitches):
                name = _NOTE_NAMES[(p + self.octave_shift) % 12]
                octave = (p + self.octave_shift) // 12 - 1
                cells.append(f"{k.upper():>2} {name}{octave}")
            return "   ".join(cells)

        lines = [
            "高音区黑键  " + row_with_gaps(_HIGH_BLACK_KEYS, HIGH_BLACKS),
            "高音区白键  " + row(_HIGH_WHITE_KEYS, HIGH_WHITES),
            "",
            "低音区黑键  " + row_with_gaps(_LOW_BLACK_KEYS, LOW_BLACKS),
            "低音区白键  " + row(_LOW_WHITE_KEYS, LOW_WHITES),
        ]
        return lines


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(pitch: int) -> str:
    """MIDI 音高 → 音名（如 60 → C4）。"""
    return f"{_NOTE_NAMES[pitch % 12]}{(pitch // 12) - 1}"
