"""盲弹模式（乐谱 + 按键推进）的单元测试。

这里的重点不是"代码跑得通"，而是**旋律本身和原谱对不对得上**：
下面把出版简谱《送别》的音级序列独立写了一遍，和 ``melody.SONGBIE`` 对比。
如果哪天有人改了乐谱字符串，这些测试会立刻发现。
"""

import pytest

from macaccordion import keymap as km
from macaccordion import melody as mel
from macaccordion.melody import MelodyPlayer, Note, parse_bar, parse_phrase


# --------------------------------------------------------------------- 乐谱核对
#
# 出版简谱（jianpujia.com，1=♭E 4/4）逐小节转写。元组是 (音级, 八度偏移)。
# 八度偏移 1 = 高八度（简谱上加点的 1̇），-1 = 低八度（下加点的 7̣）。

_A1 = [(5, 0), (3, 0), (5, 0), (1, 1),
       (6, 0), (1, 1), (6, 0), (5, 0),
       (5, 0), (1, 0), (2, 0), (3, 0), (2, 0), (1, 0),
       (2, 0)]

_A2 = [(5, 0), (3, 0), (5, 0), (1, 1), (7, 0),
       (6, 0), (1, 1), (5, 0),
       (5, 0), (2, 0), (3, 0), (4, 0), (7, -1),
       (1, 0)]

_B1 = [(6, 0), (1, 1), (1, 1),
       (7, 0), (6, 0), (7, 0), (1, 1),
       (6, 0), (7, 0), (1, 1), (6, 0), (6, 0), (5, 0), (3, 0), (1, 0),
       (2, 0)]


def _pairs(notes):
    return [(n.degree, n.octave) for n in notes]


def _phrase_pairs(phrase):
    """整个乐句展平成 (音级, 八度) 列表，休止不计。"""
    return _pairs([n for bar in phrase.bars for n in bar if not n.is_rest])


def test_送别共八十八个音():
    assert len(mel.SONGBIE.notes) == 88


def test_送别结构与出版谱一致():
    phrases = mel.SONGBIE.phrases
    assert [p.label for p in phrases] == [
        "第一段·上句", "第一段·下句", "第二段·上句",
        "第二段·下句", "第三段·上句", "第三段·下句",
    ]
    assert _phrase_pairs(phrases[0]) == _A1
    assert _phrase_pairs(phrases[1]) == _A2
    assert _phrase_pairs(phrases[2]) == _B1
    assert _phrase_pairs(phrases[3]) == _A2, "第二段下句和第一段下句同旋律"
    assert _phrase_pairs(phrases[4]) == _A1, "第三段重复第一段"
    assert _phrase_pairs(phrases[5]) == _A2


def test_关键乐句逐音核对():
    """抽几个容易记错的乐句单独钉住。"""
    phrases = mel.SONGBIE.phrases
    # 古道边 —— "道"是一字两音 1̇→6，出版谱上有连音线
    assert _pairs(phrases[0].bars[1])[:4] == [(6, 0), (1, 1), (6, 0), (5, 0)]
    # 知交半零落 —— "交"同样是一字两音
    assert _pairs(phrases[2].bars[2])[:3] == [(6, 0), (7, 0), (1, 1)]
    # 夕阳山外山 —— 结尾的 7 是低八度
    assert _pairs(phrases[1].bars[2]) == [(5, 0), (2, 0), (3, 0), (4, 0), (7, -1)]
    # 天之涯 —— 6 → 高八度 1 → 高八度 1
    assert _pairs(phrases[2].bars[0]) == [(6, 0), (1, 1), (1, 1)]


def test_每小节都是四拍():
    for phrase in mel.SONGBIE.phrases:
        for i, bar in enumerate(phrase.bars):
            total = sum(n.beats for n in bar)
            assert total == pytest.approx(4.0), \
                f"{phrase.label} 第 {i + 1} 小节是 {total} 拍，应为 4 拍"


def test_只有每句末小节有休止():
    for phrase in mel.SONGBIE.phrases:
        for i, bar in enumerate(phrase.bars):
            rests = [n for n in bar if n.is_rest]
            if i == len(phrase.bars) - 1:
                assert len(rests) == 1 and rests[0].beats == 1.0
            else:
                assert not rests, f"{phrase.label} 第 {i + 1} 小节不该有休止"


def test_小节数与总拍数():
    assert mel.SONGBIE.n_bars == 24
    assert mel.SONGBIE.n_beats == pytest.approx(96.0)
    assert mel.SONGBIE.key_root == "Eb"
    assert mel.SONGBIE.root_pitch() == 63      # E♭4


# --------------------------------------------------------------------- 记法解析


