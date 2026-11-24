"""V2-7 报告数字自检（F17 回抽校验泛化到全报告）。

背景：渲染层不做任何计算，报告里的统计数字全部由 renderer 从 stats_obj 搬运而来。
本模块在渲染完成后**独立二次核对**，作为回归安全网——若将来模板改动使「报告陈述的数字」
与「统计源值」脱节，或被润色/外部注入的文案混入错误数字，这里当场发现并纠正。

两道闸门（刻意不做「全文所有数字 ⊆ 统计集合」，那会把日期、缺陷 ID、章节号、
准入阈值 5%/30% 等合法非统计数字全部误报）：
1) 锚定句式核对（可自动纠正）：对 4.1、4.2 两句把统计值直接写进正文的句子做正则解析，
   逐个捕获位与 stats 源值比对，不一致则以源值重建该句（=「按统计源值重渲染」）；
2) 数字零丢失核对（只标注）：关键聚合量与结论 evidence 里的数字必须仍出现在报告中，
   缺失（被模板漏掉/被润色丢弃）则登记，无法凭空补回故仅标注。

只要发现任何差异，就在报告顶部插入醒目「数字自检」标注块（不一致不静默）。
report.numeric_selfcheck 开关默认关，关闭时本模块不被调用、渲染输出与现状逐字一致。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 4.1 / 4.2 正文句式：捕获位顺序 = 对应 stats 源值顺序，与 renderer 模板逐字对齐。
# 关键统计数字已在渲染层包成 **…** 加粗，此处用 (?:\*\*)? 容忍可选加粗，捕获位仍取纯数字。
_SENT_41_RE = re.compile(
    r"本次版本迭代测试周期内，累计发现缺陷 (?:\*\*)?(\d+)(?:\*\*)? 个。截至统计节点，已关闭 (?:\*\*)?(\d+)(?:\*\*)? 个，"
    r"待解决 (?:\*\*)?(\d+)(?:\*\*)? 个，待验收 (?:\*\*)?(\d+)(?:\*\*)? 个，延期 (?:\*\*)?(\d+)(?:\*\*)? 个。经核算，整体修复覆盖率为 (?:\*\*)?([\d.]+%)(?:\*\*)?。"
)
_SENT_42_RE = re.compile(
    r"本次缺陷总数 (?:\*\*)?(\d+)(?:\*\*)?，激活 ≥1 次的缺陷 (?:\*\*)?(\d+)(?:\*\*)? 个，激活率 (?:\*\*)?([\d.]+%)(?:\*\*)?。"
)
_SENT_41_FIELDS = ("缺陷总数", "已关闭", "待解决", "待验收", "延期", "修复覆盖率")
_SENT_42_FIELDS = ("缺陷总数", "激活≥1次缺陷数", "激活率")


def _summary_values(stats: Any) -> Tuple[str, ...]:
    s = stats.summary
    return (str(s.total), str(s.closed), str(s.pending_resolve),
            str(s.pending_accept), str(s.postponed), str(s.fix_coverage))


def _activated_values(stats: Any) -> Tuple[str, ...]:
    s = stats.summary
    return (str(s.total), str(s.activated_ge1_count), str(s.activated_rate))


def headline_numbers(stats: Any) -> Dict[str, str]:
    """关键聚合量（label → 源值字符串）：用于「零丢失」核对。"""
    s = stats.summary
    return {
        "缺陷总数": str(s.total),
        "已关闭": str(s.closed),
        "待解决": str(s.pending_resolve),
        "待验收": str(s.pending_accept),
        "延期": str(s.postponed),
        "修复覆盖率": str(s.fix_coverage),
        "激活≥1次缺陷数": str(s.activated_ge1_count),
        "激活率": str(s.activated_rate),
    }


def _check_sentence(md: str, pattern: re.Pattern, fields: Tuple[str, ...],
                    expected: Tuple[str, ...]) -> Tuple[str, List[Dict[str, str]]]:
    """解析锚定句，逐位比对；不一致则以源值重建整句并返回差异清单。"""
    m = pattern.search(md)
    if not m:
        return md, []  # 模板已改动、句式不匹配：非数字错误，跳过（由测试/evals 守模板稳定）
    got = m.groups()
    diffs = [{"field": f, "stated": g, "expected": e}
             for f, g, e in zip(fields, got, expected) if g != e]
    if not diffs:
        return md, []
    if pattern is _SENT_41_RE:
        fixed_sentence = (
            f"本次版本迭代测试周期内，累计发现缺陷 **{expected[0]}** 个。截至统计节点，已关闭 **{expected[1]}** 个，"
            f"待解决 **{expected[2]}** 个，待验收 **{expected[3]}** 个，延期 **{expected[4]}** 个。"
            f"经核算，整体修复覆盖率为 **{expected[5]}**。"
        )
    else:
        fixed_sentence = (
            f"本次缺陷总数 **{expected[0]}**，激活 ≥1 次的缺陷 **{expected[1]}** 个，激活率 **{expected[2]}**。"
        )
    return md[:m.start()] + fixed_sentence + md[m.end():], diffs


def _annotation(corrected: List[Dict[str, str]], missing: List[Dict[str, str]]) -> str:
    lines = ["> ⚠️ **数字自检（V2-7）**：渲染结果与统计源值存在不一致，已按下述处理。", ">"]
    for d in corrected:
        lines.append(f"> - 已按统计源值重渲染：{d['field']} 报告原值 {d['stated']} → 源值 {d['expected']}")
    for d in missing:
        lines.append(f"> - 报告缺失（未能定位、请人工核对）：{d['field']} 应为 {d['expected']}")
    return "\n".join(lines) + "\n\n"


def apply(markdown: str, stats: Any) -> Tuple[str, Dict[str, Any]]:
    """对渲染好的 markdown 做数字自检：返回 (纠正并标注后的正文, 自检报告 dict)。

    报告 dict = {"ok": bool, "corrected": [...], "missing": [...]}。
    数据不完整（integrity.data_complete=False）时不核对（此时正文本就无统计结论）。
    """
    if not getattr(getattr(stats, "integrity", None), "data_complete", True):
        return markdown, {"ok": True, "corrected": [], "missing": [], "skipped": "data_incomplete"}

    md = markdown
    corrected: List[Dict[str, str]] = []

    md, d41 = _check_sentence(md, _SENT_41_RE, _SENT_41_FIELDS, _summary_values(stats))
    corrected += d41
    md, d42 = _check_sentence(md, _SENT_42_RE, _SENT_42_FIELDS, _activated_values(stats))
    corrected += d42

    # 零丢失核对：关键聚合量 + 结论 evidence 数字，纠正后仍应能在正文找到。
    missing: List[Dict[str, str]] = []
    for field, val in headline_numbers(stats).items():
        if val and val not in md:
            missing.append({"field": field, "expected": val})
    evidence_nums = set()
    for e in (getattr(stats.conclusion, "evidence", None) or []):
        evidence_nums |= set(_NUM_RE.findall(str(e)))
    for n in sorted(evidence_nums):
        if n not in md:
            missing.append({"field": f"准入依据数字 {n}", "expected": n})

    ok = not corrected and not missing
    if not ok:
        md = _annotation(corrected, missing) + md
    return md, {"ok": ok, "corrected": corrected, "missing": missing}
