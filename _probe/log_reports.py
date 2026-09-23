#!/usr/bin/env python3
"""记录 Apple SPU 角度传感器的所有 HID 输入报告，用于判定角度数据到底在哪。

采用 IOHIDDeviceRegisterInputReportCallback + CFRunLoop（macimu 验证过的路径），
比同步读 Feature Report 更可靠。

用法：
  python3 log_reports.py -t 90 -o /tmp/lid_log.txt
运行期间请缓慢开合屏幕，脚本结束后会输出「哪些 report 的哪些字节在变化」。
"""
import argparse
import ctypes
import ctypes.util
import time

cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))

kCFStringEncodingUTF8 = 0x08000100
kCFNumberIntType = 9
kCFTypeDictionaryKeyCallBacks = ctypes.addressof((ctypes.c_byte * 128).in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
kCFTypeDictionaryValueCallBacks = ctypes.addressof((ctypes.c_byte * 128).in_dll(cf, "kCFTypeDictionaryValueCallBacks"))

cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFNumberCreate.restype = ctypes.c_void_p
cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
cf.CFDictionaryCreate.restype = ctypes.c_void_p
cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                  ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                  ctypes.c_void_p, ctypes.c_void_p]
cf.CFSetGetCount.restype = ctypes.c_long
cf.CFSetGetCount.argtypes = [ctypes.c_void_p]
cf.CFSetGetValues.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
cf.CFRelease.argtypes = [ctypes.c_void_p]
cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
cf.CFRunLoopRunInMode.restype = ctypes.c_int32
cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]

# kCFRunLoopDefaultMode 是全局 CFStringRef 变量，in_dll 读到的就是指针值
kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode").value

iokit.IOHIDManagerCreate.restype = ctypes.c_void_p
iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerSetDeviceMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerOpen.restype = ctypes.c_int
iokit.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerCopyDevices.restype = ctypes.c_void_p
iokit.IOHIDManagerCopyDevices.argtypes = [ctypes.c_void_p]
iokit.IOHIDDeviceOpen.restype = ctypes.c_int
iokit.IOHIDDeviceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDDeviceScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDDeviceUnscheduleFromRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

REPORT_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                             ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_ubyte),
                             ctypes.c_long)
iokit.IOHIDDeviceRegisterInputReportCallback.restype = None
iokit.IOHIDDeviceRegisterInputReportCallback.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_long, REPORT_CB, ctypes.c_void_p]

TARGET = {"VendorID": 0x05AC, "ProductID": 0x8104,
          "PrimaryUsagePage": 0x0020, "PrimaryUsage": 0x008A}
BUF_SZ = 4096

records = []      # (t, rid, bytes)
report_buf = (ctypes.c_ubyte * BUF_SZ)()
t0 = time.time()


def cfstr(s):
    return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)


def mkdict(pairs):
    n = len(pairs)
    k = (ctypes.c_void_p * n)()
    v = (ctypes.c_void_p * n)()
    for i, (a, b) in enumerate(pairs.items()):
        k[i] = cfstr(a)
        val = ctypes.c_int32(b)
        v[i] = cf.CFNumberCreate(None, kCFNumberIntType, ctypes.byref(val))
    return cf.CFDictionaryCreate(None, k, v, n, kCFTypeDictionaryKeyCallBacks,
                                 kCFTypeDictionaryValueCallBacks)


def on_report(ctx, result, sender, rtype, rid, rpt, length):
    if result != 0 or length <= 0:
        return
    records.append((time.time() - t0, rid, bytes(rpt[:length])))


_cb = REPORT_CB(on_report)   # 必须保持引用，否则被 GC


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--seconds", type=float, default=90.0)
    ap.add_argument("-o", "--out", default="/tmp/lid_log.txt")
    args = ap.parse_args()

    mgr = iokit.IOHIDManagerCreate(None, 0)
    iokit.IOHIDManagerSetDeviceMatching(ctypes.c_void_p(mgr), ctypes.c_void_p(mkdict(TARGET)))
    assert iokit.IOHIDManagerOpen(ctypes.c_void_p(mgr), 0) == 0, "manager open failed"
    devset = iokit.IOHIDManagerCopyDevices(ctypes.c_void_p(mgr))
    n = cf.CFSetGetCount(ctypes.c_void_p(devset))
    buf = (ctypes.c_void_p * n)()
    cf.CFSetGetValues(ctypes.c_void_p(devset), buf)
    dev = ctypes.c_void_p(buf[0])

    assert iokit.IOHIDDeviceOpen(dev, 0) == 0, "device open failed"
    iokit.IOHIDDeviceRegisterInputReportCallback(dev, report_buf, BUF_SZ, _cb, None)
    iokit.IOHIDDeviceScheduleWithRunLoop(dev, ctypes.c_void_p(cf.CFRunLoopGetCurrent()),
                                        ctypes.c_void_p(kCFRunLoopDefaultMode))

    print(f"开始记录 {args.seconds:.0f} 秒 —— 请现在缓慢地把屏幕从最小开到最大，再合回去，重复 2~3 次。", flush=True)
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        cf.CFRunLoopRunInMode(ctypes.c_void_p(kCFRunLoopDefaultMode), 0.25, False)

    iokit.IOHIDDeviceUnscheduleFromRunLoop(dev, ctypes.c_void_p(cf.CFRunLoopGetCurrent()),
                                          ctypes.c_void_p(kCFRunLoopDefaultMode))

    # ---- 汇总 ----
    lines = []
    lines.append(f"总回调次数: {len(records)}")
    by_id = {}
    for t, rid, data in records:
        by_id.setdefault(rid, []).append((t, data))

    lines.append(f"\n收到的 report id: {sorted(by_id)}")
    for rid in sorted(by_id):
        samples = by_id[rid]
        maxlen = max(len(d) for _, d in samples)
        # 找出每个字节位是否变化
        varying = []
        for i in range(maxlen):
            vals = {d[i] for _, d in samples if i < len(d)}
            if len(vals) > 1:
                varying.append((i, len(vals)))
        lines.append(f"\n=== Report id={rid}  回调 {len(samples)} 次  最长 {maxlen} 字节 ===")
        lines.append(f"  变化字节: {varying if varying else '无（恒定）'}")
        # 展示前若干条与变化样例
        shown = samples[:4]
        if varying:
            idx = varying[0][0]
            vals = sorted({d[idx] for _, d in samples if idx < len(d)})
            shown += [(t, d) for t, d in samples if d[idx] == vals[0]][:2]
            shown += [(t, d) for t, d in samples if d[idx] == vals[-1]][:2]
        for t, d in shown[:8]:
            lines.append(f"  t={t:7.3f}  {d.hex()}")

    out = "\n".join(lines)
    with open(args.out, "w") as f:
        f.write(out)
    # 原始记录也存一份，便于后续离线分析
    with open(args.out + ".raw", "w") as f:
        for t, rid, data in records:
            f.write(f"{t:.4f}\t{rid}\t{data.hex()}\n")
    print(out)
    print(f"\n已写入 {args.out} 和 {args.out}.raw")


if __name__ == "__main__":
    main()
