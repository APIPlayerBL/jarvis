                  
"""Диагностика слуха: микрофон -> faster-whisper -> текст.

Примеры:
    python dev\\stt_test.py --list
    python dev\\stt_test.py --mic 1
    python dev\\stt_test.py --mic "USB" --seconds 5 --save
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка микрофона и распознавания")
    parser.add_argument("--mic", default=None, help="индекс или часть имени микрофона")
    parser.add_argument("--seconds", type=float, default=5.0, help="длина записи (0 = до паузы)")
    parser.add_argument("--save", action="store_true", help="сохранить wav в assets/")
    parser.add_argument("--list", action="store_true", help="только список устройств")
    args = parser.parse_args()
    setup_logging()

    if args.list:
        Listener.print_devices()
        return 0

    device = int(args.mic) if args.mic and str(args.mic).isdigit() else args.mic
    listener = Listener(device=device)
    listener.open()
    listener.calibrate()
    model = model_manager.ensure("desktop")

    try:
        while True:
            input("Enter — запись >>> ")
            if args.seconds > 0:
                                                                           
                listener.flush()
                chunks = []
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline:
                    try:
                        chunks.append(listener.blocks.get(timeout=0.5))
                    except Exception:                
                        break
                audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
            else:
                audio = listener.record_utterance(max_seconds=12)

            if audio.size == 0:
                print("   Тишина / речь не обнаружена")
                continue
            print(f"   записано {audio.size / config.SAMPLE_RATE:.2f} с")
            if args.save:
                path = config.ASSETS_DIR / f"rec_{int(time.time())}.wav"
                save_wav(audio, path)
                print(f"   сохранено: {path}")
            started = time.perf_counter()
            print("   РАСШИФРОВКА:", repr(model_manager.transcribe(audio, model)),
                  f"({time.perf_counter() - started:.2f} с)")
    except (KeyboardInterrupt, EOFError):
        print("\nВыход.")
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
