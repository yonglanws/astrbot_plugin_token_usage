"""统计页面数据构建：把 TokenDB 的原始数据整理成 Jinja2 模板可直接使用的结构。

所有格式化（千分位、万单位、百分比、横条长度、构成占比）都在 Python
侧完成，模板只做展示，数据全部为 JSON 可序列化的字符串/数字。
"""

from __future__ import annotations

from datetime import datetime, timedelta

try:
    from .token_query import TZ, DayUsage
except ImportError:  # 非包方式加载时兜底
    from token_query import TZ, DayUsage

# 与页面配色一致的三类 Token 颜色（暖纸账簿风）
COLOR_INPUT = "#44598f"   # 输入（未命中缓存）· 靛蓝
COLOR_CACHED = "#3d7a6b"  # 输入（缓存命中）· 松绿
COLOR_OUTPUT = "#b4552f"  # 输出 · 赭红
COLOR_OCHRE = "#a8862f"   # 强调 · 赭金

# 近 24 小时折线图绘图区尺寸（与模板中的 SVG 尺寸保持一致）
LINE_W = 718.0
LINE_H = 180.0
LINE_PAD_TOP = 18.0
LINE_PAD_BOTTOM = 8.0
LINE_PAD_X = 8.0


def fmt(n: int) -> str:
    """千分位分隔。"""
    return f"{n:,}"


def fmt_short(n: int) -> str:
    """紧凑中文单位：1.23万 / 4567。"""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.2f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,}"


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "0.0"
    return f"{part / whole * 100:.1f}"


def _bar_height(value: int, chart_max: int) -> float:
    """柱高百分比（占图表区高度），有值但极低时保底 3%。"""
    if chart_max <= 0 or value <= 0:
        return 0.0
    return max(value / chart_max * 100.0, 3.0)


def _sum(usages: list[DayUsage]) -> DayUsage:
    return DayUsage(
        date="",
        input_other=sum(u.input_other for u in usages),
        input_cached=sum(u.input_cached for u in usages),
        output=sum(u.output for u in usages),
    )


def _frac(value: int, whole: int) -> float:
    """单段占当天总量的百分比（堆叠柱内部分段），有值但极低时保底 1.5%。"""
    if whole <= 0 or value <= 0:
        return 0.0
    return max(value / whole * 100.0, 1.5)


def _line_geometry(usages: list) -> dict:
    """折线图几何数据（SVG 坐标在 Python 侧计算）。

    usages 需提供 date（"YYYY-MM-DD HH:00"）与 total 属性，token / 消息统计共用。
    """
    n = len(usages)
    chart_max = max((u.total for u in usages), default=0)
    baseline = LINE_H - LINE_PAD_BOTTOM
    usable = baseline - LINE_PAD_TOP
    step = (LINE_W - 2 * LINE_PAD_X) / max(1, n - 1)

    points = []
    peak: dict | None = None
    now_pt: dict | None = None
    for i, u in enumerate(usages):
        dt = datetime.strptime(u.date, "%Y-%m-%d %H:00")
        x = round(LINE_PAD_X + i * step, 2)
        ratio = (u.total / chart_max) if chart_max > 0 else 0.0
        y = round(LINE_PAD_TOP + (1 - ratio) * usable, 2)

        anchor, label_x = "middle", x
        if x < 60:
            anchor, label_x = "start", x + 2
        elif x > LINE_W - 60:
            anchor, label_x = "end", x - 2

        p = {
            "x": x,
            "y": y,
            "label": dt.strftime("%H:00"),
            "date": dt.strftime("%m-%d"),
            "total": fmt(u.total),
            "total_short": fmt_short(u.total),
            "is_peak": False,
            "is_now": i == n - 1,
            "show_label": (i % 4 == 0) or i == n - 1,
            "label_anchor": anchor,
            "label_x": label_x,
            "label_y": round(max(y - 9, 11), 2),
            # 末点靠近底部时把「现在」放到点上方，避免与时间轴标签重叠
            "now_label_y": round(y - 12 if y > baseline - 34 else y + 17, 2),
        }
        points.append(p)
        if chart_max > 0 and u.total == chart_max and peak is None:
            p["is_peak"] = True
            peak = p
        if p["is_now"]:
            now_pt = p

    # 峰值与当前小时重合时只显示峰值标签，避免文字重叠
    if peak is not None and now_pt is not None and peak is now_pt:
        now_pt = None

    line = " ".join(f"{p['x']},{p['y']}" for p in points)
    area = (
        f"{LINE_PAD_X:.2f},{baseline:.2f} "
        + line
        + f" {LINE_W - LINE_PAD_X:.2f},{baseline:.2f}"
    )
    grid = [round(LINE_PAD_TOP + usable * r, 2) for r in (0.33, 0.66)]

    return {
        "points": points,
        "line": line,
        "area": area,
        "grid": grid,
        "baseline": round(baseline, 2),
        "has_data": chart_max > 0,
        "peak": peak,
        "now": (
            {
                "label_x": now_pt["label_x"],
                "label_y": now_pt["now_label_y"],
                "anchor": now_pt["label_anchor"],
            }
            if now_pt is not None
            else None
        ),
        "_chart_max": chart_max,
    }


