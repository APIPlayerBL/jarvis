"""Движок визуального интерфейса.







Слух, мозг, действия и озвучка живут в одном фоновом потоке (так у TTS и



микрофона один владелец). UI общается с ним через очередь событий и



потокобезопасные методы submit_text()/pause()/resume().







Что оптимизировано:



- wake word проверяется лёгкой моделью, тяжёлая грузится только при команде



  и выгружается после долгого простоя;



- ответ LLM озвучивается по фразам (стриминг), а не целиком;



- последние 2–3 реплики уходят в LLM как контекст — уточнения работают;



- «повтори» повторяет прошлое действие локально, без обращения к модели.



"""



from __future__ import annotations







import os



import queue



import re



import threading



import time



import uuid







import config



from common.log import get_logger



from hands.hands import execute_verbose, set_confirm_handler, shutdown as hands_shutdown



from memory import router, sanity



from tts.speaker import speak, stream_speaker







log = get_logger("ui.controller")







_UNSET = object()



_REPEAT_PHRASES = {



    "повтори", "повтори пожалуйста", "еще раз", "ещё раз", "повтори еще раз",



    "повтори ещё раз", "сделай еще раз", "сделай ещё раз", "еще разок", "ещё разок",



}



_NORM = re.compile(r"[^\w\s]", re.UNICODE)











def _normalized(text: str) -> str:



    text = (text or "").lower().replace("ё", "е")



    return " ".join(_NORM.sub(" ", text).split())











def _friendly_call(name: str, args: dict) -> str:



    if name == "launch_app":



        return f"запуск: {args.get('app_name', '?')}"



    if name == "close_app":



        return f"закрыть: {args.get('app_name', '?')}"



    if name == "open_website":



        return f"сайт: {args.get('url', '?')}"



    if name == "control_volume":



        level = args.get("level")



        return f"громкость: {args.get('action', '?')}" + (f" {level}%" if level is not None else "")



    if name == "media_control":



        return f"медиа: {args.get('action', '?')}"



    if name == "system_action":



        return f"питание: {args.get('action', '?')}"



    if name == "search_web":



        return f"поиск: {args.get('query', '?')}"



    if name == "search_and_open_file":



        return f"файл: {args.get('query', '?')}"



    if name == "get_datetime":



        return "дата/время"



    if name == "minimize_all":



        return "свернуть окна"



    if name == "see_screen":



        return "смотрю экран"



    if name == "browser_open":



        return f"сайт: {args.get('url', '?')}"



    if name == "browser_read":



        return "читаю страницу"



    if name == "browser_click":



        return f"нажимаю: {args.get('text', '?')}"



    if name == "browser_fill":



        return f"заполняю: {args.get('field', '?')}"



    if name == "browser_play_youtube":



        return f"видео: {args.get('query', '?')}"



    if name == "browser_back":



        return "назад"



    if name == "focus_window":



        return f"окно: {args.get('title', '?')}"



    if name == "click_text_on_screen":



        return f"экран: {args.get('text', '?')}"



    if name == "click_screen":



        return f"клик: {args.get('x')},{args.get('y')}"



    if name == "type_text":



        return "ввод текста"



    if name == "press_keys":



        return f"клавиши: {args.get('keys', '?')}"



    if name == "discord_action":



        return f"Discord: {args.get('action', '?')}"



    if name == "discord_send":



        return f"Discord: сообщение в {args.get('channel', '?')}"



    if name == "steam_action":



        return f"Steam: {args.get('action', '?')}"



    return name











def intent_summary(intent: dict | None) -> str:



    """Короткое человекочитаемое описание интента для строки статуса."""



    if not isinstance(intent, dict):



        return "—"



    kind = intent.get("type")



    if kind == "tool":



        summary = _friendly_call(str(intent.get("name", "?")), intent.get("args") or {})



    elif kind == "tools":



        calls = intent.get("calls") or []



        summary = " + ".join(



            _friendly_call(str(call.get("name", "?")), call.get("args") or {}) for call in calls



        ) or "несколько действий"



    elif kind == "chat":



        summary = "ответ"



    elif kind == "error":



        summary = "ошибка"



    else:



        summary = str(kind)



    return summary if len(summary) <= 100 else summary[:100] + "…"











