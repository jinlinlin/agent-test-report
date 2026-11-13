"""V2-4 数据源 D：测试用例 xlsx 解析纯函数单测（内存构造 xlsx，不联网）。

覆盖：一级模块聚合与计数、full 粒度、表头模糊匹配、模块列缺失/空/坏字节回退 None、分隔符变体。
"""
from __future__ import annotations

import io

import openpyxl

from report_agent.report.cases import extract_scope_from_cases


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_aggregate_top_level_with_counts():
    data = _xlsx([
        ["用例编号", "模块分类", "用例名称"],
        ["TC-1", "AI对话组件-模型选择", "a"],
        ["TC-2", "AI对话组件-对话模式", "b"],
        ["TC-3", "研究助手-问答", "c"],
    ])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["AI对话组件", "研究助手"]
    # 描述=该菜单测到的功能点（二级段），非用例数
    assert rows[0]["desc"] == "模型选择、对话模式" and rows[0]["case_count"] == 2
    assert rows[1]["desc"] == "问答"


def test_full_group_level_keeps_paths():
    data = _xlsx([
        ["模块分类", "用例名称"],
        ["AI对话组件-模型选择", "下拉展示"],
        ["AI对话组件-对话模式", "极速切换"],
    ])
    rows = extract_scope_from_cases(data, group_level="full")
    assert [r["module"] for r in rows] == ["AI对话组件-模型选择", "AI对话组件-对话模式"]
    assert rows[0]["desc"] == "下拉展示"


def test_module_col_fuzzy_match():
    data = _xlsx([["编号", "所属模块分类", "标题"], ["1", "文献管理-导入", "x"]])
    rows = extract_scope_from_cases(data, module_col="模块分类")
    assert rows and rows[0]["module"] == "文献管理"


def test_preserves_first_seen_order():
    data = _xlsx([
        ["模块分类"],
        ["研究助手-问答"],
        ["AI对话组件-模型"],
        ["研究助手-溯源"],
    ])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["研究助手", "AI对话组件"]
    assert rows[0]["desc"] == "问答、溯源" and rows[0]["case_count"] == 2


def test_blank_and_none_cells_skipped():
    data = _xlsx([["模块分类"], ["文献管理-导入"], [None], [""]])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["文献管理"]


def test_no_module_column_returns_none():
    data = _xlsx([["用例编号", "用例名称"], ["TC-1", "x"]])
    assert extract_scope_from_cases(data) is None


def test_header_only_no_data_returns_none():
    data = _xlsx([["用例编号", "模块分类"]])
    assert extract_scope_from_cases(data) is None


def test_empty_bytes_returns_none():
    assert extract_scope_from_cases(b"") is None


def test_corrupt_bytes_returns_none():
    assert extract_scope_from_cases(b"not an xlsx at all") is None


def test_name_path_fallback_uses_second_segment():
    """模块分类只有一级时，描述退用「用例名称」的二级段，不堆整条多级路径。"""
    data = _xlsx([
        ["模块分类", "用例名称"],
        ["研究探索", "研究探索-页面重构-旧的类型单选tab已移除"],
        ["研究探索", "研究探索-意图识别-研究类问题走研究探索链路"],
        ["异常与边界", "异常边界-输入超长文本-发送处理"],
    ])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["研究探索", "异常与边界"]
    assert rows[0]["desc"] == "页面重构、意图识别"
    assert rows[1]["desc"] == "输入超长文本"


def test_alternative_separators():
    data = _xlsx([["模块分类"], ["数据分析／可视化"], ["数据分析/图表"], ["研究助手>问答"]])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["数据分析", "研究助手"]
