#!/usr/bin/env python3
"""录一段真实的开合轨迹，之后就能离线反复渲染、反复调参。

这个脚本存在的意义：**铰链寿命是有限资源**（见 docs/调研报告.md 与铰链寿命结论）。
把开合动作录一次存成 CSV，后面调音色、调风箱参数全部离线跑，不用再折腾铰链。

    # 1) 录 12 秒：按提示缓慢开合屏幕
    python scripts/record_and_render.py record -t 12 -o _pipeline/take1.csv

    # 2) 用录好的轨迹反复渲染，随便改参数
    python scripts/record_and_render.py render -i _pipeline/take1.csv --leak 1.6
    python scripts/record_and_render.py render -i _pipeline/take1.csv --detune 14
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macaccordion.bellows import BellowsConfig          # noqa: E402
from macaccordion.render import AngleTrack, chord, note, record_angle_track, render  # noqa: E402
from macaccordion.synth import SynthConfig              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cmd_record(args) -> int:
    out = args.out or os.path.join(ROOT, "_pipeline", "take.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    print(f"准备录 {args.seconds:g} 秒。请缓慢、匀速地开合屏幕，重复 2~3 次。")
    print("提示：用小行程（约 60°~100°）就好，不用拉到最大角。\n")
    for i in (3, 2, 1):
        print(f"  {i}…", flush=True)
        time.sleep(1.0)
    print("  开始！\n", flush=True)

    def progress(t: float, angle: float) -> None:
        bar_len = 24
        filled = int(min(1.0, max(0.0, angle / 135.0)) * bar_len)
        sys.stdout.write(f"\r  {t:5.1f}s  角度 {angle:5.0f}°  [{'█' * filled}{'·' * (bar_len - filled)}]")
        sys.stdout.flush()

    track = record_angle_track(args.seconds, rate_hz=60.0, progress=progress)
    track.save_csv(out)
    print(f"\n\n已保存 {len(track)} 个采样点到 {out}")
    return 0


def cmd_render(args) -> int:
    track = AngleTrack.from_csv(args.input)
    out = args.out or os.path.join(ROOT, "_pipeline", "from_take.wav")

    scfg = SynthConfig(master_gain=args.gain, detune_cents=args.detune,
                       reed_count=max(1, min(5, args.reeds)))
    bcfg = BellowsConfig(leak_rate=args.leak, pos_base=args.pos_base)

    duration = track.duration + 0.4

    events: list = []
    if args.notes:
        # 按 --notes "起始秒:音高" 逐个排，比如 --notes 1:60 2:64
        for item in args.notes:
            t_s, pitch = item.split(":")
            events += note(float(t_s), duration - 0.3, int(pitch))
    else:
        # 默认：轨迹中段按住一个 C 大三和弦
        events += chord(duration * 0.25, duration * 0.85, [60, 64, 67])

    result = render(track, events, duration=duration,
                    bellows_config=bcfg, synth_config=scfg)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    result.write_wav(out, normalize=args.normalize)

    st = result.stats()
    print(f"已写入 {out}")
    print(f"  时长 {st['duration_s']}s  峰值 {st['peak']}  RMS {st['rms']}  "
          f"削波 {st['clipped']}")
    pressures = [p for _, p, _ in result.bellows_trace]
    if pressures:
        print(f"  气压范围 {min(pressures):.2f} ~ {max(pressures):.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="用真实传感器录一段开合轨迹")
    r.add_argument("-t", "--seconds", type=float, default=12.0)
    r.add_argument("-o", "--out", default=None)
    r.set_defaults(func=cmd_record)

    p = sub.add_parser("render", help="用录好的轨迹离线渲染")
    p.add_argument("-i", "--input", required=True, help="record 产出的 CSV")
    p.add_argument("-o", "--out", default=None)
    p.add_argument("--notes", nargs="*", default=None,
                   help='按键安排，如 --notes "1:60" "1.5:64"；默认中段一个 C 和弦')
    p.add_argument("--detune", type=float, default=9.0)
    p.add_argument("--reeds", type=int, default=3)
    p.add_argument("--gain", type=float, default=0.42)
    p.add_argument("--leak", type=float, default=1.10)
    p.add_argument("--pos-base", type=float, default=0.22)
    p.add_argument("--normalize", type=float, default=None)
    p.set_defaults(func=cmd_render)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
