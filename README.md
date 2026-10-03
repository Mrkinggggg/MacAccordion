# MacAccordion 🪗

把 MacBook 的开盖角度当风箱，把键盘当琴键。

屏幕推拉得越快，气压越高，声音越响；按住的和弦越多，气流被分摊得越多，要更用力推。

> **注意**：频繁开合会消耗铰链寿命。请用小行程（约 60°~100°）、从屏幕正中推、
> 匀速不要急停。详见[铰链寿命](#铰链寿命)。

---

## 快速开始

```bash
# 1. 依赖（建议先建虚拟环境，见下方"环境"）
pip install numpy sounddevice pynput PySide6

# 2. 图形界面：先选模式，再开弹
python3 -m macaccordion --gui

# 3. 命令行直接开弹
python3 -m macaccordion

# 4. 只看布局不开弹
python3 -m macaccordion --print-layout

# 5. 自检：用模拟角度跑一遍实时链路（会出声，约 5 秒）
python3 -m macaccordion --selftest

# 6. 盲弹模式：字母区随便按就能出《送别》
python3 -m macaccordion --melody
```

按 `Esc` 退出。

## 图形界面

```bash
python3 -m macaccordion --gui
```

分三步：

1. 启动页选专业模式或盲弹模式
2. 选盲弹的话，再挑一首乐谱，决定走完要不要绕回开头
3. 演奏页显示角度、气压、风向、正在响的音；盲弹时另有进度

演奏页每 80 ms 读一次引擎状态，音频与键盘逻辑和命令行模式共用同一套代码。
按 `Esc` 或点「停止并返回」回到启动页。

界面用 PySide6（Qt for Python），不装也能用命令行模式。乐谱列表直接读
`macaccordion/melody.py` 里的 `SONGS`，加一首就会出现在列表里。

## 键位

上下两组音域，黑键放在白键上一行的对应位置（`·` 是钢琴上 E-F / B-C 之间的空位）：

```
高音区黑键   1 C#5    2 D#5    ·      4 F#5    5 G#5    6 A#5    ·      8 C#6    9 D#6
高音区白键   Q C5   W D5   E E5   R F5   T G5   Y A5   U B5   I C6   O D6   P E6

低音区黑键   A C#4    S D#4    ·      F F#4    G G#4    H A#4    ·      K C#5    L D#5
低音区白键   Z C4   X D4   C E4   V F4   B G4   N A4   M B4   , C5   . D5   / E5
```

两组在 C5–E5 处故意重叠，所以从低音区走到高音区不会断档。

| 按键 | 作用 |
|---|---|
| `[` / `]` | 降 / 升一个八度（盲弹模式一样生效） |
| `Tab` | 八度复位 |
| `空格` | 呼吸（阀门全开、静音泄气，风箱照常走） |
| `\` | 开 / 关盲弹模式（打开时旋律回到第一个音） |
| `↑` / `↓` | 传感器不可用时，手动模拟开合 |
| `Esc` | 退出 |

按键按 macOS 虚拟键码识别，不是字符，所以中文输入法开着也能弹。

---

## 盲弹模式

正常模式下字母区每个键绑定固定音高，想弹出调子得先练指法。盲弹模式换了个思路：

> 音高由预设乐谱决定，按键只决定"什么时候响"。

字母区（A–Z 共 26 个键）随便按，按哪个键都一样：每按一下发出旋律的下一个音，
一直按下去整首曲子就走完了。节奏快慢由自己掌握。

```bash
# 启动即进入盲弹模式（默认曲子《送别》）
python -m macaccordion --melody

# 先看看乐谱长什么样
python -m macaccordion --print-melody
python -m macaccordion --list-songs
```

几个细节：

- 同时只发一个音，新按下的键会掐掉上一个音
- 按住不放不会连冲：系统会自动重复发送"按下"事件，程序忽略掉，必须真的抬起来再按
- 数字行和标点仍然是普通琴键，只有字母区触发旋律
- 音高完全由乐谱决定，所以不管按什么顺序，前 7 个按键出来的一定是乐谱的前 7 个音
- 走完一遍会自动绕回开头（`--no-melody-loop` 改成到末尾停住）
- 风箱照旧，不推屏幕一样没声

### 内置乐谱：《送别》

1 = ♭E，4/4 拍，80 BPM。第一段（长亭外…夕阳山外山）、第二段（天之涯…今宵别梦寒）、
第三段重复第一段，共 24 小节 88 个音。

乐谱在 `macaccordion/melody.py` 里直接用简谱字符串写着：

```python
_A1 = "5:1 3:0.5 5:0.5 1^:2 | 6:1 1^:0.5 6:0.5 5:2 | 5:1 1:0.5 2:0.5 3:1 2:0.5 1:0.5 | 2:3 0:1"
```

记法：`<音级>[^|v][:拍数]`，`^` 高八度、`v` 低八度、`0` 休止，小节用 `|` 分隔。
1 对应 MIDI 63（E♭4），全曲音域 62–75（D4–E♭5）。

> 乐谱取自出版简谱（jianpujia.com）。`tests/test_melody.py` 里独立写了一份同样的旋律做对照，
> 改错谱会立刻报错。

---

## 原理

### 1. 读角度（`sensor.py`）

传感器由 Apple SPU 管理，HID 标识 `0x05AC/0x8104`、`UsagePage 0x20 / Usage 0x8A`。
角度在 Feature Report ID 1 的第 1 个字节，`byte[2] != 0` 的帧是脏数据要丢掉。
用定向匹配的 `IOHIDManager` 打开即可，不需要 root。

> 注意：不能用 Input Report 回调。在 M3 MacBook Air 上它只有约 1 Hz，屏幕静止时还会在
> 359/0 之间乱跳，必须轮询 Feature Report。实测数据见 `docs/调研报告.md`。

### 2. 风箱模型（`bellows.py`）

一个储气罐：

```
平衡气压 = 进气量 / 耗气系数
dP/dt    = 一阶滞后（上升快 rise_tau、下降慢 fall_tau）

进气量 ∝ 角速度（推拉风箱） + 位置项（静止时开得大有一点基础气息）
耗气   ∝ 基础漏气 + 每个按住琴键的簧片耗气
```

于是自然得到：不动就没声、推得越快越响、和弦越多越难推。
角速度有 35 ms 平滑和 8°/s 死区，避免手抖出音。

### 3. 合成（`synth.py`）

- 多簧片失谐（Musette）：同一个音用 3 片簧片，彼此差 ±9 音分，这是手风琴音色的来源
- 用波表而不是逐谐波累加；波表按谐波数做 mip 分级（1/2/4/…/64），按基频选表避免混叠
- 音色明暗在"暗表"与"亮表"之间线性插值
- 音量由风箱气压驱动，按键只负责开阀门（12 ms 起 / 90 ms 落）
- 气声噪声随气流强度变化；没有按键按住时气声为零（阀门关着）

### 4. 线程模型（`engine.py`）

```
sensor 线程   ~60 Hz       轮询 Feature Report，只写 latest_angle
audio 线程    声卡驱动       读 latest_angle → 跑风箱 → 渲染一块（256 样本 ≈ 5.8 ms）
keyboard 线程 pynput        按键事件更新音符集合
```

风箱模型放在音频回调里而不是传感器线程里：回调本来就是等间隔触发的（172 Hz），
比 60 Hz 更均匀，也省掉一次跨线程同步。

---

## 调参

命令行：

```bash
python -m macaccordion --gain 0.5 --detune 14 --reeds 3 --leak 1.6 --pos-base 0
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--gain` | 0.42 | 总输出增益 |
| `--detune` | 9.0 | 簧片失谐（音分）。0 = 齐奏，8~15 = 经典 Musette，越大越抖 |
| `--reeds` | 3 | 簧片数 1/2/3 |
| `--leak` | 1.10 | 漏气速率。越大越难攒气压、越需要用力推 |
| `--pos-base` | 0.22 | 静止时的基础气息。设为 0 就是"停手立刻断音"，最像真手风琴 |
| `--melody` | 关 | 启动即进入盲弹模式 |
| `--song` | 送别 | 盲弹用哪首曲子 |
| `--melody-root` | 乐谱调号 | 乐谱里 1 对应的 MIDI 音高（送别是 63 = E♭4） |
| `--no-melody-loop` | — | 盲弹到末尾停住，不绕回开头 |

改代码里的默认值：`SynthConfig` / `BellowsConfig`（两个文件顶部，字段都有中文注释）。

---

## 离线工作流（省铰链）

调音色不该反复开合屏幕。可以先录一段真实轨迹，之后离线反复渲染：

```bash
# 1) 录一段 12 秒的真实开合轨迹
python scripts/record_and_render.py record -t 12 -o _pipeline/take1.csv

# 2) 之后反复渲染，改任何参数都不碰屏幕
python scripts/record_and_render.py render -i _pipeline/take1.csv --leak 1.6
python scripts/record_and_render.py render -i _pipeline/take1.csv --detune 14 --reeds 2
```

`scripts/render_demo.py` 用内置轨迹直接出一版演示曲，不需要硬件：

```bash
python scripts/render_demo.py -o _pipeline/demo.wav
```

---

## 环境

建议用虚拟环境隔离依赖，不污染系统 Python：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install numpy sounddevice pynput PySide6 pytest
```

- Python 3.13+
- `numpy` 实时合成
- `sounddevice` 音频输出（wheel 自带 PortAudio，不需要 brew）
- `pynput` 键盘监听（需要「辅助功能」权限）
- `PySide6` 图形界面（只跑命令行模式的话可以不装）
- `pytest` 仅测试用

跑测试：

```bash
python -m pytest tests/ -q      # 123 项
```

---

## 项目结构

```
macaccordion/
  sensor.py     IOKit 读铰链角度（纯 ctypes，零第三方依赖）
  bellows.py    风箱模型：角度 → 气压
  synth.py      波表合成：多簧片失谐 + 气声
  keymap.py     键位 → 音高（按虚拟键码，含布局示意图）
  melody.py     简谱乐谱 + 按键推进旋律
  engine.py     实时引擎（音频回调 + 键盘监听 + 状态栏）
  gui.py        图形界面（模式选择 / 乐谱选择 / 演奏）
  render.py     离线渲染（不依赖声卡）
_probe/         立项时的传感器逆向探针（诊断用）
docs/调研报告.md  调研记录：已有开源项目盘点 + 实测数据
scripts/        演示渲染、录制+离线渲染
tests/          单元测试
_pipeline/      临时产物（WAV、录制的轨迹）
```

---

## 铰链寿命

实测与调研结论（完整推导见 `docs/调研报告.md`）：

- Apple 不公布 MacBook 铰链循环次数指标，行业惯例是 2 万~3 万次全行程开合
- 更合理的度量是累计扫过的总角度：常规使用约 450°/天，5 年约 82 万°，占额定（约 325 万°）的 25%
- 演奏按 30 次/分 × 35° 行程约 63,000°/小时，即中度演奏 1 小时相当于常规使用 4~5 个月的铰链行程量
- 幅度和速度决定一切：20 次/分 × 20° 能撑约 100 小时；30 次/分 × 130° 全行程只剩约 10 小时

所以：

1. 用小行程演奏（60°~100° 区间，单次 20~30°），别每次拉满
2. 从屏幕正中推，不要捏一角（单侧扭矩会加速磨损并让机身变形）
3. 匀速、端点轻柔，别急停急返
4. 大部分时间用 `↑↓` 模拟模式调参和练手感，只在真正演奏时才开合
5. 出现「任意角度停不住/下垂、异响、左右缝隙不均」就停手

传感器本身是非接触式的，不会因为开合次数磨损，会坏的是机械铰链。
