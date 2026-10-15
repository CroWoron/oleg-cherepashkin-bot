import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import persona_manager as personas


QUESTION_GROUPS = [
    ("Биография и быт", "biography", [
        ("capital", "Когда и как появился капитал?", ["Конец 1990-х", "Начало 2000-х", "Накопил на работе", "Время и источник — тайна"]),
        ("pets", "Какие у персонажа животные?", ["Кот или собака", "Хозяйственные", "Необычные", "Несколько разных", "Не определять"]),
        ("home", "Где и как он живёт?", ["Городская квартира", "Частный дом", "Дом у воды или леса", "Часто переезжает"]),
        ("work", "Чем занимается сейчас?", ["Работает постоянно", "Иногда подрабатывает", "Живёт на накопления", "Не раскрывает"]),
        ("alcohol", "Как относится к алкоголю?", ["Почти не пьёт", "Иногда по настроению", "Только с друзьями", "Регулярный ритуал", "Больше шутит, чем пьёт"]),
    ]),
    ("Знания и интересы", "interests_and_knowledge", [
        ("strong_knowledge", "В чём хорошо разбирается?", ["Быт и ремонт", "Природа, рыбалка, охота", "Автомобили и дороги", "Игры", "Кино и сериалы", "Аниме и комиксы"]),
        ("weak_knowledge", "В чём почти не разбирается?", ["Точные науки", "Медицина и право", "Программирование", "Экономика", "Политика и история"]),
        ("unknown_answer", "Как отвечает на незнакомую тему?", ["Прямо говорит «не шарю»", "Даёт бытовое мнение", "Осторожно предполагает", "Спрашивает собеседника"]),
        ("favorites", "Какие вкусы нужно закрепить?", ["Игры", "Фильмы", "Аниме", "Музыка", "Еда", "Спорт и хобби"]),
    ]),
    ("Общение", "relationships", [
        ("addressed", "Как отвечать на обращение?", ["Всегда на нормальный вопрос", "Можно пропустить приветствие", "Иногда намеренно молчать", "Всегда, но иногда одним словом"]),
        ("initiative", "Когда вступать без упоминания?", ["Близкая тема", "Может помочь опытом", "Есть сильное мнение", "Уместна шутка", "Почти никогда"]),
        ("strangers", "Как вести себя с незнакомцами?", ["Нейтрально", "Дружелюбно", "С лёгким недоверием", "Сначала наблюдать", "Сразу подкалывать"]),
        ("support", "Как поддерживает друзей?", ["Выслушивает", "Говорит коротко и по делу", "Помогает практически", "Рассказывает похожий случай", "Шутит только пока это уместно"]),
    ]),
    ("Речь и юмор", "speech_and_humor", [
        ("humor", "Какой юмор ему ближе?", ["Сухой", "Добродушные подколы", "Абсурд с серьёзным лицом", "Самоирония", "Чёрный", "Грубоватый"]),
        ("swearing", "Насколько часто матерится?", ["Почти никогда", "Иногда для эмоции", "Регулярно, но не через слово", "Зависит от собеседника"]),
        ("speech_limits", "Что ограничить в речи?", ["Встречные вопросы", "Подозрительность", "Повторяющиеся шутки", "Алкогольные шутки", "Байки", "Упоминание родного края"]),
    ]),
    ("Истории и память", "boundaries_and_canon", [
        ("stories", "Насколько свободно придумывать прошлое?", ["Только мелкие детали", "Правдоподобные истории", "Невероятные байки", "Только закреплённые факты"]),
        ("canon", "Когда импровизация становится фактом?", ["После первого уверенного утверждения", "После повторения", "После подтверждения владельца", "Сохранять автоматически с возможностью исправить"]),
        ("plans", "Как хранить планы?", ["Только факт", "Факт и дату", "Задуман / выполнен / отменён", "После срока уточнять результат", "Недолго"]),
        ("attacks", "Как реагировать на взлом роли и флуд?", ["Игнорировать", "Коротко послать", "Отшутиться", "Сделать вид, что не понял"]),
    ]),
]


