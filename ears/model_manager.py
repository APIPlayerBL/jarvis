# ears/model_manager.py
"""Whisper-модели: основная (команда) + лёгкая (только wake word).

faster_whisper импортируется лениво: тесты, текстовый режим и локальный
роутер не должны платить за тяжёлые зависимости.

Две модели вместо одной:
- **wake** (`tiny`, ~75 МБ) слушает всё время в простое и проверяет, прозвучало
  ли «Джарвис»: дешёвая по CPU и всегда в памяти;
- **main** (`small`) распознаёт саму команду — грузится только когда wake word
  действительно услышан, а после `JARVIS_STT_IDLE` секунд простоя выгружается
  (`unload_idle`), чтобы не держать ~0.5 ГБ RAM без дела.
"""
from __future__ import annotations

import gc
import threading
import time

import numpy as np

import config
from common.log import get_logger

log = get_logger("ears.stt")

_SIZES = {"game": config.WHISPER_SIZES["game"], "desktop": config.WHISPER_SIZES["desktop"]}
_state: dict = {"model": None, "size": None, "device": None, "compute": None,
                "lang": config.LANGUAGE, "vad": config.USE_VAD, "last_used": 0.0}
_wake: dict = {"model": None, "size": None, "failed": False, "last_used": 0.0}
_loading = False
# Один поток на загрузку: wake-путь, warmup и команда не должны тащить модель
# параллельно.
_LOADING_LOCK = threading.RLock()


def configure(device: str | None = None, compute_type: str | None = None,
              language: str | None = None, vad: bool | None = None) -> None:
    if device:
        _state["device"] = device
    if compute_type:
        _state["compute"] = compute_type
    if language:
        _state["lang"] = language
    if vad is not None:
        _state["vad"] = bool(vad)


def _load(size: str):
    from faster_whisper import WhisperModel

    device = _state["device"] or config.STT_DEVICE
    compute = _state["compute"] or config.STT_COMPUTE
    log.info("Загружаю Whisper '%s' (%s/%s, потоков %d)...", size, device, compute, config.CPU_THREADS)
    try:
        return WhisperModel(size, device=device, compute_type=compute, cpu_threads=config.CPU_THREADS)
    except TypeError:
        return WhisperModel(size, device=device, compute_type=compute)


def _warmup(model) -> float:
    silence = np.zeros(int(config.SAMPLE_RATE * 0.5), dtype=np.float32)
    started = time.perf_counter()
    try:
        transcribe(silence, model)
    except Exception as exc:  # noqa: BLE001
        log.warning("Прогрев Whisper не удался: %s", exc)
    return time.perf_counter() - started


def ensure(mode: str = "desktop", verbose: bool = True):
    desired = _SIZES.get(mode, _SIZES["desktop"])
    with _LOADING_LOCK:
        if _state["model"] is not None and _state["size"] == desired:
            _state["last_used"] = time.monotonic()
            return _state["model"]

        global _loading
        old = _state["model"]
        _state["model"] = None
        if old is not None:
            del old
            gc.collect()

        _loading = True
        started = time.perf_counter()
        try:
            model = _load(desired)
        finally:
            _loading = False
        if verbose:
            log.info("Whisper '%s' готова за %.1f с", desired, time.perf_counter() - started)
        _warmup(model)
        _state["model"] = model
        _state["size"] = desired
        _state["last_used"] = time.monotonic()
        return model


def ensure_wake():
    """Лёгкая модель для проверки wake word (None — если её нет)."""
    size = config.STT_WAKE_SIZE
    if not size or _wake["failed"]:
        return None
    if _wake["model"] is not None and _wake["size"] == size:
        _wake["last_used"] = time.monotonic()
        return _wake["model"]
    with _LOADING_LOCK:
        # Повторная проверка под блокировкой.
        if _wake["model"] is not None and _wake["size"] == size:
            _wake["last_used"] = time.monotonic()
            return _wake["model"]
        try:
            model = _load(size)
        except Exception as exc:  # noqa: BLE001 - нет модели в кэше/нет сети: работаем без неё
            _wake["failed"] = True
            log.warning("Лёгкая wake-модель '%s' недоступна (%s) — слушаю основной", size, exc)
            return None
    _wake.update(model=model, size=size, last_used=time.monotonic())
    log.info("Wake-модель '%s' загружена", size)
    return model


def main_model_loaded() -> bool:
    return _state["model"] is not None


def wake_model_loaded() -> bool:
    return _wake["model"] is not None


def unload_main() -> bool:
    """Выгрузить тяжёлую модель (освободить RAM). Возвращает True, если выгрузили."""
    with _LOADING_LOCK:
        if _state["model"] is None or _loading:
            return False
        _state["model"] = None
        _state["size"] = None
    gc.collect()
    log.info("Основная Whisper-модель выгружена (простой)")
    return True


def unload_all() -> None:
    unload_main()
    _wake["model"] = None
    _wake["size"] = None
    gc.collect()


def idle_seconds() -> float:
    if not _state["last_used"]:
        return 0.0
    return time.monotonic() - _state["last_used"]


def unload_idle(idle_sec: float | None = None) -> bool:
    """Выгрузить основную модель, если давно не было речи."""
    limit = config.STT_IDLE_UNLOAD_SEC if idle_sec is None else idle_sec
    if limit <= 0:
        return False
    if _state["model"] is None or idle_seconds() < limit:
        return False
    return unload_main()


def transcribe(audio: np.ndarray, model=None) -> str:
    """Аудио float32 16 кГц -> текст (быстрый режим, один проход)."""
    if audio is None or getattr(audio, "size", 0) == 0:
        return ""
    audio = np.asarray(audio, dtype=np.float32)
    # Тихий микрофон: поднимаем уровень перед распознаванием (без клиппинга).
    peak = float(np.max(np.abs(audio)))
    if 0.0005 < peak < 0.5:
        gain = min(4.0, 0.9 / peak)
        audio = audio * gain
        log.debug("Нормализация аудио: peak=%.4f, gain=%.1f", peak, gain)
    if model is None:
        model = _state["model"] or ensure("desktop", verbose=False)
    _state["last_used"] = time.monotonic()

    kwargs = dict(
        language=_state["lang"],
        beam_size=1,
        vad_filter=_state["vad"],
        condition_on_previous_text=False,
        initial_prompt=config.STT_INITIAL_PROMPT,
        without_timestamps=True,
        temperature=0.0,
    )
    try:
        segments, _info = model.transcribe(audio, **kwargs)
    except TypeError:
        kwargs.pop("without_timestamps", None)
        kwargs.pop("initial_prompt", None)
        segments, _info = model.transcribe(audio, **kwargs)
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()


def transcribe_wake(audio: np.ndarray) -> str:
    """Распознать реплику лёгкой моделью (только чтобы услышать wake word)."""
    model = ensure_wake()
    if model is None:
        return transcribe(audio)
    _wake["last_used"] = time.monotonic()
    return transcribe(audio, model)


def main_size(mode: str = "desktop") -> str:
    """Размер основной модели для режима (даже если она сейчас выгружена)."""
    return _SIZES.get(mode, _SIZES["desktop"])


def active_size() -> str | None:
    return _state["size"]


def wake_size() -> str | None:
    return _wake["size"]
