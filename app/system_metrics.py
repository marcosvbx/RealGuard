"""
Métricas do equipamento para o Painel de monitores (CPU, memória, temperatura).

Lê direto do Linux (/proc e /sys) — sem instalar nada na AIBOX. No Windows
(notebook) usa a própria API do sistema (ctypes) para CPU e memória; a
temperatura não é exposta pelo Windows sem programas extras (fica None).
"""

import glob
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()
_last_cpu: Optional[Tuple[float, float]] = None     # (ocioso, total)
_last_pct: Optional[float] = None
_last_at = 0.0


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="ascii", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


IS_WINDOWS = os.name == "nt"


def _win_cpu_times() -> Optional[Tuple[float, float]]:
    """(ocioso, total) pelo GetSystemTimes do Windows."""
    try:
        import ctypes
        from ctypes import wintypes
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        val = lambda ft: (ft.dwHighDateTime << 32) + ft.dwLowDateTime  # noqa: E731
        return float(val(idle)), float(val(kernel) + val(user))       # kernel já inclui o ocioso
    except Exception:
        return None


def cpu_percent() -> Optional[float]:
    """Uso de CPU desde a última leitura (mín. 0,5 s entre amostras)."""
    global _last_cpu, _last_pct, _last_at
    if IS_WINDOWS:
        times = _win_cpu_times()
        if times is None:
            return None
        idle, total = times
    else:
        text = _read("/proc/stat")
        if not text:
            return None
        parts = [float(x) for x in text.splitlines()[0].split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)
        total = sum(parts)
    with _lock:
        now = time.time()
        if _last_cpu is not None and now - _last_at < 0.5:
            return _last_pct
        pct = None
        if _last_cpu is not None and total > _last_cpu[1]:
            pct = round(100.0 * (1.0 - (idle - _last_cpu[0]) / (total - _last_cpu[1])), 1)
        _last_cpu, _last_at = (idle, total), now
        if pct is not None:
            _last_pct = max(0.0, min(100.0, pct))
        return _last_pct


def _win_memory() -> Dict[str, Optional[float]]:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            raise OSError
        return {"mem_pct": float(st.dwMemoryLoad), "mem_total_mb": round(st.ullTotalPhys / 1048576)}
    except Exception:
        return {"mem_pct": None, "mem_total_mb": None}


def _win_process_mb() -> Optional[float]:
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        proc = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(proc, ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / 1048576, 1)
    except Exception:
        pass
    return None


def memory() -> Dict[str, Optional[float]]:
    if IS_WINDOWS:
        return _win_memory()
    text = _read("/proc/meminfo")
    if not text:
        return {"mem_pct": None, "mem_total_mb": None}
    info = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        try:
            info[key] = float(rest.split()[0])
        except (IndexError, ValueError):
            pass
    total = info.get("MemTotal")
    avail = info.get("MemAvailable", info.get("MemFree"))
    if not total or avail is None:
        return {"mem_pct": None, "mem_total_mb": None}
    return {"mem_pct": round(100.0 * (total - avail) / total, 1), "mem_total_mb": round(total / 1024)}


def process_memory_mb() -> Optional[float]:
    if IS_WINDOWS:
        return _win_process_mb()
    text = _read("/proc/self/status")
    for line in (text or "").splitlines():
        if line.startswith("VmRSS:"):
            try:
                return round(float(line.split()[1]) / 1024, 1)
            except (IndexError, ValueError):
                return None
    return None


def temperature_c() -> Optional[float]:
    """Maior temperatura entre as zonas térmicas (SoC da AIBOX)."""
    best = None
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        raw = (_read(path) or "").strip()
        try:
            value = float(raw) / (1000.0 if abs(float(raw)) > 200 else 1.0)
        except ValueError:
            continue
        if 0 < value < 150:
            best = value if best is None else max(best, value)
    return round(best, 1) if best is not None else None


def load_average() -> Optional[float]:
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


def snapshot() -> Dict[str, Any]:
    return {
        "sistema_op": "windows" if IS_WINDOWS else "linux",
        "cpu_pct": cpu_percent(),
        **memory(),
        "processo_mb": process_memory_mb(),
        "temp_c": temperature_c(),
        "carga": load_average(),
        "nucleos": os.cpu_count(),
    }

cpu_percent()   # 1ª amostra: a próxima leitura já devolve o uso real