def test_解析小节():
    assert parse_bar("5:1 3:0.5 5:0.5 1^:2") == (
        Note(5, 0, 1.0), Note(3, 0, 0.5), Note(5, 0, 0.5), Note(1, 1, 2.0))


def test_解析低八度与休止():
    assert parse_bar("7v:0.5") == (Note(7, -1, 0.5),)
    assert parse_bar("0:1") == (Note(0, 0, 1.0),)
    assert parse_bar("0:1")[0].is_rest


def test_省略时值默认一拍():
    assert parse_bar("5 6 7") == (Note(5), Note(6), Note(7))


def test_解析整句按竖线切小节():
    bars = parse_phrase("5 | 6 | 7")
    assert len(bars) == 3
    assert all(len(b) == 1 for b in bars)


def test_还原成简谱文本():
    assert Note(1, 1).label() == "1^"
    assert Note(7, -1).label() == "7v"
    assert Note(5).label() == "5"
    assert Note(0).label() == "0"


def test_简谱打印不报错():
    lines = mel.jianpu_lines(mel.SONGBIE)
    assert any("长亭外" in l for l in lines)
    assert any("4拍" in l for l in lines)


# --------------------------------------------------------------------- 音高映射


def test_音级到音高():
    player = MelodyPlayer(mel.SONGBIE)
    assert player.root_pitch == 63
    assert player.pitch_of(Note(1)) == 63          # E♭4
    assert player.pitch_of(Note(3)) == 67          # G4
    assert player.pitch_of(Note(4)) == 68          # A♭4
    assert player.pitch_of(Note(5)) == 70          # B♭4
    assert player.pitch_of(Note(6)) == 72          # C5
    assert player.pitch_of(Note(1, 1)) == 75       # E♭5
    assert player.pitch_of(Note(7, -1)) == 62      # D4


def test_音域落在舒适区():
    pitches = [MelodyPlayer(mel.SONGBIE).pitch_of(n) for n in mel.SONGBIE.notes]
    assert min(pitches) == 62 and max(pitches) == 75


def test_可以指定根音():
    player = MelodyPlayer(mel.SONGBIE, root_pitch=51)
    assert player.pitch_of(Note(1)) == 51


def test_摘要可读():
    text = mel.summary(mel.SONGBIE)
    assert "送别" in text and "88 个音" in text and "24 小节" in text


def test_取谱与错误提示():
    assert mel.get_song("送别") is mel.SONGBIE
    with pytest.raises(KeyError, match="没有这首曲子"):
        mel.get_song("不存在的曲子")


# --------------------------------------------------------------------- 播放器


def test_按顺序前进():
    player = MelodyPlayer(mel.SONGBIE)
    assert player.total == 88
    first = player.advance()
    assert first == (70, Note(5, 0, 1.0))          # 第一个音是 5
    second = player.advance()
    assert second[0] == 67                          # 第二个音是 3
    assert player.index == 2


def test_从头到尾不重不漏():
    player = MelodyPlayer(mel.SONGBIE)
    seen = [player.advance()[1] for _ in range(player.total)]
    assert seen == list(mel.SONGBIE.notes)
    assert player.finished


def test_走到末尾会绕回开头():
    player = MelodyPlayer(mel.SONGBIE)
    for _ in range(player.total):
        player.advance()
    assert player.finished
    assert player.advance() == (70, Note(5, 0, 1.0))
    assert player.index == 1


def test_不循环时末尾返回_None():
    player = MelodyPlayer(mel.SONGBIE, loop=False)
    for _ in range(player.total):
        player.advance()
    assert player.advance() is None
    assert player.advance() is None


def test_reset_回到开头():
    player = MelodyPlayer(mel.SONGBIE)
    for _ in range(10):
        player.advance()
    player.reset()
    assert player.index == 0
    assert player.last is None
    assert player.advance()[0] == 70


def test_peek_不前进():
    player = MelodyPlayer(mel.SONGBIE)
    assert player.peek() == Note(5, 0, 1.0)
    assert player.index == 0
    assert player.progress() == "0/88"


# --------------------------------------------------------------------- 字母区


def test_字母区正好二十六个键():
    assert len(km.LETTER_KEYS) == 26
    assert sorted(km.LETTER_KEYS) == [chr(c) for c in range(ord("a"), ord("z") + 1)]
    assert len(km.LETTER_VKS) == 26


def test_字母区不含数字与功能键():
    taken = set(km.LETTER_VKS)
    for name in ("1", "2", "4", "5", "6", "8", "9", "space", "tab", "esc",
                 "up", "down", "[", "]", "\\", ",", ".", "/"):
        assert km.VK[name] not in taken, f"{name} 不该算字母区"


def test_盲弹开关键不是琴键():
    assert km.Keymap().pitch_for(km.VK[km.KEY_MELODY_TOGGLE]) is None
