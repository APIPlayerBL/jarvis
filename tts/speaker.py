# tts/speaker.py
"""Озвучка Jarvis: три движка, кэш, потоковая выдача по фразам.

Порядок по умолчанию (`JARVIS_TTS_ENGINE=auto`):
1. **Piper** — локально, мгновенно, нейросетевой голос (нужны exe и модель);
2. **edge-tts** — голоса Microsoft, качественно и бесплатно (но нужен интернет;
   готовый mp3 кэшируется в assets/tts_cache, повтор мгновенный);
3. **SAPI** (pyttsx3) — всегда доступный офлайн-резерв.

`StreamSpeaker` — конвейер для стриминга LLM: пока звучит фраза N, уже
синтезируется фраза N+1, поэтому первый звук появляется почти сразу.
"""
from __future__ import annotations

import hashlib
import io
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import config
from common.log import get_logger

log = get_logger("tts")

_engine = None  # pyttsx3-сингл singleton (SAPI)
_piper_votes = None
# Отдельные замки: синтез можно делать параллельно с проигрыванием, а SAPI и
# аудиоустройство требуют, чтобы их дёргали по одному.
_sapi_lock = threading.RLock()
_play_lock = threading.RLock()
_state_lock = threading.RLock()
_lock = _play_lock  # совместимость: speak() держит замок звука всё время
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_MARKDOWN = re.compile(r"[*_`#>]+")
_WEIRD = re.compile(r"[^\w\s.,!?;:()\-–—%°+/\"']", re.UNICODE)
_SPACES = re.compile(r"\s+")

_state = {
    "engine": None,        # выбранный движок
    "failures": 0,         # подряд неудач текущего движка
    "disabled": set(),     # движки, отключённые до перезапуска
    "cooldown": {},        # движок -> до какого момента не пытаться (сбой сети)
    "out_rate": None,      # частота устройства вывода
}


# --------------------------------------------------------------------------
# текст
# --------------------------------------------------------------------------
def _clean(text: str) -> str:
    text = _URL.sub("ссылка", str(text))
    text = _MARKDOWN.sub("", text)
    text = _WEIRD.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    if len(text) > config.TTS_MAX_CHARS:
        cut = text[: config.TTS_MAX_CHARS]
        boundary = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"), cut.rfind(";"))
        text = cut[: boundary + 1] if boundary > 60 else cut.rstrip() + "…"
    return text


# --------------------------------------------------------------------------
# SAPI (pyttsx3) — офлайн-резерв
# --------------------------------------------------------------------------
def _create_engine():
    import pyttsx3

    engine = pyttsx3.init()
    try:
        voices = engine.getProperty("voices") or []
        for voice in voices:
            voice_id = str(getattr(voice, "id", ""))
            voice_name = str(getattr(voice, "name", ""))
            if "ru_RU" in voice_id or "Irina" in voice_name or "Pavel" in voice_name:
                engine.setProperty("voice", voice.id)
                break
    except Exception as exc:  # noqa: BLE001
        log.debug("Не удалось выбрать русский голос: %s", exc)
    # rate ставим после voice: смена голоса в SAPI сбрасывает скорость
    engine.setProperty("rate", config.TTS_RATE)
    return engine


def _get_engine():
    global _engine
    if _engine is None:
        _engine = _create_engine()
    return _engine


def _reset_engine() -> None:
    global _engine
    if _engine is not None:
        try:
            _engine.stop()
        except Exception:  # noqa: BLE001
            pass
    _engine = None


def _sapi_speak(text: str) -> bool:
    with _sapi_lock:
        for attempt in range(2):
            try:
                engine = _get_engine()
                engine.say(text)
                engine.runAndWait()
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("Ошибка озвучки SAPI (попытка %d): %s", attempt + 1, exc)
                _reset_engine()
    return False


