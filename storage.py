import time
from pathlib import Path
from typing import Optional

import aiosqlite
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_group_user
    ON messages(group_id, user_id);

CREATE INDEX IF NOT EXISTS idx_messages_group_time
    ON messages(group_id, timestamp);

CREATE TABLE IF NOT EXISTS personas (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    last_distill_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS group_state (
    group_id TEXT PRIMARY KEY,
    last_message_at REAL,
    last_bot_reply_at REAL,
    last_name_change_at REAL,
    enabled INTEGER DEFAULT 1
);
"""


class GroupFriendStorage:
    def __init__(self):
        data_dir = (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_crowd_persona_distiller"
        )
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(data_dir / "pig.db")
        self._conn: Optional[aiosqlite.Connection] = None

    async def init_db(self):
        logger.info(f"[群友蒸馏] DB path: {self.db_path}")
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        cursor = await self._conn.execute("SELECT COUNT(*) as cnt FROM messages")
        row = await cursor.fetchone()
        cnt = row["cnt"] if row else 0
        logger.info(f"[群友蒸馏] DB init done, messages count: {cnt}")

    async def close(self):
        if self._conn:
            await self._conn.close()

    # ---------- 消息记录 ----------

    async def record_message(
        self, group_id: str, user_id: str, user_name: str, content: str
    ):
        ts = time.time()
        await self._conn.execute(
            "INSERT INTO messages (group_id, user_id, user_name, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (group_id, user_id, user_name, content, ts),
        )
        await self._conn.commit()

    async def get_user_messages(
        self, group_id: str, user_id: str, limit: int = 200
    ) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE group_id = ? AND user_id = ? ORDER BY timestamp ASC LIMIT ?",
            (group_id, user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_message_count(self, group_id: str, user_id: str) -> int:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def get_recent_messages(self, group_id: str, limit: int = 20) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE group_id = ? ORDER BY timestamp DESC LIMIT ?",
            (group_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows][::-1]

    async def list_active_users(
        self, group_id: str, min_messages: int = 0
    ) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT user_id, user_name, COUNT(*) as cnt FROM messages WHERE group_id = ? GROUP BY user_id, user_name HAVING cnt >= ? ORDER BY cnt DESC",
            (group_id, min_messages),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_user_messages(self, group_id: str, user_id: str):
        await self._conn.execute(
            "DELETE FROM messages WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        await self._conn.commit()

    async def list_distillable_users(self, min_messages: int = 0) -> list[dict]:
        """返回所有群的所有用户及蒸馏状态"""
        sql = """SELECT m.group_id, m.user_id, m.user_name,
                        COUNT(*) as message_count, MAX(m.timestamp) as last_msg_at,
                        p.slug, p.last_distill_at
                 FROM messages m
                 LEFT JOIN personas p ON m.group_id = p.group_id AND m.user_id = p.user_id
                 GROUP BY m.group_id, m.user_id
                 HAVING message_count >= ?
                 ORDER BY message_count DESC"""
        logger.info(f"[群友蒸馏] list_distillable SQL: {sql[:80]}... param={min_messages}")
        try:
            cursor = await self._conn.execute(sql, (min_messages,))
        except Exception as e:
            logger.error(f"[群友蒸馏] execute failed: {e}", exc_info=True)
            return []
        rows = await cursor.fetchall()
        logger.info(f"[群友蒸馏] list_distillable fetched {len(rows)} rows")
        results = []
        for row in rows:
            r = dict(row)
            r["distilled"] = bool(r.get("slug"))
            results.append(r)
        return results

    # ---------- 群状态 ----------

    async def update_group_state(
        self,
        group_id: str,
        last_message_at: Optional[float] = None,
        last_bot_reply_at: Optional[float] = None,
        last_name_change_at: Optional[float] = None,
    ):
        await self._conn.execute(
            "INSERT INTO group_state (group_id, last_message_at, last_bot_reply_at, last_name_change_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET "
            "last_message_at = COALESCE(?, last_message_at), "
            "last_bot_reply_at = COALESCE(?, last_bot_reply_at), "
            "last_name_change_at = COALESCE(?, last_name_change_at)",
            (
                group_id,
                last_message_at,
                last_bot_reply_at,
                last_name_change_at,
                last_message_at,
                last_bot_reply_at,
                last_name_change_at,
            ),
        )
        await self._conn.commit()

    async def get_group_state(self, group_id: str) -> Optional[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM group_state WHERE group_id = ?", (group_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ---------- Persona 索引 ----------

    async def create_persona_index(
        self,
        slug: str,
        name: str,
        group_id: str,
        user_id: str,
        message_count: int = 0,
    ):
        await self._conn.execute(
            "INSERT OR REPLACE INTO personas (slug, name, group_id, user_id, message_count) VALUES (?, ?, ?, ?, ?)",
            (slug, name, group_id, user_id, message_count),
        )
        await self._conn.commit()

    async def update_persona_index(
        self, slug: str, message_count: int = 0, last_distill_at: str = ""
    ):
        await self._conn.execute(
            "UPDATE personas SET message_count = ?, last_distill_at = ?, updated_at = datetime('now') WHERE slug = ?",
            (message_count, last_distill_at, slug),
        )
        await self._conn.commit()

    async def get_persona_index(self, slug: str) -> Optional[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM personas WHERE slug = ?", (slug,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_personas_by_group(self, group_id: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM personas WHERE group_id = ?", (group_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_all_personas(self) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM personas ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_persona_index(self, slug: str):
        await self._conn.execute("DELETE FROM personas WHERE slug = ?", (slug,))
        await self._conn.commit()

    async def list_group_persona_user_ids(self, group_id: str) -> list[str]:
        cursor = await self._conn.execute(
            "SELECT user_id FROM personas WHERE group_id = ?", (group_id,)
        )
        rows = await cursor.fetchall()
        return [row["user_id"] for row in rows]