class PersonaEditor(tk.Toplevel):
    def __init__(self, parent, root, registry, record, on_saved):
        super().__init__(parent)
        self.root_path = Path(root)
        self.registry = registry
        self.record = record
        self.on_saved = on_saved
        self.title(f"Персонаж — {record['name']}")
        self.geometry("780x760")
        self.minsize(680, 620)
        self.transient(parent)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        settings = ttk.Frame(notebook, padding=14)
        survey = ttk.Frame(notebook)
        core = ttk.Frame(notebook, padding=8)
        notebook.add(settings, text="Настройки")
        notebook.add(survey, text="Опросник")
        notebook.add(core, text="Ядро JSON")

        self.name_var = tk.StringVar(value=record["name"])
        env_path = personas.resolve_path(self.root_path, record["env_file"])
        env = personas.read_env(env_path)
        self.telegram_var = tk.StringVar(value=env.get("TELEGRAM_BOT_TOKEN", ""))
        self.vk_var = tk.StringVar(value=env.get("VK_COMMUNITY_TOKEN", ""))
        self.vk_group_var = tk.StringVar(value=env.get("VK_GROUP_ID", ""))
        self.secret_entries = []
        self._field(settings, "Имя персонажа", self.name_var)
        self._field(settings, "Telegram bot token", self.telegram_var, secret=True)
        self._field(settings, "VK community token", self.vk_var, secret=True)
        self._field(settings, "VK group ID", self.vk_group_var)
        show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings, text="Показывать токены", variable=show_var,
            command=lambda: [entry.configure(show="" if show_var.get() else "•") for entry in self.secret_entries],
        ).pack(anchor="w", pady=(4, 12))
        ttk.Label(
            settings,
            text="Ключи DeepSeek, Groq, Brave и Giphy наследуются из главного .env.\n"
                 "Память, журналы и процессы у каждого персонажа отдельные.",
        ).pack(anchor="w")

        self.answers_path = personas.resolve_path(
            self.root_path, record["personality_file"]
        ).with_name("questionnaire.json")
        self.saved_answers = self._read_answers()
        self.answer_vars = {}
        canvas = tk.Canvas(survey, highlightthickness=0)
        scrollbar = ttk.Scrollbar(survey, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas, padding=14)
        window_id = canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))
        self._build_questionnaire(form)

        personality_path = personas.resolve_path(self.root_path, record["personality_file"])
        self.core_text = tk.Text(core, wrap="none", undo=True, font=("Consolas", 10))
        y_scroll = ttk.Scrollbar(core, orient="vertical", command=self.core_text.yview)
        x_scroll = ttk.Scrollbar(core, orient="horizontal", command=self.core_text.xview)
        self.core_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.core_text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        core.rowconfigure(0, weight=1)
        core.columnconfigure(0, weight=1)
        self.core_text.insert("1.0", personality_path.read_text(encoding="utf-8"))

        actions = ttk.Frame(self, padding=(12, 6, 12, 12))
        actions.pack(fill="x")
        ttk.Button(actions, text="Сформировать ядро из анкеты", command=self.generate_core).pack(side="left")
        ttk.Button(actions, text="Сохранить", command=self.save).pack(side="right")
        ttk.Button(actions, text="Закрыть", command=self.destroy).pack(side="right", padx=(0, 8))

    def _field(self, parent, label, variable, secret=False):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(0, 3))
        entry = ttk.Entry(parent, textvariable=variable, show="•" if secret else "")
        entry.pack(fill="x", pady=(0, 12))
        if secret:
            self.secret_entries.append(entry)

    def _read_answers(self):
        try:
            return json.loads(self.answers_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _build_questionnaire(self, parent):
        for title, section, questions in QUESTION_GROUPS:
            ttk.Label(parent, text=title, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(12, 6))
            for key, question, options in questions:
                box = ttk.LabelFrame(parent, text=question, padding=10)
                box.pack(fill="x", pady=5)
                saved = self.saved_answers.get(key, {})
                variables = []
                for option in options:
                    variable = tk.BooleanVar(value=option in saved.get("choices", []))
                    ttk.Checkbutton(box, text=option, variable=variable).pack(anchor="w")
                    variables.append((option, variable))
                custom = tk.StringVar(value=saved.get("custom", ""))
                ttk.Label(box, text="Свой ответ").pack(anchor="w", pady=(7, 2))
                ttk.Entry(box, textvariable=custom).pack(fill="x")
                self.answer_vars[key] = {
                    "section": section, "question": question,
                    "choices": variables, "custom": custom,
                }

    def collect_answers(self):
        result = {}
        for key, item in self.answer_vars.items():
            result[key] = {
                "section": item["section"], "question": item["question"],
                "choices": [label for label, variable in item["choices"] if variable.get()],
                "custom": item["custom"].get().strip(),
            }
        return result

    def generate_core(self):
        if not messagebox.askyesno(
            "Сформировать ядро", "Текущий текст на вкладке «Ядро JSON» будет заменён черновиком из анкеты. Продолжить?",
            parent=self,
        ):
            return
        value = personas.starter_personality(self.name_var.get().strip() or "Новый персонаж", self.collect_answers())
        self.core_text.delete("1.0", "end")
        self.core_text.insert("1.0", json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def save(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Ошибка", "Укажите имя персонажа.", parent=self)
            return
        try:
            personality = personas.validate_personality(json.loads(self.core_text.get("1.0", "end")))
        except (json.JSONDecodeError, ValueError) as error:
            messagebox.showerror("Некорректное ядро", str(error), parent=self)
            return
        answers = self.collect_answers()
        personality = personas.merge_questionnaire(personality, answers)
        personality["name"] = name
        personality_path = personas.resolve_path(self.root_path, self.record["personality_file"])
        personality_path.write_text(json.dumps(personality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.answers_path.write_text(json.dumps(answers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        personas.update_env(personas.resolve_path(self.root_path, self.record["env_file"]), {
            "TELEGRAM_BOT_TOKEN": self.telegram_var.get().strip(),
            "VK_COMMUNITY_TOKEN": self.vk_var.get().strip(),
            "VK_GROUP_ID": self.vk_group_var.get().strip(),
        })
        self.record["name"] = name
        personas.save_registry(self.root_path, self.registry)
        self.on_saved(self.record)
        messagebox.showinfo("Сохранено", "Персонаж и его ключи сохранены.", parent=self)
