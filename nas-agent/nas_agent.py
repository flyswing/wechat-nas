"""Archive inbound WeChat files and queue one confirmation reply per message."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


MEMORY_DB = Path(os.getenv("MEMORY_DB", "/app/runtime/memory/wechat_memory.sqlite"))
STATE_DB = Path(os.getenv("ARCHIVE_DB", "/app/runtime/nas-agent/archive.sqlite"))
WECHAT_BASE_DIR = Path(os.getenv("WECHAT_BASE_DIR", "/app/config/xwechat_files"))
ARCHIVE_ROOT = Path(os.getenv("ARCHIVE_ROOT", "/archive"))
ACCOUNT_DIR_NAME = os.getenv("WECHAT_ACCOUNT_DIR_NAME", "").strip()
SELF_USERNAME = ACCOUNT_DIR_NAME.rsplit("_", 1)[0] if "_" in ACCOUNT_DIR_NAME else ACCOUNT_DIR_NAME
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "3"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_component(value: str, fallback: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip(". ")
    return value[:100] or fallback


def xml_value(content: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", content, re.S)
    return match.group(1).strip() if match else ""


def message_month(create_time: int | None) -> str:
    if create_time:
        try:
            return datetime.fromtimestamp(create_time, tz=timezone.utc).strftime("%Y-%m")
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now().strftime("%Y-%m")


def find_downloaded_file(file_name: str, month: str) -> Path | None:
    # The expected location is fast and avoids a broad recursive scan in normal use.
    direct = WECHAT_BASE_DIR / "msg" / "file" / month / file_name
    if direct.is_file():
        return direct

    candidates = [path for path in (WECHAT_BASE_DIR / "msg" / "file").rglob(file_name) if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def init_state(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS archives (
            message_uid TEXT PRIMARY KEY,
            chat_username TEXT NOT NULL,
            chat_display_name TEXT,
            file_name TEXT NOT NULL,
            source_path TEXT NOT NULL,
            archive_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            archived_at TEXT NOT NULL
        )
    """)
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


def unread_file_messages(memory: sqlite3.Connection):
    return memory.execute("""
        SELECT message_uid, chat_username, chat_display_name, create_time, message_content
        FROM messages
        WHERE base_type = 49 AND app_subtype = 6
        ORDER BY create_time ASC
    """)


def archive_message(state: sqlite3.Connection, row: sqlite3.Row) -> bool:
    if state.execute("SELECT 1 FROM archives WHERE message_uid=?", (row["message_uid"],)).fetchone():
        return False

    content = row["message_content"] or ""
    file_name = safe_component(xml_value(content, "title"), "unnamed-file")
    sender = xml_value(content, "fromusername")

    # A file sent by the logged-in account itself must never trigger a confirmation.
    if SELF_USERNAME and sender == SELF_USERNAME:
        return False
    if not sender:
        return False

    source = find_downloaded_file(file_name, message_month(row["create_time"]))
    if not source:
        return False

    # All received files live directly under the configured archive root.
    # A content suffix prevents different files with the same name from
    # overwriting one another in that shared directory.
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    source_hash = sha256(source)
    destination = ARCHIVE_ROOT / file_name
    if destination.exists() and sha256(destination) != source_hash:
        original = Path(file_name)
        destination = ARCHIVE_ROOT / f"{original.stem}__{source_hash[:12]}{original.suffix}"

    if destination.exists() and sha256(destination) == source_hash:
        destination_hash = source_hash
    else:
        partial = destination.with_name(destination.name + ".partial")
        shutil.copy2(source, partial)
        destination_hash = sha256(partial)
        if destination_hash != source_hash:
            partial.unlink(missing_ok=True)
            raise RuntimeError("文件复制后的 SHA-256 校验失败")
        os.replace(partial, destination)

    timestamp = utc_now()
    state.execute("""
        INSERT INTO archives (
            message_uid, chat_username, chat_display_name, file_name,
            source_path, archive_path, sha256, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["message_uid"], row["chat_username"], row["chat_display_name"], file_name,
        str(source), str(destination), destination_hash, timestamp,
    ))
    state.execute("""
        INSERT OR IGNORE INTO reply_outbox (
            message_uid, chat_username, chat_display_name, reply_text,
            archive_path, status, attempts, created_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)
    """, (
        row["message_uid"], row["chat_username"], row["chat_display_name"] or row["chat_username"],
        f"已收到文件《{file_name}》，已成功保存到 NAS。", str(destination), timestamp,
    ))
    state.commit()
    print(f"archived: {destination}", flush=True)
    return True


def main() -> None:
    if not ACCOUNT_DIR_NAME:
        raise RuntimeError("必须设置 WECHAT_ACCOUNT_DIR_NAME")

    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    state = sqlite3.connect(STATE_DB, timeout=30)
    init_state(state)

    while True:
        try:
            if MEMORY_DB.exists():
                memory = sqlite3.connect(f"file:{MEMORY_DB}?mode=ro", uri=True, timeout=10)
                memory.row_factory = sqlite3.Row
                for row in unread_file_messages(memory):
                    archive_message(state, row)
                memory.close()
        except Exception as exc:
            print(f"archive error: {exc}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
