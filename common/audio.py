                 
"""Единый безопасный вывод звука: TTS и музыка играют одинаково и не шумят.

Почему это важно: у Windows несколько устройств вывода с разными частотами и
каналами (колонки 8 каналов 44.1 кГц, HDMI 48 кГц, Bluetooth-гарнитура 8 кГц).
Если открыть поток с «чужими» параметрами, драйвер выдаёт хрип/шум — именно
так и звучал «сильный шум в наушниках».

Правила:
1. играем на **одном выбранном устройстве** (`JARVIS_AUDIO_OUTPUT`, по умолчанию
   системное), частота — его собственная;
2. перед выводом сигнал **ресемплируется и ограничивается** по амплитуде
   (лимитер), поэтому клиппинг-треск невозможен;
3. музыка **приглушается на время речи** (duck), а не играет вместе с TTS;
4. параметры каждого воспроизведения пишутся в лог (устройство, частота, каналы,
   пик) — по ним видно, что пошло не так.
"""
from __future__ import annotations

import sys
import threading

import config
from common.log import get_logger

log = get_logger("audio")

_state: dict = {"device_index": None, "device_name": "", "rate": 48000, "ready": False}
_lock = threading.RLock()
                                                                               
                                                
_speaking = threading.Event()


def set_speaking(flag: bool) -> None:
    if flag:
        _speaking.set()
    else:
        _speaking.clear()


def is_speaking() -> bool:
    return _speaking.is_set()


                                                                            
                   
                                                                            
def _resolve_device():
    """Индекс устройства из JARVIS_AUDIO_OUTPUT (номер или часть имени)."""
    import sounddevice as sd

    wanted = (config.AUDIO_OUTPUT or "").strip()
    if not wanted:
        info = sd.query_devices(kind="output")
        return int(info["index"]), str(info["name"])
    if wanted.lstrip("-").isdigit():
        info = sd.query_devices(int(wanted), "output")
        return int(wanted), str(info["name"])
    needle = wanted.lower()
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_output_channels", 0) > 0 and needle in str(info["name"]).lower():
            return index, str(info["name"])
    log.warning("Устройство вывода «%s» не найдено — использую системное", wanted)
    info = sd.query_devices(kind="output")
    return int(info["index"]), str(info["name"])


def configure(force: bool = False) -> dict:
    """Определить устройство и частоту (один раз, либо принудительно)."""
    with _lock:
        if _state["ready"] and not force:
            return dict(_state)
        try:
            import sounddevice as sd

            index, name = _resolve_device()
            info = sd.query_devices(index, "output")
            rate = int(info.get("default_samplerate") or 48000)
            if rate < 8000:                                      
                rate = 44100
            if rate < 16000:
                log.warning("Устройство «%s» работает на %d Гц (режим телефонии) — "
                            "звук будет телефонного качества", name, rate)
            _state.update(device_index=index, device_name=name, rate=rate, ready=True)
            log.info("Аудиовыход: [%d] %s, %d Гц", index, name, rate)
        except Exception as exc:                
            log.warning("Не удалось определить устройство вывода: %s", exc)
            _state.update(ready=True, device_index=None, rate=44100)
        return dict(_state)


def output_info() -> dict:
    return configure()


def output_rate() -> int:
    return int(configure()["rate"])


                                                                            
                   
                                                                            
def resample(pcm, src: int, dst: int):
    """Линейный ресемплинг (дешёвый и предсказуемый)."""
    import numpy as np

    if pcm is None or len(pcm) == 0 or src == dst:
        return pcm
    count = max(1, int(round(len(pcm) * dst / max(1, src))))
    positions = np.linspace(0, len(pcm) - 1, count, dtype=np.float32)
    return np.interp(positions, np.arange(len(pcm), dtype=np.float32), pcm).astype(np.float32)


def limit(pcm, peak: float | None = None, gain: float = 1.0):
    """Лимитер: масштабирует, если пик выше нормы, и убирает NaN/клиппинг."""
    import numpy as np

    if pcm is None:
        return pcm
    data = np.nan_to_num(np.asarray(pcm, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if gain != 1.0:
        data = data * float(gain)
    ceiling = float(peak if peak is not None else config.AUDIO_MAX_PEAK)
    top = float(np.max(np.abs(data))) if data.size else 0.0
    if top > ceiling:
        data = data * (ceiling / top)
    return np.clip(data, -ceiling, ceiling)


def prepare(pcm, rate: int, channels: int = 1, gain: float = 1.0):
    """Привести сигнал к частоте устройства и безопасному уровню."""
    import numpy as np

    info = configure()
    data = resample(np.asarray(pcm, dtype=np.float32).reshape(-1), int(rate), int(info["rate"]))
    data = limit(data, gain=gain)
    if channels > 1:
        data = np.repeat(data[:, None], channels, axis=1)
    return data, int(info["rate"])


                                                                            
                 
                                                                            
def play(pcm, rate: int, *, channels: int = 1, gain: float = 1.0, blocking: bool = True) -> bool:
    """Проиграть сигнал на выбранном устройстве с ресемплингом и лимитером."""
    try:
        import sounddevice as sd

        data, out_rate = prepare(pcm, rate, channels=channels, gain=gain)
        if data.size == 0:
            return False
        info = configure()
        if info["device_index"] is None:
            sd.play(data, out_rate, blocking=blocking)
        else:
            sd.play(data, out_rate, device=info["device_index"], blocking=blocking)
        return True
    except Exception as exc:                
        log.warning("Не удалось проиграть звук: %s", exc)
        return False


def stop() -> None:
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:                
        pass


def test_tone(seconds: float = 1.5) -> dict:
    """Диагностика: короткий тихий тон на выбранном устройстве."""
    import numpy as np

    info = configure(force=True)
    rate = info["rate"]
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    tone = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    ok = play(tone, rate, blocking=True)
    return {"ok": ok, **info}


if __name__ == "__main__":
    import sounddevice as sd

    print("Устройства вывода:")
    default = sd.query_devices(kind="output")
    for index, item in enumerate(sd.query_devices()):
        if item.get("max_output_channels", 0) > 0:
            mark = "*" if index == int(default["index"]) else " "
            print(f" {mark}[{index:>2}] {str(item['name'])[:52]:<54} "
                  f"{int(item['default_samplerate'])} Гц")
    print("\nТон 1.5 с на выбранном устройстве…")
    print(test_tone())
    if sys.platform == "win32":
        print("Выбрать устройство: JARVIS_AUDIO_OUTPUT=<индекс или часть имени> в .env")
