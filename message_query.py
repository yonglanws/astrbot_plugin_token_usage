"""消息统计读取层 —— 与 AstrBot 系统数据库同源。

读取 AstrBot 主数据库 ``data_v4.db`` 的 ``platform_message_history`` 表，
按上海时区（UTC+8）统计用户在群聊/私聊中发送的消息与机器人回复的消息。

- ``created_at`` 以 UTC DATETIME 字符串存储（带微秒），查询时换算为 UTC 边界；
- ``content`` 为 JSON，``$.type`` 区分 ``user``（用户消息）与 ``bot``（机器人消息）；
- 表无 created_at 索引，故各区间均用单次 GROUP BY 聚合，避免重复全表扫描；
- 所有 SQL 均为固定字面量，时间边界通过参数占位符传入。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from astrbot.api import logger
except ImportError:  # 本地预览等非 AstrBot 环境
    import logging

    logger = logging.getLogger("message_stats")

try:
    from .token_query import TZ, _day_bounds_utc, _to_utc_str
except ImportError:  # 非包方式加载时兜底
    from token_query import TZ, _day_bounds_utc, _to_utc_str


@dataclass
class MsgUsage:
    """一个时间桶内的消息数。"""

    date: str  # 桶标识：小时 "YYYY-MM-DD HH" / 天 "YYYY-MM-DD"
    user: int  # 用户消息数
    bot: int  # 机器人消息数
    sessions: int = 0  # 仅累计统计使用：唯一会话数

    @property
    def total(self) -> int:
        return self.user + self.bot


class MessageDB:
    """platform_message_history 只读查询器。失败返回 None，由上层降级。"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)

    # ── 各查询的字面量 SQL 版本（JSON1 优先，instr 降级） ──

    def _rows_hourly(self, start_utc: str, end_utc: str):
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT strftime('%Y-%m-%d %H', created_at, '+8 hours') AS bucket, "
                "SUM(CASE WHEN json_valid(content) AND "
                "json_extract(content, '$.type') = 'user' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN json_valid(content) AND "
                "json_extract(content, '$.type') = 'bot' THEN 1 ELSE 0 END) "
                "FROM platform_message_history "
                "WHERE created_at >= ? AND created_at < ? "
                "GROUP BY bucket ORDER BY bucket",
                (start_utc, end_utc),
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _rows_hourly_fallback(self, start_utc: str, end_utc: str):
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT strftime('%Y-%m-%d %H', created_at, '+8 hours') AS bucket, "
                "SUM(CASE WHEN instr(content, '\"type\": \"user\"') > 0 OR "
                "instr(content, '\"type\":\"user\"') > 0 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN instr(content, '\"type\": \"bot\"') > 0 OR "
                "instr(content, '\"type\":\"bot\"') > 0 THEN 1 ELSE 0 END) "
                "FROM platform_message_history "
                "WHERE created_at >= ? AND created_at < ? "
                "GROUP BY bucket ORDER BY bucket",
                (start_utc, end_utc),
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _rows_daily(self, start_utc: str):
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT strftime('%Y-%m-%d', created_at, '+8 hours') AS bucket, "
                "SUM(CASE WHEN json_valid(content) AND "
                "json_extract(content, '$.type') = 'user' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN json_valid(content) AND "
                "json_extract(content, '$.type') = 'bot' THEN 1 ELSE 0 END) "
                "FROM platform_message_history "
                "WHERE created_at >= ? "
                "GROUP BY bucket ORDER BY bucket",
                (start_utc,),
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _rows_daily_fallback(self, start_utc: str):
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT strftime('%Y-%m-%d', created_at, '+8 hours') AS bucket, "
                "SUM(CASE WHEN instr(content, '\"type\": \"user\"') > 0 OR "
                "instr(content, '\"type\":\"user\"') > 0 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN instr(content, '\"type\": \"bot\"') > 0 OR "
                "instr(content, '\"type\":\"bot\"') > 0 THEN 1 ELSE 0 END) "
                "FROM platform_message_history "
                "WHERE created_at >= ? "
                "GROUP BY bucket ORDER BY bucket",
                (start_utc,),
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _rows_cumulative(self):
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN json_valid(content) AND "
                "json_extract(content, '$.type') = 'user' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN json_valid(content) AND "
                "json_extract(content, '$.type') = 'bot' THEN 1 ELSE 0 END) "
                "FROM platform_message_history"
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _rows_cumulative_fallback(self):
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN instr(content, '\"type\": \"user\"') > 0 OR "
                "instr(content, '\"type\":\"user\"') > 0 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN instr(content, '\"type\": \"bot\"') > 0 OR "
                "instr(content, '\"type\":\"bot\"') > 0 THEN 1 ELSE 0 END) "
                "FROM platform_message_history"
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _rows_sessions(self):
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(DISTINCT user_id) FROM platform_message_history"
            )
            return cur.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _try(primary, fallback, *args):
        """先执行 JSON1 版本，失败自动降级到 instr 版本。"""
        try:
            return primary(*args)
        except sqlite3.Error as e:
            logger.warning(f"[MessageStats] 聚合查询失败，尝试降级: {e}")
        try:
            return fallback(*args)
        except sqlite3.Error as e:
            logger.warning(f"[MessageStats] 降级查询仍失败: {e}")
            return None

    @staticmethod
    def _to_counts(rows) -> dict[str, tuple[int, int]]:
        return {
            row[0]: (int(row[1] or 0), int(row[2] or 0))
            for row in rows
            if row[0] is not None
        }

    # ── 对外接口 ──

    def hourly(self, hours: int = 24) -> list[MsgUsage] | None:
        """最近 N 个整点小时（上海时区，含当前小时），旧 → 新。"""
        now = datetime.now(TZ)
        end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        start = end - timedelta(hours=hours)
        rows = self._try(
            self._rows_hourly,
            self._rows_hourly_fallback,
            _to_utc_str(start),
            _to_utc_str(end),
        )
        if rows is None:
            return None
        counts = self._to_counts(rows)
        result = []
        for i in range(hours):
            key = (start + timedelta(hours=i)).strftime("%Y-%m-%d %H")
            user, bot = counts.get(key, (0, 0))
            # 与折线图统一使用 "YYYY-MM-DD HH:00" 形式的桶标识
            result.append(MsgUsage(key + ":00", user, bot))
        return result

    def daily(self, days: int = 31) -> list[MsgUsage] | None:
        """最近 N 个自然日（上海时区，含今天），旧 → 新。"""
        today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=days - 1)
        start_utc, _ = _day_bounds_utc(start.strftime("%Y-%m-%d"))
        rows = self._try(self._rows_daily, self._rows_daily_fallback, start_utc)
        if rows is None:
            return None
        counts = self._to_counts(rows)
        result = []
        for i in range(days):
            key = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            user, bot = counts.get(key, (0, 0))
            result.append(MsgUsage(key, user, bot))
        return result

    def cumulative(self) -> MsgUsage | None:
        """全历史累计消息数与唯一会话数。"""
        rows = self._try(self._rows_cumulative, self._rows_cumulative_fallback)
        if rows is None:
            return None
        _total, user, bot = rows[0]
        session_rows = self._try(self._rows_sessions, self._rows_sessions)
        sessions = int(session_rows[0][0] or 0) if session_rows else 0
        return MsgUsage("", int(user or 0), int(bot or 0), sessions)
