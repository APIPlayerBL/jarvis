                     

"""Графический интерфейс Jarvis.



Запуск:

    python gui.py

    python gui.py --no-audio      # без микрофона, только текст

    python gui.py --dry-run       # безопасный режим

    pythonw gui.py                # без консольного окна

"""

from __future__ import annotations



import argparse

import ctypes

import os

import sys

from pathlib import Path



ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



import config              

from common.log import get_logger, setup_logging              



log = get_logger("gui")





def _prepare_windows() -> None:

    """DPI-awareness и AppUserModelID — до создания окна Tk."""

    try:

        ctypes.windll.shcore.SetProcessDpiAwareness(1)

    except Exception:                

        pass

    try:

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jarvis.VoiceAssistant")

    except Exception:                

        pass





def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(description="Jarvis — визуальный интерфейс")

    parser.add_argument("--no-audio", action="store_true", help="не открывать микрофон (текстовый режим)")

    parser.add_argument("--dry-run", action="store_true", help="не выполнять действия на ПК")

    parser.add_argument("--no-tts", action="store_true", help="без озвучки")

    return parser





def main(argv: list[str] | None = None) -> int:

    args = build_parser().parse_args(argv)

    setup_logging()

    _prepare_windows()

    if config.LOW_PRIORITY:

        from common.priority import apply_low_priority



        apply_low_priority()

    if args.dry_run:

        os.environ["JARVIS_DRY_RUN"] = "1"

    if args.no_tts:

        config.TTS_ENABLED = False



    try:

        from ui.app import run_gui

    except Exception as exc:                

        print(f"Не удалось загрузить интерфейс: {exc}")

        print("Проверь, что установлен Tkinter (в стандартном Python для Windows он есть).")

        return 1



    try:

        return run_gui(audio=not args.no_audio, dry_run=args.dry_run, no_tts=args.no_tts)

    except KeyboardInterrupt:

        return 0

    except Exception as exc:                

        log.exception("GUI упал")

        print(f"Интерфейс завершился с ошибкой: {exc}")

        print("Подробности: logs/jarvis.log")

        return 1





if __name__ == "__main__":

    raise SystemExit(main())

