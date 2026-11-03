                    
"""Jarvis не должен мешать работе компьютера — особенно играм.

Что делаем:
- процесс получает **BELOW_NORMAL** приоритет: планировщик отдаёт Jarvis точки
  времени только когда активные программы (игра, компилятор, браузер) не ждут;
- по желанию — **EcoQoS** (Windows 11): приоритет на энергоэффективные ядра;
- потоки озвучки/микрофона не трогаем: у них нормальный приоритет, чтобы звук
  не «дробился»;
- остальная экономия — в коде: выгрузка моделей по простою, один аудиопоток,
  ленивые импорты, троттлинг анимации UI в фоне.
"""
from __future__ import annotations

import ctypes
import sys

import config
from common.log import get_logger

log = get_logger("priority")

_BELOW_NORMAL = 0x00004000
_IDLE = 0x00000040
_APPLIED = {"done": False, "priority": "normal", "eco": False}


class _ThrottlingState(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32),
                ("ControlMask", ctypes.c_uint32),
                ("StateMask", ctypes.c_uint32)]


def apply_low_priority(idle: bool = False) -> dict:
    """Понизить приоритет текущего процесса. Возвращает что получилось."""
    if _APPLIED["done"]:
        return dict(_APPLIED)
    _APPLIED["done"] = True
    if sys.platform != "win32":
        return dict(_APPLIED)

    try:
        kernel32 = ctypes.windll.kernel32
                                                                           
                                                               
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetPriorityClass.restype = ctypes.c_bool
        kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
        kernel32.GetPriorityClass.restype = ctypes.c_uint32
        handle = kernel32.GetCurrentProcess()
        want = _IDLE if idle else _BELOW_NORMAL
        ok = kernel32.SetPriorityClass(handle, want)
        current = kernel32.GetPriorityClass(handle)
        _APPLIED["priority"] = {_BELOW_NORMAL: "below_normal", _IDLE: "idle"}.get(
            current, "normal" if not ok else "below_normal")
    except Exception as exc:                
        log.debug("SetPriorityClass: %s", exc)
        return dict(_APPLIED)

    if config.ECO_MODE:
        try:
            state = _ThrottlingState(1, 1, 1)                   
            ok = ctypes.windll.kernel32.SetProcessInformation(
                ctypes.windll.kernel32.GetCurrentProcess(), 4,
                ctypes.byref(state), ctypes.sizeof(state))
            _APPLIED["eco"] = bool(ok)
        except Exception as exc:                                               
            log.debug("EcoQoS недоступен: %s", exc)

    log.info("Приоритет процесса: %s%s", _APPLIED["priority"],
             ", eco-режим" if _APPLIED["eco"] else "")
    return dict(_APPLIED)


def status() -> dict:
    return dict(_APPLIED)


if __name__ == "__main__":
    print(apply_low_priority())
