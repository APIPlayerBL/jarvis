                   
"""Диагностика микрофона: уровень шума, порог речи и распознавание.

Запуск:
    python dev\\mic_check.py --seconds 6
    python dev\\mic_check.py --mic 1 --seconds 8 --save
"""
from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np              

import config              
from common.log import setup_logging              
from ears import model_manager              
from ears.listener import Listener              


def save_wav(audio: np.ndarray, path: Path) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(config.SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def record_fixed(listener: Listener, seconds: float) -> np.ndarray:
    listener.flush()
    chunks: list[np.ndarray] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            chunks.append(listener.blocks.get(timeout=0.5))
        except Exception:                
            break
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка чувствительности микрофона")
    parser.add_argument("--mic", default=None, help="индекс или часть имени микрофона")
    parser.add_argument("--seconds", type=float, default=6.0, help="сколько секунд писать")
    parser.add_argument("--save", action="store_true", help="сохранить запись в assets/")
    args = parser.parse_args()
    setup_logging()

    device = int(args.mic) if args.mic and str(args.mic).isdigit() else args.mic
    listener = Listener(device=device)
    listener.open()
    listener.calibrate()
    speech_thr, silence_thr = listener._effective_thresholds()

    print(f"\nМикрофон: {listener.device_name}")
    print(f"Шум: {listener.noise_floor:.5f} | порог речи: {speech_thr:.5f} | "
          f"порог тишины: {silence_thr:.5f} | чувствительность: {listener.sensitivity:.1f}x")
    print(f"\nГоворите обычным голосом {args.seconds:.0f} секунд: «Джарвис, открой ютуб»…")
    for remaining in range(3, 0, -1):
        print(f"  {remaining}…", end=" ", flush=True)
        time.sleep(1.0)
    print("пишу!")

    audio = record_fixed(listener, args.seconds)
    listener.close()
    if audio.size == 0:
        print("Нет аудио — микрофон не отдаёт данные.")
        return 1

    block = listener.block_size
    blocks = [audio[i:i + block] for i in range(0, len(audio) - block + 1, block)]
    raw = np.array([float(np.sqrt(np.mean(b ** 2))) for b in blocks])
    band = np.array([listener._band_rms(b) for b in blocks])
    print("\nУровни (100 мс блоки):")
    print("  raw : p10=%.5f  p50=%.5f  p90=%.5f  max=%.5f"
          % tuple(np.percentile(raw, [10, 50, 90, 100])))
    print("  речь: p10=%.5f  p50=%.5f  p90=%.5f  max=%.5f"
          % tuple(np.percentile(band, [10, 50, 90, 100])))

    above = band > speech_thr
    detected = any(above[i:i + 3].all() for i in range(len(above) - 2))
    print(f"\nДетектор речи: {'СЛЫШИТ' if detected else 'НЕ СЛЫШИТ'} "
          f"(блоков выше порога: {int(above.sum())}/{len(band)})")

    if args.save:
        path = config.ASSETS_DIR / f"check_{int(time.time())}.wav"
        save_wav(audio, path)
        print(f"Сохранено: {path}")

    model = model_manager.ensure("desktop", verbose=False)
    started = time.perf_counter()
    text = model_manager.transcribe(audio, model)
    print(f"\nРаспознано ({time.perf_counter() - started:.2f} с): {text!r}")

    if not detected:
        print("\nСовет: поднимите чувствительность — добавьте в .env строку")
        print("  JARVIS_SENSITIVITY=2.0")
        print("и перезапустите. Также проверьте в Windows: Параметры → Система → Звук →")
        print("устройство ввода → громкость микрофона и «Усиление».")
    elif not text:
        print("\nРечь detected, но текст пуст — попробуйте говорить ближе к микрофону.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
