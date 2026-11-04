"""Тёмная тема и вспомогательные функции отрисовки."""

from __future__ import annotations



BG = "#0b0f14"

SURFACE = "#111823"

SURFACE_2 = "#18212e"

SURFACE_3 = "#202c3d"

BORDER = "#26344a"

TEXT = "#e7edf5"

MUTED = "#8b99ad"

DIM = "#5d6b7f"

ACCENT = "#38c8ff"

GREEN = "#43dfa1"

AMBER = "#ffb454"

RED = "#ff6b7a"

PURPLE = "#b48cff"



FONT = ("Segoe UI", 10)

FONT_SMALL = ("Segoe UI", 9)

FONT_BOLD = ("Segoe UI Semibold", 10)

FONT_TITLE = ("Segoe UI Semibold", 18)

FONT_ORB = ("Segoe UI Semibold", 26)

FONT_CHAT = ("Segoe UI", 11)

FONT_MONO = ("Consolas", 9)



STATE_COLORS = {

    "idle": MUTED,

    "loading": ACCENT,

    "listening": GREEN,

    "waiting": AMBER,

    "thinking": PURPLE,

    "speaking": ACCENT,

    "paused": MUTED,

    "text": AMBER,

    "error": RED,

    "stopped": DIM,

}



STATE_LABELS = {

    "idle": "Готов",

    "loading": "Загрузка",

    "listening": "Слушаю",

    "waiting": "Жду команду",

    "thinking": "Думаю",

    "speaking": "Говорю",

    "paused": "Пауза",

    "text": "Текстовый режим",

    "error": "Ошибка",

    "stopped": "Остановлен",

}





def hex_to_rgb(value: str) -> tuple[int, int, int]:

    value = value.lstrip("#")

    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)





def blend(color_a: str, color_b: str, t: float) -> str:

    """Смешать два цвета (t=0 -> a, t=1 -> b)."""

    t = max(0.0, min(1.0, t))

    ar, ag, ab = hex_to_rgb(color_a)

    br, bg, bb = hex_to_rgb(color_b)

    return "#{:02x}{:02x}{:02x}".format(

        int(ar + (br - ar) * t),

        int(ag + (bg - ag) * t),

        int(ab + (bb - ab) * t),

    )