# --------------------------------------------------------------------------
# Piper — локальный нейроголос
# --------------------------------------------------------------------------
def _piper_voice_ok() -> bool:
    """Проверка на практике: Piper иногда отдаёт шум (неподходящая версия/модель).
    Быстрая синтезация одного слова показывает, речь ли это или плоский шум."""
    global _piper_votes
    if _piper_votes is not None:
        return _piper_votes
    result = _piper_synthesize("тест")
    if not result:
        _piper_votes = False
        return False
    try:
        import numpy as np

        pcm, rate = result
        x = np.asarray(pcm, dtype=np.float32)
        if x.size < 128:
            return False
        spectrum = np.abs(np.fft.rfft(x)) + 1e-12
        flatness = float(np.exp(np.mean(np.log(spectrum))) / np.mean(spectrum))
        ok = flatness < 0.72
        if not ok:
            log.warning("Piper выдаёт шум (flatness=%.2f) — отключаю Piper, перехожу на edge/SAPI.", flatness)
        _piper_votes = ok
        return ok
    except Exception as exc:  # noqa: BLE001
        _piper_votes = False
        log.warning("Не удалось проверить качество Piper: %s", exc)
        return False


def piper_ready() -> bool:
    exe, model = config.PIPER_EXE, config.PIPER_MODEL
    if not (exe and model and Path(exe).is_file() and Path(model).is_file()):
        return False
    try:
        return _piper_voice_ok()
    except Exception as exc:  # noqa: BLE001
        log.warning("Проверка Piper не удалась: %s", exc)
        return False


def _decode_wav(data: bytes) -> tuple["object", int] | None:
    import numpy as np

    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            rate = source.getframerate()
            raw = source.readframes(source.getnframes())
    except Exception as exc:  # noqa: BLE001
        log.warning("WAV не читается: %s", exc)
        return None
    if not raw:
        return None
    if width == 2:
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        pcm = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        pcm = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, rate


