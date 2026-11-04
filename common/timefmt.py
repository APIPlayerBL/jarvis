"""Форматирование даты/времени без зависимости от локали Windows."""

from __future__ import annotations



from datetime import datetime



MONTHS_GENITIVE = (

    "января", "февраля", "марта", "апреля", "мая", "июня",

    "июля", "августа", "сентября", "октября", "ноября", "декабря",

)

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")





def local_now() -> datetime:

    return datetime.now().astimezone()





def date_text(now: datetime | None = None, with_weekday: bool = True) -> str:

    now = now or local_now()

    base = f"{now.day} {MONTHS_GENITIVE[now.month - 1]} {now.year} года"

    return f"{base}, {WEEKDAYS[now.weekday()]}" if with_weekday else base





def time_text(now: datetime | None = None) -> str:

    now = now or local_now()

    return now.strftime("%H:%M")





def both_text(now: datetime | None = None) -> str:

    now = now or local_now()

    return f"{time_text(now)}, {date_text(now)}"





def prompt_stamp(now: datetime | None = None) -> str:

    now = now or local_now()

    offset = now.strftime("%z")

    zone = f"UTC{offset[:3]}:{offset[3:]}" if offset else "местное время"

    return f"{WEEKDAYS[now.weekday()]}, {date_text(now, with_weekday=False)}, {time_text(now)} ({zone})"

