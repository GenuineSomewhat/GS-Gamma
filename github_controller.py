import os

import requests
from flask import Blueprint, jsonify, request
from flask_socketio import emit


github_bp = Blueprint("github", __name__)


def fetch_latest_commit(repo_owner=None, repo_name=None):
    owner = (repo_owner or os.getenv("GITHUB_REPO_OWNER") or "").strip()
    name = (repo_name or os.getenv("GITHUB_REPO_NAME") or "").strip()
    if not owner or not name:
        raise RuntimeError("GITHUB_REPO_OWNER and GITHUB_REPO_NAME must be configured.")

    url = f"https://api.github.com/repos/{owner}/{name}/commits?per_page=1"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_API_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, list):
        if not payload:
            raise RuntimeError(f"No commits found for {owner}/{name}.")
        payload = payload[0]

    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API did not return a valid commit payload.")

    return payload


def summarize_commit(payload):
    commit_data = payload.get("commit") or {}
    author = (commit_data.get("author") or {}).get("name") or "unknown"
    message = (commit_data.get("message") or "").split("\n", 1)[0]
    sha = payload.get("sha") or ""
    url = payload.get("html_url") or f"https://github.com/{os.getenv('GITHUB_REPO_OWNER','')}/{os.getenv('GITHUB_REPO_NAME','')}/commit/{sha}"

    return {
        "sha": sha,
        "message": message,
        "author": author,
        "url": url,
    }


def register_github_socket_handlers(socketio):
    @socketio.on("connect")
    def handle_socket_connect():
        try:
            payload = fetch_latest_commit()
            emit("latest_commit", summarize_commit(payload))
        except Exception as exc:  # pragma: no cover - runtime integration
            emit("latest_commit_error", {"error": str(exc)})

    @socketio.on("request_latest_commit")
    def handle_request_latest_commit():
        try:
            payload = fetch_latest_commit()
            emit("latest_commit", summarize_commit(payload))
        except Exception as exc:  # pragma: no cover - runtime integration
            emit("latest_commit_error", {"error": str(exc)})


def emit_latest_commit(socketio):
    try:
        payload = fetch_latest_commit()
        summary = summarize_commit(payload)
        socketio.emit("latest_commit", summary)
        return summary
    except Exception as exc:  # pragma: no cover - runtime integration
        socketio.emit("latest_commit_error", {"error": str(exc)})
        raise


@github_bp.route("/github/latest", methods=["GET"])
def github_latest_commit():
    try:
        payload = fetch_latest_commit()
        return jsonify({"ok": True, "commit": summarize_commit(payload)})
    except Exception as exc:  # pragma: no cover - runtime integration
        return jsonify({"ok": False, "error": str(exc)}), 500


@github_bp.route("/github/webhook", methods=["POST"])
def github_webhook():
    event = request.headers.get("X-GitHub-Event", "")
    payload = request.get_json(silent=True) or {}

    if event and event != "push":
        return jsonify({"ok": True, "event": event})

    try:
        latest = fetch_latest_commit()
        summary = summarize_commit(latest)
        return jsonify({"ok": True, "event": event or "push", "commit": summary})
    except Exception as exc:  # pragma: no cover - runtime integration
        return jsonify({"ok": False, "error": str(exc)}), 500
