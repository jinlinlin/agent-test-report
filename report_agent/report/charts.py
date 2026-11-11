"""本地图表生成（matplotlib）：把 StatsResult 契约数据直接渲染为 PNG。

原则：
- 与报告表格同源：图中数值只取契约字段，不做任何二次计算/汇总估算。
- 永不阻断报告：matplotlib 缺失、字体异常、绘图异常等全部捕获，
  单图失败只跳过该图，渲染层对缺失键回退【图表占位】文字。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict

from ..schemas.contracts import StatsResult

# 状态配色（浅色系，饼图/等级图共用口径：已关闭浅绿、待解决浅粉、待验收浅橙、延期浅灰）
COLOR_CLOSED = "#A5D6A7"
COLOR_ACTIVE = "#F8BBD0"
COLOR_ACCEPT = "#FFE082"
COLOR_POSTPONE = "#E0E0E0"
COLOR_MODULE = "#90CAF9"   # 浅蓝（每人提交图/等级饼图 P3 用色）
COLOR_NOT_ACTIVATED = "#CFD8DC"  # 未激活浅灰蓝
# 语义别名：复用上文浅色系，把「图表用色」与「状态含义」解耦，改一处不影响他处
COLOR_PERSON_RESOLVE = COLOR_ACTIVE  # 每人解决图浅粉
COLOR_PIE_P1 = COLOR_ACTIVE   # 等级饼图 P1 粉
COLOR_PIE_P2 = COLOR_ACCEPT   # 等级饼图 P2 橙
COLOR_PIE_P3 = COLOR_MODULE   # 等级饼图 P3 蓝
COLOR_PIE_P4 = COLOR_CLOSED   # 等级饼图 P4 绿


def _setup_pyplot():
    """切换 Agg 后端并配置中文字体；返回 pyplot 模块。异常向上抛由 render_charts 捕获。"""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager, pyplot as plt

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei"):
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
    return plt


def _status_bar(plt, stats: StatsResult, out: Path, dpi: int) -> None:
    """4.1 缺陷状态分布：垂直条形柱状图（四状态各自浅色，顶部标件数与占比）。"""
    s = stats.summary
    labels = ["已关闭", "待解决", "待验收", "延期"]
    values = [s.closed, s.pending_resolve, s.pending_accept, s.postponed]
    colors = [COLOR_CLOSED, COLOR_ACTIVE, COLOR_ACCEPT, COLOR_POSTPONE]
    total = sum(values)
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    bars = ax.bar(labels, values, color=colors, width=0.55)
    for b, v in zip(bars, values):
        pct = f"{v / total * 100:.1f}%" if total else "0.0%"  # 展示层格式化，非业务计算
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v} 个\n{pct}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("缺陷数")
    ax.set_title(f"缺陷状态分布（共 {total} 个）", fontsize=12)
    ax.set_ylim(0, max(values) * 1.22 + 1)
    fig.tight_layout()
    fig.savefig(out, dpi=dpi)


def _hbar(plt, names, values, colors, out: Path, dpi: int, title: str,
          fmt: Callable[[int], str], label: str, figsize):
    """水平条形图公共画法：条末数值标签 + 右侧留白防裁切（figsize/dpi 集中在此）。

    names/values/colors 顺序即 y 轴自下而上；最大值在顶由调用方反转保证。
    """
    fig, ax = plt.subplots(figsize=figsize)
    bars = ax.barh(names, values, color=colors)
    pad = max(values) * 0.03 + 0.05
    for b, v in zip(bars, values):
        ax.text(b.get_width() + pad, b.get_y() + b.get_height() / 2,
                fmt(v), va="center", fontsize=9)
    ax.set_xlabel(label)
    ax.set_xlim(0, (max(values) + pad) * 1.08)
    ax.set_title(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(out, dpi=dpi)


def _activated_bar(plt, stats: StatsResult, out: Path, dpi: int) -> None:
    """4.2 Bug 激活次数分布：按激活次数逐档（0 次、1 次、2 次…）的水平条形图，条末标缺陷数。

    y 轴为每一档激活次数（不再合并为“未激活/≥ 1 次”两档）；0 次置顶、向上逐档递增，未激活用浅灰蓝区分。
    """
    s = stats.summary
    dist = s.activated_dist
    if not dist:
        raise ValueError("无激活次数分布数据")
    total = sum(b.total for b in dist)
    # _hbar 首元素画在底部，整体反转后 0 次落在顶部
    pairs = [
        (f"{b.times} 次", b.total, COLOR_NOT_ACTIVATED if b.times == 0 else COLOR_ACTIVE)
        for b in dist
    ][::-1]
    _hbar(plt, [p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs],
          out, dpi,
          f"Bug 激活次数分布（激活率 {s.activated_rate}，共 {total} 个）",
          lambda v: f"{v} 个", "缺陷数", (7.2, max(3.2, 0.55 * len(dist) + 1.2)))


def _level_pie(plt, stats: StatsResult, out: Path, dpi: int) -> None:
    """4.5 缺陷等级分布：饼图（P1–P4 件数占比；等级×状态交叉以表格呈现）。"""
    rows = [r for r in sorted(stats.level_status_distribution, key=lambda r: r.level) if r.total > 0]
    if not rows:
        raise ValueError("无等级分布数据")
    labels = [f"{r.level}（{r.total} 个）" for r in rows]
    values = [r.total for r in rows]
    palette = [COLOR_PIE_P1, COLOR_PIE_P2, COLOR_PIE_P3, COLOR_PIE_P4]  # P1粉 P2橙 P3蓝 P4绿
    total = sum(values)
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    # 等级数超出预设四色时循环取色，避免 colors/values 长度不匹配导致整图静默丢失
    colors = [palette[i % len(palette)] for i in range(len(rows))]
    ax.pie(
        values,
        labels=labels,
        colors=colors,
        autopct=lambda pct: f"{pct:.1f}%",
        startangle=90,
        counterclock=False,
        wedgeprops={"edgecolor": "white", "linewidth": 1.2},  # 白边分隔，防浅色扇区粘连
        textprops={"fontsize": 9},
    )
    ax.set_title(f"缺陷等级分布（共 {total} 个）", fontsize=12)
    ax.axis("equal")
    fig.tight_layout()
    fig.savefig(out, dpi=dpi)


def _person_bar(plt, counts, out: Path, dpi: int, color: str, title: str) -> None:
    """4.3/4.4 每人件数横向条形图（件数已降序，反转后最大值在顶部）。"""
    if not counts:
        raise ValueError("无每人计数数据")
    names = [p.name for p in counts][::-1]
    values = [p.total for p in counts][::-1]
    _hbar(plt, names, values, color, out, dpi, title,
          str, "Bug 数", (8, max(3.2, 0.42 * len(names) + 1.2)))


def _person_submit_bar(plt, stats: StatsResult, out: Path, dpi: int) -> None:
    _person_bar(plt, stats.people.submit_counts, out, dpi, COLOR_MODULE,
                f"每人提交 Bug 数（按提交人 openedBy 实算，共 {len(stats.people.submit_counts)} 人）")


def _person_resolve_bar(plt, stats: StatsResult, out: Path, dpi: int) -> None:
    _person_bar(plt, stats.people.resolve_counts, out, dpi, COLOR_PERSON_RESOLVE,
                f"每人解决 Bug 数（按解决人 resolvedBy 实算，共 {len(stats.people.resolve_counts)} 人）")


_CHARTS: Dict[str, Callable] = {
    "status_bar": _status_bar,
    "activated_bar": _activated_bar,
    "person_submit_bar": _person_submit_bar,
    "person_resolve_bar": _person_resolve_bar,
    "level_pie": _level_pie,
}


def render_charts(stats: StatsResult, assets_dir: Path, dpi: int = 150) -> Dict[str, Path]:
    """生成图表 PNG 到 assets_dir，返回 {图key: 路径}；任何失败返回已完成部分（可能为空）。

    调用方（渲染层）对缺失 key 回退占位文字，报告不因此中断。
    """
    result: Dict[str, Path] = {}
    try:
        if stats is None or stats.summary is None or stats.summary.total <= 0:
            return result  # 无缺陷数据：出图无意义，全部回退占位
        plt = _setup_pyplot()
    except Exception:
        return result
    try:
        assets_dir = Path(assets_dir)
        assets_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return result
    for key, fn in _CHARTS.items():
        try:
            path = assets_dir / f"{key}.png"
            fn(plt, stats, path, dpi)
            result[key] = path
        except Exception:
            continue  # 单图失败：跳过，渲染层回退该处占位
        finally:
            plt.close("all")
    return result
