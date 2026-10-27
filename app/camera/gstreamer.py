"""Optional GStreamer appsink reader using the installed C ABI (no PyGObject).
Only CPU-owned BGR buffers cross into numpy; samples are released after copying.
RTSP credentials are never passed through command-line arguments or logs.
"""
import ctypes as C
import ctypes.util
import os
import threading
import numpy as np

class _MapInfo(C.Structure):
    _fields_ = [("memory", C.c_void_p), ("flags", C.c_int),
                ("data", C.POINTER(C.c_ubyte)), ("size", C.c_size_t),
                ("maxsize", C.c_size_t), ("user_data", C.c_void_p * 4),
                ("reserved", C.c_void_p * 4)]

class _GError(C.Structure):
    _fields_ = [("domain", C.c_uint), ("code", C.c_int), ("message", C.c_char_p)]

_init_lock = threading.Lock()
_api = None

def _bind(lib, name, restype, *argtypes):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn

def _get_api():
    global _api
    with _init_lock:
        if _api is not None:
            return _api
        gst = C.CDLL(ctypes.util.find_library("gstreamer-1.0") or "libgstreamer-1.0.so.0")
        app = C.CDLL(ctypes.util.find_library("gstapp-1.0") or "libgstapp-1.0.so.0")
        glib = C.CDLL(ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0")
        v, i = C.c_void_p, C.c_int
        api = {
            "init": _bind(gst, "gst_init", None, v, v),
            "parse": _bind(gst, "gst_parse_launch", v, C.c_char_p, C.POINTER(v)),
            "get": _bind(gst, "gst_bin_get_by_name", v, v, C.c_char_p),
            "state": _bind(gst, "gst_element_set_state", i, v, i),
            "unref": _bind(gst, "gst_object_unref", None, v),
            "mini_unref": _bind(gst, "gst_mini_object_unref", None, v),
            "pull": _bind(app, "gst_app_sink_try_pull_sample", v, v, C.c_uint64),
            "buffer": _bind(gst, "gst_sample_get_buffer", v, v),
            "caps": _bind(gst, "gst_sample_get_caps", v, v),
            "structure": _bind(gst, "gst_caps_get_structure", v, v, C.c_uint),
            "integer": _bind(gst, "gst_structure_get_int", i, v, C.c_char_p, C.POINTER(i)),
            "map": _bind(gst, "gst_buffer_map", i, v, C.POINTER(_MapInfo), i),
            "unmap": _bind(gst, "gst_buffer_unmap", None, v, C.POINTER(_MapInfo)),
            "free_error": _bind(glib, "g_error_free", None, v),
        }
        api["init"](None, None)
        _api = api
        return api

def device_access():
    return os.name == "posix" and any(os.access(p, os.R_OK | os.W_OK)
        for p in ("/dev/video32", "/dev/video33"))

def _quoted(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

class GstCameraCapture:
    def __init__(self, source=None, codec="h264", decoder="qtivdec", pipeline=None):
        self.backend_name = "gstreamer:" + decoder
        self._api = _get_api()
        self._pipeline = self._sink = None
        self._first = None
        if pipeline is None:
            if decoder not in ("qtivdec", "qtic2vdec"):
                raise ValueError("Unsupported hardware decoder")
            if codec not in ("h264", "h265"):
                raise ValueError("Unsupported RTSP codec")
            if not device_access():
                raise PermissionError("Hardware decoder requires video-device access")
            latency = max(50, int(os.getenv("CAMERA_RTSP_LATENCY_MS", "150")))
            pipeline = (
                "rtspsrc location=" + _quoted(source) +
                " protocols=tcp latency=" + str(latency) + " drop-on-latency=true ! "
                "application/x-rtp,media=video,encoding-name=" + codec.upper() + " ! "
                "rtp" + codec + "depay ! " + codec + "parse ! " + decoder +
                " ! videoconvert ! video/x-raw,format=BGR ! "
                "appsink name=frames max-buffers=1 drop=true sync=false"
            )
        error = C.c_void_p()
        self._pipeline = self._api["parse"](pipeline.encode(), C.byref(error))
        if error.value:
            # The GStreamer message can contain the URL; never expose it.
            self._api["free_error"](error)
            self.release()
            raise RuntimeError("GStreamer pipeline could not be constructed")
        if not self._pipeline:
            raise RuntimeError("GStreamer pipeline unavailable")
        self._sink = self._api["get"](self._pipeline, b"frames")
        if not self._sink or self._api["state"](self._pipeline, 4) == 0:
            self.release()
            raise RuntimeError("GStreamer could not start")
        ok, frame = self._read(5.0)
        if not ok:
            self.release()
            raise RuntimeError("No hardware-decoded frame within 5 seconds")
        self._first = frame

    def isOpened(self):
        return bool(self._pipeline and self._sink)

    def read(self):
        if self._first is not None:
            frame, self._first = self._first, None
            return True, frame
        return self._read(1.5)

    def _read(self, timeout):
        if not self._sink:
            return False, None
        sample = self._api["pull"](self._sink, int(timeout * 1e9))
        if not sample:
            return False, None
        info = _MapInfo()
        buffer = None
        mapped = False
        try:
            caps = self._api["caps"](sample)
            if not caps:
                return False, None
            structure = self._api["structure"](caps, 0)
            width, height = C.c_int(), C.c_int()
            if not (self._api["integer"](structure, b"width", C.byref(width)) and
                    self._api["integer"](structure, b"height", C.byref(height))):
                return False, None
            buffer = self._api["buffer"](sample)
            mapped = bool(self._api["map"](buffer, C.byref(info), 1))
            if not mapped or width.value <= 0 or height.value <= 0:
                return False, None
            # videoconvert's packed BGR output may have row-alignment padding.
            stride, remainder = divmod(info.size, height.value)
            if remainder or stride < width.value * 3 or stride > width.value * 3 + 4096:
                return False, None
            pixels = np.ctypeslib.as_array(info.data, shape=(info.size,))
            frame = pixels.reshape(height.value, stride)[:, :width.value * 3]
            return True, frame.reshape(height.value, width.value, 3).copy()
        finally:
            if mapped:
                self._api["unmap"](buffer, C.byref(info))
            self._api["mini_unref"](sample)

    def release(self):
        if self._pipeline:
            self._api["state"](self._pipeline, 1)
        if self._sink:
            self._api["unref"](self._sink)
            self._sink = None
        if self._pipeline:
            self._api["unref"](self._pipeline)
            self._pipeline = None
        self._first = None
