import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse

import bot as core


VK_TOKEN = os.getenv("VK_COMMUNITY_TOKEN", "")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0") or 0)
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
core.DB_PATH = Path(os.getenv("VK_DB_PATH", core.DATA_DIR / "vk_memory.sqlite3"))
VK_LONG_POLL_STATE = core.DATA_DIR / "vk_long_poll_state.json"


def curl_executable(platform_name=None):
    return "curl.exe" if (platform_name or os.name) == "nt" else "curl"


def curl_json(url, data="", timeout=45):
    config = [
        f'url = "{url}"',
        "silent",
        "show-error",
        "fail-with-body",
        f"max-time = {timeout}",
        'header = "User-Agent: oleg-vk-bot/1.0"',
    ]
    if data:
        config.extend(['request = "POST"', f'data = "{data}"'])
    try:
        result = subprocess.run(
            [curl_executable(), "--config", "-"],
            input="\n".join(config) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout + 5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"VK network error: {error}") from error
    if result.returncode:
        raise RuntimeError(f"VK network error: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"VK returned invalid JSON: {result.stdout[:200]}") from error


def curl_download(url, path, max_bytes=25_000_000):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(char in url for char in '\r\n"'):
        raise RuntimeError("Invalid media URL")
    result = subprocess.run(
        [curl_executable(), "--config", "-", "--output", str(path)],
        input="\n".join([
            f'url = "{url}"', "location", "silent", "show-error", "fail-with-body",
            "max-time = 45", f"max-filesize = {max_bytes}",
            'header = "User-Agent: oleg-vk-bot/1.0"',
        ]) + "\n",
        capture_output=True, text=True, encoding="utf-8", timeout=50, check=False,
    )
    if result.returncode or not path.is_file() or not path.stat().st_size:
        raise RuntimeError(f"Media download failed: {result.stderr.strip() or result.stdout.strip()}")
    if path.stat().st_size > max_bytes:
        raise RuntimeError("Media file is too large")


def curl_upload(url, field, path):
    if any(char in url for char in '\r\n"'):
        raise RuntimeError("Invalid VK upload URL")
    result = subprocess.run(
        [curl_executable(), "--config", "-", "--form", f"{field}=@{path}"],
        input="\n".join([
            f'url = "{url}"', "silent", "show-error", "fail-with-body", "max-time = 60",
        ]) + "\n",
        capture_output=True, text=True, encoding="utf-8", timeout=65, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"VK media upload failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"VK upload returned invalid JSON: {result.stdout[:200]}") from error


def vk(method, **params):
    payload = urlencode({**params, "access_token": VK_TOKEN, "v": VK_API_VERSION})
    data = curl_json(f"https://api.vk.com/method/{method}", payload)
    if data.get("error"):
        error = data["error"]
        raise RuntimeError(f"VK API {error.get('error_code')}: {error.get('error_msg')}")
    return data["response"]


@lru_cache(maxsize=500)
def user_details(user_id):
    if user_id < 0:
        return {"id": user_id, "first_name": "Сообщество", "is_bot": True}
    users = vk("users.get", user_ids=user_id, fields="screen_name")
    if not users:
        return {"id": user_id, "first_name": f"VK {user_id}"}
    user = users[0]
    return {
        "id": user_id,
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "username": user.get("screen_name"),
    }


@lru_cache(maxsize=100)
def conversation_title(peer_id):
    response = vk("messages.getConversationsById", peer_ids=peer_id)
    items = response.get("items", [])
    if not items:
        return f"VK {peer_id}"
    return items[0].get("chat_settings", {}).get("title") or f"VK {peer_id}"


def clean_text(text):
    return re.sub(r"\[(?:id|club)-?\d+\|([^]]+)]", r"\1", text or "").strip()


def normalize_message(raw, author=None):
    peer_id = int(raw["peer_id"])
    from_id = int(raw.get("from_id", 0))
    lookup_chat_title = author is None
    author = author or user_details(from_id)
    reply = raw.get("reply_message") or {}
    title = f"VK {peer_id}"
    if peer_id >= 2_000_000_000 and lookup_chat_title:
        try:
            title = conversation_title(peer_id)
        except RuntimeError:
            logging.warning("VK chat title unavailable for peer %s", peer_id)
    message = {
        "chat": {
            "id": peer_id,
            "type": "supergroup" if peer_id >= 2_000_000_000 else "private",
            "title": title,
        },
        "message_id": int(raw.get("conversation_message_id") or raw.get("id") or 0),
        "external_message_id": int(raw.get("id") or 0),
        "date": int(raw.get("date") or time.time()),
        "from": {**author, "is_bot": from_id < 0 or author.get("is_bot", False)},
        "text": clean_text(raw.get("text", "")),
    }
    if reply:
        reply_from = int(reply.get("from_id", 0))
        message["reply_to_message"] = {"from": {"id": reply_from}}
    return message


def send_text(peer_id, text, reply_to=None):
    params = {
        "peer_id": peer_id,
        "random_id": random.randint(1, 2_147_483_647),
        "message": text[:4096],
    }
    if reply_to:
        params["reply_to"] = reply_to
    try:
        return vk("messages.send", **params)
    except RuntimeError as error:
        if not reply_to or "VK API 100" not in str(error):
            raise
        logging.warning("VK rejected reply_to=%s; sending without quote", reply_to)
        params.pop("reply_to", None)
        params["random_id"] = random.randint(1, 2_147_483_647)
        return vk("messages.send", **params)


def send_media(peer_id, media_type, url, caption=""):
    suffix = Path(urlparse(url).path).suffix.lower()
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    suffix = suffix if suffix in allowed else (".gif" if media_type == "gif" else ".jpg")
    with tempfile.TemporaryDirectory(prefix="oleg-vk-media-") as directory:
        path = Path(directory) / f"media{suffix}"
        curl_download(url, path)
        if media_type == "gif":
            server = vk("docs.getMessagesUploadServer", type="doc", peer_id=peer_id)
            uploaded = curl_upload(server["upload_url"], "file", path)
            saved = vk("docs.save", file=uploaded["file"], title="мем.gif")
            media = saved.get("doc", saved) if isinstance(saved, dict) else saved[0]
            prefix = "doc"
        else:
            server = vk("photos.getMessagesUploadServer", peer_id=peer_id)
            uploaded = curl_upload(server["upload_url"], "photo", path)
            saved = vk(
                "photos.saveMessagesPhoto", photo=uploaded["photo"],
                server=uploaded["server"], hash=uploaded["hash"],
            )
            media = saved[0]
            prefix = "photo"
    attachment = f"{prefix}{media['owner_id']}_{media['id']}"
    if media.get("access_key"):
        attachment += f"_{media['access_key']}"
    return vk(
        "messages.send", peer_id=peer_id, random_id=random.randint(1, 2_147_483_647),
        message=caption[:4096], attachment=attachment,
    )


def save_answer(message, answer, sent, group_name):
    sent_id = sent if isinstance(sent, int) else sent.get("message_id", 0)
    if not sent_id:
        sent_id = int(time.time() * 1000)
    core.save_message(
        {
            "chat": message["chat"],
            "message_id": sent_id,
            "date": int(time.time()),
            "from": {"id": -VK_GROUP_ID, "first_name": group_name, "is_bot": True},
            "text": answer,
        },
        role="assistant",
        text=answer,
    )
    return sent_id


def handle_message(raw, bot_info, personality):
    message = normalize_message(raw)
    core.log_communication("vk", message, "получено")
    if not message["text"]:
        core.log_communication("vk", message, "пропущено: нет текста")
        return
    if message["from"].get("is_bot"):
        core.log_communication("vk", message, "пропущено: сообщение бота/сообщества")
        return
    chat_id = message["chat"]["id"]
    user_id, username, name = core.author_details(message)
    core.save_chat(message)
    if message["text"].split(maxsplit=1)[0].lower() == "/forgetme":
        core.forget_user(chat_id, user_id)
        answer = "Удалил твои сообщения и профиль из памяти этой беседы."
        send_text(chat_id, answer, message["message_id"])
        core.log_communication("vk", message, "выполнена команда /forgetme", answer)
        return
    if not core.should_consider(message):
        core.log_communication("vk", message, "пропущено: команда, шум или служебная реплика")
        return
    if not core.save_message(message):
        core.log_communication("vk", message, "пропущено: дубликат")
        return
    core.save_person_context_message(
        "vk", user_id, chat_id, message["message_id"], "user", name,
        message["text"], message.get("date"),
    )
    try:
        core.maybe_update_profile(chat_id, user_id, username, name, platform="vk")
        core.maybe_update_chat_style(chat_id)
        core.prune_messages(chat_id)
    except Exception:
        logging.exception("VK memory summary update failed")
    if (
        core.reply_already_covers_message(chat_id, message.get("date"))
        and not core.is_addressed(message, bot_info)
        and not core.personal_chat_request(message["text"])
    ):
        core.log_communication("vk", message, "пропущено: уже покрыто недавним ответом")
        return
    should_reply, sections, mode = core.plan_reply(message, bot_info, chat_id, personality)
    if not should_reply:
        core.log_communication("vk", message, "промолчал: решение планировщика")
        return
    memory = core.memory_context(chat_id, user_id, message["text"], platform="vk")
    system = core.response_system(personality, sections, mode, memory, platform="ВКонтакте")
    history = core.response_history(core.recent_history(chat_id), mode)
    system += core.comeback_instruction(message["text"])
    system += core.capability_instruction(message["text"])
    answer = core.generate_answer(system, history)
    reply_to = message.get("external_message_id") if core.is_addressed(message, bot_info) else None
    sent = send_text(chat_id, answer, reply_to)
    sent_id = save_answer(message, answer, sent, bot_info["first_name"])
    core.save_person_context_message(
        "vk", user_id, chat_id, sent_id, "assistant", "Олег", answer,
    )
    core.save_self_memory("vk", chat_id, sent_id, answer)
    core.log_communication("vk", message, f"ответил, режим={mode}", answer)


def group_info():
    result = vk("groups.getById", group_ids=VK_GROUP_ID)
    groups = result.get("groups", []) if isinstance(result, dict) else result
    if not groups:
        raise RuntimeError("VK community not found for VK_GROUP_ID")
    group = groups[0]
    return {
        "id": -VK_GROUP_ID,
        "username": group.get("screen_name"),
        "first_name": group.get("name", "Олег"),
    }


def load_long_poll_state():
    try:
        state = json.loads(VK_LONG_POLL_STATE.read_text(encoding="utf-8"))
        if int(state.get("group_id", 0)) != VK_GROUP_ID:
            return None
        return {key: state[key] for key in ("server", "key", "ts")}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def load_long_poll_saved_at():
    try:
        checkpoint = json.loads(VK_LONG_POLL_STATE.read_text(encoding="utf-8"))
        if int(checkpoint.get("group_id", 0)) == VK_GROUP_ID:
            return int(checkpoint.get("saved_at", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return int(time.time())


def missed_message_updates(since):
    """Recover the history gap when VK says the old Long Poll position is lost."""
    conversations = vk("messages.getConversations", count=200).get("items", [])
    messages = {}
    for conversation in conversations:
        last = conversation.get("last_message", {})
        peer_id = last.get("peer_id")
        if not peer_id or int(last.get("date", 0)) <= since:
            continue
        offset = 0
        while True:
            try:
                batch = vk("messages.getHistory", peer_id=peer_id, count=200, offset=offset).get("items", [])
            except RuntimeError as error:
                logging.warning("VK history unavailable for peer %s: %s", peer_id, error)
                messages[(peer_id, last.get("id"))] = last
                break
            for message in batch:
                if int(message.get("date", 0)) > since:
                    messages[(peer_id, message.get("id"))] = message
            if len(batch) < 200 or any(int(message.get("date", 0)) <= since for message in batch):
                break
            offset += len(batch)
    for message in sorted(
        messages.values(),
        key=lambda item: (item.get("date", 0), item.get("conversation_message_id", 0)),
    ):
        yield {"type": "message_new", "object": {"message": message}}


def sync_vk_chat(chat_id, bot_info, personality):
    since = core.latest_message_time(chat_id)
    if not since:
        return 0
    history = vk("messages.getHistory", peer_id=chat_id, count=200).get("items", [])
    pending = sorted(
        (message for message in history if int(message.get("date", 0)) > since),
        key=lambda message: (message.get("date", 0), message.get("conversation_message_id", 0)),
    )
    for message in pending:
        handle_message(message, bot_info, personality)
    return len(pending)


def sync_known_vk_chats(bot_info, personality):
    for chat_id, _title, _chat_type, _updated_at in core.known_chats():
        try:
            count = sync_vk_chat(chat_id, bot_info, personality)
            if count:
                logging.info("VK startup sync processed %s messages for peer %s", count, chat_id)
        except RuntimeError as error:
            logging.warning("VK startup sync unavailable for peer %s: %s", chat_id, error)


def handle_vk_commands(bot_info, personality):
    for command_id, action, chat_id in core.pending_control_commands():
        if action not in {"speak", "joke", "reply", "meme"} and not action.startswith("tease:"):
            core.finish_control_command(command_id, "неизвестная команда")
            continue
        try:
            try:
                sync_vk_chat(chat_id, bot_info, personality)
            except RuntimeError as error:
                logging.warning("VK manual sync unavailable for peer %s: %s", chat_id, error)
            source = None
            sent = None
            if action.startswith("tease:"):
                answer, person = core.manual_person_tease(
                    chat_id, personality, int(action.split(":", 1)[1]), "vk"
                )
                sent = send_text(chat_id, answer)
                sent_id = save_answer({"chat": {"id": chat_id}}, answer, sent, bot_info["first_name"])
                core.save_self_memory("vk", chat_id, sent_id, answer)
                core.log_communication(
                    "vk",
                    {"chat": {"id": chat_id}, "from": {"first_name": "Панель"},
                     "date": int(time.time()), "text": f"[подкол: {person['canonical_name']}]"},
                    "ручная команда: подколол", answer,
                )
                core.finish_control_command(command_id, "подкол отправлен")
                continue
            if action == "meme":
                plan = core.manual_media_plan(chat_id, personality)
                candidates = core.search_media(plan["type"], plan["query"]) if plan else []
                answer = plan["caption"] if plan else ""
                mode = "meme"
                for candidate in candidates[:3]:
                    try:
                        sent = send_media(chat_id, plan["type"], candidate, answer)
                        break
                    except (RuntimeError, KeyError, OSError):
                        logging.warning("VK rejected media candidate", exc_info=True)
                if sent is None:
                    action = "joke"
            if action != "meme":
                source, answer, mode = core.manual_chat_answer(
                    chat_id, bot_info, personality, platform="vk", intent=action
                )
            reply_to = source.get("external_message_id") if source else None
            sent = sent if sent is not None else send_text(chat_id, answer, reply_to or None)
            remembered = core.media_memory_text(plan["type"], answer) if mode == "meme" else answer
            sent_id = save_answer(
                source or {"chat": {"id": chat_id}}, remembered, sent, bot_info["first_name"]
            )
            core.save_self_memory("vk", chat_id, sent_id, remembered)
            if source:
                core.save_person_context_message(
                    "vk", source["from"]["id"], chat_id, sent_id,
                    "assistant", "Олег", answer,
                )
            core.log_communication(
                "vk",
                source or {
                    "chat": {"id": chat_id}, "from": {"first_name": "Панель"},
                    "date": int(time.time()), "text": "[команда написать в чат]",
                },
                f"ручная команда: ответил, режим={mode}", answer or "[медиа]",
            )
            core.finish_control_command(command_id, "отправлено")
        except Exception as error:
            logging.exception("VK control command failed")
            core.finish_control_command(command_id, f"ошибка: {error}")


def save_long_poll_state(state):
    VK_LONG_POLL_STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = {**state, "group_id": VK_GROUP_ID, "saved_at": int(time.time())}
    temporary = VK_LONG_POLL_STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(VK_LONG_POLL_STATE)


def clear_long_poll_state():
    try:
        VK_LONG_POLL_STATE.unlink(missing_ok=True)
    except OSError:
        pass


def long_poll():
    state = load_long_poll_state()
    transport_failures = 0
    while True:
        if state is None:
            try:
                state = vk("groups.getLongPollServer", group_id=VK_GROUP_ID)
                save_long_poll_state(state)
            except RuntimeError:
                core.write_service_status("vk", "error", "Long Poll server unavailable")
                logging.exception("VK Long Poll server unavailable; retrying")
                time.sleep(3)
                continue
        try:
            data = curl_json(
                state["server"] + "?" + urlencode(
                    {"act": "a_check", "key": state["key"], "ts": state["ts"], "wait": 25}
                ),
                timeout=35,
            )
            failed = data.get("failed")
            if failed == 1:
                state["ts"] = data["ts"]
                save_long_poll_state(state)
                continue
            if failed == 2:
                previous_ts = state["ts"]
                state = vk("groups.getLongPollServer", group_id=VK_GROUP_ID)
                state["ts"] = previous_ts
                save_long_poll_state(state)
                continue
            if failed == 3:
                yield from missed_message_updates(load_long_poll_saved_at())
                state = vk("groups.getLongPollServer", group_id=VK_GROUP_ID)
                if data.get("ts"):
                    state["ts"] = data["ts"]
                save_long_poll_state(state)
                continue
            if failed == 4:
                state = vk("groups.getLongPollServer", group_id=VK_GROUP_ID)
                if data.get("ts"):
                    state["ts"] = data["ts"]
                save_long_poll_state(state)
                continue
            state["ts"] = data["ts"]
            save_long_poll_state(state)
            transport_failures = 0
            core.write_service_status("vk", "online", f"updates={len(data.get('updates', []))}")
            updates = sorted(
                data.get("updates", []),
                key=lambda update: (
                    update.get("object", {}).get("message", {}).get("date", 0),
                    update.get("object", {}).get("message", {}).get("conversation_message_id", 0),
                ),
            )
            yield from updates or [{"type": "_idle"}]
        except RuntimeError as error:
            transport_failures += 1
            if transport_failures < 3:
                core.write_service_status("vk", "reconnecting", "Long Poll retrying")
                logging.warning("VK Long Poll interrupted; retrying same position: %s", error)
            else:
                core.write_service_status("vk", "error", "Long Poll retrying")
                logging.exception("VK Long Poll repeatedly failed; retrying same position")
            time.sleep(3)
        except KeyError:
            core.write_service_status("vk", "error", "Long Poll reconnecting")
            logging.exception("VK Long Poll state is invalid; reconnecting")
            clear_long_poll_state()
            state = None
            time.sleep(3)


def main():
    if not VK_TOKEN or not VK_GROUP_ID:
        raise SystemExit("Set VK_COMMUNITY_TOKEN and VK_GROUP_ID in .env")
    if not core.DEEPSEEK_API_KEY:
        raise SystemExit("Set DEEPSEEK_API_KEY in .env")
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = RotatingFileHandler(
        core.DATA_DIR / "vk_bot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), log_file],
        force=True,
    )
    core.claim_process(core.DATA_DIR / "vk.pid")
    core.write_service_status("vk", "starting")
    core.init_db("vk")
    personality = core.load_personality()
    bot_info = group_info()
    logging.info("VK bot %s started; memory: %s", bot_info["first_name"], core.DB_PATH)
    sync_known_vk_chats(bot_info, personality)
    for event in long_poll():
        if event.get("type") == "message_new":
            try:
                handle_message(event.get("object", {}).get("message", {}), bot_info, personality)
            except Exception as error:
                logging.exception("VK message handling failed")
                raw = event.get("object", {}).get("message", {})
                fallback = {
                    "chat": {"id": raw.get("peer_id"), "title": f"VK {raw.get('peer_id')}"},
                    "from": {"id": raw.get("from_id"), "first_name": "VK"},
                    "date": raw.get("date"), "text": clean_text(raw.get("text", "")),
                }
                core.log_communication("vk", fallback, f"ошибка обработки: {type(error).__name__}")
        handle_vk_commands(bot_info, personality)


if __name__ == "__main__":
    main()
