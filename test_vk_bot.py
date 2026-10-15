import unittest
from unittest.mock import patch

import vk_bot


class VkBotTest(unittest.TestCase):
    def test_curl_command_matches_host_platform(self):
        self.assertEqual(vk_bot.curl_executable("posix"), "curl")
        self.assertEqual(vk_bot.curl_executable("nt"), "curl.exe")

    def test_normalizes_group_message_and_mentions(self):
        message = vk_bot.normalize_message(
            {
                "peer_id": 2_000_000_123,
                "from_id": 7,
                "conversation_message_id": 14,
                "id": 314,
                "date": 100,
                "text": "[club42|Олег], привет",
                "reply_message": {"from_id": -42},
            },
            {"id": 7, "first_name": "Иван", "username": "ivan"},
        )
        self.assertEqual(message["chat"]["type"], "supergroup")
        self.assertEqual(message["text"], "Олег, привет")
        self.assertEqual(message["external_message_id"], 314)
        self.assertEqual(message["reply_to_message"]["from"]["id"], -42)
        self.assertFalse(message["from"]["is_bot"])

    def test_marks_community_message_as_bot(self):
        message = vk_bot.normalize_message(
            {"peer_id": 10, "from_id": -99, "id": 1, "text": "реклама"},
            {"id": -99, "first_name": "Сообщество", "is_bot": True},
        )
        self.assertTrue(message["from"]["is_bot"])
        self.assertEqual(message["chat"]["type"], "private")

    def test_uses_vk_conversation_title_for_live_group_message(self):
        raw = {"peer_id": 2_000_000_123, "from_id": 7, "id": 1, "text": "привет"}
        with patch("vk_bot.user_details", return_value={"id": 7, "first_name": "Иван"}), \
             patch("vk_bot.conversation_title", return_value="Наша беседа"):
            message = vk_bot.normalize_message(raw)
        self.assertEqual(message["chat"]["title"], "Наша беседа")

    def test_vk_token_is_sent_through_stdin_not_process_arguments(self):
        response = type("Response", (), {"returncode": 0, "stdout": '{"response":[]}', "stderr": ""})()
        with patch("vk_bot.subprocess.run", return_value=response) as run:
            vk_bot.curl_json("https://api.vk.com/method/test", "access_token=secret")
        self.assertEqual(run.call_args.args[0], [vk_bot.curl_executable(), "--config", "-"])
        self.assertNotIn("secret", " ".join(run.call_args.args[0]))
        self.assertIn("access_token=secret", run.call_args.kwargs["input"])

    def test_invalid_reply_id_falls_back_to_plain_message(self):
        with patch("vk_bot.vk", side_effect=[RuntimeError("VK API 100: invalid reply_to"), 0]) as call:
            self.assertEqual(vk_bot.send_text(123, "ответ", reply_to=7), 0)
        self.assertEqual(call.call_count, 2)
        self.assertNotIn("reply_to", call.call_args_list[1].kwargs)

    def test_vk_image_is_uploaded_and_sent_as_attachment(self):
        upload_server = {"upload_url": "https://upload.example/photo"}
        uploaded = {"photo": "encoded", "server": 1, "hash": "hash"}
        saved = [{"owner_id": -42, "id": 7, "access_key": "key"}]
        with patch("vk_bot.curl_download"), patch("vk_bot.curl_upload", return_value=uploaded), \
             patch("vk_bot.vk", side_effect=[upload_server, saved, 99]) as call:
            result = vk_bot.send_media(2_000_000_001, "image", "https://img.example/a.jpg", "подпись")
        self.assertEqual(result, 99)
        self.assertEqual(call.call_args_list[-1].args[0], "messages.send")
        self.assertEqual(call.call_args_list[-1].kwargs["attachment"], "photo-42_7_key")
        self.assertEqual(call.call_args_list[-1].kwargs["message"], "подпись")

    def test_vk_gif_is_uploaded_as_document(self):
        upload_server = {"upload_url": "https://upload.example/doc"}
        with patch("vk_bot.curl_download"), \
             patch("vk_bot.curl_upload", return_value={"file": "encoded"}), \
             patch("vk_bot.vk", side_effect=[
                 upload_server, {"type": "doc", "doc": {"owner_id": -42, "id": 8}}, 100,
             ]) as call:
            result = vk_bot.send_media(2_000_000_001, "gif", "https://img.example/a.gif")
        self.assertEqual(result, 100)
        self.assertEqual(call.call_args_list[-1].kwargs["attachment"], "doc-42_8")

    def test_long_poll_recovers_when_server_request_fails(self):
        update = {"type": "message_new", "object": {"message": {"text": "привет"}}}
        server = {"server": "https://lp.example", "key": "key", "ts": "1"}
        with patch("vk_bot.vk", side_effect=[RuntimeError("reset"), server]), \
             patch("vk_bot.load_long_poll_state", return_value=None), \
             patch("vk_bot.save_long_poll_state"), \
             patch("vk_bot.time.sleep"), \
             patch("vk_bot.curl_json", return_value={"ts": "2", "updates": [update]}):
            self.assertEqual(next(vk_bot.long_poll()), update)

    def test_long_poll_transport_retry_keeps_server_position(self):
        update = {"type": "message_new", "object": {"message": {"text": "не потеряй"}}}
        server = {"server": "https://lp.example", "key": "key", "ts": "17"}
        with patch("vk_bot.vk", return_value=server) as get_server, \
             patch("vk_bot.load_long_poll_state", return_value=None), \
             patch("vk_bot.save_long_poll_state"), \
             patch("vk_bot.time.sleep"), \
             patch("vk_bot.core.write_service_status") as status, \
             patch("vk_bot.curl_json", side_effect=[RuntimeError("timeout"), {"ts": "18", "updates": [update]}]):
            self.assertEqual(next(vk_bot.long_poll()), update)
        get_server.assert_called_once()
        self.assertEqual(status.call_args_list[0].args, ("vk", "reconnecting", "Long Poll retrying"))
        self.assertEqual(status.call_args_list[-1].args, ("vk", "online", "updates=1"))

    def test_long_poll_marks_error_only_after_repeated_transport_failures(self):
        update = {"type": "message_new", "object": {"message": {"text": "вернулся"}}}
        saved = {"server": "https://lp.example", "key": "key", "ts": "17"}
        with patch("vk_bot.load_long_poll_state", return_value=saved), \
             patch("vk_bot.save_long_poll_state"), \
             patch("vk_bot.time.sleep"), \
             patch("vk_bot.core.write_service_status") as status, \
             patch("vk_bot.curl_json", side_effect=[
                 RuntimeError("timeout 1"), RuntimeError("timeout 2"),
                 RuntimeError("timeout 3"), {"ts": "18", "updates": [update]},
             ]):
            self.assertEqual(next(vk_bot.long_poll()), update)
        self.assertEqual(status.call_args_list[0].args[1], "reconnecting")
        self.assertEqual(status.call_args_list[1].args[1], "reconnecting")
        self.assertEqual(status.call_args_list[2].args[1], "error")
        self.assertEqual(status.call_args_list[-1].args, ("vk", "online", "updates=1"))

    def test_long_poll_resumes_saved_position_after_restart(self):
        update = {"type": "message_new", "object": {"message": {"text": "пока был выключен"}}}
        saved = {"server": "https://lp.example", "key": "old-key", "ts": "41"}
        with patch("vk_bot.load_long_poll_state", return_value=saved), \
             patch("vk_bot.save_long_poll_state") as save_state, \
             patch("vk_bot.vk") as get_server, \
             patch("vk_bot.curl_json", return_value={"ts": "42", "updates": [update]}):
            self.assertEqual(next(vk_bot.long_poll()), update)
        get_server.assert_not_called()
        self.assertEqual(save_state.call_args.args[0]["ts"], "42")

    def test_long_poll_refreshes_expired_key_without_losing_position(self):
        update = {"type": "message_new", "object": {"message": {
            "date": 100, "conversation_message_id": 8, "text": "накопилось",
        }}}
        saved = {"server": "https://old.example", "key": "old", "ts": "41"}
        refreshed = {"server": "https://new.example", "key": "new", "ts": "999"}
        saved_states = []
        with patch("vk_bot.load_long_poll_state", return_value=saved), \
             patch("vk_bot.save_long_poll_state", side_effect=lambda state: saved_states.append(dict(state))), \
             patch("vk_bot.vk", return_value=refreshed), \
             patch("vk_bot.curl_json", side_effect=[
                 {"failed": 2}, {"ts": "42", "updates": [update]},
             ]):
            self.assertEqual(next(vk_bot.long_poll()), update)
        self.assertEqual(saved_states[0]["ts"], "41")

    def test_long_poll_orders_accumulated_messages_chronologically(self):
        older = {"type": "message_new", "object": {"message": {
            "date": 100, "conversation_message_id": 7,
        }}}
        newer = {"type": "message_new", "object": {"message": {
            "date": 101, "conversation_message_id": 8,
        }}}
        saved = {"server": "https://lp.example", "key": "key", "ts": "41"}
        with patch("vk_bot.load_long_poll_state", return_value=saved), \
             patch("vk_bot.save_long_poll_state"), \
             patch("vk_bot.curl_json", return_value={"ts": "42", "updates": [newer, older]}):
            events = vk_bot.long_poll()
            self.assertEqual(next(events), older)
            self.assertEqual(next(events), newer)

    def test_recovers_missed_history_in_chronological_order(self):
        conversations = {"items": [
            {"last_message": {"peer_id": 20, "date": 120}},
            {"last_message": {"peer_id": 10, "date": 110}},
        ]}
        histories = {
            10: {"items": [{"id": 1, "peer_id": 10, "date": 105, "conversation_message_id": 2}]},
            20: {"items": [
                {"id": 3, "peer_id": 20, "date": 120, "conversation_message_id": 4},
                {"id": 2, "peer_id": 20, "date": 99, "conversation_message_id": 3},
            ]},
        }

        def api(method, **params):
            return conversations if method == "messages.getConversations" else histories[params["peer_id"]]

        with patch("vk_bot.vk", side_effect=api):
            updates = list(vk_bot.missed_message_updates(100))
        messages = [update["object"]["message"] for update in updates]
        self.assertEqual([message["id"] for message in messages], [1, 3])

    def test_manual_sync_processes_only_messages_newer_than_local_memory(self):
        old = {"id": 1, "peer_id": 7, "date": 100, "conversation_message_id": 1}
        new = {"id": 2, "peer_id": 7, "date": 101, "conversation_message_id": 2}
        with patch("vk_bot.core.latest_message_time", return_value=100), \
             patch("vk_bot.vk", return_value={"items": [new, old]}), \
             patch("vk_bot.handle_message") as handle:
            count = vk_bot.sync_vk_chat(7, {"id": -1}, {"sections": {}})
        self.assertEqual(count, 1)
        handle.assert_called_once_with(new, {"id": -1}, {"sections": {}})


if __name__ == "__main__":
    unittest.main()
