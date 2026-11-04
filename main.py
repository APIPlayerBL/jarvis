                     



"""Jarvis — голосовой ассистент управления ПК.







Запуск:



    python main.py                 # голосовой режим



    python main.py --text          # текстовый режим (без микрофона)



    python main.py --dry-run       # ничего не выполнять, только показать интент



    python main.py --health        # проверить LLM-провайдеров



    python main.py --list-devices  # список микрофонов



"""



from __future__ import annotations







import argparse



import json



import os



import sys



import time



from pathlib import Path







ROOT = Path(__file__).resolve().parent



if str(ROOT) not in sys.path:



    sys.path.insert(0, str(ROOT))







import config              



from common.log import get_logger, setup_logging              



from hands.hands import execute_verbose, set_confirm_handler, shutdown as hands_shutdown              



from memory import router, sanity              



from tts.speaker import shutdown as tts_shutdown              



from tts.speaker import speak              







log = get_logger("main")











                                                                            



def _compact(intent: dict) -> str:



    try:



        text = json.dumps(intent, ensure_ascii=False)



    except (TypeError, ValueError):



        text = str(intent)



    return text if len(text) <= 400 else text[:400] + "…"











def handle_command(text: str, confirm=None, say=print, history: list | None = None,



                   on_sentence=None) -> tuple[str, bool]:



    """Роутер -> (агент | локальный интент) -> execute. Возвращает (ответ, успех)."""



    started = time.perf_counter()



    modes = bool(getattr(config, "THINKING_MODE", False) or getattr(config, "NO_SCRIPTS_MODE", False))



    intent = None if modes else router.route(text)



    source = "router"



    if modes:



        source = "brain-modes"







    if intent is None:



        source = "brain"



        from brain.brain import run_agent







        def execute_tool(name: str, args: dict):



            return execute_verbose({"type": "tool", "name": name, "args": args}, confirm=confirm)







        from brain import brain as brain_module







        usage_before = brain_module.usage_snapshot()



        reply = run_agent(text, executor=execute_tool, history=history or [],



                          on_sentence=on_sentence,



                          on_thought=(lambda thought: print(f"   мысль: {thought}")) if getattr(config, "THINKING_MODE", False) else None)



        usage_after = brain_module.usage_snapshot()



        spent = round(usage_after["cost"] - usage_before["cost"], 4)



        if spent:



            log.info("Стоимость команды: %.4f ₽ (%d+%d ток., сеанс %.2f ₽)", spent,



                     usage_after["tokens_in"] - usage_before["tokens_in"],



                     usage_after["tokens_out"] - usage_before["tokens_out"],



                     usage_after["cost"])



        elapsed = time.perf_counter() - started



        if reply.get("type") == "error":



            result = "Сервис недоступен. Проверь VPN и ключи API."



            ok = False



        else:



            result = reply.get("reply") or "Готово."



            ok = True



        log.info("Агент [%.2fs, %s]: %s", elapsed, reply.get("_via"), result)



        if history is not None:



            history.append({"user": text, "assistant": result})



        if not reply.get("_spoken"):



            say(result)                                        



        return result, ok







    log.info("Интент [%s, %.2fs]: %s", source, time.perf_counter() - started, _compact(intent))



    result, ok = execute_verbose(intent, confirm=confirm)



    log.info("Результат [%s]: %s", "ok" if ok else "fail", result)



    if history is not None:



        history.append({"user": text,



                        "assistant": result,



                        "tools": [intent.get("name")] if intent.get("type") == "tool" else []})



    say(result)



    return result, ok











                                                                            



def _voice_confirm_factory(listener, model):



    def confirm(prompt: str) -> bool:



        listener.set_muted(True)



        try:



            speak(prompt)



            time.sleep(config.SPEAK_SETTLE_SEC)



        finally:



            listener.flush()



            listener.set_muted(False)



        try:



            audio = listener.record_utterance(max_seconds=6.0, silence_sec=0.6)



        except Exception as exc:                



            log.warning("Не удалось записать подтверждение: %s", exc)



            return False



        from ears import model_manager







        text = model_manager.transcribe(audio, model)



        log.info("Подтверждение: %r", text)



        tokens = set(text.lower().replace("ё", "е").split())



        yes = bool(tokens & {"да", "подтверждаю", "конечно", "верно", "ага", "угу", "yes"})



        no = any(word in text.lower() for word in ("нет", "отмен", "не надо", "no"))



        return yes and not no







    return confirm











                                                                            



def run_text() -> int:



    from ears.wake import split_wake







    print("Текстовый режим. Команда без wake word, 'quit' — выход.")



    history: list[dict] = []



    while True:



        try:



            line = input(">>> ").strip()



        except (EOFError, KeyboardInterrupt):



            print()



            break



        if not line:



            continue



        if line.lower() in {"quit", "exit", "выход", "q"}:



            break



        found, rest = split_wake(line)



        if found and rest:



            line = rest



        handle_command(line, confirm=None, say=lambda text: print(f"   {text}"),



                       history=history)



    return 0











                                                                            



