"""F4 报告渲染器：将 StatsResult 填充为 Markdown（按 6.1 章节裁剪对照表）。

同时提供失败/异常说明版报告（5.3）。渲染层不做任何计算，只搬运统计工具产出的数值。
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ..schemas.contracts import BugRow, StatsResult
from . import templates as T


def _chart_block(charts: Optional[Dict[str, str]], key: str, item: str) -> str:
    """图表位：有对应 PNG（相对路径）则输出图片行（无 alt，避免飞书图下多描述文字），否则回退占位（不阻断）。"""
    if charts and charts.get(key):
        return f"![]({charts[key]})"
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


# ---- 第 6 节缺陷清单：禅道枚举值→中文、单元格净化、日期格式化 ----
_TAG_RE = re.compile(r"<[^>]+>")
_NULLISH = {"", "null", "none", "0000-00-00 00:00:00", "0000-00-00"}
_CN_TZ = timezone(timedelta(hours=8))

_STATUS_CN = {"active": "激活", "resolved": "已解决", "closed": "已关闭"}
_RESOLUTION_CN = {
    "fixed": "已解决", "willnotfix": "不予解决", "bydesign": "设计如此",
    "duplicate": "重复Bug", "notrepro": "无法重现", "external": "外部原因",
    "postponed": "延期处理",
}
_TYPE_CN = {
    "codeerror": "代码错误", "config": "配置相关", "install": "安装部署",
    "security": "安全相关", "performance": "性能问题", "standard": "规范文档",
    "designdefect": "设计缺陷", "reliability": "可靠性问题", "other": "其他",
}


def _clean_cell(value, limit: int = 0) -> str:
    """去 HTML 标签、反转义、折叠空白、竖线转全角（防破表），可选截断；空值返回 '-'。"""
    s = str(value if value is not None else "")
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s.replace("|", "｜")).strip()
    if limit and len(s) > limit:
        s = s[:limit] + "…"
    return s or "-"


def _map_cn(value: str, table: Dict[str, str]) -> str:
    key = str(value or "").strip().lower()
    return table.get(key, _clean_cell(value))


def _fmt_date(value) -> str:
    s = str(value or "").strip()
    if s.lower() in _NULLISH:
        return "-"
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    dt = None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return s[:10] if len(s) >= 10 else "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_CN_TZ)
    return dt.astimezone(_CN_TZ).strftime("%Y-%m-%d")


_DEFECT_HEADERS = ["ID", "Bug编号", "所属模块", "bug标题", "优先级", "功能类型", "重现步骤",
                   "bug状态", "由谁创建", "创建日期", "影响版本", "指派给", "解决方案"]


def _defect_table(rows: List[BugRow], empty_msg: str) -> str:
    """输出 12 列缺陷清单；无数据则返回提示语（不删列、不编造）。"""
    if not rows:
        return empty_msg
    lines = ["| " + " | ".join(_DEFECT_HEADERS) + " |",
             "| " + " | ".join(["---"] * len(_DEFECT_HEADERS)) + " |"]
    for i, r in enumerate(rows, 1):
        cells = [
            str(i),
            _clean_cell(r.id),
            _clean_cell(r.module),
            _clean_cell(r.title, 60),
            _clean_cell(r.priority),
            _map_cn(r.type, _TYPE_CN),
            _clean_cell(r.steps, 120),
            _map_cn(r.status, _STATUS_CN),
            _clean_cell(r.opened_by),
            _fmt_date(r.opened_date),
            _clean_cell(r.opened_build),
            _clean_cell(r.assigned_to),
            _map_cn(r.resolution, _RESOLUTION_CN),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---- 多维表格（第 6 节导出）：字段规格与记录映射（重现步骤全文不截断）----
DEFECT_FIELD_SPECS: List[Dict[str, Any]] = [
    {"field_name": "序号", "type": 1},
    {"field_name": "Bug编号", "type": 1},
    {"field_name": "所属模块", "type": 1},
    {"field_name": "bug标题", "type": 1},
    {"field_name": "优先级", "type": 1},
    {"field_name": "功能类型", "type": 3},
    {"field_name": "重现步骤", "type": 1},
    {"field_name": "bug状态", "type": 3},
    {"field_name": "由谁创建", "type": 1},
    {"field_name": "创建日期", "type": 1},
    {"field_name": "影响版本", "type": 1},
    {"field_name": "指派给", "type": 1},
    {"field_name": "解决方案", "type": 3},
]


def _bit_text(value) -> str:
    """多维表格文本单元格：去 HTML 标签、反转义、折叠空白；空→空串（bitable 非 markdown，不转义竖线）。"""
    s = _TAG_RE.sub(" ", str(value if value is not None else ""))
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _bit_select(value, table: Dict[str, str]):
    """单选字段：空值返回 None（不建空选项）；枚举命中取中文，否则取净化后的原值。"""
    key = str(value or "").strip().lower()
    if not key:
        return None
    return table.get(key) or (_bit_text(value) or None)


def bug_to_fields(row: BugRow, seq: int) -> Dict[str, Any]:
    """一条缺陷→多维表格记录（列名→值）：枚举中文化、重现步骤全文不截断、空单选留 None。"""
    d = _fmt_date(row.opened_date)
    return {
        "序号": str(seq),
        "Bug编号": _bit_text(row.id),
        "所属模块": _bit_text(row.module),
        "bug标题": _bit_text(row.title),
        "优先级": _bit_text(row.priority),
        "功能类型": _bit_select(row.type, _TYPE_CN),
        "重现步骤": _bit_text(row.steps),
        "bug状态": _bit_select(row.status, _STATUS_CN),
        "由谁创建": _bit_text(row.opened_by),
        "创建日期": "" if d == "-" else d,
        "影响版本": _bit_text(row.opened_build),
        "指派给": _bit_text(row.assigned_to),
        "解决方案": _bit_select(row.resolution, _RESOLUTION_CN),
    }


def render_report(stats: StatsResult, charts: Optional[Dict[str, str]] = None,
                  ref_links: Optional[Dict[str, str]] = None,
                  defect_bitable_inline: bool = False) -> str:
    s = stats.summary
    v = stats.version
    c = stats.conclusion

    parts = []
    # 正文不输出 H1 标题：飞书知识库节点标题即文档名（另有页面顶部 H1 展示），
    # 重复会压字；章节标题整体上调一级（原二级→一级）承接层级。

    # 1. 引言
    parts.append("# 1. 引言\n")
    parts.append("## 1.1 测试概述\n")
    parts.append(T.TEST_OVERVIEW.format(version=v.name or "本") + "\n")
    parts.append("## 1.2 缺陷管理工具\n")
    parts.append(T.DEFECT_TOOL + "\n")
    parts.append("## 1.3 参考文档\n")
    parts.append(T.reference_doc_table(ref_links) + "\n")

    # 2. 测试概要
    parts.append("# 2. 测试概要\n")
    parts.append("## 2.1 测试范围\n")
    parts.append(T.TEST_SCOPE_TABLE + "\n")
    parts.append("> 模块清单可参照 4.1.1 缺陷分布自动列出；描述需人工或 V2 补齐。\n")
    parts.append("## 2.2 测试环境\n")
    parts.append(T.TEST_ENV_TABLE + "\n")
    parts.append("## 2.3 测试方法以及标准\n")
    parts.append("### 2.3.1 测试方法\n")
    parts.append(T.TEST_METHOD + "\n")
    parts.append("### 2.3.2 测试标准\n")
    parts.append(T.TEST_STANDARD + "\n")

    # 3. 执行结果情况记录
    parts.append("# 3. 执行结果情况记录\n")
    parts.append("## 3.1 测试人员\n")
    parts.append(_tester_table(stats) + "\n")
    parts.append("## 3.2 测试时间\n")
    parts.append("| 测试阶段 | 开始时间 | 结束时间 |")
    parts.append("| --- | --- | --- |")
    parts.append(f"| {v.name or '版本'} | {v.test_start or '待补充'} | {v.test_end or '待补充'} |\n")

    # 4. 功能测试执行及测试结果
    parts.append("# 4. 功能测试执行及测试结果\n")
    parts.append("## 4.1 缺陷分布统计\n")
    parts.append(
        f"本次版本迭代测试周期内，累计发现缺陷 {s.total} 个。截至统计节点，已关闭 {s.closed} 个，"
        f"待解决 {s.pending_resolve} 个，待验收 {s.pending_accept} 个，延期 {s.postponed} 个。"
        f"经核算，整体修复覆盖率为 {s.fix_coverage}。\n"
    )
    parts.append(_chart_block(charts, "status_bar", "缺陷状态分布图") + "\n")
    parts.append("### 4.1.1 功能缺陷分布情况\n")
    parts.append(_module_table(stats) + "\n")
    parts.append("## 4.2 Bug 激活次数统计\n")
    parts.append(
        f"本次缺陷总数 {s.total}，激活 ≥1 次的缺陷 {s.activated_ge1_count} 个，激活率 {s.activated_rate}。\n"
    )
    parts.append(_chart_block(charts, "activated_bar", "Bug 激活次数分布图") + "\n")
    parts.append("## 4.3 每人提交 Bug 数统计\n")
    if stats.people.submit_counts:
        parts.append(_chart_block(charts, "person_submit_bar", "每人提交 Bug 数柱状图") + "\n")
    else:
        parts.append("【待补充：每人提交 Bug 数统计，需按缺陷 openedBy 字段聚合】\n")
        parts.append(T.CHART_PLACEHOLDER.format(item="每人提交 Bug 数柱状图") + "\n")
    parts.append("## 4.4 每人解决 Bug 数统计\n")
    if stats.people.resolve_counts:
        parts.append(_chart_block(charts, "person_resolve_bar", "每人解决 Bug 数柱状图") + "\n")
    else:
        parts.append("【待补充：每人解决 Bug 数统计，需按缺陷 resolvedBy 字段聚合】\n")
        parts.append(T.CHART_PLACEHOLDER.format(item="每人解决 Bug 数柱状图") + "\n")
    parts.append("## 4.5 缺陷等级状态分布\n")
    parts.append(_level_table(stats) + "\n")
    parts.append(_chart_block(charts, "level_pie", "缺陷等级分布图") + "\n")
    parts.append("## 4.6 缺陷级别定义\n")
    parts.append(T.LEVEL_DEFINITION + "\n")

    # 5. 测试结论
    parts.append("# 5. 测试结论\n")
    if stats.integrity.data_complete:
        parts.append("依据发布准入标准（激活 P1=0；P2 激活占比 ≤5%；P3 激活占比 ≤30%；P4 不限），逐项核对如下：\n")
        for e in c.evidence:
            parts.append(f"- {e}")
        parts.append("")
        parts.append("## 5.1 发布建议\n")
        parts.append(c.release_advice + "\n")
    else:
        parts.append("数据不完整，暂不输出结论。缺口：" + "、".join(stats.integrity.missing_fields) + "\n")

    # 6. 缺陷清单
    parts.append("# 6. 缺陷清单\n")
    if defect_bitable_inline:
        # C：正文只写计数与引导，多维表格实体由推送环节内嵌到本文档末尾（失败则降级为仅本段说明）。
        parts.append(T.defect_bitable_inline_note(len(stats.postponed_bugs), len(stats.active_bugs)) + "\n")
    else:
        parts.append(T.DEFECT_LIST_NOTE + "\n")
        parts.append("## 6.1 延期处理缺陷清单\n")
        parts.append(_defect_table(stats.postponed_bugs, "本版本无「延期处理且未关闭」的缺陷。") + "\n")
        parts.append("## 6.2 激活（待解决）缺陷清单\n")
        parts.append(_defect_table(stats.active_bugs, "本版本无激活（待解决）的缺陷。") + "\n")

    return "\n".join(parts)


def render_failure_report(reason: str, version_name: Optional[str] = None, detail: str = "") -> str:
    """失败/异常说明版报告（5.3）：仅说明失败，不含任何虚构数据。与正文同口径不出 H1 标题、章节从一级起。"""
    lines = [
        f"**结论：{reason}**\n",
        "# 失败环节与原因\n",
        f"- {detail or reason}\n",
        "# 缺口说明\n",
        "- 缺陷统计、模块分布、等级分布、测试结论与发布建议等章节均未能生成。\n",
        "> 依据防幻觉原则：取数失败时如实标注，不补造任何数字、链接与业务描述。\n",
    ]
    return "\n".join(lines)


def render_step_limit_report(step: int, max_steps: int, version_name: Optional[str] = None) -> str:
    """步数超限说明版报告：与正文同口径不出 H1 标题（version_name 仅为签名兼容）。"""
    return "\n".join([
        f"**执行步数超限，报告生成不完整**（已执行 {step} 步，上限 {max_steps} 步）。\n",
        "> 依据执行上限规则：达到最大步数直接终止，不循环重试，不产出伪造内容。\n",
    ])
