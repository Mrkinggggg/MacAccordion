#!/usr/bin/env python3
"""MacBook 铰链角度传感器探针（纯 ctypes + IOKit，无需 hidapi / brew）。

原理：
  屏幕角度由 Apple SPU（Sensor Processing Unit）的 HID 设备上报：
    VendorID 0x05AC / ProductID 0x8104 / UsagePage 0x0020 / Usage 0x008A
  角度值通过 HID **Feature Report**（不是 Input Report）读取：
    Report ID = 1，载荷为 16-bit little-endian 的角度整数（单位：度）。

用法：
  python3 lid_angle_probe.py            # 列设备 + 连续读数 5 秒
  python3 lid_angle_probe.py --list     # 只列设备
  python3 lid_angle_probe.py -t 15      # 读 15 秒
"""

import argparse
import ctypes
import ctypes.util
import time

# ---------------------------------------------------------------- CoreFoundation
cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))

kCFStringEncodingUTF8 = 0x08000100
kCFNumberSInt32Type = 3

cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFNumberGetValue.restype = ctypes.c_bool
cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
cf.CFSetGetCount.restype = ctypes.c_long
cf.CFSetGetCount.argtypes = [ctypes.c_void_p]
cf.CFSetGetValues.restype = None
cf.CFSetGetValues.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
cf.CFRelease.argtypes = [ctypes.c_void_p]
cf.CFGetTypeID.restype = ctypes.c_ulong
cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
cf.CFNumberGetTypeID.restype = ctypes.c_ulong
cf.CFStringGetTypeID.restype = ctypes.c_ulong

kCFNumberIntType = 9
cf.CFNumberCreate.restype = ctypes.c_void_p
cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
cf.CFDictionaryCreate.restype = ctypes.c_void_p
cf.CFDictionaryCreate.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
]

# 取 CFType callbacks 符号的地址（in_dll 拿到的是数据本体，addressof 才是符号地址）
_CB = ctypes.c_byte * 128
_kCBKey = _CB.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
_kCBVal = _CB.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
kCFTypeDictionaryKeyCallBacks = ctypes.addressof(_kCBKey)
kCFTypeDictionaryValueCallBacks = ctypes.addressof(_kCBVal)


def cfstr(s: str):
    return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)


def cfint(v: int):
    val = ctypes.c_int32(v)
    return cf.CFNumberCreate(None, kCFNumberIntType, ctypes.byref(val))


def matching_dict(pairs: dict):
    """构造 CFDictionary，供 IOHIDManagerSetDeviceMatching 使用。"""
    n = len(pairs)
    keys = (ctypes.c_void_p * n)()
    vals = (ctypes.c_void_p * n)()
    for i, (k, v) in enumerate(pairs.items()):
        keys[i] = cfstr(k)
        vals[i] = cfint(v)
    return cf.CFDictionaryCreate(None, keys, vals, n,
                                 kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks)


def num_prop(dev, key):
    """读取 IOHIDDevice 的整数属性，取不到返回 None。"""
    k = cfstr(key)
    try:
        ref = iokit.IOHIDDeviceGetProperty(ctypes.c_void_p(dev), ctypes.c_void_p(k))
        if not ref:
            return None
        if cf.CFGetTypeID(ctypes.c_void_p(ref)) != cf.CFNumberGetTypeID():
            return None  # 非数字属性（如 Transport 是字符串）
        out = ctypes.c_int32(0)
        if not cf.CFNumberGetValue(ctypes.c_void_p(ref), kCFNumberSInt32Type, ctypes.byref(out)):
            return None
        return out.value
    finally:
        cf.CFRelease(ctypes.c_void_p(k))


# ---------------------------------------------------------------- IOKit HID
iokit.IOHIDDeviceGetProperty.restype = ctypes.c_void_p
iokit.IOHIDDeviceGetProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerCreate.restype = ctypes.c_void_p
iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerSetDeviceMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerOpen.restype = ctypes.c_int
iokit.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerCopyDevices.restype = ctypes.c_void_p
iokit.IOHIDManagerCopyDevices.argtypes = [ctypes.c_void_p]
iokit.IOHIDDeviceOpen.restype = ctypes.c_int
iokit.IOHIDDeviceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDDeviceClose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDDeviceGetReport.restype = ctypes.c_int
iokit.IOHIDDeviceGetReport.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)
]

kIOHIDOptionsTypeNone = 0
kIOHIDReportTypeFeature = 2

