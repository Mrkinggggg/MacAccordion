"""读取 MacBook 铰链角度传感器（Apple SPU / IOKit HID）。

实测结论（Mac15,12 / M3 MacBook Air / macOS 15.7.9，详见 docs/调研报告.md）：
  - 设备标识：VendorID 0x05AC / ProductID 0x8104 / UsagePage 0x0020 / Usage 0x008A
  - 数据来源：**Feature Report**（Report ID = 1），3 字节 [report_id, 角度, 标志位]
  - byte[1] 就是角度（度）；byte[2] != 0 表示本帧无效，必须丢弃
  - 必须轮询 Feature Report；Input Report 回调只有约 1 Hz 且静止时全是脏值
  - 定向匹配该设备即可打开，**不需要 root**

本模块只用 ctypes 调 IOKit/CoreFoundation，没有任何第三方依赖。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import threading
import time
from dataclasses import dataclass
from typing import Optional

# --------------------------------------------------------------------- 常量

VENDOR_ID = 0x05AC
PRODUCT_ID = 0x8104
USAGE_PAGE = 0x0020
USAGE = 0x008A
FEATURE_REPORT_ID = 1

_MATCHING = {
    "VendorID": VENDOR_ID,
    "ProductID": PRODUCT_ID,
    "PrimaryUsagePage": USAGE_PAGE,
    "PrimaryUsage": USAGE,
}

_kCFStringEncodingUTF8 = 0x08000100
_kCFNumberIntType = 9
_kCFNumberSInt32Type = 3
_kIOHIDOptionsTypeNone = 0
_kIOHIDReportTypeFeature = 2

_LOADED = False
_cf = None
_iokit = None


def _load_frameworks():
    """惰性加载 IOKit / CoreFoundation 并声明函数签名。

    注意：所有返回指针的函数都必须显式设置 restype = c_void_p，
    否则 ctypes 默认按 c_int 截断 64 位指针，后续调用会段错误。
    """
    global _LOADED, _cf, _iokit
    if _LOADED:
        return _cf, _iokit

    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))

    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFNumberCreate.restype = ctypes.c_void_p
    cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
    ]
    cf.CFSetGetCount.restype = ctypes.c_long
    cf.CFSetGetCount.argtypes = [ctypes.c_void_p]
    cf.CFSetGetValues.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
    cf.CFNumberGetTypeID.restype = ctypes.c_ulong
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

    iokit.IOHIDDeviceGetProperty.restype = ctypes.c_void_p
    iokit.IOHIDDeviceGetProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    iokit.IOHIDManagerCreate.restype = ctypes.c_void_p
    iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDManagerSetDeviceMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    iokit.IOHIDManagerOpen.restype = ctypes.c_int
    iokit.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDManagerClose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDManagerCopyDevices.restype = ctypes.c_void_p
    iokit.IOHIDManagerCopyDevices.argtypes = [ctypes.c_void_p]
    iokit.IOHIDDeviceOpen.restype = ctypes.c_int
    iokit.IOHIDDeviceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDDeviceClose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    iokit.IOHIDDeviceGetReport.restype = ctypes.c_int
    iokit.IOHIDDeviceGetReport.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_long),
    ]

    _cf, _iokit, _LOADED = cf, iokit, True
    return cf, iokit


# --------------------------------------------------------------------- 底层读取


class SensorError(RuntimeError):
    """传感器不可用或读取失败。"""


class LidSensor:
    """铰链角度传感器（阻塞式轮询 Feature Report）。

    用法::

        with LidSensor() as s:
            angle = s.read()          # int（度）或 None
    """

    def __init__(self, report_size: int = 64) -> None:
        self._dev = None
        self._mgr = None
        self._report_size = report_size
        self._buf = None
        self._open = False

    # -- 设备发现 ---------------------------------------------------------

    @staticmethod
    def is_present() -> bool:
        """只做探测，不打开设备。"""
        try:
            cf, iokit = _load_frameworks()
        except OSError:
            return False
        try:
            mgr = iokit.IOHIDManagerCreate(None, _kIOHIDOptionsTypeNone)
            if not mgr:
                return False
            iokit.IOHIDManagerSetDeviceMatching(
                ctypes.c_void_p(mgr), ctypes.c_void_p(_make_matching_dict()))
            if iokit.IOHIDManagerOpen(ctypes.c_void_p(mgr), _kIOHIDOptionsTypeNone) != 0:
                iokit.IOHIDManagerClose(ctypes.c_void_p(mgr), _kIOHIDOptionsTypeNone)
                return False
            devset = iokit.IOHIDManagerCopyDevices(ctypes.c_void_p(mgr))
            iokit.IOHIDManagerClose(ctypes.c_void_p(mgr), _kIOHIDOptionsTypeNone)
            return bool(devset) and cf.CFSetGetCount(ctypes.c_void_p(devset)) > 0
        except Exception:
            return False

    # -- 生命周期 ---------------------------------------------------------

    def open(self) -> None:
        if self._open:
            return
        cf, iokit = _load_frameworks()

        self._mgr = iokit.IOHIDManagerCreate(None, _kIOHIDOptionsTypeNone)
        if not self._mgr:
            raise SensorError("IOHIDManagerCreate 失败")
        iokit.IOHIDManagerSetDeviceMatching(
            ctypes.c_void_p(self._mgr), ctypes.c_void_p(_make_matching_dict()))
        if iokit.IOHIDManagerOpen(ctypes.c_void_p(self._mgr), _kIOHIDOptionsTypeNone) != 0:
            raise SensorError("IOHIDManagerOpen 失败（权限或设备被占用）")

        devset = iokit.IOHIDManagerCopyDevices(ctypes.c_void_p(self._mgr))
        n = cf.CFSetGetCount(ctypes.c_void_p(devset)) if devset else 0
        if not n:
            raise SensorError(
                "未找到角度传感器。需要 2019 年之后的 MacBook（M1/M2 MacBook Pro 无此传感器）")

        arr = (ctypes.c_void_p * n)()
        cf.CFSetGetValues(ctypes.c_void_p(devset), arr)
        self._dev = arr[0]

        if iokit.IOHIDDeviceOpen(ctypes.c_void_p(self._dev), _kIOHIDOptionsTypeNone) != 0:
            raise SensorError("IOHIDDeviceOpen 失败")

        self._buf = (ctypes.c_ubyte * self._report_size)()
        self._open = True

    def close(self) -> None:
        if not self._open:
            return
        _, iokit = _load_frameworks()
        try:
            iokit.IOHIDDeviceClose(ctypes.c_void_p(self._dev), _kIOHIDOptionsTypeNone)
        finally:
            if self._mgr:
                iokit.IOHIDManagerClose(ctypes.c_void_p(self._mgr), _kIOHIDOptionsTypeNone)
            self._dev = self._mgr = self._buf = None
            self._open = False

    def __enter__(self) -> "LidSensor":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- 读取 -------------------------------------------------------------

    def read(self) -> Optional[int]:
        """读一次角度。无效帧返回 None（不抛异常，方便在控制循环里用）。"""
        if not self._open:
            raise SensorError("传感器未打开，先调用 open()")
        _, iokit = _load_frameworks()

        self._buf[0] = FEATURE_REPORT_ID
        length = ctypes.c_long(self._report_size)
        ret = iokit.IOHIDDeviceGetReport(
            ctypes.c_void_p(self._dev), _kIOHIDReportTypeFeature,
            ctypes.c_void_p(FEATURE_REPORT_ID), self._buf, ctypes.byref(length))
        if ret != 0:
            return None

        raw = bytes(self._buf[: length.value])
        if len(raw) < 3 or raw[2] != 0:
            return None
        return raw[1]


def _make_matching_dict() -> int:
    """构造 IOHIDManager 的匹配字典。"""
    cf, _ = _load_frameworks()
    n = len(_MATCHING)
    keys = (ctypes.c_void_p * n)()
    vals = (ctypes.c_void_p * n)()
    for i, (k, v) in enumerate(_MATCHING.items()):
        keys[i] = cf.CFStringCreateWithCString(None, k.encode(), _kCFStringEncodingUTF8)
        iv = ctypes.c_int32(v)
        vals[i] = cf.CFNumberCreate(None, _kCFNumberIntType, ctypes.byref(iv))

    kcb = ctypes.addressof((ctypes.c_byte * 128).in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
    vcb = ctypes.addressof((ctypes.c_byte * 128).in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
    return cf.CFDictionaryCreate(None, keys, vals, n, kcb, vcb)


# --------------------------------------------------------------------- 行程归一化


@dataclass
class RangeTracker:
    """自适应跟踪实际开合行程，把绝对角度映射到 0..1。

    不同机型的角度基线/量程不同（本机 0..132，有的机型能到 180+），
    所以不能写死映射区间，必须按实际观察到的极值自适应。
    """

    min_seen: float = 180.0
    max_seen: float = 0.0
    decay: float = 0.0            # >0 时缓慢把极值向当前值收缩，适应姿势变化
    min_span: float = 25.0        # 小于这个跨度就认为还没张开，不做归一化

    def update(self, angle: float) -> None:
        self.min_seen = min(self.min_seen, angle)
        self.max_seen = max(self.max_seen, angle)

    def normalize(self, angle: float) -> float:
        span = self.max_seen - self.min_seen
        if span < self.min_span:
            return 0.0
        return _clamp((angle - self.min_seen) / span, 0.0, 1.0)

    @property
    def span(self) -> float:
        """已观察到的行程跨度（未初始化时为 0）。"""
        return max(0.0, self.max_seen - self.min_seen)

    def reset(self) -> None:
        self.min_seen, self.max_seen = 180.0, 0.0


# --------------------------------------------------------------------- 后台采集


class SensorReader:
    """后台线程持续采集角度，供音频回调读取最新值。

    音频回调需要的是"最新角度"，而不是自己阻塞去读设备（会引入抖动）。
    所以这里用一条独立线程按固定频率采集，写入 `latest_angle` / `latest_time`。
    """

    def __init__(self, rate_hz: float = 60.0, sensor: Optional[LidSensor] = None,
                 simulated_angle: float = 90.0) -> None:
        self.rate_hz = rate_hz
        self._sensor = sensor
        self._simulated_angle = simulated_angle
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # 供音频线程读取（CPython 下简单赋值即原子）
        self.latest_angle: float = simulated_angle
        self.latest_time: float = time.monotonic()
        self.valid: bool = False
        self.invalid_frames: int = 0
        self.read_count: int = 0
        self.range_tracker = RangeTracker()

    # -- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        if self._sensor is not None and not self._sensor._open:
            self._sensor.open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lid-sensor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def __enter__(self) -> "SensorReader":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- 采集循环 ---------------------------------------------------------

    def _run(self) -> None:
        period = 1.0 / self.rate_hz
        next_t = time.monotonic()
        while not self._stop.is_set():
            angle = self._sensor.read() if self._sensor is not None else self._simulated_angle
            now = time.monotonic()
            if angle is None:
                self.invalid_frames += 1
            else:
                self.latest_angle = float(angle)
                self.latest_time = now
                self.valid = True
                self.read_count += 1
                self.range_tracker.update(float(angle))

            next_t += period
            sleep = next_t - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.monotonic()   # 落后了就重新对齐，避免追赶式空转

    # -- 便捷访问 ---------------------------------------------------------

    @property
    def position(self) -> float:
        """归一化开合位置 0..1。"""
        return self.range_tracker.normalize(self.latest_angle)

    def set_simulated(self, angle: float) -> None:
        """模拟模式下直接设定角度（供键盘 ↑↓ 试玩用）。"""
        self._simulated_angle = _clamp(angle, 0.0, 180.0)
        self.latest_angle = self._simulated_angle
        self.latest_time = time.monotonic()


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def open_best_available(rate_hz: float = 60.0) -> SensorReader:
    """优先用真实传感器，不可用则退回模拟模式（不抛异常）。"""
    if not LidSensor.is_present():
        return SensorReader(rate_hz=rate_hz, sensor=None)
    sensor = LidSensor()
    try:
        sensor.open()
    except SensorError:
        return SensorReader(rate_hz=rate_hz, sensor=None)
    return SensorReader(rate_hz=rate_hz, sensor=sensor)
