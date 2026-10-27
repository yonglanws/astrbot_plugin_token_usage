"""Token 用量读取层 —— 与 AstrBot 系统统计（dashboard）同源。

直接以只读方式打开 AstrBot 主数据库 ``data_v4.db`` 的 ``provider_stats``
表进行聚合，不自行累加，保证「插件报的数」=「AstrBot 系统统计的数」。

统计口径与 dashboard 一致：
- 仅统计 ``agent_type = 'internal'`` 的主代理调用；
- ``created_at`` 以 UTC ISO 字符串存储，按上海时区（UTC+8）的自然日切分。

Token 读取逻辑参考 yuebai5203/astrbot_plugin_msg_stat（GPLv3）的实现。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

try:
    from astrbot.api import logger
except ImportError:  # 本地预览等非 AstrBot 环境
    import logging

    logger = logging.getLogger("token_usage")

# 上海时区（与 AstrBot dashboard 统计口径一致）
TZ = timezone(timedelta(hours=8))

# 与 dashboard 统计口径一致：只统计 internal 主代理
TOKEN_AGENT_TYPE = "internal"


@dataclass
class DayUsage:
    """一天的 Token 用量（均为 token 数）。"""

    date: str  # YYYY-MM-DD（上海时区）
    input_other: int  # 输入（未命中缓存）
    input_cached: int  # 输入（缓存命中）
    output: int  # 输出

    @property
    def total(self) -> int:
        return self.input_other + self.input_cached + self.output

    @property
    def input_all(self) -> int:
        return self.input_other + self.input_cached


def _day_bounds_utc(date_str: str) -> tuple[str, str]:
    """把上海时区的某一天（YYYY-MM-DD）转换成 UTC 的 [start, end) 字符串区间。

    provider_stats.created_at 存的是 UTC ISO 字符串，dashboard 也是按
    created_at >= query_start_utc 过滤的，这里保持一致。
    """
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    start_utc = day.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = (day + timedelta(days=1)).astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return start_utc, end_utc


def _to_utc_str(dt_sh: datetime) -> str:
    """上海时区 datetime → UTC ISO 字符串（与 provider_stats.created_at 同格式）。"""
    return dt_sh.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _validated_db_file(path: str) -> str | None:
    """校验并规范化数据库路径。

    仅接受名为 data_v4.db 的已存在普通文件（realpath 规范化，杜绝路径穿越
    与任意文件读取）；不符合条件返回 None。
    """
    try:
        resolved = os.path.realpath(os.path.abspath(path))
    except (OSError, ValueError):
        return None
    if os.path.basename(resolved) != "data_v4.db":
        return None
    if not os.path.isfile(resolved):
        return None
    return resolved


def find_db_path(configured: str = "") -> str | None:
    """推断 AstrBot 主数据库 data_v4.db 路径。

    优先级：插件配置显式指定 > 标准部署位置推断 > 常见候选。
    插件目录为 <data>/plugins/astrbot_plugin_token_usage/，数据库为
    <data>/data_v4.db，向上三级即可（容器内外通用）。
    所有候选均经过 _validated_db_file 白名单式校验（文件名必须为 data_v4.db）。
    """
    candidates: list[str] = []
    if configured:
        candidates.append(configured)

    here = os.path.abspath(__file__)
    data_dir = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    candidates.append(os.path.join(data_dir, "data_v4.db"))
    # 本地开发 / 非标准布局的兜底候选
    candidates.append(os.path.join(os.path.dirname(data_dir), "data", "data_v4.db"))
    candidates.append(os.path.join(os.path.dirname(here), "data_v4.db"))

    for p in candidates:
        if not p:
            continue
        safe = _validated_db_file(p)
        if safe:
            return safe
    return None


class TokenDB:
    """provider_stats 只读查询器。所有查询失败均返回 None，由上层优雅降级。"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        # 只读 URI 连接，避免锁住主库
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)

    def _sum_where(self, cur: sqlite3.Cursor, where: str, params: tuple) -> DayUsage:
        cur.execute(
            "SELECT COALESCE(SUM(token_input_other),0), "
            "COALESCE(SUM(token_input_cached),0), "
            "COALESCE(SUM(token_output),0) "
            "FROM provider_stats "
            f"WHERE {where}",
            params,
        )
        row = cur.fetchone()
        return DayUsage("", int(row[0]), int(row[1]), int(row[2]))

    def usage_for_day(self, cur: sqlite3.Cursor, date_str: str) -> DayUsage:
        """某一天（上海时区）的 Token 消耗。"""
        start, end = _day_bounds_utc(date_str)
        usage = self._sum_where(
            cur,
            "agent_type = ? AND created_at >= ? AND created_at < ?",
            (TOKEN_AGENT_TYPE, start, end),
        )
        usage.date = date_str
        return usage

    def daily_usage(self, days: int) -> list[DayUsage] | None:
        """最近 N 天的按天明细（上海时区，含今天），旧 → 新。

        单连接内逐日查询，保证与单日查询完全相同的口径。
        """
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                base = datetime.now(TZ)
                result = []
                for i in range(days - 1, -1, -1):
                    date_str = (base - timedelta(days=i)).strftime("%Y-%m-%d")
                    result.append(self.usage_for_day(cur, date_str))
                return result
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[TokenUsage] 查询系统 Token 统计失败: {e}")
            return None

    def cumulative(self) -> DayUsage | None:
        """全历史累计 Token。"""
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                usage = self._sum_where(
                    cur, "agent_type = ?", (TOKEN_AGENT_TYPE,)
                )
                return usage
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[TokenUsage] 查询累计 Token 失败: {e}")
            return None

    def hourly_usage(self, hours: int = 24) -> list[DayUsage] | None:
        """最近 N 个整点小时（上海时区，含当前小时），旧 → 新。

        单个只读连接内逐小时查询，与按天统计同样的过滤口径。
        """
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                now = datetime.now(TZ)
                # 当前小时的下一个整点作为窗口右界，左界往前推 N 小时
                end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                start = end - timedelta(hours=hours)
                result = []
                for i in range(hours):
                    hs = start + timedelta(hours=i)
                    he = hs + timedelta(hours=1)
                    usage = self._sum_where(
                        cur,
                        "agent_type = ? AND created_at >= ? AND created_at < ?",
                        (TOKEN_AGENT_TYPE, _to_utc_str(hs), _to_utc_str(he)),
                    )
                    usage.date = hs.strftime("%Y-%m-%d %H:00")
                    result.append(usage)
                return result
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[TokenUsage] 查询近 {hours} 小时 Token 失败: {e}")
            return None