def run_audio(args) -> int:



    from ears import game_mode, model_manager



    from ears.listener import AudioError, Listener



    from ears.wake import split_wake



    from tts.speaker import stream_speaker







    model_manager.configure(config.STT_DEVICE, config.STT_COMPUTE, config.LANGUAGE, config.USE_VAD)



    mode = game_mode.current_mode()



                                                                          



    model_manager.ensure_wake()



    model = model_manager.ensure(mode)







    device = args.mic



    if isinstance(device, str) and device.isdigit():



        device = int(device)



    listener = Listener(device=device)



    listener.open()



    if not args.no_calibrate:



        listener.calibrate()







    def say(text: str) -> None:



        if not text:



            return



        listener.set_muted(True)



        try:



            speak(text)



            time.sleep(config.SPEAK_SETTLE_SEC)



        finally:



            listener.flush()



            listener.set_muted(False)







    def command(text: str, history: list) -> None:



        """Команда с контекстом и стриминговой озвучкой (если включена)."""



        speaker = stream_speaker() if config.STREAM_TTS else None



        muted = [False]







        def on_sentence(chunk: str) -> None:



            if speaker is None:



                return



            if not muted[0]:



                listener.set_muted(True)



                muted[0] = True



            speaker.feed(chunk)







        try:



            handle_command(text, say=say, history=history, on_sentence=on_sentence)



        finally:



            if speaker is not None:



                speaker.close(timeout=120.0)



                if muted[0]:



                    listener.flush()



                    listener.set_muted(False)







    set_confirm_handler(_voice_confirm_factory(listener, model))



    try:                                         



        from hands import reminders







        reminders.set_speaker(say)



    except Exception:                



        pass



    say("Джарвис на связи.")







    history: list[dict] = []



    waiting = False



    reasks = 0



    deadline = 0.0



    failures = 0







    try:



        while True:



            if waiting and time.monotonic() >= deadline:



                log.info("Время ожидания команды истекло — снова жду wake word")



                waiting = False



                reasks = 0



                continue







            log.info("⏳ Жду команду..." if waiting else "👂 Слушаю wake word...")



            try:



                audio = listener.record_utterance()



            except AudioError as exc:



                failures += 1



                log.error("Аудио: %s (попытка %d/3)", exc, failures)



                if failures >= 3:



                    break



                time.sleep(0.5)



                try:



                    listener.close()



                    listener.open()



                    listener.flush()



                except AudioError:



                    time.sleep(1.0)



                continue



            failures = 0



            if audio.size == 0:



                continue







            started = time.perf_counter()



            if waiting:



                text = model_manager.transcribe(audio, model)



            else:



                text = model_manager.transcribe_wake(audio)                 



            if not text:



                continue



            log.info("STT (%.2f с): %s", time.perf_counter() - started, text)







            if waiting:



                if sanity.passes(text):



                    waiting = False



                    reasks = 0



                    command(text, history)



                else:



                    reasks += 1



                    if reasks >= config.MAX_REASKS:



                        say("Не расслышал.")



                        waiting = False



                        reasks = 0



                    else:



                        say("Повтори, пожалуйста.")



                continue







            found, rest = split_wake(text)



            if not found:



                model_manager.unload_idle()



                continue



            if rest:



                accurate = model_manager.transcribe(audio, model)



                _found, accurate_rest = split_wake(accurate)



                rest = accurate_rest or rest



                if rest and sanity.passes(rest):



                    command(rest, history)



                else:



                    say("Не понял команду.")



            else:



                say("Слушаю.")



                waiting = True



                reasks = 0



                deadline = time.monotonic() + config.WAIT_TIMEOUT_SEC



    except KeyboardInterrupt:



        log.info("Выход по Ctrl+C")



        try:



            speak("До свидания.")



        except Exception:                



            pass



    finally:



        listener.close()



        hands_shutdown()



        tts_shutdown()



    return 0











                                                                            



def build_parser() -> argparse.ArgumentParser:



    parser = argparse.ArgumentParser(description="Jarvis — голосовой ассистент")



    parser.add_argument("--gui", action="store_true", help="открыть визуальный интерфейс")



    parser.add_argument("--text", action="store_true", help="текстовый режим без микрофона")



    parser.add_argument("--dry-run", action="store_true", help="не выполнять действия, только показать")



    parser.add_argument("--health", action="store_true", help="проверить LLM-провайдеров и выйти")



    parser.add_argument("--list-devices", action="store_true", help="показать микрофоны")



    parser.add_argument("--mic", default=None, help="индекс или часть имени микрофона")



    parser.add_argument("--no-calibrate", action="store_true", help="не калибровать порог тишины")



    parser.add_argument("--no-tts", action="store_true", help="без озвучки")



    return parser











def main(argv: list[str] | None = None) -> int:



    args = build_parser().parse_args(argv)



    setup_logging()



    from common.first_run import ensure_api_keys







    ensure_api_keys()



    if config.LOW_PRIORITY:



        from common.priority import apply_low_priority







        apply_low_priority()



    if args.dry_run:



        os.environ["JARVIS_DRY_RUN"] = "1"



    if args.no_tts:



        config.TTS_ENABLED = False







    if args.list_devices:



        from ears.listener import Listener







        Listener.print_devices()



        try:



            import sounddevice as sd







            from common.audio import output_info







            info = output_info()



            print("\nУстройства вывода (JARVIS_AUDIO_OUTPUT):")



            for index, item in enumerate(sd.query_devices()):



                if item.get("max_output_channels", 0) > 0:



                    mark = "*" if index == info.get("device_index") else " "



                    print(f" {mark}[{index:>2}] {str(item['name'])[:50]:<52} "



                          f"{int(item['default_samplerate'])} Гц")



            print(f"\nСейчас играем на: [{info.get('device_index')}] {info.get('device_name')}")



        except Exception as exc:                



            print(f"Устройства вывода недоступны: {exc}")



        return 0







    if args.health:



        from brain.brain import health_check







        result = health_check()



        print(json.dumps(result, ensure_ascii=False, indent=2))



        return 0 if result.get("ok") else 1







    if args.gui:



        from ui.app import run_gui







        return run_gui(audio=True, dry_run=args.dry_run, no_tts=args.no_tts)







    if args.text:



        try:



            return run_text()



        finally:



            hands_shutdown()



    return run_audio(args)











if __name__ == "__main__":



    raise SystemExit(main())



