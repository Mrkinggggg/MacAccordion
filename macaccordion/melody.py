"""盲弹模式：把键盘变成"按一下、往下唱一个音"的旋律推进器。

设计意图
--------
正常琴键模式里，字母区每个键绑定一个固定音高，想弹出曲子得先练指法。
盲弹模式换了个思路：

    音高由**预设乐谱**决定，按键只决定"什么时候响"。

按一下字母区的任意键（按哪个都一样），就发出旋律的下一个音；一直按下去，
整首曲子就顺着走完了。节奏快慢完全由自己掌握，听上去却是在正经演奏。

乐谱记法
--------
源码里直接用简谱字符串写，方便对着原谱逐字核对::

    <音级>[^|v][:拍数]

    5        音级 5，一拍
    3:0.5    音级 3，半拍
    1^:2     高八度的 1，两拍
    7v:0.5   低八度的 7，半拍
    0:1      休止一拍

小节之间用 ``|`` 分隔。每个小节的拍数之和必须等于 4（4/4 拍）。

关于八度记号
------------
``^`` = 高八度（简谱里数字上方加点），``v`` = 低八度（数字下方加点）。
《送别》里只有两处用到：``古道边``/``笛声残`` 的 1 是高八度 ``1^``；
``夕阳山外山``/``今宵别梦寒`` 的 7 是低八度 ``7v``。
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------- 音高

# 大调音阶里 1..7 相对主音的半音数（自然大调，无升降号）
MAJOR_SEMITONES: tuple[int, ...] = (0, 2, 4, 5, 7, 9, 11)

# 音名 → "1" 这个音所在的 MIDI 音高（以 4 为基准八度）
ROOT_PITCHES: dict[str, int] = {
    "C": 60, "Db": 61, "D": 62, "Eb": 63, "E": 64, "F": 65,
    "Gb": 66, "G": 67, "Ab": 68, "A": 69, "Bb": 70, "B": 71,
}


@dataclass(frozen=True)
class Note:
    """简谱上的一个音。"""

    degree: int
    """音级 1..7；0 表示休止。"""

    octave: int = 0
    """八度偏移：-1 低八度 / 0 中音区 / +1 高八度。"""

    beats: float = 1.0
    """时值（拍）。4/4 拍下每小节合计 4 拍。"""

    @property
    def is_rest(self) -> bool:
        return self.degree == 0

    def semitone(self) -> int:
        """相对主音的半音数（休止返回 0）。"""
        if self.is_rest:
            return 0
        return MAJOR_SEMITONES[self.degree - 1] + 12 * self.octave

    def label(self) -> str:
        """还原成简谱写法，用于打印核对。"""
        if self.is_rest:
            return "0"
        mark = "^" if self.octave > 0 else ("v" if self.octave < 0 else "")
        return f"{self.degree}{mark}"


# --------------------------------------------------------------------- 解析


def parse_bar(text: str) -> tuple[Note, ...]:
    """解析一个小节，如 ``"5:1 3:0.5 5:0.5 1^:2"``。"""
    out: list[Note] = []
    for token in text.split():
        head, _, tail = token.partition(":")
        beats = float(tail) if tail else 1.0
        octave = 0
        if head.endswith("^"):
            octave, head = 1, head[:-1]
        elif head.endswith("v"):
            octave, head = -1, head[:-1]
        out.append(Note(int(head), octave, beats))
    return tuple(out)


def parse_phrase(text: str) -> tuple[tuple[Note, ...], ...]:
    """解析一整句，``|`` 分隔小节。"""
    return tuple(parse_bar(bar) for bar in text.split("|"))


# --------------------------------------------------------------------- 乐谱


@dataclass(frozen=True)
class Phrase:
    """一个乐句（通常是谱面上的一行）。"""

    label: str
    lyric: str
    bars: tuple[tuple[Note, ...], ...]


@dataclass(frozen=True)
class Melody:
    """一首固定乐曲。"""

    name: str
    key_root: str
    """简谱开头的 1 对应哪个音名，如 ``"Eb"``。"""

    bpm: int
    phrases: tuple[Phrase, ...]

    @property
    def notes(self) -> tuple[Note, ...]:
        """展平后的旋律，**不含休止**——盲弹模式实际依次发出的音就是这些。"""
        return tuple(n for p in self.phrases for bar in p.bars
                     for n in bar if not n.is_rest)

    @property
    def n_bars(self) -> int:
        return sum(len(p.bars) for p in self.phrases)

    @property
    def n_beats(self) -> float:
        return sum(n.beats for p in self.phrases for bar in p.bars for n in bar)

    def root_pitch(self) -> int:
        return ROOT_PITCHES[self.key_root]


# 《送别》——李叔同填词，J.P. Ordway 曲。1 = ♭E，4/4 拍。
#
# 取自出版简谱（jianpujia.com），已逐小节按像素核对过音符与八度记号。
# 结构：第一段（长亭外…夕阳山外山）→ 第二段（天之涯…今宵别梦寒）
#       → 第三段（重复第一段）。前奏 4 小节未计入。
_A1 = "5:1 3:0.5 5:0.5 1^:2 | 6:1 1^:0.5 6:0.5 5:2 | 5:1 1:0.5 2:0.5 3:1 2:0.5 1:0.5 | 2:3 0:1"
_A2 = "5:1 3:0.5 5:0.5 1^:1.5 7:0.5 | 6:1 1^:1 5:2 | 5:1 2:0.5 3:0.5 4:1.5 7v:0.5 | 1:3 0:1"
_B1 = ("6:1 1^:1 1^:2 | 7:1 6:0.5 7:0.5 1^:2 | "
       "6:0.5 7:0.5 1^:0.5 6:0.5 6:0.5 5:0.5 3:0.5 1:0.5 | 2:3 0:1")

SONGBIE = Melody(
    name="送别",
    key_root="Eb",
    bpm=80,
    phrases=(
        Phrase("第一段·上句", "长亭外，古道边，芳草碧连天", parse_phrase(_A1)),
        Phrase("第一段·下句", "晚风拂柳笛声残，夕阳山外山", parse_phrase(_A2)),
        Phrase("第二段·上句", "天之涯，地之角，知交半零落", parse_phrase(_B1)),
        Phrase("第二段·下句", "一觚浊酒尽余欢，今宵别梦寒", parse_phrase(_A2)),
        Phrase("第三段·上句", "长亭外，古道边，芳草碧连天", parse_phrase(_A1)),
        Phrase("第三段·下句", "晚风拂柳笛声残，夕阳山外山", parse_phrase(_A2)),
    ),
)

SONGS: dict[str, Melody] = {SONGBIE.name: SONGBIE}


def get_song(name: str) -> Melody:
    """按名字取乐谱，名字不认识时抛 KeyError 并列出可选项。"""
    try:
        return SONGS[name]
    except KeyError:
        raise KeyError(f"没有这首曲子：{name}（可选：{'、'.join(SONGS)}）") from None


# --------------------------------------------------------------------- 打印


def jianpu_lines(melody: Melody, beats_width: int = 5) -> list[str]:
    """把乐谱还原成简谱文本，用于人工核对。

    每个音占固定宽度，八度记号写在音级后面，时值不足一拍时用 ``.`` 标出来，
    这样一眼就能和原谱对上。
    """
    lines: list[str] = []
    for phrase in melody.phrases:
        lines.append(f"  {phrase.label}　{phrase.lyric}")
        for bar in phrase.bars:
            cells: list[str] = []
            for note in bar:
                cell = note.label()
                if not note.is_rest and note.beats != 1.0:
                    cell += f"({note.beats:g})"
                cells.append(f"{cell:^{beats_width}}")
            total = sum(n.beats for n in bar)
            lines.append("  |" + "|".join([" ".join(cells), f" {total:g}拍"]) + "|")
        lines.append("")
    return lines


def summary(melody: Melody) -> str:
    """一行摘要，启动时打印。"""
    key = melody.key_root.replace("b", "♭")
    return (f"《{melody.name}》 1={key}  {melody.bpm} BPM  "
            f"{melody.n_bars} 小节 / {len(melody.notes)} 个音 / "
            f"{melody.n_beats:g} 拍")


# --------------------------------------------------------------------- 播放器


class MelodyPlayer:
    """按顺序吐出旋律的音，供盲弹模式使用。

    每次 :meth:`advance` 前进一个音并返回它的 MIDI 音高。走到头之后，
    ``loop=True`` 会绕回开头，否则停在末尾并返回 ``None``。
    """

    def __init__(self, melody: Melody, root_pitch: int | None = None,
                 loop: bool = True) -> None:
        self.melody = melody
        self.root_pitch = melody.root_pitch() if root_pitch is None else int(root_pitch)
        self.loop = loop
        self.index = 0
        self.last: Note | None = None
        self._notes = melody.notes

    # ------------------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self._notes)

    @property
    def finished(self) -> bool:
        return self.index >= self.total

    def pitch_of(self, note: Note) -> int:
        """音符 → MIDI 音高。"""
        return self.root_pitch + note.semitone()

    def peek(self) -> Note | None:
        """看下一个音但不前进。"""
        if self.index >= self.total:
            return self._notes[0] if self.loop and self.total else None
        return self._notes[self.index]

    def advance(self) -> tuple[int, Note] | None:
        """前进到下一个音，返回 ``(MIDI 音高, 音符)``；到头且不循环时返回 None。"""
        if self.index >= self.total:
            if not self.loop or not self.total:
                return None
            self.index = 0
        note = self._notes[self.index]
        self.index += 1
        self.last = note
        return self.pitch_of(note), note

    def reset(self) -> None:
        self.index = 0
        self.last = None

    def progress(self) -> str:
        return f"{self.index}/{self.total}"
