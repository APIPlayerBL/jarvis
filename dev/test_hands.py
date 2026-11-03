                     
"""Сквозной текстовый тест: router -> brain -> execute.

По умолчанию dry-run (ничего не запускается и не закрывается).
Боевой режим:  python dev\\test_hands.py --live
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.log import setup_logging              
from hands.hands import execute_verbose              
from memory import intent_cache, router              


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="реально выполнять действия")
    args = parser.parse_args()
    setup_logging()
    if not args.live:
        os.environ["JARVIS_DRY_RUN"] = "1"
        print("Режим dry-run (действия не выполняются). --live для боевого режима.")

    print("Вводи команды, 'quit' — выход.\n")
    while True:
        try:
            text = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break
        if not text or text.lower() in {"quit", "exit"}:
            break

        intent = router.route(text)
        source = "router"
        if intent is None:
            from brain.brain import process_command

            source = "brain"
            intent = process_command(text)
        print(f"   [{source}] {intent}")
        result, ok = execute_verbose(intent)
        if ok and source == "brain" and intent.get("type") in {"tool", "tools"}:
            intent_cache.save_intent(text, intent)
        print("   ->", result, "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
