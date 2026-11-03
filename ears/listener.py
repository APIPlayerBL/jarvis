                  
"""Микрофон: один постоянно открытый поток + endpointing по тишине.

Что исправлено:
- поток открывается один раз (раньше — на каждую реплику);
- чтение из очереди с таймаутом (раньше `q.get()` мог висеть вечно);
- пре-ролл 0.3 с, чтобы не срезать первый слог;
- калибровка порога тишины под текущий уровень шума;
- mute на время озвучки — микрофон не слышит сам себя.
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from contextlib import contextmanager

import numpy as np

import config
from common.log import get_logger

log = get_logger("ears.listener")


class AudioError(RuntimeError):
    """Поток умер / устройство недоступно."""


class Listener:
    def __init__(self, device: int | str | None = None, samplerate: int | None = None,
                 block_sec: float | None = None):
        self.device = config.MIC_DEVICE if device is None else device
        self.samplerate = int(samplerate or config.SAMPLE_RATE)
        self.block_sec = float(block_sec or config.BLOCK_SEC)
        self.block_size = max(1, int(self.samplerate * self.block_sec))
        self.blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=256)
        self.stream = None
        self.muted = False
        self.overflows = 0
        self.noise_floor = 0.0
        self.speech_threshold = max(0.0025, config.SILENCE_RMS)
        self.silence_threshold = max(0.0018, config.SILENCE_RMS * 0.7)
        self.sensitivity = max(0.3, min(5.0, config.MIC_SENSITIVITY))
        self.device_name = ""
        self.on_level = None
        self._sd = None
        self._interrupt = threading.Event()
        self._level_tick = 0
        frequencies = np.fft.rfftfreq(self.block_size, d=1.0 / self.samplerate)
        self._band_mask = (frequencies >= 150.0) & (frequencies <= 3800.0)

    @property
    def threshold(self) -> float:
        """Порог начала речи (для совместимости с диагностикой)."""
        return self.speech_threshold

    def _band_rms(self, chunk: np.ndarray) -> float:
        """RMS в речевой полосе 150–3800 Гц — отсекает гул/шипение микрофона."""
        if chunk.size < 16:
            return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        spectrum = np.fft.rfft(chunk)
        mask = self._band_mask
        if mask.size != spectrum.size:
            frequencies = np.fft.rfftfreq(chunk.size, d=1.0 / self.samplerate)
            mask = (frequencies >= 150.0) & (frequencies <= 3800.0)
        spectrum[~mask] = 0
        filtered = np.fft.irfft(spectrum, n=chunk.size)
        return float(np.sqrt(np.mean(filtered * filtered)))

    def _effective_thresholds(self) -> tuple[float, float]:
        """(порог начала речи, порог тишины) с учётом ползунка чувствительности."""
        sensitivity = max(0.3, min(5.0, self.sensitivity))
        speech = max(0.0008, self.speech_threshold / sensitivity)
        silence = max(0.0006, self.silence_threshold / sensitivity)
        return speech, silence

    def _recalc_thresholds(self, noise: float) -> None:
        self.noise_floor = max(0.0, noise)
                                                                            
                                                                       
        speech = max(config.SILENCE_RMS, noise * 1.2 + 0.0005)
        self.speech_threshold = min(0.0045, speech)
        self.silence_threshold = min(self.speech_threshold * 0.85, max(0.0018, noise * 1.5))

    def set_sensitivity(self, value: float) -> None:
        self.sensitivity = max(0.3, min(5.0, float(value)))
        log.info("Чувствительность микрофона: %.1fx (порог %.4f)",
                 self.sensitivity, self._effective_thresholds()[0])

                                                                        
    def _resolve_device(self):
        if self.device is None:
            return None
        if isinstance(self.device, int):
            return self.device
        needle = str(self.device).lower()
        try:
            for index, info in enumerate(self._sd.query_devices()):
                if info.get("max_input_channels", 0) > 0 and needle in str(info.get("name", "")).lower():
                    return index
        except Exception:                
            pass
        log.warning("Микрофон %r не найден — использую устройство по умолчанию", self.device)
        return None

    def open(self) -> None:
        import sounddevice as sd

        self._sd = sd
        device = self._resolve_device()
        try:
            self.stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                blocksize=self.block_size,
                device=device,
                callback=self._callback,
            )
            self.stream.start()
        except Exception as exc:                
            raise AudioError(f"Не удалось открыть микрофон: {exc}") from exc

        try:
            index = device if device is not None else sd.default.device[0]
            self.device_name = str(sd.query_devices(index, "input").get("name", "?"))
        except Exception:                
            self.device_name = "?"
        log.info("Микрофон: %s (%s Гц)", self.device_name, self.samplerate)

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:                
                pass
            self.stream = None

                                                                        
    def _callback(self, indata, frames, time_info, status) -> None:                
        if status:
            self.overflows += 1
        if self.muted:
            return
        chunk = indata[:, 0].copy()
        try:
            self.blocks.put_nowait(chunk)
        except queue.Full:
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                pass
            try:
                self.blocks.put_nowait(chunk)
            except queue.Full:
                pass

        self._level_tick += 1
        if self.on_level is not None and self._level_tick % 3 == 0:
            try:
                self.on_level(self._band_rms(chunk) * 35.0)
            except Exception:                
                pass

    def interrupt(self) -> None:
        """Разбудить record_utterance (для текстовых команд/паузы/выхода)."""
        self._interrupt.set()

    def consume_interrupt(self) -> bool:
        if self._interrupt.is_set():
            self._interrupt.clear()
            return True
        return False

    def flush(self) -> None:
        while True:
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                return

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        if self.muted:
            self.flush()

    @contextmanager
    def muted_ctx(self):
        self.set_muted(True)
        try:
            yield
        finally:
            self.set_muted(False)

                                                                        
    def calibrate(self, seconds: float | None = None) -> float:
        """Оценить шум помещения и настроить пороги речи/тишины."""
        seconds = seconds or config.NOISE_CALIBRATION_SEC
        if seconds <= 0:
            return self.speech_threshold
        self.flush()
        samples: list[float] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                chunk = self.blocks.get(timeout=0.5)
            except queue.Empty:
                break
            samples.append(self._band_rms(chunk))
        if samples:
                                                                                 
                                                              
            noise = float(np.percentile(samples, 10))
            self._recalc_thresholds(noise)
        self.flush()
        speech, silence = self._effective_thresholds()
        log.info(
            "Шум: %.5f | речь: %.5f | тишина: %.5f | чувствительность: %.1fx",
            self.noise_floor, speech, silence, self.sensitivity,
        )
        return speech

    def record_utterance(self, max_seconds: float | None = None, silence_sec: float | None = None,
                         min_audio_sec: float | None = None) -> np.ndarray:
        """Записать реплику до паузы. Возвращает пустой массив, если речи не было."""
        if self.stream is None:
            raise AudioError("Поток микрофона не открыт")

        max_seconds = float(max_seconds or config.MAX_SECONDS)
        try:
            from ears import game_mode
            if game_mode.current_mode() == "game":
                max_seconds = min(max_seconds, 4.0)
        except Exception:                
            pass
        silence_sec = float(silence_sec if silence_sec is not None else config.SILENCE_SEC)
        min_audio_sec = float(min_audio_sec if min_audio_sec is not None else config.MIN_AUDIO_SEC)
        block_sec = self.block_size / self.samplerate
        need_silence = max(1, int(round(silence_sec / block_sec)))
        preroll: deque[np.ndarray] = deque(maxlen=max(1, int(config.PREROLL_SEC / block_sec)))
        max_blocks = max(1, int(round(max_seconds / block_sec)))

        chunks: list[np.ndarray] = []
        speech_seen = False
        speech_run = 0
        silence_run = 0
        speech_threshold, silence_threshold = self._effective_thresholds()
        idle_levels: deque[float] = deque(maxlen=30)
        deadline = time.monotonic() + max_seconds + 1.0

        while True:
            if self._interrupt.is_set():
                self._interrupt.clear()
                self.flush()
                return np.zeros(0, dtype=np.float32)
            if time.monotonic() > deadline:
                log.debug("Таймаут записи")
                break
            try:
                chunk = self.blocks.get(timeout=0.5)
            except queue.Empty:
                if self.stream is not None and not self.stream.active:
                    raise AudioError("Поток микрофона остановился")
                continue

            rms = self._band_rms(chunk)
            if not speech_seen:
                preroll.append(chunk)
                idle_levels.append(rms)
                                                                          
                                                                     
                if len(idle_levels) >= 10 and len(idle_levels) % 5 == 0:
                    try:
                        from ears import game_mode
                        in_game = game_mode.current_mode() == "game"
                    except Exception:                
                        in_game = False
                    quiet = float(np.percentile(idle_levels, 10))
                    if (not in_game) and quiet < self.noise_floor * 0.8:
                        self._recalc_thresholds(quiet)
                        speech_threshold, silence_threshold = self._effective_thresholds()
                    elif in_game and quiet > self.noise_floor * 1.3:
                        self._recalc_thresholds(max(self.noise_floor, quiet * 0.9))
                        speech_threshold, silence_threshold = self._effective_thresholds()
                if rms > speech_threshold:
                    speech_run += 1
                    if speech_run >= 3:
                        speech_seen = True
                        chunks.extend(preroll)
                        silence_run = 0
                        log.debug("Речь: rms=%.5f (порог %.5f)", rms, speech_threshold)
                else:
                    speech_run = 0
                continue

            chunks.append(chunk)
            if rms > silence_threshold:
                silence_run = 0
            else:
                silence_run += 1
                if silence_run >= need_silence:
                    break
            if len(chunks) >= max_blocks:
                break

        self.flush()
        if not speech_seen or not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks)
        if audio.size < int(self.samplerate * min_audio_sec):
            return np.zeros(0, dtype=np.float32)
        return audio

                                                                        
    @staticmethod
    def list_devices() -> list[tuple[int, str, int]]:
        """[(index, name, channels)] для выпадающего списка в UI."""
        import sounddevice as sd

        result = []
        for index, info in enumerate(sd.query_devices()):
            channels = int(info.get("max_input_channels", 0))
            if channels > 0:
                result.append((index, str(info.get("name", "?")), channels))
        return result

    @staticmethod
    def print_devices() -> None:
        print("Устройства ввода:")
        for index, name, channels in Listener.list_devices():
            print(f"  [{index:>2}] {name}  ({channels} кан.)")
