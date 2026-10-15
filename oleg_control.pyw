import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import persona_manager as personas
from persona_editor import PersonaEditor


ROOT = Path(__file__).resolve().parent
PYTHON = str(Path(sys.executable).with_name("python.exe"))
CREATE_NO_WINDOW = 0x08000000


def read_pid(path):
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def process_running(pid):
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def service_running(service):
    return process_running(read_pid(service["pid"]))


def service_health(service):
    if not service_running(service):
        return "off", "Выключен"
    try:
        status = json.loads(service["status"].read_text(encoding="utf-8"))
        age = time.time() - int(status.get("updated_at", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "starting", "Запускается"
    if status.get("state") == "online" and age <= 90:
        return "online", "Работает"
    if status.get("state") == "reconnecting":
        return "reconnecting", "Переподключается"
    if status.get("state") == "error":
        return "error", "Нет связи"
    return "starting", "Запускается"


def start_service(service, environment):
    if service_running(service):
        return
    try:
        service["pid"].unlink(missing_ok=True)
    except OSError:
        pass
    subprocess.Popen(
        [PYTHON, str(service["script"])], cwd=ROOT, env=environment,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
    )


def stop_service(service):
    pid = read_pid(service["pid"])
    if process_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        service["pid"].unlink(missing_ok=True)
    except OSError:
        pass


class CharacterControl(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Персонажи — управление")
        self.configure(bg="#171a20")
        self.registry = personas.load_registry(ROOT)
        self.record = self._record_by_id(self.registry.get("selected"))
        self.services = personas.services_for(ROOT, self.record)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Root.TFrame", background="#171a20")
        style.configure("Card.TFrame", background="#232832")
        style.configure("Title.TLabel", background="#171a20", foreground="#f2f4f8", font=("Segoe UI", 17, "bold"))
        style.configure("Hint.TLabel", background="#171a20", foreground="#9ca6b5", font=("Segoe UI", 9))
        style.configure("Service.TLabel", background="#232832", foreground="#f2f4f8", font=("Segoe UI", 11, "bold"))
        style.configure("State.TLabel", background="#232832", foreground="#9ca6b5", font=("Segoe UI", 9))
        style.configure("Switch.TCheckbutton", background="#232832", foreground="#dce2ea", font=("Segoe UI", 10), padding=6)
        style.map("Switch.TCheckbutton", background=[("active", "#2c3340")])
        style.configure("Action.TButton", font=("Segoe UI", 9), padding=(10, 6))

        header = ttk.Frame(self, style="Root.TFrame", padding=(20, 17, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text="Персонажи", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="У каждого персонажа свои ядро, ключи, память и каналы.", style="Hint.TLabel").pack(anchor="w", pady=(3, 10))
        selector = ttk.Frame(header, style="Root.TFrame")
        selector.pack(fill="x")
        self.persona_name = tk.StringVar(value=self.record["name"])
        self.persona_combo = ttk.Combobox(selector, textvariable=self.persona_name, state="readonly", values=self._persona_names())
        self.persona_combo.pack(side="left", fill="x", expand=True)
        self.persona_combo.bind("<<ComboboxSelected>>", self.select_persona)
        ttk.Button(selector, text="Редактировать", style="Action.TButton", command=self.edit_persona).pack(side="left", padx=(8, 0))
        ttk.Button(selector, text="Новый", style="Action.TButton", command=self.new_persona).pack(side="left", padx=(8, 0))

        self.rows = {}
        body = ttk.Frame(self, style="Root.TFrame", padding=(20, 8, 20, 8))
        body.pack(fill="both", expand=True)
        for key in ("telegram", "vk"):
            self.add_service_row(body, key)

        footer = ttk.Frame(self, style="Root.TFrame", padding=(20, 4, 20, 16))
        footer.pack(fill="x")
        ttk.Button(footer, text="Запустить выбранного", style="Action.TButton", command=self.start_all).pack(side="left")
        ttk.Button(footer, text="Остановить выбранного", style="Action.TButton", command=self.stop_all).pack(side="left", padx=(8, 0))
        ttk.Button(footer, text="Карточки людей", style="Action.TButton", command=self.open_people_cards).pack(side="right")
        self.update_idletasks()
        width = max(610, self.winfo_reqwidth() + 8)
        height = max(390, self.winfo_reqheight() + 8)
        self.geometry(f"{width}x{height}")
        self.minsize(width, height)
        self.after(400, self.refresh_states)

    def _record_by_id(self, persona_id):
        return next((item for item in self.registry["personas"] if item["id"] == persona_id), self.registry["personas"][0])

    def _persona_names(self):
        return [item["name"] for item in self.registry["personas"]]

    def add_service_row(self, parent, key):
        service = self.services[key]
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10))
        card.pack(fill="x", pady=5)
        dot = tk.Canvas(card, width=18, height=18, bg="#232832", highlightthickness=0)
        dot.pack(side="left", padx=(0, 10))
        dot_id = dot.create_oval(3, 3, 15, 15, fill="#d9534f", outline="")
        labels = ttk.Frame(card, style="Card.TFrame")
        labels.pack(side="left", fill="x", expand=True)
        title = tk.StringVar(value=service["title"])
        ttk.Label(labels, textvariable=title, style="Service.TLabel").pack(anchor="w")
        state_label = ttk.Label(labels, text="Выключен", style="State.TLabel")
        state_label.pack(anchor="w")
        ttk.Button(card, text="Написать в чат", style="Action.TButton", command=lambda name=key: self.choose_chat(name)).pack(side="right", padx=(8, 0))
        ttk.Button(card, text="Общение", style="Action.TButton", command=lambda name=key: self.open_log(name, "communication_log")).pack(side="right", padx=(8, 0))
        ttk.Button(card, text="Система", style="Action.TButton", command=lambda name=key: self.open_log(name)).pack(side="right", padx=(8, 0))
        enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(card, text="Включён", variable=enabled, style="Switch.TCheckbutton", command=lambda name=key: self.toggle(name)).pack(side="right")
        self.rows[key] = {"enabled": enabled, "dot": dot, "dot_id": dot_id, "state": state_label, "title": title}

    def select_persona(self, _event=None):
        selected = self.persona_name.get()
        self.record = next(item for item in self.registry["personas"] if item["name"] == selected)
        self.registry["selected"] = self.record["id"]
        personas.save_registry(ROOT, self.registry)
        self.services = personas.services_for(ROOT, self.record)
        for key, service in self.services.items():
            self.rows[key]["title"].set(service["title"])
        self.refresh_states(schedule=False)

    def new_persona(self):
        name = simpledialog.askstring("Новый персонаж", "Имя персонажа:", parent=self)
        if not name or not name.strip():
            return
        self.record = personas.create_persona(ROOT, self.registry, name.strip())
        self._reload_selector()
        self.edit_persona()

    def edit_persona(self):
        PersonaEditor(self, ROOT, self.registry, self.record, self.persona_saved)

    def persona_saved(self, record):
        self.registry = personas.load_registry(ROOT)
        self.record = self._record_by_id(record["id"])
        self.registry["selected"] = self.record["id"]
        personas.save_registry(ROOT, self.registry)
        self._reload_selector()

    def _reload_selector(self):
        self.persona_combo.configure(values=self._persona_names())
        self.persona_name.set(self.record["name"])
        self.select_persona()

    def toggle(self, key):
        service = self.services[key]
        if self.rows[key]["enabled"].get():
            try:
                start_service(service, personas.persona_environment(ROOT, self.record))
            except Exception as error:
                messagebox.showerror("Не удалось запустить", str(error), parent=self)
        else:
            stop_service(service)
        self.after(700, lambda: self.refresh_states(schedule=False))

    def start_all(self):
        environment = personas.persona_environment(ROOT, self.record)
        for service in self.services.values():
            start_service(service, environment)
        self.after(800, lambda: self.refresh_states(schedule=False))

    def stop_all(self):
        for service in self.services.values():
            stop_service(service)
        self.after(500, lambda: self.refresh_states(schedule=False))

    def open_log(self, key, log_key="log"):
        service = self.services[key]
        path = service[log_key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        os.startfile(path)

    def open_people_cards(self):
        path = personas.identity_db_path(ROOT, self.record)
        window = tk.Toplevel(self)
        window.title(f"Карточки людей — {self.record['name']}")
        window.transient(self)
        window.configure(bg="#171a20")
        ttk.Label(
            window,
            text="Общая память Telegram и VK. Для объединения выберите две карточки.",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=16, pady=(14, 8))
        columns = ("name", "level", "relationship", "accounts")
        tree = ttk.Treeview(window, columns=columns, show="headings", selectmode="extended", height=16)
        headings = {
            "name": "Человек", "level": "Уровень отношения",
            "relationship": "Отношения", "accounts": "Аккаунты",
        }
        widths = {"name": 180, "level": 130, "relationship": 280, "accounts": 300}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=90)
        tree.pack(fill="both", expand=True, padx=16)

        def reload_cards():
            tree.delete(*tree.get_children())
            for card in personas.list_identity_cards(path):
                accounts = ", ".join(
                    f"{link['platform'].upper()}: {link['display_name']}" for link in card["links"]
                )
                tree.insert("", "end", iid=str(card["person_id"]), values=(
                    card["canonical_name"], card["level"], card["relationship"], accounts,
                ))

        def edit_card():
            selected = tree.selection()
            if len(selected) != 1:
                messagebox.showinfo("Карточка", "Выберите одну карточку.", parent=window)
                return
            card = next(
                item for item in personas.list_identity_cards(path)
                if item["person_id"] == int(selected[0])
            )
            editor = tk.Toplevel(window)
            editor.title("Редактировать карточку")
            editor.transient(window)
            editor.grab_set()
            editor.configure(bg="#171a20")
            form = ttk.Frame(editor, style="Root.TFrame", padding=16)
            form.pack(fill="both", expand=True)
            name = tk.StringVar(value=card["canonical_name"])
            addresses = tk.StringVar(value=", ".join(card["address_names"]))
            level = tk.StringVar(value=card["level"])
            for label, variable in (("Основное имя", name), ("Обращения через запятую", addresses)):
                ttk.Label(form, text=label, style="Hint.TLabel").pack(anchor="w", pady=(4, 2))
                ttk.Entry(form, textvariable=variable, width=68).pack(fill="x")
            ttk.Label(form, text="Уровень отношения", style="Hint.TLabel").pack(anchor="w", pady=(8, 2))
            ttk.Combobox(
                form, textvariable=level, values=personas.RELATIONSHIP_LEVELS,
                state="readonly", width=25,
            ).pack(anchor="w")
            ttk.Label(form, text="Характер отношений", style="Hint.TLabel").pack(anchor="w", pady=(8, 2))
            relationship = tk.Text(form, width=68, height=4, wrap="word")
            relationship.insert("1.0", card["relationship"])
            relationship.pack(fill="x")
            ttk.Label(form, text="Мнение Олега", style="Hint.TLabel").pack(anchor="w", pady=(8, 2))
            opinion = tk.Text(form, width=68, height=4, wrap="word")
            opinion.insert("1.0", card["opinion"])
            opinion.pack(fill="x")

            def save():
                try:
                    personas.update_identity_card(
                        path, card["person_id"], name.get(), addresses.get().split(","),
                        level.get(), relationship.get("1.0", "end").strip(),
                        opinion.get("1.0", "end").strip(),
                    )
                except (OSError, sqlite3.Error, ValueError) as error:
                    messagebox.showerror("Не удалось сохранить", str(error), parent=editor)
                    return
                editor.destroy()
                reload_cards()

            buttons = ttk.Frame(form, style="Root.TFrame", padding=(0, 12, 0, 0))
            buttons.pack(fill="x")
            ttk.Button(buttons, text="Сохранить", command=save).pack(side="right")
            ttk.Button(buttons, text="Отмена", command=editor.destroy).pack(side="right", padx=(0, 8))

        def merge_cards():
            selected = tree.selection()
            if len(selected) != 2:
                messagebox.showinfo("Объединение", "Выберите ровно две карточки.", parent=window)
                return
            labels = {item: tree.item(item, "values")[0] for item in selected}
            keep = tk.StringVar(value=selected[0])
            dialog = tk.Toplevel(window)
            dialog.title("Объединить карточки")
            dialog.transient(window)
            dialog.grab_set()
            dialog.configure(bg="#171a20")
            frame = ttk.Frame(dialog, style="Root.TFrame", padding=16)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="Какую карточку оставить основной?", style="Hint.TLabel").pack(anchor="w")
            for item in selected:
                ttk.Radiobutton(frame, text=labels[item], variable=keep, value=item).pack(anchor="w", pady=3)

            def confirm():
                target = int(keep.get())
                source = next(int(item) for item in selected if int(item) != target)
                try:
                    personas.merge_identity_cards(path, target, source)
                except (OSError, sqlite3.Error, ValueError) as error:
                    messagebox.showerror("Не удалось объединить", str(error), parent=dialog)
                    return
                dialog.destroy()
                reload_cards()

            ttk.Button(frame, text="Объединить", command=confirm).pack(side="right", pady=(12, 0))
            ttk.Button(frame, text="Отмена", command=dialog.destroy).pack(side="right", padx=(0, 8), pady=(12, 0))

        buttons = ttk.Frame(window, style="Root.TFrame", padding=16)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Закрыть", command=window.destroy).pack(side="right")
        ttk.Button(buttons, text="Объединить", command=merge_cards).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Редактировать", command=edit_card).pack(side="right", padx=(0, 8))
        tree.bind("<Double-Button-1>", lambda _event: edit_card())
        reload_cards()
        window.geometry("980x520")

    def choose_chat(self, platform):
        chats = [chat for chat in personas.known_service_chats(self.services) if chat[0] == platform]
        platform_name = {"telegram": "Telegram", "vk": "VK"}[platform]
        if not chats:
            messagebox.showinfo("Чаты", f"Персонаж пока не видел ни одного чата в {platform_name}.", parent=self)
            return
        window = tk.Toplevel(self)
        window.title(f"Написать в {platform_name}")
        window.transient(self)
        window.grab_set()
        window.configure(bg="#171a20")
        ttk.Label(
            window, text="Выберите чат. Персонаж пересмотрит хвост или напишет от себя.",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=16, pady=(14, 8))
        chat_list = tk.Listbox(
            window, width=72, height=min(14, max(4, len(chats))),
            bg="#232832", fg="#f2f4f8", selectbackground="#3b82f6",
            borderwidth=0, highlightthickness=0, font=("Segoe UI", 10),
        )
        chat_list.pack(fill="both", expand=True, padx=16)
        for _platform, chat_id, title, chat_type, _updated_at in chats:
            chat_list.insert("end", title or str(chat_id))
        chat_list.selection_set(0)
        modes = {
            "Сообщение": "speak",
            "Шутка": "joke",
            "Ответ на сообщение": "reply",
            "Мем (картинка или GIF)": "meme",
            "Подколоть участника": "tease",
        }
        mode_frame = ttk.Frame(window, style="Root.TFrame", padding=(16, 10, 16, 0))
        mode_frame.pack(fill="x")
        ttk.Label(mode_frame, text="Что написать:", style="Hint.TLabel").pack(side="left")
        mode_name = tk.StringVar(value="Сообщение")
        ttk.Combobox(
            mode_frame, textvariable=mode_name, state="readonly",
            values=list(modes), width=28,
        ).pack(side="left", padx=(8, 0))

        def submit():
            selection = chat_list.curselection()
            if not selection:
                return
            _platform, chat_id, _title, _chat_type, _updated_at = chats[selection[0]]
            if modes[mode_name.get()] == "tease":
                window.destroy()
                self.choose_tease_target(platform, chat_id)
                return
            command_id = personas.queue_service_command(
                self.services[platform], modes[mode_name.get()], chat_id
            )
            window.destroy()
            messagebox.showinfo(
                "Команда поставлена", f"Команда №{command_id} передана в {platform_name}.", parent=self
            )

        actions = ttk.Frame(window, style="Root.TFrame", padding=16)
        actions.pack(fill="x")
        ttk.Button(actions, text="Отмена", command=window.destroy).pack(side="right")
        ttk.Button(actions, text="Написать", command=submit).pack(side="right", padx=(0, 8))
        chat_list.bind("<Double-Button-1>", lambda _event: submit())
        window.update_idletasks()
        window.geometry(f"{max(560, window.winfo_reqwidth())}x{max(260, window.winfo_reqheight())}")

    def choose_tease_target(self, platform, chat_id):
        cards = personas.list_identity_cards(personas.identity_db_path(ROOT, self.record))
        if not cards:
            messagebox.showinfo("Подкол", "Карточек людей пока нет.", parent=self)
            return
        window = tk.Toplevel(self)
        window.title("Кого подколоть")
        window.transient(self)
        window.grab_set()
        window.configure(bg="#171a20")
        ttk.Label(
            window,
            text="Подкол будет основан только на публичной переписке и уровне отношений.",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=16, pady=(14, 8))
        people = tk.Listbox(
            window, width=62, height=min(16, max(5, len(cards))),
            bg="#232832", fg="#f2f4f8", selectbackground="#3b82f6",
            borderwidth=0, highlightthickness=0, font=("Segoe UI", 10),
        )
        people.pack(fill="both", expand=True, padx=16)
        for card in cards:
            people.insert("end", f"{card['canonical_name']} — {card['level']}")
        people.selection_set(0)

        def submit():
            selected = people.curselection()
            if not selected:
                return
            card = cards[selected[0]]
            command_id = personas.queue_service_command(
                self.services[platform], f"tease:{card['person_id']}", chat_id
            )
            window.destroy()
            messagebox.showinfo("Команда поставлена", f"Команда №{command_id} передана боту.", parent=self)

        actions = ttk.Frame(window, style="Root.TFrame", padding=16)
        actions.pack(fill="x")
        ttk.Button(actions, text="Отмена", command=window.destroy).pack(side="right")
        ttk.Button(actions, text="Подколоть", command=submit).pack(side="right", padx=(0, 8))
        window.geometry("590x390")

    def refresh_states(self, schedule=True):
        for key, service in self.services.items():
            health, label = service_health(service)
            row = self.rows[key]
            row["enabled"].set(health != "off")
            colors = {"online": "#39c66d", "starting": "#e4ad3a", "reconnecting": "#e4ad3a", "error": "#d9534f", "off": "#d9534f"}
            row["dot"].itemconfigure(row["dot_id"], fill=colors[health])
            row["state"].configure(text=label)
        if schedule:
            self.after(1200, self.refresh_states)


if __name__ == "__main__":
    CharacterControl().mainloop()
