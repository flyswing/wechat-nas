"""Send queued archive confirmations through the verified WeChat UI controller."""

from __future__ import annotations

import base64
import json
import os
import socket
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(os.getenv("ARCHIVE_DB", "/app/runtime/nas-agent/archive.sqlite"))
SELKIES_CONTAINER = os.getenv("WECHAT_CONTAINER", "wechat-selkies")
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))
ALLOWED = {value.strip() for value in os.getenv("ALLOWED_CHAT_USERNAMES", "").split(",") if value.strip()}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def docker_request(method: str, path: str, body: dict | None = None, parse_json: bool = True):
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    request = (
        f"{method} {path} HTTP/1.1\r\n"
        "Host: docker\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8") + payload

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect("/var/run/docker.sock")
    client.sendall(request)
    response = bytearray()
    while chunk := client.recv(65536):
        response.extend(chunk)
    client.close()

    header, _, content = bytes(response).partition(b"\r\n\r\n")
    status = int(header.split(b"\r\n", 1)[0].split()[1])
    if not 200 <= status < 300:
        raise RuntimeError(f"Docker API HTTP {status}: {content[:300].decode('utf-8', 'replace')}")
    if not parse_json:
        return content.decode("utf-8", "replace")
    return json.loads(content.decode("utf-8")) if content else {}


def docker_exec(command: list[str], require_json_result: bool = True) -> None:
    result = docker_request("POST", f"/containers/{SELKIES_CONTAINER}/exec", {
        # TTY avoids Docker's multiplexed stream header, making the controller's
        # JSON result available for a positive success check below.
        "AttachStdout": True, "AttachStderr": True, "Tty": True, "Cmd": command,
    })
    exec_id = result["Id"]
    output = docker_request(
        "POST", f"/exec/{exec_id}/start", {"Detach": False, "Tty": True}, parse_json=False
    )
    result = docker_request("GET", f"/exec/{exec_id}/json")
    if result.get("Running"):
        raise RuntimeError("微信控制器执行超时")
    if result.get("ExitCode") != 0:
        raise RuntimeError(f"微信控制器退出码：{result.get('ExitCode')}；输出：{output[-1000:]}")

    if not require_json_result:
        return

    # A zero exit code is insufficient: the controller may return {"ok": false}.
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("ok") is True:
            return
        raise RuntimeError(f"微信控制器未完成操作：{json.dumps(payload, ensure_ascii=False)}")
    raise RuntimeError(f"微信控制器未返回成功结果：{output[-1000:]}")


def encode(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def send_reply(chat_name: str, text: str) -> None:
    controller = "/opt/wechat-controller/wechat_controller.py"

    # The controller's open action currently reports success before it has
    # selected a search result. This X11 sequence was verified in Selkies:
    # focus WeChat -> Ctrl+F -> paste name -> Enter.
    docker_exec([
        "sh", "-lc",
        "set -eu; export DISPLAY=:1; "
        "window=$(xdotool search --onlyvisible --name 微信 | head -n 1); "
        "test -n \"$window\"; "
        "xdotool windowactivate --sync \"$window\"; "
        "xdotool windowraise \"$window\"; "
        "printf %s \"$1\" | base64 -d | xclip -selection clipboard; "
        "xdotool key --clearmodifiers ctrl+f; sleep 1; "
        "xdotool key --clearmodifiers ctrl+v; sleep 1; "
        # The Linux WeChat client may report the chat switched before its
        # message editor is ready.  Give the renderer time to settle before
        # asking the controller to validate and paste into that editor.
        "xdotool key Return; sleep 5",
        "sh", encode(chat_name),
    ], require_json_result=False)

    docker_exec([
        "python3", controller, "paste", "--text-b64", encode(text),
    ])
    # Do not rely on paste --send: in some WeChat client states it can paste
    # successfully without committing the message. Submit is an explicit UI action.
    docker_exec([
        "python3", controller, "submit", "--send-delay", "0.8",
    ])


def init_state(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reply_outbox (
            message_uid TEXT PRIMARY KEY,
            chat_username TEXT NOT NULL,
            chat_display_name TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            archive_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            error TEXT
        )
    """)
    conn.commit()


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    init_state(conn)

    while True:
        row = conn.execute("""
            SELECT * FROM reply_outbox WHERE status='pending'
            ORDER BY created_at LIMIT 1
        """).fetchone()
        if not row:
            time.sleep(POLL_SECONDS)
            continue

        conn.execute("UPDATE reply_outbox SET status='sending', attempts=attempts+1 WHERE message_uid=?", (row["message_uid"],))
        conn.commit()
        try:
            if ALLOWED and row["chat_username"] not in ALLOWED:
                raise RuntimeError(f"发送者不在白名单：{row['chat_username']}")
            send_reply(row["chat_display_name"], row["reply_text"])
            conn.execute("UPDATE reply_outbox SET status='sent', sent_at=?, error=NULL WHERE message_uid=?", (now(), row["message_uid"]))
            conn.commit()
            print(json.dumps({"ok": True, "message_uid": row["message_uid"]}, ensure_ascii=False), flush=True)
        except Exception as exc:
            # Never retry automatically: an interrupted UI action may already have sent the message.
            conn.execute("UPDATE reply_outbox SET status='uncertain', error=? WHERE message_uid=?", (str(exc)[:2000], row["message_uid"]))
            conn.commit()
            print(json.dumps({"ok": False, "message_uid": row["message_uid"], "status": "uncertain", "error": str(exc)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
