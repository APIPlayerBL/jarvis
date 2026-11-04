                     

"""Настроить авто-воспроизведение музыки в фоновом окне.



Зачем: в Edge/Chrome 136+ нельзя включить отладку на основном профиле, а браузер

без отладки не даёт нажать «играть» сам. Поэтому Jarvis держит отдельный

музыкальный профиль (`assets/browser_profile`), окно которого стоит за

пределами экрана: звук идёт, окна не видно.



    python setup_music.py           # открыть окно и войти в VK / Яндекс.Музыку

    python setup_music.py --check   # проверить настройку

    python setup_music.py --off     # выключить авто-воспроизведение

    python setup_music.py --service vk|yandex

"""

from __future__ import annotations



import argparse

import sys

import time

from pathlib import Path



ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



import config              

from common.log import get_logger, setup_logging              



log = get_logger("setup_music")



SERVICES = {

    "vk": "https://vk.com/audio",

    "yandex": "https://music.yandex.ru/",

    "zvuk": "https://zvuk.com/",

}





def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description="Авто-воспроизведение музыки Jarvis")

    parser.add_argument("--check", action="store_true", help="проверить настройку")

    parser.add_argument("--off", action="store_true", help="выключить авто-воспроизведение")

    parser.add_argument("--service", choices=sorted(SERVICES), default=None,

                        help="какой сервис открыть для входа")

    args = parser.parse_args(argv)

    setup_logging()



    if args.off:

        config.save_env_value("JARVIS_MUSIC_AUTOPLAY", "0")

        config.MUSIC_AUTOPLAY = False

        print("Авто-воспроизведение выключено: музыка откроется вкладкой в основном браузере.")

        return 0



    if args.check:

        service = config.MUSIC_WEB

        print(f"Авто-воспроизведение: {'вкл' if config.MUSIC_AUTOPLAY else 'выкл'}")

        print(f"Сервис: {service}")

        print(f"Профиль: {config.BROWSER_PROFILE_DIR} "

              f"({'есть' if config.BROWSER_PROFILE_DIR.exists() else 'ещё не создан'})")

        print(f"Локальная музыка: {config.MUSIC_DIR}")

        try:

            from hands import music



            print(f"Треков в библиотеке: {len(music.library())}")

        except Exception as exc:                

            print(f"Библиотека: ошибка {exc}")

        if not config.MUSIC_AUTOPLAY:

            print("Подсказка: python setup_music.py — войти в сервис и включить авто-плей.")

        return 0



    service = args.service or config.MUSIC_WEB if config.MUSIC_WEB in SERVICES else "yandex"

    url = SERVICES.get(service, SERVICES["yandex"])

    print("Открываю окно музыкального профиля.")

    print("1) Войди в аккаунт (VK / Яндекс) — логин сохранится в профиле Jarvis.")

    print("2) Включи любой трек один раз, чтобы браузер запомнил разрешение на звук.")

    print("3) Закрой окно и нажми Enter здесь.\n")



    try:

        from hands import browser



        page = browser.background_player().page()

        if page is None:

            print("Не удалось открыть окно Playwright. Проверь установку playwright.")

            return 1

        page.goto(url, wait_until="domcontentloaded",

                  timeout=int(config.BROWSER_TIMEOUT_SEC * 1000))

        page.wait_for_timeout(1200)

        try:

            page.bring_to_front()

        except Exception:                

            pass

        try:

            input("Enter — когда закончишь: ")

        except EOFError:

            time.sleep(5)

        browser.background_player().close()

    except Exception as exc:                

        print(f"Ошибка: {exc}")

        return 1



    config.save_env_value("JARVIS_MUSIC_AUTOPLAY", "1")

    if args.service:

        config.save_env_value("JARVIS_MUSIC_WEB", args.service)

    config.MUSIC_AUTOPLAY = True

    if args.service:

        config.MUSIC_WEB = args.service

    print("\nГотово. Теперь «включи музыку кишлак» играет без видимого окна.")

    print("Настройки: python setup_music.py --check")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

