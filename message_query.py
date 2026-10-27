"""消息统计读取层 —— 与 AstrBot 后台（dashboard）同源同口径。

读取 AstrBot 主数据库 ``data_v4.db`` 的 ``platform_stats`` 表：
AstrBot 每收到一条消息就把对应「小时 × 平台」的计数 +1（upsert），
dashboard 的累计消息数即 ``SUM(count)``，本插件采用完全相同的口径。

- ``timestamp`` 为**服务器本地时间**的整点时间（历史版本曾写入 Unix 时间戳，
  解析时兼容两种格式）；
- ``platform_id`` 为平台适配器名称（如 aiocqhttp / qq_official）；
- 表规模很小（按小时聚合，通常几千行），一次性读入内存后按窗口聚合。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

try:
    from astrbot.api import logger
except ImportError:  # 本地预览等非 AstrBot 环境
    import logging

    logger = logging.getLogger("message_stats")

try:
    from .token_query import TZ
except ImportError:  # 非包方式加载时兜底
    from token_query import TZ


def _parse_timestamp(value) -> datetime | None:
    """解析 platform_stats.timestamp（本地时间的 ISO 字符串或 Unix 时间戳）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), TZ).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), TZ).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


@dataclass
class MsgUsage:
    """一个时间桶内的消息数（含各平台明细）。"""

    date: str  # 桶标识：小时 "YYYY-MM-DD HH:00" / 天 "YYYY-MM-DD"
    total_count: int
    platforms: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.total_count


class MessageDB:
    """platform_stats 只读查询器。失败返回 None，由上层降级。"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)

    def _load_rows(self) -> list[tuple[datetime, str, int]] | None:
        """读取全部 (本地时间, 平台, 计数) 记录。"""
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT timestamp, platform_id, count FROM platform_stats")
                raw = cur.fetchall()
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.warning(f"[MessageStats] 读取 platform_stats 失败: {e}")
            return None

        rows: list[tuple[datetime, str, int]] = []
        skipped = 0
        for ts, platform_id, count in raw:
            dt = _parse_timestamp(ts)
            if dt is None:
                skipped += 1
                continue
            rows.append((dt, str(platform_id or "unknown"), int(count or 0)))
        if skipped:
            logger.warning(f"[MessageStats] 跳过 {skipped} 条无法解析时间戳的记录")
        return rows

    @staticmethod
    def _bucket(
        rows: list[tuple[datetime, str, int]],
        start: datetime,
        end: datetime,
        key_fmt: str,
        step: timedelta,
        buckets: int,
    ) -> list[MsgUsage]:
        """把行数据按 [start, end) 聚合到 buckets 个时间桶。"""
        index: dict[str, dict[str, int]] = {}
        for dt, platform_id, count in rows:
            if dt < start or dt >= end:
                continue
            key = dt.strftime(key_fmt)
            platforms = index.setdefault(key, {})
            platforms[platform_id] = platforms.get(platform_id, 0) + count

        result = []
        for i in range(buckets):
            key = (start + step * i).strftime(key_fmt)
            platforms = index.get(key, {})
            result.append(MsgUsage(key, sum(platforms.values()), platforms))
        return result

    def hourly(self, hours: int = 24) -> list[MsgUsage] | None:
        """最近 N 个整点小时（含当前小时），旧 → 新。"""
        rows = self._load_rows()
        if rows is None:
            return None
        now = datetime.now(TZ).replace(tzinfo=None)
        end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        start = end - timedelta(hours=hours)
        return self._bucket(
            rows, start, end, "%Y-%m-%d %H:00", timedelta(hours=1), hours
        )

    def daily(self, days: int = 31) -> list[MsgUsage] | None:
        """最近 N 个自然日（含今天），旧 → 新。"""
        rows = self._load_rows()
        if rows is None:
            return None
        today = datetime.now(TZ).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        start = today - timedelta(days=days - 1)
        end = today + timedelta(days=1)
        return self._bucket(rows, start, end, "%Y-%m-%d", timedelta(days=1), days)

    def cumulative(self) -> MsgUsage | None:
        """全历史累计消息数与各平台明细（口径 = dashboard 总消息数）。"""
        rows = self._load_rows()
        if rows is None:
            return None
        platforms: dict[str, int] = {}
        for _dt, platform_id, count in rows:
            platforms[platform_id] = platforms.get(platform_id, 0) + count
        return MsgUsage("", sum(platforms.values()), platforms)
