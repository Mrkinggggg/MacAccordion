"""键位映射的单元测试。"""

import pytest

from macaccordion.keymap import (
    HIGH_BLACKS, HIGH_WHITES, LOW_BLACKS, LOW_WHITES, Keymap, VK, note_name,
)


def test_音域范围正确():
    assert LOW_WHITES == [60, 62, 64, 65, 67, 69, 71, 72, 74, 76]     # C4..E5
    assert LOW_BLACKS == [61, 63, 66, 68, 70, 73, 75]                 # C#4..D#5
    assert HIGH_WHITES == [72, 74, 76, 77, 79, 81, 83, 84, 86, 88]    # C5..E6
    assert HIGH_BLACKS == [73, 75, 78, 80, 82, 85, 87]                # C#5..D#6


def test_每个音域内琴键映射到唯一音高():
    """两组音域各自内部不能有重复键。

    两组之间**故意重叠** C5/D5/E5 —— 这样从低音区走到高音区不会断档，
    同一个音也有两个键位可选（便于连奏）。
    """
    km = Keymap()
    low = [km.base[VK[k]] for k in
           ["z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "a", "s", "f", "g", "h", "k", "l"]]
    high = [km.base[VK[k]] for k in
            ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "1", "2", "4", "5", "6", "8", "9"]]
    assert len(low) == len(set(low)) == 17
    assert len(high) == len(set(high)) == 17

    overlap = set(low) & set(high)
    assert overlap == {72, 73, 74, 75, 76}, "重叠的应该正好是 C5 到 E5 这 5 个音"

    # 并集必须是从 C4 到 E6 连续无缺口
    union = sorted(set(low) | set(high))
    assert union[0] == 60 and union[-1] == 88
    assert union == list(range(60, 89))


def test_琴键总数():
    km = Keymap()
    assert len(km.base) == 34, f"应该是 10+7 两组共 34 个键，实际 {len(km.base)}"


def test_低音区在高音区之下():
    """Z 行应该比 Q 行低一个八度，符合"往下 = 更低"的直觉。"""
    km = Keymap()
    assert km.pitch_for(VK["z"]) == 60
    assert km.pitch_for(VK["q"]) == 72


def test_黑键位置与钢琴几何一致():
    """E-F、B-C 之间没有黑键，对应位置应留空（3、7、D、J 不发声）。"""
    km = Keymap()
    for key in ("3", "7", "d", "j"):
        assert km.pitch_for(VK[key]) is None, f"{key} 应该是空位"


def test_功能键不是琴键():
    km = Keymap()
    for name in ("space", "tab", "esc", "up", "down", "[", "]", "-", "="):
        assert km.pitch_for(VK[name]) is None


def test_八度移调():
    km = Keymap()
    assert km.pitch_for(VK["q"]) == 72
    km.shift_octave(1)
    assert km.pitch_for(VK["q"]) == 84
    km.shift_octave(-2)
    assert km.pitch_for(VK["q"]) == 60
    km.reset_octave()
    assert km.pitch_for(VK["q"]) == 72


def test_移调超出音域时返回_None():
    km = Keymap(min_pitch=24, max_pitch=108)
    for _ in range(10):
        km.shift_octave(1)
    assert km.octave_shift == 24
    assert km.pitch_for(VK["p"]) is None      # E6 + 2 个八度超出上限


def test_移调幅度被限制():
    km = Keymap()
    for _ in range(100):
        km.shift_octave(1)
    assert km.octave_shift == 24
    for _ in range(100):
        km.shift_octave(-1)
    assert km.octave_shift == -24


def test_反查键名():
    km = Keymap()
    assert km.key_for(60) == "z"
    assert km.key_for(72) == "q"
    km.shift_octave(1)
    assert km.key_for(84) == "q"
    assert km.key_for(60) is None     # 移调后没有键对应 C4


def test_音名格式():
    assert note_name(60) == "C4"
    assert note_name(69) == "A4"
    assert note_name(61) == "C#4"
    assert note_name(88) == "E6"


def test_布局示意图能生成():
    lines = Keymap().layout_lines()
    assert len(lines) == 5
    assert any("C4" in l for l in lines)
    assert any("E6" in l for l in lines)
