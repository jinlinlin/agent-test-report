"""F4 报告渲染器：将 StatsResult 填充为 Markdown（按 6.1 章节裁剪对照表）。

同时提供失败/异常说明版报告（5.3）。渲染层不做任何计算，只搬运统计工具产出的数值。
"""
from __future__ import annotations

from typing import Dict, Optional

from ..schemas.contracts import StatsResult
from . import templates as T


def _chart_block(charts: Optional[Dict[str, str]], key: str, alt: str, item: str) -> str:
    """图表位：有对应 PNG（相对路径）则输出图片行，否则回退占位文字（不阻断）。"""
    if charts and charts.get(key):
        return f"![{alt}]({charts[key]})"
    return T.CHART_PLACEHOLDER.format(item=item)


def _module_table(stats: StatsResult) -> str:
    rows = ["| 模块 | 缺陷总数 | 已关闭 | 待解决 | 待验收 | 延期 |", "| --- | --- | --- | --- | --- | --- |"]
    for m in stats.module_distribution:
        rows.append(f"| {m.module} | {m.total} | {m.closed} | {m.pending_resolve} | {m.pending_accept} | {m.postponed} |")
    s = stats.summary
    rows.append(f"| **总计** | **{s.total}** | **{s.closed}** | **{s.pending_resolve}** | **{s.pending_accept}** | **{s.postponed}** |")
    return "\n".join(rows)


def _level_table(stats: StatsResult) -> str:
    rows = ["| 等级 | 缺陷总数 | 激活(待解决) | 已关闭 | 待验收 | 延期 |", "| --- | --- | --- | --- | --- | --- |"]
    tt = ta = tc = tp = ts = 0
    for lv in stats.level_status_distribution:
        rows.append(f"| {lv.level} | {lv.total} | {lv.active} | {lv.closed} | {lv.pending_accept} | {lv.postponed} |")
        tt += lv.total
        ta += lv.active
        tc += lv.closed
        tp += lv.pending_accept
        ts += lv.postponed
    rows.append(f"| **总计** | **{tt}** | **{ta}** | **{tc}** | **{tp}** | **{ts}** |")
    return "\n".join(rows)


def _tester_table(stats: StatsResult) -> str:
    """3.1 测试人员表：由本版本缺陷 openedBy/resolvedBy 如实聚合；无数据则标【待补充】。"""
    p = stats.people

    def _names(lst):
        return "、".join(lst) if lst else "【待补充】"

    rows = [
        "| 岗位角色 | 人员姓名 |",
        "| --- | --- |",
        f"| 项目负责人 | {p.project_owner or '【待补充：未配置 field_product_owner】'} |",
        f"| 测试负责人 | {_names(p.test_lead)} |",
        f"| 软件工程师（开发、运维） | {_names(p.developers)} |",
        f"| 测试人员 | {_names(p.testers)} |",
    ]
    return "\n".join(rows)


