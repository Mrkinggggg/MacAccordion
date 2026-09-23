#!/usr/bin/env python3
"""转储 Apple SPU (0x05AC/0x8104) HID 设备的原始 Feature/Input 报告 + 报告描述符。

用途：确认 M3 MacBook Air 上角度数据到底在哪个 report、按什么格式编码。
"""
import ctypes
import ctypes.util
import sys
import time

cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))

kCFStringEncodingUTF8 = 0x08000100
kCFNumberIntType = 9
kCFNumberSInt32Type = 3
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
cf.CFGetTypeID.restype = ctypes.c_ulong
cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
cf.CFNumberGetTypeID.restype = ctypes.c_ulong
cf.CFDataGetTypeID.restype = ctypes.c_ulong
cf.CFDataGetLength.restype = ctypes.c_long
cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
cf.CFDataGetBytePtr.restype = ctypes.c_void_p
cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
cf.CFRelease.argtypes = [ctypes.c_void_p]

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
iokit.IOHIDDeviceGetReport.restype = ctypes.c_int
iokit.IOHIDDeviceGetReport.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)]

TARGET = {"VendorID": 0x05AC, "ProductID": 0x8104,
          "PrimaryUsagePage": 0x0020, "PrimaryUsage": 0x008A}


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
    return cf.CFDictionaryCreate(None, k, v, n, kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks)


def get_prop(dev, key):
    k = cfstr(key)
    try:
        return iokit.IOHIDDeviceGetProperty(ctypes.c_void_p(dev), ctypes.c_void_p(k))
    finally:
        cf.CFRelease(ctypes.c_void_p(k))


mgr = iokit.IOHIDManagerCreate(None, 0)
iokit.IOHIDManagerSetDeviceMatching(ctypes.c_void_p(mgr), ctypes.c_void_p(mkdict(TARGET)))
assert iokit.IOHIDManagerOpen(ctypes.c_void_p(mgr), 0) == 0, "manager open failed"
devset = iokit.IOHIDManagerCopyDevices(ctypes.c_void_p(mgr))
n = cf.CFSetGetCount(ctypes.c_void_p(devset))
buf = (ctypes.c_void_p * n)()
cf.CFSetGetValues(ctypes.c_void_p(devset), buf)
dev = buf[0]
print(f"devices matched: {n}")

# --- 报告描述符 ---
desc_ref = get_prop(dev, "ReportDescriptor")
if desc_ref and cf.CFGetTypeID(ctypes.c_void_p(desc_ref)) == cf.CFDataGetTypeID():
    ln = cf.CFDataGetLength(ctypes.c_void_p(desc_ref))
    ptr = cf.CFDataGetBytePtr(ctypes.c_void_p(desc_ref))
    data = ctypes.string_at(ptr, ln)
    print(f"\n=== ReportDescriptor ({ln} bytes) ===")
    print(data.hex())
else:
    print("\n(无 ReportDescriptor 属性)")

assert iokit.IOHIDDeviceOpen(ctypes.c_void_p(dev), 0) == 0, "device open failed"

REPORT_TYPES = {1: "Input", 2: "Feature", 3: "Output"}


def dump(rid, rtype, size=64):
    b = (ctypes.c_ubyte * size)()
    b[0] = rid
    length = ctypes.c_long(size)
    ret = iokit.IOHIDDeviceGetReport(ctypes.c_void_p(dev), rtype,
                                     ctypes.c_void_p(rid), b, ctypes.byref(length))
    if ret != 0:
        return None
    return bytes(b[:length.value])


def scan(label):
    print(f"\n=== {label} ===")
    for rtype in (2, 1):
        for rid in range(0, 24):
            r = dump(rid, rtype)
            if r is not None and any(r[1:]):
                print(f"  {REPORT_TYPES[rtype]:7s} id={rid:2d} len={len(r):2d}  {r.hex()}")


scan("第一遍（屏幕保持不动）")
time.sleep(1.0)
scan("第二遍（屏幕保持不动，1 秒后）")
print("\n>>> 现在请把屏幕开到另一个角度，脚本将在 6 秒后再扫一次 <<<")
time.sleep(6.0)
scan("第三遍（屏幕改变角度后）")
