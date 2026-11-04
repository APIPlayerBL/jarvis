"""Learn-tab UI for Jarvis (mixin for JarvisApp)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui import theme as T


class LearnTabMixin:
    def _build_learn_tab(self, parent) -> None:



        from memory.lessons import ACTIONS







        self._action_labels = {data[0]: key for key, data in ACTIONS.items()}







        head = tk.Frame(parent, bg=T.BG)



        head.pack(fill="x", padx=22, pady=(14, 6))



        tk.Label(head, text="Обучение — свои команды", font=("Segoe UI Semibold", 14),



                 fg=T.TEXT, bg=T.BG).pack(side="left")



        tk.Label(head, text="выполняются мгновенно и бесплатно", font=T.FONT_SMALL,



                 fg=T.DIM, bg=T.BG).pack(side="left", padx=10)







        hint = tk.Label(



            parent,



            text=("Научи фразе — Jarvis сделает именно то, что нужно, без обращения к модели.\n"



                  "Например: «включи музыку» → Открыть сайт → https://music.yandex.ru"),



            font=T.FONT_SMALL, fg=T.MUTED, bg=T.BG, justify="left",



        )



        hint.pack(fill="x", padx=22, pady=(0, 8))







        body = tk.Frame(parent, bg=T.BG)



        body.pack(fill="both", expand=True, padx=22, pady=(0, 10))







                               



        list_wrap = tk.Frame(body, bg=T.SURFACE_2, highlightbackground=T.BORDER,



                             highlightthickness=1)



        list_wrap.pack(side="left", fill="both", expand=True)



        self.lessons_tree = ttk.Treeview(



            list_wrap, columns=("phrase", "action", "value", "state"),



            show="headings", style="Dark.Treeview", height=12,



        )



        for column, title, width in (("phrase", "Фраза", 210), ("action", "Действие", 150),



                                     ("value", "Значение", 210), ("state", "Вкл", 40)):



            self.lessons_tree.heading(column, text=title)



            self.lessons_tree.column(column, width=width, anchor="w",



                                     stretch=(column == "value"))



        tree_scroll = ttk.Scrollbar(list_wrap, orient="vertical",



                                    command=self.lessons_tree.yview,



                                    style="Dark.Vertical.TScrollbar")



        self.lessons_tree.configure(yscrollcommand=tree_scroll.set)



        tree_scroll.pack(side="right", fill="y")



        self.lessons_tree.pack(side="left", fill="both", expand=True)



        self.lessons_tree.bind("<<TreeviewSelect>>", self._lesson_selected)







                       



        form = tk.Frame(body, bg=T.SURFACE, width=340)



        form.pack(side="left", fill="y", padx=(12, 0))



        form.pack_propagate(False)



        inner = tk.Frame(form, bg=T.SURFACE)



        inner.pack(fill="both", expand=True, padx=14, pady=12)







        def label(text: str) -> None:



            tk.Label(inner, text=text, font=("Segoe UI Semibold", 8), fg=T.DIM,



                     bg=T.SURFACE).pack(anchor="w", pady=(8, 3))







        label("КОМАНДА (что скажешь)")



        self.lesson_phrase_var = tk.StringVar()



        tk.Entry(inner, textvariable=self.lesson_phrase_var, bg=T.SURFACE_2, fg=T.TEXT,



                 insertbackground=T.ACCENT, relief="flat", bd=0,



                 font=("Segoe UI", 10)).pack(fill="x", ipady=6, padx=1)







        label("ЧТО СДЕЛАТЬ")



        self.lesson_action_var = tk.StringVar(value=list(self._action_labels)[0])



        action_combo = ttk.Combobox(inner, state="readonly", style="Dark.TCombobox",



                                    textvariable=self.lesson_action_var,



                                    values=list(self._action_labels), font=T.FONT_SMALL)



        action_combo.pack(fill="x")



        action_combo.bind("<<ComboboxSelected>>", lambda _e: self._lesson_action_changed())







        label("ЗНАЧЕНИЕ")



        self.lesson_value_var = tk.StringVar()



        tk.Entry(inner, textvariable=self.lesson_value_var, bg=T.SURFACE_2, fg=T.TEXT,



                 insertbackground=T.ACCENT, relief="flat", bd=0,



                 font=("Segoe UI", 10)).pack(fill="x", ipady=6, padx=1)



        self.lesson_hint = tk.Label(inner, text="", font=T.FONT_SMALL, fg=T.DIM,



                                    bg=T.SURFACE, wraplength=300, justify="left")



        self.lesson_hint.pack(anchor="w", pady=(3, 0))







        buttons = tk.Frame(inner, bg=T.SURFACE)



        buttons.pack(fill="x", pady=(10, 0))



        self._button(buttons, "Сохранить", self._lesson_save, primary=True,



                     small=True).pack(side="left")



        self._button(buttons, "Удалить", self._lesson_delete, small=True).pack(side="left", padx=6)



        self._button(buttons, "Вкл/выкл", self._lesson_toggle, small=True).pack(side="left")







        self._button(inner, "Из последней команды", self._lesson_from_last,



                     small=True).pack(fill="x", pady=(8, 0))







        label("ПРОВЕРИТЬ ФРАЗУ")



        check = tk.Frame(inner, bg=T.SURFACE)



        check.pack(fill="x")



        self.check_phrase_var = tk.StringVar()



        tk.Entry(check, textvariable=self.check_phrase_var, bg=T.SURFACE_2, fg=T.TEXT,



                 insertbackground=T.ACCENT, relief="flat", bd=0,



                 font=T.FONT_SMALL).pack(side="left", fill="x", expand=True, ipady=5, padx=1)



        self._button(check, "→", self._lesson_check, small=True).pack(side="left", padx=(6, 0))



        self.check_result = tk.Label(inner, text="Скажи фразу — покажу, что сработает",



                                     font=T.FONT_SMALL, fg=T.DIM, bg=T.SURFACE,



                                     wraplength=300, justify="left")



        self.check_result.pack(anchor="w", pady=(4, 0))







        self._lesson_action_changed()



        self._refresh_lessons()







    def _lesson_action_changed(self) -> None:



        from memory.lessons import ACTIONS







        key = self._action_labels.get(self.lesson_action_var.get(), "open_url")



        _label, _field, hint = ACTIONS[key]



        self.lesson_hint.configure(text=f"например: {hint}")







    def _refresh_lessons(self) -> None:



        from memory.lessons import describe, list_lessons







        for item in self.lessons_tree.get_children():



            self.lessons_tree.delete(item)



        for lesson in list_lessons():



            self.lessons_tree.insert("", "end", iid=lesson["id"], values=(



                lesson["phrase"],



                describe(lesson).split(":")[0],



                lesson["value"][:60],



                "да" if lesson["enabled"] else "нет",



            ))







    def _lesson_selected(self, _event=None) -> None:



        from memory.lessons import ACTIONS, list_lessons







        selection = self.lessons_tree.selection()



        if not selection:



            return



        lesson_id = selection[0]



        lesson = next((item for item in list_lessons() if item["id"] == lesson_id), None)



        if not lesson:



            return



        self.lesson_phrase_var.set(lesson["phrase"])



        label = ACTIONS.get(lesson["action"], ("", "", ""))[0]



        if label in self._action_labels:



            self.lesson_action_var.set(label)



        self.lesson_value_var.set(lesson["value"])



        self._lesson_action_changed()







    def _lesson_save(self) -> None:



        from memory.lessons import ACTIONS, add







        phrase = self.lesson_phrase_var.get().strip()



        key = self._action_labels.get(self.lesson_action_var.get())



        value = self.lesson_value_var.get().strip()



        if not phrase or key not in ACTIONS:



            self._toast("Впиши фразу, которой будешь управлять")



            return



        try:



            record = add(phrase, key, value)



        except ValueError as exc:



            self._toast(f"Не сохранено: {exc}")



            return



        self._refresh_lessons()



        self.lessons_tree.selection_set(record["id"])



        self._toast(f"Готово: «{record['phrase']}» → {record['value']}")







    def _lesson_delete(self) -> None:



        from memory.lessons import remove







        selection = self.lessons_tree.selection()



        if not selection:



            self._toast("Выбери команду в списке")



            return



        if remove(selection[0]):



            self._refresh_lessons()



            self.lesson_phrase_var.set("")



            self.lesson_value_var.set("")



            self._toast("Удалено")







    def _lesson_toggle(self) -> None:



        from memory.lessons import toggle







        selection = self.lessons_tree.selection()



        if not selection:



            self._toast("Выбери команду в списке")



            return



        record = toggle(selection[0])



        self._refresh_lessons()



        if record:



            self._toast(f"«{record['phrase']}»: {'включена' if record['enabled'] else 'выключена'}")







    def _lesson_check(self) -> None:



        from memory import router



        from ui.controller import intent_summary







        phrase = self.check_phrase_var.get().strip()



        if not phrase:



            return



        try:



            intent = router.route(phrase)



        except Exception as exc:                



            self.check_result.configure(text=f"Ошибка: {exc}", fg=T.RED)



            return



        if not intent:



            self.check_result.configure(



                text="Локально ничего не нашлось — фраза уйдёт на сервер (платно).",



                fg=T.AMBER)



            return



        source = str(intent.get("_via") or "router")



        if source.startswith("lesson"):



            source = "твоя команда"



        self.check_result.configure(text=f"{intent_summary(intent)} · {source}", fg=T.GREEN)







    def _lesson_from_last(self) -> None:



        from memory.lessons import ACTIONS







        command = getattr(self.controller, "last_command", "") or ""



        intent = getattr(self.controller, "last_intent", None) or {}



        if command:



            self.lesson_phrase_var.set(command)



        name, args = intent.get("name"), intent.get("args") or {}



        mapping = {



            "open_website": ("open_url", args.get("url")),



            "launch_app": ("launch_app", args.get("app_name")),



            "search_web": ("search_web", args.get("query")),



            "browser_play_youtube": ("youtube", args.get("query")),



            "media_control": ("media", args.get("action", "play")),



            "control_volume": ("volume", str(args.get("level") or 40)),



            "save_note": ("note", args.get("text")),



        }



        if name in mapping:



            key, value = mapping[name]



            label = ACTIONS[key][0]



            if label in self._action_labels:



                self.lesson_action_var.set(label)



            self.lesson_value_var.set("" if value is None else str(value))



            self._lesson_action_changed()



            self._toast("Подставил последнюю команду")



        elif command:



            self._toast("Фраза подставлена, выбери действие")



        else:



            self._toast("Сначала выполни любую команду")