def render_report(stats: StatsResult, charts: Optional[Dict[str, str]] = None) -> str:
    s = stats.summary
    v = stats.version
    c = stats.conclusion

    parts = []
    parts.append(f"# {v.name or '版本'} 测试报告\n")

    # 1. 引言
    parts.append("## 1. 引言\n")
    parts.append("### 1.1 测试概述\n")
    parts.append(T.TEST_OVERVIEW.format(version=v.name or "本") + "\n")
    parts.append("### 1.2 缺陷管理工具\n")
    parts.append(T.DEFECT_TOOL + "\n")
    parts.append("### 1.3 参考文档\n")
    parts.append(T.REFERENCE_DOC_TABLE + "\n")
    parts.append("> 链接待 V2 接入飞书自动填充。\n")

    # 2. 测试概要
    parts.append("## 2. 测试概要\n")
    parts.append("### 2.1 测试范围\n")
    parts.append(T.TEST_SCOPE_TABLE + "\n")
    parts.append("> 模块清单可参照 4.1.1 缺陷分布自动列出；端/描述需人工或 V2 补齐。\n")
    parts.append("### 2.2 测试环境\n")
    parts.append(T.TEST_ENV_TABLE + "\n")
    parts.append("> 地址与凭证涉敏，不自动写入报告，需人工确认补充。\n")
    parts.append("### 2.3 测试方法以及标准\n")
    parts.append("#### 2.3.1 测试方法\n")
    parts.append(T.TEST_METHOD + "\n")
    parts.append("#### 2.3.2 测试标准\n")
    parts.append(T.TEST_STANDARD + "\n")

    # 3. 执行结果情况记录
    parts.append("## 3. 执行结果情况记录\n")
    parts.append("### 3.1 测试人员\n")
    parts.append(_tester_table(stats) + "\n")
    parts.append("> 测试负责人/测试人员由本版本缺陷提交人(openedBy)、软件工程师由解决人(resolvedBy)如实聚合（仅当前版本，取禅道用户真实姓名 realname）；项目负责人需在产品对象上配置 field_product_owner 字段，未配置或未取到时如实标注待补充。\n")
    parts.append("### 3.2 测试时间\n")
    parts.append("| 测试阶段 | 开始时间 | 结束时间 |")
    parts.append("| --- | --- | --- |")
    parts.append(f"| {v.name or '版本'} | {v.test_start or '待补充'} | {v.test_end or '待补充'} |\n")

    # 4. 功能测试执行及测试结果
    parts.append("## 4. 功能测试执行及测试结果\n")
    parts.append("### 4.1 缺陷分布统计\n")
    parts.append(
        f"本次版本迭代测试周期内，累计发现缺陷 {s.total} 个。截至统计节点，已关闭 {s.closed} 个，"
        f"待解决 {s.pending_resolve} 个，待验收 {s.pending_accept} 个，延期 {s.postponed} 个。"
        f"经核算，整体修复覆盖率为 {s.fix_coverage}。\n"
    )
    parts.append(_chart_block(charts, "status_bar", "缺陷状态分布图", "缺陷状态分布图（已关闭/待解决/待验收/延期）") + "\n")
    parts.append("#### 4.1.1 功能缺陷分布情况\n")
    parts.append(_module_table(stats) + "\n")
    parts.append("### 4.2 Bug 激活次数统计\n")
    parts.append(
        f"本次缺陷总数 {s.total}，激活 ≥1 次的缺陷 {s.activated_ge1_count} 个，激活率 {s.activated_rate}。\n"
    )
    parts.append(_chart_block(charts, "activated_bar", "Bug 激活次数分布图", "Bug 激活次数分布图") + "\n")
    parts.append("### 4.3 每人提交 Bug 数统计\n")
    if stats.people.submit_counts:
        parts.append("> 由本版本缺陷提交人（openedBy）实算聚合，与 3.1 同源；件数标注于柱末。\n")
        parts.append(_chart_block(charts, "person_submit_bar", "每人提交 Bug 数柱状图", "每人提交 Bug 数柱状图") + "\n")
    else:
        parts.append("【待补充：每人提交 Bug 数统计，需按缺陷 openedBy 字段聚合】\n")
        parts.append(T.CHART_PLACEHOLDER.format(item="每人提交 Bug 数柱状图") + "\n")
    parts.append("### 4.4 每人解决 Bug 数统计\n")
    if stats.people.resolve_counts:
        parts.append("> 由本版本缺陷解决人（resolvedBy）实算聚合；仅统计有解决人的缺陷，故总计可能小于缺陷总数（未解决/已关闭无解决记者不计）。件数标注于柱末。\n")
        parts.append(_chart_block(charts, "person_resolve_bar", "每人解决 Bug 数柱状图", "每人解决 Bug 数柱状图") + "\n")
    else:
        parts.append("【待补充：每人解决 Bug 数统计，需按缺陷 resolvedBy 字段聚合】\n")
        parts.append(T.CHART_PLACEHOLDER.format(item="每人解决 Bug 数柱状图") + "\n")
    parts.append("### 4.5 缺陷等级状态分布\n")
    parts.append(_level_table(stats) + "\n")
    parts.append(_chart_block(charts, "level_pie", "缺陷等级分布图", "缺陷等级分布图") + "\n")
    parts.append("### 4.6 缺陷级别定义\n")
    parts.append(T.LEVEL_DEFINITION + "\n")

    # 5. 测试结论
    parts.append("## 5. 测试结论\n")
    if stats.integrity.data_complete:
        parts.append("依据发布准入标准（激活 P1=0；P2 激活占比 ≤5%；P3 激活占比 ≤30%；P4 不限），逐项核对如下：\n")
        for e in c.evidence:
            parts.append(f"- {e}")
        parts.append("")
        parts.append("### 5.1 发布建议\n")
        parts.append(c.release_advice + "\n")
    else:
        parts.append("数据不完整，暂不输出结论。缺口：" + "、".join(stats.integrity.missing_fields) + "\n")

    # 6. 缺陷清单
    parts.append("## 6. 缺陷清单\n")
    parts.append(T.DEFECT_LIST_NOTE + "\n")

    return "\n".join(parts)


def render_failure_report(reason: str, version_name: Optional[str] = None, detail: str = "") -> str:
    """失败/异常说明版报告（5.3）：仅说明失败，不含任何虚构数据。"""
    title = version_name or "目标版本"
    lines = [
        f"# {title} 测试报告（生成失败）\n",
        f"**结论：{reason}**\n",
        "## 失败环节与原因\n",
        f"- {detail or reason}\n",
        "## 缺口说明\n",
        "- 缺陷统计、模块分布、等级分布、测试结论与发布建议等章节均未能生成。\n",
        "> 依据防幻觉原则：取数失败时如实标注，不补造任何数字、链接与业务描述。\n",
    ]
    return "\n".join(lines)


def render_step_limit_report(step: int, max_steps: int, version_name: Optional[str] = None) -> str:
    title = version_name or "目标版本"
    return "\n".join([
        f"# {title} 测试报告（生成不完整）\n",
        f"**执行步数超限，报告生成不完整**（已执行 {step} 步，上限 {max_steps} 步）。\n",
        "> 依据执行上限规则：达到最大步数直接终止，不循环重试，不产出伪造内容。\n",
    ])
