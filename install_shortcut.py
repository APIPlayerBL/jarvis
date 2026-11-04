                     

"""Создаёт простые ярлыки запуска Jarvis.



По умолчанию — на рабочем столе и в меню «Пуск»:



    python install_shortcut.py

    python install_shortcut.py --desktop

    python install_shortcut.py --start-menu

    python install_shortcut.py --remove



Запуск без консольного окна (pythonw) и с иконкой assets/jarvis.ico.

"""

from __future__ import annotations



import argparse

import ctypes

import os

import subprocess

import sys

from pathlib import Path



ROOT = Path(__file__).resolve().parent

GUI = ROOT / "gui.py"

PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

ICON = ROOT / "assets" / "jarvis.ico"

DEFAULT_NAME = "Jarvis"



CSIDL_DESKTOPDIRECTORY = 0x10

CSIDL_PROGRAMS = 0x02





def known_folder(csidl: int) -> Path:

    buffer = ctypes.create_unicode_buffer(260)

    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)

    return Path(buffer.value)





def shortcut_paths(name: str = DEFAULT_NAME) -> dict[str, Path]:

    return {

        "desktop": known_folder(CSIDL_DESKTOPDIRECTORY) / f"{name}.lnk",

        "start-menu": known_folder(CSIDL_PROGRAMS) / f"{name}.lnk",

    }





def launcher() -> Path:

    if PYTHONW.is_file():

        return PYTHONW

    if PYTHON.is_file():

        return PYTHON

    raise FileNotFoundError("Не найден Python в .venv. Запусти из папки проекта.")





def build_arguments(gui_path: Path = GUI) -> str:

    return f'"{gui_path}"'





def _create_via_comtypes(link: Path) -> None:

    import comtypes

    import comtypes.client



    comtypes.CoInitialize()

    try:

        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)

        shortcut = shell.CreateShortcut(str(link))

        shortcut.TargetPath = str(launcher())

        shortcut.Arguments = build_arguments()

        shortcut.WorkingDirectory = str(ROOT)

        shortcut.Description = "Jarvis — голосовой помощник"

        shortcut.WindowStyle = 1

        if ICON.is_file():

            shortcut.IconLocation = f"{ICON},0"

        shortcut.Save()

    finally:

        comtypes.CoUninitialize()





def _ps_quote(value) -> str:

    return "'" + str(value).replace("'", "''") + "'"





def _create_via_powershell(link: Path) -> None:

    icon = f"{ICON},0" if ICON.is_file() else ""

    script = (

        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(link)});"

        f"$s.TargetPath={_ps_quote(launcher())};"

        f"$s.Arguments={_ps_quote(build_arguments())};"

        f"$s.WorkingDirectory={_ps_quote(ROOT)};"

        f"$s.Description={_ps_quote('Jarvis — голосовой помощник')};"

        + (f"$s.IconLocation={_ps_quote(icon)};" if icon else "")

        + "$s.Save()"

    )

    subprocess.run(

        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],

        check=True,

    )





def create(link: Path) -> None:

    if not GUI.is_file():

        raise FileNotFoundError(f"Не найден {GUI}")

    link.parent.mkdir(parents=True, exist_ok=True)

    try:

        _create_via_comtypes(link)

    except Exception as exc:                                               

        print(f"   comtypes недоступен ({exc}), использую PowerShell…")

        _create_via_powershell(link)





def remove(link: Path) -> bool:

    try:

        link.unlink()

        return True

    except FileNotFoundError:

        return False





def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description="Ярлыки запуска Jarvis")

    parser.add_argument("--desktop", action="store_true", help="только на рабочем столе")

    parser.add_argument("--start-menu", action="store_true", help="только в меню «Пуск»")

    parser.add_argument("--remove", action="store_true", help="удалить ярлыки")

    parser.add_argument("--name", default=DEFAULT_NAME, help="имя ярлыка")

    args = parser.parse_args(argv)



    paths = shortcut_paths(args.name)

    chosen: dict[str, Path] = {}

    if args.desktop or args.start_menu:

        if args.desktop:

            chosen["desktop"] = paths["desktop"]

        if args.start_menu:

            chosen["start-menu"] = paths["start-menu"]

    else:

        chosen = paths



    if args.remove:

        removed = 0

        for label, link in chosen.items():

            if remove(link):

                print(f"Удалён ярлык ({label}): {link}")

                removed += 1

        if not removed:

            print("Ярлыки не найдены — удалять нечего.")

        return 0



    try:

        target = launcher()

    except FileNotFoundError as exc:

        print(f"Ошибка: {exc}")

        return 1



    print(f"Запуск: {target} {build_arguments()}")

    if ICON.is_file():

        print(f"Иконка: {ICON}")

    for label, link in chosen.items():

        try:

            create(link)

            print(f"Создан ярлык ({label}): {link}")

        except Exception as exc:                

            print(f"Не удалось создать ярлык ({label}): {exc}")

            return 1

    print("\nГотово. Ярлык «Jarvis» можно найти на рабочем столе / в меню Пуск.")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

