from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator, Sequence


_CONTEXT_LIMIT = 12_000


class ThreadState:
    def __init__(self, path: Path, app_id: str):
        self.path = Path(path)
        self.app_id = str(app_id)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feishu_thread_topics (
                    app_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    PRIMARY KEY (app_id, chat_id, topic_id)
                );
                CREATE TABLE IF NOT EXISTS feishu_thread_aliases (
                    app_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    alias_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    PRIMARY KEY (app_id, chat_id, alias_id)
                );
                CREATE TABLE IF NOT EXISTS feishu_thread_members (
                    app_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    PRIMARY KEY (app_id, chat_id, topic_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS feishu_thread_context (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    UNIQUE (app_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS feishu_thread_seen (
                    app_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY (app_id, message_id)
                );
                """
            )
            conn.commit()
        os.chmod(self.path, 0o600)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            yield conn

    @staticmethod
    def _ids(values: Sequence[str]) -> tuple[str, ...]:
        return tuple(str(value) for value in values if value)

    def resolve(
        self,
        chat_id: str,
        root_id: str | None = None,
        thread_id: str | None = None,
    ) -> str | None:
        aliases = self._ids((thread_id, root_id))
        if not aliases:
            return None
        placeholders = ",".join("?" for _ in aliases)
        with self._connection() as conn:
            rows = conn.execute(
                f"""SELECT alias_id, topic_id FROM feishu_thread_aliases
                    WHERE app_id = ? AND chat_id = ?
                      AND alias_id IN ({placeholders})""",
                (self.app_id, str(chat_id), *aliases),
            ).fetchall()
        resolved = {alias: topic for alias, topic in rows}
        for alias in aliases:
            if alias in resolved:
                return str(resolved[alias])
        return None

    def activate(
        self,
        chat_id: str,
        canonical_id: str,
        aliases: Sequence[str] = (),
    ) -> None:
        chat_id = str(chat_id)
        canonical_id = str(canonical_id)
        with self._connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO feishu_thread_topics
                   (app_id, chat_id, topic_id) VALUES (?, ?, ?)""",
                (self.app_id, chat_id, canonical_id),
            )
            self._bind(conn, chat_id, canonical_id, (canonical_id, *aliases))
            conn.commit()

    def bind(
        self,
        chat_id: str,
        canonical_id: str,
        aliases: Sequence[str],
    ) -> None:
        with self._connection() as conn:
            self._bind(conn, str(chat_id), str(canonical_id), aliases)
            conn.commit()

    def _bind(
        self,
        conn: sqlite3.Connection,
        chat_id: str,
        canonical_id: str,
        aliases: Sequence[str],
    ) -> None:
        conn.executemany(
            """INSERT INTO feishu_thread_aliases
               (app_id, chat_id, alias_id, topic_id) VALUES (?, ?, ?, ?)
               ON CONFLICT (app_id, chat_id, alias_id)
               DO UPDATE SET topic_id = excluded.topic_id""",
            (
                (self.app_id, chat_id, alias, canonical_id)
                for alias in self._ids(aliases)
            ),
        )

    def is_active(self, chat_id: str, topic: str, user_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT active FROM feishu_thread_members
                   WHERE app_id = ? AND chat_id = ? AND topic_id = ?
                     AND user_id = ?""",
                (self.app_id, str(chat_id), str(topic), str(user_id)),
            ).fetchone()
        return bool(row and row[0])

    def set_active(
        self,
        chat_id: str,
        topic: str,
        user_id: str,
        active: bool,
    ) -> int:
        key = (self.app_id, str(chat_id), str(topic), str(user_id))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT generation FROM feishu_thread_members
                   WHERE app_id = ? AND chat_id = ? AND topic_id = ?
                     AND user_id = ?""",
                key,
            ).fetchone()
            generation = (int(row[0]) if row else 0) + 1
            conn.execute(
                """INSERT INTO feishu_thread_members
                   (app_id, chat_id, topic_id, user_id, active, generation)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (app_id, chat_id, topic_id, user_id)
                   DO UPDATE SET active = excluded.active,
                                 generation = excluded.generation""",
                (*key, int(bool(active)), generation),
            )
            conn.commit()
        return generation

    def generation(self, chat_id: str, topic: str, user_id: str) -> int:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT generation FROM feishu_thread_members
                   WHERE app_id = ? AND chat_id = ? AND topic_id = ?
                     AND user_id = ?""",
                (self.app_id, str(chat_id), str(topic), str(user_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def remember(
        self,
        chat_id: str,
        topic: str,
        message_id: str,
        user_id: str,
        user_name: str,
        text: str,
        timestamp: str = "",
    ) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO feishu_thread_context
                   (app_id, chat_id, topic_id, message_id, user_id,
                    user_name, text, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.app_id,
                    str(chat_id),
                    str(topic),
                    str(message_id),
                    str(user_id),
                    str(user_name),
                    str(text),
                    str(timestamp),
                ),
            )
            conn.commit()
            return cursor.rowcount == 1

    def context(self, chat_id: str, topic: str) -> tuple[str, list[str]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT message_id, user_id, user_name, text, timestamp
                   FROM feishu_thread_context
                   WHERE app_id = ? AND chat_id = ? AND topic_id = ?
                   ORDER BY sequence""",
                (self.app_id, str(chat_id), str(topic)),
            ).fetchall()
        if not rows:
            return "", []

        message_ids = [str(row[0]) for row in rows]
        entries = []
        for _, user_id, user_name, text, timestamp in rows:
            author = user_name or user_id or "未知成员"
            when = f" ({timestamp})" if timestamp else ""
            entries.append(f"[{author}{when}]\n{text}")
        content = "\n\n".join(entries)
        if len(content) > _CONTEXT_LIMIT:
            marker_template = "[旁听背景已截断：省略较早内容约 {count} 个字符。]\n"
            omitted = len(content) - _CONTEXT_LIMIT
            marker = marker_template.format(count=omitted)
            while True:
                kept = max(0, _CONTEXT_LIMIT - len(marker))
                omitted = len(content) - kept
                updated = marker_template.format(count=omitted)
                if updated == marker:
                    break
                marker = updated
            content = marker + content[-(_CONTEXT_LIMIT - len(marker)) :]
        return content, message_ids

    def consume(self, chat_id: str, topic: str, ids: Sequence[str]) -> None:
        message_ids = self._ids(ids)
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        with self._connection() as conn:
            conn.execute(
                f"""DELETE FROM feishu_thread_context
                    WHERE app_id = ? AND chat_id = ? AND topic_id = ?
                      AND message_id IN ({placeholders})""",
                (self.app_id, str(chat_id), str(topic), *message_ids),
            )
            conn.commit()

    def seen(self, message_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT 1 FROM feishu_thread_seen
                   WHERE app_id = ? AND message_id = ?""",
                (self.app_id, str(message_id)),
            ).fetchone()
        return row is not None

    def mark_seen(self, message_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO feishu_thread_seen
                   (app_id, message_id) VALUES (?, ?)""",
                (self.app_id, str(message_id)),
            )
            conn.commit()

    def close(self) -> None:
        return None
