#!/usr/bin/env python3
"""渲染内置演示曲到 WAV，用来试听音色和风箱手感。

    python scripts/render_demo.py                    # 默认输出到 _pipeline/demo.wav
    python scripts/render_demo.py -o /tmp/x.wav
    python scripts/render_demo.py --detune 14 --reeds 3 --leak 1.6
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macaccordion.bellows import BellowsConfig          # noqa: E402
from macaccordion.render import demo, render            # noqa: E402
from macaccordion.synth import SynthConfig              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "_pipeline", "demo.wav"))
    ap.add_argument("--detune", type=float, default=9.0, help="簧片失谐量（音分）")
    ap.add_argument("--reeds", type=int, default=3, help="簧片数 1/2/3")
    ap.add_argument("--gain", type=float, default=0.42, help="总输出增益")
    ap.add_argument("--leak", type=float, default=1.10, help="漏气速率")
    ap.add_argument("--pos-base", type=float, default=0.22,
                    help="静止时的基础气息；0 = 停手就断音")
    ap.add_argument("--normalize", type=float, default=None,
                    help="按给定峰值归一化（默认不归一化，保留真实动态）")
    args = ap.parse_args()

    scfg = SynthConfig(master_gain=args.gain, detune_cents=args.detune,
                       reed_count=max(1, min(5, args.reeds)))
    bcfg = BellowsConfig(leak_rate=args.leak, pos_base=args.pos_base)

    track, events, duration = demo()
    result = render(track, events, duration=duration,
                    bellows_config=bcfg, synth_config=scfg)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    result.write_wav(args.out, normalize=args.normalize)

    st = result.stats()
    print(f"已写入 {args.out}")
    print(f"  时长 {st['duration_s']}s  峰值 {st['peak']}  RMS {st['rms']}")
    print(f"  削波 {st['clipped']} 样本   NaN {st['nan']} 样本")
    pressures = [p for _, p, _ in result.bellows_trace]
    if pressures:
        print(f"  气压范围 {min(pressures):.2f} ~ {max(pressures):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
