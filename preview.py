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

from page_builder import build_page_data
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


def main() -> None:
    daily30 = sample_daily(30)
    cumulative = DayUsage(
        "",
        sum(u.input_other for u in daily30) * 26,
        sum(u.input_cached for u in daily30) * 26,
        sum(u.output for u in daily30) * 26,
    )
    data = build_page_data(daily30, cumulative)

    env = Environment(
        loader=FileSystemLoader(str(HERE / "templates")),
        undefined=StrictUndefined,
        autoescape=True,
    )
    html = env.get_template("stats.html").render(**data)
    (HERE / "preview.html").write_text(html, encoding="utf-8")
    print("preview.html written")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 860, "height": 1200})
            page.goto((HERE / "preview.html").as_uri(), wait_until="networkidle")
            page.wait_for_timeout(600)
            page.screenshot(path=str(HERE / "preview.png"), full_page=True)
            browser.close()
        print("preview.png written")
    except Exception as e:
        print(f"playwright screenshot failed: {e}")


if __name__ == "__main__":
    main()