def _piper_synthesize(text: str) -> tuple["object", int] | None:
    exe, model = config.PIPER_EXE, config.PIPER_MODEL
    if not (exe and model and Path(exe).is_file() and Path(model).is_file()):
        return None
    command = ([sys.executable, exe] if exe.lower().endswith(".py") else [exe])
    command += ["--model", model, "--output_file", "-"]
    try:
        proc = subprocess.run(command, input=text.encode("utf-8"),
                              capture_output=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log.warning("Piper недоступен: %s", exc)
        return None
    if proc.returncode != 0 or not proc.stdout:
        log.warning("Piper вернул ошибку: %s", proc.stderr[:200])
        return None
    return _decode_wav(proc.stdout)


# --------------------------------------------------------------------------
# edge-tts — облачные голоса Microsoft, с кэшем
# --------------------------------------------------------------------------
def _cache_path(text: str, voice: str) -> Path:
    digest = hashlib.sha1(f"{voice}|{config.TTS_RATE}|{text}".encode("utf-8")).hexdigest()[:20]
    return config.TTS_CACHE_DIR / f"{digest}.mp3"


def _cache_trim() -> None:
    try:
        files = list(config.TTS_CACHE_DIR.glob("*.mp3"))
        if len(files) <= config.TTS_CACHE_MAX_FILES:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for item in files[: len(files) - config.TTS_CACHE_MAX_FILES]:
            item.unlink(missing_ok=True)
    except OSError:
        pass


async def _edge_collect(text: str, voice: str) -> bytes:
    import asyncio

    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []

    async def reader() -> None:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                chunks.append(chunk["data"])

    await asyncio.wait_for(reader(), timeout=config.EDGE_TIMEOUT_SEC)
    return b"".join(chunks)


def _edge_synth_mp3(text: str) -> bytes | None:
    import asyncio

    cache = _cache_path(text, config.EDGE_VOICE)
    try:
        if cache.is_file() and cache.stat().st_size > 0:
            return cache.read_bytes()
    except OSError:
        pass
    audio = b""
    for attempt in range(2):
        try:
            audio = asyncio.run(_edge_collect(text, config.EDGE_VOICE))
        except Exception as exc:  # noqa: BLE001 - нет сети/таймаут — уходим на SAPI
            log.warning("edge-tts недоступен (попытка %d): %s: %s",
                        attempt + 1, type(exc).__name__, exc)
            audio = b""
        if audio:
            break
        time.sleep(0.15)  # сервис иногда отвечает пустым аудио — повторяем
    if not audio:
        return None
    try:
        config.TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(cache)
        _cache_trim()
    except OSError as exc:
        log.debug("Не удалось закэшировать озвучку: %s", exc)
    return audio


def _decode_mp3(data: bytes) -> tuple["object", int] | None:
    import av
    import numpy as np

    try:
        with av.open(io.BytesIO(data)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                return None
            rate = int(stream.rate or 24000)
            resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
            frames = []
            for frame in container.decode(stream):
                frames.extend(resampler.resample(frame))
            frames.extend(resampler.resample(None))
            arrays = [f.to_ndarray().reshape(-1) for f in frames if f is not None]
    except Exception as exc:  # noqa: BLE001
        log.warning("MP3 не декодируется: %s", exc)
        return None
    if not arrays:
        return None
    pcm = np.concatenate(arrays).astype(np.float32, copy=False)
    return np.clip(pcm, -1.0, 1.0), rate


def _edge_synthesize(text: str) -> tuple["object", int] | None:
    audio = _edge_synth_mp3(text)
    if not audio:
        return None
    return _decode_mp3(audio)


def edge_ready() -> bool:
    try:
        import edge_tts  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


# --------------------------------------------------------------------------
# проигрывание PCM
# --------------------------------------------------------------------------
def _output_rate() -> int:
    if _state["out_rate"]:
        return _state["out_rate"]
    try:
        from common import audio

        rate = audio.output_rate()
    except Exception:  # noqa: BLE001
        rate = 44100
    _state["out_rate"] = rate
    return rate


def _duck_music() -> None:
    """Приглушить музыку на время речи (иначе речь тонет/склеивается в шум).

    JARVIS_AUDIO_DUCK=1 — не трогать музыку совсем.
    """
    if config.AUDIO_DUCK_GAIN >= 1.0:
        return
    try:
        from hands import music

        if music.player.active:
            music.player.duck(True)
    except Exception:  # noqa: BLE001
        pass


def _unduck_music() -> None:
    try:
        from hands import music

        if music.player.active:
            music.player.duck(False)
    except Exception:  # noqa: BLE001
        pass


def _play_pcm(pcm, rate: int) -> None:
    """Озвучка через общий аудиовыход: ресемплинг + лимитер + одно устройство."""
    if pcm is None or len(pcm) == 0:
        return
    _duck_music()
    try:
        from common import audio

        audio.set_speaking(True)
        audio.play(pcm, int(rate), blocking=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось проиграть звук: %s", exc)
    finally:
        try:
            from common import audio

            audio.set_speaking(False)
        except Exception:  # noqa: BLE001
            pass
        _unduck_music()


def stop() -> None:
    """Прервать текущую озвучку (barge-in/выход)."""
    try:
        from common import audio

        audio.stop()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# выбор движка
# --------------------------------------------------------------------------
_CHAIN_BY_CONFIG = {
    "auto": ("piper", "edge", "sapi"),
    "piper": ("piper", "edge", "sapi"),
    "edge": ("edge", "sapi"),
    "sapi": ("sapi",),
}


def _usable(engine: str) -> bool:
    if engine in _state["disabled"]:
        return False
    if _state["cooldown"].get(engine, 0.0) > time.monotonic():
        return False
    if engine == "piper":
        return piper_ready()
    if engine == "edge":
        return edge_ready()
    return True


def current_engine() -> str:
    """Движок, который реально озвучивает (для GUI/логов)."""
    with _state_lock:
        if _state["engine"] and _usable(_state["engine"]):
            return _state["engine"]
        order = _CHAIN_BY_CONFIG.get(config.TTS_ENGINE, _CHAIN_BY_CONFIG["auto"])
        for engine in order:
            if _usable(engine):
                _state["engine"] = engine
                _state["failures"] = 0
                return engine
        _state["engine"] = "sapi"
        return "sapi"


def _degrade(engine: str) -> None:
    """Движок не сработал — переключаемся на следующий до перезапуска."""
    with _state_lock:
        _state["failures"] += 1
        failures = _state["failures"]
        if failures >= 2:
            # Не отключаем навсегда: сеть/сервис может вернуться. Пауза на
            # 2 минуты, дальше пробуем снова (SAPI тем временем говорит).
            _state["cooldown"][engine] = time.monotonic() + config.EDGE_TIMEOUT_SEC * 15
            _state["engine"] = None
            _state["failures"] = 0
            log.warning("Движок «%s» на паузе, дальше — %s", engine, current_engine())
            return
    log.warning("Озвучка «%s» не удалась (%d подряд)", engine, failures)


def _pcm_for(engine: str, text: str):
    if engine == "piper":
        return _piper_synthesize(text)
    if engine == "edge":
        return _edge_synthesize(text)
    return None


def _speak_locked(text: str) -> None:
    """Озвучить текст выбранным движком (вызывается под _play_lock)."""
    for _attempt in range(2):
        engine = current_engine()
        if engine == "sapi":
            _sapi_speak(text)
            return
        pcm = _pcm_for(engine, text)
        if pcm is not None and len(pcm[0]):
            with _state_lock:
                _state["failures"] = 0
            _play_pcm(*pcm)
            return
        _degrade(engine)
    _sapi_speak(text)


def speak(text: str) -> None:
    """Озвучить текст и дождаться конца (синхронно)."""
    if not config.TTS_ENABLED:
        log.info("TTS выключен: %s", text)
        return
    cleaned = _clean(text)
    if not cleaned:
        return
    log.info("🔊 %s", cleaned)
    with _lock:
        _speak_locked(cleaned)


def speak_async(text: str) -> threading.Thread:
    """Озвучка в фоне — не блокирует разбор следующей команды."""
    thread = threading.Thread(target=speak, args=(text,), name="tts", daemon=True)
    thread.start()
    return thread


# --------------------------------------------------------------------------
# стриминг: фразы приходят по мере генерации ответа
# --------------------------------------------------------------------------
class StreamSpeaker:
    """Конвейер «синтез → проигрывание» для стримингового ответа.

    feed() вызывается из потока движка по мере поступления фраз;
    close() дожидается, пока всё прозвучит. Синтез и звук идут параллельно,
    поэтому длинный ответ звучит непрерывно.
    """

    def __init__(self):
        self.spoken: list[str] = []
        self._texts: queue.Queue = queue.Queue()
        self._pcm: queue.Queue = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._closed = False
        self.engine: str | None = None
        self._synth = threading.Thread(target=self._synth_loop, name="tts-synth", daemon=True)
        self._play = threading.Thread(target=self._play_loop, name="tts-play", daemon=True)
        self._synth.start()
        self._play.start()

    # --- API ---
    def feed(self, text: str) -> None:
        cleaned = _clean(text) if text else ""
        if not cleaned or self._closed:
            return
        if self.engine is None:
            self.engine = current_engine()
        log.info("🔊 %s", cleaned)
        self.spoken.append(cleaned)
        self._texts.put(cleaned)

    def close(self, timeout: float = 60.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._texts.put(None)
        deadline = time.monotonic() + timeout
        self._synth.join(timeout=max(0.1, deadline - time.monotonic()))
        self._play.join(timeout=max(0.1, deadline - time.monotonic()))

    def stop(self) -> None:
        self._stop.set()
        stop()

    @property
    def spoke(self) -> bool:
        return bool(self.spoken)

    # --- внутреннее ---
    def _synth_loop(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._texts.get()
            except Exception:  # noqa: BLE001
                break
            if text is None:
                break
            engine = current_engine()
            if engine == "sapi":
                _sapi_speak(text)
                continue
            try:
                # Синтез идёт без замков звука: пока звучит фраза N,
                # спокойно готовим фразу N+1.
                pcm = _pcm_for(engine, text)
            except Exception as exc:  # noqa: BLE001
                log.warning("Синтез «%s» упал: %s", engine, exc)
                pcm = None
            if pcm is None or not len(pcm[0]):
                _degrade(engine)
                _sapi_speak(text)
                continue
            with _state_lock:
                _state["failures"] = 0
            while not self._stop.is_set():
                try:
                    self._pcm.put(pcm, timeout=0.2)
                    break
                except queue.Full:
                    continue
        try:
            self._pcm.put(None, timeout=1.0)
        except queue.Full:
            pass

    def _play_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._pcm.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            with _play_lock:
                _play_pcm(*item)


def stream_speaker() -> StreamSpeaker | None:
    """Создать конвейер озвучки (None, если TTS выключен)."""
    if not config.TTS_ENABLED:
        return None
    return StreamSpeaker()


def shutdown() -> None:
    stop()
    with _lock:
        _reset_engine()
