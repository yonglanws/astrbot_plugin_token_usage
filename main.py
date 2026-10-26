"""astrbot_plugin_token_usage — 精美 Token 用量统计。

功能：
- 读取 AstrBot 主数据库 provider_stats 表（与 dashboard 系统统计同源同口径）
- 一次性生成包含「近 7 天明细 / 近 30 天趋势 / 累计构成」的 HTML 报表图片
- HTML 通过 AstrBot 的 t2i 渲染服务出图（走 AstrBot 配置的远程渲染端点）
- 支持每天定时把报表图片推送到指定群聊（可多个群）

指令（默认仅管理员）：
- /token        查看完整统计报表（同时注册「token统计」「看看token」别名）

Author: 慵懒午睡
License: GPLv3
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain

try:
    from .page_builder import build_fallback_text, build_page_data
    from .token_query import TZ, TokenDB, find_db_path
except ImportError:  # 非包方式加载时兜底
    from page_builder import build_fallback_text, build_page_data
    from token_query import TZ, TokenDB, find_db_path

PLUGIN_NAME = "astrbot_plugin_token_usage"
# 插件内部固定文件（路径仅由 __file__ + 固定文件名推导，不涉及外部输入）
PLUGIN_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = PLUGIN_DIR / "templates" / "stats.html"
STATE_PATH = PLUGIN_DIR / "state.json"

# 定时推送检查间隔（秒）
SCHEDULER_TICK = 20


@register(
    PLUGIN_NAME,
    "慵懒午睡",
    "Token 用量统计：近7天/近30天/累计消耗，HTML 精美报表，支持定时推送群聊",
    "1.0.0",
)
class TokenUsagePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._scheduler_task: asyncio.Task | None = None
        # 上次定时推送日期（上海时区 YYYY-MM-DD），用于防重复发送
        self._last_sent_date: str = self._load_state().get("last_schedule_date", "")

    # ══════════════════════════════════════════════════════════
    #  生命周期
    # ══════════════════════════════════════════════════════════

    async def initialize(self):
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            logger.info("[TokenUsage] 定时推送任务已启动")

    async def terminate(self):
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
            self._scheduler_task = None
            logger.info("[TokenUsage] 定时推送任务已停止")

    # ══════════════════════════════════════════════════════════
    #  状态持久化（仅记录上次定时发送日期）
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _load_state() -> dict:
        try:
            if STATE_PATH.is_file():
                return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[TokenUsage] 读取状态文件失败: {e}")
        return {}

    def _save_state(self):
        try:
            STATE_PATH.write_text(
                json.dumps(
                    {"last_schedule_date": self._last_sent_date},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[TokenUsage] 保存状态文件失败: {e}")

    # ══════════════════════════════════════════════════════════
    #  数据 & 渲染
    # ══════════════════════════════════════════════════════════

    def _read_stats(self):
        """读取近 30 天明细与累计数据。返回 (daily30, cumulative)，失败为 None。"""
        db_path = find_db_path(str(self.config.get("db_path", "") or "").strip())
        if not db_path:
            logger.warning("[TokenUsage] 未找到 AstrBot 主数据库 data_v4.db")
            return None, None
        db = TokenDB(db_path)
        daily30 = db.daily_usage(30)
        if daily30 is None:
            return None, None
        cumulative = db.cumulative()
        if cumulative is None:
            return None, None
        return daily30, cumulative

    @staticmethod
    def _load_template() -> str:
        return TEMPLATE_PATH.read_text(encoding="utf-8")

    async def _render_report(self, daily30, cumulative) -> str:
        """渲染报表图片，返回图片 URL 或本地文件路径。"""
        tmpl = self._load_template()
        data = build_page_data(daily30, cumulative)
        options = {"type": "png", "full_page": True}

        # 优先使用 Star.html_render（内部走 AstrBot 配置的 t2i 渲染服务）
        if hasattr(self, "html_render"):
            return await self.html_render(
                tmpl, data, return_url=False, options=options
            )
        # 兼容旧版核心
        renderer = getattr(self.context, "html_renderer", None)
        if renderer is not None:
            return await renderer.render_custom_template(
                tmpl, data, return_url=False, options=options
            )
        raise RuntimeError("当前 AstrBot 版本不支持 HTML 渲染")

    async def _build_image(self) -> tuple[str | None, str | None, str]:
        """返回 (图片地址, 降级文本, 错误信息)。"""
        daily30, cumulative = self._read_stats()
        if daily30 is None or cumulative is None:
            return (
                None,
                None,
                "❌ 读取 Token 统计失败：未找到 AstrBot 数据库 data_v4.db，"
                "请在插件配置 db_path 中手动指定其绝对路径。",
            )
        try:
            image = await self._render_report(daily30, cumulative)
            return image, None, ""
        except Exception as e:
            logger.error(f"[TokenUsage] 报表图片渲染失败: {e}")
            return (
                None,
                build_fallback_text(daily30[-7:], cumulative, reason=str(e)[:120]),
                "",
            )

    # ══════════════════════════════════════════════════════════
    #  指令
    # ══════════════════════════════════════════════════════════

    def _deny_admin(self, event: AstrMessageEvent) -> bool:
        """是否拒绝本次调用（admin_only 开启且非管理员）。"""
        if not self.config.get("admin_only", True):
            return False
        checker = getattr(event, "is_admin", None)
        if not callable(checker):
            return False
        try:
            return not checker()
        except Exception:
            return False

    async def _handle_token_command(self, event: AstrMessageEvent):
        if self._deny_admin(event):
            yield event.plain_result("这个指令仅管理员可用哦～")
            return

        image, fallback_text, error = await self._build_image()
        if error:
            yield event.plain_result(error)
            return
        if image:
            yield event.image_result(image)
            return
        # 图片渲染失败 → 纯文本降级
        yield event.plain_result(fallback_text or "暂无数据")

    @filter.command("token")
    async def token_stats(self, event: AstrMessageEvent):
        """查看 Token 用量统计（近 7 天 / 近 30 天 / 累计）"""
        async for result in self._handle_token_command(event):
            yield result

    @filter.command("token统计")
    async def token_stats_cn(self, event: AstrMessageEvent):
        """查看 Token 用量统计（别名）"""
        async for result in self._handle_token_command(event):
            yield result

    @filter.command("看看token")
    async def token_stats_alias(self, event: AstrMessageEvent):
        """查看 Token 用量统计（别名，兼容原插件习惯）"""
        async for result in self._handle_token_command(event):
            yield result

    # ══════════════════════════════════════════════════════════
    #  定时推送
    # ══════════════════════════════════════════════════════════

    def _schedule_groups(self) -> list[str]:
        raw = str(self.config.get("schedule_groups", "") or "")
        # 兼容中英文逗号、分号、空白分隔
        for sep in ("，", "；", ";", " ", "\n", "\t"):
            raw = raw.replace(sep, ",")
        groups = []
        for g in raw.split(","):
            g = g.strip()
            if g.isdigit():  # 群号必须是纯数字
                groups.append(g)
        return groups

    def _get_platform_id(self) -> str | None:
        configured = str(self.config.get("schedule_platform_id", "") or "").strip()
        if configured:
            return configured
        try:
            insts = self.context.platform_manager.platform_insts
            if insts:
                return insts[0].meta().id
        except Exception as e:
            logger.warning(f"[TokenUsage] 获取平台 ID 失败: {e}")
        return None

    @staticmethod
    def _build_chain(image: str) -> MessageChain:
        comp = (
            Image.fromURL(image)
            if image.startswith(("http://", "https://"))
            else Image.fromFileSystem(image)
        )
        return MessageChain(chain=[comp])

    async def _scheduler_loop(self):
        while True:
            try:
                await self._check_schedule()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[TokenUsage] 定时推送检查异常: {e}")
            await asyncio.sleep(SCHEDULER_TICK)

    async def _check_schedule(self):
        if not self.config.get("schedule_enabled", False):
            return

        now = datetime.now(TZ)
        target = str(self.config.get("schedule_time", "08:00") or "").strip()
        if now.strftime("%H:%M") != target:
            return
        today = now.strftime("%Y-%m-%d")
        if self._last_sent_date == today:
            return

        groups = self._schedule_groups()
        if not groups:
            logger.warning("[TokenUsage] 已启用定时推送但未配置 schedule_groups，跳过")
            return
        platform_id = self._get_platform_id()
        if not platform_id:
            logger.error("[TokenUsage] 无法获取平台 ID，跳过定时推送")
            return

        image, fallback_text, error = await self._build_image()
        if error:
            logger.error(f"[TokenUsage] 定时推送跳过：{error}")
            return

        sent, failed = 0, 0
        for gid in groups:
            try:
                session = f"{platform_id}:GroupMessage:{gid}"
                if image:
                    ok = await self.context.send_message(
                        session, self._build_chain(image)
                    )
                else:
                    ok = await self.context.send_message(
                        session, MessageChain().message(fallback_text or "暂无数据")
                    )
                if ok:
                    sent += 1
                else:
                    failed += 1
                    logger.warning(
                        f"[TokenUsage] 定时推送到群 {gid} 失败（未找到平台适配器）"
                    )
            except Exception as e:
                failed += 1
                logger.error(f"[TokenUsage] 定时推送到群 {gid} 异常: {e}")

        # 记录日期避免同一分钟反复重试；发送失败的群次日会重新推送
        self._last_sent_date = today
        self._save_state()
        logger.info(
            f"[TokenUsage] 定时推送完成 | 成功 {sent} 个群，失败 {failed} 个群 | {today} {target}"
        )
