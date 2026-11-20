"""用例执行统计（用例条数 + 执行通过率）纯函数与渲染链路单测（不联网）。

覆盖：
- cases.extract_scope_from_rows 按「执行结果」列逐模块分桶（通过/失败/阻塞/未执行/其他）；
- 无「执行结果」列 → exec_available=False、分桶全 0；
- summarize_case_exec：执行率=已执行÷总数、通过率=通过÷已执行，其他值不并进通过；
- apply_module_groups 归并时执行分桶随组相加；
- scope_table 恒为两列（用例数不占表列，只以文字说明，用户 2026-11-20 拍板）；
- render_report：case_summary 非空才出 3.3 与 2.1 表后用例条数文字；执行结果不可用时如实只出条数。
"""
from __future__ import annotations

from report_agent.report import templates as T
from report_agent.report.cases import (
    apply_module_groups,
    extract_scope_from_rows,
    summarize_case_exec,
)
from report_agent.report.renderer import render_report
from report_agent.schemas.contracts import StatsResult


def _rows():
    return [
        ["用例编号", "模块分类", "用例名称", "执行结果"],
        ["TC-1", "云文档-新建", "a", "通过"],
        ["TC-2", "云文档-分享", "b", "成功"],
        ["TC-3", "云文档-导入", "c", "失败"],
        ["TC-4", "研究-问答", "d", "阻塞"],
        ["TC-5", "研究-溯源", "e", ""],
        ["TC-6", "研究-检索", "f", "跳过本次"],  # 未识别非空 → other
    ]


def test_extract_buckets_exec_per_module():
    rows = extract_scope_from_rows(_rows())
    by = {r["module"]: r for r in rows}
    assert all(r["exec_available"] for r in rows)
    cloud = by["云文档"]
    assert cloud["case_count"] == 3 and cloud["passed"] == 2 and cloud["failed"] == 1
    res = by["研究"]
    assert res["case_count"] == 3 and res["blocked"] == 1 and res["not_run"] == 1 and res["other"] == 1


def test_extract_without_exec_col_marks_unavailable():
    rows = extract_scope_from_rows([["用例编号", "模块分类", "用例名称"], ["1", "A-x", "n"]])
    assert rows[0]["exec_available"] is False
    assert rows[0]["passed"] == 0 and rows[0]["not_run"] == 0


def test_summarize_rates_and_other_excluded_from_pass():
    cs = summarize_case_exec(extract_scope_from_rows(_rows()))
    assert cs["total"] == 6
    # 已执行 = 通过2 + 失败1 + 阻塞1 + 其他1 = 5；未执行 1
    assert cs["executed"] == 5 and cs["not_run"] == 1 and cs["other"] == 1
    assert cs["exec_rate"] == "83.33%"
    assert cs["pass_rate"] == "40.00%"  # 通过 2 ÷ 已执行 5


def test_summarize_no_exec_still_counts_cases():
    rows = extract_scope_from_rows([["用例编号", "模块分类", "用例名称"],
                                    ["1", "A-x", "n"], ["2", "A-y", "m"]])
    cs = summarize_case_exec(rows)
    assert cs["exec_available"] is False and cs["total"] == 2 and cs["pass_rate"] == ""


def test_apply_module_groups_sums_exec():
    rows = extract_scope_from_rows(_rows())
    grouped = apply_module_groups(rows, [("全部", ("云文档", "研究"))])
    g = grouped[0]
    assert g["module"] == "全部" and g["case_count"] == 6
    assert g["passed"] == 2 and g["failed"] == 1 and g["blocked"] == 1 and g["other"] == 1 and g["not_run"] == 1
    assert g["exec_available"] is True


def test_scope_table_stays_two_columns():
    rows = [{"module": "A", "desc": "x", "case_count": 3}, {"module": "B", "desc": "y", "case_count": 2}]
    out = T.scope_table(rows)
    assert "用例数" not in out and "总计" not in out
    assert out.startswith("| 模块 | 描述 |\n")


def test_render_section_33_and_21_case_count_text():
    rows = extract_scope_from_rows(_rows())
    md = render_report(StatsResult(), test_scope=rows, case_summary=summarize_case_exec(rows))
    assert "## 3.3 测试用例执行概况" in md
    assert "（通过 ÷ 已执行）" in md
    # 2.1：表保持两列，用例条数以文字句给出（用户 2026-11-20 拍板）
    assert "| 模块 | 描述 |" in md and "| 模块 | 描述 | 用例数 |" not in md
    assert "本次测试范围共覆盖测试用例 6 条" in md


def test_render_off_has_no_33_no_case_count_text():
    rows = extract_scope_from_rows(_rows())
    md_off = render_report(StatsResult(), test_scope=rows)  # case_summary 默认 None
    assert "## 3.3" not in md_off
    assert "本次测试范围共覆盖测试用例" not in md_off


def test_render_no_exec_fallback_text():
    rows = extract_scope_from_rows([["用例编号", "模块分类", "用例名称"], ["1", "A-x", "n"]])
    md = render_report(StatsResult(), test_scope=rows, case_summary=summarize_case_exec(rows))
    assert "## 3.3 测试用例执行概况" in md
    assert "不输出执行率与通过率" in md
