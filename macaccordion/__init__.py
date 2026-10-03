"""MacAccordion —— 把 MacBook 的开盖角度当风箱、键盘当琴键的虚拟手风琴。

模块划分：
    sensor   读取铰链角度（IOKit HID）
    bellows  风箱模型：角度 → 气压
    synth    实时合成：多簧片 + 气声
    keymap   键位 → 音高映射
    melody   盲弹模式：预设乐谱 + 按键推进旋律
    engine   实时引擎（音频回调 + 键盘监听）
    gui      图形界面（模式选择 / 乐谱选择 / 演奏）
    render   离线渲染（不依赖声卡）
"""

__version__ = "0.1.0"
