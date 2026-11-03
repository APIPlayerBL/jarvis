                   
"""Проверка озвучки: какой движок выбран, сколько ждать первого звука.

    python -m tts.test_voice            # одна фраза выбранным движком
    python -m tts.test_voice "текст"    # своя фраза
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config              
from tts import speaker              

TEXT = "Привет. Я Джарвис. Проверяю голос."


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    text = " ".join(argv) if argv else TEXT
    print(f"Движок: {speaker.current_engine()}")
    print(f"Piper готов: {speaker.piper_ready()}  ·  edge-tts готов: {speaker.edge_ready()}")
    if not config.TTS_ENABLED:
        print("Озвучка выключена (JARVIS_TTS=0) — включаю только для проверки")
        config.TTS_ENABLED = True
    started = time.perf_counter()
    speaker.speak(text)
    print(f"Готово за {time.perf_counter() - started:.2f} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
