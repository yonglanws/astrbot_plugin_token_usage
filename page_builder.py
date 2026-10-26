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
) -> dict:
    """组装完整页面数据。daily30 为最近 30 天（旧 → 新，含今天）。"""
    now = datetime.now(TZ)
    daily7 = daily30[-7:]

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
        "range7": range7,
        "range30": range30,
        "cumulative": cumulative_block,
    }


def build_fallback_text(
    daily7: list[DayUsage], cumulative: DayUsage | None, reason: str = ""
) -> str:
    """渲染失败时的纯文本降级输出（口径同参考插件）。"""
    today = daily7[-1] if daily7 else None
    lines = ["📊 Token 用量统计（与 AstrBot 系统统计同源）", "━━━━━━━━━━━━━━━━━━━━"]
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
