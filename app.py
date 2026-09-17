import json
import os
import shlex
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_socketio import SocketIO

from github_controller import github_bp, register_github_socket_handlers

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
app.register_blueprint(github_bp)
register_github_socket_handlers(socketio)

BASE_DIR = Path(__file__).resolve().parent
COMMANDS_PATH = BASE_DIR / "commands.json"
load_dotenv(BASE_DIR / ".env")


def load_commands():
    with COMMANDS_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    indicator = str(payload.get("indicator", "!")).strip() or "!"
    commands = payload.get("commands", [])
    normalized = []

    for item in commands:
        name = str(item.get("name", "")).strip().lower()
        aliases = [str(alias).strip().lower() for alias in item.get("aliases", [])]
        response = item.get("response") or item.get("text") or ""
        function_name = str(item.get("function", "")).strip()

        if not name and not aliases:
            continue

        normalized.append(
            {
                "name": name,
                "aliases": aliases,
                "response": response,
                "function": function_name,
            }
        )

    return normalized, indicator


app.config["COMMANDS"], app.config["COMMAND_INDICATOR"] = load_commands()


def refresh_commands_config():
    commands, indicator = load_commands()
    app.config["COMMANDS"] = commands
    app.config["COMMAND_INDICATOR"] = indicator


def is_placeholder_value(value):
    if value is None:
        return True
    normalized = str(value).strip().lower()
    if not normalized:
        return True

    placeholder_fragments = (
        "your_",
        "_here",
        "placeholder",
        "change_me",
        "example",
    )
    return any(fragment in normalized for fragment in placeholder_fragments)


def normalize_text(raw_text, indicator=None):
    if not isinstance(raw_text, str):
        return ""
    text = raw_text.strip()
    active_indicator = indicator or app.config.get("COMMAND_INDICATOR", "!")

    if active_indicator and text.startswith(active_indicator):
        return text[len(active_indicator):].strip()

    if text.startswith("!") or text.startswith("/"):
        return text[1:].strip()

    return text


def find_command_response(message_text):
    normalized = normalize_text(message_text, app.config.get("COMMAND_INDICATOR", "!"))
    if not normalized:
        return None

    for command in app.config["COMMANDS"]:
        all_names = [command["name"], *command["aliases"]]

        for name in all_names:
            if not name:
                continue

            lowered_name = name.lower()
            if normalized.lower() == lowered_name:
                return resolve_command_response(command, lowered_name, normalized)

            prefix = f"{lowered_name} "
            if normalized.lower().startswith(prefix):
                return resolve_command_response(command, lowered_name, normalized)

    return None


def resolve_command_response(command, matched_name, normalized_text):
    function_name = str(command.get("function", "")).strip().lower()
    argument = normalized_text[len(matched_name) :].strip()

    if function_name:
        return dispatch_command_function(function_name, argument)

    response = command["response"]
    if "{arg}" in response:
        if not argument:
            usage = app.config.get("COMMAND_INDICATOR", "!")
            return f"Usage: {usage}{matched_name} <message>"
        return response.format(arg=argument)

    return response


def dispatch_command_function(function_name, argument):
    function_handlers = {
        "sendmessage": handle_send_message,
    }

    handler = function_handlers.get(function_name)
    if not handler:
        return f"Unknown function handler: {function_name}"

    return handler(argument)