def _hour_block(usages: list[DayUsage]) -> dict:
    """近 24 小时折线图区块（Token 版，含输入/缓存/输出明细）。"""
    geo = _line_geometry(usages)
    chart_max = geo.pop("_chart_max")
    total = _sum(usages)
    n = len(usages)
    geo.update(
        {
            "sum": {
                "input": fmt(total.input_other),
                "cached": fmt(total.input_cached),
                "output": fmt(total.output),
                "total": fmt(total.total),
                "total_short": fmt_short(total.total),
            },
            "avg_hour": fmt(round(total.total / n)) if n else "0",
            "avg_hour_short": fmt_short(round(total.total / n)) if n else "0",
            "peak_total": fmt(chart_max) if chart_max > 0 else "0",
            "peak_hour": geo["peak"]["label"] if geo["peak"] else "—",
            "active_hours": sum(1 for u in usages if u.total > 0),
            "cached_hit_pct": _pct(total.input_cached, total.input_all)
            if total.input_all > 0
            else "0.0",
        }
    )
    return geo



def _range_block(title: str, subtitle: str, usages: list[DayUsage], label_step: int = 1) -> dict:
    """构建一个时间区间板块（近7天 / 近30天）的数据。"""
    n = len(usages)
    total = _sum(usages)
    chart_max = max((u.total for u in usages), default=0)
    max_day = max(usages, key=lambda u: u.total) if usages else None
    active_days = sum(1 for u in usages if u.total > 0)

    days = []
    for idx, u in enumerate(usages):
        d = datetime.strptime(u.date, "%Y-%m-%d")
        is_last = idx == n - 1
        days.append(
            {
                "date_short": u.date[5:],  # MM-DD
                "date_full": u.date,
                "weekday": "一二三四五六日"[d.weekday()],
                "total": fmt(u.total),
                "total_short": fmt_short(u.total),
                "input": fmt(u.input_other),
                "cached": fmt(u.input_cached),
                "output": fmt(u.output),
                "bar_h": round(_bar_height(u.total, chart_max), 2),
                "frac_in": round(_frac(u.input_other, u.total), 2),
                "frac_cached": round(_frac(u.input_cached, u.total), 2),
                "frac_out": round(_frac(u.output, u.total), 2),
                "is_today": is_last,
                "is_max": max_day is not None and u.date == max_day.date and u.total > 0,
                # 30 天图太密，按步长抽样日期标签，最后一天始终显示
                "show_label": (idx % label_step == (n - 1) % label_step) or is_last,
            }
        )

    cached_hit = _pct(total.input_cached, total.input_all) if total.input_all > 0 else "0.0"
    return {
        "title": title,
        "subtitle": subtitle,
        "days": days,
        "sum": {
            "input": fmt(total.input_other),
            "cached": fmt(total.input_cached),
            "output": fmt(total.output),
            "total": fmt(total.total),
            "total_short": fmt_short(total.total),
        },
        "avg_total": fmt(round(total.total / n)) if n else "0",
        "avg_total_short": fmt_short(round(total.total / n)) if n else "0",
        "peak_total": fmt(max_day.total) if max_day else "0",
        "peak_date": max_day.date if max_day else "",
        "active_days": active_days,
        "cached_hit_pct": cached_hit,
        "breakdown": [
            {"name": "输入", "value": fmt(total.input_other), "pct": _pct(total.input_other, total.total), "color": COLOR_INPUT},
            {"name": "缓存命中", "value": fmt(total.input_cached), "pct": _pct(total.input_cached, total.total), "color": COLOR_CACHED},
            {"name": "输出", "value": fmt(total.output), "pct": _pct(total.output, total.total), "color": COLOR_OUTPUT},
        ],
    }


def _kpi_card(label: str, usage: DayUsage, date_text: str, accent: str, sub: str) -> dict:
    return {
        "label": label,
        "date": date_text,
        "accent": accent,
        "sub": sub,
        "trend_dir": "",
        "total": fmt(usage.total),
        "total_short": fmt_short(usage.total),
        "input": fmt(usage.input_other),
        "cached": fmt(usage.input_cached),
        "output": fmt(usage.output),
    }


