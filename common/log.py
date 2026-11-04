"""Единое логирование: консоль + ротация файла."""

from __future__ import annotations



import logging

import sys

from logging.handlers import RotatingFileHandler



import config



_CONFIGURED = False





def setup_logging(level: int = logging.INFO) -> None:

    global _CONFIGURED

    if _CONFIGURED:

        return

    _CONFIGURED = True



    try:

        if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):

            sys.stdout.reconfigure(encoding="utf-8", errors="replace")

        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):

            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    except Exception:

        pass



    root = logging.getLogger()

    root.setLevel(level)



    console = logging.StreamHandler(sys.stdout)

    console.setLevel(level)

    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))

    root.addHandler(console)



    try:

        config.LOG_DIR.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(

            config.JARVIS_LOG_PATH,

            maxBytes=2_000_000,

            backupCount=3,

            encoding="utf-8",

        )

        file_handler.setLevel(logging.DEBUG)

        file_handler.setFormatter(

            logging.Formatter(

                "%(asctime)s %(levelname)-7s %(name)s [%(filename)s:%(lineno)d]: %(message)s"

            )

        )

        root.addHandler(file_handler)

    except OSError:

        pass



                                                

    for noisy in (

        "httpx",

        "httpx2",

        "httpcore",

        "httpcore2",

        "urllib3",

        "faster_whisper",

        "openai",

        "huggingface_hub",

        "comtypes",

    ):

        logging.getLogger(noisy).setLevel(logging.WARNING)





def get_logger(name: str) -> logging.Logger:

    return logging.getLogger(name)

