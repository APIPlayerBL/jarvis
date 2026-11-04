                     



"""Установка локального голоса Piper для Jarvis (Windows, без интернета в работе).







Piper даёт качественный офлайн-голос и отвечает за ~100 мс — это лучший вариант



для голосового ассистента: приватно, без задержек сети и без лимитов.







    python setup_tts.py --pull            # скачать Piper + русский голос (~85 МБ)



    python setup_tts.py --voice irina     # женский голос вместо мужского



    python setup_tts.py --check           # проверить, что уже установлено



    python setup_tts.py --remove          # удалить Piper и вернуться к edge/SAPI







После --pull в .env прописываются JARVIS_PIPER_EXE и JARVIS_PIPER_MODEL,



а Jarvis сам выбирает движок (JARVIS_TTS_ENGINE=auto): Piper → edge-tts → SAPI.



"""



from __future__ import annotations







import argparse



import shutil



import sys



import urllib.request



import zipfile



from pathlib import Path







ROOT = Path(__file__).resolve().parent



if str(ROOT) not in sys.path:



    sys.path.insert(0, str(ROOT))







import config              







PIPER_DIR = config.ASSETS_DIR / "piper"



BIN_DIR = PIPER_DIR / "bin"



VOICE_DIR = PIPER_DIR / "voices"







PIPER_URL = ("https://github.com/rhasspy/piper/releases/download/"



             "2023.11.14-2/piper_windows_amd64.zip")



VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"



VOICES = {



    "dmitri": "ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium",



    "irina": "ru/ru_RU/irina/medium/ru_RU-irina-medium",



}











def _download(url: str, target: Path) -> None:



    target.parent.mkdir(parents=True, exist_ok=True)



    tmp = target.with_suffix(target.suffix + ".part")



    print(f"  скачиваю {url}\n     -> {target}")



    with urllib.request.urlopen(url, timeout=120) as response, open(tmp, "wb") as handle:



        total = int(response.headers.get("Content-Length") or 0)



        done = 0



        while True:



            block = response.read(1 << 20)



            if not block:



                break



            handle.write(block)



            done += len(block)



            if total:



                print(f"\r     {done * 100 // total:3d}% ({done // 1048576} МБ)", end="")



    print()



    tmp.replace(target)











def install(voice: str = "dmitri") -> Path:



    if voice not in VOICES:



        raise SystemExit(f"Голос «{voice}» неизвестен. Доступны: {', '.join(VOICES)}")







    exe = BIN_DIR / "piper.exe"



    if not exe.is_file():



        zip_path = PIPER_DIR / "piper_windows_amd64.zip"



        _download(PIPER_URL, zip_path)



        print("  распаковываю...")



        tmp_dir = PIPER_DIR / "_unpack"



        if tmp_dir.exists():



            shutil.rmtree(tmp_dir)



        with zipfile.ZipFile(zip_path) as archive:



            archive.extractall(tmp_dir)



                                                              



        source = tmp_dir / "piper" if (tmp_dir / "piper").is_dir() else tmp_dir



        if BIN_DIR.exists():



            shutil.rmtree(BIN_DIR)



        shutil.move(str(source), str(BIN_DIR))



        shutil.rmtree(tmp_dir, ignore_errors=True)



        zip_path.unlink(missing_ok=True)







    model_stem = VOICES[voice]



    model = VOICE_DIR / f"{model_stem.rsplit('/', 1)[-1]}.onnx"



    if not model.is_file():



        _download(VOICE_BASE + model_stem + ".onnx", model)



        _download(VOICE_BASE + model_stem + ".onnx.json", model.with_suffix(".onnx.json"))







    config.save_env_value("JARVIS_PIPER_EXE", str(exe))



    config.save_env_value("JARVIS_PIPER_MODEL", str(model))



    config.save_env_value("JARVIS_TTS_ENGINE", "auto")



                                                                                   



    config.PIPER_EXE = str(exe)



    config.PIPER_MODEL = str(model)



    print(f"\nГотово. Piper: {exe}\nГолос: {model}")



    print("Проверка: python -m tts.test_voice")



    return exe











def check() -> int:



    from tts import speaker







    print(f"Piper готов: {speaker.piper_ready()}")



    print(f"edge-tts готов: {speaker.edge_ready()}")



    print(f"Движок озвучки: {speaker.current_engine()}")



    if not speaker.piper_ready():



        print("Подсказка: python setup_tts.py --pull")



    return 0











def remove() -> int:



    if PIPER_DIR.exists():



        shutil.rmtree(PIPER_DIR)



    config.save_env_value("JARVIS_PIPER_EXE", "")



    config.save_env_value("JARVIS_PIPER_MODEL", "")



    print("Piper удалён: озвучка вернётся к edge-tts/SAPI.")



    return 0











def main(argv: list[str] | None = None) -> int:



    parser = argparse.ArgumentParser(description="Установка Piper для Jarvis")



    parser.add_argument("--pull", action="store_true", help="скачать Piper и русский голос")



    parser.add_argument("--voice", default="dmitri", choices=sorted(VOICES),



                        help="голос: dmitri (мужской) или irina (женский)")



    parser.add_argument("--check", action="store_true", help="проверить установку")



    parser.add_argument("--remove", action="store_true", help="удалить Piper")



    args = parser.parse_args(argv)







    if args.remove:



        return remove()



    if args.check:



        return check()



    if args.pull:



        install(args.voice)



        return check()



    parser.print_help()



    return 0











if __name__ == "__main__":



    raise SystemExit(main())



