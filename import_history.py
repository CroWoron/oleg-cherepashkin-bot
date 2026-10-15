import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import bot


def plain_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in value
        )
    return ""


def sender_id(value):
    value = str(value or "unknown")
    if match := re.search(r"(\d+)$", value):
        return int(match.group(1))
    return int.from_bytes(hashlib.blake2b(value.encode(), digest_size=7).digest(), "big")


def target_chat_id(explicit):
    if explicit is not None:
        return explicit
    with bot.connect_db() as connection:
        chats = connection.execute(
            "SELECT chat_id, title FROM chats WHERE chat_type IN ('group', 'supergroup')"
        ).fetchall()
    if len(chats) == 1:
        print(f"Использую найденный чат: {chats[0][1]} ({chats[0][0]})")
        return chats[0][0]
    choices = ", ".join(f"{title}: {chat_id}" for chat_id, title in chats) or "нет"
    raise SystemExit(f"Укажите --chat-id. Найденные группы: {choices}")


def summarize_profile(chat_id, user_id, username, name, newest_id):
    profile = {}
    with bot.connect_db() as connection:
        current = connection.execute(
            "SELECT last_message_id FROM profiles WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        if current and current[0] >= newest_id:
            return
        cursor = connection.execute(
            """
            SELECT text FROM messages
            WHERE chat_id = ? AND user_id = ? AND role = 'user'
            ORDER BY created_at, message_id
            """,
            (chat_id, user_id),
        )
        while rows := cursor.fetchmany(80):
            examples = "\n".join(f"- {row[0]}" for row in rows)
            profile = bot.summarize_profile(profile, examples)
    with bot.connect_db() as connection:
        connection.execute(
            """
            INSERT INTO profiles
                (chat_id, user_id, username, display_name, summary, summarized_count, updated_at,
                 relationship, opinion, topics, speech_style, confidence, last_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                username=excluded.username, display_name=excluded.display_name,
                summary=excluded.summary, summarized_count=excluded.summarized_count,
                updated_at=excluded.updated_at, relationship=excluded.relationship,
                opinion=excluded.opinion, topics=excluded.topics,
                speech_style=excluded.speech_style, confidence=excluded.confidence,
                last_message_id=excluded.last_message_id
            """,
            (chat_id, user_id, username, name, profile.get("facts", ""), newest_id,
             int(time.time()), profile.get("relationship", ""), profile.get("opinion", ""),
             json.dumps(profile.get("topics", []), ensure_ascii=False),
             profile.get("speech_style", ""), profile.get("confidence", "низкая"), newest_id),
        )


def summarize_chat(chat_id, newest_id):
    style = {}
    with bot.connect_db() as connection:
        current = connection.execute(
            "SELECT last_message_id FROM chat_styles WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if current and current[0] >= newest_id:
            return
        cursor = connection.execute(
            """
            SELECT display_name, text FROM messages
            WHERE chat_id = ? AND role = 'user'
            ORDER BY created_at, message_id
            """,
            (chat_id,),
        )
        while rows := cursor.fetchmany(160):
            fragment = "\n".join(f"{name}: {text}" for name, text in rows)
            style = bot.summarize_style(style, fragment)
    with bot.connect_db() as connection:
        connection.execute(
            """
            INSERT INTO chat_styles
                (chat_id, summary, summarized_count, updated_at, norms, topics, last_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                summary=excluded.summary, summarized_count=excluded.summarized_count,
                updated_at=excluded.updated_at, norms=excluded.norms, topics=excluded.topics,
                last_message_id=excluded.last_message_id
            """,
            (chat_id, style.get("style", ""), newest_id, int(time.time()),
             style.get("norms", ""), json.dumps(style.get("topics", []), ensure_ascii=False), newest_id),
        )


def main():
    parser = argparse.ArgumentParser(description="Импорт JSON-истории Telegram Desktop")
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--chat-id", type=int)
    args = parser.parse_args()

    data = json.loads(args.json_file.read_text(encoding="utf-8"))
    bot.init_db()
    chat_id = target_chat_id(args.chat_id)
    imported = 0
    for item in data.get("messages", []):
        text = plain_text(item.get("text")).strip()
        if item.get("type") != "message" or not text or not item.get("from"):
            continue
        message = {
            "chat": {"id": chat_id, "type": "supergroup", "title": data.get("name", "Импорт")},
            "message_id": int(item["id"]),
            "date": int(item.get("date_unixtime", 0)),
            "from": {
                "id": sender_id(item.get("from_id") or item.get("from")),
                "first_name": item["from"],
            },
            "text": text,
        }
        bot.save_chat(message)
        imported += bot.save_message(message)
    print(f"Импортировано новых сообщений: {imported}")

    with bot.connect_db() as connection:
        people = connection.execute(
            """
            SELECT user_id, MAX(username), MAX(display_name), MAX(message_id)
            FROM messages WHERE chat_id = ? AND role = 'user'
            GROUP BY user_id
            """,
            (chat_id,),
        ).fetchall()
        newest_id = connection.execute(
            "SELECT COALESCE(MAX(message_id), 0) FROM messages WHERE chat_id = ? AND role = 'user'", (chat_id,)
        ).fetchone()[0]

    for index, (user_id, username, name, user_newest_id) in enumerate(people, 1):
        print(f"Профиль {index}/{len(people)}: {name}")
        summarize_profile(chat_id, user_id, username, name, user_newest_id)
    if newest_id:
        print("Анализирую общий стиль чата...")
        summarize_chat(chat_id, newest_id)
    bot.prune_messages(chat_id)
    print("Готово.")


if __name__ == "__main__":
    main()