def build_page_data(
    daily30: list[DayUsage],
    cumulative: DayUsage,
    hourly24: list[DayUsage] | None = None,
) -> dict:
    """组装完整页面数据。

    daily30 为最近 30 天（旧 → 新，含今天）；hourly24 为最近 24 个整点小时。
    """
    now = datetime.now(TZ)
    daily7 = daily30[-7:]

    range24 = _hour_block(hourly24 or [])
    range7 = _range_block("近 7 天消耗", "每日明细 · 输入 / 缓存 / 输出", daily7)
    range30 = _range_block("近 30 天趋势", "每日总消耗走势", daily30, label_step=5)

    sum7 = _sum(daily7)
    today = daily7[-1]
    yesterday = daily7[-2] if len(daily7) >= 2 else DayUsage("", 0, 0, 0)

    # 近 7 天环比再前 7 天
    trend_sub = f"日均 {fmt_short(round(sum7.total / 7))}"
    trend_dir = ""
    if len(daily30) >= 14:
        prev7 = _sum(daily30[-14:-7])
        if prev7.total > 0:
            delta = (sum7.total - prev7.total) / prev7.total * 100
            arrow = "▲" if delta >= 0 else "▼"
            trend_sub = f"{arrow} {abs(delta):.1f}% 环比前7天"
            trend_dir = "up" if delta >= 0 else "down"

    kpis = [
        _kpi_card("今日消耗", today, now.strftime("%m-%d"), COLOR_INPUT, f"输入 {fmt_short(today.input_all)} · 输出 {fmt_short(today.output)}"),
        _kpi_card("昨日消耗", yesterday, (now - timedelta(days=1)).strftime("%m-%d"), COLOR_CACHED, f"输入 {fmt_short(yesterday.input_all)} · 输出 {fmt_short(yesterday.output)}"),
        _kpi_card("近 7 天", sum7, "7 天合计", COLOR_OCHRE, trend_sub),
        _kpi_card("累计消耗", cumulative, "全部历史", COLOR_OUTPUT, "详见下方累计构成"),
    ]
    kpis[2]["trend_dir"] = trend_dir

    cumulative_block = {
        "total": fmt(cumulative.total),
        "total_short": fmt_short(cumulative.total),
        "input": fmt(cumulative.input_other),
        "cached": fmt(cumulative.input_cached),
        "output": fmt(cumulative.output),
        "cached_hit_pct": _pct(cumulative.input_cached, cumulative.input_all),
        "breakdown": [
            {"name": "输入", "desc": "未命中缓存", "value": fmt(cumulative.input_other), "pct": _pct(cumulative.input_other, cumulative.total), "color": COLOR_INPUT},
            {"name": "缓存命中", "desc": "命中前缀缓存", "value": fmt(cumulative.input_cached), "pct": _pct(cumulative.input_cached, cumulative.total), "color": COLOR_CACHED},
            {"name": "输出", "desc": "模型生成", "value": fmt(cumulative.output), "pct": _pct(cumulative.output, cumulative.total), "color": COLOR_OUTPUT},
        ],
    }

    return {
        "report_no": now.strftime("%Y%m%d-%H%M"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "generated_weekday": "周" + "一二三四五六日"[now.weekday()],
        "kpis": kpis,
        "range24": range24,
        "range7": range7,
        "range30": range30,
        "cumulative": cumulative_block,
    }


def build_fallback_text(
    daily7: list[DayUsage],
    cumulative: DayUsage | None,
    reason: str = "",
    hourly24: list[DayUsage] | None = None,
) -> str:
    """渲染失败时的纯文本降级输出（口径同参考插件）。"""
    today = daily7[-1] if daily7 else None
    lines = ["📊 Token 用量统计（与 AstrBot 系统统计同源）", "━━━━━━━━━━━━━━━━━━━━"]
    if hourly24:
        h_total = _sum(hourly24)
        h_peak = max(hourly24, key=lambda u: u.total)
        lines += [
            "【近 24 小时】",
            f"  合计：{fmt(h_total.total)}  峰值：{fmt(h_peak.total)}（{h_peak.date[11:]}）",
            f"  输入：{fmt(h_total.input_other)}  缓存：{fmt(h_total.input_cached)}  输出：{fmt(h_total.output)}",
        ]
    if today:
        lines += [
            f"【今日 {today.date}】",
            f"  输入：{fmt(today.input_other)}  缓存：{fmt(today.input_cached)}  输出：{fmt(today.output)}",
            f"  合计：{fmt(today.total)}",
        ]
    lines.append("【近 7 天】")
    for u in daily7:
        lines.append(f"  {u.date}  {fmt(u.total)}（输入 {fmt(u.input_other)} / 缓存 {fmt(u.input_cached)} / 输出 {fmt(u.output)}）")
    if cumulative is not None:
        lines += [
            "【累计总量】",
            f"  输入：{fmt(cumulative.input_other)}  缓存：{fmt(cumulative.input_cached)}  输出：{fmt(cumulative.output)}",
            f"  合计：{fmt(cumulative.total)}",
        ]
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if reason:
        lines.append(f"（图片渲染失败已降级为文本：{reason}）")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  消息统计页面数据
# ══════════════════════════════════════════════════════════════

COLOR_USER = COLOR_INPUT  # 用户消息 · 靛蓝
COLOR_BOT = COLOR_OUTPUT  # 机器人消息 · 赭红


def _msg_hour_block(usages: list) -> dict:
    """近 24 小时消息折线图区块（用户 / 机器人合计）。"""
    geo = _line_geometry(usages)
    chart_max = geo.pop("_chart_max")
    n = len(usages)
    total_user = sum(u.user for u in usages)
    total_bot = sum(u.bot for u in usages)
    total = total_user + total_bot
    geo.update(
        {
            "sum": {
                "user": fmt(total_user),
                "bot": fmt(total_bot),
                "total": fmt(total),
                "total_short": fmt_short(total),
            },
            "avg_hour": fmt(round(total / n)) if n else "0",
            "peak_total": fmt(chart_max) if chart_max > 0 else "0",
            "peak_hour": geo["peak"]["label"] if geo["peak"] else "—",
            "active_hours": sum(1 for u in usages if u.total > 0),
            "user_pct": _pct(total_user, total) if total > 0 else "0.0",
        }
    )
    return geo


def _msg_range_block(usages: list, label_step: int = 1) -> dict:
    """消息统计的按天区块（近 7 天 / 近 31 天），条形按用户 / 机器人分两段。"""
    n = len(usages)
    total_user = sum(u.user for u in usages)
    total_bot = sum(u.bot for u in usages)
    total = total_user + total_bot
    chart_max = max((u.total for u in usages), default=0)
    max_day = max(usages, key=lambda u: u.total) if usages else None
    active_days = sum(1 for u in usages if u.total > 0)

    days = []
    for idx, u in enumerate(usages):
        d = datetime.strptime(u.date, "%Y-%m-%d")
        is_last = idx == n - 1
        days.append(
            {
                "date_short": u.date[5:],
                "date_full": u.date,
                "weekday": "一二三四五六日"[d.weekday()],
                "total": fmt(u.total),
                "total_short": fmt_short(u.total),
                "user": fmt(u.user),
                "bot": fmt(u.bot),
                "bar_h": round(_bar_height(u.total, chart_max), 2),
                "frac_user": round(_frac(u.user, u.total), 2),
                "frac_bot": round(_frac(u.bot, u.total), 2),
                "is_today": is_last,
                "is_max": max_day is not None and u.date == max_day.date and u.total > 0,
                "show_label": (idx % label_step == (n - 1) % label_step) or is_last,
            }
        )

    return {
        "days": days,
        "sum": {
            "user": fmt(total_user),
            "bot": fmt(total_bot),
            "total": fmt(total),
            "total_short": fmt_short(total),
        },
        "avg_total": fmt(round(total / n)) if n else "0",
        "avg_total_short": fmt_short(round(total / n)) if n else "0",
        "peak_total": fmt(max_day.total) if max_day else "0",
        "peak_date": max_day.date if max_day else "",
        "active_days": active_days,
        "user_pct": _pct(total_user, total) if total > 0 else "0.0",
    }


def _msg_kpi(label: str, total: int, date_text: str, accent: str, sub: str, trend_dir: str = "") -> dict:
    return {
        "label": label,
        "date": date_text,
        "accent": accent,
        "sub": sub,
        "trend_dir": trend_dir,
        "total": fmt(total),
        "total_short": fmt_short(total),
    }


def build_message_page_data(
    hourly24: list,
    daily38: list,
    cumulative,
) -> dict:
    """组装消息统计页面数据。

    hourly24 为最近 24 个整点小时；daily38 为最近 38 个自然日（含今天），
    最近 31 天用于趋势图，最近 14 天用于 7 天环比。
    """
    now = datetime.now(TZ)
    hourly24 = hourly24 or []
    daily38 = daily38 or []
    daily7 = daily38[-7:]
    daily31 = daily38[-31:]

    range24 = _msg_hour_block(hourly24)
    range7 = _msg_range_block(daily7)
    range31 = _msg_range_block(daily31, label_step=5)

    h24_total = sum(u.total for u in hourly24)
    h24_user = sum(u.user for u in hourly24)
    h24_bot = sum(u.bot for u in hourly24)
    d7_total = sum(u.total for u in daily7)
    d7_user = sum(u.user for u in daily7)
    d7_bot = sum(u.bot for u in daily7)
    d31_total = sum(u.total for u in daily31)

    # 近 7 天环比再前 7 天
    trend_sub = f"日均 {fmt_short(round(d7_total / 7))}" if daily7 else "日均 0"
    trend_dir = ""
    if len(daily38) >= 14:
        prev7 = sum(u.total for u in daily38[-14:-7])
        if prev7 > 0:
            delta = (d7_total - prev7) / prev7 * 100
            arrow = "▲" if delta >= 0 else "▼"
            trend_sub = f"{arrow} {abs(delta):.1f}% 环比前7天"
            trend_dir = "up" if delta >= 0 else "down"

    kpis = [
        _msg_kpi(
            "近 24 小时",
            h24_total,
            now.strftime("%m-%d"),
            COLOR_USER,
            f"用户 {fmt_short(h24_user)} · 机器人 {fmt_short(h24_bot)}",
        ),
        _msg_kpi("近 7 天", d7_total, "7 天合计", COLOR_CACHED, trend_sub, trend_dir),
        _msg_kpi(
            "近 31 天",
            d31_total,
            "31 天合计",
            COLOR_OCHRE,
            f"日均 {fmt_short(round(d31_total / 31))}",
        ),
        _msg_kpi(
            "累计消息",
            cumulative.total,
            "全部历史",
            COLOR_OUTPUT,
            f"会话 {fmt(cumulative.sessions)} 个",
        ),
    ]

    total = cumulative.total
    cumulative_block = {
        "total": fmt(total),
        "total_short": fmt_short(total),
        "user": fmt(cumulative.user),
        "bot": fmt(cumulative.bot),
        "user_pct": _pct(cumulative.user, total),
        "bot_pct": _pct(cumulative.bot, total),
        "sessions": fmt(cumulative.sessions),
        "breakdown": [
            {
                "name": "用户消息",
                "desc": "群友 / 私聊",
                "value": fmt(cumulative.user),
                "pct": _pct(cumulative.user, total),
                "color": COLOR_USER,
            },
            {
                "name": "机器人消息",
                "desc": "AI 回复与主动消息",
                "value": fmt(cumulative.bot),
                "pct": _pct(cumulative.bot, total),
                "color": COLOR_BOT,
            },
            {
                "name": "会话数",
                "desc": "群聊 + 私聊（去重）",
                "value": fmt(cumulative.sessions),
                "pct": "",
                "color": "#7c7364",
            },
        ],
    }

    return {
        "report_no": now.strftime("%Y%m%d-%H%M"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "generated_weekday": "周" + "一二三四五六日"[now.weekday()],
        "kpis": kpis,
        "range24": range24,
        "range7": range7,
        "range31": range31,
        "cumulative": cumulative_block,
    }


def build_message_fallback_text(
    hourly24: list,
    daily7: list,
    cumulative,
    reason: str = "",
) -> str:
    """消息统计渲染失败时的纯文本降级输出。"""
    lines = ["💬 消息统计（与 AstrBot 系统数据库同源）", "━━━━━━━━━━━━━━━━━━━━"]
    if hourly24:
        total = sum(u.total for u in hourly24)
        peak = max(hourly24, key=lambda u: u.total)
        lines += [
            "【近 24 小时】",
            f"  合计：{fmt(total)} 条  峰值：{fmt(peak.total)} 条（{peak.date[11:]}）",
        ]
    if daily7:
        total7 = sum(u.total for u in daily7)
        lines += [
            "【近 7 天】",
            f"  合计：{fmt(total7)} 条  日均：{fmt(round(total7 / 7))} 条",
        ]
        for u in daily7:
            lines.append(f"  {u.date}  {fmt(u.total)}（用户 {fmt(u.user)} / 机器人 {fmt(u.bot)}）")
    if cumulative is not None:
        lines += [
            "【累计】",
            f"  合计：{fmt(cumulative.total)} 条  会话：{fmt(cumulative.sessions)} 个",
            f"  用户 {fmt(cumulative.user)} 条 / 机器人 {fmt(cumulative.bot)} 条",
        ]
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if reason:
        lines.append(f"（图片渲染失败已降级为文本：{reason}）")
    return "\n".join(lines)
