"""Phone-first readability contract for Monitor Telegram cards."""

from __future__ import annotations

import html
import re

from infrastructure.providers.notifications.telegram import _format_notification_html


def _visible_lines(rendered: str) -> tuple[str, ...]:
    without_expanded_details = re.sub(
        r"<details>.*?</details>",
        "",
        rendered,
        flags=re.DOTALL,
    )
    without_tables = re.sub(r"<table.*?</table>", "", without_expanded_details, flags=re.DOTALL)
    plain = html.unescape(re.sub(r"<[^>]+>", "", without_tables))
    return tuple(line.strip() for line in plain.splitlines() if line.strip())


def test_xau_transition_and_judgment_are_scannable_before_delivery() -> None:
    monitor_name = "XAUUSD方向与GDX/GLD相对强弱金字塔计划监控"
    body = "\n".join(
        (
            monitor_name,
            "标的：XAUUSD",
            "当前价格：4395.940",
            "价格口径：Dukascopy OTC，非 LBMA",
            "价格时间：2026-08-17T02:03:00+00:00",
            "数据来源：dukascopy",
            "上次价格：4372.935",
            "价格变化：+23.005 (+0.53%)",
            "CHANGES",
            "• [INFO] XAU_HOLD_STRUCTURE_4380 · 条件：≥ 4380 · "
            "含义：XAUUSD ≥ 4380：突破结构仍在，继续观察黄金方向 → TRIGGERED",
            "RULES",
            "• 状态：TRIGGERED · 条件：≥ 4380 · "
            "含义：XAUUSD ≥ 4380：突破结构仍在，继续观察黄金方向 · 级别：INFO",
            "• 状态：QUIET · 条件：≥ 4430 · "
            "含义：XAUUSD ≥ 4430：进入顶部复核区 · 级别：INFO",
            "JUDGMENT",
            f"监控：{monitor_name}",
            "阶段：PYRAMID_WAIT · WATCH",
            "结论：HOLD · 数量 0，等待确认",
            "市场：XAUUSD 4395.94 处于 4380 结构支撑与 4430 顶部复核区之间的整理带；"
            "美股 ETF 处于周日夜间/周一盘前延展时段，GDX 90.77（较前收 +2.83%）、"
            "GLD 403.24（+1.07%），GDX/GLD 最新报价比值约 0.2251，"
            "位于 0.220 预警线上方但尚未突破 0.226 重置位；"
            "日线与小时收益窗口未对齐，不构成严格背离确认。",
            "背离：NONE",
            "依据：commodity_spot:OTC:XAUUSD.latest_price, "
            "rule.XAU_HOLD_STRUCTURE_4380.state, "
            "relative_strength.GDX_GLD.return_from_previous_regular_session_close_spread_pct",
            "关注：① XAUUSD ≥ 4430 进入顶部复核观察区；"
            "② XAUUSD 回落至 4290–4310 且 GDX/GLD 守住 0.220 时复核第一档加仓；"
            "③ GDX/GLD 在常规时段突破 0.226 并连续两个时段保持；"
            "④ 周一常规时段开盘后重新对齐日线窗口。",
            "失效：XAUUSD ≤ 4200 且 GDX/GLD 跌破 0.215 时停止新增；"
            "XAUUSD ≤ 4098 时退出金字塔新增仓并复核主 Thesis。",
            "说明：仅更新判断记录；未修改持仓、阶段或订单。",
        )
    )

    rendered = _format_notification_html("🚨 XAUUSD · ≥ 4380 新触发", body)
    visible = _visible_lines(rendered)

    assert rendered.count(monitor_name) == 1
    assert "🟥 <b>新告警</b>" in rendered
    assert "📈 <b>行情</b>" in rendered
    assert "🧭 <b>判断</b>：HOLD · WATCH" in rendered
    assert "<b>下一关注</b>" in rendered
    assert "<summary>更多判断与失效条件</summary>" in rendered
    assert "<b>失效条件</b>" in rendered
    assert "XAUUSD ≥ 4380：突破结构" not in rendered
    assert "commodity_spot:OTC:XAUUSD.latest_price" not in rendered
    assert "状态较上次发生变化" not in rendered
    assert "规则概览" not in rendered
    assert " · INFO" not in rendered
    assert max(map(len, visible)) <= 120
    assert sum(map(len, visible)) <= 900
    assert rendered.index("新告警") < rendered.index("行情") < rendered.index("判断")


def test_standalone_judgment_uses_the_same_readable_structure() -> None:
    monitor_name = "XAUUSD方向与GDX/GLD相对强弱金字塔计划监控"
    body = "\n".join(
        (
            f"监控：{monitor_name}",
            "阶段：PYRAMID_WAIT · WATCH",
            "结论：HOLD · 数量 0，等待确认",
            "市场：XAUUSD 位于 4380 与 4430 之间；延展时段只作初步观察。",
            "背离：NONE",
            "依据：rule.XAU_HOLD_STRUCTURE_4380.state, relative_strength.GDX_GLD.spread",
            "关注：① XAUUSD ≥ 4430 进入顶部复核；② 常规时段重新对齐窗口。",
            "失效：XAUUSD ≤ 4200 时停止新增。",
        )
    )

    rendered = _format_notification_html("🧭 XAUUSD · HOLD", body)
    visible = _visible_lines(rendered)

    assert rendered.count(monitor_name) == 1
    assert "🧭 <b>判断</b>：HOLD · WATCH" in rendered
    assert "<b>市场观察</b>" in rendered
    assert "<b>下一关注</b>" in rendered
    assert "rule.XAU_HOLD_STRUCTURE_4380.state" not in rendered
    assert max(map(len, visible)) <= 120
