import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import persona_manager as personas


class PersonaManagerTest(unittest.TestCase):
    def test_bootstrap_preserves_existing_oleg_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = personas.load_registry(root)
            record = registry["personas"][0]
            self.assertEqual(record["id"], "oleg")
            self.assertEqual(personas.resolve_path(root, record["data_dir"]), root / "data")

    def test_new_persona_has_separate_core_tokens_and_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = personas.load_registry(root)
            record = personas.create_persona(root, registry, "Иван Лесной")
            self.assertTrue(personas.resolve_path(root, record["personality_file"]).is_file())
            self.assertNotEqual(record["data_dir"], "data")
            environment = personas.persona_environment(root, record)
            self.assertEqual(Path(environment["DATA_DIR"]), personas.resolve_path(root, record["data_dir"]))
            personality = json.loads(personas.resolve_path(root, record["personality_file"]).read_text(encoding="utf-8"))
            self.assertEqual(personality["name"], "Иван Лесной")
            personas.validate_personality(personality)

    def test_env_update_keeps_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DEEPSEEK_API_KEY=shared\nTELEGRAM_BOT_TOKEN=old\n", encoding="utf-8")
            personas.update_env(path, {"TELEGRAM_BOT_TOKEN": "new", "VK_GROUP_ID": "42"})
            values = personas.read_env(path)
            self.assertEqual(values["DEEPSEEK_API_KEY"], "shared")
            self.assertEqual(values["TELEGRAM_BOT_TOKEN"], "new")
            self.assertEqual(values["VK_GROUP_ID"], "42")

    def test_questionnaire_merges_without_replacing_written_core(self):
        personality = personas.starter_personality("Олег")
        personality["sections"]["biography"]["content"] = "Богатая написанная вручную биография."
        answers = {
            "pets": {
                "section": "biography", "question": "Какие животные?",
                "choices": ["Необычные"], "custom": "Без кличек",
            }
        }
        merged = personas.merge_questionnaire(personality, answers)
        content = merged["sections"]["biography"]["content"]
        self.assertIn("Богатая написанная вручную биография", content)
        self.assertIn("Необычные; Без кличек", content)
        merged_again = personas.merge_questionnaire(merged, answers)
        self.assertEqual(merged_again["sections"]["biography"]["content"].count("Настройки владельца"), 1)

    def test_chat_catalog_and_control_command_use_persona_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "memory.sqlite3"
            connection = sqlite3.connect(db)
            try:
                connection.execute(
                    "CREATE TABLE chats (chat_id INTEGER, title TEXT, chat_type TEXT, updated_at INTEGER)"
                )
                connection.execute("INSERT INTO chats VALUES (7, 'Компания', 'group', 100)")
                connection.commit()
            finally:
                connection.close()
            services = {"telegram": {"db": db}}
            self.assertEqual(personas.known_service_chats(services)[0][1:4], (7, "Компания", "group"))
            command_id = personas.queue_service_command(services["telegram"], "speak", 7)
            connection = sqlite3.connect(db)
            try:
                command = connection.execute(
                    "SELECT command_id, action, chat_id FROM control_commands"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(command, (command_id, "speak", 7))

    def test_identity_cards_can_be_edited_and_merged_across_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "identities.sqlite3"
            connection = sqlite3.connect(db)
            try:
                connection.executescript(
                    """
                    CREATE TABLE people (person_id INTEGER PRIMARY KEY, canonical_name TEXT,
                        address_names TEXT, updated_at INTEGER);
                    CREATE TABLE identity_links (platform TEXT, external_user_id INTEGER,
                        person_id INTEGER, display_name TEXT, username TEXT, updated_at INTEGER,
                        PRIMARY KEY(platform, external_user_id));
                    CREATE TABLE identity_aliases (platform TEXT, name_key TEXT, person_id INTEGER,
                        display_name TEXT, updated_at INTEGER, PRIMARY KEY(platform, name_key));
                    CREATE TABLE person_memory (person_id INTEGER PRIMARY KEY, facts TEXT,
                        relationship TEXT, opinion TEXT, topics TEXT, speech_style TEXT,
                        confidence TEXT, updated_at INTEGER);
                    CREATE TABLE person_context (person_id INTEGER, platform TEXT, chat_id INTEGER,
                        message_id INTEGER, role TEXT, display_name TEXT, text TEXT, created_at INTEGER,
                        PRIMARY KEY(platform, chat_id, message_id));
                    INSERT INTO people VALUES (1, 'Михаил', '["Миха"]', 1);
                    INSERT INTO people VALUES (2, 'Михаил второй', '["Эль Михо"]', 1);
                    INSERT INTO identity_links VALUES ('telegram', 10, 1, 'Михаил', NULL, 1);
                    INSERT INTO identity_links VALUES ('vk', 20, 2, 'Михаил Сорокин', NULL, 1);
                    INSERT INTO person_memory VALUES (1, 'играет', 'приятели', 'нормальный',
                        '["игры"]', '', 'средняя', 1);
                    INSERT INTO person_memory VALUES (2, 'любит баню', '', '',
                        '["баня"]', '', 'низкая', 1);
                    """
                )
                connection.commit()
            finally:
                connection.close()
            personas.update_identity_card(
                db, 1, "Михаил Сорокин", ["Миха", "Лысый"], "хорошее", "друзья", "свой"
            )
            personas.merge_identity_cards(db, 1, 2)
            cards = personas.list_identity_cards(db)
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["level"], "хорошее")
            self.assertEqual({link["platform"] for link in cards[0]["links"]}, {"telegram", "vk"})
            self.assertIn("Эль Михо", cards[0]["address_names"])
            connection = sqlite3.connect(db)
            try:
                facts = connection.execute(
                    "SELECT facts FROM person_memory WHERE person_id=1"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertIn("любит баню", facts)


if __name__ == "__main__":
    unittest.main()