def send_groupme_message(text, bot_id=None):
    bot_id = bot_id or os.getenv("GROUPME_BOT_ID")
    if not bot_id:
        raise RuntimeError("GROUPME_BOT_ID is not set.")

    response = requests.post(
        "https://api.groupme.com/v3/bots/post",
        json={"bot_id": bot_id, "text": text},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def load_chat_bot_ids():
    """
    Load named chat targets from environment.

    Supported formats:
    - GROUPME_CHAT_BOT_IDS_JSON='{"chat1":"bot_id_1","chat2":"bot_id_2"}'
    - GROUPME_CHAT_BOT_IDS='chat1=bot_id_1,chat2=bot_id_2'
    """
    mapping = {}

    raw_json = os.getenv("GROUPME_CHAT_BOT_IDS_JSON", "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    name = str(key).strip().lower()
                    bot_id = str(value).strip()
                    if name and bot_id:
                        mapping[name] = bot_id
        except json.JSONDecodeError:
            app.logger.warning("Invalid GROUPME_CHAT_BOT_IDS_JSON format.")

    raw_pairs = os.getenv("GROUPME_CHAT_BOT_IDS", "").strip()
    if raw_pairs:
        for part in raw_pairs.split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            name = key.strip().lower()
            bot_id = value.strip()
            if name and bot_id:
                mapping[name] = bot_id

    return mapping


def parse_send_arguments(argument_text):
    remainder = str(argument_text or "").strip()
    usage = 'Usage: /send "<message>" chat1, chat2, chat3'
    if not remainder:
        return {"error": usage}

    try:
        parts = shlex.split(remainder)
    except ValueError:
        return {"error": usage}

    if len(parts) < 2:
        return {"error": usage}

    message = parts[0].strip()
    if not message:
        return {"error": usage}

    targets_raw = " ".join(parts[1:]).strip()
    if not targets_raw:
        return {"error": usage}

    if "," in targets_raw:
        targets = [item.strip().lower() for item in targets_raw.split(",") if item.strip()]
    else:
        targets = [item.strip().lower().rstrip(",") for item in parts[1:] if item.strip().rstrip(",")]

    deduped_targets = []
    for target in targets:
        if target and target not in deduped_targets:
            deduped_targets.append(target)

    if not deduped_targets:
        return {"error": usage}

    return {"message": message, "targets": deduped_targets}


def handle_send_message(argument_text):
    parsed = parse_send_arguments(argument_text)
    if "error" in parsed:
        return parsed["error"]

    targets_to_bot_ids = load_chat_bot_ids()
    missing = [target for target in parsed["targets"] if target not in targets_to_bot_ids]
    if missing:
        missing_list = ", ".join(missing)
        return (
            f"Unknown chat target(s): {missing_list}. "
            "Configure GROUPME_CHAT_BOT_IDS or GROUPME_CHAT_BOT_IDS_JSON."
        )

    for target in parsed["targets"]:
        send_groupme_message(parsed["message"], bot_id=targets_to_bot_ids[target])

    sent_list = ", ".join(parsed["targets"])
    return f"Sent to {len(parsed['targets'])} chat(s): {sent_list}"


def is_authorized_user(sender_id):
    allowed_user_id = os.getenv("GROUPME_USER_ID")
    if is_placeholder_value(allowed_user_id):
        return True
    return sender_id == allowed_user_id


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200


@app.route("/groupme", methods=["POST"])
def groupme_webhook():
    refresh_commands_config()

    payload = request.get_json(silent=True) or {}
    message_text = payload.get("text", "")
    sender_id = payload.get("sender_id") or payload.get("user_id")
    
    # Debug logging - write to file
    with open("/tmp/groupme_debug.log", "a") as f:
        f.write(f"[WEBHOOK] Received message: '{message_text}' from sender_id={sender_id}\n")
        f.write(f"[WEBHOOK] Full payload: {payload}\n")

    if not message_text:
        return jsonify({"ok": True})

    if not is_authorized_user(sender_id):
        with open("/tmp/groupme_debug.log", "a") as f:
            f.write(f"[WEBHOOK] Unauthorized sender: {sender_id}\n")
        return jsonify({"ok": True})

    try:
        reply = find_command_response(message_text)
    except Exception as exc:  # pragma: no cover - runtime integration
        app.logger.exception("Failed to process command: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500

    if not reply:
        return jsonify({"ok": True})

    try:
        send_groupme_message(reply)
    except Exception as exc:  # pragma: no cover - runtime integration
        app.logger.exception("Failed to send GroupMe response: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
