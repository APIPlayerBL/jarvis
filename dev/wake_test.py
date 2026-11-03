                   
"""Проверка wake word + sanity без запуска brain и без выполнения действий.

Пример:
    python dev\\wake_test.py --mic 1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config              
from common.log import setup_logging              
from ears import model_manager              
from ears.listener import Listener              
from ears.wake import split_wake              
from memory import router, sanity              


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка wake word")
    parser.add_argument("--mic", default=None, help="индекс или часть имени микрофона")
    args = parser.parse_args()
    setup_logging()

    device = int(args.mic) if args.mic and str(args.mic).isdigit() else args.mic
    listener = Listener(device=device)
    listener.open()
    listener.calibrate()
    model = model_manager.ensure("desktop", verbose=False)

    print("Скажи 'Джарвис' и команду. Ctrl+C — выход.\n")
    try:
        while True:
            audio = listener.record_utterance()
            if audio.size == 0:
                continue
            text = model_manager.transcribe(audio, model)
            if not text:
                continue
            found, rest = split_wake(text)
            if not found:
                print(f"   [шум] {text!r}")
                continue
            print(f"   [wake] остаток: {rest!r}")
            if not rest:
                print("      (только wake word)")
                continue
            if not sanity.passes(rest):
                print("      sanity: отклонено (переспрос)")
                continue
            intent = router.route(rest)
            print(f"      sanity: ok | router: {intent if intent else '-> brain'}")
    except KeyboardInterrupt:
        print("\nВыход.")
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