class AssistantController:



    def __init__(self, events: queue.Queue, *, audio: bool = True, dry_run: bool = False):



        self.events = events



        self._audio_requested = audio



        self._dry_run = bool(dry_run)



        if self._dry_run:



            os.environ["JARVIS_DRY_RUN"] = "1"







        self._stop = threading.Event()



        self._resume = threading.Event()



        self._resume.set()



        self._text_inbox: queue.Queue[str] = queue.Queue()



        self._thread: threading.Thread | None = None







        self._listener = None



        self._model = None



        self._audio_ok = False



        self._device = config.MIC_DEVICE



        self._device_request = _UNSET



        self._waiting = False



        self._reasks = 0



        self._deadline = 0.0



        self._failures = 0







        self._pending_confirm: dict | None = None



        self._last_state: tuple[str, str] | None = None



        self._last_level_emit = 0.0



        self._sensitivity = max(0.3, min(5.0, config.MIC_SENSITIVITY))







                                                                                 



        self._history: list[dict] = []



        self._last_intent: dict | None = None



        self._last_tool_names: list[str] = []



        self._last_idle_check = 0.0



        self._level_every = 0.06



        self._mode_check = 0.0



                                                                    



        self.last_command = ""



        self.last_intent: dict | None = None







                                                                        



                                             



                                                                        



    @property



    def running(self) -> bool:



        return self._thread is not None and self._thread.is_alive()







    @property



    def paused(self) -> bool:



        return not self._resume.is_set()







    def start(self) -> None:



        if self.running:



            return



        self._stop.clear()



        self._thread = threading.Thread(target=self._run, name="jarvis-engine", daemon=True)



        self._thread.start()







    def stop(self, timeout: float = 2.0) -> None:



        self._stop.set()



        self._resume.set()



        listener = self._listener



        if listener is not None:



            try:



                listener.interrupt()



            except Exception:                



                pass



        pending = self._pending_confirm



        if pending:



            pending["holder"]["value"] = False



            pending["done"].set()



        if self._thread is not None:



            self._thread.join(timeout=timeout)







    def pause(self) -> None:



        self._resume.clear()



        self._waiting = False



        self._reasks = 0



        if self._listener is not None:



            self._listener.interrupt()



        self._emit_state("paused", "Пауза")







    def resume(self) -> None:



        self._resume.set()



        self._emit_state("listening" if self._audio_ok else "text",



                         "Слушаю…" if self._audio_ok else "Текстовый режим")







    def submit_text(self, text: str) -> None:



        text = (text or "").strip()



        if not text:



            return



        self._text_inbox.put(text)



        if self._listener is not None:



            self._listener.interrupt()







    def set_device(self, device) -> None:



        self._device_request = device



        if self._listener is not None:



            self._listener.interrupt()







    def request_audio_restart(self) -> None:



        self._audio_requested = True



        self._audio_ok = False



        self._device_request = self._device



        if self._listener is not None:



            self._listener.interrupt()



        if not self.running:



            self.start()







    def set_dry_run(self, enabled: bool) -> None:



        self._dry_run = bool(enabled)



        os.environ["JARVIS_DRY_RUN"] = "1" if enabled else "0"







    def set_sensitivity(self, value: float) -> None:



        self._sensitivity = max(0.3, min(5.0, float(value)))



        if self._listener is not None:



            self._listener.set_sensitivity(self._sensitivity)







    def resolve_confirm(self, token: str | None, value: bool) -> None:



        pending = self._pending_confirm



        if not pending:



            return



        if token is not None and pending["token"] != token:



            return



        pending["holder"]["value"] = bool(value)



        pending["done"].set()







                                                                        



                         



                                                                        



    def _emit(self, event_type: str, **payload) -> None:



        payload["type"] = event_type



        try:



            self.events.put_nowait(payload)



        except queue.Full:



            pass







    def _emit_state(self, state: str, detail: str = "") -> None:



        key = (state, detail)



        if key == self._last_state:



            return



        self._last_state = key



        self._emit("state", state=state, detail=detail)







    def _on_level(self, rms: float) -> None:



        now = time.monotonic()



        if now - self._last_level_emit < self._level_every:



            return



        self._last_level_emit = now



        if self.events.qsize() > 400:                               



            return



        self._emit("level", value=max(0.0, min(1.0, rms * 14.0)))







                                                                        



                  



                                                                        



    def _run(self) -> None:



        self._emit("controller", running=True)



        set_confirm_handler(self._confirm)



        self._register_reminder_speaker()



        if self._audio_requested:



            try:



                self._init_audio()



            except Exception as exc:                



                log.exception("Не удалось инициализировать слух")



                self._audio_ok = False



                self._emit("error", message=f"Слух недоступен: {exc}", fatal=False)



                self._emit("ready", audio=False, mic="—", model="—")



        else:



            self._emit("ready", audio=False, mic="—", model="—", disabled=True)







        try:



            self._loop()



        except Exception as exc:                



            log.exception("Движок упал")



            self._emit("error", message=f"Внутренняя ошибка: {exc}", fatal=True)



        finally:



            self._shutdown()







    def _register_reminder_speaker(self) -> None:



        """Напоминания озвучиваем через движок (с глушением микрофона)."""



        try:



            from hands import reminders







            reminders.set_speaker(self._say)



        except Exception as exc:                



            log.warning("Напоминания недоступны: %s", exc)







    def _init_audio(self) -> None:



        from ears import model_manager







        self._emit_state("loading", "Готовлю слух…")



        model_manager.configure(config.STT_DEVICE, config.STT_COMPUTE, config.LANGUAGE, config.USE_VAD)



                                                                             



                                                             



        model_manager.ensure_wake()



        self._warm_main_model()



        self._warm_brain()



        self._open_listener()



        self._audio_ok = True



        self._failures = 0



        self._emit("ready", audio=True, mic=self._listener.device_name,



                   model=model_manager.active_size() or self._main_size())



        self._emit_state("listening", "Слушаю wake word…")







    def _main_size(self) -> str:



        from ears import game_mode, model_manager







        return model_manager.main_size(game_mode.current_mode())







    def _warm_main_model(self) -> None:



        """Фоновая загрузка основной модели — чтобы первая команда не ждала."""



        def work() -> None:



            try:



                from ears import game_mode, model_manager







                if game_mode.current_mode() == "game":



                    log.info("Прогрев основной модели пропущен: game mode")



                    return



                self._model = model_manager.ensure(game_mode.current_mode(), verbose=False)



                log.info("Основная модель прогрета в фоне")



            except Exception as exc:                



                log.warning("Не удалось прогреть основную модель: %s", exc)







        threading.Thread(target=work, name="stt-warmup", daemon=True).start()







    def _warm_brain(self) -> None:



        """Прогреть LLM в фоне: первый ответ не ждёт «холодного старта»."""



        if config.LOCAL_ONLY or config.LOCAL_LLM_ENABLED:



            return                                                        







        def work() -> None:



            try:



                from brain.brain import warmup







                warmup()



            except Exception as exc:                



                log.warning("Прогрев мозга не удался: %s", exc)







        threading.Thread(target=work, name="brain-warmup", daemon=True).start()







    def _open_listener(self) -> None:



        from ears.listener import Listener







        self._close_listener()



        listener = Listener(device=self._device)



        listener.sensitivity = self._sensitivity



        listener.open()



        listener.calibrate()



        listener.on_level = self._on_level



        self._listener = listener







    def _close_listener(self) -> None:



        if self._listener is not None:



            try:



                self._listener.close()



            except Exception:                



                pass



            self._listener = None







    def _shutdown(self) -> None:



        self._close_listener()



        try:



            hands_shutdown()



        except Exception:                



            pass



        self._emit("controller", running=False)



        self._emit_state("stopped", "Остановлен")







    def _loop(self) -> None:



        while not self._stop.is_set():



            if self._device_request is not _UNSET:



                self._apply_device_change()







            text = self._pop_text()



            if text is not None:



                self._handle_command(text)



                continue







            if not self._audio_ok:



                self._emit_state("text", "Печатай команду в поле ниже")



                self._wait(0.2)



                continue







            if not self._resume.is_set():



                self._emit_state("paused", "Пауза")



                self._wait(0.2)



                continue







            self._audio_iteration()



            self._maybe_unload_models()



            self._maybe_relax_in_game()







    def _maybe_relax_in_game(self) -> None:



        """В игре Jarvis должен быть ещё незаметнее: реже рисовать уровень."""



        now = time.monotonic()



        if now - self._mode_check < 5.0:



            return



        self._mode_check = now



        try:



            from ears import game_mode







            in_game = game_mode.current_mode() == "game"



        except Exception:                



            in_game = False



        wanted = 0.30 if in_game else 0.06



        if wanted != self._level_every:



            self._level_every = wanted



            log.info("Обновление индикатора микрофона: %.2f с (режим: %s)",



                     wanted, "игра" if in_game else "обычный")







    def _maybe_unload_models(self) -> None:



        """После долгого простоя выгрузить тяжёлую модель (RAM свободна)."""



        now = time.monotonic()



        if now - self._last_idle_check < 30.0:



            return



        self._last_idle_check = now



        try:



            from ears import model_manager







            if model_manager.unload_idle():



                self._model = None



                self._emit("toast", text="Модель распознавания выгружена (простой)")



        except Exception:                



            pass







    def _wait(self, seconds: float) -> None:



        deadline = time.monotonic() + seconds



        while not self._stop.is_set() and time.monotonic() < deadline:



            if not self._text_inbox.empty() or self._device_request is not _UNSET:



                return



            if self._resume.is_set() and self._audio_ok:



                return



            time.sleep(0.03)







    def _pop_text(self) -> str | None:



        try:



            return self._text_inbox.get_nowait()



        except queue.Empty:



            return None







    def _apply_device_change(self) -> None:



        device = self._device_request



        self._device_request = _UNSET



        self._device = device







        if self._model is None and not self._audio_ok:



            try:



                self._init_audio()



            except Exception as exc:                



                self._audio_ok = False



                self._emit("error", message=f"Не удалось включить слух: {exc}", fatal=False)



            return







        try:



            from ears import model_manager







            self._open_listener()



            self._audio_ok = True



            self._failures = 0



            self._emit("ready", audio=True, mic=self._listener.device_name,



                       model=model_manager.active_size() or self._main_size())



            self._emit("toast", text=f"Микрофон: {self._listener.device_name}")



            self._emit_state("listening", "Слушаю wake word…")



        except Exception as exc:                



            self._audio_ok = False



            self._emit("error", message=f"Не удалось открыть микрофон: {exc}", fatal=False)







                                                                        



          



                                                                        



    def _transcribe_command(self, audio):



        """Распознать команду основной моделью (загрузив её при необходимости)."""



        from ears import game_mode, model_manager







        if not model_manager.main_model_loaded():



            self._emit_state("loading", "Загружаю модель распознавания…")



        self._model = model_manager.ensure(game_mode.current_mode())



        return model_manager.transcribe(audio, self._model)







    def _audio_iteration(self) -> None:



        from ears import model_manager



        from ears.listener import AudioError



        from ears.wake import split_wake







        if self._waiting and time.monotonic() >= self._deadline:



            log.info("Ожидание команды истекло")



            self._waiting = False



            self._reasks = 0



            self._emit("toast", text="Время ожидания команды истекло")







        self._emit_state("waiting" if self._waiting else "listening",



                         "Жду команду…" if self._waiting else "Слушаю wake word…")



        try:



            audio = self._listener.record_utterance()



        except AudioError as exc:



            self._failures += 1



            self._emit("error", message=f"Аудио: {exc}", fatal=False)



            if self._failures >= 3:



                self._audio_ok = False



                self._emit("audio_lost", message="Микрофон отключился")



                self._close_listener()



            else:



                time.sleep(0.4)



            return



        if audio.size == 0:



            return







        self._failures = 0



        self._emit_state("thinking", "Распознаю речь…")



        started = time.perf_counter()







        if self._waiting:



                                                                           



            text = self._transcribe_command(audio)



            elapsed = time.perf_counter() - started



            if not text:



                self._emit_state("waiting")



                return



            self._emit("transcript", text=text, elapsed=round(elapsed, 2), final=True)



            if sanity.passes(text):



                self._waiting = False



                self._reasks = 0



                self._handle_command(text)



            else:



                self._reasks += 1



                if self._reasks >= config.MAX_REASKS:



                    self._say("Не расслышал.")



                    self._waiting = False



                    self._reasks = 0



                else:



                    self._say("Повтори, пожалуйста.")



            return







                                                               



        wake_text = model_manager.transcribe_wake(audio)



        elapsed = time.perf_counter() - started



        if not wake_text:



            self._emit_state("listening")



            return



        found, rest = split_wake(wake_text)



        if not found:



            self._emit_state("listening", "Не услышал wake word")



            return



        self._emit("transcript", text=wake_text, elapsed=round(elapsed, 2), final=True)



        if rest:



                                                                           



                                                                               



                                                                            



            try:



                from ears import game_mode



                in_game = game_mode.current_mode() == "game"



            except Exception:                



                in_game = False



            if in_game and not sanity.passes(rest):



                self._emit_state("listening", "Ложное срабатывание (игра)")



                return



            command = rest



            accurate = self._transcribe_command(audio)



            if accurate:



                _found, accurate_rest = split_wake(accurate)



                command = accurate_rest or rest



                self._emit("transcript", text=accurate, final=True)



            if sanity.passes(command):



                self._handle_command(command)



            else:



                self._say("Не понял команду.")



        else:



            self._say("Слушаю.")



            self._waiting = True



            self._reasks = 0



            self._deadline = time.monotonic() + config.WAIT_TIMEOUT_SEC







                                                                        



             



                                                                        



    def _handle_command(self, text: str) -> None:



        self._emit("user", text=text)



        started = time.perf_counter()



        self._last_tool_names = []







        if _normalized(text) in {_normalized(item) for item in _REPEAT_PHRASES}:



            if self._repeat_last():



                return







        modes = bool(getattr(config, "THINKING_MODE", False) or getattr(config, "NO_SCRIPTS_MODE", False))



        intent = None if modes else router.route(text)







        if intent is None:



            self._handle_with_brain(text, started)



            return







        elapsed = time.perf_counter() - started



        self._last_intent = intent



        self.last_command = text



        self.last_intent = intent



        self._remember(text, "")                                  



        self._emit("intent", source="router", intent=intent,



                   summary=intent_summary(intent), elapsed=round(elapsed, 2))



        result, ok = execute_verbose(intent)



        names = ([intent.get("name")] if intent.get("type") == "tool"



                 else [call.get("name") for call in intent.get("calls") or []])



        self._last_tool_names = [name for name in names if name]



        if self._history:



            self._history[-1]["assistant"] = result



            self._history[-1]["tools"] = self._last_tool_names



        self._emit("assistant", text=result, ok=ok)



        self._say(result)







    def _handle_with_brain(self, text: str, started: float) -> None:



        from brain.brain import run_agent, usage_snapshot







        speaker = stream_speaker() if config.STREAM_TTS else None



        first_sound: list[float] = []



        muted = [False]







        def on_sentence(chunk: str) -> None:



            if speaker is None:



                return



            if not muted[0]:



                self._speak_begin()



                muted[0] = True



            if not first_sound:



                first_sound.append(time.perf_counter() - started)



                log.info("Первый звук через %.2f с", first_sound[0])



            speaker.feed(chunk)







        before = usage_snapshot()



        try:



            reply = run_agent(



                text,



                executor=self._execute_tool,



                on_step=self._on_tool_step,



                history=self._history,



                on_sentence=on_sentence,



                on_thought=self._on_thought,



            )



        finally:



            if speaker is not None:



                speaker.close(timeout=120.0)



        after = usage_snapshot()



        cost = round(after["cost"] - before["cost"], 4)



        tokens_in = after["tokens_in"] - before["tokens_in"]



        tokens_out = after["tokens_out"] - before["tokens_out"]



        if cost:



            log.info("Стоимость команды: %.4f ₽ (%d вход / %d выход ток., всего за сеанс %.4f ₽)",



                     cost, tokens_in, tokens_out, after["cost"])







        elapsed = time.perf_counter() - started



        if reply.get("type") == "error":



            result = "Сервис недоступен. Проверь VPN и ключи API."



            ok = False



        else:



            result = reply.get("reply") or "Готово."



            ok = True







        self._last_intent = {"type": "chat", "reply": result}



        self.last_command = text



        self.last_intent = {"type": "tool", "name": self._last_tool_names[-1],



                            "args": {}} if self._last_tool_names else self._last_intent



        self._remember(text, result)



        self._emit(



            "intent",



            source="brain",



            intent={"type": "chat", "reply": result},



            summary="агент",



            elapsed=round(elapsed, 2),



            cost=cost,



            tokens=(tokens_in, tokens_out),



        )



        self._emit("assistant", text=result, ok=ok)



        spoken = bool(speaker is not None and speaker.spoke)



        if spoken:



                                                                           



            if muted[0]:



                self._speak_end()



        else:



            self._say(result)







    def _repeat_last(self) -> bool:



        """«Повтори» — выполнить прошлое действие ещё раз (0 токенов)."""



        intent = self._last_intent



        if not intent:



            self._say("Нечего повторять.")



            return True



        self._emit("toast", text="Повторяю прошлую команду")



        result, ok = execute_verbose(intent)



        self._last_tool_names = ([intent.get("name")] if intent.get("type") == "tool"



                                 else [call.get("name") for call in intent.get("calls") or []])



        self._remember("повтори", result)



        self._emit("intent", source="router", intent=intent,



                   summary="повтор: " + intent_summary(intent), elapsed=0.0)



        self._emit("assistant", text=result, ok=ok)



        self._say(result)



        return True







    def _remember(self, text: str, answer: str) -> None:



        """Добавить реплику в короткий контекст (2–3 пары)."""



        limit = max(0, int(config.CONTEXT_TURNS))



        if limit <= 0:



            return



        self._history.append({"user": text, "assistant": answer,



                              "tools": list(self._last_tool_names)})



        del self._history[:-limit]







    def _execute_tool(self, name: str, args: dict):



        result, ok = execute_verbose({"type": "tool", "name": name, "args": args})



        self._emit("tool_result", name=name, ok=ok, text=str(result)[:600])



        return result, ok







    def _on_tool_step(self, name: str, args: dict) -> None:



        self._last_tool_names.append(name)



        self._emit("tool", name=name, args=args)



        self._emit_state("thinking", f"Инструмент: {name}")







    def _on_thought(self, text: str) -> None:



        text = (text or "").strip()



        if not text:



            return



        log.info("Мысль агента: %s", text)



        if not getattr(config, "THINKING_MODE", False):



            return



        self._emit("thought", text=text)







                                                                        



                                                 



                                                                        



    def _speak_begin(self) -> None:



        self._emit_state("speaking", "Озвучиваю ответ…")



        if self._listener is not None:



            self._listener.set_muted(True)







    def _speak_end(self) -> None:



        listener = self._listener



        if listener is not None:



            listener.flush()



            listener.set_muted(False)



        if self._audio_ok and self._resume.is_set():



            self._emit_state("waiting" if self._waiting else "listening")



        elif not self._audio_ok:



            self._emit_state("text", "Текстовый режим")



        else:



            self._emit_state("paused", "Пауза")







    def _say(self, text: str) -> None:



        if not text:



            return



        self._speak_begin()



        try:



            if config.TTS_ENABLED:



                speak(text)



                if config.SPEAK_SETTLE_SEC:



                    time.sleep(config.SPEAK_SETTLE_SEC)



        finally:



            self._speak_end()







                                                                        



                                                        



                                                                        



    def _confirm(self, prompt: str) -> bool:



        token = uuid.uuid4().hex



        done = threading.Event()



        holder: dict = {"value": False}



        self._pending_confirm = {"token": token, "done": done, "holder": holder}



        self._emit("confirm", prompt=prompt, token=token)



        deadline = time.monotonic() + 30.0



        while not done.wait(0.2):



            if self._stop.is_set() or time.monotonic() > deadline:



                break



        self._pending_confirm = None



        return bool(holder.get("value"))