TARGET = {"VendorID": 0x05AC, "ProductID": 0x8104,
          "PrimaryUsagePage": 0x0020, "PrimaryUsage": 0x008A}
PROPS = ["VendorID", "ProductID", "PrimaryUsagePage", "PrimaryUsage", "MaxFeatureReportSize"]


def find_lid_sensor():
    """返回 (device_ref, 属性 dict) 列表，只含匹配角度传感器的设备。"""
    mgr = iokit.IOHIDManagerCreate(None, kIOHIDOptionsTypeNone)
    # 只匹配角度传感器本身，避免匹配到键盘等需要「输入监控」授权的设备
    md = matching_dict(TARGET)
    iokit.IOHIDManagerSetDeviceMatching(ctypes.c_void_p(mgr), ctypes.c_void_p(md))
    ret = iokit.IOHIDManagerOpen(ctypes.c_void_p(mgr), kIOHIDOptionsTypeNone)
    if ret != 0:
        raise RuntimeError(f"IOHIDManagerOpen 失败 (0x{ret & 0xFFFFFFFF:x})")

    devset = iokit.IOHIDManagerCopyDevices(ctypes.c_void_p(mgr))
    if not devset:
        return [], mgr

    n = cf.CFSetGetCount(ctypes.c_void_p(devset))
    buf = (ctypes.c_void_p * n)()
    cf.CFSetGetValues(ctypes.c_void_p(devset), buf)

    found = []
    for i in range(n):
        dev = buf[i]
        info = {p: num_prop(dev, p) for p in PROPS}
        if all(info.get(k) == v for k, v in TARGET.items()):
            found.append((dev, info))
    return found, mgr


def read_angle(dev, report_id=1, size=64):
    """读一次 Feature Report，返回 (角度, 原始hex) 或 (None, 原因)。

    实测结论（Mac15,12 / M3 MacBook Air / macOS 15.7.9）：
      - Feature Report ID = 1，3 字节：[report_id, 角度, 标志位]
      - byte[1] 就是角度（度），本机型范围 0..132
      - byte[2] != 0 时是无效值（会读到 0x016701 = 359 这种脏数据），应丢弃
      - 必须用 Feature Report 轮询；Input Report 回调只有 ~1Hz 且在静止时全是脏值
    """
    if iokit.IOHIDDeviceOpen(ctypes.c_void_p(dev), kIOHIDOptionsTypeNone) != 0:
        return None, "IOHIDDeviceOpen 失败"
    try:
        buf = (ctypes.c_ubyte * size)()
        buf[0] = report_id
        length = ctypes.c_long(size)
        ret = iokit.IOHIDDeviceGetReport(
            ctypes.c_void_p(dev), kIOHIDReportTypeFeature,
            ctypes.c_void_p(report_id), buf, ctypes.byref(length))
        if ret != 0:
            return None, f"IOHIDDeviceGetReport 返回 0x{ret & 0xFFFFFFFF:x}"
        raw = bytes(buf[:length.value])
        if len(raw) < 3:
            return None, f"报告过短: {raw.hex()}"
        if raw[2] != 0:
            return None, f"无效标志位 byte[2]={raw[2]} (raw={raw.hex()})"
        return raw[1], raw.hex()
    finally:
        iokit.IOHIDDeviceClose(ctypes.c_void_p(dev), kIOHIDOptionsTypeNone)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列设备")
    ap.add_argument("-t", "--seconds", type=float, default=5.0, help="连续读数时长（秒）")
    ap.add_argument("-i", "--interval", type=float, default=0.2, help="采样间隔（秒）")
    args = ap.parse_args()

    found, _mgr = find_lid_sensor()
    if not found:
        print("未找到角度传感器 (0x05AC/0x8104 UsagePage 32 Usage 138)")
        return 1

    print(f"找到 {len(found)} 个角度传感器设备：")
    for dev, info in found:
        print("  " + "  ".join(
            f"{k}={v if v is None else hex(v)}" for k, v in info.items()))

    if args.list:
        return 0

    dev, _ = found[0]
    print(f"\n连续读取 {args.seconds:.0f} 秒（请缓慢开合屏幕）：")
    t0 = time.time()
    ok = 0
    while time.time() - t0 < args.seconds:
        val, extra = read_angle(dev)
        if val is None:
            print(f"  读取失败: {extra}")
        else:
            ok += 1
            print(f"  t={time.time() - t0:5.1f}s  angle={val:>4}  raw={extra}")
        time.sleep(args.interval)
    print(f"\n成功 {ok} 次读取。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
