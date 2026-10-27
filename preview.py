"""本地设计预览（开发用，不参与插件运行）。

用法：python preview.py
生成 preview.html，并用 Playwright 按 t2i 服务的逻辑（视口宽度取自
meta viewport，full_page 截图）输出 preview.png。

模板通过 jinja2.FileSystemLoader 从固定模板目录加载（非用户输入），
渲染数据全部为脚本内确定性生成的示例值。
"""

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from message_query import MsgUsage
from page_builder import build_message_page_data, build_page_data
from token_query import TZ, DayUsage


def sample_daily(days: int) -> list[DayUsage]:
    """确定性伪随机示例数据（正弦波叠加，无需随机源）。"""
    base = datetime.now(TZ)
    out = []
    for i in range(days - 1, -1, -1):
        d = base - timedelta(days=i)
        # 平滑趋势 + 双周期波动，做出自然的曲线
        volume = (
            1.0
            + 0.45 * math.sin(i / 3.1)
            + 0.30 * math.sin(i / 1.7 + 1.2)
            + 0.15 * math.sin(i / 9.0)
        )
        if d.weekday() >= 5:
            volume *= 0.55
        inp = int(80_000 * volume)
        cached = int(50_000 * volume * (0.6 + 0.5 * abs(math.sin(i / 2.3))))
        output = int(24_000 * volume)
        out.append(DayUsage(d.strftime("%Y-%m-%d"), inp, cached, output))
    return out


def sample_hourly(hours: int = 24) -> list[DayUsage]:
    """确定性伪随机逐小时示例数据（模拟昼夜作息）。"""
    now = datetime.now(TZ)
    end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    out = []
    for i in range(hours):
        hs = end - timedelta(hours=hours - i)
        hour = hs.hour
        # 白天高、深夜低的作息曲线
        activity = 0.15 + 0.85 * max(0.0, math.sin((hour - 5) / 19 * math.pi)) ** 1.4
        wave = 1.0 + 0.28 * math.sin(i / 1.9) + 0.18 * math.sin(i / 4.3 + 0.8)
        vol = activity * wave
        inp = int(9_000 * vol)
        cached = int(5_500 * vol * (0.7 + 0.4 * abs(math.sin(i / 2.7))))
        output = int(2_600 * vol)
        out.append(DayUsage(hs.strftime("%Y-%m-%d %H:00"), inp, cached, output))
    return out


def sample_msgs_hourly(hours: int = 24) -> list:
    """确定性伪随机逐小时消息示例数据（按平台分布）。"""
    now = datetime.now(TZ)
    end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    out = []
    for i in range(hours):
        hs = end - timedelta(hours=hours - i)
        hour = hs.hour
        activity = 0.10 + 0.90 * max(0.0, math.sin((hour - 5) / 19 * math.pi)) ** 1.5
        wave = 1.0 + 0.35 * math.sin(i / 2.4) + 0.22 * math.sin(i / 5.1 + 0.6)
        total = int(2600 * activity * wave)
        qq = int(total * (0.72 + 0.10 * math.sin(i / 3.7)))
        aio = max(total - qq - 2, 0)
        out.append(
            MsgUsage(
                hs.strftime("%Y-%m-%d %H:00"),
                total,
                {"qq_official": qq, "aiocqhttp": aio, "cron": 2},
            )
        )
    return out


def sample_msgs_daily(days: int = 38) -> list:
    """确定性伪随机按天消息示例数据（按平台分布）。"""
    base = datetime.now(TZ)
    out = []
    for i in range(days - 1, -1, -1):
        d = base - timedelta(days=i)
        weekend = d.weekday() >= 5
        volume = (
            1.0
            + 0.40 * math.sin(i / 3.4)
            + 0.26 * math.sin(i / 1.9 + 1.1)
            + 0.14 * math.sin(i / 8.5)
        )
        if weekend:
            volume *= 0.72
        total = int(34_000 * volume)
        qq = int(total * (0.68 + 0.12 * math.sin(i / 2.9)))
        aio = max(int(total * 0.28), 0)
        out.append(
            MsgUsage(
                d.strftime("%Y-%m-%d"),
                total,
                {"qq_official": qq, "aiocqhttp": aio, "cron": max(total - qq - aio, 0)},
            )
        )
    return out


def sample_msgs_cumulative() -> MsgUsage:
    """由示例日数据放大构造累计平台分布。"""
    daily = sample_msgs_daily(38)
    platforms = {}
    for u in daily:
        for pid, count in u.platforms.items():
            platforms[pid] = platforms.get(pid, 0) + count * 18
    # 历史遗留平台，占比很小
    platforms["onebot_dist"] = 2400
    platforms["webchat"] = 900
    return MsgUsage("", sum(platforms.values()), platforms)


def render(env: Environment, template_name: str, data: dict, out_html: Path) -> None:
    html = env.get_template(template_name).render(**data)
    out_html.write_text(html, encoding="utf-8")


def main() -> None:
    daily30 = sample_daily(30)
    hourly24 = sample_hourly(24)
    cumulative = DayUsage(
        "",
        sum(u.input_other for u in daily30) * 26,
        sum(u.input_cached for u in daily30) * 26,
        sum(u.output for u in daily30) * 26,
    )
    data = build_page_data(daily30, cumulative, hourly24)

    msg_data = build_message_page_data(
        sample_msgs_hourly(24),
        sample_msgs_daily(38),
        sample_msgs_cumulative(),
    )

    env = Environment(
        loader=FileSystemLoader(str(HERE / "templates")),
        undefined=StrictUndefined,
        autoescape=True,
    )
    render(env, "stats.html", data, HERE / "preview.html")
    render(env, "messages.html", msg_data, HERE / "preview_msg.html")
    print("preview.html / preview_msg.html written")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 860, "height": 1200})
            for html_name, png_name in (
                ("preview.html", "preview.png"),
                ("preview_msg.html", "preview_msg.png"),
            ):
                page.goto((HERE / html_name).as_uri(), wait_until="networkidle")
                page.wait_for_timeout(600)
                page.screenshot(path=str(HERE / png_name), full_page=True)
            browser.close()
        print("preview.png / preview_msg.png written")
    except Exception as e:
        print(f"playwright screenshot failed: {e}")


if __name__ == "__main__":
    main()
