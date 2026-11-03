"""Jarvis GUI: тёмный HUD, диалог, статус слуха, быстрые действия."""

from __future__ import annotations



import math

import ctypes

import queue

import threading

import time

import tkinter as tk

from datetime import datetime

from tkinter import messagebox, ttk



import config

from brain import brain

from common.log import get_logger

from ui import theme as T

from ui.controller import AssistantController



log = get_logger("ui")



QUICK_ACTIONS = [

    ("Музыка", "включи музыку"),

    ("Стоп музыку", "пауза"),

    ("Громче", "громче"),

    ("Тише", "тише"),

    ("Свернуть окна", "сверни все окна"),

    ("Время", "который час"),

    ("YouTube", "открой ютуб"),

    ("Discord", "открой дискорд"),

]



DEFAULT_DETAILS = {

    "idle": "Готов к работе",

    "loading": "Подготавливаю слух…",

    "listening": "Скажи «Джарвис» или напиши команду",

    "waiting": "Жду команду…",

    "thinking": "Обрабатываю запрос…",

    "speaking": "Озвучиваю ответ…",

    "paused": "Слух приостановлен",

    "text": "Можно печатать команды в поле ниже",

    "stopped": "Движок остановлен",

    "error": "Что-то пошло не так",

}





class JarvisApp(tk.Tk):

    def __init__(self, *, audio: bool = True, dry_run: bool = False,

                 no_tts: bool = False, autostart: bool = True):

        super().__init__()

        self.title("JARVIS — голосовой помощник")

        self.configure(bg=T.BG)

        self.minsize(1000, 640)

        self._center(1240, 800)



        if no_tts:

            config.TTS_ENABLED = False



        self.events: queue.Queue = queue.Queue(maxsize=1000)

        self.controller = AssistantController(self.events, audio=audio, dry_run=dry_run)



        self._state = "idle"

        self._detail = ""

        self._phase = 0.0

        self._level = 0.0

        self._target_level = 0.0

        self._running = False

        self._audio_ok = audio

        self._dry_run = dry_run

        self._history: list[str] = []

        self._history_pos = 0

        self._mic_map: dict[str, int | None] = {}

        self._model_name = "—"

        self._mic_name = "—"

        self._compact = False

        self._saved_geometry: str | None = None

        self._thinking_mode = bool(getattr(config, "THINKING_MODE", False))

        self._no_scripts_mode = bool(getattr(config, "NO_SCRIPTS_MODE", False))

        self._confirm_mode = bool(getattr(config, "CONFIRM_MODE", True))



        self._build_styles()

        self._build()

        self._apply_window_style()

        self._append_chat(

            "system",

            "Готов. Скажи «Джарвис» или напиши команду — я покажу, что услышал и что сделал.\n"

            "Примеры: «открой ютуб», «громкость 30», «который час», «закрой доту».",

        )

        self.chat.configure(state="disabled")



        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.bind("<Escape>", lambda _event: self._toggle_listen())

        self.bind("<Control-l>", lambda _event: self._clear_chat())

        self.bind("<Control-m>", lambda _event: self.toggle_compact())

        self.after(60, self._poll_events)

        self.after(50, self._animate)

        if autostart:

            self.after(250, self._autostart)

        else:

            self.after(120, self._load_devices)



                                                                        

                           

                                                                        

    def _center(self, width: int, height: int) -> None:

        self.update_idletasks()

        screen_w = self.winfo_screenwidth()

        screen_h = self.winfo_screenheight()

        width = max(900, min(width, screen_w - 80))

        height = max(620, min(height, screen_h - 100))

        x = max(0, (screen_w - width) // 2)

        y = max(0, (screen_h - height) // 2 - 20)

        self.geometry(f"{width}x{height}+{x}+{y}")



    def _build_styles(self) -> None:

        style = ttk.Style(self)

        try:

            style.theme_use("clam")

        except tk.TclError:

            pass

        style.configure(

            "Dark.TCombobox",

            fieldbackground=T.SURFACE_2,

            background=T.SURFACE_2,

            foreground=T.TEXT,

            arrowcolor=T.MUTED,

            bordercolor=T.BORDER,

            lightcolor=T.SURFACE_2,

            darkcolor=T.SURFACE_2,

            padding=5,

        )

        style.map(

            "Dark.TCombobox",

            fieldbackground=[("readonly", T.SURFACE_2)],

            foreground=[("readonly", T.TEXT)],

            selectbackground=[("readonly", T.SURFACE_2)],

            selectforeground=[("readonly", T.TEXT)],

            bordercolor=[("focus", T.ACCENT)],

        )

        style.configure(

            "Dark.Vertical.TScrollbar",

            background=T.SURFACE_3,

            troughcolor=T.SURFACE_2,

            bordercolor=T.SURFACE_2,

            arrowcolor=T.MUTED,

            width=10,

        )

        style.map("Dark.Vertical.TScrollbar", background=[("active", T.ACCENT)])



        style.configure("Dark.TNotebook", background=T.BG, borderwidth=0, tabmargins=(0, 0, 0, 0))

        style.configure(

            "Dark.TNotebook.Tab",

            background=T.SURFACE,

            foreground=T.MUTED,

            padding=(16, 7),

            font=T.FONT_BOLD,

            borderwidth=0,

        )

        style.map(

            "Dark.TNotebook.Tab",

            background=[("selected", T.SURFACE_2)],

            foreground=[("selected", T.ACCENT)],

        )

        style.configure(

            "Dark.Treeview",

            background=T.SURFACE_2,

            fieldbackground=T.SURFACE_2,

            foreground=T.TEXT,

            rowheight=26,

            borderwidth=0,

            font=T.FONT_SMALL,

        )

        style.map("Dark.Treeview",

                  background=[("selected", T.SURFACE_3)],

                  foreground=[("selected", T.ACCENT)])

        style.configure(

            "Dark.Treeview.Heading",

            background=T.SURFACE_3,

            foreground=T.MUTED,

            font=("Segoe UI Semibold", 8),

            borderwidth=0,

            padding=(6, 4),

        )

        style.map("Dark.Treeview.Heading", background=[("active", T.SURFACE_3)])



    def _apply_window_style(self) -> None:

        """Тёмный заголовок окна и аккуратная иконка (Windows)."""

        try:

            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())

            enabled = ctypes.c_int(1)

            for attribute in (20, 19):                                                

                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(

                    hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)

                )

                if result == 0:

                    break

        except Exception:                                         

            pass



        try:

            size = 32

            icon = tk.PhotoImage(width=size, height=size)

            icon.put(T.BG, to=(0, 0, size, size))

            center = (size - 1) / 2

            for y in range(size):

                for x in range(size):

                    distance = math.hypot(x - center, y - center)

                    if 10 <= distance <= 13:

                        icon.put(T.ACCENT, (x, y))

            icon.put(T.ACCENT, to=(14, 9, 19, 20))

            icon.put(T.ACCENT, to=(9, 19, 19, 23))

            icon.put(T.ACCENT, to=(9, 15, 13, 23))

            self.iconphoto(True, icon)

            self._icon_ref = icon

        except Exception:                

            pass



    def _button(self, parent, text, command, *, primary=False, small=False):

        bg = T.ACCENT if primary else T.SURFACE_3

        fg = T.BG if primary else T.TEXT

        hover = T.blend(T.ACCENT, "#ffffff", 0.22) if primary else T.blend(T.SURFACE_3, T.ACCENT, 0.18)

        button = tk.Button(

            parent,

            text=text,

            command=command,

            bg=bg,

            fg=fg,

            activebackground=hover,

            activeforeground=fg,

            relief="flat",

            bd=0,

            highlightthickness=0,

            cursor="hand2",

            font=T.FONT_SMALL if small else T.FONT_BOLD,

            padx=10 if small else 14,

            pady=5 if small else 9,

        )

        button.bind("<Enter>", lambda _event, b=button, h=hover: b.configure(bg=h))

        button.bind("<Leave>", lambda _event, b=button, c=bg: b.configure(bg=c))

        return button



    def _check(self, parent, text, variable, command):

        return tk.Checkbutton(

            parent,

            text=text,

            variable=variable,

            command=command,

            bg=T.SURFACE,

            fg=T.TEXT,

            selectcolor=T.SURFACE_2,

            activebackground=T.SURFACE,

            activeforeground=T.TEXT,

            highlightthickness=0,

            bd=0,

            font=T.FONT_SMALL,

            cursor="hand2",

            anchor="w",

        )



    def _build(self) -> None:

        self._top_frame = tk.Frame(self, bg=T.SURFACE, height=68)

        top = self._top_frame

        top.pack_propagate(False)

        left = tk.Frame(top, bg=T.SURFACE)

        left.pack(side="left", padx=22, pady=11)

        tk.Label(left, text="JARVIS", font=("Segoe UI Semibold", 20), fg=T.ACCENT,

                 bg=T.SURFACE).pack(anchor="w")

        tk.Label(left, text="голосовой помощник", font=T.FONT_SMALL, fg=T.MUTED,

                 bg=T.SURFACE).pack(anchor="w")



        right = tk.Frame(top, bg=T.SURFACE)

        right.pack(side="right", padx=20)

        self._button(right, "Мини", self.toggle_compact, small=True).pack(

            side="left", padx=(0, 8))

        self._button(right, "Посмотреть экран", lambda: self._quick("что на экране"),

                     small=True).pack(side="left", padx=(0, 12))

        self.think_button = self._button(right, self._mode_caption("Размышление", self._thinking_mode),

                                        self._toggle_thinking, small=True)

        self.think_button.pack(side="left", padx=(0, 8))

        self.no_scripts_button = self._button(right, self._mode_caption("Без скриптов", self._no_scripts_mode),

                                             self._toggle_no_scripts, small=True)

        self.no_scripts_button.pack(side="left", padx=(0, 8))

        self.confirm_button = self._button(right, self._mode_caption("Подтверждение", self._confirm_mode),

                                          self._toggle_confirm, small=True)

        self.confirm_button.pack(side="left", padx=(0, 8))

        self.provider_label = tk.Label(right, text="Сервис: не проверено", font=T.FONT_SMALL,

                                       fg=T.MUTED, bg=T.SURFACE)

        self.provider_label.pack(side="left", padx=(0, 12))



        self.model_combo = ttk.Combobox(

            right, values=brain.model_labels(), state="readonly",

            style="Dark.TCombobox", width=32)

        self.model_combo.set(brain.active_model_label())

        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)

        self.model_combo.pack(side="left", padx=(0, 6))



        self.model_check_button = self._button(

            right, "Проверить модели", self._check_models, small=True)

        self.model_check_button.pack(side="left", padx=(0, 6))



        self.model_status = tk.Label(right, text="", font=T.FONT_SMALL,

                                     fg=T.MUTED, bg=T.SURFACE)

        self.model_status.pack(side="left", padx=(0, 12))



        self.health_button = self._button(right, "Проверить связь", self._check_health, small=True)

        self.health_button.pack(side="left")



        self._sep = tk.Frame(self, bg=T.BORDER, height=1)



        self._footer_frame = tk.Frame(self, bg=T.SURFACE, height=30)

        footer = self._footer_frame

        footer.pack_propagate(False)

        self.footer_info = tk.Label(footer, text="Whisper: —   ·   Микрофон: —   ·   Голос: —",

                                    font=T.FONT_SMALL, fg=T.DIM, bg=T.SURFACE, anchor="w")

        self.footer_info.pack(side="left", padx=18)

        tk.Label(footer, text="Enter — отправить   ·   Esc — пауза   ·   Ctrl+M — мини   ·   Ctrl+L — очистить",

                 font=T.FONT_SMALL, fg=T.DIM, bg=T.SURFACE).pack(side="right", padx=18)



        self._body_frame = tk.Frame(self, bg=T.BG)

        self._build_sidebar(self._body_frame)

        tk.Frame(self._body_frame, bg=T.BORDER, width=1).pack(side="left", fill="y")

        self._build_main(self._body_frame)



        self._build_compact()

        self._pack_main()



    def _pack_main(self) -> None:

        """Вернуть обычную раскладку окна (после мини-режима)."""

        for widget in (self._top_frame, self._sep, self._footer_frame, self._body_frame):

            widget.pack_forget()

        self._top_frame.pack(fill="x")

        self._sep.pack(fill="x")

        self._footer_frame.pack(side="bottom", fill="x")

        self._body_frame.pack(fill="both", expand=True)



                                                                        

                                                 

                                                                        

    def _build_compact(self) -> None:

        self.compact = tk.Frame(self, bg=T.SURFACE)

        row = tk.Frame(self.compact, bg=T.SURFACE)

        row.pack(fill="x", padx=10, pady=(7, 2))

        self.compact_dot = tk.Canvas(row, width=16, height=16, bg=T.SURFACE,

                                     highlightthickness=0)

        self.compact_dot.pack(side="left")

        self.compact_state = tk.Label(row, text="Готов", font=("Segoe UI Semibold", 10),

                                      fg=T.MUTED, bg=T.SURFACE)

        self.compact_state.pack(side="left", padx=(7, 10))

        self.compact_reply = tk.Label(row, text="", font=("Segoe UI", 9), fg=T.MUTED,

                                      bg=T.SURFACE, anchor="w")

        self.compact_reply.pack(side="left", fill="x", expand=True)

        for text, command, tip in (("□", self.toggle_compact, "вернуть окно"),

                                   ("—", self.iconify, "свернуть")):

            button = tk.Button(row, text=text, command=command, bg=T.SURFACE_3, fg=T.TEXT,

                               activebackground=T.SURFACE_2, activeforeground=T.ACCENT,

                               relief="flat", bd=0, highlightthickness=0, cursor="hand2",

                               font=("Segoe UI", 10), padx=7, pady=0)

            button.pack(side="right", padx=2)

            self._tooltip(button, tip)



        row2 = tk.Frame(self.compact, bg=T.SURFACE)

        row2.pack(fill="x", padx=10, pady=(0, 8))

        self.compact_entry = tk.Entry(row2, bg=T.SURFACE_2, fg=T.TEXT,

                                      insertbackground=T.ACCENT, relief="flat", bd=0,

                                      font=("Segoe UI", 10))

        self.compact_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))

        self.compact_entry.bind("<Return>", self._send)

        self._button(row2, "→", self._send, primary=True, small=True).pack(side="left")



    @staticmethod

    def _tooltip(widget, text: str) -> None:

        widget.bind("<Enter>", lambda _e: widget.configure(bg=T.SURFACE_2))

        widget.bind("<Leave>", lambda _e: widget.configure(bg=T.SURFACE_3))



    def _mode_caption(self, label: str, value: bool) -> str:

        return f"{label}: {'вкл' if value else 'выкл'}"



    def _toggle_thinking(self) -> None:

        self._thinking_mode = not self._thinking_mode

        config.THINKING_MODE = self._thinking_mode

        self.think_button.configure(text=self._mode_caption("Размышление", self._thinking_mode))

        self._append_chat("system", f"Режим размышления: {'включён' if self._thinking_mode else 'выключён'}.")



    def _toggle_no_scripts(self) -> None:

        self._no_scripts_mode = not self._no_scripts_mode

        config.NO_SCRIPTS_MODE = self._no_scripts_mode

        self.no_scripts_button.configure(text=self._mode_caption("Без скриптов", self._no_scripts_mode))

        self._append_chat("system", f"Режим без скриптов: {'включен' if self._no_scripts_mode else 'выключен'}.")



    def _toggle_confirm(self) -> None:

        self._confirm_mode = not self._confirm_mode

        config.CONFIRM_MODE = self._confirm_mode

        config.AUTO_CONFIRM = not self._confirm_mode

        self.confirm_button.configure(text=self._mode_caption("Подтверждение", self._confirm_mode))

        self._append_chat("system", f"Режим с подтверждением: {'включён' if self._confirm_mode else 'выключен'}.")



    def toggle_compact(self) -> None:

        if self._compact:

            self._exit_compact()

        else:

            self._enter_compact()



    def _enter_compact(self) -> None:

        if self._compact:

            return

        self._compact = True

        try:

            self._saved_geometry = self.geometry()

        except tk.TclError:

            self._saved_geometry = None

        for widget in (self._top_frame, self._sep, self._footer_frame, self._body_frame):

            widget.pack_forget()

        self.compact.pack(fill="both", expand=True)

        self.minsize(360, 96)

        screen_w = self.winfo_screenwidth()

        self.geometry(f"480x104+{max(0, screen_w - 500)}+12")

        self.update_idletasks()

        if config.COMPACT_TOPMOST:

            try:

                self.attributes("-topmost", True)

            except tk.TclError:

                pass

        self.compact_entry.focus_set()

        self._set_state(self._state, self._detail)

        self._toast("Мини-режим: Ctrl+M — вернуть окно")



    def _exit_compact(self) -> None:

        if not self._compact:

            return

        self._compact = False

        self.compact.pack_forget()

        self._pack_main()

        try:

            self.attributes("-topmost", False)

        except tk.TclError:

            pass

        self.minsize(1000, 640)

        if self._saved_geometry:

            self.geometry(self._saved_geometry)

        self.entry.focus_set()



    def _draw_compact_dot(self) -> None:

        canvas = self.compact_dot

        canvas.delete("all")

        color = T.STATE_COLORS.get(self._state, T.ACCENT)

        active = self._state in {"listening", "speaking", "thinking", "waiting"}

        pulse = (math.sin(self._phase) + 1) / 2 if active else 0.4

        canvas.create_oval(1, 1, 15, 15,

                           fill=T.blend(T.SURFACE, color, 0.20 + 0.40 * pulse), outline="")

        canvas.create_oval(4, 4, 12, 12, fill=color, outline="")



    def _build_sidebar(self, parent) -> None:

        sidebar = tk.Frame(parent, bg=T.SURFACE, width=312)

        sidebar.pack(side="left", fill="y")

        sidebar.pack_propagate(False)

        inner = tk.Frame(sidebar, bg=T.SURFACE)

        inner.pack(fill="both", expand=True, padx=18, pady=14)



        self.orb = tk.Canvas(inner, width=276, height=152, bg=T.SURFACE, highlightthickness=0)

        self.orb.pack(pady=(0, 2))

        self.state_label = tk.Label(inner, text="Готов", font=("Segoe UI Semibold", 15),

                                    fg=T.TEXT, bg=T.SURFACE)

        self.state_label.pack()

        self.detail_label = tk.Label(inner, text=DEFAULT_DETAILS["idle"], font=T.FONT_SMALL,

                                     fg=T.MUTED, bg=T.SURFACE, wraplength=270, justify="center")

        self.detail_label.pack(pady=(2, 10), fill="x")



        self.toggle_button = self._button(inner, "Запустить", self._toggle_listen, primary=True)

        self.toggle_button.pack(fill="x", pady=(0, 12))



        self.tts_var = tk.BooleanVar(value=config.TTS_ENABLED)

        self._check(inner, "Озвучивать ответы", self.tts_var, self._toggle_tts).pack(fill="x")

        self.dry_var = tk.BooleanVar(value=self._dry_run)

        self._check(inner, "Безопасный режим (без действий)", self.dry_var,

                    self._toggle_dry).pack(fill="x", pady=(0, 10))



        tk.Label(inner, text="МИКРОФОН", font=("Segoe UI Semibold", 8), fg=T.DIM,

                 bg=T.SURFACE).pack(anchor="w", pady=(4, 4))

        self.mic_combo = ttk.Combobox(inner, state="readonly", style="Dark.TCombobox",

                                      font=T.FONT_SMALL, values=["Загрузка…"])

        self.mic_combo.set("Загрузка…")

        self.mic_combo.pack(fill="x")

        self.mic_combo.bind("<<ComboboxSelected>>", self._on_mic_selected)



        sensitivity_head = tk.Frame(inner, bg=T.SURFACE)

        sensitivity_head.pack(fill="x", pady=(10, 0))

        tk.Label(sensitivity_head, text="ЧУВСТВИТЕЛЬНОСТЬ", font=("Segoe UI Semibold", 8),

                 fg=T.DIM, bg=T.SURFACE).pack(side="left")

        self.sensitivity_value = tk.Label(

            sensitivity_head, text=f"{config.MIC_SENSITIVITY:.1f}x", font=T.FONT_SMALL,

            fg=T.MUTED, bg=T.SURFACE,

        )

        self.sensitivity_value.pack(side="right")



        presets = tk.Frame(inner, bg=T.SURFACE)

        presets.pack(fill="x", pady=(4, 0))

        self._sensitivity_buttons: dict[float, tk.Button] = {}

        for preset in (0.7, 1.0, 1.5, 2.0, 3.0):

            button = tk.Button(

                presets,

                text=f"{preset:g}x",

                command=lambda value=preset: self._set_sensitivity(value),

                bg=T.SURFACE_3,

                fg=T.TEXT,

                activebackground=T.blend(T.SURFACE_3, T.ACCENT, 0.25),

                activeforeground=T.TEXT,

                relief="flat",

                bd=0,

                highlightthickness=0,

                cursor="hand2",

                font=T.FONT_SMALL,

                padx=2,

                pady=3,

            )

            button.pack(side="left", expand=True, fill="x", padx=2)

            self._sensitivity_buttons[preset] = button

        self._set_sensitivity(config.MIC_SENSITIVITY, persist=False)



        tk.Label(inner, text="БЫСТРЫЕ ДЕЙСТВИЯ", font=("Segoe UI Semibold", 8), fg=T.DIM,

                 bg=T.SURFACE).pack(anchor="w", pady=(16, 6))

        grid = tk.Frame(inner, bg=T.SURFACE)

        grid.pack(fill="x")

        for column in (0, 1):

            grid.columnconfigure(column, weight=1)

        for index, (label, command) in enumerate(QUICK_ACTIONS):

            button = self._button(grid, label, lambda c=command: self._quick(c), small=True)

            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=3, pady=3)



        spacer = tk.Frame(inner, bg=T.SURFACE)

        spacer.pack(fill="both", expand=True)

        self._button(inner, "Очистить диалог", self._clear_chat, small=True).pack(fill="x")



    def _build_main(self, parent) -> None:

        main = tk.Frame(parent, bg=T.BG)

        main.pack(side="left", fill="both", expand=True)



        self.tabs = ttk.Notebook(main, style="Dark.TNotebook")

        self.tabs.pack(fill="both", expand=True, padx=(6, 8), pady=(8, 4))

        self.chat_tab = tk.Frame(self.tabs, bg=T.BG)

        self.learn_tab = tk.Frame(self.tabs, bg=T.BG)

        self.tabs.add(self.chat_tab, text="  Диалог  ")

        self.tabs.add(self.learn_tab, text="  Обучение  ")

        self._build_chat_tab(self.chat_tab)

        self._build_learn_tab(self.learn_tab)



    def _build_chat_tab(self, main) -> None:

        header = tk.Frame(main, bg=T.BG)

        header.pack(fill="x", padx=22, pady=(14, 6))

        tk.Label(header, text="Диалог", font=("Segoe UI Semibold", 14), fg=T.TEXT,

                 bg=T.BG).pack(side="left")

        self.source_label = tk.Label(header, text="", font=T.FONT_SMALL, fg=T.DIM, bg=T.BG)

        self.source_label.pack(side="right")



        chat_wrap = tk.Frame(main, bg=T.SURFACE_2, highlightbackground=T.BORDER,

                             highlightthickness=1)

        chat_wrap.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        self.chat = tk.Text(

            chat_wrap,

            bg=T.SURFACE_2,

            fg=T.TEXT,

            insertbackground=T.ACCENT,

            relief="flat",

            bd=0,

            wrap="word",

            padx=16,

            pady=12,

            font=T.FONT_CHAT,

            spacing1=1,

            spacing3=1,

            state="normal",

        )

        scrollbar = ttk.Scrollbar(chat_wrap, orient="vertical", command=self.chat.yview,

                                  style="Dark.Vertical.TScrollbar")

        self.chat.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")

        self.chat.pack(side="left", fill="both", expand=True)

        self.chat.tag_configure("meta_user", foreground=T.ACCENT, font=("Segoe UI Semibold", 8),

                                spacing1=10)

        self.chat.tag_configure("meta_bot", foreground=T.GREEN, font=("Segoe UI Semibold", 8),

                                spacing1=10)

        self.chat.tag_configure("user", foreground=T.TEXT, font=("Segoe UI", 11),

                                lmargin1=2, lmargin2=2, spacing3=2)

        self.chat.tag_configure("bot", foreground=T.TEXT, font=("Segoe UI", 11),

                                lmargin1=2, lmargin2=2, spacing3=2)

        self.chat.tag_configure("system", foreground=T.MUTED, font=("Segoe UI", 9),

                                lmargin1=2, lmargin2=2, spacing3=2)

        self.chat.tag_configure("error", foreground=T.RED, font=("Segoe UI", 9),

                                lmargin1=2, lmargin2=2, spacing3=2)

        self.chat.tag_configure("thought", foreground=T.MUTED, font=("Segoe UI", 10, "italic"),

                                lmargin1=2, lmargin2=2, spacing3=2)

        self.chat.tag_configure("meta_thought", foreground=T.PURPLE if hasattr(T, "PURPLE") else T.MUTED,

                                font=("Segoe UI Semibold", 8), spacing1=6)



        live = tk.Frame(main, bg=T.BG)

        live.pack(fill="x", padx=22)

        tk.Label(live, text="Услышал:", font=T.FONT_SMALL, fg=T.DIM, bg=T.BG).pack(side="left")

        self.heard_var = tk.StringVar(value="")

        self.heard_label = tk.Label(live, textvariable=self.heard_var, font=T.FONT_SMALL,

                                    fg=T.MUTED, bg=T.BG, anchor="w")

        self.heard_label.pack(side="left", fill="x", expand=True, padx=8)

        self.level_canvas = tk.Canvas(live, width=110, height=16, bg=T.BG,

                                      highlightthickness=0)

        self.level_canvas.pack(side="right")



        self.intent_var = tk.StringVar(value="")

        tk.Label(main, textvariable=self.intent_var, font=T.FONT_SMALL, fg=T.DIM, bg=T.BG,

                 anchor="w").pack(fill="x", padx=22, pady=(2, 6))



        input_wrap = tk.Frame(main, bg=T.BG)

        input_wrap.pack(fill="x", padx=22, pady=(0, 12))

        box = tk.Frame(input_wrap, bg=T.SURFACE_2, highlightbackground=T.BORDER,

                       highlightthickness=1)

        box.pack(side="left", fill="x", expand=True)

        self.entry = tk.Entry(box, bg=T.SURFACE_2, fg=T.TEXT, insertbackground=T.ACCENT,

                              relief="flat", bd=0, font=("Segoe UI", 11))

        self.entry.pack(side="left", fill="x", expand=True, padx=14, pady=11)

        self.entry.bind("<Return>", self._send)

        self.entry.bind("<Up>", self._history_up)

        self.entry.bind("<Down>", self._history_down)

        self._button(input_wrap, "Отправить", self._send, primary=True).pack(

            side="left", padx=(8, 0))



                                                                        

                                                            

                                                                        

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



                                                                        

                       

                                                                        

    def _autostart(self) -> None:

        self._load_devices()

        self._set_state("loading", "Запускаю…")

        self.controller.start()

        self.after(1500, self._check_health)



    def _load_devices(self) -> None:

        def work() -> None:

            try:

                from ears.listener import Listener



                items = Listener.list_devices()

                self.events.put({"type": "devices", "items": items})

            except Exception as exc:                

                self.events.put({"type": "error", "message": f"Не удалось получить микрофоны: {exc}"})



        threading.Thread(target=work, name="ui-devices", daemon=True).start()



    def _apply_devices(self, items) -> None:

        labels = ["По умолчанию"]

        self._mic_map = {"По умолчанию": None}

        for index, name, channels in items:

            label = f"[{index}] {name}" + ("" if channels < 2 else " · stereo")

            labels.append(label)

            self._mic_map[label] = index

        self.mic_combo.configure(values=labels)



        selected = "По умолчанию"

        configured = config.MIC_DEVICE

        for label, index in self._mic_map.items():

            if configured is None:

                break

            if isinstance(configured, int) and index == configured:

                selected = label

                break

            if isinstance(configured, str) and configured.lower() in label.lower():

                selected = label

                break

        self.mic_combo.set(selected)



    def _on_mic_selected(self, _event=None) -> None:

        label = self.mic_combo.get()

        index = self._mic_map.get(label)

        self.controller.set_device(index)

        self._set_detail(f"Переключаю микрофон: {label}")



    def _set_sensitivity(self, value: float, persist: bool = True) -> None:

        try:

            sensitivity = max(0.5, min(3.0, round(float(value), 1)))

        except (TypeError, ValueError):

            return

        config.MIC_SENSITIVITY = sensitivity

        self.sensitivity_value.configure(text=f"{sensitivity:.1f}x")

        self.controller.set_sensitivity(sensitivity)

        for preset, button in self._sensitivity_buttons.items():

            active = abs(preset - sensitivity) < 0.01

            button.configure(

                bg=T.ACCENT if active else T.SURFACE_3,

                fg=T.BG if active else T.TEXT,

                activebackground=T.blend(T.ACCENT, "#ffffff", 0.2) if active

                else T.blend(T.SURFACE_3, T.ACCENT, 0.25),

            )

        if persist and config.save_env_value("JARVIS_SENSITIVITY", f"{sensitivity:.1f}"):

            self._toast(f"Чувствительность {sensitivity:.1f}x сохранена")



    def _toggle_listen(self) -> None:

        if not self._running:

            self._set_state("loading", "Запускаю…")

            self.controller.start()

            return

        if not self._audio_ok:

            self._set_detail("Перезапускаю микрофон…")

            self.controller.request_audio_restart()

            return

        if self.controller.paused:

            self.controller.resume()

        else:

            self.controller.pause()

        self._refresh_toggle_button()



    def _voice_name(self) -> str:

        if not config.TTS_ENABLED:

            return "выкл"

        try:

            from tts.speaker import current_engine



            return current_engine()

        except Exception:                

            return "—"



    def _toggle_tts(self) -> None:

        config.TTS_ENABLED = bool(self.tts_var.get())

        if config.TTS_ENABLED:

            self._toast(f"Озвучка включена: {self._voice_name()}")

        else:

            self._toast("Озвучка выключена")



    def _toggle_dry(self) -> None:

        self._dry_run = bool(self.dry_var.get())

        self.controller.set_dry_run(self._dry_run)

        self._toast("Безопасный режим включён: действия не выполняются"

                    if self._dry_run else "Безопасный режим выключен")



    def _active_entry(self):

        """Поле ввода текущего режима (обычное окно или мини)."""

        return self.compact_entry if self._compact else self.entry



    def _quick(self, command: str) -> None:

        self.controller.submit_text(command)

        self._active_entry().focus_set()



    def _send(self, _event=None) -> None:

        entry = self._active_entry()

        text = entry.get().strip()

        if not text:

            return

        entry.delete(0, "end")

        self._history.append(text)

        self._history_pos = len(self._history)

        if not self._running:

            self.controller.start()

        self.controller.submit_text(text)



    def _history_up(self, _event=None) -> str:

        if self._history:

            self._history_pos = max(0, self._history_pos - 1)

            self.entry.delete(0, "end")

            self.entry.insert(0, self._history[self._history_pos])

        return "break"



    def _history_down(self, _event=None) -> str:

        if self._history:

            self._history_pos = min(len(self._history), self._history_pos + 1)

            self.entry.delete(0, "end")

            if self._history_pos < len(self._history):

                self.entry.insert(0, self._history[self._history_pos])

        return "break"



    def _clear_chat(self) -> None:

        self.chat.configure(state="normal")

        self.chat.delete("1.0", "end")

        self.chat.configure(state="disabled")

        self.heard_var.set("")

        self.intent_var.set("")



                                                                        

                    

                                                                        

    def _poll_events(self) -> None:

        for _ in range(120):

            try:

                event = self.events.get_nowait()

            except queue.Empty:

                break

            try:

                self._handle_event(event)

            except Exception:                

                log.exception("Ошибка обработки события UI: %s", event.get("type"))

        self.after(60, self._poll_events)



    def _handle_event(self, event: dict) -> None:

        kind = event.get("type")

        if kind == "controller":

            self._running = bool(event.get("running"))

            self._refresh_toggle_button()

        elif kind == "state":

            self._set_state(event.get("state", "idle"), event.get("detail") or "")

        elif kind == "ready":

            self._audio_ok = bool(event.get("audio"))

            self._model_name = event.get("model") or "—"

            self._mic_name = event.get("mic") or "—"

            self.footer_info.configure(

                text=f"Whisper: {self._model_name}   ·   Микрофон: {self._mic_name}"

                     f"   ·   Голос: {self._voice_name()}")

            if not self._audio_ok:

                if event.get("disabled"):

                    self._append_chat("system", "Микрофон отключён — текстовый режим. "

                                                "Кнопка «Включить микрофон» запустит слух.")

                    self._set_state("text", "Можно печатать команды")

                else:

                    self._append_chat("system", "Микрофон недоступен — интерфейс работает в текстовом режиме. "

                                                "Команды можно писать в поле ниже.")

            self._refresh_toggle_button()

        elif kind == "user":

            self._append_chat("user", event.get("text", ""))

            self.heard_var.set("")

        elif kind == "assistant":

            self._append_chat("assistant", event.get("text", ""))

        elif kind == "thought":

            self._append_chat("thought", event.get("text", ""))

        elif kind == "intent":

            source = "локально" if event.get("source") == "router" else "LLM"

            cost = event.get("cost")

            suffix = f" · {cost:g} ₽" if cost else ""

            self.source_label.configure(

                text=f"{source} · {event.get('summary', '')} · {event.get('elapsed', 0)} с{suffix}"

            )

            self.intent_var.set(f"Последний интент: {event.get('summary', '')}")

        elif kind == "tool":

            name = event.get("name", "?")

            self._set_detail(f"Выполняю: {name}…")

            self.source_label.configure(text=f"инструмент · {name}")

        elif kind == "tool_result":

            name = event.get("name", "?")

            state = "готово" if event.get("ok") else "ошибка"

            self.source_label.configure(text=f"инструмент · {name} · {state}")

        elif kind == "transcript":

            self.heard_var.set(event.get("text", ""))

        elif kind == "level":

            self._target_level = max(self._target_level, float(event.get("value", 0.0)))

        elif kind == "error":

            self._append_chat("error", event.get("message", "Неизвестная ошибка"))

            if event.get("fatal"):

                self._running = False

                self._refresh_toggle_button()

        elif kind == "audio_lost":

            self._audio_ok = False

            self._append_chat("system", "Микрофон отключился. Нажми «Включить микрофон», чтобы попробовать снова.")

            self._refresh_toggle_button()

        elif kind == "toast":

            self._toast(event.get("text", ""))

        elif kind == "devices":

            self._apply_devices(event.get("items") or [])

        elif kind == "health":

            self._apply_health(event)

        elif kind == "confirm":

            self._show_confirm(event)



    def _show_confirm(self, event: dict) -> None:

        prompt = event.get("prompt", "Подтвердить действие?")

        self.bell()

        answer = messagebox.askyesno("Подтверждение", prompt, parent=self)

        self.controller.resolve_confirm(event.get("token"), answer)



    def _apply_health(self, event: dict) -> None:

        if event.get("ok"):

            via = str(event.get("via") or "").split(":")[-1]

            self.provider_label.configure(text=f"Сервис: онлайн · {via}", fg=T.GREEN)

        else:

            self.provider_label.configure(text="LLM: офлайн — проверь VPN/ключи", fg=T.RED)



    def _check_health(self) -> None:

        self.provider_label.configure(text="LLM: проверка…", fg=T.AMBER)



        def work() -> None:

            try:

                from brain.brain import health_check



                result = health_check()

            except Exception as exc:                

                result = {"ok": False, "error": str(exc)}

            self.events.put({"type": "health", **result})



        threading.Thread(target=work, name="ui-health", daemon=True).start()



    def _on_model_selected(self, _event=None) -> None:

        label = self.model_combo.get().strip()

        if not label:

            return

        chosen = brain.set_active_model(label)

        self.model_status.configure(text=f"активно: {chosen}", fg=T.GREEN)

        self.provider_label.configure(text=f"LLM: {chosen}", fg=T.AMBER)



    def _check_models(self) -> None:

        self.model_status.configure(text="Проверка моделей…", fg=T.AMBER)



        def work() -> None:

            labels = brain.model_labels()

            good = 0

            errors = []

            for label in labels:

                ok, msg = brain.ping_model(label, timeout_sec=6.0)

                if ok:

                    good += 1

                else:

                    errors.append(f"{label}: {msg[:40]}")

            text = f"OK: {good}/{len(labels)}" + ("" if not errors else f" (нет: {len(errors)})")

            self.after(0, self._set_model_status, text, errors)



        threading.Thread(target=work, name="ui-model-check", daemon=True).start()



    def _set_model_status(self, text: str, errors: list[str] | None = None) -> None:

        color = T.GREEN if (errors is None or not errors) else T.AMBER

        self.model_status.configure(text=text, fg=color)

        if errors:

            log.warning("Недоступные модели: " + "; ".join(errors))

                                                                        

                        

                                                                        

    def _set_state(self, state: str, detail: str = "") -> None:

        self._state = state

        color = T.STATE_COLORS.get(state, T.ACCENT)

        self.state_label.configure(text=T.STATE_LABELS.get(state, state), fg=color)

        if hasattr(self, "compact_state"):

            self.compact_state.configure(text=T.STATE_LABELS.get(state, state), fg=color)

        self._set_detail(detail or DEFAULT_DETAILS.get(state, ""))

        self._refresh_toggle_button()



    def _set_detail(self, text: str) -> None:

        self._detail = text

        self.detail_label.configure(text=text)



    def _refresh_toggle_button(self) -> None:

        if not self._running:

            label = "Запустить"

        elif not self._audio_ok:

            label = "Включить микрофон"

        elif self.controller.paused:

            label = "Продолжить"

        else:

            label = "Пауза"

        self.toggle_button.configure(text=label)



    def _animate(self) -> None:

        if self._compact and self.winfo_exists():

            self._phase += 0.12

            self._draw_compact_dot()

            self.after(120, self._animate)

            return

                                                                          

                                                                    

        try:

            minimized = self.state() == "iconic"

            focused = self.focus_displayof() is not None

        except tk.TclError:

            minimized, focused = False, True

        if minimized:

            self.after(500, self._animate)

            return

        busy = self._state in {"thinking", "speaking", "waiting"}

        if focused or busy:

            self._phase += 0.09

            self._level += (self._target_level - self._level) * 0.28

            self._target_level *= 0.93

            self._draw_orb()

            self._draw_level()

            delay = 50

        else:

            self._level += (self._target_level - self._level) * 0.6

            self._target_level *= 0.9

            self._draw_orb()

            delay = int(1000 / max(1, config.UI_FPS_BACKGROUND))

        self.after(delay, self._animate)



    def _draw_orb(self) -> None:

        canvas = self.orb

        canvas.delete("all")

        cx, cy = 138, 76

        color = T.STATE_COLORS.get(self._state, T.ACCENT)

        active = self._state in {"listening", "speaking", "thinking", "waiting"}

        pulse = (math.sin(self._phase) + 1) / 2 if active else 0.35

        for radius, strength in ((62, 0.10), (54, 0.16), (46, 0.24)):

            fill = T.blend(T.SURFACE, color, min(1.0, strength + 0.12 * pulse))

            canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,

                               fill=fill, outline="")

        canvas.create_oval(cx - 34, cy - 34, cx + 34, cy + 34,

                           fill=T.SURFACE_2, outline=color, width=2)

        canvas.create_text(cx, cy, text="J", fill=color, font=T.FONT_ORB)

        if self._audio_ok and self._level > 0.02:

            extent = -max(10.0, 330 * min(1.0, self._level))

            canvas.create_arc(cx - 52, cy - 52, cx + 52, cy + 52, start=90, extent=extent,

                              style="arc", outline=color, width=3)



    def _draw_level(self) -> None:

        canvas = self.level_canvas

        canvas.delete("all")

        bars = 10

        width = 110 / bars

        active = int(round(self._level * bars))

        color = T.GREEN if self._audio_ok else T.DIM

        for index in range(bars):

            height = 4 + (index % 3) * 2

            x0 = index * width + 1

            y0 = 16 - height

            fill = color if index < active else T.SURFACE_3

            canvas.create_rectangle(x0, y0, x0 + width - 2, 16, fill=fill, outline="")



                                                                        

            

                                                                        

    def _append_chat(self, kind: str, text: str) -> None:

        text = (text or "").strip()

        if not text:

            return

        stamp = datetime.now().strftime("%H:%M")

        self.chat.configure(state="normal")

        if kind == "user":

            self.chat.insert("end", f"ВЫ  ·  {stamp}\n", "meta_user")

            self.chat.insert("end", text + "\n", "user")

        elif kind == "assistant":

            self.chat.insert("end", f"ДЖАРВИС  ·  {stamp}\n", "meta_bot")

            self.chat.insert("end", text + "\n", "bot")

        elif kind == "thought":

            self.chat.insert("end", f"МЫСЛИ  ·  {stamp}\n", "meta_thought")

            self.chat.insert("end", text + "\n", "thought")

        elif kind == "error":

            self.chat.insert("end", f"Ошибка · {stamp}\n", "meta_user")

            self.chat.insert("end", text + "\n", "error")

        else:

            self.chat.insert("end", text + "\n", "system")

        self.chat.configure(state="disabled")

        self.chat.see("end")

        if hasattr(self, "compact_reply"):

            short = " ".join(text.split())

            self.compact_reply.configure(

                text=("Джарвис: " if kind == "assistant" else "") + short[:120])



    def _toast(self, text: str) -> None:

        if not text:

            return

        self._set_detail(text)

        self.after(5000, lambda: self._restore_detail(text))



    def _restore_detail(self, toast_text: str) -> None:

        if self._detail == toast_text:

            self._set_detail(DEFAULT_DETAILS.get(self._state, ""))



                                                                        

    def _on_close(self) -> None:

        try:

            self.controller.stop(timeout=1.5)

        finally:

            self.destroy()





def run_gui(*, audio: bool = True, dry_run: bool = False, no_tts: bool = False,

            autostart: bool = True) -> int:

    app = JarvisApp(audio=audio, dry_run=dry_run, no_tts=no_tts, autostart=autostart)

    app.mainloop()

    return 0

